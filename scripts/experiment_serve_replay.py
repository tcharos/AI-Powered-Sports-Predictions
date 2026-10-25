"""What do stale serve-time inputs cost the deployed model? Live counterfactual.

scripts/update_football_data.py is pinned to season 2526, so since the
2026/27 season began every serve-time feature derived from MatchHistory —
L5/L10/L15 form, venue form, shots/corners, H2H — has been computed from
matches that ended in May 2026 for the 22 main leagues; and ELO
(data_sets/elo_ratings.json, last rebuilt 2026-09-18 off the same corpus)
has not seen a single 2026/27 European match.

This replays the DEPLOYED MatchPredictor (same model files, same code path —
it subclasses it and records exactly what the model was fed) over every
scraped slate since the model and ELO file were last written, under three
input regimes:

  R1  stale    current data_sets/MatchHistory (what production actually used)
  R2  fresh    + the 2026/27 season files (output/experiments/_fresh_history)
  R3  fresh+   R2, and ELO recomputed through each slate's date

History is truncated to rows dated before each slate, so no arm sees the
result it is predicting. The model, standings, odds and league routing are
identical across arms. R1 is checked against the probabilities production
actually logged (fidelity), then all arms are scored against outcomes and
against the devigged Flashscore price.

Nothing deployed is written: EntityResolver.save_mappings is a no-op here and
the predictor's CSV write is intercepted.

Usage: python3 scripts/experiment_serve_replay.py [--since 2026-09-19]
Writes output/experiments/serve_replay_<ts>.{json,txt} and _serve_replay_rows.pkl
"""
import argparse
import contextlib
import io
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import (EXP_DIR, PROJECT_ROOT, _dated_files, boot_ci,  # noqa: E402
                          brier3, fresh_history_dir, ll2, ll3, load_live_settled,
                          rps3, write_report)

sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'ml_project'))
os.chdir(PROJECT_ROOT)

import entity_resolver  # noqa: E402
entity_resolver.EntityResolver.save_mappings = lambda self: None   # read-only

from predict_matches import MatchPredictor  # noqa: E402
from elo_engine import EloTracker  # noqa: E402
from ml_project.calibration.league_aliases import LEAGUE_ALIASES  # noqa: E402

MAIN = {'B1', 'D1', 'D2', 'E0', 'E1', 'E2', 'E3', 'F1', 'F2', 'G1', 'I1', 'I2',
        'N1', 'P1', 'SC0', 'SC1', 'SC2', 'SC3', 'SP1', 'SP2', 'T1'}


def league_group(league):
    cat = LEAGUE_ALIASES.get(league, league)
    if cat in MAIN:
        return 'main'
    if str(league).startswith('EUROPE'):
        return 'cups'
    return 'extra'


class ReplayPredictor(MatchPredictor):
    """The deployed predictor, instrumented. Records every model input row
    and output, and swallows the predictions CSV instead of writing it."""

    def __init__(self, history_dir):
        with contextlib.redirect_stdout(io.StringIO()):
            super().__init__(history_dir=history_dir)
        self.full_history = self.history_df.copy()
        self.rec_1x2, self.rec_ou = [], []
        p1, pou = self.model_1x2.predict_proba, self.model_ou.predict

        def rec1(X):
            out = p1(X)
            self.rec_1x2.append((X.iloc[0].to_dict(), out[0]))
            return out

        def rec2(X):
            out = pou(X)
            self.rec_ou.append((X.iloc[0].to_dict(), float(out[0])))
            return out
        self.model_1x2.predict_proba = rec1
        self.model_ou.predict = rec2

    def run_slate(self, slate_date, slate_path, elo=None):
        self.history_df = self.full_history[self.full_history['date'] < pd.Timestamp(slate_date)]
        if elo is not None:
            self.resolver.elo_data = elo
        self.output_file = slate_path
        self.rec_1x2, self.rec_ou = [], []
        captured = {}
        orig = pd.DataFrame.to_csv

        def fake_to_csv(df, path=None, *a, **kw):
            if isinstance(path, str) and path.startswith('output/predictions_'):
                captured['df'] = df.copy()
                return None
            return orig(df, path, *a, **kw)
        pd.DataFrame.to_csv = fake_to_csv
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.predict()
        finally:
            pd.DataFrame.to_csv = orig
        out = captured.get('df')
        if out is None or out.empty:
            return pd.DataFrame()
        assert len(out) == len(self.rec_1x2) == len(self.rec_ou), 'record misaligned'
        feats = pd.DataFrame([r[0] for r in self.rec_1x2])
        probs = np.array([r[1] for r in self.rec_1x2])
        lam = np.array([r[1] for r in self.rec_ou])
        ou_feats = pd.DataFrame([r[0] for r in self.rec_ou])
        res = out[['League', 'Home Team', 'Away Team', 'match_id']].reset_index(drop=True)
        res['slate'] = slate_date
        res[['p_H', 'p_D', 'p_A']] = probs
        res['lam'] = lam
        res['p_over'] = 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
        feats.columns = [f'f_{c}' for c in feats.columns]
        ou_feats.columns = [f'fo_{c}' for c in ou_feats.columns]
        return pd.concat([res, feats.reset_index(drop=True), ou_feats.reset_index(drop=True)], axis=1)


