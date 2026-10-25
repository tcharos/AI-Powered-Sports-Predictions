"""Which rows does the deployed model actually learn from — and does it matter?

Two silent row filters shrink the deployed 1X2/O/U training set to ~15.7k
rows of 2024/25 + 2025/26 main-league football:

 (1) Every main-league season file before 2024/25 carries no `Div` column
     (written pre-renamed), so DataLoader gives its rows league=NaN ->
     league_cat=NaN -> ModelTrainer._training_rows drops them. prepare_data()
     says "2020-present"; in practice the model starts in 2024.
 (2) Shots/corners are absent outside the 21 main divisions, so the same
     dropna removes every MLS / J1 / Brazil / Nordic / ... row. The model is
     served on those leagues anyway.

Uses output/experiments/_prepared_fixed.pkl (audit_prepare_frames.py --fixed:
league codes restored, 2012+, current season included). Walk-forward:
train on everything before each test window, score the window.

Arms (1X2 and O/U heads, same features as production unless stated):
  A_deployed  main leagues, 2024-07+ only, all features present — what
              production trains on today
  D_2020      main leagues, 2020+                — what prepare_data intends
  D_2012      main leagues, 2012+
  B_all       D_2012 + every other league, shots NaN kept (XGBoost
              learns the missing direction)
  C_noshots   all leagues, the 12 shots/corner columns dropped

Serve emulation for test rows in non-main leagues on A/D arms: venue shots
filled 0 as predict_matches does. IP_* are built correctly in every arm (the
serve-time IP_*=0 bug is measured separately in experiment_live_gap.py).

Usage: python3 scripts/experiment_training_coverage.py
Writes output/experiments/training_coverage_<ts>.{json,txt}
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

MAIN = {'B1', 'D1', 'D2', 'E0', 'E1', 'E2', 'E3', 'F1', 'F2', 'G1', 'I1', 'I2',
        'N1', 'P1', 'SC0', 'SC1', 'SC2', 'SC3', 'SP1', 'SP2', 'T1'}   # EC has no shots either
SHOTS = ['H_home_sf', 'H_home_sa', 'A_away_sf', 'A_away_sa',
         'H_form_sf', 'H_form_sa', 'H_form_cf', 'H_form_ca',
         'A_form_sf', 'A_form_sa', 'A_form_cf', 'A_form_ca']
VENUE_SHOTS = ['H_home_sf', 'H_home_sa', 'A_away_sf', 'A_away_sa']
WINDOWS = [('2025H2', '2025-07-01', '2026-01-01'),
           ('2026H1', '2026-01-01', '2026-07-01'),
           ('2026/27', '2026-07-01', '2026-12-31')]
ARMS = ['A_deployed', 'D_2020', 'D_2012', 'B_all', 'C_noshots']


def load_frame():
    df = pd.read_pickle(os.path.join(EXP_DIR, '_prepared_fixed.pkl'))
    df = df.sort_values('date').reset_index(drop=True)
    df['league_cat'] = df['league'].astype(str).str.strip().astype('category')
    ip = np.column_stack([1 / df.B365H, 1 / df.B365D, 1 / df.B365A])
    df[['mkt_H', 'mkt_D', 'mkt_A']] = ip / ip.sum(axis=1, keepdims=True)
    df = add_ou_market(df)
    df['grp'] = np.where(df['league'].astype(str).isin(MAIN), 'main', 'extra')
    return df


def arm_train_rows(train, arm, feats, core):
    main = train['grp'] == 'main'
    if arm == 'A_deployed':
        t = train[main & (train['date'] >= '2024-07-01')].dropna(subset=feats)
    elif arm == 'D_2020':
        t = train[main & (train['date'] >= '2020-01-01')].dropna(subset=feats)
    elif arm == 'D_2012':
        t = train[main].dropna(subset=feats)
    else:
        t = train.dropna(subset=core)
    return t


def run_head(df, head):
    tr = ModelTrainer('data_sets/MatchHistory')
    base = ['B365H', 'B365D', 'B365A'] + (['H_form_ou', 'A_form_ou'] if head == 'ou' else [])
    feats = list(dict.fromkeys(base + tr.common_features))
    core = [f for f in feats if f not in SHOTS]
    target = 'target_1x2' if head == '1x2' else 'total_goals'
    d = df.dropna(subset=core + [target, 'mkt_H', 'mkt_D', 'mkt_A']).reset_index(drop=True)
    spec = get_spec(head, 'xgboost')
    parts = []
    for wname, lo, hi in WINDOWS:
        train = d[d['date'] < lo]
        test = d[(d['date'] >= lo) & (d['date'] < hi)].copy()
        out = test[['date', 'league', 'grp', target, 'mkt_H', 'mkt_D', 'mkt_A', 'mkt_over']].copy()
        out['window'] = wname
        for arm in ARMS:
            f = core if arm == 'C_noshots' else feats
            t = arm_train_rows(train, arm, feats, core)
            X = test[f].copy()
            if arm in ('A_deployed', 'D_2020', 'D_2012'):
                X[VENUE_SHOTS] = X[VENUE_SHOTS].fillna(0)      # predict_matches zero-fill
            m = spec.build()
            m.fit(t[f], t[target])
            if head == '1x2':
                out[[f'{arm}:H', f'{arm}:D', f'{arm}:A']] = m.predict_proba(X)
            else:
                lam = m.predict(X)
                out[f'{arm}:over'] = 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
            print(f'  [{head}] {wname} {arm:<11} train {len(t):6d} '
                  f'(main {int((t.grp == "main").sum())}, from {t.date.min():%Y-%m})  test {len(test)}', flush=True)
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


def losses(out, head, arm, s):
    if head == '1x2':
        y = out['target_1x2'].astype(int).to_numpy()[s]
        p = out[[f'{arm}:H', f'{arm}:D', f'{arm}:A']].to_numpy()[s] if arm != 'market' \
            else out[['mkt_H', 'mkt_D', 'mkt_A']].to_numpy()[s]
        return ll3(p, y), rps3(p, y)
    y = (out['total_goals'].to_numpy()[s] > 2.5).astype(int)
    p = out[f'{arm}:over'].to_numpy()[s] if arm != 'market' else out['mkt_over'].to_numpy()[s]
    return ll2(p, y), None


def score(out, head, P):
    res = {}
    for g in ['main', 'extra']:
        for wname in [w[0] for w in WINDOWS] + ['pooled']:
            s = (out['grp'] == g)
            if wname != 'pooled':
                s &= out['window'] == wname
            if head == 'ou':
                has_mkt = out['mkt_over'].notna()
                s_m = (s & has_mkt).to_numpy()
                use_mkt = s_m.sum() >= 200
                s = s_m if use_mkt else s.to_numpy()
            else:
                s, use_mkt = s.to_numpy(), True
            if s.sum() < 50:
                continue
            lm = losses(out, head, 'market', s)[0] if use_mkt else None
            hdr = f'  [{g:<5} {wname:<7}] n={s.sum():6d}'
            hdr += f'  market ll {lm.mean():.4f}' if lm is not None else '  (no O/U price: vs outcome only)'
            P(hdr)
            la_dep = losses(out, head, 'A_deployed', s)[0]
            for arm in ARMS:
                la, rp = losses(out, head, arm, s)
                line = f'      {arm:<11} ll {la.mean():.4f}'
                if rp is not None:
                    line += f'  rps {rp.mean():.4f}'
                rec = {'n': int(s.sum()), 'll': float(la.mean())}
                if lm is not None:
                    dm = la - lm
                    lo, hi = boot_ci(dm)
                    line += f'   Δmkt {dm.mean():+.4f} [{lo:+.4f},{hi:+.4f}]'
                    rec.update(d_mkt=float(dm.mean()), ci_mkt=[lo, hi])
                if arm != 'A_deployed':
                    dd = la - la_dep
                    lo, hi = boot_ci(dd)
                    line += f'   vs A {dd.mean():+.4f} [{lo:+.4f},{hi:+.4f}]'
                    rec.update(d_vs_A=float(dd.mean()), ci_vs_A=[lo, hi])
                res[f'{g}|{wname}|{arm}'] = rec
                P(line)
    return res


def main():
    df = load_frame()
    L = []
    P = L.append
    P('TRAINING COVERAGE — walk-forward, league codes restored (fixed corpus, 2012+)')
    P(f'frame: {len(df)} rows {df.date.min():%Y-%m-%d} .. {df.date.max():%Y-%m-%d}; '
      f'main {int(np.sum(df.grp == "main"))}, other leagues {int(np.sum(df.grp == "extra"))}')
    P('Δmkt = arm logloss − devigged reference price (negative beats the price); '
      'vs A = arm − A_deployed (negative = better than production)')
    payload = {}
    for head in ['1x2', 'ou']:
        out = run_head(df, head)
        out.to_pickle(os.path.join(EXP_DIR, f'_coverage_oof_{head}.pkl'))
        P('')
        P(f'== {head.upper()} head ==')
        payload[head] = score(out, head, P)
    text = '\n'.join(L) + '\n'
    print(text)
    base = write_report('training_coverage', payload, text)
    print(f'wrote {base}.{{json,txt}}')


if __name__ == '__main__':
    main()
