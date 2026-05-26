"""Confirm (or refute) the EV-anti-predictivity finding against the
DEPLOYED value-lane configuration — calibrated probs + the lane's
actual gate/sizing. Decides whether the value lane needs surgery.

Two analyses, each raw-probs vs Platt-calibrated-probs, on OOF 1X2
predictions from the production (with-odds) feature set, 5-fold
TimeSeriesSplit (no leakage on the model; calibration is mildly
optimistic — the league_calibration.json was fit on OOF preds of this
same data — flagged in the report):

  A) Flat EV-threshold sweep. Does calibration flatten the "higher EV
     → worse ROI" gradient the raw backtest showed?
  B) Value-lane replica. Per match, bet the model's 1X2 PICK (argmax)
     iff EV>0 AND conf>=min_confidence, size ∝ min(EV, ev_cap)·conf
     (Option B). Stake-weighted ROI = the deployed lane's signature.
     If still negative after calibration → the lane logic is the
     problem; if calibration fixes it → raw finding was an artifact.

Isolated: reads training data + calibration JSON, writes to
output/odds_free/ only. No production code / model / UI touched.

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/run_value_lane_backtest.py
"""

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ml_project'))

from train_model import ModelTrainer
from calibration.diagnose import out_of_fold_predictions

OUT_DIR = os.path.join(ROOT, 'output', 'odds_free')
EV_THRESHOLDS = (0.0, 0.02, 0.05, 0.10, 0.20)
ODDS_MIN, ODDS_MAX = 1.10, 15.0


def _safe_logit(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _calibrate_1x2(raw3, league, leagues_cal):
    """Direct per-league Platt lookup by league code (the key the
    calibrators were fit on). Returns calibrated [H,D,A] or the raw
    vector unchanged if no calibrator for this league."""
    entry = (leagues_cal.get(league) or {}).get('oneXtwo')
    if not entry:
        return raw3
    platt = entry['platt']
    cal = np.zeros(3)
    for i, c in enumerate(('home', 'draw', 'away')):
        cal[i] = _sigmoid(platt[c]['a'] * _safe_logit(raw3[i]) + platt[c]['b'])
    s = cal.sum()
    return cal / s if s > 0 else raw3


def _flat_sweep(probs, odds, y, label):
    print(f"\n  Flat EV-threshold sweep [{label}]:")
    res = {}
    for thr in EV_THRESHOLDS:
        n = wins = 0
        pnl = 0.0
        for i in range(len(y)):
            p = probs[i]
            if p is None or np.any(np.isnan(p)):
                continue
            for k in range(3):
                o = odds[i, k]
                if not (ODDS_MIN <= o <= ODDS_MAX):
                    continue
                if p[k] * o - 1.0 >= thr:
                    n += 1
                    if y[i] == k:
                        wins += 1; pnl += o - 1.0
                    else:
                        pnl -= 1.0
        roi = pnl / n if n else 0.0
        res[f"{thr:.2f}"] = {'bets': n, 'roi': round(roi, 4)}
        print(f"    EV>={thr:.2f}  bets={n:>6}  win%={(wins/n*100 if n else 0):>5.1f}  ROI={roi*100:>+6.2f}%")
    return res


def _value_lane(probs, odds, y, min_conf, ev_cap, label):
    """Replicate the value lane: bet the argmax 1X2 pick iff EV>0 and
    conf>=min_conf; size ∝ min(EV,ev_cap)·conf. Report stake-weighted
    ROI (the lane's real signature) + flat ROI for reference."""
    n = wins = 0
    staked = 0.0
    pnl_w = 0.0   # stake-weighted pnl
    pnl_flat = 0.0
    for i in range(len(y)):
        p = probs[i]
        if p is None or np.any(np.isnan(p)):
            continue
        k = int(np.argmax(p))            # the model's pick
        o = odds[i, k]
        if not (ODDS_MIN <= o <= ODDS_MAX):
            continue
        conf = float(p[k])
        ev = conf * o - 1.0
        if ev <= 0 or conf < min_conf:   # value-lane gate
            continue
        stake = min(ev, ev_cap) * conf   # Option-B sizing (constants cancel in ROI)
        n += 1
        staked += stake
        if y[i] == k:
            wins += 1
            pnl_w += stake * (o - 1.0)
            pnl_flat += (o - 1.0)
        else:
            pnl_w -= stake
            pnl_flat -= 1.0
    roi_w = pnl_w / staked if staked > 0 else 0.0
    roi_flat = pnl_flat / n if n else 0.0
    print(f"  [value-lane {label:<10}] bets={n:>5}  win%={(wins/n*100 if n else 0):>5.1f}  "
          f"stake-wtd ROI={roi_w*100:>+6.2f}%  flat ROI={roi_flat*100:>+6.2f}%")
    return {'bets': n, 'win_rate': round(wins / n, 4) if n else 0,
            'roi_stake_weighted': round(roi_w, 4), 'roi_flat': round(roi_flat, 4)}


def main():
    cfg = json.load(open(os.path.join(ROOT, 'data_sets', 'betting_config.json')))
    fb = cfg['sports']['football']
    min_conf = fb.get('min_confidence', 0.45)
    ev_cap = fb.get('ev_cap_value', 0.05)
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
    print(f"rows: {len(d)} | min_confidence={min_conf} ev_cap_value={ev_cap}")

    params = json.load(open(os.path.join(ROOT, 'models', 'best_params_1x2.json')))
    params.setdefault('enable_categorical', True)
    print("OOF predictions (production with-odds model)...")
    oof, _ = out_of_fold_predictions(d, full, 'target_1x2', params, n_splits=5)

    odds = d[['B365H', 'B365D', 'B365A']].values.astype(float)
    y = d['target_1x2'].values.astype(int)
    leagues = d['league'].values

    raw = list(oof)
    # Per-row calibrated probs.
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
    print(f"calibrated rows: {n_cal}/{len(d)} (rest had no per-league calibrator → raw)")

    print("\n=== A) Flat EV-threshold sweep: does calibration flatten the gradient? ===")
    sweep_raw = _flat_sweep(raw, odds, y, 'RAW')
    sweep_cal = _flat_sweep(cal, odds, y, 'CALIBRATED')

    print("\n=== B) Value-lane replica (argmax pick, EV>0 & conf>=min_conf, Option-B sizing) ===")
    vl_raw = _value_lane(raw, odds, y, min_conf, ev_cap, 'RAW')
    vl_cal = _value_lane(cal, odds, y, min_conf, ev_cap, 'CALIBRATED')

    os.makedirs(OUT_DIR, exist_ok=True)
    report = {
        'rows': len(d), 'calibrated_rows': n_cal,
        'min_confidence': min_conf, 'ev_cap_value': ev_cap,
        'flat_sweep': {'raw': sweep_raw, 'calibrated': sweep_cal},
        'value_lane': {'raw': vl_raw, 'calibrated': vl_cal},
        'caveats': ('OOF model = no leakage; calibration mildly optimistic '
                    '(fit on OOF preds of same data). 1X2 only. Heuristic '
                    'adjuster (post-Platt) NOT replicated. Stake-weighted ROI '
                    'is the value-lane signature.'),
    }
    path = os.path.join(OUT_DIR, 'value_lane_backtest.json')
    with open(path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nReport → {path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
