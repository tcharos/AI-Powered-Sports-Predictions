import json
import os
import sys
import argparse
import pandas as pd
import glob
import re
from rapidfuzz import process, fuzz

# Reach sports_config (lives under web_ui/) from this ml_project script.
# Both paths share the same betting_config.json so per-lane bankroll
# helpers live in one place — we just import them across packages.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'web_ui'))
from sports_config import update_bankroll, lane_bankrolls, sport_total, LANES

_SPORT = 'football'

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r') as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

def extract_date_from_filename(filename):
    # Extracts YYYY-MM-DD from string
    match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(filename))
    if match:
        return match.group(1)
    return None

def normalize(name):
    return name.lower().strip() if name else ""

def load_verification_csv(filepath):
    if not os.path.exists(filepath):
        return {}
    
    try:
        df = pd.read_csv(filepath)
        results_map = {}
        for _, row in df.iterrows():
            # CSV Cols: Home, Away, Score (e.g. 2-1)
            home = row.get('Home')
            score = row.get('Score')
            
            if home and score and isinstance(score, str) and '-' in score:
                parts = score.split('-')
                try:
                    h_score = int(parts[0])
                    a_score = int(parts[1])
                    results_map[normalize(home)] = {
                        'home_team': home, 
                        'home_score': h_score, 
                        'away_score': a_score
                    }
                except: pass
        return results_map
    except Exception as e:
        print(f"Error loading CSV {filepath}: {e}")
        return {}

