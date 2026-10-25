"""Validate the 2026-10-25 football pipeline fixes before deploying them.

The fixes: IP_* built at serve time, training on 2012+ across every served
league (league codes restored, shots/corners dropped, season derived from the
date), unresolved teams skipped instead of served as ELO 1500, and the
heuristic rule layer removed.

A  CORPUS HOLDOUT. The fixed pipeline trained on matches before
   --holdout-start, scored on --holdout-start .. --replay-start against the
   devigged reference price. The same model is also scored with IP_* zeroed,
   which is what production served before the fix. Then a lane-threshold
   sweep at the corpus price, flat 1-unit stakes.

B  LIVE REPLAY. The fixed pipeline trained on matches before --replay-start,
   then every saved slate since replayed through the fixed MatchPredictor
   (as-of ELO, history truncated per slate). Scored on the settled matches
   against what production logged for them and against the devigged
   Flashscore price; reports skipped fixtures and lane volume / ROI at the
   deployed thresholds.

Nothing deployed is touched: models, ELO and the prepared frame are written
under output/validation/stage/.

Usage: python3 scripts/validate_pipeline_fixes.py
Writes output/experiments/validate_fixes_<ts>.{json,txt}
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import (PROJECT_ROOT, _dated_files, boot_ci, ll2, ll3,  # noqa: E402
                          load_live_settled, rps3, write_report)
from experiment_serve_replay import ReplayPredictor, elo_asof  # noqa: E402

from model_registry import get_spec, save_meta  # noqa: E402
from train_model import ModelTrainer  # noqa: E402
from feature_engineering import H2H_FEATURES  # noqa: E402

STAGE = os.path.join(PROJECT_ROOT, 'output', 'validation', 'stage')
DRAW_MARGIN = 0.05          # predict_matches: Draw only if it leads by this
LANES_DEPLOYED = {'value_min_conf': 0.45, 'conv_min_conf': 0.58, 'conv_min_odds': 1.40}


# ------------------------------------------------------------------ staging --

def build_stage():
    """A cwd for the pipeline whose writes land in the stage, reading the
    worktree's corpus and standings."""
    ds = os.path.join(STAGE, 'data_sets')
    os.makedirs(ds, exist_ok=True)
    os.makedirs(os.path.join(STAGE, 'models'), exist_ok=True)
    os.makedirs(os.path.join(STAGE, 'output'), exist_ok=True)
    src = os.path.join(PROJECT_ROOT, 'data_sets')
    for name in ('MatchHistory', 'standings', 'national_teams'):
        dst = os.path.join(ds, name)
        if not os.path.lexists(dst) and os.path.exists(os.path.join(src, name)):
            os.symlink(os.path.join(src, name), dst)
    for name in ('betting_config.json', 'team_mappings.json', 'league_calibration.json'):
        if os.path.exists(os.path.join(src, name)):
            shutil.copy(os.path.join(src, name), os.path.join(ds, name))
    os.chdir(STAGE)


def prepared_frame():
    cache = os.path.join(STAGE, '_prepared.pkl')
    if os.path.exists(cache):
        return pd.read_pickle(cache)
    with contextlib.redirect_stdout(io.StringIO()):
        df = ModelTrainer('data_sets/MatchHistory').prepare_data()
    df.to_pickle(cache)
    return df


def head_features(trainer, market):
    if market == '1x2':
        f = ['B365H', 'B365D', 'B365A'] + trainer.common_features
    else:
        f = ['B365H', 'B365D', 'B365A', 'H_form_ou', 'A_form_ou'] + trainer.common_features
    return list(dict.fromkeys(f))


