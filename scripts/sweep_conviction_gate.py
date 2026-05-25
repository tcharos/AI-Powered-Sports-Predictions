"""Conviction-lane gate diagnostic — volume + observational ROI sweep.

Sweeps `conviction_min_confidence` across {0.45..0.65} at the current
`conviction_min_odds=1.40` floor, projecting (a) how many bets per
week would fire and (b) win-rate / flat-stake ROI on the subset
already covered by `output/verification_*.csv`.

Reads from `output/predictions_*.csv` + `output/history/predictions_*.csv`
and the matching verification files. No bookmaker interaction; no
betting_config.json mutation — output is read-only diagnostics so a
threshold change is a deliberate, separate edit.

Usage:
    PYTHONPATH=$(pwd):$(pwd)/ml_project python3 scripts/sweep_conviction_gate.py
"""

import csv
import glob
import os

THRESHOLDS = [0.45, 0.50, 0.55, 0.58, 0.60, 0.62, 0.65]
MIN_ODDS = 1.40
STAKE = 1.0  # flat per bet → ROI = pnl / staked


def load_predictions():
    files = sorted(
        glob.glob('output/predictions_*.csv') +
        glob.glob('output/history/predictions_*.csv'),
        key=lambda p: os.path.basename(p),
    )
    preds = {}
    for p in files:
        date = os.path.basename(p).replace('predictions_', '').replace('.csv', '')
        with open(p) as f:
            preds[date] = list(csv.DictReader(f))
    return preds


def load_verification():
    files = sorted(
        glob.glob('output/verification_*.csv') +
        glob.glob('output/history/verification_*.csv'),
        key=lambda p: os.path.basename(p),
    )
    verif = {}
    for v in files:
        date = os.path.basename(v).replace('verification_', '').replace('.csv', '')
        m = {}
        with open(v) as f:
            for row in csv.DictReader(f):
                m[row['Home'].lower().strip()] = row
        verif[date] = m
    return verif


def sweep(preds, verif, market):
    odd_key  = 'Prediction 1X2 Odd' if market == '1X2' else 'Prediction O/U Odd'
    conf_key = 'Conf 1X2'             if market == '1X2' else 'Conf O/U'
    correct_key = 'Correct 1X2'       if market == '1X2' else 'Correct O/U'

    print(f"=== {market} (odds floor {MIN_ODDS}) ===")
    print(f"{'Conf>=':>6} | {'Qual':>4} | {'Vrf':>4} | {'W':>3} | {'L':>3} | "
          f"{'Win%':>5} | {'AvgO':>5} | {'PnL':>7} | {'ROI%':>7}")
    print("-" * 70)
    for thr in THRESHOLDS:
        q = d = w = l = 0
        pnl = 0.0
        so = 0.0
        on = 0
        for date, rows in preds.items():
            v_today = verif.get(date, {})
            for row in rows:
                try:
                    c = float(row[conf_key])
                    o = float(row[odd_key])
                except (ValueError, KeyError):
                    continue
                if c < thr or o < MIN_ODDS:
                    continue
                q += 1
                so += o
                on += 1
                hk = row['Home Team'].lower().strip()
                if hk not in v_today:
                    continue
                v = v_today[hk]
                if v.get(correct_key, '').lower() == 'true':
                    d += 1
                    w += 1
                    pnl += (o - 1)
                elif v.get(correct_key, '').lower() == 'false':
                    d += 1
                    l += 1
                    pnl -= 1
        wr = w / d * 100 if d else 0
        ao = so / on if on else 0
        roi = pnl / d * 100 if d else 0
        print(f"{thr:>6.2f} | {q:>4} | {d:>4} | {w:>3} | {l:>3} | "
              f"{wr:>4.1f}% | {ao:>5.2f} | {pnl:>+7.2f} | {roi:>+7.2f}")
    print()


def volume_projection(preds):
    days = len(preds)
    print(f"=== Combined volume projection (1X2 + O/U), {days}-day window ===")
    print(f"{'Conf>=':>6} | {'1X2':>4} | {'O/U':>4} | {'Total':>5} | "
          f"{'Per day':>8} | {'Per week':>9}")
    for thr in THRESHOLDS:
        q1 = q2 = 0
        for _, rows in preds.items():
            for row in rows:
                try:
                    if (float(row['Conf 1X2']) >= thr
                            and float(row['Prediction 1X2 Odd']) >= MIN_ODDS):
                        q1 += 1
                    if (float(row['Conf O/U']) >= thr
                            and float(row['Prediction O/U Odd']) >= MIN_ODDS):
                        q2 += 1
                except (ValueError, KeyError):
                    continue
        per_day = (q1 + q2) / days if days else 0
        print(f"{thr:>6.2f} | {q1:>4} | {q2:>4} | {q1 + q2:>5} | "
              f"{per_day:>8.1f} | {per_day * 7:>9.1f}")
    print()


def odds_floor_distribution(preds):
    """Why the current 0.65 gate starves 1X2: heavy favourites cluster
    above the Conf threshold but BELOW the odds floor."""
    print("=== 1X2: Conf vs odds-floor pass rate ===")
    bands = [(0.40, 0.45), (0.45, 0.50), (0.50, 0.55),
             (0.55, 0.60), (0.60, 0.65), (0.65, 1.01)]
    points = []
    for _, rows in preds.items():
        for row in rows:
            try:
                points.append((float(row['Conf 1X2']),
                               float(row['Prediction 1X2 Odd'])))
            except (ValueError, KeyError):
                continue
    for lo, hi in bands:
        n = pass_n = 0
        for c, o in points:
            if lo <= c < hi:
                n += 1
                if o >= MIN_ODDS:
                    pass_n += 1
        pct = pass_n / n * 100 if n else 0
        print(f"  Conf {lo:.2f}-{hi:.2f}: n={n:>4}, "
              f"pass odds>={MIN_ODDS}: {pass_n:>3} ({pct:>5.1f}%)")


def main():
    preds = load_predictions()
    verif = load_verification()
    if not preds:
        print("No predictions found under output/ or output/history/.")
        return 1
    print(f"Pred days:  {len(preds)}  ({min(preds)} -> {max(preds)})")
    print(f"Verif days: {len(verif)}  "
          f"({min(verif) if verif else '-'} -> {max(verif) if verif else '-'})")
    print()
    sweep(preds, verif, '1X2')
    sweep(preds, verif, 'O/U')
    volume_projection(preds)
    odds_floor_distribution(preds)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
