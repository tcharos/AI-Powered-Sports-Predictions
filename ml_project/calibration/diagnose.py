"""Per-league miscalibration diagnostic.

Generates out-of-fold (OOF) predictions for every match in the training set
via the same 5-fold TimeSeriesSplit the trainer uses, then aggregates by
league to surface where the model systematically over- or underestimates
each outcome's probability.

OOF construction means each match's prediction comes from a model trained
*without* that match — unbiased calibration measurement without
test-on-training leakage.

Outputs:
- output/calibration/diagnose_<ts>.csv  — one row per (league, market)
- output/calibration/diagnose_<ts>.md   — human-readable report, sorted
                                          by miscalibration severity (ECE)
"""

import datetime
import json
import os
import sys
from typing import Tuple

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit


def out_of_fold_predictions(df_train: pd.DataFrame,
                            features: list,
                            target: str,
                            params: dict,
                            n_splits: int = 5) -> np.ndarray:
    """Train n_splits models on TimeSeriesSplit folds; return OOF predictions.

    Returns an ndarray of shape (len(df_train), n_classes). Rows in the
    initial chunk (no training data available for fold-1) get NaN.
    """
    df_train = df_train.dropna(subset=features + [target]).copy()
    df_train = df_train.sort_values('date').reset_index(drop=True)

    if 'league_cat' in df_train.columns:
        df_train['league_cat'] = df_train['league_cat'].astype('category')

    n_classes = int(df_train[target].max()) + 1
    oof = np.full((len(df_train), n_classes), np.nan)

    tscv = TimeSeriesSplit(n_splits=n_splits)
    for fold, (train_idx, test_idx) in enumerate(tscv.split(df_train)):
        cv_train = df_train.iloc[train_idx]
        cv_test  = df_train.iloc[test_idx]
        model = xgb.XGBClassifier(**params)
        model.fit(
            cv_train[features], cv_train[target],
            eval_set=[(cv_test[features], cv_test[target])],
            verbose=False,
        )
        preds = model.predict_proba(cv_test[features])
        # Some folds may not have seen all classes — pad columns if needed.
        if preds.shape[1] != n_classes:
            full = np.zeros((len(cv_test), n_classes))
            for i, cls in enumerate(model.classes_):
                full[:, int(cls)] = preds[:, i]
            preds = full
        oof[test_idx] = preds
        print(f"  fold {fold+1}: train={len(cv_train)} test={len(cv_test)}", flush=True)

    return oof, df_train.index.values


def expected_calibration_error(probs: np.ndarray,
                               targets: np.ndarray,
                               n_bins: int = 10) -> Tuple[float, list]:
    """Expected Calibration Error using max-prob confidence binning.

    probs: (N, K) probabilities per class.
    targets: (N,) integer class labels.

    Returns (ECE, bin_summary) where bin_summary is a list of
    (bin_lo, bin_hi, count, avg_conf, acc) for diagnostic display.
    """
    confidences = probs.max(axis=1)
    preds = probs.argmax(axis=1)
    correct = (preds == targets).astype(float)
    n = len(targets)

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    summary = []
    for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
        # Include right edge in the last bin
        mask = (confidences >= lo) & ((confidences < hi) if hi < 1.0 else (confidences <= hi))
        cnt = int(mask.sum())
        if cnt == 0:
            summary.append((lo, hi, 0, 0.0, 0.0))
            continue
        avg_conf = float(confidences[mask].mean())
        acc = float(correct[mask].mean())
        ece += (cnt / n) * abs(avg_conf - acc)
        summary.append((lo, hi, cnt, avg_conf, acc))
    return ece, summary


def brier_multiclass(probs: np.ndarray, targets: np.ndarray) -> float:
    """Multiclass Brier (mean squared error of probability vector vs one-hot)."""
    n_classes = probs.shape[1]
    onehot = np.eye(n_classes)[targets.astype(int)]
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def log_loss_safe(probs: np.ndarray, targets: np.ndarray, eps: float = 1e-15) -> float:
    """Multiclass log loss, clipping probs to avoid log(0)."""
    probs = np.clip(probs, eps, 1.0 - eps)
    n = len(targets)
    return float(-np.mean(np.log(probs[np.arange(n), targets.astype(int)])))


