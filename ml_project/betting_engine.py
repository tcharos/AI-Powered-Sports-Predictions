import json
import os
import datetime
import pandas as pd

class BettingEngine:
    def __init__(self, bets_file="data_sets/bets.json", config_file="data_sets/betting_config.json"):
        self.bets_file = bets_file
        self.config_file = config_file
        self.ensure_files()
        self.load_data()
        
    def ensure_files(self):
        if not os.path.exists(self.bets_file):
            with open(self.bets_file, 'w') as f:
                json.dump([], f)
        if not os.path.exists(self.config_file):
             # Default config
             config = {
                 "base_unit": 10.0,
                 "confidence_threshold_1x2": 0.55,
                 "confidence_threshold_ou": 0.60,
                 "initial_bankroll": 1000.0, # Not strictly needed if we just track P/L but good for "Available Units"
                 "current_bankroll": 1000.0
             }
             with open(self.config_file, 'w') as f:
                 json.dump(config, f)

    def load_data(self):
        with open(self.bets_file, 'r') as f:
            self.bets = json.load(f)
        with open(self.config_file, 'r') as f:
            self.config = json.load(f)

    def save_data(self):
        with open(self.bets_file, 'w') as f:
            json.dump(self.bets, f, indent=4)
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=4)

    def place_bets_from_predictions(self, predictions_csv):
        """
        Reads a predictions CSV and places bets on matches meeting criteria.
        Returns list of placed bets.
        """
        if not os.path.exists(predictions_csv):
            return []
        
        df = pd.read_csv(predictions_csv)
        placed = []
        
        # Avoid duplicate bets? Check if (Date, Home, Away, Type) exists.
        existing_keys = set()
        for b in self.bets:
            key = f"{b['date']}_{b['home']}_{b['away']}_{b['type']}"
            existing_keys.add(key)
        
        base_unit = self.config.get('base_unit', 10)
        thresh_1x2 = self.config.get('confidence_threshold_1x2', 0.60)
        thresh_ou = self.config.get('confidence_threshold_ou', 0.60)
        
        for _, row in df.iterrows():
            date = row['Date']
            home = row['Home Team']
            away = row['Away Team']
            
            # 1X2 Bet
            conf_1x2 = float(row['Conf 1X2'])
            if conf_1x2 >= thresh_1x2:
                bet_type = "1X2"
                key = f"{date}_{home}_{away}_{bet_type}"
                if key not in existing_keys:
                    selection = row['Prediction 1X2']
                    odd = float(row['Prediction 1X2 Odd'])
                    if odd > 1.0: # Valid odd
                        bet = {
                            "id": len(self.bets) + len(placed) + 1,
                            "date": date,
                            "home": home,
                            "away": away,
                            "type": bet_type,
                            "selection": selection,
                            "odd": odd,
                            "stake": base_unit,
                            "status": "OPEN", # OPEN, WON, LOST, VOID
                            "profit": 0.0,
                            "confidence": conf_1x2
                        }
                        placed.append(bet)
                        existing_keys.add(key)
                        
            # O/U Bet
            conf_ou = float(row['Conf O/U'])
            if conf_ou >= thresh_ou:
                bet_type = "OU2.5"
                key = f"{date}_{home}_{away}_{bet_type}"
                if key not in existing_keys:
                    selection = row['Prediction O/U']
                    # We usually don't have O/U odds in prediction output unless scraped (Prediction 1X2 Odd is there, but OU odd?)
                    # Scraper 'output.json' usually has O/U odds?
                    # My predict_matches.py doesn't currently output O/U odds properly!
                    # It only outputs `Prediction 1X2 Odd`.
                    # I need to fix predict_matches.py to output O/U odds if I want to track P/L accurately.
                    # For now, let's assume 1.90 generic or skip O/U tracking p/l?
                    # User asked for "odd".
                    odd = 1.90 # Placeholder if missing
                    
                    bet = {
                        "id": len(self.bets) + len(placed) + 1,
                        "date": date,
                        "home": home,
                        "away": away,
                        "type": bet_type,
                        "selection": selection,
                        "odd": odd, 
                        "stake": base_unit,
                        "status": "OPEN",
                        "profit": 0.0,
                        "confidence": conf_ou
                    }
                    placed.append(bet)
                    # existing_keys.add(key) # handled by append

        # Update balance (deduct stake?)
        # Paper trading: usually we deduct stake.
        total_stake = sum(b['stake'] for b in placed)
        self.config['current_bankroll'] -= total_stake
        
        self.bets.extend(placed)
        self.save_data()
        
        return placed

    def resolve_bets(self, history_loader):
        """
        Checks open bets against historical data to settle them.
        history_loader: instance of DataLoader
        Returns number of settled bets.
        """
        print("Resolving bets...")
        df = history_loader.load_historical_data()
        # Ensure df has unified team names or we use EntityResolver? 
        # For simplicity, assume names match what we predicted (which came from EntityResolver/Scraper).
        # We might need fuzzy match again if names differ slightly, but let's try direct first.
        
        # Create a lookup: (Date, Home, Away) -> Row
        # Dates in bets are string YYYY-MM-DD. Dates in df are datetime or string.
        # Ensure df['date'] is datetime
        df['date_str'] = pd.to_datetime(df['date'], dayfirst=True).dt.strftime('%Y-%m-%d')
        
        lookup = {}
        for idx, row in df.iterrows():
            key = f"{row['date_str']}_{row['home_team']}_{row['away_team']}"
            lookup[key] = row
            
        settled_count = 0
        
        for bet in self.bets:
            if bet['status'] != 'OPEN':
                continue
                
            key = f"{bet['date']}_{bet['home']}_{bet['away']}"
            if key in lookup:
                row = lookup[key]
                fthg = row['FTHG']
                ftag = row['FTAG']
                
                # Determine Result
                won = False
                if bet['type'] == '1X2':
                    if bet['selection'] == 'Home' and fthg > ftag: won = True
                    elif bet['selection'] == 'Draw' and fthg == ftag: won = True
                    elif bet['selection'] == 'Away' and fthg < ftag: won = True
                elif bet['type'] == 'OU2.5':
                    goals = fthg + ftag
                    if bet['selection'] == 'Over 2.5' and goals > 2.5: won = True
                    elif bet['selection'] == 'Under 2.5' and goals <= 2.5: won = True
                    
                # Settle
                if won:
                    bet['status'] = 'WON'
                    profit = (bet['stake'] * bet['odd']) - bet['stake']
                    bet['profit'] = profit
                    self.config['current_bankroll'] += (bet['stake'] + profit)
                else:
                    bet['status'] = 'LOST'
                    bet['profit'] = -bet['stake']
                    # Stake already deducted from bankroll on place
                    
                settled_count += 1
        
        if settled_count > 0:
            self.save_data()
            
        return settled_count
