"""Does the HeuristicAdjuster layer help or hurt? Measured on live settled bets.

Every prediction CSV logs the model's raw probabilities, the final
(post-heuristic) probabilities, and the adjuster's own log of every rule that
fired with its magnitude. That is enough to rebuild the adjuster exactly from
raw probs + logs, and so to ablate it rule by rule on real served predictions,
without needing the standings snapshot that was live at the time.

  Part 1  raw vs final vs market on 1X2 and O/U (logloss / Brier / RPS / acc)
  Part 2  reconstruction check (rebuilt final vs logged final)
  Part 3  leave-one-rule-family-out and only-this-family ablations
  Part 4  pick flips: when the heuristic changed the 1X2 pick, who was right?
  Part 5  reliability of the probability the bets are sized on (final conf)

Usage: python3 scripts/experiment_heuristic_audit.py
Writes output/experiments/heuristic_audit_<ts>.{json,txt}
"""
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import (boot_ci, brier2, brier3, ll2, ll3, load_live_settled,  # noqa: E402
                          rps3, write_report)

H, D, A = 0, 1, 2
CAP = 0.15            # HeuristicAdjuster.MAX_TOTAL_BOOST_PER_CLASS
OPP_SHARE = 0.7       # HeuristicAdjuster.FADE_TO_OPPONENT_SHARE
ALPHA = 0.15          # draw shrink weight

FAMILIES = ['cal', 'cap', 'h1_rank', 'h2_spec_rank', 'h3_form', 'h4_spec_form',
            'h6_trend', 'h5_goalfest']

# (regex, family, kind, index) — kind 'boost' lifts idx; 'fade' lifts opp idx
# by 0.7*m and Draw by 0.3*m. Index is the outcome receiving the lift.
RULES = [
    (r'^Rank Boost Home', 'h1_rank', 'boost', H),
    (r'^Rank Boost Away', 'h1_rank', 'boost', A),
    (r'^Spec Rank Home', 'h2_spec_rank', 'boost', H),
    (r'^Spec Rank Away', 'h2_spec_rank', 'boost', A),
    (r'^Form Boost Home', 'h3_form', 'boost', H),
    (r'^Form Boost Away', 'h3_form', 'boost', A),
    (r'^Form Fade Home', 'h3_form', 'fade', A),
    (r'^Form Fade Away', 'h3_form', 'fade', H),
    (r'^Spec Form Home Boost', 'h4_spec_form', 'boost', H),
    (r'^Spec Form Away Boost', 'h4_spec_form', 'boost', A),
    (r'^Spec Form Home Fade', 'h4_spec_form', 'fade', A),
    (r'^Spec Form Away Fade', 'h4_spec_form', 'fade', H),
    (r'^Home (Heating Up|Consistent)', 'h6_trend', 'boost', H),
    (r'^Away (Heating Up|Consistent)', 'h6_trend', 'boost', A),
    (r'^Home Cooling', 'h6_trend', 'fade', A),
    (r'^Away Cooling', 'h6_trend', 'fade', H),
]


def parse_logs(s):
    """-> dict(target_draw, deltas=[(family, idx_vector)], goalfest=bool)"""
    out = {'target': None, 'deltas': [], 'goalfest': False, 'standings': True}
    for part in (s or '').split('; '):
        m = re.match(r'Calibration: Draw [\d.]+->[\d.]+ \(Target ([\d.]+)\)', part)
        if m:
            out['target'] = float(m.group(1))
            continue
        if part.startswith('No Standings Data'):
            out['standings'] = False
            continue
        if part.startswith('Goal Fest Boost'):
            out['goalfest'] = True
            continue
        for rx, fam, kind, idx in RULES:
            if re.match(rx, part):
                nums = [float(x) for x in re.findall(r'\(\+([\d.]+)', part)]
                vec = np.zeros(3)
                if kind == 'boost':
                    vec[idx] += nums[-1]
                else:   # "(+opp opp, +d D)"
                    m2 = re.search(r'\(\+([\d.]+) opp, \+([\d.]+) D\)', part)
                    vec[idx] += float(m2.group(1))
                    vec[D] += float(m2.group(2))
                out['deltas'].append((fam, vec))
                break
    return out


