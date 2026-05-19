"""Platt-scaling fit + persistence for per-league probability recalibration (C2).

For each (league, market), fits a small logistic regression on the OOF
predictions produced by C1's diagnostic engine. The output is a tiny set
of `(a, b)` parameters per league per market — together they form
`data_sets/league_calibration.json`, the lookup table that
`predict_matches.py` will use at inference (C4).

Markets:
  1X2  — per-class one-vs-rest Platt (renormalised after scaling).
  O/U  — single binary Platt on P(over).

Calibration is on OOF predictions, which are unbiased measurements of
the model's probability. Fitting on them and then evaluating in-sample
(below) is mildly optimistic — that's what C3's chronological holdout
will properly correct. C2's job is to produce the calibration table and
flag any leagues where the in-sample fit doesn't even improve Brier.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression

from .diagnose import brier_multiclass, expected_calibration_error, log_loss_safe


_EPS = 1e-6


def _safe_logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def fit_platt_binary(p_pos: np.ndarray,
                     targets: np.ndarray) -> Tuple[float, float]:
    """Fit P(pos) Platt scaling: calibrated = sigmoid(a * logit(p) + b).

    Returns (a, b). Uses sklearn LogisticRegression — single-feature problem
    with mild default L2 regularisation.
    """
    X = _safe_logit(p_pos).reshape(-1, 1)
    y = targets.astype(int)
    if len(np.unique(y)) < 2:
        # Degenerate: all-same target. Fall back to identity (a=1, b=0).
        return 1.0, 0.0
    clf = LogisticRegression(solver='lbfgs', max_iter=200)
    clf.fit(X, y)
    return float(clf.coef_[0, 0]), float(clf.intercept_[0])


def apply_platt_binary(p_pos: np.ndarray, a: float, b: float) -> np.ndarray:
    return _sigmoid(a * _safe_logit(p_pos) + b)


def fit_platt_multiclass(probs: np.ndarray,
                         targets: np.ndarray,
                         n_classes: int = 3) -> List[Tuple[float, float]]:
    """One-vs-rest Platt per class. Returns list of (a, b) per class."""
    out = []
    for k in range(n_classes):
        a, b = fit_platt_binary(probs[:, k], (targets == k).astype(int))
        out.append((a, b))
    return out


def apply_platt_multiclass(probs: np.ndarray,
                           params: List[Tuple[float, float]]) -> np.ndarray:
    """Apply per-class Platt, then renormalise so each row sums to 1."""
    cal = np.zeros_like(probs)
    for k, (a, b) in enumerate(params):
        cal[:, k] = apply_platt_binary(probs[:, k], a, b)
    row_sums = cal.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums > 0, row_sums, 1.0)
    return cal / row_sums


def metrics(probs: np.ndarray, targets: np.ndarray) -> Dict[str, float]:
    """Brier + log loss + ECE — same definitions C1 uses for comparability."""
    ece, _ = expected_calibration_error(probs, targets)
    return {
        'brier':    round(brier_multiclass(probs, targets), 4),
        'log_loss': round(log_loss_safe(probs, targets), 4),
        'ece':      round(ece, 4),
    }


def fit_league_calibrators(df, oof_probs, target_col, market: str,
                           min_n: int = 100,
                           source_mode: str = 'full') -> Dict[str, dict]:
    """Per league, fit Platt + record before/after metrics.

    `market` is 'oneXtwo' or 'ou' (used as key in the output dict).
    Returns: {league_name: {market: {platt, n, source_mode, before, after, improved}}}
    """
    if market not in ('oneXtwo', 'ou'):
        raise ValueError(f"Unknown market: {market!r}")

    out: Dict[str, dict] = {}
    valid = ~np.isnan(oof_probs).any(axis=1) & df[target_col].notna()
    df_v = df.loc[valid].reset_index(drop=True)
    probs_v = oof_probs[valid]

    for league, sub in df_v.groupby('league'):
        n = len(sub)
        if n < min_n:
            continue
        idx = sub.index.values
        p = probs_v[idx]
        t = sub[target_col].astype(int).values

        before = metrics(p, t)

        if market == 'oneXtwo':
            params = fit_platt_multiclass(p, t, n_classes=3)
            cal = apply_platt_multiclass(p, params)
            platt = {
                'home': {'a': round(params[0][0], 4), 'b': round(params[0][1], 4)},
                'draw': {'a': round(params[1][0], 4), 'b': round(params[1][1], 4)},
                'away': {'a': round(params[2][0], 4), 'b': round(params[2][1], 4)},
            }
        else:  # ou
            # OOF probs from O/U binary model are 2-column [P(under), P(over)].
            # We fit on P(over) since target_ou == 1 means over.
            a, b = fit_platt_binary(p[:, 1], t)
            cal = np.zeros_like(p)
            cal[:, 1] = apply_platt_binary(p[:, 1], a, b)
            cal[:, 0] = 1.0 - cal[:, 1]
            platt = {'over': {'a': round(a, 4), 'b': round(b, 4)}}

        after = metrics(cal, t)
        improved = after['brier'] <= before['brier']

        out[league] = {
            'n': int(n),
            'source_mode': source_mode,
            'platt': platt,
            'before': before,
            'after': after,
            'improved': bool(improved),
            'brier_delta': round(after['brier'] - before['brier'], 4),
            'ece_delta':   round(after['ece']   - before['ece'],   4),
        }
    return out


def merge_calibrators(full_results: Dict[str, Dict[str, dict]],
                      minimal_results: Dict[str, Dict[str, dict]]
                      ) -> Dict[str, Dict[str, dict]]:
    """Prefer full-mode entries; backfill leagues only present in minimal mode.

    Inputs are {league: {market: result}}; output has the same shape.
    """
    out: Dict[str, Dict[str, dict]] = {}
    all_leagues = set(full_results) | set(minimal_results)
    for league in sorted(all_leagues):
        for market in ('oneXtwo', 'ou'):
            chosen = None
            if league in full_results and market in full_results[league]:
                chosen = full_results[league][market]
            elif league in minimal_results and market in minimal_results[league]:
                chosen = minimal_results[league][market]
            if chosen is not None:
                out.setdefault(league, {})[market] = chosen
    return out