def fit_heads(df, cutoff, save=False):
    """Mirror ModelTrainer.train_1x2 / train_ou's final fit (every usable
    row) on matches before `cutoff`. Returns {market: (model, feats, cats)}."""
    trainer = ModelTrainer('data_sets/MatchHistory')
    out = {}
    for market, target in (('1x2', 'target_1x2'), ('ou', 'total_goals')):
        spec = get_spec(market, 'xgboost')
        feats = head_features(trainer, market)
        d = df[df['date'] < pd.Timestamp(cutoff)].copy()
        d['total_goals'] = d['FTHG'] + d['FTAG']
        d = d.dropna(subset=[f for f in feats if f not in H2H_FEATURES] + [target])
        d = d.sort_values('date')
        d['league_cat'] = d['league_cat'].astype('category')
        model = spec.build()
        model.fit(d[feats], d[target])
        out[market] = (model, feats, d['league_cat'].cat.categories)
        print(f'  {market}: fit on {len(d):,} rows, {d.date.min().date()} .. {d.date.max().date()}, '
              f'{d.league.nunique()} leagues')
        if save:
            artifact = f'xgb_model_{market}.json'
            model.save_model(os.path.join('models', artifact))
            save_meta(market, 'xgboost', artifact)
            with open(os.path.join('models', f'features_{market}.json'), 'w') as f:
                json.dump(feats, f)
    return out


# -------------------------------------------------------------------- picks --

def pick_1x2(p):
    """predict_matches' pick rule -> (index, confidence)."""
    p = np.asarray(p, float)
    draw = p[:, 1] > np.maximum(p[:, 0], p[:, 2]) + DRAW_MARGIN
    idx = np.where(draw, 1, np.where(p[:, 0] >= p[:, 2], 0, 2))
    return idx, p[np.arange(len(p)), idx]


def lane_masks(conf, odd, ev, value_min_conf, conv_min_conf, conv_min_odds):
    valid = np.isfinite(odd) & (odd > 1.0)
    return {
        'value': valid & (ev > 0) & (conf >= value_min_conf),
        'conviction': valid & (conf >= conv_min_conf) & (odd >= conv_min_odds),
        'model': valid & (conf > 0),
    }


def roi_line(ret):
    if len(ret) < 10:
        return f'n={len(ret):5d}'
    lo, hi = boot_ci(ret)
    return f'n={len(ret):5d}  ROI {ret.mean():+6.1%} [{lo:+.1%}, {hi:+.1%}]'


# ----------------------------------------------------------------- part A --

def ou_prices(df):
    """(over, under) reference O/U 2.5 price per row, first valid source."""
    out = np.full((len(df), 2), np.nan)
    for o, u in (('B365>2.5', 'B365<2.5'), ('Avg>2.5', 'Avg<2.5'), ('BbAv>2.5', 'BbAv<2.5')):
        if o not in df.columns or u not in df.columns:
            continue
        v = df[[o, u]].apply(pd.to_numeric, errors='coerce').to_numpy()
        ok = np.isfinite(v).all(1) & (v > 1.0).all(1) & np.isnan(out[:, 0])
        out[ok] = v[ok]
    return out


