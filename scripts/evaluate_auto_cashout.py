"""Evaluate the auto-cashout decisions against the COUNTERFACTUAL of
holding each bet to the final whistle.

A cashout decision can only be judged in hindsight by asking: *what would
I have got if I'd held instead?* We have both halves:
  - what we GOT   = the cashout amount (logged in output/auto_cashout_log.jsonl)
  - what we'd GET = the bet's settlement outcome (from output/verification_*.csv):
                    stake × odds if the selection actually won, else 0.

So per executed auto-cashout:
    held_return = stake × odds   if selection won at full-time, else 0
    cash_return = cashout amount
    delta       = cash_return − held_return     (>0 ⇒ cashing beat holding)

The headline is the AGGREGATE Σcash − Σheld: positive ⇒ the rule made more
than holding everything to settlement; negative ⇒ it cost value (the price
paid for variance reduction wasn't repaid by the losses it dodged). A single
bet beaten by holding isn't a "wrong" decision — only the aggregate over many
decisions is meaningful (variance). Intuition split:
  - stop_loss "saves"  = cash banked on bets that went on to LOSE (vs 0)
  - lock_in  "gives up" = profit forgone on bets that went on to WIN
                          (held_return − cash_return)

CAVEAT: cash_return is the SYNTHETIC fair-value estimate (no real bookmaker
haircut), so Σcash is optimistic vs what a real cashout would have paid.

Read-only. Joins the audit log to verification CSVs by (date, match).
Writes output/auto_cashout_eval.json + prints a report.

Usage:
    python3 scripts/evaluate_auto_cashout.py
"""

import csv
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, 'output')
LOG_PATH = os.path.join(OUTPUT_DIR, 'auto_cashout_log.jsonl')


def _norm(s):
    return ' '.join((s or '').strip().lower().split())


def _load_verifications():
    """{date: {normalized_match: row}} from all verification CSVs."""
    idx = {}
    files = (glob.glob(os.path.join(OUTPUT_DIR, 'verification_*.csv')) +
             glob.glob(os.path.join(OUTPUT_DIR, 'history', 'verification_*.csv')))
    for f in files:
        base = os.path.basename(f)
        # verification_YYYY-MM-DD.csv  (ignore any .timestamp suffix)
        date = base.replace('verification_', '')[:10]
        try:
            with open(f) as fh:
                for r in csv.DictReader(fh):
                    m = _norm(r.get('Match'))
                    if m:
                        idx.setdefault(date, {})[m] = r
                        # also index by "home vs away" as a fallback key
                        ha = _norm(f"{r.get('Home')} vs {r.get('Away')}")
                        idx[date].setdefault(ha, r)
        except OSError:
            continue
    return idx


def _selection_won(bet_type, selection, row):
    """Did this selection win, per the verification row? Returns
    True/False, or None if the row can't decide (missing column)."""
    sel = (selection or '').strip()
    if bet_type == 'O/U' or 'Over' in sel or 'Under' in sel:
        actual = (row.get('Actual O/U') or '').strip()
        if not actual:
            return None
        want = 'Over 2.5' if 'Over' in sel else 'Under 2.5'
        return actual == want
    # 1X2
    actual = (row.get('Actual 1X2') or '').strip()
    if not actual:
        return None
    canon = {'1': '1', 'home': '1', 'x': 'X', 'X': 'X', 'draw': 'X',
             '2': '2', 'away': '2'}.get(sel, sel)
    return actual == canon


