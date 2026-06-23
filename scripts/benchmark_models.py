"""Cross-model value-bet benchmark — does our XGBoost stack actually beat a
simple baseline?

Mirrors the evaluation philosophy of georgedouzas/sports-betting
(ClassifierBettor + backtest): take any sklearn-compatible classifier,
produce out-of-fold 1X2 probabilities via TimeSeriesSplit, then run an
EV-gated flat-stake value-bet backtest against the book's 1X2 odds
(B365H/D/A, with the same Avg/Max fallback the DataLoader applies).

The point is an *independent reality check*: if a plain LogisticRegression
on the same features earns a comparable (or better) value-bet ROI than the
full XGBoost model, that tells us how much our complexity is actually buying.

This is RESEARCH ONLY — it touches no production model, calibration file,
or UI. It reuses ModelTrainer.prepare_data() so the features / odds / targets
are byte-for-byte what training sees. Calibration and the heuristic adjuster
are deliberately NOT applied here: this isolates raw model discriminative /
value-finding power. (Use run_value_lane_backtest.py for the calibrated,
deployed-lane view.)

Each registry entry is a "model spec" exposing the same fit/predict_proba
contract — this doubles as the working prototype for the estimator-seam
refactor (swap models without touching the pipeline).

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/benchmark_models.py
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/benchmark_models.py \
        --splits 5 --edge 0.0 --models logreg,xgboost
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ml_project'))

from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline

from model_registry import REGISTRY, get_spec
from train_model import ModelTrainer

OUT_DIR = os.path.join(ROOT, 'output', 'benchmarks')
ODDS_COLS = ['B365H', 'B365D', 'B365A']  # aligned with target_1x2 mapping H=0,D=1,A=2
ODDS_MIN, ODDS_MAX = 1.10, 15.0


# --------------------------------------------------------------------------- #
# Models = the production families from the shared seam (model_registry) plus
# two benchmark-only baselines that you'd never actually serve:
#   implied — the book's own de-vigged probabilities (the bar to beat;
#             can never find +EV against the odds it came from → 0 bets).
#   prior   — base-rate DummyClassifier.
# The backtest below never references XGBoost directly, only the
# fit / predict_proba contract — that contract IS the seam.
# --------------------------------------------------------------------------- #
def _prior_spec():
    return Pipeline([
        ('impute', SimpleImputer(strategy='median')),
        ('clf', DummyClassifier(strategy='prior')),
    ])


# baseline name -> factory (None = handled specially: implied probs)
BASELINES = {
    'implied': None,
    'prior': _prior_spec,
}


def make_estimator(name):
    """Return a fresh estimator for `name`, or None for the 'implied'
    odds-derived baseline. Production families resolve through the registry
    (1X2 market)."""
    if name in BASELINES:
        factory = BASELINES[name]
        return factory() if factory is not None else None
    return get_spec('1x2', name).build()


MODEL_NAMES = list(BASELINES) + list(REGISTRY['1x2'])


def _implied_probs(odds):
    """De-vigged implied probabilities from decimal 1X2 odds (n, 3)."""
    inv = 1.0 / odds
    return inv / inv.sum(axis=1, keepdims=True)


def value_bet_backtest(probs, odds, y_true, min_edge, stake=1.0):
    """EV-gated flat-stake backtest over the 3 outcomes of each match.

    For each match and each outcome o: edge = p_o * odd_o - 1. If
    edge > min_edge and odd_o in [ODDS_MIN, ODDS_MAX], stake `stake`;
    profit = (odd_o - 1) * stake if the outcome occurred else -stake.
    Returns aggregate stats (stake-weighted ROI / yield).
    """
    n_bets = staked = profit = wins = odds_sum = 0.0
    for i in range(len(y_true)):
        for o in range(3):
            odd = odds[i, o]
            if not (ODDS_MIN <= odd <= ODDS_MAX):
                continue
            if probs[i, o] * odd - 1.0 <= min_edge:
                continue
            n_bets += 1
            staked += stake
            odds_sum += odd
            if y_true[i] == o:
                profit += (odd - 1.0) * stake
                wins += 1
            else:
                profit -= stake
    roi = (profit / staked * 100.0) if staked else 0.0
    return dict(
        n_bets=int(n_bets), staked=staked, profit=profit, roi=roi,
        hit_rate=(wins / n_bets * 100.0) if n_bets else 0.0,
        avg_odds=(odds_sum / n_bets) if n_bets else 0.0,
    )


def run(models, n_splits, min_edge, stake):
    print('Preparing data (reusing production feature pipeline)...')
    trainer = ModelTrainer(os.path.join(ROOT, 'data_sets', 'MatchHistory'))
    df = trainer.prepare_data()

    # Numeric feature set shared by every model for a fair comparison.
    # (Production XGBoost additionally uses league_cat as a category; we drop
    # it here so the linear baseline competes on equal footing.)
    feats = [f for f in (ODDS_COLS + trainer.common_features) if f != 'league_cat']
    df = df.dropna(subset=feats + ['target_1x2'] + ODDS_COLS).sort_values('date')
    df = df[(df[ODDS_COLS] >= 1.01).all(axis=1)]
    X_all = df[feats].astype(float).values
    y_all = df['target_1x2'].astype(int).values
    odds_all = df[ODDS_COLS].astype(float).values
    print(f'Rows: {len(df):,}  features: {len(feats)}  splits: {n_splits}  '
          f'min_edge: {min_edge}')

    tscv = TimeSeriesSplit(n_splits=n_splits)
    results = {}
    for name in models:
        if name not in MODEL_NAMES:
            print(f'  ! unknown model "{name}", skipping')
            continue
        t0 = time.time()
        oof_p = np.full((len(df), 3), np.nan)
        oof_mask = np.zeros(len(df), dtype=bool)
        for tr, te in tscv.split(X_all):
            if name == 'implied':
                oof_p[te] = _implied_probs(odds_all[te])
            else:
                est = make_estimator(name)
                est.fit(X_all[tr], y_all[tr])
                oof_p[te] = est.predict_proba(X_all[te])
            oof_mask[te] = True

        p, y, o = oof_p[oof_mask], y_all[oof_mask], odds_all[oof_mask]
        acc = accuracy_score(y, p.argmax(axis=1))
        ll = log_loss(y, p, labels=[0, 1, 2])
        bt = value_bet_backtest(p, o, y, min_edge, stake)
        bt.update(model=name, accuracy=acc * 100, log_loss=ll,
                  secs=time.time() - t0)
        results[name] = bt
        print(f'  {name:9s} acc={acc*100:5.2f}%  logloss={ll:.4f}  '
              f'bets={bt["n_bets"]:6d}  ROI={bt["roi"]:+6.2f}%  '
              f'hit={bt["hit_rate"]:5.2f}%  ({bt["secs"]:.0f}s)')

    return results, dict(rows=len(df), features=feats, n_splits=n_splits,
                         min_edge=min_edge, stake=stake)


def _report(results, meta):
    lines = []
    lines.append('CROSS-MODEL VALUE-BET BENCHMARK')
    lines.append(f'generated: {datetime.now().isoformat(timespec="seconds")}')
    lines.append(f'rows: {meta["rows"]:,}  splits: {meta["n_splits"]}  '
                 f'min_edge: {meta["min_edge"]}  stake: {meta["stake"]}')
    lines.append('value bet = EV-gated (p*odds-1 > min_edge), flat stake, '
                 f'odds in [{ODDS_MIN}, {ODDS_MAX}]')
    lines.append('')
    hdr = (f'{"model":10s} {"acc%":>6s} {"logloss":>8s} {"bets":>7s} '
           f'{"ROI%":>7s} {"hit%":>6s} {"avgOdds":>8s}')
    lines.append(hdr)
    lines.append('-' * len(hdr))
    for r in sorted(results.values(), key=lambda x: -x['roi']):
        lines.append(f'{r["model"]:10s} {r["accuracy"]:6.2f} {r["log_loss"]:8.4f} '
                     f'{r["n_bets"]:7d} {r["roi"]:+7.2f} {r["hit_rate"]:6.2f} '
                     f'{r["avg_odds"]:8.2f}')
    lines.append('')
    lines.append('Read: a positive ROI for "implied" means the EV gate itself '
                 'leaks (look-ahead-free vig should make it negative); compare '
                 'every learned model against logreg, not against zero.')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--models', default='implied,prior,logreg,xgboost',
                    help='comma-separated subset of: ' + ','.join(MODEL_NAMES))
    ap.add_argument('--splits', type=int, default=5)
    ap.add_argument('--edge', type=float, default=0.0,
                    help='minimum EV edge to place a bet (0.0 = any +EV)')
    ap.add_argument('--stake', type=float, default=1.0)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(',') if m.strip()]
    results, meta = run(models, args.splits, args.edge, args.stake)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    with open(os.path.join(OUT_DIR, f'{stamp}.json'), 'w') as f:
        json.dump({'meta': meta, 'results': results}, f, indent=2, default=str)
    report = _report(results, meta)
    with open(os.path.join(OUT_DIR, f'{stamp}.txt'), 'w') as f:
        f.write(report + '\n')
    print('\n' + report)
    print(f'\nWrote output/benchmarks/{stamp}.{{json,txt}}')


if __name__ == '__main__':
    main()
