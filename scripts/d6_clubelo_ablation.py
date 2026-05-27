"""D6 — ClubElo OOF ablation: does ClubElo add Brier lift vs our home-grown ELO?

Scope: Big-5 top divisions (densest ClubElo coverage, cleanest name-join). Three
arms, same hyperparams + same dropna mask + same matches, so the comparison
isolates the ELO source:
  * baseline      — production features (our H_elo/A_elo + elo diffs)
  * + clubelo      — baseline PLUS ClubElo (augment — the D6 hypothesis)
  * clubelo only  — baseline with our ELO swapped OUT for ClubElo (replace)

Reuses ablate_features' CV machinery (TimeSeriesSplit 5-fold, multiclass Brier,
per-league breakdown). Reads the cached prepared DF (scripts/d6_cache_prepared.py)
so we don't re-run the slow prepare_data(). ClubElo point-in-time ELO comes from
soccerdata.ClubElo.read_team_history (indexed by 'from', with a 'to' column).

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/d6_clubelo_ablation.py
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml_project"))

from train_model import ModelTrainer  # noqa: E402
from ablate_features import (  # noqa: E402
    run_1x2_cv, run_ou_cv, load_params, fmt_pct_delta,
    per_league_1x2_brier, per_league_ou_brier, print_per_league_table,
)

CACHE_DIR = ROOT / "output" / "d6_cache"
PREPARED = CACHE_DIR / "prepared.pkl"
HIST_CACHE = CACHE_DIR / "clubelo_hist.pkl"
RESOLVED_CACHE = CACHE_DIR / "clubelo_resolved.json"

CLUBELO_FEATS = ["H_clubelo", "A_clubelo", "clubelo_diff", "abs_clubelo_diff"]
OUR_ELO_FEATS = ["H_elo", "A_elo", "elo_diff", "abs_elo_diff"]

# Explicit prepared-DF 'league' label -> ClubElo country code. Only European
# leagues ClubElo covers; built from the disambiguated labels (the name-labels
# are single foreign countries — 'Premier League'=Russia, 'Bundesliga'=Austria,
# 'Serie A'=Brazil[EXCLUDED, no ClubElo]). Resolution is WITHIN this country,
# so the global-fuzzy false positives (New York City->Man City) can't happen.
LABEL_COUNTRY = {
    "E0": "ENG", "E1": "ENG", "E2": "ENG", "E3": "ENG", "EC": "ENG",
    "SP1": "ESP", "SP2": "ESP", "I1": "ITA", "I2": "ITA",
    "D1": "GER", "D2": "GER", "F1": "FRA", "F2": "FRA",
    "N1": "NED", "B1": "BEL", "P1": "POR", "T1": "TUR", "G1": "GRE",
    "SC0": "SCO", "SC1": "SCO", "SC2": "SCO", "SC3": "SCO",
    "Premier League": "RUS", "Bundesliga": "AUT", "Superliga": "DEN",
    "Eliteserien": "NOR", "Allsvenskan": "SWE", "Ekstraklasa": "POL",
    "Veikkausliiga": "FIN", "Premier Division": "IRL",
}
FUZZ_MIN = 76  # within-country (≤~44 clubs) — safe; legit transliterations ~78


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.lower().strip()


def resolve_teams(team_country_pairs: set):
    """Resolve (team, country) -> ClubElo name by matching WITHIN that country's
    ClubElo universe. Returns (resolved: {(team, cc): ce_name}, ClubEloLookup).
    """
    import soccerdata as sd
    import pickle

    ce = sd.ClubElo()
    allclubs = ce.read_by_date()  # index = ClubElo team name; has 'country'
    # per-country normalized universe
    universe = {}  # cc -> {norm_name: ce_name}
    for ce_name, country in zip(allclubs.index, allclubs["country"]):
        universe.setdefault(country, {}).setdefault(_norm(ce_name), ce_name)

    resolved = {}
    for team, cc in team_country_pairs:
        nm = universe.get(cc)
        if not nm:
            continue
        m = process.extractOne(_norm(team), list(nm.keys()), scorer=fuzz.WRatio)
        if m and m[1] >= FUZZ_MIN:
            resolved[(team, cc)] = nm[m[0]]

    histories = {}
    if HIST_CACHE.exists():
        with open(HIST_CACHE, "rb") as f:
            histories = pickle.load(f)
    for ce_name in set(resolved.values()):
        if ce_name not in histories:
            try:
                histories[ce_name] = ce.read_team_history(ce_name)
            except Exception as e:
                print(f"  ! history fetch failed for {ce_name!r}: {e!r}")
                histories[ce_name] = None
    with open(HIST_CACHE, "wb") as f:
        pickle.dump(histories, f)
    with open(RESOLVED_CACHE, "w") as f:
        json.dump({f"{t}|{cc}": v for (t, cc), v in resolved.items()},
                  f, indent=2, ensure_ascii=False)

    return resolved, ClubEloLookup(histories)


class ClubEloLookup:
    """Point-in-time ELO: for a date, the row where from <= date <= to."""
    def __init__(self, histories: dict):
        self.h = {}
        for name, df in histories.items():
            if df is None or len(df) == 0:
                continue
            d = df.sort_index()
            self.h[name] = (
                d.index.values.astype("datetime64[ns]"),
                d["to"].values.astype("datetime64[ns]"),
                d["elo"].values.astype(float),
            )

    def get(self, ce_name, date) -> float:
        rec = self.h.get(ce_name)
        if rec is None:
            return np.nan
        froms, tos, elos = rec
        d = np.datetime64(pd.Timestamp(date), "ns")
        i = int(np.searchsorted(froms, d, side="right")) - 1
        if i < 0:
            return np.nan
        return float(elos[i]) if d <= tos[i] else np.nan


def attach_clubelo(df: pd.DataFrame, resolved, lookup) -> pd.DataFrame:
    """Add point-in-time ClubElo. Country is set by the league label (single
    country per label), so home/away are the same country by construction."""
    df = df.copy()
    df["_cc"] = df["league"].map(LABEL_COUNTRY)
    hc, ac = [], []
    for cc, ht, at, dt in zip(df["_cc"], df["home_team"], df["away_team"], df["date"]):
        hn = resolved.get((ht, cc)) if cc else None
        an = resolved.get((at, cc)) if cc else None
        hc.append(lookup.get(hn, dt) if hn else np.nan)
        ac.append(lookup.get(an, dt) if an else np.nan)
    df["H_clubelo"] = hc
    df["A_clubelo"] = ac
    df["clubelo_diff"] = df["H_clubelo"] - df["A_clubelo"]
    df["abs_clubelo_diff"] = df["clubelo_diff"].abs()
    return df


def main():
    if not PREPARED.exists():
        sys.exit(f"missing {PREPARED} — run scripts/d6_cache_prepared.py first")
    df = pd.read_pickle(PREPARED)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    print(f"loaded prepared: {len(df)} rows")

    df["_cc"] = df["league"].map(LABEL_COUNTRY)
    eu = df[df["_cc"].notna()]
    print(f"European-labelled rows: {len(eu)} across {eu['_cc'].nunique()} countries")
    pairs = set(zip(eu["home_team"], eu["_cc"])) | set(zip(eu["away_team"], eu["_cc"]))
    print(f"resolving {len(pairs)} (team,country) pairs within-country...")
    resolved, lookup = resolve_teams(pairs)
    print(f"  resolved {len(resolved)}/{len(pairs)} pairs to ClubElo")

    df = attach_clubelo(df, resolved, lookup)
    covered = df.dropna(subset=CLUBELO_FEATS).copy()
    print(f"\nClubElo-covered matches (both teams, same country): "
          f"{len(covered)}/{len(df)} ({100*len(covered)/len(df):.1f}%)")
    print("  by ClubElo country:", covered["_cc"].value_counts().head(20).to_dict())
    # sanity: a few resolved pairs per top country
    for cc in list(covered["_cc"].value_counts().head(4).index):
        ex = covered[covered["_cc"] == cc].iloc[0]
        print(f"    {cc} e.g. {ex['home_team']!r}->{resolved.get((ex['home_team'], cc))!r} "
              f"vs {ex['away_team']!r}->{resolved.get((ex['away_team'], cc))!r}")

    trainer = ModelTrainer("data_sets/MatchHistory")
    base = trainer.common_features
    sets = {
        "baseline":    list(dict.fromkeys(["B365H", "B365D", "B365A"] + base)),
        "+ clubelo":   list(dict.fromkeys(["B365H", "B365D", "B365A"] + base + CLUBELO_FEATS)),
        "clubelo only": list(dict.fromkeys(
            ["B365H", "B365D", "B365A"] + [f for f in base if f not in OUR_ELO_FEATS] + CLUBELO_FEATS)),
    }
    needed = sorted({f for v in sets.values() for f in v if f != "league_cat"}
                    | {"target_1x2", "total_goals", "H_form_ou", "A_form_ou"})
    cov = covered.dropna(subset=[c for c in needed if c in covered.columns]).copy()
    if "league_cat" in cov.columns and str(cov["league_cat"].dtype) == "category":
        cov["league_cat"] = cov["league_cat"].cat.remove_unused_categories()
    print(f"after full dropna: {len(cov)} rows\n")

    params_1x2 = load_params("models/best_params_1x2.json", default={
        "objective": "multi:softprob", "num_class": 3, "n_estimators": 200,
        "learning_rate": 0.1, "max_depth": 5, "eval_metric": "mlogloss",
        "early_stopping_rounds": 10, "tree_method": "hist", "enable_categorical": True})
    params_ou = load_params("models/best_params_ou.json", default={
        "objective": "count:poisson", "n_estimators": 200, "learning_rate": 0.1,
        "max_depth": 5, "eval_metric": "poisson-nloglik",
        "early_stopping_rounds": 10, "tree_method": "hist", "enable_categorical": True})

    print("-" * 64 + "\n1X2 — TimeSeriesSplit 5-fold CV (Big-5, ClubElo-covered)\n" + "-" * 64)
    res1 = {}
    for name, feats in sets.items():
        r = run_1x2_cv(cov, [f for f in feats], params_1x2)
        res1[name] = r
        print(f"  {name:<13} | Brier {r['brier_mean']:.4f} ± {r['brier_std']:.4f} "
              f"| LogLoss {r['ll_mean']:.4f} | Acc {r['acc_mean']:.4f}")
    b0 = res1["baseline"]["brier_mean"]
    print("\n  Δ Brier vs baseline:")
    for name, r in res1.items():
        if name != "baseline":
            print(f"    {name:<13} | {r['brier_mean']-b0:+.5f}  ({fmt_pct_delta(r['brier_mean'], b0)})")

    print("\n" + "-" * 64 + "\nO/U 2.5 — TimeSeriesSplit 5-fold CV\n" + "-" * 64)
    res_ou = {}
    for name, feats in sets.items():
        full = list(dict.fromkeys(feats + ["H_form_ou", "A_form_ou"]))
        r = run_ou_cv(cov, full, params_ou)
        res_ou[name] = r
        print(f"  {name:<13} | Brier {r['brier_mean']:.4f} ± {r['brier_std']:.4f} | LogLoss {r['ll_mean']:.4f}")
    b0o = res_ou["baseline"]["brier_mean"]
    print("\n  Δ Brier vs baseline:")
    for name, r in res_ou.items():
        if name != "baseline":
            print(f"    {name:<13} | {r['brier_mean']-b0o:+.5f}  ({fmt_pct_delta(r['brier_mean'], b0o)})")

    pl1 = {n: per_league_1x2_brier(cov, r["_oof_idx"], r["_oof_probs"]) for n, r in res1.items()}
    print("\n" + "=" * 64 + "\nPER-LEAGUE — 1X2\n" + "=" * 64)
    print_per_league_table("1X2", pl1, list(sets.keys()))

    out = {"n_covered": int(len(cov)), "n_teams_resolved": len(resolved),
           "covered_by_country": {str(k): int(v) for k, v in covered["_cc"].value_counts().items()},
           "res_1x2": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in res1.items()},
           "res_ou": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in res_ou.items()}}
    outp = CACHE_DIR / "d6_ablation_result.json"
    with open(outp, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nsaved -> {outp}")
    print("\nVERDICT (≥1% Brier improvement = ✅ augment worth it):")
    for mkt, res, b in [("1X2", res1, b0), ("O/U", res_ou, b0o)]:
        for name in ("+ clubelo", "clubelo only"):
            pct = 100 * (res[name]["brier_mean"] - b) / b
            v = "KEEP ✅" if pct <= -1 else ("worse ❌" if pct >= 0 else "marginal —")
            print(f"  {mkt:<4} | {name:<13} | Δ {pct:+.2f}% | {v}")


if __name__ == "__main__":
    main()
