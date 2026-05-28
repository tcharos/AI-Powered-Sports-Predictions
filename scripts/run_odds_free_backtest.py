"""Model-vs-market value backtest — the experiment that survived the
D3 analysis (FOOTBALL_NEXT_STEPS.md D3 section).

Hypothesis: an odds-INDEPENDENT 1X2 estimate can find value the line
misses. Tests it cheaply by reusing the existing pipeline rather than
building Dixon-Coles.

Method (no leakage, all out-of-fold via TimeSeriesSplit):
  1. Same training frame + hyperparams as production.
  2. Two OOF 1X2 models on the SAME rows:
       - WITH-odds  (current feature set, incl B365H/A + IP_H/D/A)
       - ODDS-FREE  (those 5 features removed)
  3. Flat-stake EV backtest: for each match × outcome, EV = p·odds − 1
     using the B365 decimal odds; bet 1 unit where EV ≥ threshold;
     settle on the actual result at those odds.
  4. Compare ROI of the two models across EV thresholds. The with-odds
     model is anchored to the line → should disagree little (EV≈−margin,
     ~0 edge); if the odds-free model's disagreements are PROFITABLE,
     the model-vs-market edge is real. If they're negative/noise, it
     isn't — and we've spent an afternoon, not a month on DC.

Isolated + read-only w.r.t. production: no model artifact is written
(OOF is the evaluation), output goes to output/odds_free/ only,
nothing here is imported by the prediction/betting/UI flows.

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/run_odds_free_backtest.py
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
ODDS_FEATS = ('B365H', 'B365D', 'B365A', 'IP_H', 'IP_D', 'IP_A')
EV_THRESHOLDS = (0.0, 0.02, 0.05, 0.10, 0.20)
# Ignore extreme longshots — thin, high-variance, and the data's odds
# for them are least reliable.
ODDS_MIN, ODDS_MAX = 1.10, 15.0


def _backtest(probs, df, label):
    """Flat-stake EV backtest over (match × outcome). probs aligned to
    df rows (sorted same order). df has B365H/D/A + target_1x2."""
    odds_cols = df[['B365H', 'B365D', 'B365A']].values.astype(float)
    y = df['target_1x2'].values.astype(int)  # 0=H,1=D,2=A
    out = {}
    for thr in EV_THRESHOLDS:
        n = wins = 0
        pnl = 0.0
        staked = 0.0
        for i in range(len(df)):
            p = probs[i]
            if p is None or np.any(np.isnan(p)):
                continue
            for k in range(3):
                o = odds_cols[i, k]
                if not (ODDS_MIN <= o <= ODDS_MAX):
                    continue
                ev = p[k] * o - 1.0
                if ev >= thr:
                    n += 1
                    staked += 1.0
                    if y[i] == k:
                        wins += 1
                        pnl += (o - 1.0)
                    else:
                        pnl -= 1.0
        roi = (pnl / staked) if staked > 0 else 0.0
        wr = (wins / n) if n > 0 else 0.0
        out[f"{thr:.2f}"] = {
            'bets': n, 'win_rate': round(wr, 4),
            'pnl': round(pnl, 2), 'roi': round(roi, 4),
        }
        print(f"  [{label:<10} EV>={thr:.2f}]  bets={n:>6}  win%={wr*100:>5.1f}  "
              f"pnl={pnl:>+9.1f}  ROI={roi*100:>+6.2f}%")
    return out


def main():
    trainer = ModelTrainer(data_dir=os.path.join(ROOT, 'data_sets', 'MatchHistory'))
    df = trainer.prepare_data()
    df = df.dropna(subset=['FTR']).copy()
    df['target_1x2'] = df['FTR'].map({'H': 0, 'D': 1, 'A': 2})
    df = df.dropna(subset=['target_1x2'])
    df['target_1x2'] = df['target_1x2'].astype(int)

    full = ['B365H', 'B365A'] + trainer.common_features
    base = [f for f in full if f not in ODDS_FEATS]
    # Common row set so both models + settlement align exactly: need all
    # `full` features (superset of base) AND B365D for draw settlement.
    need = list(dict.fromkeys(full + ['B365D']))
    d = df.dropna(subset=need + ['target_1x2']).copy().sort_values('date').reset_index(drop=True)
    print(f"rows: {len(d)} | full={len(full)} feats | odds-free={len(base)} feats")

    params = json.load(open(os.path.join(ROOT, 'models', 'best_params_1x2.json')))
    params.setdefault('enable_categorical', True)

    print("\nOOF predictions (WITH odds)...")
    oof_full, _ = out_of_fold_predictions(d, full, 'target_1x2', params, n_splits=5)
    print("OOF predictions (ODDS-FREE)...")
    oof_base, _ = out_of_fold_predictions(d, base, 'target_1x2', params, n_splits=5)

    print("\n=== Model-vs-market EV backtest (flat 1u, B365 odds, OOF) ===")
    res_full = _backtest(list(oof_full), d, 'with-odds')
    print()
    res_base = _backtest(list(oof_base), d, 'odds-free')

    os.makedirs(OUT_DIR, exist_ok=True)
    report = {
        'rows': len(d),
        'odds_range': [ODDS_MIN, ODDS_MAX],
        'ev_thresholds': list(EV_THRESHOLDS),
        'with_odds': res_full,
        'odds_free': res_base,
        'note': ('ROI is flat-stake on B365 decimal odds, OOF (no leakage). '
                 'With-odds model is anchored to the line (expect ~0 edge / '
                 '-margin); odds-free is the independent estimate. Positive '
                 'odds-free ROI at sane bet volume = real model-vs-market edge.'),
    }
    path = os.path.join(OUT_DIR, 'backtest.json')
    with open(path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\nReport → {path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
