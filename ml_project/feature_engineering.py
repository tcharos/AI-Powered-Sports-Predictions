import pandas as pd
import numpy as np
from elo_engine import EloTracker
import warnings

from rapidfuzz import process, fuzz

# Suppress FutureWarning for GroupBy (Pandas 2.1+ transition)
warnings.simplefilter(action='ignore', category=FutureWarning)

class FeatureEngineer:
    def __init__(self):
        pass

    def calculate_features_from_h2h(self, last_matches: list, target_team: str, window: int = 5, venue_filter: str = None) -> dict:
        """
        Calculates rolling features (Pts, GF, GA, O/U rate) from a raw list of H2H matches.
        venue_filter: None (All), 'home' (Only Home games), 'away' (Only Away games)
        """
        if not last_matches:
            return {
                'form_pts': 0, 'form_gf': 0, 'form_ga': 0, 'form_ou': 0
            }
            
        pts = []
        gf = []
        ga = []
        ou = []
        results_list = []
        
        # Sort by date descending? usually scraper gives most recent first.
        # We take top 'window' matches THAT MATCH THE FILTER
        
        count = 0
        for m in last_matches:
            if count >= window:
                break
                
            try:
                # Score format "2-1" or "2 - 1"
                s = m.get('score', '0-0').replace(' ', '')
                if '-' not in s: 
                    continue
                h_score, a_score = map(int, s.split('-')[:2])
                
                # Determine if target is home or away
                # Use fuzzy matching if exact match fails
                # Simple check first
                is_home_game = False
                if m.get('home_team') == target_team:
                    is_home_game = True
                elif m.get('away_team') == target_team:
                    is_home_game = False
                else:
                    # Fuzzy match
                    choices = [m.get('home_team'), m.get('away_team')]
                    best, score, _ = process.extractOne(target_team, choices, scorer=fuzz.ratio)
                    if score > 70:
                        is_home_game = (best == m.get('home_team'))
                    else:
                        continue # Skip if uncertain
                
                # Apply Venue Filter
                if venue_filter == 'home' and not is_home_game:
                    continue
                if venue_filter == 'away' and is_home_game:
                    continue
                        
                # Stats
                my_goals = h_score if is_home_game else a_score
                opp_goals = a_score if is_home_game else h_score
                
                total_goals = h_score + a_score
                
                # Points
                if my_goals > opp_goals: 
                    p = 3
                    res_char = 'W'
                elif my_goals == opp_goals: 
                    p = 1
                    res_char = 'D'
                else: 
                    p = 0
                    res_char = 'L'
                
                pts.append(p)
                gf.append(my_goals)
                ga.append(opp_goals)
                ou.append(1 if total_goals > 2.5 else 0)
                results_list.append(res_char)
                
                count += 1
                
            except Exception as e:
                continue

        if not pts:
            return {'form_pts': 0, 'form_gf': 0, 'form_ga': 0, 'form_ou': 0, 'form_str': ''}

        return {
            'form_pts': np.mean(pts),
            'form_gf': np.mean(gf),
            'form_ga': np.mean(ga),
            'form_ou': np.mean(ou),
            'form_str': ",".join(results_list)
        }


    def add_elo_features(self, df: pd.DataFrame) -> pd.DataFrame:
        # Assumes H_elo and A_elo already exist from prepare_data
        if 'H_elo' in df.columns and 'A_elo' in df.columns:
            df['elo_diff'] = df['H_elo'] - df['A_elo']
        return df

    def add_league_encoding(self, df: pd.DataFrame) -> pd.DataFrame:
        if 'league' in df.columns:
            df['league_cat'] = df['league'].astype('category')
        return df

    def add_implied_probabilities(self, df: pd.DataFrame) -> pd.DataFrame:
        """Adds 1/Odds features (Implied Probability)."""
        # Ensure numeric (handled in loader, but safety check)
        for col in ['B365H', 'B365D', 'B365A']:
            if col in df.columns:
                # Avoid division by zero
                df[f'IP_{col[-1]}'] = 1.0 / df[col].replace(0, np.nan)
        return df

    def add_rolling_features(self, df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
        """
        Adds rolling form features (Points, Goals Scored, Goals Conceded)
        for both Home and Away teams based on their last `window` games.
        ALSO adds PPG (season-to-date) and Relative Strength features.
        """
        df = df.sort_values('date').copy()
        
        # 0. Implied Probabilities
        df = self.add_implied_probabilities(df)
        
        # 1. Last 5 Games features (Standard)
        df = self._calculate_rolling(df, window=5, suffix="")

        # 2. League Encoding
        df = self.add_league_encoding(df)
        
        # 3. Specific Home/Away Form
        df = self._calculate_specific_home_away(df)
        
        return df

    def _calculate_specific_home_away(self, df):
        # Re-implementation of specific form logic
        teams = pd.concat([df['home_team'], df['away_team']]).unique()
        
        h_stats = {'H_home_pts': [], 'H_home_gf': [], 'H_home_ga': [], 'H_home_sf': [], 'H_home_sa': []}
        a_stats = {'A_away_pts': [], 'A_away_gf': [], 'A_away_ga': [], 'A_away_sf': [], 'A_away_sa': []}
        
        team_home_matches = {}
        team_away_matches = {}
        
        for team in teams:
            team_home_matches[team] = df[df['home_team'] == team].sort_values('date')
            team_away_matches[team] = df[df['away_team'] == team].sort_values('date')
            
        for idx, row in df.iterrows():
            date = row['date']
            home = row['home_team']
            away = row['away_team']
            
            # Home Team at Home (Last 5)
            h_hist = team_home_matches[home][team_home_matches[home]['date'] < date].tail(5)
            stats = self._get_stats_from_history(h_hist, home)
            h_stats['H_home_pts'].append(stats['form_pts'])
            h_stats['H_home_gf'].append(stats['form_gf'])
            h_stats['H_home_ga'].append(stats['form_ga'])
            h_stats['H_home_sf'].append(stats.get('form_sf', 0))
            h_stats['H_home_sa'].append(stats.get('form_sa', 0))
            
            # Away Team at Away (Last 5)
            a_hist = team_away_matches[away][team_away_matches[away]['date'] < date].tail(5)
            stats = self._get_stats_from_history(a_hist, away)
            a_stats['A_away_pts'].append(stats['form_pts'])
            a_stats['A_away_gf'].append(stats['form_gf'])
            a_stats['A_away_ga'].append(stats['form_ga'])
            a_stats['A_away_sf'].append(stats.get('form_sf', 0))
            a_stats['A_away_sa'].append(stats.get('form_sa', 0))
            
        for k, v in h_stats.items(): df[k] = v
        for k, v in a_stats.items(): df[k] = v
        
        return df

    def _calculate_rolling(self, df, window, suffix):
        """Helper to calculate rolling stats for a specific window."""
        teams = pd.concat([df['home_team'], df['away_team']]).unique()
        
        # Dictionaries to store results
        h_stats = {f'H_form_pts{suffix}': [], f'H_form_gf{suffix}': [], f'H_form_ga{suffix}': [], f'H_form_ou{suffix}': [], f'H_form_str{suffix}': [],
                   f'H_form_sf{suffix}': [], f'H_form_sa{suffix}': [], f'H_form_cf{suffix}': [], f'H_form_ca{suffix}': []}
        a_stats = {f'A_form_pts{suffix}': [], f'A_form_gf{suffix}': [], f'A_form_ga{suffix}': [], f'A_form_ou{suffix}': [], f'A_form_str{suffix}': [],
                   f'A_form_sf{suffix}': [], f'A_form_sa{suffix}': [], f'A_form_cf{suffix}': [], f'A_form_ca{suffix}': []}
        
        # Pre-calculate team match histories for speed
        team_matches = {}
        for team in teams:
            # Get all matches for team
            tm = df[(df['home_team'] == team) | (df['away_team'] == team)].sort_values('date')
            team_matches[team] = tm
            
        # Iterate through main DF to assign rolling stats
        for idx, row in df.iterrows():
            date = row['date']
            home = row['home_team']
            away = row['away_team']
            
            # HOME TEAM Stats
            h_hist = team_matches[home][team_matches[home]['date'] < date].tail(window)
            stats = self._get_stats_from_history(h_hist, home)
            h_stats[f'H_form_pts{suffix}'].append(stats['form_pts'])
            h_stats[f'H_form_gf{suffix}'].append(stats['form_gf'])
            h_stats[f'H_form_ga{suffix}'].append(stats['form_ga'])
            h_stats[f'H_form_ou{suffix}'].append(stats['form_ou'])
            h_stats[f'H_form_str{suffix}'].append(stats['form_str'])
            # Shot/Corner logic (if standard fields exist) - simplified for now
            h_stats[f'H_form_sf{suffix}'].append(stats.get('form_sf', 0)) 
            h_stats[f'H_form_sa{suffix}'].append(stats.get('form_sa', 0))
            h_stats[f'H_form_cf{suffix}'].append(stats.get('form_cf', 0))
            h_stats[f'H_form_ca{suffix}'].append(stats.get('form_ca', 0))

            # AWAY TEAM Stats
            a_hist = team_matches[away][team_matches[away]['date'] < date].tail(window)
            stats = self._get_stats_from_history(a_hist, away)
            a_stats[f'A_form_pts{suffix}'].append(stats['form_pts'])
            a_stats[f'A_form_gf{suffix}'].append(stats['form_gf'])
            a_stats[f'A_form_ga{suffix}'].append(stats['form_ga'])
            a_stats[f'A_form_ou{suffix}'].append(stats['form_ou'])
            a_stats[f'A_form_str{suffix}'].append(stats['form_str'])
            a_stats[f'A_form_sf{suffix}'].append(stats.get('form_sf', 0))
            a_stats[f'A_form_sa{suffix}'].append(stats.get('form_sa', 0))
            a_stats[f'A_form_cf{suffix}'].append(stats.get('form_cf', 0)) 
            a_stats[f'A_form_ca{suffix}'].append(stats.get('form_ca', 0))
            
        # Assign columns
        for k, v in h_stats.items(): df[k] = v
        for k, v in a_stats.items(): df[k] = v
        
        return df

    def _get_stats_from_history(self, history_df: pd.DataFrame, target_team: str) -> dict:
        """
        Calculates rolling features (Pts, GF, GA, O/U rate, Shots, Corners) from a DataFrame of historical matches.
        This is a refactored version of calculate_features_from_h2h to work with DataFrames.
        """
        if history_df.empty:
            return {
                'form_pts': 0, 'form_gf': 0, 'form_ga': 0, 'form_ou': 0, 'form_str': '',
                'form_sf': 0, 'form_sa': 0, 'form_cf': 0, 'form_ca': 0
            }
            
        pts = []
        gf = []
        ga = []
        ou = []
        results_list = []
        sf = [] # Shots For
        sa = [] # Shots Against
        cf = [] # Corners For
        ca = [] # Corners Against

        for _, m in history_df.iterrows():
            try:
                h_score = m['FTHG']
                a_score = m['FTAG']
                
                is_home_game = (m['home_team'] == target_team)
                
                # Stats
                my_goals = h_score if is_home_game else a_score
                opp_goals = a_score if is_home_game else h_score
                
                total_goals = h_score + a_score
                
                # Points
                if my_goals > opp_goals: 
                    p = 3
                    res_char = 'W'
                elif my_goals == opp_goals: 
                    p = 1
                    res_char = 'D'
                else: 
                    p = 0
                    res_char = 'L'
                
                pts.append(p)
                gf.append(my_goals)
                ga.append(opp_goals)
                ou.append(1 if total_goals > 2.5 else 0)
                results_list.append(res_char)

                # Shots and Corners (if available)
                # Shots and Corners (if available)
                sf.append(m.get('HST', 0) if is_home_game else m.get('AST', 0))
                sa.append(m.get('AST', 0) if is_home_game else m.get('HST', 0))
                cf.append(m.get('HC', 0) if is_home_game else m.get('AC', 0))
                ca.append(m.get('AC', 0) if is_home_game else m.get('HC', 0))
                
            except KeyError:
                # Handle cases where FTHG, FTAG, HST, etc. might be missing
                # For now, just skip this match or use default values
                continue
            except Exception as e:
                # Catch other potential errors during processing
                continue

        if not pts:
            return {
                'form_pts': 0, 'form_gf': 0, 'form_ga': 0, 'form_ou': 0, 'form_str': '',
                'form_sf': 0, 'form_sa': 0, 'form_cf': 0, 'form_ca': 0
            }

        return {
            'form_pts': np.mean(pts),
            'form_gf': np.mean(gf),
            'form_ga': np.mean(ga),
            'form_ou': np.mean(ou),
            'form_str': ",".join(results_list),
            'form_sf': np.mean(sf) if sf else 0,
            'form_sa': np.mean(sa) if sa else 0,
            'form_cf': np.mean(cf) if cf else 0,
            'form_ca': np.mean(ca) if ca else 0
        }
        # We need Season column for PPG to reset every season. 
        # If Season doesn't exist, we can infer from date (approx) or accumulate infinite history?
        # User said "accumulated so far in the season".
        # Assuming we can derive season from date (Aug-May).
        
        # 1. Elo Diff (Simple)
        df = self.add_elo_features(df)
        
        # 2. League Encoding
        df = self.add_league_encoding(df)

        # 3. Create a Team-Match DataFrame for calculation
        cols = ['date', 'home_team', 'away_team', 'FTHG', 'FTAG', 'FTR', 
                'HST', 'AST', 'HC', 'AC',
                'league', 'Season']
        existing_cols = [c for c in cols if c in df.columns]
        
        # We need Season column for PPG to reset every season. 
        # If Season doesn't exist, we can infer from date (approx) or accumulate infinite history?
        # User said "accumulated so far in the season".
        # Assuming we can derive season from date (Aug-May).
        
        home_matches = df[existing_cols].copy()
        home_matches['goals_for'] = home_matches['FTHG']
        home_matches['goals_against'] = home_matches['FTAG']
        home_matches['shots_for'] = home_matches.get('HST', np.nan)
        home_matches['shots_against'] = home_matches.get('AST', np.nan)
        home_matches['corners_for'] = home_matches.get('HC', np.nan)
        home_matches['corners_against'] = home_matches.get('AC', np.nan)
        
        home_matches = home_matches.rename(columns={'home_team': 'team'})
        home_matches['points'] = home_matches['FTR'].map({'H': 3, 'D': 1, 'A': 0})
        home_matches['is_home'] = 1
        home_matches['over_2_5'] = (home_matches['goals_for'] + home_matches['goals_against'] > 2.5).astype(int)
        
        meta_cols = ['date', 'team', 'points', 'goals_for', 'goals_against', 'over_2_5',
                     'shots_for', 'shots_against', 'corners_for', 'corners_against', 'is_home', 'league']
        
        home_params = home_matches[meta_cols]

        away_matches = df[existing_cols].copy()
        away_matches['goals_for'] = away_matches['FTAG']
        away_matches['goals_against'] = away_matches['FTHG']
        away_matches['shots_for'] = away_matches.get('AST', np.nan)
        away_matches['shots_against'] = away_matches.get('HST', np.nan)
        away_matches['corners_for'] = away_matches.get('AC', np.nan)
        away_matches['corners_against'] = away_matches.get('HC', np.nan)
        
        away_matches = away_matches.rename(columns={'away_team': 'team'})
        away_matches['points'] = away_matches['FTR'].map({'A': 3, 'D': 1, 'H': 0})
        away_matches['is_home'] = 0
        away_matches['over_2_5'] = (away_matches['goals_for'] + away_matches['goals_against'] > 2.5).astype(int)
        
        away_params = away_matches[meta_cols]

        # Stack them
        team_stats = pd.concat([home_params, away_params]).sort_values('date').reset_index(drop=True)
        
        # --- PPG & Relative Strength Calculation ---
        # We need to compute cumulative stats *prior* to the current match.
        # Group by [League, Team, Season] would be ideal, but Season is tricky without explicit column.
        # Let's use a simplified approach: Rolling window of 38 games (approx 1 season) for "Current Strength"?
        # OR: Cumulative Sum expanding window, but we need to reset?
        # Infinite Accumulation (Career PPG) vs Season PPG?
        # User asked for "accumulated so far in the season".
        # Let's define a Season Key based on Month. (Aug to July).
        
        team_stats['season_year'] = team_stats['date'].apply(lambda d: d.year if d.month >= 8 else d.year - 1)
        
        # Group by Team and SeasonYear to calc running PPG
        # shift(1) to ensure we use pre-match stats
        
        def calc_running_stats(x):
            # x is a dataframe for a specific team in a specific season, sorted by date
            x = x.sort_values('date')
            
            # Cumulative Points / Games Played
            # We want stats BEFORE the match.
            # So shift data down by 1.
            past_stats = x.shift(1)
            
            # Cumulative Sums
            cum_pts = past_stats['points'].cumsum().fillna(0)
            cum_games = past_stats['points'].expanding().count().fillna(0) # count any valid row
            
            # PPG
            # Avoid division by zero
            running_ppg = cum_pts / cum_games.replace(0, 1) 
            running_ppg = running_ppg.where(cum_games > 0, 0) # if games=0, ppg=0
            
            # Relative Strength Inputs (Avg Goals Scored per game so far)
            cum_gf = past_stats['goals_for'].cumsum().fillna(0)
            cum_ga = past_stats['goals_against'].cumsum().fillna(0)
            
            avg_gf = cum_gf / cum_games.replace(0, 1)
            avg_ga = cum_ga / cum_games.replace(0, 1)
            
            return pd.DataFrame({
                'ppg': running_ppg,
                'avg_gf': avg_gf,
                'avg_ga': avg_ga,
                'original_index': x.index
            })

        # Apply per team-season
        # FutureWarning Fix: include_groups=False
        try:
            running_stats = team_stats.groupby(['team', 'season_year']).apply(calc_running_stats, include_groups=False).reset_index(drop=True)
        except TypeError:
            # Fallback for older pandas
            running_stats = team_stats.groupby(['team', 'season_year']).apply(calc_running_stats).reset_index(drop=True)
        
        # Restore index to merge
        running_stats = running_stats.set_index('original_index')
        team_stats = team_stats.merge(running_stats, left_index=True, right_index=True)
        
        # Now we have PPG, AvgGF, AvgGA for each team-match.
        
        # --- League Averages (Season-to-Date) ---
        # We need the average Home GF and Away GF for the league up to that date?
        # Or simpler: Global Rolling Average for the league?
        # User: "Attack strength home and defense weakness away (take into account GD, goal difference)"
        # "Home Team Avg GF / League Avg Home GF"
        # We need LEAGUE running stats.
        
        # Calculate League running averages
        # Group by League+SeasonYear
        def calc_league_running(x):
            x = x.sort_values('date')
            past = x.shift(1)
            # Global Average Goals (Home+Away) / 2? No, League Avg Goals per Team per Game.
            # Avg GF per game.
            cum_gf = past['goals_for'].cumsum().fillna(0)
            cum_games = past['goals_for'].expanding().count().fillna(0)
            
            league_avg_gf = cum_gf / cum_games.replace(0, 1)
            return pd.DataFrame({
                'league_avg_gf': league_avg_gf,
                'original_index': x.index
            })
            
        try:
            league_running = team_stats.groupby(['league', 'season_year']).apply(calc_league_running, include_groups=False).reset_index(drop=True)
        except TypeError:
            # Fallback for older pandas
            league_running = team_stats.groupby(['league', 'season_year']).apply(calc_league_running).reset_index(drop=True)
        league_running = league_running.set_index('original_index')
        team_stats = team_stats.merge(league_running, left_index=True, right_index=True)
        
        # Apply Relative Strength Formulas
        # Attack Strength: Team Avg GF / League Avg GF
        # Defense Weakness: Team Avg GA / League Avg GF (how many they concede vs league avg scoring)
        
        # Avoid zero division
        team_stats['att_strength'] = team_stats['avg_gf'] / team_stats['league_avg_gf'].replace(0, 1)
        team_stats['def_weakness'] = team_stats['avg_ga'] / team_stats['league_avg_gf'].replace(0, 1) # using avg_gf as baseline for "goals expected"
        
        # 4. Standard Rolling Features (Last 5)
        features_to_roll = ['points', 'goals_for', 'goals_against', 'over_2_5',
                           'shots_for', 'shots_against', 'corners_for', 'corners_against']
                           
        grouped = team_stats.groupby('team')[features_to_roll]
        
        def calculate_rolling(x):
            return x.shift(1).rolling(window=window, min_periods=1).mean()

        rolling_stats = grouped.apply(calculate_rolling)
        
        # Join back
        new_cols = ['roll_pts', 'roll_gf', 'roll_ga', 'roll_ou', 
                    'roll_sf', 'roll_sa', 'roll_cf', 'roll_ca']
        team_stats[new_cols] = rolling_stats.reset_index(level=0, drop=True)
        
        # 5. Merge back into main DF
        team_stats_indexed = team_stats.set_index(['date', 'team'])
        
        # Extract features for Home Team (Current match)
        # We need: PPG, AttStr, DefWeak, Rolling Stats
        cols_extract = ['date', 'team', 'ppg', 'att_strength', 'def_weakness'] + new_cols
        
        home_feats = team_stats[team_stats['is_home'] == 1][cols_extract].copy()
        home_feats.columns = ['date', 'home_team', 'H_ppg', 'H_att', 'H_def'] + \
                             ['H_form_pts', 'H_form_gf', 'H_form_ga', 'H_form_ou', 'H_form_sf', 'H_form_sa', 'H_form_cf', 'H_form_ca']
                             
        away_feats = team_stats[team_stats['is_home'] == 0][cols_extract].copy()
        away_feats.columns = ['date', 'away_team', 'A_ppg', 'A_att', 'A_def'] + \
                             ['A_form_pts', 'A_form_gf', 'A_form_ga', 'A_form_ou', 'A_form_sf', 'A_form_sa', 'A_form_cf', 'A_form_ca']
                             
        # Merge
        df_enriched = pd.merge(df, home_feats, on=['date', 'home_team'], how='left')
        df_enriched = pd.merge(df_enriched, away_feats, on=['date', 'away_team'], how='left')
        
        # Calc Differences
        df_enriched['ppg_diff'] = df_enriched['H_ppg'] - df_enriched['A_ppg']
        
        # Re-calc Elo Diff (if not done)
        if 'H_elo' in df_enriched.columns and 'A_elo' in df_enriched.columns:
            df_enriched['elo_diff'] = df_enriched['H_elo'] - df_enriched['A_elo']
            
        # Re-add League Encoding
        df_enriched = self.add_league_encoding(df_enriched)

        # --- Specific Home/Away Form (unchanged logic) ---
        # 1. Home Form (Only Home Games)
        home_games_only = team_stats[team_stats['is_home'] == 1].copy()
        grouped_home = home_games_only.groupby('team')[['points', 'goals_for', 'goals_against', 'shots_for', 'shots_against']]
        
        def calculate_rolling_basic(x):
            return x.shift(1).rolling(window=window, min_periods=1).mean()
            
        rolling_home = grouped_home.apply(calculate_rolling_basic).reset_index(level=0, drop=True)
        home_games_only[['roll_home_pts', 'roll_home_gf', 'roll_home_ga']] = rolling_home[['points', 'goals_for', 'goals_against']]
        home_games_only['roll_home_sf'] = rolling_home.get('shots_for')
        home_games_only['roll_home_sa'] = rolling_home.get('shots_against')
        
        h_specific_feats = home_games_only[['date', 'team', 'roll_home_pts', 'roll_home_gf', 'roll_home_ga', 'roll_home_sf', 'roll_home_sa']].copy()
        h_specific_feats.columns = ['date', 'home_team', 'H_home_pts', 'H_home_gf', 'H_home_ga', 'H_home_sf', 'H_home_sa']
        
        # 2. Away Form (Only Away Games)
        away_games_only = team_stats[team_stats['is_home'] == 0].copy()
        grouped_away = away_games_only.groupby('team')[['points', 'goals_for', 'goals_against', 'shots_for', 'shots_against']]
        rolling_away = grouped_away.apply(calculate_rolling_basic).reset_index(level=0, drop=True)
        away_games_only[['roll_away_pts', 'roll_away_gf', 'roll_away_ga']] = rolling_away[['points', 'goals_for', 'goals_against']]
        away_games_only['roll_away_sf'] = rolling_away.get('shots_for')
        away_games_only['roll_away_sa'] = rolling_away.get('shots_against')
        
        a_specific_feats = away_games_only[['date', 'team', 'roll_away_pts', 'roll_away_gf', 'roll_away_ga', 'roll_away_sf', 'roll_away_sa']].copy()
        a_specific_feats.columns = ['date', 'away_team', 'A_away_pts', 'A_away_gf', 'A_away_ga', 'A_away_sf', 'A_away_sa']

        df_enriched = pd.merge(df_enriched, h_specific_feats, on=['date', 'home_team'], how='left')
        df_enriched = pd.merge(df_enriched, a_specific_feats, on=['date', 'away_team'], how='left')
        
        # Cleanup NaNs
        df_enriched = df_enriched.dropna(subset=['H_form_pts', 'A_form_pts'])
        
        return df_enriched

if __name__ == "__main__":
    from data_loader import DataLoader
    loader = DataLoader("data_sets/MatchHistory")
    df = loader.load_historical_data()
    
    fe = FeatureEngineer()
    df_features = fe.add_rolling_features(df)
    
    print(f"Enriched Data Shape: {df_features.shape}")
    print(df_features[['date', 'home_team', 'away_team', 'H_form_pts', 'FTR']].tail(10))
