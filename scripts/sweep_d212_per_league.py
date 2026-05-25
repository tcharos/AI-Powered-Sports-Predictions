"""Per-league ablation for D2.1.2 (recency-weighted form).

Question: the global CV Brier showed no improvement from the 8 new `_w`
features, but the doc said the lift should be biggest in leagues where
form fluctuates fast. Does any league materially benefit?

Method: reuse the calibration/diagnose machinery to produce OOF
predictions on two feature sets — the full set (with `_w` columns) and a
"base" set (with the 8 `_w` columns removed). Group by league, compute
mean multi-class Brier on the OOF probs for each set, report the delta.

Only the 1X2 multi-class market is run (the most-discussed model and the
one D2.1.2 was meant to help). O/U could be added later if 1X2 shows
gains worth chasing.

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/sweep_d212_per_league.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

# Make ml_project imports resolve.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ml_project'))

from train_model import ModelTrainer
from calibration.diagnose import out_of_fold_predictions, brier_multiclass


def main():
    print("[1/4] Preparing data via ModelTrainer (~30s)...")
    trainer = ModelTrainer(data_dir=os.path.join(ROOT, 'data_sets', 'MatchHistory'))
    df = trainer.prepare_data()
    print(f"  rows: {len(df)}")

    # Target column for 1X2: trainer encodes FTR → numeric class. Mirror
    # the same encoding it uses internally (H=0, D=1, A=2 per FTR).
    ftr_map = {'H': 0, 'D': 1, 'A': 2}
    df = df.dropna(subset=['FTR']).copy()
    df['target_1x2'] = df['FTR'].map(ftr_map)
    df = df.dropna(subset=['target_1x2']).copy()
    df['target_1x2'] = df['target_1x2'].astype(int)
    print(f"  rows with valid target: {len(df)}")

    # 1X2 feature lists. The full set is what the post-D2.1.2 model
    # trained on; the base set drops the 8 `_w` columns.
    full_features = ['B365H', 'B365A'] + trainer.common_features
    weighted = [f for f in full_features if f.endswith('_w')]
    base_features = [f for f in full_features if not f.endswith('_w')]
    print(f"  full features:    {len(full_features)} (incl. {len(weighted)} _w)")
    print(f"  base features:    {len(base_features)}")

    # Load the tuned hyperparams the production model uses.
    with open(os.path.join(ROOT, 'models', 'best_params_1x2.json')) as f:
        params = json.load(f)
    params.setdefault('enable_categorical', True)
    print(f"  params (1x2): n_estimators={params.get('n_estimators')}")

    # OOF predictions for each feature set. Each call trains 5 models;
    # ~3–5 min on this CPU. Returns (oof_array, kept_indices) — the
    # function does its own dropna + sort_values internally and returns
    # the post-filter row positions so callers can align.
    print(f"\n[2/4] OOF predictions on FULL feature set...")
    df_full = df.dropna(subset=full_features + ['target_1x2']).copy()
    oof_full, idx_full = out_of_fold_predictions(
        df_full, full_features, 'target_1x2', params, n_splits=5)

    print(f"\n[3/4] OOF predictions on BASE feature set (no _w columns)...")
    df_base = df.dropna(subset=base_features + ['target_1x2']).copy()
    oof_base, idx_base = out_of_fold_predictions(
        df_base, base_features, 'target_1x2', params, n_splits=5)

    # The OOF arrays are aligned to the diagnose function's internal
    # sorted-by-date frame. Sort + reset_index in the same order so the
    # `p_oof` column lines up row-for-row.
    print(f"\n[4/4] Aligning + per-league Brier...")
    df_full = df_full.sort_values('date').reset_index(drop=True)
    df_base = df_base.sort_values('date').reset_index(drop=True)
    df_full['p_oof'] = [row for row in oof_full]
    df_base['p_oof'] = [row for row in oof_base]

    key_cols = ['date', 'home_team', 'away_team']
    full_keyed = df_full[key_cols + ['p_oof', 'target_1x2', 'league']].copy()
    base_keyed = df_base[key_cols + ['p_oof']].rename(columns={'p_oof': 'p_oof_base'})
    merged = full_keyed.merge(base_keyed, on=key_cols, how='inner')
    print(f"  matched rows for comparison: {len(merged)}")

    # Brier contribution per match for each variant.
    def _row_brier(probs, target):
        if probs is None or np.any(np.isnan(probs)):
            return np.nan
        target_oh = np.zeros(len(probs))
        target_oh[int(target)] = 1.0
        return float(np.sum((probs - target_oh) ** 2))

    merged['brier_full'] = merged.apply(
        lambda r: _row_brier(r['p_oof'], r['target_1x2']), axis=1)
    merged['brier_base'] = merged.apply(
        lambda r: _row_brier(r['p_oof_base'], r['target_1x2']), axis=1)
    merged = merged.dropna(subset=['brier_full', 'brier_base']).copy()

    print(f"  rows with both OOF predictions valid: {len(merged)}")

    # Per-league aggregation.
    agg = (
        merged.groupby('league')
              .agg(n=('brier_full', 'size'),
                   brier_full=('brier_full', 'mean'),
                   brier_base=('brier_base', 'mean'))
              .reset_index()
    )
    agg['delta'] = agg['brier_full'] - agg['brier_base']  # negative = D2.1.2 helps
    agg['delta_pct'] = agg['delta'] / agg['brier_base'] * 100
    agg = agg.sort_values('delta')

    # Filter to leagues with enough samples to trust the per-league mean.
    MIN_N = 100
    significant = agg[agg['n'] >= MIN_N].copy()
    print(f"\n=== Per-league 1X2 Brier delta (full WITH _w  -  base WITHOUT _w) ===")
    print(f"  Negative delta = weighted features HELP that league.")
    print(f"  Filtered to leagues with n >= {MIN_N}; {len(significant)} qualify.\n")
    print(f"{'league':<28} {'n':>5} {'brier_base':>10} {'brier_full':>10} "
          f"{'delta':>9} {'delta_pct':>9}")
    print("-" * 75)
    for _, r in significant.iterrows():
        sign = '✅' if r['delta'] < -0.001 else ('❌' if r['delta'] > 0.001 else '·')
        print(f"{r['league']:<28} {int(r['n']):>5} "
              f"{r['brier_base']:>10.4f} {r['brier_full']:>10.4f} "
              f"{r['delta']:>+9.4f} {r['delta_pct']:>+8.2f}% {sign}")

    # Global summary.
    total_n = significant['n'].sum()
    weighted_full = (significant['brier_full'] * significant['n']).sum() / total_n
    weighted_base = (significant['brier_base'] * significant['n']).sum() / total_n
    print("-" * 75)
    print(f"{'WEIGHTED MEAN (n)':<28} {int(total_n):>5} "
          f"{weighted_base:>10.4f} {weighted_full:>10.4f} "
          f"{weighted_full - weighted_base:>+9.4f} "
          f"{(weighted_full - weighted_base) / weighted_base * 100:>+8.2f}%")

    helps = significant[significant['delta'] < -0.001]
    hurts = significant[significant['delta'] > 0.001]
    print(f"\nLeagues helped (delta < -0.001): {len(helps)}")
    print(f"Leagues hurt   (delta > +0.001): {len(hurts)}")

    # Save for the record.
    out_path = os.path.join(ROOT, 'output', 'calibration',
                            'd212_per_league_brier.csv')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    agg.to_csv(out_path, index=False)
    print(f"\nSaved → {out_path}")


if __name__ == '__main__':
    main()