def part_a(df, holdout_start, replay_start, L, payload):
    P = L.append
    P(f'A. CORPUS HOLDOUT  train < {holdout_start}, score {holdout_start} .. {replay_start}')
    heads = fit_heads(df, holdout_start)
    t = df[(df['date'] >= pd.Timestamp(holdout_start)) & (df['date'] < pd.Timestamp(replay_start))].copy()
    t = t.dropna(subset=['target_1x2', 'B365H', 'B365D', 'B365A']).reset_index(drop=True)
    m1, f1, cats1 = heads['1x2']
    mo, fo, catso = heads['ou']

    def predict(zero_ip):
        X1 = t[f1].copy()
        Xo = t[fo].copy()
        X1['league_cat'] = pd.Categorical(t['league_cat'].astype(str), categories=cats1)
        Xo['league_cat'] = pd.Categorical(t['league_cat'].astype(str), categories=catso)
        if zero_ip:
            for c in ('IP_H', 'IP_D', 'IP_A'):
                X1[c] = 0.0
                Xo[c] = 0.0
        p3 = m1.predict_proba(X1)
        lam = mo.predict(Xo)
        po = 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
        return p3, po

    fixed3, fixedo = predict(False)
    bug3, bugo = predict(True)
    odds3 = t[['B365H', 'B365D', 'B365A']].to_numpy(float)
    ip = 1 / odds3
    mkt3 = ip / ip.sum(1, keepdims=True)
    y = t['target_1x2'].astype(int).to_numpy()
    yo = ((t['FTHG'] + t['FTAG']) > 2.5).astype(int).to_numpy()

    oo = ou_prices(t)
    has_ou = np.isfinite(oo).all(1)
    io_ = 1 / oo
    mkto = io_[:, 0] / io_.sum(1)

    grp = np.where(t['league'].astype(str).str.contains(':'), 'extra', 'main')
    closing = t['odds_is_closing'].fillna(False).astype(bool).to_numpy()
    res = {}
    for g, s in [('all', np.ones(len(t), bool)), ('main', grp == 'main'), ('extra', grp == 'extra')]:
        P(f'  [{g}] n={s.sum():,}  (closing reference price on {closing[s].mean():.0%} of rows)')
        mk = ll3(mkt3[s], y[s])
        r = {}
        for nm, p in [('served (IP=0)', bug3), ('fixed', fixed3), ('market', mkt3)]:
            ll = ll3(p[s], y[s])
            d = ll - mk
            lo, hi = boot_ci(d)
            acc = np.mean(p[s].argmax(1) == y[s])
            tail = '' if nm == 'market' else f'   Δ vs market {d.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]'
            P(f'     1X2 {nm:<14} logloss {ll.mean():.4f}  RPS {rps3(p[s], y[s]).mean():.4f}  acc {acc:.3f}{tail}')
            r[nm] = {'ll': float(ll.mean()), 'd_mkt': float(d.mean()), 'ci': [lo, hi]}
        so = s & has_ou
        if so.sum() > 100:
            mko = ll2(mkto[so], yo[so])
            for nm, p in [('served (IP=0)', bugo), ('fixed', fixedo), ('market', mkto)]:
                ll = ll2(p[so], yo[so])
                d = ll - mko
                lo, hi = boot_ci(d)
                tail = '' if nm == 'market' else f'   Δ vs market {d.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]'
                P(f'     O/U {nm:<14} logloss {ll.mean():.4f}  (n={so.sum():,}){tail}')
                r[f'ou {nm}'] = {'ll': float(ll.mean()), 'd_mkt': float(d.mean()), 'ci': [lo, hi]}
        res[g] = r
    payload['A_scores'] = res
    P('')

    # Lanes at the corpus price. Flat 1u. 1X2 on every row; O/U where priced.
    def market_rows(p3, po):
        idx, conf = pick_1x2(p3)
        odd = odds3[np.arange(len(t)), idx]
        ret = np.where(idx == y, odd - 1, -1.0)
        oidx = (po >= 0.5).astype(int)          # 1 = Over
        oconf = np.where(oidx == 1, po, 1 - po)
        oodd = np.where(oidx == 1, oo[:, 0], oo[:, 1])
        oret = np.where(oidx == yo, oodd - 1, -1.0)
        return {'1X2': (conf, odd, conf * odd - 1, ret, np.ones(len(t), bool)),
                'O/U': (oconf, oodd, oconf * oodd - 1, oret, has_ou)}

    P('  Lanes at the deployed thresholds (value conf>=0.45 & EV>0; conviction conf>=0.58 & odds>=1.40)')
    P('  flat 1u at the reference price; volume is bets per 1,000 matches')
    lanes = {}
    for nm, p3, po in [('served (IP=0)', bug3, bugo), ('fixed', fixed3, fixedo)]:
        for mkt, (conf, odd, ev, ret, ok) in market_rows(p3, po).items():
            masks = lane_masks(conf, odd, ev, LANES_DEPLOYED['value_min_conf'],
                               LANES_DEPLOYED['conv_min_conf'], LANES_DEPLOYED['conv_min_odds'])
            for lane, msk in masks.items():
                sel = msk & ok
                per_k = 1000 * sel.sum() / max(ok.sum(), 1)
                P(f'     {nm:<14} {mkt:<4} {lane:<11} {per_k:6.0f}/1k  {roi_line(ret[sel])}')
                lanes[f'{nm}|{mkt}|{lane}'] = {'n': int(sel.sum()), 'roi': float(ret[sel].mean()) if sel.any() else None}
    payload['A_lanes_deployed'] = lanes
    P('')

    P('  Threshold sweep, FIXED model (flat 1u). Conviction: conf floor, odds >= 1.40')
    sweep = {}
    rows = market_rows(fixed3, fixedo)
    for mkt, (conf, odd, ev, ret, ok) in rows.items():
        for c in (0.50, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.75):
            sel = ok & np.isfinite(odd) & (conf >= c) & (odd >= 1.40)
            P(f'     {mkt:<4} conviction conf>={c:.2f}  {1000 * sel.sum() / max(ok.sum(), 1):6.0f}/1k  {roi_line(ret[sel])}')
            sweep[f'{mkt}|conv|{c}'] = {'n': int(sel.sum()), 'roi': float(ret[sel].mean()) if sel.any() else None}
    P('  Value: EV floor at conf >= 0.45')
    for mkt, (conf, odd, ev, ret, ok) in rows.items():
        for e in (0.0, 0.02, 0.05, 0.10, 0.20):
            sel = ok & np.isfinite(odd) & (ev > e) & (conf >= 0.45)
            P(f'     {mkt:<4} value EV>{e:.2f}        {1000 * sel.sum() / max(ok.sum(), 1):6.0f}/1k  {roi_line(ret[sel])}')
            sweep[f'{mkt}|value|{e}'] = {'n': int(sel.sum()), 'roi': float(ret[sel].mean()) if sel.any() else None}
    P('  Odds bands, FIXED model, every 1X2 pick')
    conf, odd, ev, ret, ok = rows['1X2']
    for lo_, hi_ in [(1.0, 1.4), (1.4, 1.7), (1.7, 2.2), (2.2, 3.0), (3.0, 50)]:
        sel = (odd >= lo_) & (odd < hi_)
        P(f'     odds {lo_:.1f}-{hi_:<4} mean conf {conf[sel].mean():.3f}  hit {np.mean(ret[sel] > -1):.3f}  '
          f'mkt-implied {np.mean(1 / odd[sel]):.3f}  {roi_line(ret[sel])}')
    payload['A_sweep'] = sweep
    P('')


