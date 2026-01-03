import json
import pandas as pd
import numpy as np
import pickle
import os
import glob
from datetime import datetime
from nba_utils import get_full_name, get_abbr

# Constants
MATCH_FILE_PATTERN = "output_basketball/nba_matches_*_final.json"
STATS_DIR = "data_sets/NBA"
MODEL_WINNER = "models/nba_winner_model.pkl"
MODEL_TOTAL = "models/nba_total_model.pkl"
ODDS_FILE = "output_basketball/espn_odds.json"

def load_latest_matches():
    files = glob.glob(MATCH_FILE_PATTERN)
    if not files:
        print("No match files found.")
        return []
    # Get latest
    latest_file = max(files, key=os.path.getctime)
    print(f"Loading matches from {latest_file}...")
    with open(latest_file, 'r') as f:
        return json.load(f)

def load_stats_file(filename):
    path = os.path.join(STATS_DIR, filename)
    if not os.path.exists(path):
        print(f"Warning: Stats file not found: {path}")
        return {}
    with open(path, 'r') as f:
        return json.load(f)

def parse_stats_row(team_data):
    if not team_data or "raw_cells" not in team_data:
        return None
    cells = team_data["raw_cells"]
    if len(cells) < 6:
        return None
    try:
        gp = float(cells[2])
        wins = float(cells[3])
        score_str = cells[5]
        pts_for, pts_against = map(float, score_str.split(':'))
        return {
            "pts_avg": pts_for / gp if gp > 0 else 0,
            "allowed_avg": pts_against / gp if gp > 0 else 0,
            "win_pct": wins / gp if gp > 0 else 0
        }
    except Exception as e:
        return None

def get_team_features(team_name, stats_l5, stats_l10):
    feat_l5 = parse_stats_row(stats_l5.get(team_name))
    feat_l10 = parse_stats_row(stats_l10.get(team_name))
    
    if not feat_l5: return None
    if not feat_l10: feat_l10 = feat_l5
    
    return {
        "pts_l5": feat_l5["pts_avg"],
        "allowed_l5": feat_l5["allowed_avg"],
        "win_l5": feat_l5["win_pct"],
        "pts_l10": feat_l10["pts_avg"],
        "allowed_l10": feat_l10["allowed_avg"],
        "win_l10": feat_l10["win_pct"]
    }

def load_espn_odds():
    if not os.path.exists(ODDS_FILE):
        return {}
    with open(ODDS_FILE, 'r') as f:
        data = json.load(f)
    odds_map = {}
    for game in data:
        # Include Date in Key for uniqueness?
        # Or store list of games per team and filter by date later?
        # A team only plays once per day basically.
        # But we need to know the date of the odd.
        
        team = game["home_team"]
        date_header = game.get("date_header", "")
        
        if team not in odds_map:
            odds_map[team] = []
        odds_map[team].append(game)
        
    return odds_map

def parse_odds_line(raw_odds):
    parts = raw_odds.split('|')
    parts = [p.strip() for p in parts]
    market_total = None
    home_spread = None
    
    def get_val(s, key_char=''):
        try:
            val_str = s.split(' ')[0]
            if key_char: val_str = val_str.replace(key_char, '')
            return float(val_str)
        except: return None

    if len(parts) >= 5:
        if parts[1].startswith('o'): market_total = get_val(parts[1], 'o')
        elif parts[4].startswith('u'): market_total = get_val(parts[4], 'u')
        home_spread = get_val(parts[3])
        
    return market_total, home_spread

