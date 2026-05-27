"""National-teams / D7 steps 2-3 — features + train 1X2 & O/U models.

Odds-free (eloratings has no odds). The dominant signal is ELO, so we hand the
model an explicit Elo expected-score (home advantage zeroed on neutral venues —
critical for WC, where most matches are neutral), plus competition type and
rolling international form. Validated with 5-fold TimeSeriesSplit and benchmarked
against an Elo-expectation-only baseline so we know the extras earn their place.

Reads data_sets/national_teams/international_matches.csv (from build_dataset.py).
Saves models -> models/national_teams/{winner,total}.json + features_nt.json.

Usage:
    python3 scripts/national_teams/train_nt.py [--since 2002] [--no-save]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parent.parent.parent
CSV = ROOT / "data_sets" / "national_teams" / "international_matches.csv"
MODEL_DIR = ROOT / "models" / "national_teams"

HOME_ADV_ELO = 100  # eloratings' home-field bonus (~100 Elo pts); 0 if neutral
FORM_N = 5          # rolling window over a team's last N internationals

FEATURES = [
    "elo_diff", "abs_elo_diff", "home_elo_pre", "away_elo_pre", "elo_exp",
    "neutral", "is_friendly",
    "home_form_pts", "home_form_gd", "home_form_gf",
    "away_form_pts", "away_form_gd", "away_form_gf",
]
ELO_ONLY = ["elo_exp", "elo_diff", "neutral"]  # baseline


def multiclass_brier(y_true, y_prob, n=3):
    onehot = np.eye(n)[y_true.astype(int)]
    return float(np.mean(np.sum((y_prob - onehot) ** 2, axis=1)))


def rolling_form(df: pd.DataFrame) -> pd.DataFrame:
    """Per-team last-N form (pts/goal-diff/goals-for), shifted to exclude the
    current match (no leakage). Built from a long (team-per-row) view."""
    rows = []
    for _, r in df.iterrows():
        rows.append((r["idx"], r["date"], r["home_team"], r["home_score"], r["away_score"], "H"))
        rows.append((r["idx"], r["date"], r["away_team"], r["away_score"], r["home_score"], "A"))
    lf = pd.DataFrame(rows, columns=["idx", "date", "team", "gf", "ga", "side"])
    lf["pts"] = np.where(lf.gf > lf.ga, 3, np.where(lf.gf == lf.ga, 1, 0))
    lf["gd"] = lf.gf - lf.ga
    lf = lf.sort_values(["team", "date"]).reset_index(drop=True)
    g = lf.groupby("team")
    for col in ("pts", "gd", "gf"):
        lf[f"form_{col}"] = (g[col].shift(1)
                             .groupby(lf["team"]).rolling(FORM_N, min_periods=1).mean()
                             .reset_index(level=0, drop=True))
    home = lf[lf.side == "H"].set_index("idx")[[f"form_{c}" for c in ("pts", "gd", "gf")]]
    away = lf[lf.side == "A"].set_index("idx")[[f"form_{c}" for c in ("pts", "gd", "gf")]]
    home.columns = ["home_form_pts", "home_form_gd", "home_form_gf"]
    away.columns = ["away_form_pts", "away_form_gd", "away_form_gf"]
    return home.join(away)


def build(since: int) -> pd.DataFrame:
    df = pd.read_csv(CSV, parse_dates=["date"])
    df = df[df["date"].dt.year >= since].reset_index(drop=True)
    df["idx"] = df.index
    df["elo_diff"] = df.home_elo_pre - df.away_elo_pre
    df["abs_elo_diff"] = df.elo_diff.abs()
    df["home_adv"] = np.where(df.neutral == 1, 0, HOME_ADV_ELO)
    df["elo_exp"] = 1.0 / (1.0 + 10 ** (-(df.elo_diff + df.home_adv) / 400.0))
    df["is_friendly"] = (df["comp"] == "F").astype(int)
    df["target_1x2"] = np.where(df.home_score > df.away_score, 0,
                                np.where(df.home_score == df.away_score, 1, 2))
    df["total_goals"] = df.home_score + df.away_score
    df = df.join(rolling_form(df))
    return df.sort_values("date").reset_index(drop=True)


def cv_1x2(df, feats, params):
    tscv = TimeSeriesSplit(n_splits=5)
    briers, lls, accs = [], [], []
    for tr_i, te_i in tscv.split(df):
        tr, te = df.iloc[tr_i], df.iloc[te_i]
        m = xgb.XGBClassifier(**params)
        m.fit(tr[feats], tr.target_1x2, eval_set=[(te[feats], te.target_1x2)], verbose=False)
        p = m.predict_proba(te[feats])
        briers.append(multiclass_brier(te.target_1x2.values, p))
        lls.append(log_loss(te.target_1x2, p, labels=[0, 1, 2]))
        accs.append(float((m.predict(te[feats]) == te.target_1x2).mean()))
    return np.mean(briers), np.mean(lls), np.mean(accs)


def cv_ou(df, feats, params):
    params = {**params, "objective": "count:poisson", "eval_metric": "poisson-nloglik"}
    tscv = TimeSeriesSplit(n_splits=5)
    briers, accs = [], []
    for tr_i, te_i in tscv.split(df):
        tr, te = df.iloc[tr_i], df.iloc[te_i]
        m = xgb.XGBRegressor(**params)
        m.fit(tr[feats], tr.total_goals, eval_set=[(te[feats], te.total_goals)], verbose=False)
        lam = np.clip(m.predict(te[feats]), 1e-3, None)
        p_le2 = np.exp(-lam) * (1 + lam + lam ** 2 / 2)
        p_over = np.clip(1 - p_le2, 1e-3, 1 - 1e-3)
        y = (te.total_goals > 2.5).astype(int)
        briers.append(brier_score_loss(y, p_over))
        accs.append(float(((p_over > 0.5).astype(int) == y).mean()))
    return np.mean(briers), np.mean(accs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=2002)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    df = build(args.since)
    print(f"training rows (since {args.since}): {len(df)}  "
          f"({df.date.min().date()}→{df.date.max().date()})")
    base = df.target_1x2.value_counts(normalize=True).sort_index()
    print(f"outcome base rates  H={base[0]:.3f} D={base[1]:.3f} A={base[2]:.3f}  "
          f"| over2.5 rate={ (df.total_goals>2.5).mean():.3f}")

    p1 = {"objective": "multi:softprob", "num_class": 3, "n_estimators": 300,
          "learning_rate": 0.05, "max_depth": 4, "min_child_weight": 5,
          "subsample": 0.8, "colsample_bytree": 0.8, "eval_metric": "mlogloss",
          "early_stopping_rounds": 20, "tree_method": "hist"}
    po = {"n_estimators": 300, "learning_rate": 0.05, "max_depth": 4,
          "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.8,
          "early_stopping_rounds": 20, "tree_method": "hist"}

    print("\n1X2 (5-fold TimeSeriesSplit):")
    bb, bl, ba = cv_1x2(df, ELO_ONLY, p1)
    print(f"  elo-only  | Brier {bb:.4f} | LogLoss {bl:.4f} | Acc {ba:.4f}")
    fb, fl, fa = cv_1x2(df, FEATURES, p1)
    print(f"  full      | Brier {fb:.4f} | LogLoss {fl:.4f} | Acc {fa:.4f}"
          f"   (Δacc {fa-ba:+.4f}, ΔBrier {fb-bb:+.4f})")

    print("\nO/U 2.5 (5-fold TimeSeriesSplit):")
    ob, oa = cv_ou(df, [f for f in FEATURES if f != "is_friendly"] + ["is_friendly"], po)
    print(f"  full      | Brier {ob:.4f} | Acc {oa:.4f}")

    if not args.no_save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        m1 = xgb.XGBClassifier(**{k: v for k, v in p1.items() if k != "early_stopping_rounds"})
        m1.fit(df[FEATURES], df.target_1x2)
        m1.save_model(MODEL_DIR / "winner.json")
        mo = xgb.XGBRegressor(**{k: v for k, v in po.items() if k != "early_stopping_rounds"},
                              objective="count:poisson")
        mo.fit(df[FEATURES], df.total_goals)
        mo.save_model(MODEL_DIR / "total.json")
        json.dump({"features": FEATURES, "home_adv_elo": HOME_ADV_ELO,
                   "form_n": FORM_N, "trained_since": args.since,
                   "n_rows": len(df)},
                  open(MODEL_DIR / "features_nt.json", "w"), indent=2)
        print(f"\nsaved models -> {MODEL_DIR}")


if __name__ == "__main__":
    main()
