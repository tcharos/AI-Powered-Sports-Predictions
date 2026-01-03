import pandas as pd
import time
import xgboost as xgb
import json
import os
import datetime
from feature_engineering import FeatureEngineer
from entity_resolver import EntityResolver
from data_loader import DataLoader
import glob

from heuristic_adjuster import HeuristicAdjuster

class MatchPredictor:
    def __init__(self, history_dir="data_sets/MatchHistory", scraper_output="output/output.json"):
        # Load Models
        self.model_1x2 = xgb.XGBClassifier()
        self.model_1x2.load_model("models/xgb_model_1x2.json")
        
        # Load O/U Model (Regressor now)
        self.model_ou = xgb.XGBRegressor() # Changed from Classifier
        self.model_ou.load_model("models/xgb_model_ou.json")
        
        # Load Features
        with open("models/features_1x2.json", 'r') as f:
            self.features_1x2 = json.load(f)
        with open("models/features_ou.json", 'r') as f:
            self.features_ou = json.load(f)
            
        self.output_file = scraper_output
        self.fe = FeatureEngineer()
        self.resolver = EntityResolver()
        
        print("Loading historical data for feature calculation...")
        self.loader = DataLoader(history_dir)
        self.history_df = self.loader.load_historical_data()
        self.history_df = self.history_df.sort_values('date')
        
        self.adjuster = HeuristicAdjuster()

    # ... (existing methods) ...

    def get_team_stats(self, team_name, date_before):
        """
        Calculates the rolling features for a team given the historical database.
        Returns a dictionary of features.
        """
        if not team_name:
            return None
            
        # Filter games played by this team BEFORE the match date
        # Note: We use date_before (string YYYY-MM-DD or datetime)
        mask = (self.history_df['date'] < date_before) & \
               ((self.history_df['home_team'] == team_name) | (self.history_df['away_team'] == team_name))
        
        team_games = self.history_df[mask].tail(50).copy() # Take last 50 to stand a chance of finding 5
        
        if team_games.empty:
            return None
            
        # We need to manually calculate the rolling stats because we can't easily rely on pre-calculated rolling columns
        # if the last match was months ago. But wait, `add_rolling_features` added rolling cols to the WHOLE history?
        # NO, `predict_matches.py` loads raw CSV data via DataLoader. It does NOT have the rolling columns pre-calculated.
        # So we must calculate them on the fly from the last 5 rows.
        
        stats = {}
        
        # Helper to extract stats from a row for the focus team
        def extract_match_stats(row, team):
            is_home = (row['home_team'] == team)
            pts = 3 if row['FTR'] == ('H' if is_home else 'A') else (1 if row['FTR'] == 'D' else 0)
            gf = row['FTHG'] if is_home else row['FTAG']
            ga = row['FTAG'] if is_home else row['FTHG']
            sf = row['HST'] if is_home else row['AST'] # HST = Home Shots on Target
            sa = row['AST'] if is_home else row['HST']
            cf = row['HC'] if is_home else row['AC']
            ca = row['AC'] if is_home else row['HC']
            
            # OU Logic: 1 if > 2.5 else 0
            ou = 1 if (row['FTHG'] + row['FTAG']) > 2.5 else 0
            
            return {'pts': pts, 'gf': gf, 'ga': ga, 'sf': sf, 'sa': sa, 'cf': cf, 'ca': ca, 'ou': ou}

        # 1. Overall Form (Last 5)
        last_5 = team_games.tail(5)
        # Calculate means
        sums = {'pts': 0, 'gf': 0, 'ga': 0, 'sf': 0, 'sa': 0, 'cf': 0, 'ca': 0, 'ou': 0}
        count = 0
        
        for _, row in last_5.iterrows():
            s = extract_match_stats(row, team_name)
            for k in sums: sums[k] += s.get(k, 0) # Handle missing stats (NaN -> 0)
            count += 1
            
        if count > 0:
            stats['form_pts'] = sums['pts'] / count
            stats['form_gf'] = sums['gf'] / count
            stats['form_ga'] = sums['ga'] / count
            stats['form_sf'] = sums['sf'] / count
            stats['form_sa'] = sums['sa'] / count
            stats['form_cf'] = sums['cf'] / count
            stats['form_ca'] = sums['ca'] / count
            stats['form_ou'] = sums['ou'] / count # Proportion of Over 2.5
        else:
             # Default to 0? Or averages?
            stats.update({k: 0 for k in ['form_pts', 'form_gf', 'form_ga', 'form_sf', 'form_sa', 'form_cf', 'form_ca', 'form_ou']})

        # 2. Venue Specific Form (Last 5 Home or Away)
        # If we are verifying this team as HOME team, we want last 5 HOME games.
        # But this method is generic. Let's return both or split?
        # Let's return separate dicts for Home and Away specific form.
        
        return stats

    def get_venue_specific_stats(self, team_name, is_home_focus, date_before):
        """
        Get stats for Last 5 HOME games if is_home_focus=True, else Last 5 AWAY games.
        """
        if not team_name: return None
        
        if is_home_focus:
            mask = (self.history_df['date'] < date_before) & (self.history_df['home_team'] == team_name)
        else:
            mask = (self.history_df['date'] < date_before) & (self.history_df['away_team'] == team_name)
            
        specific_games = self.history_df[mask].tail(5)
        
        sums = {'pts': 0, 'gf': 0, 'ga': 0, 'sf': 0, 'sa': 0} # No corners needed for specific as per feature list
        count = 0
        
        for _, row in specific_games.iterrows():
            # Extract stats logic duplicated... simplify
            # Just grab raw columns
            if is_home_focus:
                pts = 3 if row['FTR'] == 'H' else (1 if row['FTR'] == 'D' else 0)
                gf = row['FTHG']
                ga = row['FTAG']
                sf = row['HST']
                sa = row['AST']
            else:
                pts = 3 if row['FTR'] == 'A' else (1 if row['FTR'] == 'D' else 0)
                gf = row['FTAG']
                ga = row['FTHG']
                sf = row['AST']
                sa = row['HST']
            
            sums['pts'] += pts
            sums['gf'] += gf
            sums['ga'] += ga
            sums['sf'] += sf if pd.notna(sf) else 0
            sums['sa'] += sa if pd.notna(sa) else 0
            count += 1
            
        if count > 0:
            return {
                'spec_pts': sums['pts'] / count,
                'spec_gf': sums['gf'] / count,
                'spec_ga': sums['ga'] / count,
                'spec_sf': sums['sf'] / count,
                'spec_sa': sums['sa'] / count
            }
        else:
             return {k: 0 for k in ['spec_pts', 'spec_gf', 'spec_ga', 'spec_sf', 'spec_sa']}



    def predict(self):
        start_time = time.time()
        if not os.path.exists(self.output_file):
            print("No output.json found.")
            return

        with open(self.output_file, 'r') as f:
            try:
                upcoming_matches = json.load(f)
            except json.JSONDecodeError:
                print(f"[-] Error: The file {os.path.basename(self.output_file)} is corrupt or empty.")
                print("[!] Please check 'Force Scrape' in the Dashboard and run Prediction again.")
                return
            
        predictions = []
        match_dates = set()
        
        print(f"Predicting {len(upcoming_matches)} matches...")
        
        for match in upcoming_matches:
            scraper_home = match.get('home_team')
            scraper_away = match.get('away_team')
            league_name = match.get('league', 'Unknown')
            
            # --- LEAGUE FILTERING ---
            SUPPORTED_COUNTRIES = {
                'ENGLAND', 'SPAIN', 'FRANCE', 'GERMANY', 'ITALY', 'NETHERLANDS', 'PORTUGAL', 'SCOTLAND', 
                'TURKEY', 'USA', 'POLAND', 'RUSSIA', 'NORWAY', 'SWEDEN', 'FINLAND', 'ROMANIA', 'GREECE', 
                'IRELAND', 'SWITZERLAND', 'JAPAN', 'MEXICO', 'BELGIUM', 'AUSTRIA', 'DENMARK', 'CZECH REPUBLIC', 'CROATIA',
                'EUROPE', 'WORLD'
            }
            country_prefix = league_name.split(':')[0].upper().strip()
            if country_prefix not in SUPPORTED_COUNTRIES:
                continue

            # Resolve Names
            canon_home = self.resolver.get_canonical_name(scraper_home)
            canon_away = self.resolver.get_canonical_name(scraper_away)
            
            # ELO Lookup
            home_elo = self.resolver.get_elo(scraper_home)
            away_elo = self.resolver.get_elo(scraper_away)
            
            # Defaults
            if not home_elo or pd.isna(home_elo): home_elo = 1500
            if not away_elo or pd.isna(away_elo): away_elo = 1500

            match_date_str = match.get('start_time', 'Unknown')
            match_date_obj = pd.Timestamp.now() # Default
            
            if match_date_str != 'Unknown':
                 try:
                     d_str = match_date_str.split(' ')[0]
                     match_date_obj = pd.to_datetime(d_str, dayfirst=True)
                     # Standardize to ISO format for output consistency
                     match_date_str = match_date_obj.strftime("%Y-%m-%d %H:%M")
                     match_dates.add(match_date_obj.strftime("%Y-%m-%d"))
                 except:
                     pass

            # Calculate Stats from DB
            try:
                h_stats = self.get_team_stats(canon_home, match_date_obj)
                a_stats = self.get_team_stats(canon_away, match_date_obj)
                
                h_spec = self.get_venue_specific_stats(canon_home, True, match_date_obj)
                a_spec = self.get_venue_specific_stats(canon_away, False, match_date_obj)
            except Exception as e:
                # print(f"Error calculating stats for {scraper_home} vs {scraper_away}: {e}")
                h_stats = None # Will fallback to zeros
            
            if not h_stats: h_stats = {k: 0 for k in ['form_pts', 'form_gf', 'form_ga', 'form_sf', 'form_sa', 'form_cf', 'form_ca', 'form_ou']}
            if not a_stats: a_stats = {k: 0 for k in ['form_pts', 'form_gf', 'form_ga', 'form_sf', 'form_sa', 'form_cf', 'form_ca', 'form_ou']}
            if not h_spec: h_spec = {k: 0 for k in ['spec_pts', 'spec_gf', 'spec_ga', 'spec_sf', 'spec_sa']}
            if not a_spec: a_spec = {k: 0 for k in ['spec_pts', 'spec_gf', 'spec_ga', 'spec_sf', 'spec_sa']}

            # Odds
            b365_h_str = match.get('interaction_1x2_1', '-')
            b365_d_str = match.get('interaction_1x2_X', '-')
            b365_a_str = match.get('interaction_1x2_2', '-')
            
            if '-' in [b365_h_str, b365_d_str, b365_a_str]: continue
            try:
                b365_h = float(b365_h_str.replace(',', '.'))
                b365_d = float(b365_d_str.replace(',', '.'))
                b365_a = float(b365_a_str.replace(',', '.'))
            except: continue
                
            # Construct Input Vector
            input_row = {
                'B365H': b365_h, 'B365D': b365_d, 'B365A': b365_a,
                'H_form_pts': h_stats['form_pts'], 'H_form_gf': h_stats['form_gf'], 'H_form_ga': h_stats['form_ga'],
                'A_form_pts': a_stats['form_pts'], 'A_form_gf': a_stats['form_gf'], 'A_form_ga': a_stats['form_ga'],
                'H_elo': home_elo, 'A_elo': away_elo,
                'H_home_pts': h_spec['spec_pts'], 'H_home_gf': h_spec['spec_gf'], 'H_home_ga': h_spec['spec_ga'],
                'H_home_sf': h_spec['spec_sf'], 'H_home_sa': h_spec['spec_sa'],
                'A_away_pts': a_spec['spec_pts'], 'A_away_gf': a_spec['spec_gf'], 'A_away_ga': a_spec['spec_ga'],
                'A_away_sf': a_spec['spec_sf'], 'A_away_sa': a_spec['spec_sa'],
                # New Stats
                'H_form_sf': h_stats['form_sf'], 'H_form_sa': h_stats['form_sa'],
                'H_form_cf': h_stats['form_cf'], 'H_form_ca': h_stats['form_ca'],
                'A_form_sf': a_stats['form_sf'], 'A_form_sa': a_stats['form_sa'],
                'A_form_cf': a_stats['form_cf'], 'A_form_ca': a_stats['form_ca'],
                'league_cat': league_name, # Feature 11
                'match_id': match.get('match_id', '') # Save ID for verification
            }
            
            # Generate DF for prediction
            input_df = pd.DataFrame([input_row])
            input_df['league_cat'] = input_df['league_cat'].astype('category')
            
            # Predict
            # Ensure columns exist and order
            valid_cols_1x2 = [c for c in self.features_1x2 if c in input_df.columns] 
            # If missing cols (e.g. model has feature X but we didn't calc it?), fill 0
            for c in self.features_1x2:
                if c not in input_df.columns: input_df[c] = 0
            
            probs_1x2 = self.model_1x2.predict_proba(input_df[self.features_1x2])[0]
            pred_1x2_idx = probs_1x2.argmax()
            pred_1x2_label = ['Home', 'Draw', 'Away'][pred_1x2_idx]
            conf_1x2 = probs_1x2[pred_1x2_idx]
            
            # O/U
            # Add OU specific features
            input_row['H_form_ou'] = h_stats['form_ou']
            input_row['A_form_ou'] = a_stats['form_ou']
            input_df_ou = pd.DataFrame([input_row])
            input_df_ou['league_cat'] = input_df_ou['league_cat'].astype('category')
            for c in self.features_ou:
                if c not in input_df_ou.columns: input_df_ou[c] = 0
                
            # --- POISSON PREDICTION ---
            # Output is expected goals (lambda)
            pred_lam = self.model_ou.predict(input_df_ou[self.features_ou])[0]
            
            # Convert lambda to Prob(> 2.5) using Poisson
            # P(X<=2) = e^-lam * (1 + lam + lam^2/2)
            import numpy as np
            prob_le_2 = np.exp(-pred_lam) * (1 + pred_lam + (pred_lam**2 / 2))
            prob_over = 1.0 - prob_le_2
            prob_under = 1.0 - prob_over
            
            # Construct probs array [Under, Over] to match old interface
            probs_ou = np.array([prob_under, prob_over])
            
            # Extract O/U Odds (Moved Up)
            try:
                ov_str = match.get('over_2_5', '0.0')
                un_str = match.get('under_2_5', '0.0')
                ov_val = float(ov_str.replace(',', '.')) if ov_str and ov_str != '-' else 0.0
                un_val = float(un_str.replace(',', '.')) if un_str and un_str != '-' else 0.0
            except:
                ov_val, un_val = 0.0, 0.0
            
            # --- HEURISTIC ADJUSTMENT ---
            match_info = {
                'League': league_name,
                'Home Team': scraper_home,
                'Away Team': scraper_away,
                'Odds': {
                    '1': b365_h,
                    'X': b365_d,
                    '2': b365_a,
                    'O': ov_val,
                    'U': un_val
                }
            }
            adj_1x2, adj_ou, adj_logs = self.adjuster.adjust_probabilities(match_info, probs_1x2, probs_ou)
            
            # Select Final Predictions based on ADJUSTED probabilities
            # 1X2
            pred_1x2_idx = adj_1x2.index(max(adj_1x2))
            # Use ADJUSTED probs for final decision
            pred_1x2_idx = adj_1x2.index(max(adj_1x2))
            pred_1x2_label = ['1', 'X', '2'][pred_1x2_idx]
            conf_1x2 = adj_1x2[pred_1x2_idx]
            
            # OU
            pred_ou_idx = adj_ou.index(max(adj_ou))
            pred_ou_label = "Over 2.5" if pred_ou_idx == 1 else "Under 2.5"
            conf_ou = adj_ou[pred_ou_idx]
            
            
            # Extract O/U Odds (Determine which one matches prediction)
            ou_odd = ov_val if pred_ou_label == "Over 2.5" else un_val
            
            # Odd 1X2
            pred_odd = b365_h if pred_1x2_label == '1' else (b365_d if pred_1x2_label == 'X' else b365_a)
            
            # --- BETTING STRATEGY (EV & KELLY) ---
            def calculate_kelly(odd, prob):
                if odd <= 1.0 or prob <= 0: return 0.0, 0.0
                b = odd - 1.0
                q = 1.0 - prob
                # Full Kelly
                f = (b * prob - q) / b
                f = max(f, 0.0) # No negative bets
                # Quarter Kelly
                return (prob * odd) - 1.0, f * 0.25

            # 1X2 Strategy
            ev_1x2, kelly_1x2 = calculate_kelly(pred_odd, conf_1x2)
            
            # O/U Strategy
            ev_ou, kelly_ou = calculate_kelly(ou_odd, conf_ou)

            predictions.append({
                'Date': match_date_str,
                'League': league_name,
                'Home Team': scraper_home,
                'Away Team': scraper_away,
                'Home ELO': int(home_elo),
                'Away ELO': int(away_elo),
                'Prediction 1X2': pred_1x2_label,
                'Prediction 1X2 Odd': f"{pred_odd:.2f}",
                'Conf 1X2': f"{conf_1x2:.2f}",
                'EV 1X2': f"{ev_1x2:.2f}",
                'Kelly 1X2': f"{kelly_1x2:.2%}",
                'Prediction O/U': pred_ou_label,
                'Prediction O/U Odd': f"{ou_odd:.2f}",
                'Conf O/U': f"{conf_ou:.2f}",
                'EV O/U': f"{ev_ou:.2f}",
                'Kelly O/U': f"{kelly_ou:.2%}",
                'Home Win %': f"{adj_1x2[0]:.2f}",
                'Draw %': f"{adj_1x2[1]:.2f}",
                'Away Win %': f"{adj_1x2[2]:.2f}",
                'Over %': f"{adj_ou[1]:.2f}",
                'Under %': f"{adj_ou[0]:.2f}",
                'Under %': f"{adj_ou[0]:.2f}",
                'Adj Logs': "; ".join(adj_logs),
                'match_id': match.get('match_id', '')
            })


        # Save Logic (Same as before)
        if match_dates:
            # Use the most common date (mode) to avoid one-off early matches affecting the filename
            # match_dates is a set of strings. We need all dates to find mode.
            # Re-collect all dates
            all_dates = []
            for match in upcoming_matches:
                 ms = match.get('start_time', 'Unknown')
                 if ms != 'Unknown':
                     try:
                         d_str = ms.split(' ')[0]
                         d_obj = pd.to_datetime(d_str, dayfirst=True)
                         all_dates.append(d_obj.strftime("%Y-%m-%d"))
                     except: pass
            
            if all_dates:
                 target_date = pd.Series(all_dates).mode()[0]
            else:
                 target_date = sorted(list(match_dates))[0]

            filename = f"output/predictions_{target_date}.csv"
        else:
            filename = "output/predictions_unknown_date.csv"

        res_df = pd.DataFrame(predictions)
        if not res_df.empty:
             # Reorder cols
             desired = ['Date', 'League', 'Home Team', 'Away Team', 'Home ELO', 'Away ELO', 'Prediction 1X2', 'Prediction 1X2 Odd', 'Conf 1X2', 'EV 1X2', 'Kelly 1X2', 'Prediction O/U', 'Prediction O/U Odd', 'Conf O/U', 'EV O/U', 'Kelly O/U', 'Home Win %', 'Draw %', 'Away Win %', 'Over %', 'Under %', 'Adj Logs', 'match_id']
             existing = [c for c in desired if c in res_df.columns]
             print("\n--- PREDICTIONS ---")
             print(res_df[existing].to_string(index=False))
             res_df[existing].to_csv(filename, index=False)
             print(f"Saved to {filename}")
        else:
            print("No valid predictions generated.")
            
        elapsed = time.time() - start_time
        print(f"[*] Prediction Finished in {elapsed:.2f} seconds.")

if __name__ == "__main__":
    predictor = MatchPredictor()
    predictor.predict()