def elo_asof(history, dates):
    """{date: ratings dict after every match strictly before that date}."""
    h = history[['date', 'home_team', 'away_team', 'FTHG', 'FTAG']].sort_values('date')
    tr = EloTracker()
    out, i, rows = {}, 0, h.to_numpy()
    for d in sorted(dates):
        ts = pd.Timestamp(d)
        while i < len(rows) and rows[i][0] < ts:
            _, ht, at, hg, ag = rows[i]
            if not (pd.isna(hg) or pd.isna(ag)):
                tr.update_ratings(ht, at, hg - ag, 1.0 if hg > ag else (0.5 if hg == ag else 0.0))
            i += 1
        out[d] = dict(tr.ratings)
    return out


def team_staleness(pred, slates_rows):
    """Days between slate date and each home team's latest corpus match."""
    hist = pred.full_history
    last = {}
    for team, g in pd.concat([hist[['date', 'home_team']].rename(columns={'home_team': 't'}),
                              hist[['date', 'away_team']].rename(columns={'away_team': 't'})]).groupby('t'):
        last[team] = np.sort(g['date'].to_numpy())
    out = []
    for _, r in slates_rows.iterrows():
        c = pred.resolver.get_canonical_name(r['Home Team'])
        arr = last.get(c)
        if arr is None:
            out.append(np.nan)
            continue
        k = np.searchsorted(arr, np.datetime64(pd.Timestamp(r['slate'])))
        out.append((pd.Timestamp(r['slate']) - pd.Timestamp(arr[k - 1])).days if k > 0 else np.nan)
    return np.array(out, dtype=float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-19',
                    help='first slate; model + ELO files unchanged since 2026-09-18')
    args = ap.parse_args()

    slates = {d: p for d, p in _dated_files('matches', 'json').items() if d >= args.since}
    preds = _dated_files('predictions', 'csv')
    slates = {d: p for d, p in slates.items() if d in preds}
    print(f'{len(slates)} slates {min(slates)} .. {max(slates)}')

    print('loading stale predictor ...')
    stale = ReplayPredictor('data_sets/MatchHistory')
    print('loading fresh predictor ...')
    fresh = ReplayPredictor(fresh_history_dir())
    print('computing as-of ELO on the fresh corpus ...')
    elo_fresh = elo_asof(fresh.full_history, list(slates))
    elo_live = dict(fresh.resolver.elo_data)

    arms = {}
    for name, pred, elo in [('R1_stale', stale, None), ('R2_fresh', fresh, 'live'),
                            ('R3_fresh_elo', fresh, 'asof')]:
        parts = []
        for d, p in sorted(slates.items()):
            e = elo_live if elo == 'live' else (elo_fresh[d] if elo == 'asof' else None)
            parts.append(pred.run_slate(d, p, elo=e))
        arms[name] = pd.concat(parts, ignore_index=True)
        print(f'  {name}: {len(arms[name])} predictions')

    live = load_live_settled()
    live = live[live['Cal 1X2 Source'].fillna('').astype(str).str.len() == 0]
    key = ['slate', 'Home Team', 'Away Team']
    base = live[key + ['League', 'y', 'over', 'mkt_H', 'mkt_D', 'mkt_A', 'mkt_over',
                       'Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)', 'Over % (raw)']]
    m = base.copy()
    for name, a in arms.items():
        a = a.drop_duplicates(key)
        cols = {c: f'{name}:{c}' for c in a.columns if c not in key}
        m = m.merge(a.rename(columns=cols), on=key, how='inner')
    m['grp'] = m['League'].map(league_group)
    m['stale_days_R1'] = team_staleness(stale, m)
    m['stale_days_R2'] = team_staleness(fresh, m)
    m.to_pickle(os.path.join(EXP_DIR, '_serve_replay_rows.pkl'))

    L = []
    P = L.append
    payload = {'slates': len(slates), 'n_scored': int(len(m))}
    P('SERVE-PATH REPLAY — deployed model, stale vs fresh inputs')
    P(f'{len(slates)} slates since {args.since}; {len(m)} settled club matches scored in all arms')
    P('')

    # Fidelity of the replay vs what production logged (2dp)
    lg = m[['Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)']].to_numpy()
    r1 = m[['R1_stale:p_H', 'R1_stale:p_D', 'R1_stale:p_A']].to_numpy()
    err = np.abs(lg - r1).max(axis=1)
    P(f'FIDELITY  R1 replay vs logged raw 1X2: median |err| {np.median(err):.4f}, '
      f'within 0.01: {np.mean(err <= 0.01):.1%}, within 0.03: {np.mean(err <= 0.03):.1%}')
    P('          (residual = standings snapshot now vs then; R1 vs R2 shares that, so the A/B is clean)')
    payload['fidelity'] = {'median': float(np.median(err)), 'within_001': float(np.mean(err <= 0.01))}
    P('')

    P('STALENESS  days from slate to the home team\'s latest corpus match (median / p90)')
    for g, s in m.groupby('grp'):
        P(f'  {g:<6} n={len(s):4d}  R1 stale corpus: {np.nanmedian(s.stale_days_R1):6.0f} / {np.nanpercentile(s.stale_days_R1, 90):4.0f}'
          f'    R2 fresh corpus: {np.nanmedian(s.stale_days_R2):5.0f} / {np.nanpercentile(s.stale_days_R2, 90):4.0f}')
    P('')

    def arm_probs(name):
        if name == 'logged':
            return m[['Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)']].to_numpy(), m['Over % (raw)'].to_numpy()
        if name == 'market':
            return m[['mkt_H', 'mkt_D', 'mkt_A']].to_numpy(), m['mkt_over'].to_numpy()
        return m[[f'{name}:p_H', f'{name}:p_D', f'{name}:p_A']].to_numpy(), m[f'{name}:p_over'].to_numpy()

    names = ['logged', 'R1_stale', 'R2_fresh', 'R3_fresh_elo', 'market']
    y, yo = m['y'].to_numpy(), m['over'].to_numpy()
    res = {}
    for g in ['main', 'extra', 'cups', 'all']:
        s = (m['grp'] == g).to_numpy() if g != 'all' else np.ones(len(m), bool)
        s &= m[['mkt_H', 'mkt_D', 'mkt_A']].notna().all(axis=1).to_numpy()
        if s.sum() < 30:
            continue
        P(f'[{g}]  n={s.sum()}   1X2 logloss / RPS / acc, and Δ vs market (paired, 95% CI)')
        res[g] = {}
        mk3, mko = arm_probs('market')
        for nm in names:
            p3, po = arm_probs(nm)
            ll, rp = ll3(p3[s], y[s]), rps3(p3[s], y[s])
            acc = np.mean(p3[s].argmax(1) == y[s])
            d = ll - ll3(mk3[s], y[s])
            lo, hi = boot_ci(d)
            so = s & ~np.isnan(mko) & ~np.isnan(po)
            llo = ll2(po[so], yo[so])
            do = llo - ll2(mko[so], yo[so])
            lo2, hi2 = boot_ci(do)
            res[g][nm] = {'ll': float(ll.mean()), 'rps': float(rp.mean()), 'acc': float(acc),
                          'd_ll_mkt': float(d.mean()), 'ci': [lo, hi],
                          'll_ou': float(llo.mean()), 'd_ll_ou_mkt': float(do.mean()), 'ci_ou': [lo2, hi2]}
            tail = '' if nm == 'market' else f'  Δmkt {d.mean():+.4f} [{lo:+.4f},{hi:+.4f}]   O/U Δmkt {do.mean():+.4f} [{lo2:+.4f},{hi2:+.4f}]'
            P(f'   {nm:<13} ll {ll.mean():.4f}  rps {rp.mean():.4f}  acc {acc:.3f}{tail}')
        # the A/B that matters: R2 vs R1, R3 vs R1
        for a, b in [('R2_fresh', 'R1_stale'), ('R3_fresh_elo', 'R1_stale')]:
            pa, _ = arm_probs(a)
            pb, _ = arm_probs(b)
            d = ll3(pa[s], y[s]) - ll3(pb[s], y[s])
            lo, hi = boot_ci(d)
            flips = np.mean(pa[s].argmax(1) != pb[s].argmax(1))
            P(f'   {a} − {b}: Δll {d.mean():+.4f} [{lo:+.4f},{hi:+.4f}]   picks changed {flips:.1%}')
            res[g][f'{a}-{b}'] = {'d_ll': float(d.mean()), 'ci': [lo, hi], 'flips': float(flips)}
        P('')
    payload['results'] = res

    # How far apart are the feature vectors? (main leagues)
    s = (m['grp'] == 'main').to_numpy()
    P('FEATURE SHIFT stale→fresh, main leagues (mean |Δ|, and share of rows that changed)')
    rows = []
    for c in ['H_form_pts', 'H_form_gf', 'H_form_pts_l15', 'H_home_pts', 'A_away_pts',
              'H_form_sf', 'H_elo', 'A_elo', 'elo_diff']:
        a1, a3 = m.get(f'R1_stale:f_{c}'), m.get(f'R3_fresh_elo:f_{c}')
        if a1 is None or a3 is None:
            continue
        d = (pd.to_numeric(a3, errors='coerce') - pd.to_numeric(a1, errors='coerce'))[s]
        rows.append((c, float(d.abs().mean()), float((d.abs() > 1e-9).mean())))
        P(f'   {c:<16} mean|Δ| {d.abs().mean():8.3f}   changed {np.mean(d.abs() > 1e-9):.1%}')
    payload['feature_shift_main'] = rows

    text = '\n'.join(L) + '\n'
    print(text)
    out = write_report('serve_replay', payload, text)
    print(f'wrote {out}.{{json,txt}}')


if __name__ == '__main__':
    main()
