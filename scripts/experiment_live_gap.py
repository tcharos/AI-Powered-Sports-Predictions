"""Why is the deployed model further behind the price live than out-of-fold?

Uses the exact feature rows the deployed model was fed during the serve
replay (experiment_serve_replay.py -> _serve_replay_rows.pkl, arm R1 = what
production computed) and re-scores the deployed booster under controlled
perturbations, so each candidate cause is isolated:

  1. attribution     SHAP share of |contribution| from the six price
                     features vs everything else — how much is the model
                     just reading the odds?
  2. bad entities    Δ vs market for fixtures with an unresolved team
                     (ELO 1500 + zero form) vs fixtures without one
  3. price scale     live Flashscore odds carry ~8% margin vs ~7% in training
                     — re-margin them to the training median and re-score
  4. compression     slope of logit(outcome) on logit(model) and on
                     logit(market), live

Usage: python3 scripts/experiment_live_gap.py
Writes output/experiments/live_gap_<ts>.{json,txt}
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import EXP_DIR, PROJECT_ROOT, boot_ci, ll3, write_report  # noqa: E402

sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'ml_project'))
os.chdir(PROJECT_ROOT)
from model_registry import load_1x2_model  # noqa: E402
from predict_matches import MatchPredictor  # noqa: E402

PRICE = ['B365H', 'B365D', 'B365A', 'IP_H', 'IP_D', 'IP_A']
TRAIN_OVR = 1.0686     # median overround of the training rows (main leagues)


def design(m, arm, feats, cats):
    X = pd.DataFrame({f: pd.to_numeric(m[f'{arm}:f_{f}'], errors='coerce') if f != 'league_cat'
                      else m[f'{arm}:f_{f}'] for f in feats if f'{arm}:f_{f}' in m})
    for f in feats:
        if f not in X:
            X[f] = 0.0
    X['league_cat'] = pd.Categorical(X['league_cat'].astype('string').str.strip(), categories=cats)
    # IP_* are not part of the serve row; predict_matches fills absent cols with 0
    return X[feats]


def remargin(X, target):
    X = X.copy()
    ip = np.column_stack([1 / X.B365H, 1 / X.B365D, 1 / X.B365A])
    fair = ip / ip.sum(1, keepdims=True)
    new = fair * target
    X['B365H'], X['B365D'], X['B365A'] = 1 / new[:, 0], 1 / new[:, 1], 1 / new[:, 2]
    return X


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def slope(p_side, y_side):
    """Calibration slope of a binary event on logit(p)."""
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=1e6).fit(logit(p_side).reshape(-1, 1), y_side)
    return float(lr.coef_[0, 0])


def main():
    m = pd.read_pickle(os.path.join(EXP_DIR, '_serve_replay_rows.pkl'))
    m = m.dropna(subset=['mkt_H']).reset_index(drop=True)
    model, fam = load_1x2_model('models')
    feats = json.load(open('models/features_1x2.json'))
    cats = MatchPredictor._load_model_league_categories('models/xgb_model_1x2.json')
    X = design(m, 'R1_stale', feats, cats)
    p0 = model.predict_proba(X)
    rep = np.abs(p0 - m[['R1_stale:p_H', 'R1_stale:p_D', 'R1_stale:p_A']].to_numpy()).max()
    y = m['y'].to_numpy()
    mk = m[['mkt_H', 'mkt_D', 'mkt_A']].to_numpy()
    L = []
    P = L.append
    payload = {}
    P('LIVE-GAP DIAGNOSIS — deployed 1X2 booster re-scored on the exact rows it was served')
    P(f'{len(m)} settled replay rows; re-score reproduces the replay to max |Δp| {rep:.2e}')
    zero_ip = all((X[c] == 0).all() for c in ['IP_H', 'IP_D', 'IP_A'] if c in X)
    P(f'NOTE: IP_H/IP_D/IP_A are in the 1X2 feature list but never built at serve time -> filled with 0: {zero_ip}')
    P('')

    # 1. attribution
    booster = model.get_booster()
    dm = xgb.DMatrix(X, enable_categorical=True)
    contrib = booster.predict(dm, pred_contribs=True)       # (n, 3, f+1)
    absc = np.abs(contrib[:, :, :-1]).sum(axis=1)           # (n, f)
    share = absc.sum(0) / absc.sum()
    order = np.argsort(-share)
    price_share = share[[feats.index(f) for f in PRICE if f in feats]].sum()
    P(f'1. SHAP |contribution| share, live rows: price features {price_share:.1%}, everything else {1 - price_share:.1%}')
    P('   top 12: ' + ', '.join(f'{feats[i]} {share[i]:.1%}' for i in order[:12]))
    payload['shap_price_share_live'] = float(price_share)
    payload['shap_top'] = [(feats[i], float(share[i])) for i in order[:20]]

    # 2. unresolved entities
    bad = ((m['R1_stale:f_H_elo'] == 1500) | (m['R1_stale:f_A_elo'] == 1500)).to_numpy()
    d = ll3(p0, y) - ll3(mk, y)
    P('')
    P('2. Δ logloss vs market by entity resolution (ELO 1500 = team not resolved -> zero form too)')
    for nm, s in [('resolved', ~bad), ('unresolved side', bad)]:
        lo, hi = boot_ci(d[s])
        P(f'   {nm:<16} n={s.sum():4d}  Δmkt {d[s].mean():+.4f} [{lo:+.4f},{hi:+.4f}]')
        payload[f'gap_{nm}'] = [float(d[s].mean()), lo, hi, int(s.sum())]

    # 3. price scale
    Xr = remargin(X, TRAIN_OVR)
    pr = model.predict_proba(Xr)
    dd = ll3(pr, y) - ll3(p0, y)
    lo, hi = boot_ci(dd)
    ovr = (1 / X.B365H + 1 / X.B365D + 1 / X.B365A)
    P('')
    P(f'3. price scale: live overround mean {ovr.mean():.4f} vs training median {TRAIN_OVR}')
    P(f'   re-margined to training scale: Δll {dd.mean():+.4f} [{lo:+.4f},{hi:+.4f}]  '
      f'picks changed {np.mean(pr.argmax(1) != p0.argmax(1)):.1%}')
    payload['remargin'] = [float(dd.mean()), lo, hi]
    # IP features absent at serve: what if they were built as in training?
    Xi = X.copy()
    Xi['IP_H'], Xi['IP_D'], Xi['IP_A'] = 1 / X.B365H, 1 / X.B365D, 1 / X.B365A
    pi = model.predict_proba(Xi)
    di = ll3(pi, y) - ll3(p0, y)
    lo, hi = boot_ci(di)
    di_mkt = ll3(pi, y) - ll3(mk, y)
    lo2, hi2 = boot_ci(di_mkt)
    P(f'   IP_* built as in training (1/odds) instead of 0: Δll {di.mean():+.4f} [{lo:+.4f},{hi:+.4f}]  '
      f'picks changed {np.mean(pi.argmax(1) != p0.argmax(1)):.1%};  now vs market {di_mkt.mean():+.4f} [{lo2:+.4f},{hi2:+.4f}]')
    payload['ip_fixed'] = {'d_vs_served': [float(di.mean()), lo, hi], 'd_vs_market': [float(di_mkt.mean()), lo2, hi2],
                           'flips': float(np.mean(pi.argmax(1) != p0.argmax(1)))}
    for g in ['main', 'extra', 'cups']:
        s = (m['grp'] == g).to_numpy()
        a, b = ll3(p0[s], y[s]) - ll3(mk[s], y[s]), ll3(pi[s], y[s]) - ll3(mk[s], y[s])
        P(f'     [{g:<5}] n={s.sum():4d}  Δmkt served {a.mean():+.4f}   Δmkt with IP fixed {b.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(b))}')
        payload[f'ip_fixed_{g}'] = [float(a.mean()), float(b.mean()), boot_ci(b)]
    contrib_i = booster.predict(xgb.DMatrix(Xi, enable_categorical=True), pred_contribs=True)
    absi = np.abs(contrib_i[:, :, :-1]).sum(axis=1)
    sh_i = absi.sum(0) / absi.sum()
    P(f'   SHAP price share with IP fixed: {sh_i[[feats.index(f) for f in PRICE]].sum():.1%}')
    payload['shap_price_share_ip_fixed'] = float(sh_i[[feats.index(f) for f in PRICE]].sum())

    # 4. compression: calibration slope on the favourite side
    P('')
    P('4. calibration slope (1.0 = calibrated, <1 = over-confident, >1 = compressed), home-win event')
    yh = (y == 0).astype(int)
    for nm, p in [('served model', p0[:, 0]), ('model, IP fixed', pi[:, 0]), ('market', mk[:, 0])]:
        P(f'   {nm:<16} slope {slope(p, yh):.3f}   mean p {p.mean():.3f}  actual {yh.mean():.3f}')
        payload[f'slope_{nm}'] = slope(p, yh)

    # 5. O/U head — same three IP columns, same zero fill
    from model_registry import load_ou_model
    from audit_common import ll2
    mou, _ = load_ou_model('models')
    feats_ou = json.load(open('models/features_ou.json'))
    Xo = pd.DataFrame({f: (pd.to_numeric(m[f'fo_{f}'] if f'fo_{f}' in m else m[f'R1_stale:fo_{f}'], errors='coerce')
                           if f != 'league_cat' else m[f'R1_stale:fo_{f}']) for f in feats_ou})
    Xo['league_cat'] = pd.Categorical(Xo['league_cat'].astype('string').str.strip(), categories=cats)
    Xo = Xo[feats_ou]

    def p_over(lam):
        return 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
    po0 = p_over(mou.predict(Xo))
    Xoi = Xo.copy()
    Xoi['IP_H'], Xoi['IP_D'], Xoi['IP_A'] = 1 / Xo.B365H, 1 / Xo.B365D, 1 / Xo.B365A
    poi = p_over(mou.predict(Xoi))
    ok = m['mkt_over'].notna().to_numpy()
    yo = m['over'].to_numpy()
    a = ll2(po0[ok], yo[ok]) - ll2(m['mkt_over'].to_numpy()[ok], yo[ok])
    b = ll2(poi[ok], yo[ok]) - ll2(m['mkt_over'].to_numpy()[ok], yo[ok])
    c = ll2(poi[ok], yo[ok]) - ll2(po0[ok], yo[ok])
    P('')
    P(f'5. O/U head (n={ok.sum()} with O/U odds); replay reproduces served λ: '
      f'{np.abs(po0 - m["R1_stale:p_over"].to_numpy()).max():.1e}')
    P(f'   Δll vs market: served {a.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(a))}   '
      f'IP fixed {b.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(b))}')
    P(f'   IP fixed − served: {c.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(c))}   '
      f'O/U picks changed {np.mean((po0 > .5) != (poi > .5)):.1%}   '
      f'mean P(over) served {po0.mean():.3f} fixed {poi.mean():.3f} actual {yo.mean():.3f}')
    P(f'   calibration slope (over): served {slope(po0[ok], yo[ok]):.3f}  fixed {slope(poi[ok], yo[ok]):.3f}  '
      f'market {slope(m["mkt_over"].to_numpy()[ok], yo[ok]):.3f}')
    payload['ou'] = {'served_vs_mkt': float(a.mean()), 'fixed_vs_mkt': float(b.mean()),
                     'fixed_minus_served': float(c.mean()), 'ci': boot_ci(c)}

    # 6. what the three lanes would have bet, served vs IP-fixed (pre-heuristic
    #    probabilities; the heuristic layer measured neutral). Flat 1u stakes,
    #    settled at the Flashscore price the model saw.
    P('')
    P('6. lane replay on these matches — flat 1u, 1X2 + O/U picks, settled at the scraped price')
    odds3 = np.column_stack([X.B365H, X.B365D, X.B365A])
    from audit_common import load_slate_odds
    so = load_slate_odds()
    oo = np.array([[so.get(mid, {}).get('odds_U', np.nan), so.get(mid, {}).get('odds_O', np.nan)]
                   for mid in m['R1_stale:match_id']])    # columns: [Under, Over]

    def lane_bets(p3, pov):
        rows = []
        n = len(m)
        pick = np.where(p3[:, 0] >= p3[:, 2], 0, 2)     # deployed never picks the draw
        conf = p3[np.arange(n), pick]
        odd = odds3[np.arange(n), pick]
        win = (y == pick)
        rows.append(('1X2', conf, odd, win))
        pk = (pov > 0.5).astype(int)                     # 1 = Over
        conf_o = np.where(pk == 1, pov, 1 - pov)
        odd_o = oo[np.arange(n), pk]
        win_o = (yo == pk)
        rows.append(('O/U', conf_o, odd_o, win_o))
        out = {}
        for mk_, cf, od, w in rows:
            valid = ~np.isnan(od) & (od > 1.0)
            ev = cf * od - 1
            lanes = {'value': valid & (ev > 0) & (cf >= 0.45),
                     'conviction': valid & (cf >= 0.58) & (od >= 1.40),
                     'model': valid}
            for ln, sel in lanes.items():
                r = np.where(w[sel], od[sel] - 1, -1.0)
                out[(ln, mk_)] = r
        return out
    served = lane_bets(p0, po0)
    fixed = lane_bets(pi, poi)
    P(f"   {'lane':<11}{'mkt':<5}{'served n':>9}{'ROI':>8}{'fixed n':>9}{'ROI':>8}   fixed ROI 95% CI")
    lanes_out = {}
    for k in served:
        rs, rf = served[k], fixed[k]
        lo, hi = boot_ci(rf)
        P(f'   {k[0]:<11}{k[1]:<5}{len(rs):>9}{rs.mean():>+8.1%}{len(rf):>9}{rf.mean():>+8.1%}   [{lo:+.1%}, {hi:+.1%}]')
        lanes_out[f'{k[0]}|{k[1]}'] = {'served_n': len(rs), 'served_roi': float(rs.mean()),
                                       'fixed_n': len(rf), 'fixed_roi': float(rf.mean()), 'fixed_ci': [lo, hi]}
    payload['lanes'] = lanes_out
    # claimed EV vs realised, 1X2 value-style picks
    for nm, p3 in [('served', p0), ('IP fixed', pi)]:
        pick = np.where(p3[:, 0] >= p3[:, 2], 0, 2)
        conf = p3[np.arange(len(m)), pick]
        odd = odds3[np.arange(len(m)), pick]
        ev = conf * odd - 1
        ret = np.where(y == pick, odd - 1, -1.0)
        rho = pd.Series(ev).corr(pd.Series(ret), method='spearman')
        P(f'   1X2 pick, Spearman ρ(claimed EV, realised return) {nm}: {rho:+.3f}   '
          f'share of picks with EV>0: {np.mean(ev > 0):.1%}   mean pick odds when EV>0: {odd[ev > 0].mean():.2f}')
        payload[f'rho_{nm}'] = float(rho)

    # 7. The heuristic layer on top of the CORRECTED model. Its rules fire on
    #    standings/form only (not on the probabilities), so the logged firing
    #    + magnitudes can be replayed onto any base vector exactly.
    from experiment_heuristic_audit import FAMILIES, parse_logs, rebuild
    from audit_common import load_live_settled
    live = load_live_settled()
    live = live[live['Cal 1X2 Source'].fillna('').astype(str).str.len() == 0]
    j = m[['slate', 'Home Team', 'Away Team']].merge(
        live[['slate', 'Home Team', 'Away Team', 'Adj Logs']].drop_duplicates(['slate', 'Home Team', 'Away Team']),
        how='left', on=['slate', 'Home Team', 'Away Team'])
    parsed = j['Adj Logs'].fillna('').map(parse_logs).tolist()
    P('')
    P('7. heuristic layer (H1-H6 + draw calibration/cap + goal-fest) applied on top of each base model')
    for nm, p3, pov in [('served', p0, po0), ('IP fixed', pi, poi)]:
        rb = [rebuild(p3[i], pov[i], parsed[i], set(FAMILIES)) for i in range(len(m))]
        h3 = np.array([r[0] for r in rb])
        ho = np.array([r[1] for r in rb])
        d1 = ll3(h3, y) - ll3(p3, y)
        d2 = ll2(ho, yo) - ll2(pov, yo)
        P(f'   on {nm:<9} 1X2 Δll {d1.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(d1))}   '
          f'O/U Δll {d2.mean():+.4f} {tuple(round(v, 4) for v in boot_ci(d2))}   '
          f'final vs market (1X2) {np.mean(ll3(h3, y) - ll3(mk, y)):+.4f}')
        payload[f'heuristic_on_{nm}'] = {'d_1x2': float(d1.mean()), 'ci': boot_ci(d1), 'd_ou': float(d2.mean())}

    text = '\n'.join(L) + '\n'
    print(text)
    base = write_report('live_gap', payload, text)
    print(f'wrote {base}.{{json,txt}}')


if __name__ == '__main__':
    main()