# ----------------------------------------------------------------- part B --

SKIP_RE = re.compile(r'^\s+(\d+)x  (.*)$')


def parse_skips(text, slate_date):
    """Rows of predict_matches' skipped-fixture summary (fixed-width:
    league 32, team 26, then the reason)."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith('[!]'):
            inside = 'skipped' in line
            continue
        m = SKIP_RE.match(line) if inside else None
        if not m:
            continue
        rest = m.group(2)
        out.append({'slate': slate_date, 'n': int(m.group(1)), 'league': rest[:32].strip(),
                    'team': rest[33:59].strip(), 'why': rest[60:].strip()})
    return out


class FixedReplay(ReplayPredictor):
    """ReplayPredictor that also keeps the full predictions frame and the
    skipped-fixture summary of each slate."""

    def replay(self, slate_date, slate_path, elo):
        self.history_df = self.full_history[self.full_history['date'] < pd.Timestamp(slate_date)]
        self.resolver.elo_data = elo
        self.resolver._failed = set()      # a fresh process per nightly run
        self.output_file = slate_path
        self.rec_1x2, self.rec_ou = [], []
        captured, buf = {}, io.StringIO()
        orig = pd.DataFrame.to_csv

        def fake_to_csv(df, path=None, *a, **kw):
            if isinstance(path, str) and path.startswith('output/predictions_'):
                captured['df'] = df.copy()
                return None
            return orig(df, path, *a, **kw)
        pd.DataFrame.to_csv = fake_to_csv
        try:
            with contextlib.redirect_stdout(buf):
                self.predict()
        finally:
            pd.DataFrame.to_csv = orig
        skips = parse_skips(buf.getvalue(), slate_date)
        out = captured.get('df', pd.DataFrame())
        if not out.empty:
            out = out.copy()
            out['slate'] = slate_date
        return out, skips


def settle(df, conf_col, odd_col, pred_col, y, yo, market):
    """Flat-1u return of each row's pick in `market`."""
    conf = pd.to_numeric(df[conf_col], errors='coerce').to_numpy()
    odd = pd.to_numeric(df[odd_col], errors='coerce').to_numpy()
    pred = df[pred_col].astype(str).to_numpy()
    if market == '1X2':
        idx = np.select([pred == '1', pred == 'X', pred == '2'], [0, 1, 2], -1)
        won = idx == y
    else:
        won = np.where(pred == 'Over 2.5', yo == 1, yo == 0)
    return conf, odd, np.where(won, odd - 1, -1.0)


