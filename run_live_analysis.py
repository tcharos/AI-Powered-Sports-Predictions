import pandas as pd
import json
import os
import datetime
import subprocess
import sys
from thefuzz import process, fuzz
from ml_project.live_adjuster import LiveAdjuster

# Config
OUTPUT_DIR = "output"
TODAY = datetime.datetime.now().strftime('%d.%m.%Y') # 10.12.2025 match format
TODAY_FILE_DATE = datetime.datetime.now().strftime('%Y-%m-%d')
PREDICTIONS_FILE = os.path.join(OUTPUT_DIR, f"predictions_{TODAY_FILE_DATE}.csv")
LIVE_OUTPUT = os.path.join(OUTPUT_DIR, "live_data.json")

def main():
    if not os.path.exists(PREDICTIONS_FILE):
        print(f"No predictions file found for {TODAY_FILE_DATE}")
        # Clear live data to avoid showing old data
        with open(LIVE_OUTPUT, 'w') as f:
            json.dump([], f)
        return

    print("Loading predictions...")
    try:
        df = pd.read_csv(PREDICTIONS_FILE)
    except Exception as e:
        print(f"Error reading predictions: {e}")
        return

    # Step 1: Scrape List of Currently Live Matches
    print("Scraping list of LIVE matches from Flashscore...")
    cmd_list = [
        "venv/bin/python", "-m", "scrapy", "crawl", "flashscore",
        "-a", "live_list=true",
        "-O", "output/live_list.json",
        "--nolog"
    ]
    try:
        subprocess.run(cmd_list, check=True)
    except Exception as e:
        print(f"Error scraping live list: {e}")
        return
        
    if not os.path.exists("output/live_list.json"):
        print("No live list scraped.")
        return
        
    with open("output/live_list.json", 'r') as f:
        try:
            live_matches_raw = json.load(f)
        except:
            live_matches_raw = []
            
    if not live_matches_raw:
        msg = "No live matches found on Flashscore." # Or use the requested message if preferred, but strictly this means NONE are live.
        # User requested: "No live matches we have predictions for found on Flashscore"
        # If there are NO live matches, then obviously we have no predictions for them.
        # So I will use the requested message for consistency, or a clear variant.
        # Let's use the requested one to satisfy the user strictly.
        msg = "No live matches we have predictions for found on Flashscore" 
        print(msg)
        with open(LIVE_OUTPUT, 'w') as f:
             json.dump([{'message': msg}], f)
        return

    print(f"Found {len(live_matches_raw)} live matches on Flashscore.")

    # Step 2: Crosscheck with Predictions
    # We want to find matches in 'df' that correspond to 'live_matches_raw'
    # Use Home Team Name for matching
    
    predicted_teams = df['Home Team'].tolist()
    live_pairs = []
    
    for m in live_matches_raw:
        h_team = m['home_team']
        # Fuzzy match
        match, score = process.extractOne(h_team, predicted_teams, scorer=fuzz.token_sort_ratio)
        if score > 80: # Threshold
            # Found a candidate
            # Verify Away team too?
            row = df[df['Home Team'] == match].iloc[0]
            a_team_pred = row['Away Team']
            
            # Simple check on away team
            if fuzz.token_sort_ratio(m['away_team'], a_team_pred) > 70:
                print(f"MATCH FOUND: {h_team} vs {m['away_team']} (ID: {m['match_id']})")
                live_pairs.append((m, row))
                
    if not live_pairs:
        msg = "No live matches we have predictions for found on Flashscore"
        print(msg)
        with open(LIVE_OUTPUT, 'w') as f:
            json.dump([{'message': msg}], f)
        return
        
    print(f"Processing {len(live_pairs)} active matches...")
    
    # Optimize: Batch Scrape ALL IDs at once
    active_ids = [m['match_id'] for m, _ in live_pairs]
    ids_str = ",".join(active_ids)
    
    # Store minimal lookup map for stats association
    match_lookup = {m['match_id']: (m, row) for m, row in live_pairs}

    print(f"Fetching stats for {len(active_ids)} matches in BATCH mode...")
    
    # Single Scrapy Call
    cmd_batch = [
        "venv/bin/python", "-m", "scrapy", "crawl", "flashscore",
        "-a", f"live_ids={ids_str}",
        "-O", "output/live_stats_batch.json",
        "--nolog"
    ]
    
    try:
        subprocess.run(cmd_batch, check=True)
    except Exception as e:
        print(f"Error in batch scrape: {e}")
        # Continue to process whatever we have (or empty)

    # Process Batch Output
    final_results = []
    adjuster = LiveAdjuster()
    
    if os.path.exists("output/live_stats_batch.json"):
        try:
            with open("output/live_stats_batch.json", 'r') as f:
                batch_data = json.load(f)
        except:
             batch_data = []
    else:
        batch_data = []

    # Map results back to predictions
    # Note: batch_data contains {'match_id': ..., 'stats': ...}
    
    # Create a set of processed IDs to track misses if needed
    processed_ids = set()

    for item in batch_data:
        m_id = item.get('match_id')
        if m_id not in match_lookup: continue
        
        processed_ids.add(m_id)
        live_meta, pred_row = match_lookup[m_id]
        
        try:
              pre_probs = {
                'home': float(pred_row['Home Win %']),
                'draw': float(pred_row['Draw %']),
                'away': float(pred_row['Away Win %'])
              }
        except:
             pre_probs = {'home':0.33, 'draw':0.33, 'away':0.33}
             
        adjusted = adjuster.adjust_probabilities(
            pre_probs,
            item.get('stats', {}),
            item.get('minute', 0),
            item.get('score', '0-0')
        )
        
        final_results.append({
            'match': f"{live_meta['home_team']} vs {live_meta['away_team']}",
            'score': item.get('score', '0-0'),
            'minute': item.get('minute', 0),
            'stats': item.get('stats', {}),
            'pre_probs': pre_probs,
            'adj_probs': adjusted
        })

    # Add matches that failed to scrape (keep them in list with old data or error?)
    # For now, only show successfully scraped ones to avoid stale data confusion.
    # Or show "Waiting for data..."? 
    # Let's keep it clean: only update live_data.json with fresh results.
        
    with open(LIVE_OUTPUT, 'w') as f:
        json.dump(final_results, f, indent=2)
        
    print(f"Updated live data for {len(final_results)} matches.")

if __name__ == "__main__":
    main()