def resolve_all_bets(bets_dir, results_file=None, verification_file=None, config_file="data_sets/betting_config.json"):
    """Settle OPEN bets across every bets_*.json in `bets_dir` against
    scraped results.

    Idempotent and partial-friendly:
    - Already-terminal bets (WON / LOST / VOID / CASHED_OUT) are skipped
      on re-runs. Bankroll credits don't fire twice.
    - OPEN bets whose match isn't in the results map STAY OPEN. The
      script can be re-run after late matches finish. Earlier behaviour
      auto-VOIDed these, which broke late-night cycles.
    - CASHED_OUT bets had their bankroll credited at cashout time
      (Phase 7) — settlement contributes their pnl to slip totals but
      skips the bankroll update.
    - A slip's top-level `status` becomes 'CLOSED' only when every bet
      reaches a terminal status.
    - Slip-level totals are recomputed from each bet's current state on
      every run (idempotent across multiple invocations).

    Bankroll updates use sports_config's per-lane API so lane
    bankrolls are kept accurate (the legacy single `current_bankroll`
    field is no longer touched).
    """
    print(f"Resolving all OPEN bets in {bets_dir}...")

    # 1. Load Results
    results_map = {}
    if results_file:
        results_list = load_json(results_file)
        if results_list:
            for res in results_list:
                h = res.get('home_team')
                if h:
                    results_map[normalize(h)] = res

    if verification_file:
        csv_map = load_verification_csv(verification_file)
        results_map.update(csv_map)

    if not results_map:
        print("No results loaded. Cannot resolve bets.")
        return

    # Extract target date from results file if available
    target_date = None
    if results_file:
        target_date = extract_date_from_filename(results_file)
        if target_date:
            print(f"Enforcing date check: Only resolving bets for {target_date}")

    result_keys = list(results_map.keys())

    # 2. Find Bets Files
    bet_files = glob.glob(os.path.join(bets_dir, "bets_*.json"))
    print(f"Found {len(bet_files)} bet slips.")

    grand_won = 0
    grand_lost = 0
    grand_pending = 0

    for b_file in bet_files:
        bets_data = load_json(b_file)
        if not bets_data:
            continue

        file_date = extract_date_from_filename(b_file)
        if target_date and file_date and file_date != target_date:
            continue

        bets = bets_data.get('bets', [])

        # Fast path: nothing to do if no OPEN bets remain.
        if bets and not any(b.get('status') == 'OPEN' for b in bets):
            continue

        # Phase A: settle newly-resolvable OPEN bets, credit lane bankrolls
        # for newly-WON bets only. Skip everything that's already terminal.
        won_now = 0
        lost_now = 0
        pending_now = 0
        for bet in bets:
            if not bet.get('status'):
                bet['status'] = 'OPEN'
            if bet.get('status') != 'OPEN':
                continue

            # Bet-level date check (defence in depth on top of file-level).
            if target_date and bet.get('date') and not str(bet.get('date')).startswith(target_date):
                continue

            home = bet.get('home')
            if not home and bet.get('match'):
                m_str = bet.get('match')
                if ' vs ' in m_str:
                    home = m_str.split(' vs ')[0]
                elif ' - ' in m_str:
                    home = m_str.split(' - ')[0]
            if not home:
                continue

            stake = float(bet.get('stake', bet.get('stake_units', 0)))
            odd = float(bet.get('odd', bet.get('odds', 1.0)))
            bet_type = bet.get('type')
            selection = bet.get('selection')

            # Result lookup: direct then fuzzy. If neither, LEAVE OPEN.
            norm_home = normalize(home)
            result_data = None
            if norm_home in results_map:
                result_data = results_map[norm_home]
            else:
                match = process.extractOne(norm_home, result_keys, scorer=fuzz.ratio)
                if match and match[1] >= 80:
                    result_data = results_map[match[0]]

            if result_data is None:
                pending_now += 1
                continue  # leave OPEN — next run will retry

            try:
                h_score = int(result_data['home_score'])
                a_score = int(result_data['away_score'])
            except (KeyError, ValueError, TypeError):
                pending_now += 1
                continue  # malformed result; leave OPEN

            bet['final_score'] = f"{h_score}-{a_score}"
            res_1x2 = "1" if h_score > a_score else ("2" if a_score > h_score else "X")
            bet['result_1x2'] = res_1x2
            total_goals = h_score + a_score
            res_ou = "OVER" if total_goals > 2.5 else "UNDER"
            bet['result_ou'] = res_ou

            won = False
            if bet_type == '1X2':
                sel = str(selection).upper()
                if sel in ('HOME', '1'): sel = '1'
                elif sel in ('AWAY', '2'): sel = '2'
                elif sel in ('DRAW', 'X'): sel = 'X'
                won = (sel == res_1x2)
            elif bet_type in ('O/U', 'OU2.5'):
                sel = str(selection).upper()
                if 'OVER' in sel: sel = 'OVER'
                elif 'UNDER' in sel: sel = 'UNDER'
                won = (sel == res_ou)

            lane = bet.get('lane', 'value')
            if lane not in LANES:
                lane = 'value'

            if won:
                bet['status'] = 'WON'
                bet['result'] = 'WON'
                profit = round((stake * odd) - stake, 2)
                bet['pnl'] = profit
                bet['profit'] = profit  # back-compat for older readers
                # Credit lane bankroll with the full payout (stake + profit).
                update_bankroll(_SPORT, stake * odd, lane=lane)
                won_now += 1
            else:
                bet['status'] = 'LOST'
                bet['result'] = 'LOST'
                bet['pnl'] = -stake
                bet['profit'] = -stake
                lost_now += 1

        # Phase B: recompute slip-level totals from ALL bets' current state.
        # Idempotent regardless of how many partial runs preceded this one.
        total_pnl = 0.0
        total_return = 0.0
        return_by_lane = {lane: 0.0 for lane in LANES}
        pnl_by_lane = {lane: 0.0 for lane in LANES}
        for bet in bets:
            status = bet.get('status', 'OPEN')
            if status == 'OPEN':
                continue
            lane = bet.get('lane', 'value')
            if lane not in LANES:
                lane = 'value'
            stake = float(bet.get('stake', bet.get('stake_units', 0)))
            if status == 'CASHED_OUT':
                amount = float(bet.get('cashout_amount', stake))
                pnl_v = float(bet.get('pnl', amount - stake))
                return_by_lane[lane] += amount
                pnl_by_lane[lane] += pnl_v
                total_return += amount
                total_pnl += pnl_v
            elif status == 'WON':
                odd = float(bet.get('odd', bet.get('odds', 1.0)))
                payout = stake * odd
                return_by_lane[lane] += payout
                pnl_by_lane[lane] += payout - stake
                total_return += payout
                total_pnl += payout - stake
            elif status == 'LOST':
                pnl_by_lane[lane] -= stake
                total_pnl -= stake
            elif status == 'VOID':
                return_by_lane[lane] += stake
                total_return += stake

        any_open = any(b.get('status') == 'OPEN' for b in bets)
        bets_data['bets'] = bets
        bets_data['pnl'] = round(total_pnl, 2)
        bets_data['total_return'] = round(total_return, 2)
        bets_data['return_by_lane'] = {k: round(v, 2) for k, v in return_by_lane.items()}
        bets_data['pnl_by_lane'] = {k: round(v, 2) for k, v in pnl_by_lane.items()}
        bets_data['settled'] = not any_open
        bets_data['status'] = 'OPEN' if any_open else 'CLOSED'
        save_json(b_file, bets_data)

        slip_tag = 'CLOSED' if not any_open else f"OPEN ({pending_now} pending)"
        print(f"  {os.path.basename(b_file)}: this run +{won_now} won, "
              f"+{lost_now} lost. Slip: {slip_tag}. Cumulative slip P/L: {total_pnl:.2f}")
        grand_won += won_now
        grand_lost += lost_now
        grand_pending += pending_now

    if grand_won + grand_lost == 0:
        print(f"No new resolutions across {len(bet_files)} slip(s). "
              f"({grand_pending} bet(s) still pending across all slips.)")
    else:
        lane_br = lane_bankrolls(_SPORT)
        total_br = sport_total(_SPORT)
        print(f"Settled this run: {grand_won} won, {grand_lost} lost, "
              f"{grand_pending} still pending. "
              f"Lane bankrolls: {lane_br}. Total: {total_br:.2f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bets_dir", default="output", help="Directory containing bets_*.json")
    parser.add_argument("--results", help="Path to results json")
    parser.add_argument("--verification_csv", help="Path to verification CSV")
    args = parser.parse_args()
    
    resolve_all_bets(args.bets_dir, args.results, args.verification_csv)