def part_b(df, replay_start, L, payload):
    P = L.append
    P(f'B. LIVE REPLAY  fixed pipeline trained < {replay_start}, every saved slate since')
    fit_heads(df, replay_start, save=True)
    slates = {d: p for d, p in _dated_files('matches', 'json').items() if d >= replay_start}
    preds = _dated_files('predictions', 'csv')
    slates = {d: p for d, p in slates.items() if d in preds}
    print(f'  replaying {len(slates)} slates {min(slates)} .. {max(slates)}')
    with contextlib.redirect_stdout(io.StringIO()):
        pred = FixedReplay('data_sets/MatchHistory')
    elo = elo_asof(pred.full_history, list(slates))
    outs, skips = [], []
    for d, p in sorted(slates.items()):
        o, s = pred.replay(d, p, elo[d])
        outs.append(o)
        skips += s
    new = pd.concat(outs, ignore_index=True)
    skips = pd.DataFrame(skips)

    # What production predicted for the same slates (club fixtures only).
    logged_all = pd.concat([pd.read_csv(preds[d]).assign(slate=d) for d in slates], ignore_index=True)
    logged_all = logged_all[~logged_all['Adj Logs'].fillna('').str.contains('NT model')]
    n_skip = int(skips['n'].sum()) if len(skips) else 0
    P(f'  {len(slates)} slates. production predicted {len(logged_all):,} club fixtures, '
      f'the fixed pipeline {len(new):,}; {n_skip} skipped for an unresolved team')
    if len(skips):
        top = skips.groupby(['league', 'team', 'why'])['n'].sum().sort_values(ascending=False)
        for (lg, team, why), n in top.head(15).items():
            P(f'     {n:3d}x  {lg:<30} {team:<24} {why}')
    payload['B_counts'] = {'slates': len(slates), 'logged': int(len(logged_all)),
                           'fixed': int(len(new)), 'skipped': n_skip}
    P('')

    # Score on settled matches present in both.
    live = load_live_settled()
    live = live[live['slate'] >= replay_start]
    key = ['slate', 'Home Team', 'Away Team']
    cols = ['Home Win %', 'Draw %', 'Away Win %', 'Over %',
            'Prediction 1X2', 'Prediction 1X2 Odd', 'Conf 1X2', 'EV 1X2',
            'Prediction O/U', 'Prediction O/U Odd', 'Conf O/U', 'EV O/U']
    n = new.drop_duplicates(key)[key + cols].rename(columns={c: f'new:{c}' for c in cols})
    m = live.merge(n, on=key, how='inner')
    m = m[m[['mkt_H', 'mkt_D', 'mkt_A']].notna().all(axis=1)].reset_index(drop=True)
    y, yo = m['y'].to_numpy(), m['over'].to_numpy()
    lost = live[~live.set_index(key).index.isin(n.set_index(key).index)]
    P(f'  scored: {len(m):,} settled club matches with a price, predicted by both '
      f'({len(lost)} settled matches production predicted and the fix skips)')

    def probs(prefix):
        p3 = m[[f'{prefix}Home Win %', f'{prefix}Draw %', f'{prefix}Away Win %']].apply(pd.to_numeric, errors='coerce').to_numpy()
        return p3 / p3.sum(1, keepdims=True), pd.to_numeric(m[f'{prefix}Over %'], errors='coerce').to_numpy()

    arms = {'logged final': probs(''), 'fixed': probs('new:'),
            'market': (m[['mkt_H', 'mkt_D', 'mkt_A']].to_numpy(), m['mkt_over'].to_numpy())}
    raw = m[['Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)']].to_numpy(float)
    arms['logged raw'] = (raw / raw.sum(1, keepdims=True), m['Over % (raw)'].to_numpy(float))
    mk3, mko = arms['market']
    res = {}
    for nm in ['logged final', 'logged raw', 'fixed', 'market']:
        p3, po = arms[nm]
        ll = ll3(p3, y)
        d = ll - ll3(mk3, y)
        lo, hi = boot_ci(d)
        so = np.isfinite(po) & np.isfinite(mko)
        llo = ll2(np.clip(po[so], 1e-3, 1 - 1e-3), yo[so])
        do = llo - ll2(mko[so], yo[so])
        lo2, hi2 = boot_ci(do)
        acc = np.mean(p3.argmax(1) == y)
        tail = '' if nm == 'market' else (f'  1X2 Δmkt {d.mean():+.4f} [{lo:+.4f},{hi:+.4f}]'
                                          f'  O/U Δmkt {do.mean():+.4f} [{lo2:+.4f},{hi2:+.4f}]')
        P(f'   {nm:<13} 1X2 ll {ll.mean():.4f} acc {acc:.3f}  O/U ll {llo.mean():.4f}{tail}')
        res[nm] = {'ll': float(ll.mean()), 'd_mkt': float(d.mean()), 'ci': [lo, hi],
                   'll_ou': float(llo.mean()), 'd_mkt_ou': float(do.mean()), 'ci_ou': [lo2, hi2]}
    p_old, _ = arms['logged final']
    p_new, _ = arms['fixed']
    d = ll3(p_new, y) - ll3(p_old, y)
    lo, hi = boot_ci(d)
    P(f'   fixed − logged final: 1X2 Δll {d.mean():+.4f} [{lo:+.4f},{hi:+.4f}], '
      f'1X2 picks changed on {np.mean(m["Prediction 1X2"].astype(str) != m["new:Prediction 1X2"].astype(str)):.1%}')
    P(f'   mean |P(home) − market|: logged final {np.mean(np.abs(p_old[:, 0] - mk3[:, 0])):.3f}, '
      f'fixed {np.mean(np.abs(p_new[:, 0] - mk3[:, 0])):.3f}')
    payload['B_scores'] = res
    P('')

    P('  Lanes at the deployed thresholds on these matches (flat 1u at the scraped price)')
    lanes = {}
    for nm, prefix in [('logged final', ''), ('fixed', 'new:')]:
        for mkt, c, o, pr, e in [('1X2', 'Conf 1X2', 'Prediction 1X2 Odd', 'Prediction 1X2', 'EV 1X2'),
                                 ('O/U', 'Conf O/U', 'Prediction O/U Odd', 'Prediction O/U', 'EV O/U')]:
            conf, odd, ret = settle(m, prefix + c, prefix + o, prefix + pr, y, yo, mkt)
            ev = pd.to_numeric(m[prefix + e], errors='coerce').to_numpy()
            masks = lane_masks(conf, odd, ev, LANES_DEPLOYED['value_min_conf'],
                               LANES_DEPLOYED['conv_min_conf'], LANES_DEPLOYED['conv_min_odds'])
            for lane, msk in masks.items():
                P(f'     {nm:<13} {mkt:<4} {lane:<11} {roi_line(ret[msk])}')
                lanes[f'{nm}|{mkt}|{lane}'] = {'n': int(msk.sum()),
                                               'roi': float(ret[msk].mean()) if msk.any() else None}
    payload['B_lanes'] = lanes
    P('')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--holdout-start', default='2025-07-01')
    ap.add_argument('--replay-start', default='2026-09-19')
    args = ap.parse_args()

    build_stage()
    print('preparing the training frame with the fixed pipeline ...')
    df = prepared_frame()
    print(f'  {len(df):,} rows, {df.date.min().date()} .. {df.date.max().date()}, '
          f'{df.league.nunique()} leagues')

    L, payload = [], {'holdout_start': args.holdout_start, 'replay_start': args.replay_start}
    L.append('VALIDATION OF THE 2026-10-25 PIPELINE FIXES')
    L.append(f'training frame: {len(df):,} rows 2012+, {df.league.nunique()} leagues')
    L.append('')
    part_a(df, args.holdout_start, args.replay_start, L, payload)
    print('\n'.join(L))
    part_b(df, args.replay_start, L, payload)

    text = '\n'.join(L) + '\n'
    print(text)
    os.chdir(PROJECT_ROOT)
    out = write_report('validate_fixes', payload, text)
    print(f'wrote {out}.{{json,txt}}')


if __name__ == '__main__':
    main()
