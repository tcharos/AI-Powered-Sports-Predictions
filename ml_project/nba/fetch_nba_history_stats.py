import os
import pbpstats
from pbpstats.client import Client
import pandas as pd
import time
import json

# Settings
# User requested 'fetch_nba_history_stats.json' as the target-like name
# We save to data_sets/NBA/nba_history_stats.json
DATA_DIR = os.path.join(os.getcwd(), "data_sets", "NBA")
SEASONS = ['2019-20', '2020-21', '2021-22', '2022-23', '2023-24', '2024-25']
SEASON_TYPE = "Regular Season"

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def fetch_season_stats():
    ensure_dir(DATA_DIR)
    
    # Initialize PBPStats Client
    # We use 'file' source to cache responses (if configured), but 'web' to fetch fresh
    cache_dir = os.path.join(DATA_DIR, "pbp_cache")
    ensure_dir(cache_dir)
    ensure_dir(os.path.join(cache_dir, "schedule"))
    ensure_dir(os.path.join(cache_dir, "games")) # or 'boxscore' depending on internals, let's play safe

    
    settings = {
        "dir": cache_dir,
        "Boxscore": {"source": "web", "data_provider": "stats_nba"},
        "Games": {"source": "web", "data_provider": "data_nba"},
    }
    
    client = Client(settings)
    
    all_games_data = []

    print(f"🏀 Initializing NBA Stats Fetcher...")
    print(f"📂 Output File: nba_history_stats.json")

    for season in SEASONS:
        print(f"\n📅 Processing Season: {season}")
        
        try:
            # 1. Get Schedule for the Season
            print(f"   > Fetching Schedule...")
            # Use the correct Client method for Season resource
            season_obj = client.Season("nba", season, SEASON_TYPE)
            print(f"DEBUG: Type: {type(season_obj)}")
            print(f"DEBUG: Dir: {dir(season_obj)}")
            
            count = 0
            # Access final games list directly
            games_list = season_obj.games.final_games
            total = len(games_list)
            print(f"   > Found {total} final games. Extracting details...")
            
            for game in games_list:
                try:
                    # Extract basic info (it is a dict)
                    row = {
                        "game_id": game['game_id'],
                        "date": game['date'],
                        "home_team": game['home_team_abbreviation'],
                        "away_team": game['away_team_abbreviation'],
                        "home_score": game['home_score'],
                        "away_score": game['away_score'],
                        "season": season
                    }
                    all_games_data.append(row)
                    
                    count += 1
                    # Reduced logging to avoid clutter
                    if count % 200 == 0:
                        print(f"     Processed {count}/{total} games...")
                        
                except Exception as e:
                    print(f"     ❌ Error processing game: {e}")
            
        except Exception as e:
            print(f"   ❌ Critical Error for season {season}: {e}")

    # Save Aggregate Data to JSON
    output_file = os.path.join(DATA_DIR, "nba_history_stats.json")
    try:
        with open(output_file, 'w') as f:
            json.dump(all_games_data, f, indent=4)
        print(f"\n🎉 Success! All data saved to: {output_file}")
        print(f"Total Games Processed: {len(all_games_data)}")
    except Exception as e:
        print(f"❌ Error saving JSON file: {e}")

if __name__ == "__main__":
    fetch_season_stats()
