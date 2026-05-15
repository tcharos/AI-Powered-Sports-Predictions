import pandas as pd
import json
import os
import argparse
from rapidfuzz import process, fuzz

ANALYTICS_FILE = "data_sets/NBA/nba_analytics.json"

def normalize(name):
    # Common NBA Cleanups
    if not name: return ""
    name = name.lower().strip()
    return name

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)

def evaluate_nba(date_str):
    preds_file = f"output_basketball/predictions_nba_{date_str}.csv"
    results_file = f"output_basketball/results_nba_{date_str}.json"
    
    if not os.path.exists(preds_file):
        print(f"Predictions file not found: {preds_file}")
        return
        
    if not os.path.exists(results_file):
        print(f"Results file not found: {results_file}")
        return
        
    print(f"Evaluating {preds_file} vs {results_file}...")
    
    df = pd.read_csv(preds_file)
    results = load_json(results_file)
    
    # Map Results by Home Team
    results_map = {}
    result_keys = []
    
    for r in results:
        h = r['home_team']
        results_map[normalize(h)] = r
        result_keys.append(normalize(h))
        
    correct_moneyline = 0
    correct_total = 0
    total_checked = 0
    
    verification_rows = []
    
    for _, row in df.iterrows():
        home = row['Home Team']
        away = row['Away Team']
        
        pred_winner = row.get('Prediction 1X2', '') # Expect "1" or "2"
        pred_total_pick = row.get('Prediction O/U', '') # Expect "Over" or "Under"
        market_total = float(row.get('Total Line', 0)) if row.get('Total Line') else 0
        
        # Match Result
        norm_h = normalize(home)
        match_res = None
        
        if norm_h in results_map:
            match_res = results_map[norm_h]
        else:
            m = process.extractOne(norm_h, result_keys, scorer=fuzz.ratio)
            if m and m[1] >= 70: # Lower threshold for "Knicks" vs "New York Knicks"
                match_res = results_map[m[0]]
            else:
                print(f"Could not find result for {home}")
                continue
                
        h_score = match_res['home_score']
        a_score = match_res['away_score']
        actual_total = h_score + a_score
        
        # Determine Winner
        if h_score > a_score:
            actual_winner = "1"
        else:
            actual_winner = "2"
            
        # Verify Moneyline
        is_correct_winner = False
        if str(pred_winner) == str(actual_winner):
            is_correct_winner = True
            
        # Verify Total
        actual_ou_outcome = "Over" if actual_total > market_total else "Under"
        # If market_total is 0/missing, skip total verification
        is_correct_total = False
        if market_total > 0:
            if pred_total_pick.lower() == actual_ou_outcome.lower():
                is_correct_total = True
            elif pred_total_pick.lower() == "pass":
                is_correct_total = None # Pass
        else:
            is_correct_total = None
            
        # Stats
        total_checked += 1
        if is_correct_winner: correct_moneyline += 1
        if is_correct_total is True: correct_total += 1
        
        # Row Data
        verification_rows.append({
            "Match": f"{home} vs {away}",
            "Score": f"{h_score}-{a_score}",
            "Pred Winner": pred_winner,
            "Actual Winner": actual_winner,
            "Correct Winner": is_correct_winner,
            "Line": market_total,
            "Pred Total": pred_total_pick,
            "Actual Total": actual_total,
            "Actual O/U": actual_ou_outcome,
            "Correct Total": is_correct_total
        })
        
    # Validation CSV output
    v_df = pd.DataFrame(verification_rows)
    v_file = f"output_basketball/verification_nba_{date_str}.csv"
    v_df.to_csv(v_file, index=False)
    print(f"Saved verification report to {v_file}")
    
    # Cumulative Stats Update
    analytics = load_json(ANALYTICS_FILE)
    if not analytics:
        analytics = {
            "total_matches": 0,
            "winner_correct": 0,
            "total_correct": 0,
            "total_predictions_made": 0 # Count only non-pass total picks
        }
        
    analytics["total_matches"] += total_checked
    analytics["winner_correct"] += correct_moneyline
    
    # Count valid total picks
    valid_total_picks = sum(1 for r in verification_rows if r['Correct Total'] is not None)
    analytics["total_predictions_made"] += valid_total_picks
    analytics["total_correct"] += correct_total
    
    # Rates
    if analytics["total_matches"] > 0:
        analytics["winner_accuracy"] = round((analytics["winner_correct"] / analytics["total_matches"]) * 100, 2)
    if analytics["total_predictions_made"] > 0:
        analytics["total_accuracy"] = round((analytics["total_correct"] / analytics["total_predictions_made"]) * 100, 2)
        
    save_json(ANALYTICS_FILE, analytics)
    print("Updated Cumulative NBA Stats.")
    print(json.dumps(analytics, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    
    evaluate_nba(args.date)
