"""Train the NBA winner + total models on the enhanced feature matrix.

Reads ``data_sets/NBA/training_data.csv`` (built by ``nba_feature_engineering.py``
from the canonical per-game corpus). Two models, same structure as before but
on the expanded feature set:

* **Winner** — ``XGBClassifier``, target ``home_win`` (binary).
* **Total** — ``XGBRegressor``, target ``total_points``.

Both: 5-fold TimeSeriesSplit CV for honest accuracy/MAE reporting, then a final
fit on (95 % train, 5 % validation tail). Models pickled to
``models/nba/{winner,total}_model.pkl``; the **feature list** is dumped to
``models/nba/features_{winner,total}.json`` so the predictor reads exactly the
columns the trainer used (no implicit drift, mirrors the football side).

Tuned hyperparameters are loaded from ``best_params_{winner,total}.json`` if
present (defaults: depth-5 trees, 100 estimators, lr 0.1). Retune via
``tune_nba_models.py`` once the feature set stabilizes.
"""

import json
import os
import pickle
import sys
from typing import List

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier, XGBRegressor


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_PATH = os.path.join(_REPO, "data_sets", "NBA", "training_data.csv")
MODEL_DIR = os.path.join(_REPO, "models", "nba")
WINNER_MODEL = os.path.join(MODEL_DIR, "winner_model.pkl")
TOTAL_MODEL  = os.path.join(MODEL_DIR, "total_model.pkl")
WINNER_PARAMS = os.path.join(MODEL_DIR, "best_params_winner.json")
TOTAL_PARAMS  = os.path.join(MODEL_DIR, "best_params_total.json")
WINNER_FEATURES = os.path.join(MODEL_DIR, "features_winner.json")
TOTAL_FEATURES  = os.path.join(MODEL_DIR, "features_total.json")

# Feature naming convention — see nba_feature_engineering.py:
#   home_/away_ prefix in wide format; metric blocks: rest/b2b/elo + l5_* + l10_* + venue_l5_*.
SCHEDULE_FEATS = ("rest_days", "b2b", "elo_pre")
L5_METRICS  = ("pts", "pts_allowed", "win", "fg_pct", "fg3_pct", "ft_pct",
               "reb", "ast", "tov", "plus_minus")
L10_METRICS = ("pts", "pts_allowed", "win", "plus_minus")
VENUE_METRICS = ("pts", "pts_allowed", "win")


def feature_list() -> List[str]:
    feats: list[str] = []
    for side in ("home", "away"):
        for m in SCHEDULE_FEATS:
            feats.append(f"{side}_{m}")
        for m in L5_METRICS:
            feats.append(f"{side}_l5_{m}")
        for m in L10_METRICS:
            feats.append(f"{side}_l10_{m}")
        for m in VENUE_METRICS:
            feats.append(f"{side}_venue_l5_{m}")
    return feats


def load_params(path: str, default: dict) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            print(f"  loaded tuned params from {os.path.basename(path)}")
            return json.load(f)
    print(f"  no tuned params at {os.path.basename(path)} → using defaults")
    return default


def train() -> int:
    if not os.path.exists(DATA_PATH):
        print(f"missing training data: {DATA_PATH}", file=sys.stderr)
        return 1
    os.makedirs(MODEL_DIR, exist_ok=True)

    print(f"[train] reading {DATA_PATH}")
    df = pd.read_csv(DATA_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    print(f"  {len(df):,} games, date {df['date'].min().date()} → {df['date'].max().date()}")

    features = feature_list()
    missing = [f for f in features if f not in df.columns]
    if missing:
        print(f"[train] FAIL — training_data missing cols: {missing}", file=sys.stderr)
        return 1
    df = df.dropna(subset=features)
    print(f"  after dropna(features): {len(df):,} rows × {len(features)} features")

    X = df[features]
    y_win   = df["home_win"]
    y_total = df["total_points"]
    tscv = TimeSeriesSplit(n_splits=5)

    # ------------------------------------------------------------------
    # Winner — XGBClassifier
    # ------------------------------------------------------------------
    print("\n🏀 winner (XGBClassifier)")
    params_w = load_params(WINNER_PARAMS, {
        "n_estimators": 200, "max_depth": 5, "learning_rate": 0.05,
        "eval_metric": "logloss", "random_state": 42,
    })

    accs, briers = [], []
    for fold, (tr, te) in enumerate(tscv.split(X), 1):
        clf = XGBClassifier(**params_w)
        clf.fit(X.iloc[tr], y_win.iloc[tr], verbose=False)
        proba = clf.predict_proba(X.iloc[te])[:, 1]
        pred  = (proba >= 0.5).astype(int)
        acc = accuracy_score(y_win.iloc[te], pred)
        br  = brier_score_loss(y_win.iloc[te], proba)
        accs.append(acc); briers.append(br)
        print(f"  fold {fold}: acc={acc:.4f}  brier={br:.4f}  (n_test={len(te)})")
    print(f"  TS-CV mean: acc={np.mean(accs):.4f}  brier={np.mean(briers):.4f}")

    # Final fit on 95% with the last 5% as eval set (for early-stopping signal).
    split = int(len(df) * 0.95)
    X_tr, X_va = X.iloc[:split], X.iloc[split:]
    y_tr, y_va = y_win.iloc[:split], y_win.iloc[split:]
    final_w = XGBClassifier(**params_w)
    final_w.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)

    top = sorted(zip(features, final_w.feature_importances_), key=lambda x: -x[1])[:8]
    print("  top predictors:")
    for name, imp in top:
        print(f"    {imp:.4f}  {name}")

    with open(WINNER_MODEL, "wb") as f:
        pickle.dump(final_w, f)
    with open(WINNER_FEATURES, "w") as f:
        json.dump(features, f, indent=2)
    print(f"  → {WINNER_MODEL}\n  → {WINNER_FEATURES}")

    # ------------------------------------------------------------------
    # Total — XGBRegressor
    # ------------------------------------------------------------------
    print("\n🔢 total (XGBRegressor)")
    params_t = load_params(TOTAL_PARAMS, {
        "n_estimators": 200, "max_depth": 5, "learning_rate": 0.05,
        "random_state": 42,
    })

    maes, r2s = [], []
    for fold, (tr, te) in enumerate(tscv.split(X), 1):
        reg = XGBRegressor(**params_t)
        reg.fit(X.iloc[tr], y_total.iloc[tr], verbose=False)
        pred = reg.predict(X.iloc[te])
        mae = mean_absolute_error(y_total.iloc[te], pred)
        r2  = r2_score(y_total.iloc[te], pred)
        maes.append(mae); r2s.append(r2)
        print(f"  fold {fold}: MAE={mae:.2f}  R²={r2:.3f}  (n_test={len(te)})")
    print(f"  TS-CV mean: MAE={np.mean(maes):.2f}  R²={np.mean(r2s):.3f}")

    y_tot_tr, y_tot_va = y_total.iloc[:split], y_total.iloc[split:]
    final_t = XGBRegressor(**params_t)
    final_t.fit(X_tr, y_tot_tr, eval_set=[(X_va, y_tot_va)], verbose=False)

    with open(TOTAL_MODEL, "wb") as f:
        pickle.dump(final_t, f)
    with open(TOTAL_FEATURES, "w") as f:
        json.dump(features, f, indent=2)
    print(f"  → {TOTAL_MODEL}\n  → {TOTAL_FEATURES}")

    return 0


if __name__ == "__main__":
    sys.exit(train())
