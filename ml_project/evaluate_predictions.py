import pandas as pd
import json
import os
import argparse
import datetime
from rapidfuzz import process, fuzz

class LeagueStatsManager:
    def __init__(self, stats_file="data_sets/league_analytics.json", check_file="data_sets/league_analytics_check.json"):
        self.stats_file = stats_file
        self.check_file = check_file
        self.stats = {}
        self.processed_dates = []
        self.load_data()
        
    def load_data(self):
        # Load Stats
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r') as f:
                    self.stats = json.load(f)
            except:
                self.stats = {}
        
        # Load Date Check
        if os.path.exists(self.check_file):
            try:
                with open(self.check_file, 'r') as f:
                    self.processed_dates = json.load(f)
            except:
                self.processed_dates = []

    def is_date_processed(self, date_str):
        return date_str in self.processed_dates

    def mark_date_processed(self, date_str):
        if date_str and date_str not in self.processed_dates:
            self.processed_dates.append(date_str)
            self.save_check()

    def update_match(self, league, correct_1x2, correct_ou, skip_stats=False):
        if not league: return
        
        # Ensure League in Stats
        if league not in self.stats:
            self.stats[league] = {
                "total_matches": 0,
                "correct_1x2": 0,
                "correct_ou": 0,
                "last_updated": ""
            }
            
        # Only update stats if not skipping (duplicate date)
        if not skip_stats:
            self.stats[league]["total_matches"] += 1
            if correct_1x2:
                self.stats[league]["correct_1x2"] += 1
            if correct_ou:
                self.stats[league]["correct_ou"] += 1
            
        self.stats[league]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")
        
    def save_stats(self):
        os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
        with open(self.stats_file, 'w') as f:
            json.dump(self.stats, f, indent=4)
            
    def save_check(self):
        os.makedirs(os.path.dirname(self.check_file), exist_ok=True)
        with open(self.check_file, 'w') as f:
            json.dump(self.processed_dates, f, indent=4)

