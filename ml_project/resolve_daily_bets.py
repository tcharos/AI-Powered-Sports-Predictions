import json
import os
import argparse
import pandas as pd
import glob
from rapidfuzz import process, fuzz

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r') as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

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

    # Load Config (Bankroll)
    config = load_json(config_file)
    if not config:
        config = {"current_bankroll": 1000.0}
    current_bankroll = config.get('current_bankroll', 1000.0)
    
    result_keys = list(results_map.keys())
    
    # 2. Find Bets Files
    bet_files = glob.glob(os.path.join(bets_dir, "bets_*.json"))
    print(f"Found {len(bet_files)} bet slips.")
    
    total_settled_count = 0
    total_pnl_impact = 0.0
    
    for b_file in bet_files:
        bets_data = load_json(b_file)
        if not bets_data: continue
        
        # Skip if already fully settled
        if bets_data.get('settled', False):
            continue
            
        bets = bets_data.get('bets', [])
        file_updated = False
        file_pnl = 0.0
        
        for bet in bets:
            # Default status to OPEN if missing
            if not bet.get('status'):
                bet['status'] = 'OPEN'
            
            if bet.get('status') != 'OPEN':
                continue
                
            home = bet.get('home')
            bet_type = bet.get('type')
            selection = bet.get('selection')
            
            # Key Mapping / Normalization
            stake = float(bet.get('stake', bet.get('stake_units', 0)))
            odd = float(bet.get('odd', bet.get('odds', 1.0)))
            
            home = bet.get('home')
            match_id = bet.get('match_id')

            # Parse 'match' string if 'home' is missing
            if not home and bet.get('match'):
                m_str = bet.get('match')
                if ' vs ' in m_str:
                    home = m_str.split(' vs ')[0]
                elif ' - ' in m_str:
                     home = m_str.split(' - ')[0]
            
            if not home:
                continue
            
            # Find Result
            norm_home = normalize(home)
            result_data = None
            
            # Prioritize clean ID match if available
            # (Assuming results_map might eventually support ID indexing, but for now mostly name)
            
            if norm_home in results_map:
                result_data = results_map[norm_home]
            else:
                match = process.extractOne(norm_home, result_keys, scorer=fuzz.ratio)
                if match and match[1] >= 80:
                    result_data = results_map[match[0]]
            
                # Result not found -> Mark as VOID (Neutral)
                # This assumes relevant results file is complete. Mismatches will be voided.
                bet['status'] = 'VOID'
                bet['result'] = 'VOID'
                bet['profit'] = 0.0 # Return stake logic (net change 0 relative to wallet deduction? No wait.)
                # If deducted on OPEN, then VOID means return STAKE.
                # Profit field usually means PnL.
                # If WON: profit = (stake*odd) - stake.
                # If LOST: profit = -stake.
                # If VOID: profit = 0. (You get stake back, no win, no loss).
                
                # Bankroll impact:
                # WON: bankroll += stake + profit (total return)
                # LOST: bankroll += 0
                # VOID: bankroll += stake
                
                # But here we handle bankroll at line 190 and 196.
                # Let's align with that logic below or handle it here.
                # Since we are modifying the flow, we should probably set variables and let downstream handle it,
                # BUT the downstream logic (lines 145+) assumes we have scores.
                # So we must handle VOID here and `continue` or skip scoring logic.
                
                current_bankroll += stake
                file_pnl += 0.0 # No PnL change for file
                file_updated = True
                total_settled_count += 1
                
                continue 

                
            # Check Scores
            try:
                h_score = int(result_data['home_score'])
                a_score = int(result_data['away_score'])
            except: continue
            
            # Outcome
            bet['final_score'] = f"{h_score}-{a_score}"
            
            # Calculate and store 1X2 result
            res_1x2 = "X"
            if h_score > a_score: res_1x2 = "1"
            elif a_score > h_score: res_1x2 = "2"
            bet['result_1x2'] = res_1x2

            # Calculate and store OU result
            total_goals = h_score + a_score
            res_ou = "OVER" if total_goals > 2.5 else "UNDER"
            bet['result_ou'] = res_ou

            won = False
            if bet_type == '1X2':
                outcome = res_1x2
                
                sel = str(selection).upper()
                if sel in ["HOME", "1"]: sel = "1"
                elif sel in ["AWAY", "2"]: sel = "2"
                elif sel in ["DRAW", "X"]: sel = "X"
                if sel == outcome: won = True
                
            elif bet_type == 'O/U' or bet_type == 'OU2.5':
                outcome = res_ou
                sel = str(selection).upper()
                if "OVER" in sel: sel = "OVER"
                elif "UNDER" in sel: sel = "UNDER"
                elif "2.5" in sel: 
                     if "OVER" in sel: sel = "OVER"
                     elif "UNDER" in sel: sel = "UNDER"
                if sel == outcome: won = True
            
            # Update
            if won:
                bet['status'] = 'WON'
                bet['result'] = 'WON'
                profit = (stake * odd) - stake
                bet['profit'] = round(profit, 2)
                current_bankroll += (stake + profit)
                file_pnl += profit
            else:
                bet['status'] = 'LOST'
                bet['result'] = 'LOST'
                bet['profit'] = -stake
                file_pnl -= stake
            
            file_updated = True
            total_settled_count += 1
            total_pnl_impact += (bet['profit'] if won else -stake) # wait, profit logic above is inconsistent. 
            # if WON: profit = (stake*odd)-stake. total_pnl += profit.
            # if LOST: profit = -stake. total_pnl += (-stake).
            # Correct.
            
            total_pnl_impact += (file_pnl) # No, accumulate carefully or just sum at the end.
            # Let's just track file_pnl properly
            
        all_bets_closed = all(b.get('status') != 'OPEN' for b in bets)
        
        # Save if actual updates occurred OR if we need to close the file (cleanup)
        if file_updated or (not bets_data.get('settled') and all_bets_closed):
            bets_data['bets'] = bets
            bets_data['pnl'] = bets_data.get('pnl', 0) + file_pnl
            
            if all_bets_closed:
                bets_data['settled'] = True
                bets_data['status'] = 'CLOSED'
                
            save_json(b_file, bets_data)
            if file_updated:
                print(f"Updated {b_file}: PnL {file_pnl:.2f}")
            else:
                print(f"Closed {b_file} with no new updates.")

    # Update Bankroll Once
    if total_settled_count > 0:
        config['current_bankroll'] = current_bankroll
        save_json(config_file, config)
        print(f"Total Settled: {total_settled_count}. New Bankroll: {current_bankroll:.2f}")
    else:
        print("No matches resolved across any open slips.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bets_dir", default="output", help="Directory containing bets_*.json")
    parser.add_argument("--results", help="Path to results json")
    parser.add_argument("--verification_csv", help="Path to verification CSV")
    args = parser.parse_args()
    
    resolve_all_bets(args.bets_dir, args.results, args.verification_csv)
