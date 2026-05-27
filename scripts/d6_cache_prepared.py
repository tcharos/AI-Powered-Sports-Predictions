"""D6 helper — run the slow ModelTrainer.prepare_data() ONCE and cache it.

prepare_data() runs full ELO + feature engineering over the corpus (~10-15 min,
memory-heavy). Caching it to a pickle lets the ClubElo ablation iterate fast
without re-running. Also prints the schema + unique league labels so the
ablation's Big-5 filter is grounded in the real values.

Usage: PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/d6_cache_prepared.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml_project"))

from train_model import ModelTrainer  # noqa: E402

OUT = ROOT / "output" / "d6_cache"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = OUT / "prepared.pkl"


def main():
    trainer = ModelTrainer("data_sets/MatchHistory")
    print("Running prepare_data() (slow)...", flush=True)
    df = trainer.prepare_data()
    df.to_pickle(CACHE)
    print(f"\nCached {len(df)} rows -> {CACHE}", flush=True)
    print(f"columns ({len(df.columns)}): {list(df.columns)}", flush=True)
    # league labels + key columns for the ablation to key off
    for col in ("league", "date", "home_team", "away_team", "H_elo", "A_elo",
                "target_1x2", "total_goals"):
        if col in df.columns:
            print(f"  has '{col}'", flush=True)
    if "league" in df.columns:
        print("\nunique leagues:", flush=True)
        for lg, n in df["league"].value_counts().items():
            print(f"  {lg!r}: {n}", flush=True)


if __name__ == "__main__":
    main()
