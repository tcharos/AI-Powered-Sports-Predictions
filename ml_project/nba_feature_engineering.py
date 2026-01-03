import json
import pandas as pd
import numpy as np
import os
from nba_utils import get_full_name

HISTORY_FILE = "data_sets/NBA/nba_history_stats.json"
OUTPUT_FILE = "data_sets/NBA/training_data.csv"

def load_data():
    if not os.path.exists(HISTORY_FILE):
        print(f"File not found: {HISTORY_FILE}")
        return None
    
    with open(HISTORY_FILE, 'r') as f:
        data = json.load(f)
    
    df = pd.DataFrame(data)
    return df

def process_data(df):
    # Convert dates
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    # Map Teams to Full Names
    df['home_team_full'] = df['home_team'].apply(get_full_name)
    df['away_team_full'] = df['away_team'].apply(get_full_name)
    
    # Convert scores to numeric
    df['home_score'] = pd.to_numeric(df['home_score'])
    df['away_score'] = pd.to_numeric(df['away_score'])
    df['total_points'] = df['home_score'] + df['away_score']
    
    # Determine Winner (1 for Home Win[1], 0 for Away Win[2])
    # Note: Traditional ML assumes Home Win = 1, Away Win = 0
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    
    return df

def calculate_rolling_stats(df, window=5):
    # We need to calculate stats PER TEAM before merging back
    # Transform to long format: Date, Team, Opponent, IsHome, PointsScored, PointsAllowed, Win
    
    home_df = df[['date', 'game_id', 'home_team_full', 'away_team_full', 'home_score', 'away_score', 'home_win']].copy()
    home_df.columns = ['date', 'game_id', 'team', 'opponent', 'points', 'points_allowed', 'win']
    home_df['is_home'] = 1
    
    away_df = df[['date', 'game_id', 'away_team_full', 'home_team_full', 'away_score', 'home_score', 'home_win']].copy()
    away_df.columns = ['date', 'game_id', 'team', 'opponent', 'points', 'points_allowed', 'home_win_flag']
    away_df['win'] = 1 - away_df['home_win_flag'] # Invert for Away team
    away_df['is_home'] = 0
    away_df = away_df.drop(columns=['home_win_flag'])
    
    team_stats = pd.concat([home_df, away_df]).sort_values(['team', 'date'])
    
    # Calculate Rolling Metrics
    team_stats[f'pts_last_{window}'] = team_stats.groupby('team')['points'].transform(lambda x: x.shift().rolling(window, min_periods=1).mean())
    team_stats[f'allowed_last_{window}'] = team_stats.groupby('team')['points_allowed'].transform(lambda x: x.shift().rolling(window, min_periods=1).mean())
    team_stats[f'win_pct_last_{window}'] = team_stats.groupby('team')['win'].transform(lambda x: x.shift().rolling(window, min_periods=1).mean())
    
    return team_stats

def main():
    print("Loading data...")
    df = load_data()
    if df is None: return

    print(f"Loaded {len(df)} games.")
    df = process_data(df)
    
    # Calculate Features for different windows
    print("Calculating rolling stats...")
    stats_l5 = calculate_rolling_stats(df, window=5)
    stats_l10 = calculate_rolling_stats(df, window=10)
    
    # Merge back to Main Match DataFrame
    # Note: We need to merge TWICE (once for Home Team, once for Away Team)
    
    # Prepare stats for merging - keep only keys + calculated cols
    cols_l5 = ['date', 'game_id', 'team', 'pts_last_5', 'allowed_last_5', 'win_pct_last_5']
    cols_l10 = ['date', 'game_id', 'team', 'pts_last_10', 'allowed_last_10', 'win_pct_last_10']
    
    # Merge L5
    df = df.merge(stats_l5[cols_l5], left_on=['date', 'game_id', 'home_team_full'], right_on=['date', 'game_id', 'team'], how='left')
    df = df.rename(columns={'pts_last_5': 'home_pts_l5', 'allowed_last_5': 'home_allowed_l5', 'win_pct_last_5': 'home_win_l5'})
    df = df.drop(columns=['team'])
    
    df = df.merge(stats_l5[cols_l5], left_on=['date', 'game_id', 'away_team_full'], right_on=['date', 'game_id', 'team'], how='left', suffixes=('', '_away'))
    df = df.rename(columns={'pts_last_5': 'away_pts_l5', 'allowed_last_5': 'away_allowed_l5', 'win_pct_last_5': 'away_win_l5'})
    df = df.drop(columns=['team'])

    # Merge L10
    df = df.merge(stats_l10[cols_l10], left_on=['date', 'game_id', 'home_team_full'], right_on=['date', 'game_id', 'team'], how='left')
    df = df.rename(columns={'pts_last_10': 'home_pts_l10', 'allowed_last_10': 'home_allowed_l10', 'win_pct_last_10': 'home_win_l10'})
    df = df.drop(columns=['team'])
    
    df = df.merge(stats_l10[cols_l10], left_on=['date', 'game_id', 'away_team_full'], right_on=['date', 'game_id', 'team'], how='left', suffixes=('', '_away'))
    df = df.rename(columns={'pts_last_10': 'away_pts_l10', 'allowed_last_10': 'away_allowed_l10', 'win_pct_last_10': 'away_win_l10'})
    df = df.drop(columns=['team'])
    
    # H2H (Head to Head) - Simple approach: Previous Matchup Winner
    # For simplicity in V1, we stick to Team Performance features
    
    # Drop rows with NaN (early season games) to ensure clean training
    before_drop = len(df)
    df = df.dropna()
    print(f"Dropped {before_drop - len(df)} rows due to missing history (start of datasets).")
    
    print(f"Saving {len(df)} training examples to {OUTPUT_FILE}")
    df.to_csv(OUTPUT_FILE, index=False)

if __name__ == "__main__":
    main()