def per_league_metrics(df: pd.DataFrame,
                       probs: np.ndarray,
                       target_col: str,
                       outcome_labels: list,
                       min_n: int = 100) -> pd.DataFrame:
    """Aggregate calibration metrics per league.

    Returns DataFrame with one row per league: N, per-outcome predicted-vs-actual
    delta, Brier, log loss, ECE, and a single 'severity' score for ranking.
    """
    mask = ~np.isnan(probs).any(axis=1) & df[target_col].notna()
    df = df.loc[mask].reset_index(drop=True).copy()
    probs = probs[mask]

    rows = []
    for league, sub in df.groupby('league'):
        n = len(sub)
        if n < min_n:
            continue
        sub_idx = sub.index.values  # positions in `df` (which we re-indexed above)
        p = probs[sub_idx]
        t = sub[target_col].astype(int).values

        mean_pred = p.mean(axis=0)
        actual_freq = np.bincount(t, minlength=p.shape[1]) / n
        deltas = mean_pred - actual_freq  # positive = model overestimates

        brier = brier_multiclass(p, t)
        ll = log_loss_safe(p, t)
        ece, _ = expected_calibration_error(p, t)

        row = {
            'league': league,
            'n': int(n),
            'brier': round(brier, 4),
            'log_loss': round(ll, 4),
            'ece': round(ece, 4),
            'max_abs_delta': round(float(np.max(np.abs(deltas))), 4),
        }
        for label, mp, af, d in zip(outcome_labels, mean_pred, actual_freq, deltas):
            row[f'pred_{label}'] = round(float(mp), 4)
            row[f'actual_{label}'] = round(float(af), 4)
            row[f'delta_{label}'] = round(float(d), 4)
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Severity = ECE + max_abs_delta — higher means more recalibration warranted.
    out['severity'] = (out['ece'] + out['max_abs_delta']).round(4)
    out = out.sort_values('severity', ascending=False).reset_index(drop=True)
    return out


def write_markdown_report(out_path: str,
                          df_1x2: pd.DataFrame,
                          df_ou: pd.DataFrame,
                          meta: dict) -> None:
    lines = []
    lines.append('# Per-league calibration diagnostic')
    lines.append('')
    lines.append(f"Generated: {meta['generated_at']}")
    lines.append(f"Source: {meta['source']}")
    if 'mode' in meta:
        lines.append(f"Mode: {meta['mode']}")
    lines.append(f"OOF predictions via TimeSeriesSplit(n_splits={meta['n_splits']})")
    lines.append(f"Minimum matches per league for inclusion: {meta['min_n']}")
    lines.append('')

    def _section(title: str, df: pd.DataFrame, outcome_labels: list):
        lines.append(f'## {title}')
        lines.append('')
        if df.empty:
            lines.append(f"_No leagues met the minimum-N threshold ({meta['min_n']})._")
            lines.append('')
            return
        header = ['league', 'n', 'brier', 'log_loss', 'ece', 'severity']
        for lbl in outcome_labels:
            header.extend([f'pred_{lbl}', f'actual_{lbl}', f'delta_{lbl}'])
        lines.append('| ' + ' | '.join(header) + ' |')
        lines.append('| ' + ' | '.join(['---'] * len(header)) + ' |')
        for _, r in df.iterrows():
            cells = [str(r[c]) for c in header]
            lines.append('| ' + ' | '.join(cells) + ' |')
        lines.append('')
        lines.append('Notes: `delta_<outcome>` is mean-predicted minus actual-frequency. '
                     'Positive ⇒ model overestimates that outcome in this league. '
                     '`severity = ece + max(|delta|)`; higher is worse.')
        lines.append('')

    _section('1X2 market', df_1x2, ['home', 'draw', 'away'])
    _section('Over/Under 2.5 market', df_ou, ['under', 'over'])

    lines.append('## How to read this')
    lines.append('')
    lines.append('- **ece** (Expected Calibration Error): average gap between '
                 'predicted confidence and observed accuracy across 10 bins. '
                 '0 = perfectly calibrated. >0.05 = noticeable drift.')
    lines.append('- **delta_X**: in this league, the model says outcome X happens '
                 'X% of the time but actually happens at a different rate. '
                 'Positive deltas inflate EV for that outcome — exactly the '
                 'pathology that produced the Lahti VPS €24 stake.')
    lines.append('- Sort by `severity` to see which leagues need recalibration '
                 'most urgently in phase C2.')
    lines.append('')

    with open(out_path, 'w') as f:
        f.write('\n'.join(lines))
