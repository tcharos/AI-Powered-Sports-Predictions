"""NBA winner-probability Platt calibration (single global).

Mirrors the football-side ``ml_project/calibration/{fit,apply}.py`` pattern,
adapted for NBA: one calibrator for ``P(home_win)`` (no league splits, since
the NBA is one league). The total-points regressor is **not** Platt-scaled —
calibration of a continuous target is a different problem; we report a
diagnostic only.

Pipeline
--------
1. Read ``data_sets/NBA/training_data.csv`` (built by ``nba_feature_engineering``).
2. 5-fold ``TimeSeriesSplit``: per fold, fit a fresh ``XGBClassifier`` on the
   same features/params as ``train_nba_models``, predict on the holdout, collect
   the OOF probabilities across all folds.
3. Fit Platt scaling (``a, b`` so ``calibrated = σ(a · logit(p) + b)``) on
   ``(logit(oof_p), y)`` via ``sklearn.LogisticRegression``.
4. Write ``data_sets/NBA/nba_calibration.json``::

       {"home_win_platt": {"a": ..., "b": ...},
        "n_oof": N,
        "brier_before": ..., "brier_after": ...,
        "total_diagnostic": {"mean_pred": ..., "mean_actual": ..., "mean_abs_err": ...},
        "generated_at": "<iso>"}

5. ``apply_home_win_platt`` is the inference helper used by ``predict_nba.py``;
   returns the standard ``(prob, applied, source)`` 3-tuple. Falls back to raw
   if the calibration file is missing or the input is malformed.

The calibration file is loaded on demand; default behavior in
``predict_nba.py`` is **apply if present, raw otherwise** (no flag for v1 —
Phase 3 introduces a ``use_nba_calibration`` flag in ``betting_config.json``
when the NBA sport entry is added).
"""

import datetime
import json
import os
import sys
from functools import lru_cache
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_PATH = os.path.join(_REPO, "data_sets", "NBA", "training_data.csv")
OUT_PATH = os.path.join(_REPO, "data_sets", "NBA", "nba_calibration.json")

_EPS = 1e-6


def _logit(p):
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------------------
# Fit (CLI / retrain pipeline use)
# ---------------------------------------------------------------------------

def fit_calibration(data_path: str = DATA_PATH, out_path: str = OUT_PATH) -> dict:
    # Import lazily so the apply path stays import-cheap.
    from train_nba_models import feature_list, load_params, WINNER_PARAMS

    if not os.path.exists(data_path):
        raise FileNotFoundError(data_path)
    print(f"[calibrate] reading {data_path}")
    df = pd.read_csv(data_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    features = feature_list()
    df = df.dropna(subset=features)
    X, y = df[features], df["home_win"].values
    print(f"  {len(df):,} games × {len(features)} features")

    params = load_params(WINNER_PARAMS, {
        "n_estimators": 200, "max_depth": 5, "learning_rate": 0.05,
        "eval_metric": "logloss", "random_state": 42,
    })

    # 1. Collect OOF probabilities across 5 chronological folds.
    print("  collecting OOF P(home_win) via TimeSeriesSplit ...")
    oof_p = np.full(len(df), np.nan)
    tscv = TimeSeriesSplit(n_splits=5)
    for fold, (tr, te) in enumerate(tscv.split(X), 1):
        clf = XGBClassifier(**params)
        clf.fit(X.iloc[tr], y[tr], verbose=False)
        oof_p[te] = clf.predict_proba(X.iloc[te])[:, 1]
        print(f"    fold {fold}: n_test={len(te)}")
    mask = ~np.isnan(oof_p)
    p_oof, y_oof = oof_p[mask], y[mask]
    print(f"  OOF coverage: {mask.sum():,}/{len(df):,} games (fold 1's train tail isn't OOF)")

    # 2. Fit Platt: logit(p) → linear → sigmoid via LogisticRegression on logit(p).
    z = _logit(p_oof).reshape(-1, 1)
    lr = LogisticRegression(C=1e6, solver="lbfgs")   # large C ⇒ minimal regularisation
    lr.fit(z, y_oof)
    a = float(lr.coef_[0, 0])
    b = float(lr.intercept_[0])

    # 3. Score before vs after.
    brier_before = brier_score_loss(y_oof, p_oof)
    p_cal = _sigmoid(a * _logit(p_oof) + b)
    brier_after = brier_score_loss(y_oof, p_cal)
    print(f"  Platt fit:  a = {a:+.4f}   b = {b:+.4f}")
    print(f"  Brier:      raw = {brier_before:.4f}   calibrated = {brier_after:.4f}   "
          f"Δ = {brier_after - brier_before:+.4f}")
    if brier_after >= brier_before:
        print("  ⚠ calibration did NOT improve Brier — model is already well-calibrated; "
              "applying Platt here would be a no-op or mild hurt. Saving anyway for the audit "
              "trail; the apply path will still use it (Δ is typically tiny when raw is good).")

    # 4. Total-points diagnostic (no fitting — regressor cal is a separate problem).
    import pickle
    total_diag = {}
    total_path = os.path.join(_REPO, "models", "nba", "total_model.pkl")
    if os.path.exists(total_path):
        with open(total_path, "rb") as f:
            total_model = pickle.load(f)
        pred = total_model.predict(X)
        actual = df["total_points"].values
        total_diag = {
            "mean_pred":     round(float(np.mean(pred)), 3),
            "mean_actual":   round(float(np.mean(actual)), 3),
            "mean_abs_err":  round(float(np.mean(np.abs(pred - actual))), 3),
            "bias_pred_minus_actual": round(float(np.mean(pred - actual)), 3),
        }
        print(f"  total diagnostic: mean_pred={total_diag['mean_pred']}  "
              f"mean_actual={total_diag['mean_actual']}  "
              f"bias={total_diag['bias_pred_minus_actual']:+.3f}")

    out = {
        "home_win_platt": {"a": a, "b": b},
        "n_oof": int(mask.sum()),
        "brier_before": float(brier_before),
        "brier_after":  float(brier_after),
        "total_diagnostic": total_diag,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[calibrate] wrote {out_path}")
    return out


# ---------------------------------------------------------------------------
# Apply (predict-time)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def load_calibration_data(path: str = OUT_PATH) -> dict:
    """Cached load — empty dict if the file is missing (apply will no-op)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def apply_home_win_platt(prob: float,
                         calibration_data: dict,
                         enabled: bool = True) -> Tuple[float, bool, Optional[str]]:
    """Calibrate ``P(home_win)`` and return ``(calibrated, applied, source)``.

    Mirrors the football-side ``apply_platt_1x2`` contract (3-tuple), so callers
    can be sport-agnostic. ``applied=False`` means raw was returned (disabled
    or no calibration data); ``source`` is ``'platt'`` on success, else ``None``.
    """
    if not enabled or not calibration_data:
        return float(prob), False, None
    platt = calibration_data.get("home_win_platt") or {}
    a, b = platt.get("a"), platt.get("b")
    if a is None or b is None:
        return float(prob), False, None
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return prob, False, None
    cal = float(_sigmoid(a * _logit(p) + b))
    return cal, True, "platt"


if __name__ == "__main__":
    try:
        fit_calibration()
    except Exception as e:
        print(f"[calibrate] FAIL: {e}", file=sys.stderr)
        sys.exit(1)
