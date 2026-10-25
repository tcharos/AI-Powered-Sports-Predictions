"""Large-sample replication of the IP_*=0 serve-time bug.

predict_matches never builds IP_H/IP_D/IP_A (1/odds), which sit in both the
1X2 and O/U feature lists; its zero-fill loop hands the model 0 for all
three on every prediction. experiment_live_gap.py measured the cost on ~1k
settled live matches. This replicates it out-of-sample on the corpus:
train the deployed recipe (main leagues, 2024-07 .. 2025-06), then score
every main-league match from 2025-07 on — once with IP_* as training builds
them, once with IP_* = 0 as serving does — and run the value-lane EV gate
(EV = p*odds - 1 > 0, p >= 0.45) on both.

Usage: python3 scripts/experiment_ip_serve_bug.py
Writes output/experiments/ip_serve_bug_<ts>.{json,txt}
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import EXP_DIR, PROJECT_ROOT, boot_ci, ll2, ll3, rps3, write_report  # noqa: E402

sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'ml_project'))
os.chdir(PROJECT_ROOT)
from model_registry import get_spec  # noqa: E402
from train_model import ModelTrainer  # noqa: E402
from experiment_odds_free import add_ou_market  # noqa: E402
from experiment_live_gap import slope  # noqa: E402
from experiment_training_coverage import MAIN  # noqa: E402

SPLIT = '2025-07-01'


def main():
    df = pd.read_pickle(os.path.join(EXP_DIR, '_prepared_fixed.pkl')).sort_values('date')
    df = df[df['league'].astype(str).isin(MAIN)].copy()
    df['league_cat'] = df['league'].astype(str).astype('category')
    ip = np.column_stack([1 / df.B365H, 1 / df.B365D, 1 / df.B365A])
    df[['mkt_H', 'mkt_D', 'mkt_A']] = ip / ip.sum(1, keepdims=True)
    df = add_ou_market(df)
    tr = ModelTrainer('data_sets/MatchHistory')
    L = []
    P = L.append
    payload = {}
    P(f'IP_* SERVE BUG — out-of-sample replication (train 2024-07..{SPLIT}, test {SPLIT}..)')
    for head in ['1x2', 'ou']:
        base = ['B365H', 'B365D', 'B365A'] + (['H_form_ou', 'A_form_ou'] if head == 'ou' else [])
        feats = list(dict.fromkeys(base + tr.common_features))
        target = 'target_1x2' if head == '1x2' else 'total_goals'
        d = df.dropna(subset=feats + [target, 'mkt_H'])
        train = d[(d.date >= '2024-07-01') & (d.date < SPLIT)]
        test = d[d.date >= SPLIT]
        m = get_spec(head, 'xgboost').build()
        m.fit(train[feats], train[target])
        X0 = test[feats].copy()
        X0[['IP_H', 'IP_D', 'IP_A']] = 0.0
        P('')
        if head == '1x2':
            y = test[target].astype(int).to_numpy()
            pc, pb = m.predict_proba(test[feats]), m.predict_proba(X0)
            mk = test[['mkt_H', 'mkt_D', 'mkt_A']].to_numpy()
            P(f'== 1X2 == train {len(train)}  test {len(test)}')
            for nm, p in [('IP correct', pc), ('IP = 0 (served)', pb), ('market', mk)]:
                dm = ll3(p, y) - ll3(mk, y)
                P(f'   {nm:<16} ll {ll3(p, y).mean():.4f}  rps {rps3(p, y).mean():.4f}  acc {np.mean(p.argmax(1) == y):.3f}'
                  f'  Δmkt {dm.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(dm))}'
                  f'  home-win slope {slope(p[:, 0], (y == 0).astype(int)):.3f}')
                payload[f'1x2|{nm}'] = {'ll': float(ll3(p, y).mean()), 'd_mkt': float(dm.mean())}
            dd = ll3(pb, y) - ll3(pc, y)
            P(f'   bug cost: {dd.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(dd))}   '
              f'picks changed {np.mean(pb.argmax(1) != pc.argmax(1)):.1%}')
            odds = test[['B365H', 'B365D', 'B365A']].to_numpy()
            P('   value-lane gate on the H/A pick (EV>0, p>=0.45), flat 1u at B365:')
            for nm, p in [('IP correct', pc), ('IP = 0 (served)', pb)]:
                pick = np.where(p[:, 0] >= p[:, 2], 0, 2)
                conf = p[np.arange(len(p)), pick]
                o = odds[np.arange(len(p)), pick]
                ev = conf * o - 1
                sel = (ev > 0) & (conf >= 0.45)
                r = np.where(y[sel] == pick[sel], o[sel] - 1, -1.0)
                rho = pd.Series(ev).corr(pd.Series(np.where(y == pick, o - 1, -1.0)), method='spearman')
                lo, hi = boot_ci(r)
                P(f'     {nm:<16} bets {sel.sum():5d} ({sel.mean():.1%})  mean odds {o[sel].mean():.2f}  '
                  f'claimed EV {ev[sel].mean():+.3f}  realised ROI {r.mean():+.1%} [{lo:+.1%},{hi:+.1%}]  '
                  f'ρ(EV,return) all picks {rho:+.3f}')
                payload[f'value|{nm}'] = {'n': int(sel.sum()), 'roi': float(r.mean()), 'ci': [lo, hi], 'rho': float(rho)}
        else:
            ok = test['mkt_over'].notna().to_numpy()
            yo = (test[target].to_numpy() > 2.5).astype(int)

            def po(X):
                lam = m.predict(X)
                return 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
            pc, pb = po(test[feats]), po(X0)
            P(f'== O/U == train {len(train)}  test {len(test)} ({ok.sum()} with O/U odds)')
            for nm, p in [('IP correct', pc), ('IP = 0 (served)', pb), ('market', test['mkt_over'].to_numpy())]:
                dm = ll2(p[ok], yo[ok]) - ll2(test['mkt_over'].to_numpy()[ok], yo[ok])
                P(f'   {nm:<16} ll {ll2(p[ok], yo[ok]).mean():.4f}  Δmkt {dm.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(dm))}'
                  f'  mean P(over) {np.nanmean(p[ok]):.3f} (actual {yo[ok].mean():.3f})')
            dd = ll2(pb[ok], yo[ok]) - ll2(pc[ok], yo[ok])
            P(f'   bug cost: {dd.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(dd))}   '
              f'picks changed {np.mean((pb > .5) != (pc > .5)):.1%}')
            payload['ou_bug_cost'] = [float(dd.mean()), *boot_ci(dd)]
    text = '\n'.join(L) + '\n'
    print(text)
    base = write_report('ip_serve_bug', payload, text)
    print(f'wrote {base}.{{json,txt}}')


if __name__ == '__main__':
    main()