def match_odds_by_date(home_team, target_date_str, odds_map):
    # target_date_str: "2025-12-15"
    # odds_map: { "Team": [ {date_header: "Monday, December 15", ...}, ... ] }
    
    if home_team not in odds_map:
        return None
        
    games = odds_map[home_team]
    
    # Needs to match date.
    # Convert "2025-12-15" to "Monday, December 15" format?
    # Or strict check?
    try:
        dt = datetime.strptime(target_date_str, "%Y-%m-%d")
        # ESPN Header: "Monday, December 15"
        # Let's construct it.
        # Note: %A = Day Name, %B = Month Name, %d = Day (padded?) or %-d (unpadded)
        # Python strftime on non-padded days varies by OS.
        # ESPN uses "December 15" (space + number).
        # Let's try simple match.
        
        target_day = dt.strftime("%d").lstrip('0')
        target_month = dt.strftime("%B")
        
        for g in games:
            header = g.get("date_header", "")
            if target_month in header and f" {target_day}" in header:
                return g
                
    except:
        pass
        
    # Fallback: exact team match if only 1 game in list?
    if len(games) == 1:
        return games[0]
        
    return None

def main():
    try:
        with open(MODEL_WINNER, 'rb') as f: clf = pickle.load(f)
        with open(MODEL_TOTAL, 'rb') as f: reg = pickle.load(f)
    except Exception as e:
        print(f"Error loading models: {e}")
        return

    matches = load_latest_matches()
    stats_l5_overall = load_stats_file("form_last_5_overall.json")
    stats_l10_overall = load_stats_file("form_last_10_overall.json")
    espn_odds = load_espn_odds()
    
    predictions = []
    print(f"Generating predictions for {len(matches)} matches...")
    
    for m in matches:
        home = m['home_team']
        away = m['away_team']
        
        hf = get_team_features(home, stats_l5_overall, stats_l10_overall)
        af = get_team_features(away, stats_l5_overall, stats_l10_overall)
        
        if not hf or not af:
            print(f"Skipping {home} vs {away} - Missing Stats")
            continue
            
        X = [[
            hf['pts_l5'], hf['allowed_l5'], hf['win_l5'],
            af['pts_l5'], af['allowed_l5'], af['win_l5'],
            hf['pts_l10'], hf['allowed_l10'], hf['win_l10'],
            af['pts_l10'], af['allowed_l10'], af['win_l10']
        ]]
        
        win_prob = clf.predict_proba(X)[0][1]
        pred_total = reg.predict(X)[0]
        
        # Match with Odds
        market_total = "N/A"
        home_spread = "N/A"
        
        eff_total = None
        
        # New Date-Aware Lookup
        target_date = m.get('date', 'Tomorrow')
        match_data = match_odds_by_date(home, target_date, espn_odds)
        
        if match_data:
            mt, hs = parse_odds_line(match_data.get("raw_odds", ""))
            if mt: 
                market_total = mt
                eff_total = mt
            if hs is not None: 
                home_spread = hs
        
        ou_pick = "Pass"
        if eff_total:
            diff = pred_total - eff_total
            if diff > 3: ou_pick = "OVER"
            elif diff < -3: ou_pick = "UNDER"
        
        predictions.append({
            "Date": m.get('date', 'Tomorrow'),
            "Home Team": home,
            "Away Team": away,
            "Home Win %": round(win_prob * 100, 1),
            "Spread (Home)": home_spread,
            "Total (Market)": market_total,
            "Total (Model)": round(pred_total, 1),
            "O/U Pick": ou_pick,
            "Confidence": round(abs(win_prob - 0.5) * 2 * 100, 1)
        })
       # Output
    if predictions:
        output_dir = "output_basketball"
        os.makedirs(output_dir, exist_ok=True)
        
        # Determine date from first prediction (they should all be for same day usually)
        # If mixed dates, we pick the first one or most common. First one is safe for "Tomorrow" batches.
        file_date = predictions[0]['Date']
        
        # Sanitization: ensure YYYY-MM-DD
        try:
            datetime.strptime(file_date, '%Y-%m-%d')
        except:
            # Fallback if scraping gave weird date string
            file_date = datetime.now().strftime('%Y-%m-%d')

        out_df = pd.DataFrame(predictions)
        out_path = os.path.join(output_dir, f"predictions_nba_{file_date}.csv")
        out_df.to_csv(out_path, index=False)
        print(f"\n✅ Predictions saved to {out_path}")
        print(out_df.to_string())
    else:
        print("No predictions generated.")

if __name__ == "__main__":
    main()