def rebuild(raw3, raw_over, parsed, active):
    """Re-run HeuristicAdjuster.adjust_probabilities from raw probs + parsed
    log, with only the rule families in `active` switched on."""
    p = np.array(raw3, dtype=float)
    p = p / p.sum()
    t = parsed['target']
    if t is not None:
        if 'cal' in active:
            nd = (1 - ALPHA) * p[D] + ALPHA * t
            ratio = (1 - nd) / max(1 - p[D], 1e-3)
            p = np.array([p[H] * ratio, nd, p[A] * ratio])
            p = p / p.sum()
        if 'cap' in active and p[D] > t + 0.05:
            diff = p[D] - (t + 0.05)
            p[D] = t + 0.05
            nd = p[H] + p[A]
            p[H] += diff * p[H] / nd
            p[A] += diff * p[A] / nd
    delta = np.zeros(3)
    for fam, vec in parsed['deltas']:
        if fam in active:
            delta += vec
    delta = np.clip(delta, -CAP, CAP)
    p = np.maximum(p + delta, 0)
    p = p / p.sum()

    over = raw_over
    if parsed['goalfest'] and 'h5_goalfest' in active:
        over = (raw_over + 0.05) / 1.05
    return p, over


def scores(p3, pover, y, yo):
    return {
        'll': ll3(p3, y), 'brier': brier3(p3, y), 'rps': rps3(p3, y),
        'acc': (p3.argmax(axis=1) == y).astype(float),
        'll_ou': ll2(pover, yo), 'brier_ou': brier2(pover, yo),
        'acc_ou': ((pover > 0.5).astype(int) == yo).astype(float),
    }


def fmt_ci(d):
    lo, hi = boot_ci(d)
    return f"{np.mean(d):+.4f} [{lo:+.4f}, {hi:+.4f}]"


