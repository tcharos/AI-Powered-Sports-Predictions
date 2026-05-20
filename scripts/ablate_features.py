"""Ablation harness for the D2 §1.3 opponent-adjusted form features.

Compares baseline (without H_form_pts_w / H_form_xpts / diffs) against
several extensions, reporting per-feature-set CV Brier on both 1X2 and
O/U markets. Same hyperparams and same dropna mask across all sets so
the comparison isolates the feature contribution.

Usage (from repo root):
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/ablate_features.py

Notes:
- Runs prepare_data() once (~10–15 min) — the slow step.
- Per CV fold uses the tuned best_params_*.json so we're comparing
  apples to apples vs. production. We do NOT re-tune per feature set —
  that would conflate feature lift with tuning lift.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import TimeSeriesSplit

# Make ml_project imports resolvable when run from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml_project"))

from train_model import ModelTrainer  # noqa: E402


# The 6 new opp-adjusted form features (D2 §1.3).
FLAVOUR_A_FEATURES = ["H_form_pts_w", "A_form_pts_w", "form_pts_w_diff"]
FLAVOUR_B_FEATURES = ["H_form_xpts", "A_form_xpts", "form_xpts_diff"]
NEW_FEATURES = FLAVOUR_A_FEATURES + FLAVOUR_B_FEATURES


def get_baseline_features(trainer: ModelTrainer) -> list[str]:
    """Common features minus the new opp-adj ones — i.e. pre-D2 state."""
    return [f for f in trainer.common_features if f not in NEW_FEATURES]


def feature_sets(trainer: ModelTrainer) -> dict[str, list[str]]:
    base = get_baseline_features(trainer)
    return {
        "baseline":   base,
        "+ flavour A": base + FLAVOUR_A_FEATURES,
        "+ flavour B": base + FLAVOUR_B_FEATURES,
        "+ both (prod)": base + NEW_FEATURES,
    }


def multiclass_brier(y_true: np.ndarray, y_prob: np.ndarray, n_classes: int = 3) -> float:
    """Mean over samples of sum_c (p_c - y_c)^2 — multi-class Brier."""
    onehot = np.eye(n_classes)[y_true.astype(int)]
    return float(np.mean(np.sum((y_prob - onehot) ** 2, axis=1)))


def load_params(path: str, default: dict) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            params = json.load(f)
        return params
    return default.copy()


def run_1x2_cv(df: pd.DataFrame, features: list[str], params: dict) -> dict:
    """5-fold TimeSeriesSplit CV. Returns global stats + OOF predictions
    (pooled across folds) so caller can do per-league analysis."""
    df_cv = df.sort_values("date").reset_index(drop=True).copy()
    if "league_cat" in df_cv.columns:
        df_cv["league_cat"] = df_cv["league_cat"].astype("category")

    tscv = TimeSeriesSplit(n_splits=5)
    briers, lls, accs = [], [], []
    oof_idx, oof_probs = [], []

    for train_idx, test_idx in tscv.split(df_cv):
        tr = df_cv.iloc[train_idx]
        te = df_cv.iloc[test_idx]

        model = xgb.XGBClassifier(**params)
        model.fit(tr[features], tr["target_1x2"],
                  eval_set=[(te[features], te["target_1x2"])], verbose=False)

        probs = model.predict_proba(te[features])
        preds = model.predict(te[features])
        y_true = te["target_1x2"].values

        briers.append(multiclass_brier(y_true, probs, n_classes=3))
        lls.append(log_loss(y_true, probs, labels=[0, 1, 2]))
        accs.append(float(np.mean(preds == y_true)))

        oof_idx.append(test_idx)
        oof_probs.append(probs)

    oof_idx = np.concatenate(oof_idx)
    oof_probs = np.concatenate(oof_probs)
    return {
        "brier_mean": float(np.mean(briers)),
        "brier_std":  float(np.std(briers)),
        "ll_mean":    float(np.mean(lls)),
        "acc_mean":   float(np.mean(accs)),
        "n_samples":  int(len(df_cv)),
        "_oof_idx":   oof_idx,
        "_oof_probs": oof_probs,
    }


def run_ou_cv(df: pd.DataFrame, features: list[str], params: dict) -> dict:
    """OU 2.5 Poisson regression. Returns mean/std Brier on P(over) +
    OOF P(over) (pooled across folds) for per-league analysis."""
    df_cv = df.sort_values("date").reset_index(drop=True).copy()
    if "league_cat" in df_cv.columns:
        df_cv["league_cat"] = df_cv["league_cat"].astype("category")

    # Coerce binary-objective best_params to Poisson (mirrors train_ou).
    params = params.copy()
    params["objective"] = "count:poisson"
    if params.get("eval_metric") in {"logloss", "error"}:
        params["eval_metric"] = "poisson-nloglik"

    tscv = TimeSeriesSplit(n_splits=5)
    briers, lls = [], []
    oof_idx, oof_pover = [], []

    for train_idx, test_idx in tscv.split(df_cv):
        tr = df_cv.iloc[train_idx]
        te = df_cv.iloc[test_idx]

        model = xgb.XGBRegressor(**params)
        model.fit(tr[features], tr["total_goals"],
                  eval_set=[(te[features], te["total_goals"])], verbose=False)

        lam = model.predict(te[features])
        # P(X<=2) for Poisson, then P(over 2.5) = 1 - P(X<=2)
        p_le2 = np.exp(-lam) * (1 + lam + (lam ** 2) / 2)
        p_over = np.clip(1.0 - p_le2, 0.001, 0.999)
        y_true = (te["total_goals"] > 2.5).astype(int).values

        briers.append(brier_score_loss(y_true, p_over))
        lls.append(log_loss(y_true, p_over, labels=[0, 1]))

        oof_idx.append(test_idx)
        oof_pover.append(p_over)

    oof_idx = np.concatenate(oof_idx)
    oof_pover = np.concatenate(oof_pover)
    return {
        "brier_mean": float(np.mean(briers)),
        "brier_std":  float(np.std(briers)),
        "ll_mean":    float(np.mean(lls)),
        "n_samples":  int(len(df_cv)),
        "_oof_idx":   oof_idx,
        "_oof_pover": oof_pover,
    }


def per_league_1x2_brier(df: pd.DataFrame, oof_idx: np.ndarray,
                         oof_probs: np.ndarray, min_n: int = 100) -> dict:
    """Group OOF predictions by league, compute multiclass Brier per league."""
    df_sorted = df.sort_values("date").reset_index(drop=True)
    leagues = df_sorted["league"].iloc[oof_idx].values
    y_true = df_sorted["target_1x2"].iloc[oof_idx].values

    result = {}
    for lg in np.unique(leagues):
        mask = leagues == lg
        n = int(mask.sum())
        if n < min_n:
            continue
        result[str(lg)] = {
            "brier": multiclass_brier(y_true[mask], oof_probs[mask], n_classes=3),
            "n": n,
        }
    return result


def per_league_ou_brier(df: pd.DataFrame, oof_idx: np.ndarray,
                        oof_pover: np.ndarray, min_n: int = 100) -> dict:
    """Group OOF predictions by league, compute binary Brier on P(over)."""
    df_sorted = df.sort_values("date").reset_index(drop=True)
    leagues = df_sorted["league"].iloc[oof_idx].values
    y_true = (df_sorted["total_goals"].iloc[oof_idx] > 2.5).astype(int).values

    result = {}
    for lg in np.unique(leagues):
        mask = leagues == lg
        n = int(mask.sum())
        if n < min_n:
            continue
        result[str(lg)] = {
            "brier": float(brier_score_loss(y_true[mask], oof_pover[mask])),
            "n": n,
        }
    return result


def print_per_league_table(market_name: str, per_league_results: dict,
                           set_names: list[str]) -> None:
    """Print a sorted table: per-league Brier baseline + Δ% for each variant."""
    baseline_lookup = per_league_results["baseline"]
    rows = []
    for lg, base_stats in baseline_lookup.items():
        row = {"league": lg, "n": base_stats["n"],
               "baseline": base_stats["brier"]}
        for s in set_names:
            if s == "baseline":
                continue
            other = per_league_results.get(s, {}).get(lg)
            if other is None:
                row[s] = None
                continue
            delta_pct = 100.0 * (other["brier"] - base_stats["brier"]) / base_stats["brier"]
            row[s] = delta_pct
        rows.append(row)

    # Sort by best improvement available across any variant (most-negative delta).
    def best_delta(r):
        deltas = [r[s] for s in set_names if s != "baseline" and r.get(s) is not None]
        return min(deltas) if deltas else 0

    rows.sort(key=best_delta)

    variant_cols = [s for s in set_names if s != "baseline"]
    header = f"  {'league':<35} {'n':>6} {'baseline':>10}  " + \
             "  ".join([f"{s:>14}" for s in variant_cols])
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        cells = [f"{r['league']:<35}", f"{r['n']:>6}", f"{r['baseline']:>10.4f}"]
        for s in variant_cols:
            val = r.get(s)
            if val is None:
                cells.append(f"{'—':>14}")
            else:
                marker = " ✅" if val <= -1.0 else (" ❌" if val >= 1.0 else "  ")
                cells.append(f"{val:+9.2f}%{marker:>3}")
        print("  " + "  ".join(cells))

    # Summary: how many leagues improve by ≥1% in each variant?
    print()
    for s in variant_cols:
        deltas = [r[s] for r in rows if r.get(s) is not None]
        improved = sum(1 for d in deltas if d <= -1.0)
        regressed = sum(1 for d in deltas if d >= 1.0)
        neutral = len(deltas) - improved - regressed
        print(f"    {s:<14} | improved ≥1%: {improved}/{len(deltas)} "
              f"| regressed ≥1%: {regressed} | neutral: {neutral}")


def fmt_pct_delta(new: float, base: float) -> str:
    """Format relative Brier change vs baseline. Negative = improvement."""
    if base == 0:
        return "n/a"
    pct = 100.0 * (new - base) / base
    sign = "-" if pct < 0 else "+"
    marker = " ✅" if pct <= -1.0 else (" ❌" if pct >= 1.0 else "")
    return f"{sign}{abs(pct):.2f}%{marker}"


def main() -> None:
    print("=" * 72)
    print("D2 §1.3 Opponent-adjusted form — ablation harness")
    print("=" * 72)

    trainer = ModelTrainer("data_sets/MatchHistory")
    print("\nPreparing data (slow ~10–15 min, runs feature engineering once)...")
    df = trainer.prepare_data()
    print(f"Prepared rows: {len(df)}")

    sets = feature_sets(trainer)
    print(f"\nFeature sets ({len(sets)}):")
    for name, feats in sets.items():
        delta = set(feats) - set(sets["baseline"])
        print(f"  {name:<14} | n_features={len(feats)} | extras={sorted(delta) or '—'}")

    # Consistent dropna: union of every feature across sets + targets.
    all_features = sorted({f for feats in sets.values() for f in feats})
    needed = list(set(all_features + ["B365H", "B365D", "B365A",
                                      "H_form_ou", "A_form_ou",
                                      "target_1x2", "total_goals"]))
    df_full = df.dropna(subset=needed).copy()
    print(f"\nAfter consistent dropna: {len(df_full)} rows "
          f"(was {len(df)} → dropped {len(df) - len(df_full)}).")

    params_1x2 = load_params("models/best_params_1x2.json", default={
        "objective": "multi:softprob", "num_class": 3,
        "n_estimators": 100, "learning_rate": 0.1, "max_depth": 5,
        "eval_metric": "mlogloss", "early_stopping_rounds": 10,
        "tree_method": "hist", "enable_categorical": True,
    })
    params_ou = load_params("models/best_params_ou.json", default={
        "objective": "count:poisson", "n_estimators": 100,
        "learning_rate": 0.1, "max_depth": 5,
        "eval_metric": "poisson-nloglik", "early_stopping_rounds": 10,
        "tree_method": "hist", "enable_categorical": True,
    })

    # 1X2 ablation
    print("\n" + "-" * 72)
    print("1X2 market — TimeSeriesSplit 5-fold CV")
    print("-" * 72)
    results_1x2 = {}
    for name, feats in sets.items():
        # 1X2 uses B365H/D/A on top of common_features (mirror train_1x2)
        full_feats = list(dict.fromkeys(["B365H", "B365D", "B365A"] + feats))
        r = run_1x2_cv(df_full, full_feats, params_1x2)
        results_1x2[name] = r
        print(f"  {name:<14} | Brier {r['brier_mean']:.4f} ± {r['brier_std']:.4f} "
              f"| LogLoss {r['ll_mean']:.4f} | Acc {r['acc_mean']:.4f}")

    base_brier = results_1x2["baseline"]["brier_mean"]
    print("\n  Δ Brier vs baseline:")
    for name, r in results_1x2.items():
        if name == "baseline":
            continue
        delta = r["brier_mean"] - base_brier
        print(f"    {name:<14} | {delta:+.5f}  ({fmt_pct_delta(r['brier_mean'], base_brier)})")

    # O/U ablation
    print("\n" + "-" * 72)
    print("O/U 2.5 market — TimeSeriesSplit 5-fold CV")
    print("-" * 72)
    results_ou = {}
    for name, feats in sets.items():
        # OU uses B365H/D/A + H_form_ou/A_form_ou on top of common_features
        full_feats = list(dict.fromkeys(
            ["B365H", "B365D", "B365A", "H_form_ou", "A_form_ou"] + feats
        ))
        r = run_ou_cv(df_full, full_feats, params_ou)
        results_ou[name] = r
        print(f"  {name:<14} | Brier {r['brier_mean']:.4f} ± {r['brier_std']:.4f} "
              f"| LogLoss {r['ll_mean']:.4f}")

    base_brier = results_ou["baseline"]["brier_mean"]
    print("\n  Δ Brier vs baseline:")
    for name, r in results_ou.items():
        if name == "baseline":
            continue
        delta = r["brier_mean"] - base_brier
        print(f"    {name:<14} | {delta:+.5f}  ({fmt_pct_delta(r['brier_mean'], base_brier)})")

    # Per-league breakdown (D2 §1.3 acceptance gate: ≥1% globally OR
    # no league regresses meaningfully — option 2 check).
    print("\n" + "=" * 72)
    print("PER-LEAGUE BREAKDOWN — 1X2 (OOF pooled across folds, n ≥ 100)")
    print("=" * 72)
    per_league_1x2 = {
        name: per_league_1x2_brier(df_full, r["_oof_idx"], r["_oof_probs"])
        for name, r in results_1x2.items()
    }
    print_per_league_table("1X2", per_league_1x2, list(sets.keys()))

    print("\n" + "=" * 72)
    print("PER-LEAGUE BREAKDOWN — O/U 2.5 (OOF pooled across folds, n ≥ 100)")
    print("=" * 72)
    per_league_ou = {
        name: per_league_ou_brier(df_full, r["_oof_idx"], r["_oof_pover"])
        for name, r in results_ou.items()
    }
    print_per_league_table("O/U", per_league_ou, list(sets.keys()))

    # Persist for later inspection. Strip OOF arrays from results dicts
    # before serialising (NumPy arrays aren't JSON-friendly).
    def _strip(r):
        return {k: v for k, v in r.items() if not k.startswith("_")}
    out_dir = ROOT / "output" / "ablation"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d-%H%M%S")
    out = {
        "timestamp": ts,
        "n_rows_after_dropna": len(df_full),
        "sets": {name: feats for name, feats in sets.items()},
        "results_1x2": {k: _strip(v) for k, v in results_1x2.items()},
        "results_ou": {k: _strip(v) for k, v in results_ou.items()},
        "per_league_1x2": per_league_1x2,
        "per_league_ou": per_league_ou,
    }
    out_path = out_dir / f"ablate_oppform_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nResults saved to {out_path}")

    # Prune older ablation outputs — keep latest 3 (rare runs; small
    # buffer in case of comparison across consecutive iterations).
    _KEEP = 3
    import glob as _glob
    _old = sorted(_glob.glob(str(out_dir / "ablate_oppform_*.json")))[:-_KEEP]
    for _f in _old:
        try: os.remove(_f)
        except OSError: pass
    if _old:
        print(f"Pruned {len(_old)} older ablation output(s) (kept latest {_KEEP}).")

    # Verdict heuristic.
    print("\n" + "=" * 72)
    print("Acceptance gate (≥1% Brier improvement = ✅):")
    for market, results in [("1X2", results_1x2), ("O/U", results_ou)]:
        base = results["baseline"]["brier_mean"]
        for name in ["+ flavour A", "+ flavour B", "+ both (prod)"]:
            new = results[name]["brier_mean"]
            pct = 100.0 * (new - base) / base
            verdict = "KEEP ✅" if pct <= -1.0 else ("DROP ❌" if pct >= 0.0 else "marginal —")
            print(f"  {market:<4} | {name:<14} | Δ {pct:+.2f}% | {verdict}")
    print("=" * 72)


if __name__ == "__main__":
    main()