def main():
    if not os.path.exists(LOG_PATH):
        print(f"No audit log at {LOG_PATH} — nothing auto-cashed yet.")
        return 0

    # Executed cashouts, deduped by bet_id (a bet flips to CASHED_OUT after
    # the first firing, so later sweeps skip it; dedupe defensively anyway).
    fired = {}
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get('executed') and e.get('bet_id') and e['bet_id'] not in fired:
                fired[e['bet_id']] = e

    if not fired:
        print("Audit log has no EXECUTED cashouts yet (only held evaluations).")
        return 0

    verifs = _load_verifications()
    scored, pending = [], []
    for bid, e in fired.items():
        date = bid.split(':', 1)[0] if ':' in bid else ''
        row = (verifs.get(date) or {}).get(_norm(e.get('match')))
        if row is None:
            pending.append(e)
            continue
        won = _selection_won(e.get('type'), e.get('selection'), row)
        if won is None:
            pending.append(e)
            continue
        stake = float(e.get('stake') or 0)
        odds = float(e.get('odds') or 0)
        cash = float(e.get('amount') or 0)
        held = stake * odds if won else 0.0
        scored.append({**e, 'won': won, 'score': row.get('Score'),
                       'held_return': round(held, 2), 'cash_return': round(cash, 2),
                       'delta': round(cash - held, 2)})

    # Aggregate.
    def agg(items):
        cash = sum(x['cash_return'] for x in items)
        held = sum(x['held_return'] for x in items)
        return {'n': len(items), 'cash': round(cash, 2), 'held': round(held, 2),
                'net_delta': round(cash - held, 2)}

    overall = agg(scored)
    by_dec = {d: agg([x for x in scored if x['decision'] == d])
              for d in ('lock_in', 'stop_loss') if any(x['decision'] == d for x in scored)}
    saved = round(sum(x['cash_return'] for x in scored if not x['won']), 2)
    given_up = round(sum(x['held_return'] - x['cash_return'] for x in scored if x['won']), 2)
    would_win = sum(1 for x in scored if x['won'])

    report = {
        'cashouts_executed': len(fired), 'scored': len(scored), 'pending_settlement': len(pending),
        'overall': overall, 'by_decision': by_dec,
        'stop_loss_saved_vs_losing': saved,
        'lock_in_given_up_vs_winning': given_up,
        'cashed_bets_that_would_have_won': would_win,
        'caveat': 'cash_return is the SYNTHETIC estimate (no real haircut) → Σcash optimistic.',
        'details': sorted(scored, key=lambda x: x['delta']),
    }
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, 'auto_cashout_eval.json'), 'w') as f:
        json.dump(report, f, indent=2)

    # Console report.
    print("=== Auto-cashout decision evaluation (cash vs hold-to-settlement) ===")
    print(f"executed cashouts: {len(fired)}   scored: {len(scored)}   "
          f"pending settlement: {len(pending)}")
    if scored:
        o = overall
        verdict = ("auto-cashout BEAT holding" if o['net_delta'] > 0 else
                   "auto-cashout COST vs holding" if o['net_delta'] < 0 else "even")
        print(f"\nAggregate over {o['n']} scored cashouts:")
        print(f"  Σ cashed  = €{o['cash']:.2f}")
        print(f"  Σ if held = €{o['held']:.2f}")
        print(f"  net Δ     = €{o['net_delta']:+.2f}   → {verdict}")
        print(f"  ({would_win}/{o['n']} cashed bets would have WON if held)")
        print(f"\n  stop_loss saved (on bets that went on to lose): €{saved:+.2f}")
        print(f"  lock_in gave up (on bets that went on to win):  €{-given_up:+.2f}")
        for d, a in by_dec.items():
            print(f"  [{d:<9}] n={a['n']}  cashed=€{a['cash']:.2f}  "
                  f"held=€{a['held']:.2f}  netΔ=€{a['net_delta']:+.2f}")
        print("\n  Worst 5 decisions (held would've beaten cashing):")
        for x in report['details'][:5]:
            if x['delta'] >= 0:
                break
            print(f"    {x['match'][:34]:<34} {x['decision']:<9} {x['selection']:<9} "
                  f"score={x['score']} cash=€{x['cash_return']:.2f} held=€{x['held_return']:.2f} "
                  f"Δ€{x['delta']:+.2f}")
    if pending:
        print(f"\n  {len(pending)} cashout(s) await settlement (no verification row yet) "
              f"— re-run after the next verification.")
    print(f"\nReport → {os.path.join(OUTPUT_DIR, 'auto_cashout_eval.json')}")
    print("Caveat: cash_return is the synthetic estimate (no real haircut) → Σcash optimistic.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