def main():
    full = load_live_settled()
    full['platt'] = full['Cal 1X2 Source'].fillna('').astype(str).str.len() > 0
    # Platt-period rows: raw -> final also includes per-league calibration, so
    # they say nothing about the heuristic alone. Report them, then drop them.
    fp = full[full.platt]
    if len(fp):
        fp3 = fp[['Home Win %', 'Draw %', 'Away Win %']].to_numpy()
        rp3 = fp[['Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)']].to_numpy()
        d_platt = ll3(fp3, fp.y.to_numpy()) - ll3(rp3, fp.y.to_numpy())
        platt_line = (f"[Platt-period rows, raw -> Platt+heuristic: n={len(fp)}  "
                      f"Δll {fmt_ci(d_platt)}  acc raw {np.mean(rp3.argmax(1) == fp.y):.1%} "
                      f"-> final {np.mean(fp3.argmax(1) == fp.y):.1%}]")
    else:
        platt_line = ''
    df = full[~full.platt].reset_index(drop=True)
    df['parsed'] = df['Adj Logs'].fillna('').map(parse_logs)
    raw3 = df[['Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)']].to_numpy()
    raw_over = df['Over % (raw)'].to_numpy()
    fin3 = df[['Home Win %', 'Draw %', 'Away Win %']].to_numpy()
    fin_over = df['Over %'].to_numpy()
    y, yo = df['y'].to_numpy(), df['over'].to_numpy()
    n = len(df)
    lines = []
    P = lines.append
    payload = {'n': n, 'slates': int(df.slate.nunique()),
               'first': df.slate.min(), 'last': df.slate.max()}
    P(f"HEURISTIC LAYER AUDIT — live settled club predictions")
    P(f"{n} matches over {df.slate.nunique()} slates ({df.slate.min()} .. {df.slate.max()})")
    P(f"standings found (H1-H6 eligible): {sum(p['standings'] for p in df.parsed)} / {n}")
    P(f"(rows where per-league Platt was ALSO applied are excluded: {len(fp)})")
    P(platt_line)
    P("")

    # ---- Part 1: raw vs final (all), and vs market where odds survive ----
    s_raw = scores(raw3, raw_over, y, yo)
    s_fin = scores(fin3, fin_over, y, yo)
    P("PART 1 — raw model vs final (post-heuristic), all non-Platt settled matches")
    P(f"{'metric':<10}{'raw':>10}{'final':>10}   final-raw [95% CI]  (lower is better except acc)")
    part1 = {}
    for k in ['ll', 'brier', 'rps', 'acc', 'll_ou', 'brier_ou', 'acc_ou']:
        d = s_fin[k] - s_raw[k]
        part1[k] = {'raw': float(s_raw[k].mean()), 'final': float(s_fin[k].mean()),
                    'diff': float(d.mean()), 'ci': boot_ci(d)}
        P(f"{k:<10}{s_raw[k].mean():>10.4f}{s_fin[k].mean():>10.4f}   {fmt_ci(d)}")
    payload['part1_all'] = part1

    m = df[['mkt_H', 'mkt_D', 'mkt_A']].notna().all(axis=1).to_numpy()
    mo = df['mkt_over'].notna().to_numpy()
    P("")
    P(f"PART 1b — same, on the {m.sum()} matches whose pre-match odds survived (vs devigged Flashscore price)")
    mk3 = df.loc[m, ['mkt_H', 'mkt_D', 'mkt_A']].to_numpy()
    sub = {}
    for name, p3 in [('raw', raw3[m]), ('final', fin3[m]), ('market', mk3)]:
        sub[name] = {'ll': ll3(p3, y[m]), 'brier': brier3(p3, y[m]), 'rps': rps3(p3, y[m]),
                     'acc': (p3.argmax(1) == y[m]).astype(float)}
    P(f"{'metric':<8}{'raw':>9}{'final':>9}{'market':>9}   raw-mkt            final-mkt")
    part1b = {}
    for k in ['ll', 'brier', 'rps', 'acc']:
        P(f"{k:<8}{sub['raw'][k].mean():>9.4f}{sub['final'][k].mean():>9.4f}{sub['market'][k].mean():>9.4f}"
          f"   {fmt_ci(sub['raw'][k] - sub['market'][k])}  {fmt_ci(sub['final'][k] - sub['market'][k])}")
        part1b[k] = {nm: float(sub[nm][k].mean()) for nm in sub}
    if mo.sum() > 50:
        P(f"O/U on {mo.sum()} with odds:  ll raw {ll2(raw_over[mo], yo[mo]).mean():.4f}  "
          f"final {ll2(fin_over[mo], yo[mo]).mean():.4f}  market {ll2(df.mkt_over[mo], yo[mo]).mean():.4f}")
        part1b['ll_ou'] = {'raw': float(ll2(raw_over[mo], yo[mo]).mean()),
                           'final': float(ll2(fin_over[mo], yo[mo]).mean()),
                           'market': float(ll2(df.mkt_over[mo].to_numpy(), yo[mo]).mean())}
    payload['part1_with_odds'] = {'n': int(m.sum()), **part1b}

    # ---- Part 2: reconstruction fidelity ----
    rb = [rebuild(raw3[i], raw_over[i], df.parsed.iat[i], set(FAMILIES)) for i in range(n)]
    rb3 = np.array([r[0] for r in rb])
    rbo = np.array([r[1] for r in rb])
    err = np.abs(rb3 - fin3).max(axis=1)
    erro = np.abs(rbo - fin_over)
    P("")
    P("PART 2 — reconstruction from raw + logs vs logged final (both logged at 2dp)")
    P(f"1X2 max-abs err: median {np.median(err):.4f}  p95 {np.percentile(err, 95):.4f}  max {err.max():.4f}")
    P(f"O/U abs err:     median {np.median(erro):.4f}  p95 {np.percentile(erro, 95):.4f}  max {erro.max():.4f}")
    payload['part2'] = {'p95_err_1x2': float(np.percentile(err, 95)), 'p95_err_ou': float(np.percentile(erro, 95))}

    # ---- Part 3: ablations ----
    s_full = scores(rb3, rbo, y, yo)
    P("")
    P("PART 3 — ablation on the reconstructed adjuster (Δ logloss vs reconstructed-full; negative = removing it HELPS)")
    P(f"{'family':<14}{'fires':>7}{'Δll 1X2 drop-one':>30}{'Δll 1X2 only-this vs raw':>32}")
    part3 = {}
    for fam in FAMILIES:
        fires = sum(1 for p in df.parsed if (fam in ('cal', 'cap') and p['target'] is not None)
                    or any(f == fam for f, _ in p['deltas']) or (fam == 'h5_goalfest' and p['goalfest']))
        rest = set(FAMILIES) - {fam}
        r = [rebuild(raw3[i], raw_over[i], df.parsed.iat[i], rest) for i in range(n)]
        s_drop = scores(np.array([x[0] for x in r]), np.array([x[1] for x in r]), y, yo)
        r1 = [rebuild(raw3[i], raw_over[i], df.parsed.iat[i], {fam}) for i in range(n)]
        s_only = scores(np.array([x[0] for x in r1]), np.array([x[1] for x in r1]), y, yo)
        key = 'll_ou' if fam == 'h5_goalfest' else 'll'
        dd = s_drop[key] - s_full[key]
        do = s_only[key] - s_raw[key]
        part3[fam] = {'fires': fires, 'metric': key, 'drop_one': float(dd.mean()), 'drop_one_ci': boot_ci(dd),
                      'only_this': float(do.mean()), 'only_this_ci': boot_ci(do)}
        P(f"{fam:<14}{fires:>7}   {fmt_ci(dd):>27}   {fmt_ci(do):>29}" + ('  (O/U)' if key == 'll_ou' else ''))
    payload['part3'] = part3

    # ---- Part 4: pick flips ----
    raw_pick = raw3.argmax(1)
    lab = df['Prediction 1X2'].astype(str).map({'1': 0, 'X': 1, '2': 2}).to_numpy()
    flip = raw_pick != lab
    P("")
    P("PART 4 — final 1X2 pick (incl. the draw-margin rule) vs raw argmax")
    P(f"picks changed: {flip.sum()} / {n} ({flip.mean():.1%})")
    if flip.sum():
        P(f"  on changed picks: raw pick right {np.mean(raw_pick[flip] == y[flip]):.1%}, "
          f"final pick right {np.mean(lab[flip] == y[flip]):.1%}")
    P(f"overall pick accuracy: raw argmax {np.mean(raw_pick == y):.1%}  deployed pick {np.mean(lab == y):.1%}")
    P(f"draws: actual rate {np.mean(y == 1):.1%}; raw argmax picks draw {np.mean(raw_pick == 1):.1%}; "
      f"deployed picks draw {np.mean(lab == 1):.1%}")
    ou_raw = (raw_over > 0.5).astype(int)
    ou_fin = df['Prediction O/U'].astype(str).str.startswith('Over').astype(int).to_numpy()
    fo = ou_raw != ou_fin
    P(f"O/U picks changed: {fo.sum()} ({fo.mean():.1%}); on those raw right {np.mean(ou_raw[fo] == yo[fo]) if fo.sum() else float('nan'):.1%}, "
      f"final right {np.mean(ou_fin[fo] == yo[fo]) if fo.sum() else float('nan'):.1%}")
    payload['part4'] = {'flips': int(flip.sum()), 'raw_acc_on_flips': float(np.mean(raw_pick[flip] == y[flip])) if flip.sum() else None,
                        'final_acc_on_flips': float(np.mean(lab[flip] == y[flip])) if flip.sum() else None,
                        'ou_flips': int(fo.sum())}

    # ---- Part 5: reliability of the picked-side probability ----
    conf_fin = fin3[np.arange(n), lab]
    conf_raw = raw3[np.arange(n), lab]
    hit = (lab == y).astype(float)
    P("")
    P("PART 5 — reliability of the 1X2 pick's probability (what EV and stakes are computed from)")
    P(f"{'band':<12}{'n':>6}{'final conf':>12}{'raw conf':>10}{'market':>9}{'hit rate':>10}")
    mk_pick = np.full(n, np.nan)
    mm = df[['mkt_H', 'mkt_D', 'mkt_A']].to_numpy()
    mk_pick = mm[np.arange(n), lab]
    bands = [(0, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1.01)]
    part5 = []
    for lo, hi in bands:
        s = (conf_fin >= lo) & (conf_fin < hi)
        if s.sum() < 10:
            continue
        P(f"{lo:.1f}-{min(hi, 1):.1f}{'':<5}{s.sum():>6}{conf_fin[s].mean():>12.3f}{conf_raw[s].mean():>10.3f}"
          f"{np.nanmean(mk_pick[s]):>9.3f}{hit[s].mean():>10.3f}")
        part5.append({'band': [lo, hi], 'n': int(s.sum()), 'final': float(conf_fin[s].mean()),
                      'raw': float(conf_raw[s].mean()), 'market': float(np.nanmean(mk_pick[s])),
                      'hit': float(hit[s].mean())})
    payload['part5'] = part5

    text = "\n".join(lines) + "\n"
    print(text)
    base = write_report('heuristic_audit', payload, text)
    print(f"wrote {base}.{{json,txt}}")


if __name__ == '__main__':
    main()
