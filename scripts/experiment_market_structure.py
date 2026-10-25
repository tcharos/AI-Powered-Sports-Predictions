"""Is there a structural, model-free inefficiency in the prices this system bets?

The conviction lane is the only lane with positive live ROI, and E0 found
its gate behaves like a short-price filter. If its profit is real it should
show up as a favourite-longshot bias in the market alone, on far more data
than the live record. This scores model-free rules on every main-league
match since 2012 (league codes restored, see audit_prepare_frames.py):

  1. ROI of backing each outcome by odds band — favourite / draw / underdog,
     Over / Under — at the B365 pre-match price, and at the best price
     (Max*) as an upper bound for line shopping.
  2. A market-only replica of the conviction gate: devigged favourite
     probability >= 0.58 and odds >= 1.40 — no model — per season.

Flat 1u stakes, match-level bootstrap CIs.

Usage: python3 scripts/experiment_market_structure.py
Writes output/experiments/market_structure_<ts>.{json,txt}
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import EXP_DIR, boot_ci, write_report  # noqa: E402

MAIN = {'B1', 'D1', 'D2', 'E0', 'E1', 'E2', 'E3', 'F1', 'F2', 'G1', 'I1', 'I2',
        'N1', 'P1', 'SC0', 'SC1', 'SC2', 'SC3', 'SP1', 'SP2', 'T1', 'EC'}
BANDS = [1.0, 1.3, 1.5, 1.7, 2.0, 2.5, 3.5, 5.0, 100]


def roi_table(odds, win, label, P, out):
    P(f'  {label}')
    P(f"    {'odds band':<12}{'n':>7}{'hit':>7}{'implied':>9}{'ROI':>8}   95% CI")
    for lo, hi in zip(BANDS[:-1], BANDS[1:]):
        s = (odds >= lo) & (odds < hi) & ~np.isnan(odds)
        if s.sum() < 200:
            continue
        r = np.where(win[s], odds[s] - 1, -1.0)
        a, b = boot_ci(r, n=2000)
        P(f'    {lo:>4.1f}-{hi if hi < 100 else "":<6}{s.sum():>7}{win[s].mean():>7.3f}{np.mean(1 / odds[s]):>9.3f}'
          f'{r.mean():>+8.1%}   [{a:+.1%}, {b:+.1%}]')
        out.append({'rule': label, 'band': [lo, hi], 'n': int(s.sum()), 'roi': float(r.mean()), 'ci': [a, b]})


def main():
    df = pd.read_pickle(os.path.join(EXP_DIR, '_prepared_fixed.pkl'))
    df = df[df['league'].astype(str).isin(MAIN)].dropna(subset=['B365H', 'B365D', 'B365A', 'FTR']).copy()
    df['season'] = np.where(df.date.dt.month >= 8, df.date.dt.year, df.date.dt.year - 1)
    y = df['FTR'].map({'H': 0, 'D': 1, 'A': 2}).to_numpy()
    tg = (df['FTHG'] + df['FTAG']).to_numpy()
    L = []
    P = L.append
    rows = []
    P(f'MARKET STRUCTURE — {len(df)} main-league matches, seasons {df.season.min()}-{df.season.max()}')
    P('')
    for src, cols in [('B365 (pre-match)', ['B365H', 'B365D', 'B365A']),
                      ('best price (Max)', ['MaxH', 'MaxD', 'MaxA'])]:
        if not all(c in df for c in cols):
            continue
        o = df[cols].apply(pd.to_numeric, errors='coerce').to_numpy()
        ok = ~np.isnan(o).any(axis=1)
        o_, y_ = o[ok], y[ok]
        fav = np.argmin(np.where(np.arange(3) == 1, np.inf, o_), axis=1)   # shorter of H/A
        dog = 2 - fav
        n = len(o_)
        P(f'== 1X2 at {src}  (n={n}, mean overround {np.mean((1 / o_).sum(1)):.4f}) ==')
        roi_table(o_[np.arange(n), fav], y_ == fav, f'{src}: back the favourite', P, rows)
        roi_table(o_[:, 1], y_ == 1, f'{src}: back the draw', P, rows)
        roi_table(o_[np.arange(n), dog], y_ == dog, f'{src}: back the underdog', P, rows)
        P('')
    for src, (oc, uc) in [('B365', ('B365>2.5', 'B365<2.5')), ('best price', ('Max>2.5', 'Max<2.5'))]:
        if oc not in df or uc not in df:
            continue
        oo = pd.to_numeric(df[oc], errors='coerce').to_numpy()
        uu = pd.to_numeric(df[uc], errors='coerce').to_numpy()
        ok = ~np.isnan(oo) & ~np.isnan(uu)
        P(f'== O/U 2.5 at {src} (n={ok.sum()}) ==')
        roi_table(oo[ok], tg[ok] > 2.5, f'{src}: back Over', P, rows)
        roi_table(uu[ok], tg[ok] < 2.5, f'{src}: back Under', P, rows)
        P('')

    # market-only conviction replica
    o = df[['B365H', 'B365D', 'B365A']].to_numpy()
    fair = (1 / o) / (1 / o).sum(1, keepdims=True)
    fav = np.where(o[:, 0] <= o[:, 2], 0, 2)
    n = len(o)
    pf, of = fair[np.arange(n), fav], o[np.arange(n), fav]
    sel = (pf >= 0.58) & (of >= 1.40)
    r = np.where(y[sel] == fav[sel], of[sel] - 1, -1.0)
    a, b = boot_ci(r, n=2000)
    P(f'== market-only conviction replica (fair fav prob >= 0.58, odds >= 1.40, B365) ==')
    P(f'  all seasons: n={sel.sum()}  ROI {r.mean():+.2%} [{a:+.2%}, {b:+.2%}]  mean odds {of[sel].mean():.2f}')
    seas = df['season'].to_numpy()[sel]
    per = []
    for s_ in sorted(set(seas)):
        rr = r[seas == s_]
        per.append((int(s_), len(rr), float(rr.mean())))
        P(f'    {s_}/{(s_ + 1) % 100:02d}  n={len(rr):5d}  ROI {rr.mean():+.2%}')
    pos = sum(1 for _, _, v in per if v > 0)
    P(f'  seasons positive: {pos}/{len(per)}')
    payload = {'rows': rows, 'conviction_replica': {'n': int(sel.sum()), 'roi': float(r.mean()), 'ci': [a, b],
                                                    'per_season': per}}
    text = '\n'.join(L) + '\n'
    print(text)
    base = write_report('market_structure', payload, text)
    print(f'wrote {base}.{{json,txt}}')


if __name__ == '__main__':
    main()