class Evaluator:
    def __init__(self, predictions_file, results_file, output_csv=None):
        self.predictions_file = predictions_file
        self.results_file = results_file
        self.output_csv = output_csv
        self.stats_manager = LeagueStatsManager()

    def load_data(self):
        try:
            self.preds_df = pd.read_csv(self.predictions_file)
            with open(self.results_file, 'r') as f:
                self.results_data = json.load(f)
        except Exception as e:
            print(f"Error loading files: {e}")
            return False
        return True

    def normalize_name(self, name):
        return name.lower().strip() if name else ""

    def evaluate(self):
        if not self.load_data():
            return

        print(f"Evaluating {len(self.preds_df)} predictions against {len(self.results_data)} actual results...")

        matches_found = 0
        correct_1x2 = 0
        correct_ou = 0
        
        # Create a lookup for results based on home team
        # Since names might differ slightly, we'll iterate predictions and fuzzy match against results
        
        # Pre-process results for faster lookup
        results_map = {} # Normalized Home Team -> Result Object
        result_teams = []
        
        for res in self.results_data:
            h_team = res.get('home_team')
            if h_team:
                results_map[h_team] = res
                result_teams.append(h_team)

        details = []
        dates_in_file = []

        for _, row in self.preds_df.iterrows():
            pred_home = row['Home Team']
            pred_league = row.get('League', 'Unknown')
            pred_1x2 = str(row['Prediction 1X2']).strip() # Home, Draw, Away OR 1, X, 2
            
            # Normalize Prediction Labels (Backward Compatibility)
            status_map = {"Home": "1", "Draw": "X", "Away": "2"}
            if pred_1x2 in status_map:
                pred_1x2 = status_map[pred_1x2]
                
            pred_ou = str(row['Prediction O/U']).strip()   # Over 2.5, Under 2.5
            
            # Match Logic: ID First, Name Second
            match_id = str(row.get('match_id', '')).strip()
            
            # 1. Try Match ID (Exact)
            actual_data = None
            
            # Map by ID if we haven't already
            if not hasattr(self, 'results_by_id'):
                self.results_by_id = {r.get('match_id'): r for r in self.results_data if r.get('match_id')}

            if match_id and match_id in self.results_by_id:
                actual_data = self.results_by_id[match_id]
            
            # 2. Fallback to Fuzzy Name Match
            if not actual_data:
                match = process.extractOne(pred_home, result_teams, scorer=fuzz.ratio)
                if match and match[1] > 80:
                    matched_team_name = match[0]
                    actual_data = results_map[matched_team_name]
            
            if actual_data:
                # Check if scores exist
                h_score = actual_data.get('home_score')
                a_score = actual_data.get('away_score')
                
                if h_score is None or a_score is None:
                    continue # Match might be postponed or scores not scraped
                    
                try:
                    h_score = int(h_score)
                    a_score = int(a_score)
                except:
                    continue

                matches_found += 1
                
                # Determine Actual 1X2
                if h_score > a_score: actual_1x2 = "1"
                elif h_score == a_score: actual_1x2 = "X"
                else: actual_1x2 = "2"
                
                # Determine Actual O/U
                total_goals = h_score + a_score
                actual_ou = "Over 2.5" if total_goals > 2.5 else "Under 2.5"
                
                # Compare
                is_correct_1x2 = (pred_1x2 == actual_1x2)
                is_correct_ou = (pred_ou == actual_ou)
                
                if is_correct_1x2: correct_1x2 += 1
                if is_correct_ou: correct_ou += 1
                
                # Update Cumulative League Stats
                raw_date = str(row['Date'])
                # Split by space to remove time (e.g. "12.12.2025 21:45" -> "12.12.2025")
                current_date = raw_date.split(' ')[0]
                
                skip_update = self.stats_manager.is_date_processed(current_date)
                self.stats_manager.update_match(pred_league, is_correct_1x2, is_correct_ou, skip_stats=skip_update)
                
                # collect unique dates to mark as processed later
                if current_date not in dates_in_file:
                    dates_in_file.append(current_date)
                
                details.append({
                    'League': pred_league,
                    'Date': row['Date'],
                    'Match': f"{pred_home} vs {row['Away Team']}",
                    'Home': pred_home,
                    'Away': row['Away Team'],
                    'Score': f"{h_score}-{a_score}",
                    'Pred 1X2': pred_1x2,
                    'Actual 1X2': actual_1x2,
                    'Correct 1X2': is_correct_1x2,
                    'Pred O/U': pred_ou,
                    'Actual O/U': actual_ou,
                    'Correct O/U': is_correct_ou,
                    'Conf 1X2': row.get('Conf 1X2', ''),
                    'Conf O/U': row.get('Conf O/U', '')
                })
            else:
                if match:
                    print(f"Missed: {pred_home} (Best: {match[0]} Score: {match[1]})")
                else:
                    print(f"Missed: {pred_home} (No match found)")
                pass

        # Save cumulative stats
        self.stats_manager.save_stats()
        
        # Mark dates as processed
        for d in dates_in_file:
            self.stats_manager.mark_date_processed(d)

        # Report
        print("\n" + "="*40)
        print(f"VERIFICATION REPORT")
        print("="*40)
        print(f"Predictions Checked: {matches_found}/{len(self.preds_df)}")
        
        if matches_found > 0:
            df_res = pd.DataFrame(details)
            
            acc_1x2 = (correct_1x2 / matches_found) * 100
            acc_ou = (correct_ou / matches_found) * 100
            
            print(f"1X2 Accuracy: {acc_1x2:.2f}% ({correct_1x2}/{matches_found})")
            print(f"O/U Accuracy: {acc_ou:.2f}% ({correct_ou}/{matches_found})")
            
            # Per League Stats (Current Run)
            if 'League' in df_res.columns:
                 print("\n--- Accuracy Per League (Current Run) ---")
                 league_stats = df_res.groupby('League').agg(
                     Count=('League', 'count'),
                     Acc_1X2=('Correct 1X2', 'mean'),
                     Acc_OU=('Correct O/U', 'mean')
                 ).reset_index()
                 league_stats['Acc_1X2'] *= 100
                 league_stats['Acc_OU'] *= 100
                 print(league_stats.to_string(index=False, float_format="%.2f"))

            # Save to CSV
            if self.output_csv:
                # Convert booleans to symbols for readable columns, but KEEP original booleans for UI logic
                df_res['Correct 1X2 Label'] = df_res['Correct 1X2'].apply(lambda x: "✅" if x else "❌")
                df_res['Correct O/U Label'] = df_res['Correct O/U'].apply(lambda x: "✅" if x else "❌")
                
                df_res.to_csv(self.output_csv, index=False)
                print(f"\n[+] Detailed Verification CSV saved to: {self.output_csv}")
            
            print("\nDetailed Results:")
            print(f"{'Match':<50} {'Score':<10} {'1X2':<5} {'O/U':<5}")
            print("-" * 80)
            for d in details:
                # Truncate match name
                m_name = (d['Match'][:47] + '..') if len(d['Match']) > 47 else d['Match']
                c1 = "✅" if d['Correct 1X2'] else "❌"
                c2 = "✅" if d['Correct O/U'] else "❌"
                print(f"{m_name:<50} {d['Score']:<10} {c1:<5} {c2:<5}")
        else:
            print("No matches matched between predictions and results.")
            print("Ensure the dates match and scraper fetched results correctly.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preds", required=True, help="Path to predictions csv")
    parser.add_argument("--results", required=True, help="Path to scraper results json")
    parser.add_argument("--output", help="Path to output CSV", default=None)
    args = parser.parse_args()
    
    evaluator = Evaluator(args.preds, args.results, args.output)
    evaluator.evaluate()
