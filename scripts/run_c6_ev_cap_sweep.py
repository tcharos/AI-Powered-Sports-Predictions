"""C6 — ev_cap_value sweep on calibrated probs.

FOOTBALL_NEXT_STEPS C6: "re-run the backtest harness with
ev_cap_value ∈ {0.05, 0.08, 0.15, ∞} on calibrated probs and pick the one
that maximises Δ vs baseline."

Reuses the value-lane replica from run_value_lane_backtest.py. Computes the
(expensive) 5-fold OOF predictions ONCE, calibrates per-league, then loops the
value-lane sizing over each ev_cap. ev_cap only affects STAKE SIZING via
stake = min(ev, ev_cap)*conf, so stake-weighted ROI is the signal; flat ROI is
ev_cap-invariant and shown once as the reference.

Run:
  PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/run_c6_ev_cap_sweep.py
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'ml_project'))

from train_model import ModelTrainer
from calibration.diagnose import out_of_fold_predictions
from run_value_lane_backtest import _value_lane, _calibrate_1x2, OUT_DIR

EV_CAPS = [0.05, 0.08, 0.15, float('inf')]


def main():
    cfg = json.load(open(os.path.join(ROOT, 'data_sets', 'betting_config.json')))
    fb = cfg['sports']['football']
    min_conf = fb.get('min_confidence', 0.45)
    deployed_cap = fb.get('ev_cap_value', 0.05)
    leagues_cal = json.load(open(os.path.join(
        ROOT, 'data_sets', 'league_calibration.json'))).get('leagues', {})

    trainer = ModelTrainer(data_dir=os.path.join(ROOT, 'data_sets', 'MatchHistory'))
    df = trainer.prepare_data()
    df = df.dropna(subset=['FTR']).copy()
    df['target_1x2'] = df['FTR'].map({'H': 0, 'D': 1, 'A': 2})
    df = df.dropna(subset=['target_1x2'])
    df['target_1x2'] = df['target_1x2'].astype(int)

    full = ['B365H', 'B365A'] + trainer.common_features
    need = list(dict.fromkeys(full + ['B365D', 'league']))
    d = df.dropna(subset=need + ['target_1x2']).copy().sort_values('date').reset_index(drop=True)
    print(f"rows: {len(d)} | min_confidence={min_conf} | deployed ev_cap_value={deployed_cap}")

    params = json.load(open(os.path.join(ROOT, 'models', 'best_params_1x2.json')))
    params.setdefault('enable_categorical', True)
    print("OOF predictions (production with-odds model)...")
    oof, _ = out_of_fold_predictions(d, full, 'target_1x2', params, n_splits=5)

    odds = d[['B365H', 'B365D', 'B365A']].values.astype(float)
    y = d['target_1x2'].values.astype(int)
    leagues = d['league'].values

    raw = list(oof)
    cal = []
    n_cal = 0
    for i in range(len(d)):
        p = oof[i]
        if p is None or np.any(np.isnan(p)):
            cal.append(p); continue
        c = _calibrate_1x2(p, leagues[i], leagues_cal)
        if not np.allclose(c, p):
            n_cal += 1
        cal.append(c)
    print(f"calibrated rows: {n_cal}/{len(d)}\n")

    print("=== C6: value-lane ROI by ev_cap (CALIBRATED probs — production path) ===")
    sweep = {}
    for cap in EV_CAPS:
        label = '∞' if cap == float('inf') else f"{cap:.2f}"
        res = _value_lane(cal, odds, y, min_conf, cap, f"cap={label}")
        sweep[label] = res

    print("\n=== reference: same sweep on RAW (uncalibrated) probs ===")
    sweep_raw = {}
    for cap in EV_CAPS:
        label = '∞' if cap == float('inf') else f"{cap:.2f}"
        sweep_raw[label] = _value_lane(raw, odds, y, min_conf, cap, f"cap={label}")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, 'c6_ev_cap_sweep.json')
    with open(path, 'w') as f:
        json.dump({
            'rows': len(d), 'calibrated_rows': n_cal,
            'min_confidence': min_conf, 'deployed_ev_cap_value': deployed_cap,
            'caps_tested': [('inf' if c == float('inf') else c) for c in EV_CAPS],
            'calibrated': sweep, 'raw': sweep_raw,
            'caveats': ('OOF (no leakage); calibration mildly optimistic; 1X2 only; '
                        'heuristic adjuster NOT replicated. ev_cap affects stake-weighted '
                        'ROI only (flat ROI is cap-invariant).'),
        }, f, indent=2)
    print(f"\nReport → {path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
