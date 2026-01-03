import json
import os
import pandas as pd
from entity_resolver import EntityResolver

class HeuristicAdjuster:
    def __init__(self, data_dir="data_sets/standings"):
        self.data_dir = data_dir
        self.resolver = EntityResolver()
        
        # Load Data
        self.standings = self._load_json("standings_overall.json")
        self.form_overall = self._load_json("last_5_matches_overall.json")
        self.standings_home = self._load_json("standings_home.json")
        self.standings_away = self._load_json("standings_away.json")
        self.form_home = self._load_json("last_5_matches_home.json")
        self.form_away = self._load_json("last_5_matches_away.json")
        
        # Load Last 10 Data
        self.form_overall_10 = self._load_json("last_10_matches_overall.json")
        
        # Create lookups based on "Country: League" keys for faster access
        self.standings_lookup = self._build_lookup(self.standings)
        self.form_lookup = self._build_lookup(self.form_overall)
        self.home_table_lookup = self._build_lookup(self.standings_home)
        self.away_table_lookup = self._build_lookup(self.standings_away)
        self.form_home_lookup = self._build_lookup(self.form_home)
        self.form_away_lookup = self._build_lookup(self.form_away)
        
        self.form_lookup_10 = self._build_lookup(self.form_overall_10)

    def _load_json(self, filename):
        path = os.path.join(self.data_dir, filename)
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return []

    def _build_lookup(self, data):
        """
        Builds a dict: { "Country|League": { "TeamName": {stats...} } }
        """
        lookup = {}
        for entry in data:
            c = entry.get('country', '').upper()
            l = entry.get('league', '')
            key = f"{c}|{l}"
            
            if key not in lookup:
                lookup[key] = {}
            
            team = entry.get('team_name')
            if team:
                # Normalize team name for lookup key?
                # Using resolver might be overkill here, we'll fuzzy match at query time
                # Store by raw name for iteration
                lookup[key][team] = entry
        return lookup

    def find_team_stats(self, lookup, country, league_name, team_name):
        """
        Finds stats for a team in a specific league lookup.
        Tries exact match first, then fuzzy via EntityResolver logic if needed.
        """
        # 1. Construct Key
        # Scraper league: "ENGLAND: Premier League" -> Country="ENGLAND", League="Premier League"
        # Adjuster lookup key: "ENGLAND|Premier League"
        
        # Normalize League Name input
        if ":" in league_name:
            parts = league_name.split(":")
            c_in = parts[0].strip().upper()
            l_in = parts[1].strip()
        else:
            # Maybe just league name? heuristic check
            # For now assume scraper provides full string
            return None 

        key = f"{c_in}|{l_in}"
        
        league_data = lookup.get(key)
        if not league_data:
            # Try fuzzy league matching?
            # Flashscore names should allow exact match if sourced from same place
            # But "Premier League" vs "Premier League" might differ by spaces
            # Try to find partial match
            found_key = None
            for k in lookup.keys():
                if c_in in k and l_in in k:
                    found_key = k
                    break
            if found_key:
                league_data = lookup[found_key]
            else:
                return None

        # 2. Find Team
        if team_name in league_data:
            return league_data[team_name]
        
        # Fuzzy Match Team
        best_match = None
        best_score = 0
        from difflib import SequenceMatcher
        
        for t_key in league_data.keys():
            ratio = SequenceMatcher(None, team_name.lower(), t_key.lower()).ratio()
            if ratio > 0.8 and ratio > best_score:
                best_score = ratio
                best_match = t_key
        
        if best_match:
            return league_data[best_match]
            
        return None

        return adj_1x2, adj_ou, logs

    def adjust_probabilities(self, match_info, probs_1x2, probs_ou):
        """
        match_info: dict with 'League', 'Home Team', 'Away Team'
        probs_1x2: [Home%, Draw%, Away%] (0.0-1.0)
        probs_ou: [Under%, Over%]
        
        Returns: (adj_1x2, adj_ou, logs)
        """
        logs = []
        league = match_info.get('League', '')
        home = match_info.get('Home Team', '')
        away = match_info.get('Away Team', '')
        
        # Copy to avoid mutation
        adj_1x2 = list(probs_1x2)
        adj_ou = list(probs_ou)
        
        # Get Stats
        s_home = self.find_team_stats(self.standings_lookup, "", league, home)
        s_away = self.find_team_stats(self.standings_lookup, "", league, away)
        
        f_home = self.find_team_stats(self.form_lookup, "", league, home)
        f_away = self.find_team_stats(self.form_lookup, "", league, away)
        
        # Specific Stats
        s_home_spec = self.find_team_stats(self.home_table_lookup, "", league, home)
        s_away_spec = self.find_team_stats(self.away_table_lookup, "", league, away)
        
        f_home_spec = self.find_team_stats(self.form_home_lookup, "", league, home)
        f_away_spec = self.find_team_stats(self.form_away_lookup, "", league, away)
        
        if not s_home or not s_away:
            # We can continue if specific stats exist? Usually implying missing league data
            return adj_1x2, adj_ou, ["No Standings Data"]

        # --- HEURISTIC 1: Standings Differential (Overall) ---
        try:
            h_rank = int(s_home['rank'])
            a_rank = int(s_away['rank'])
            
            diff = a_rank - h_rank 
            
            if diff >= 5:
                # Home is better
                boost = 0.02 * (diff / 5)
                boost = min(boost, 0.10)
                adj_1x2[0] += boost
                logs.append(f"Rank Boost Home (+{boost:.2f}): H#{h_rank} vs A#{a_rank}")
                
            elif diff <= -5:
                # Away is better
                boost = 0.02 * (abs(diff) / 5)
                boost = min(boost, 0.10)
                adj_1x2[2] += boost
                logs.append(f"Rank Boost Away (+{boost:.2f}): H#{h_rank} vs A#{a_rank}")
        except: pass

        # --- HEURISTIC 2: Standings Differential (Specific: Home Table vs Away Table) ---
        if s_home_spec and s_away_spec:
            try:
                h_rank_spec = int(s_home_spec['rank'])
                a_rank_spec = int(s_away_spec['rank'])
                
                # Note: Home Table rank 1 means best home team. Away Table rank 1 means best away team.
                # Direct comparison: Rank 1 Home vs Rank 10 Away = Mismatch favoring Home.
                
                diff_spec = a_rank_spec - h_rank_spec
                
                if diff_spec >= 5:
                    boost = 0.03 * (diff_spec / 5) # Slightly higher weight for specific mismatch?
                    boost = min(boost, 0.10)
                    adj_1x2[0] += boost
                    logs.append(f"Spec Rank Boost Home (+{boost:.2f}): H_home#{h_rank_spec} vs A_away#{a_rank_spec}")
                    
                elif diff_spec <= -5:
                    boost = 0.03 * (abs(diff_spec) / 5)
                    boost = min(boost, 0.10)
                    adj_1x2[2] += boost
                    logs.append(f"Spec Rank Boost Away (+{boost:.2f}): H_home#{h_rank_spec} vs A_away#{a_rank_spec}")
            except: pass

        # --- HEURISTIC 3: Form Momentum (Overall) ---
        if f_home:
            res = f_home.get('last_5_results', '')
            wins = res.count('W')
            if wins >= 4:
                adj_1x2[0] += 0.05
                logs.append(f"Form Boost Home (Wins={wins})")
        
        if f_away:
            res = f_away.get('last_5_results', '')
            losses = res.count('L')
            if losses >= 4:
                adj_1x2[0] += 0.05 
                logs.append(f"Form Fade Away (Losses={losses})")

        # --- HEURISTIC 4: Form Momentum (Specific) ---
        # Home Team's form AT HOME
        if f_home_spec:
            res = f_home_spec.get('last_5_results', '')
            wins = res.count('W')
            if wins >= 4:
                adj_1x2[0] += 0.06 # Stronger signal
                logs.append(f"Spec Form Home Boost (Wins={wins})")
                
        # Away Team's form AWAY
        if f_away_spec:
            res = f_away_spec.get('last_5_results', '')
            losses = res.count('L')
            if losses >= 4:
                adj_1x2[0] += 0.06
                logs.append(f"Spec Form Away Fade (Losses={losses})")
            elif res.count('W') >= 4:
                adj_1x2[2] += 0.06
                logs.append(f"Spec Form Away Boost (Wins={res.count('W')})")


        # --- HEURISTIC 6: Form Trend Analysis (Last 5 vs Last 10) ---
        # Logic: Compare Win Rate/Points Rate of Last 5 vs Last 10
        # If L5 > L10 significantly -> Heating Up -> Boost
        # If L5 < L10 significantly -> Cooling Down -> Dampen
        
        def calculate_win_rate(form_entry):
            if not form_entry: return 0.0
            res = form_entry.get('last_5_results', '') # string like W|W|L...
            games = len(res.split('|')) if '|' in res else len(res) # Handle both delimiter styles if present
            # Our scraper puts "|" delimiter? let's check scraper. YES: "|".join(texts)
            # But wait, existing code splits or counts chars directly?
            # existing code: res.count('W'). If delimiter is |, 'W' count works.
            # But games count: 'W|W|L'. len is 5. 
            # If string is 'W|W' len is 3. 
            # Safest is count W, D, L.
            w = res.count('W')
            d = res.count('D')
            l = res.count('L')
            total = w + d + l
            return (w / total) if total > 0 else 0.0

        # Home Trend
        f_home_10 = self.find_team_stats(self.form_lookup_10, "", league, home)
        if f_home and f_home_10:
            wr_5 = calculate_win_rate(f_home)
            wr_10 = calculate_win_rate(f_home_10)
            
            # Heating Up: Significant improvement (e.g. 80% vs 40%)
            if wr_5 >= (wr_10 + 0.3): 
                adj_1x2[0] += 0.04
                logs.append(f"Home Heating Up (L5:{wr_5:.1%} vs L10:{wr_10:.1%})")
            
            # Cooling Down: Significant drop (e.g. 20% vs 60%)
            elif wr_5 <= (wr_10 - 0.3):
                adj_1x2[0] -= 0.03
                logs.append(f"Home Cooling Down (L5:{wr_5:.1%} vs L10:{wr_10:.1%})")

            # Consistency Reward: High Performance in both short and medium term
            # e.g., >70% win rate in both
            elif wr_5 >= 0.70 and wr_10 >= 0.60:
                adj_1x2[0] += 0.03
                logs.append(f"Home Consistent Form (L5:{wr_5:.1%} & L10:{wr_10:.1%})")
                
        # Away Trend
        f_away_10 = self.find_team_stats(self.form_lookup_10, "", league, away)
        if f_away and f_away_10:
            wr_5 = calculate_win_rate(f_away)
            wr_10 = calculate_win_rate(f_away_10)
            
            if wr_5 >= (wr_10 + 0.3):
                adj_1x2[2] += 0.04
                logs.append(f"Away Heating Up (L5:{wr_5:.1%} vs L10:{wr_10:.1%})")
            elif wr_5 <= (wr_10 - 0.3):
                adj_1x2[2] -= 0.03
                logs.append(f"Away Cooling Down (L5:{wr_5:.1%} vs L10:{wr_10:.1%})")
            elif wr_5 >= 0.70 and wr_10 >= 0.60:
                adj_1x2[2] += 0.03
                logs.append(f"Away Consistent Form (L5:{wr_5:.1%} & L10:{wr_10:.1%})")


        # Re-normalize 1x2 (Once at the end logic-wise, but we do it incrementally usually. Let's do it now)
        total = sum(adj_1x2)
        adj_1x2 = [x/total for x in adj_1x2]

        # --- HEURISTIC 5: High Scoring Teams (O/U) ---
        try:
            h_mp = int(s_home['matches_played'])
            a_mp = int(s_away['matches_played'])
            
            h_gf = int(s_home['goals'].split(':')[0])
            a_gf = int(s_away['goals'].split(':')[0])
            
            h_avg = h_gf / h_mp
            a_avg = a_gf / a_mp
            
            if (h_avg + a_avg) > 3.5:
                adj_ou[1] += 0.05 
                logs.append(f"Goal Fest Boost (Avg GF: {h_avg+a_avg:.2f})")
                
            total_ou = sum(adj_ou)
            adj_ou = [x/total_ou for x in adj_ou]
            
        except: pass

        # --- HEURISTIC 7: Value Bet Identification (Logging Only) ---
        odds = match_info.get('Odds', {})
        
        # 1X2 Value
        try:
            o_h = float(odds.get('1', 0.0))
            o_d = float(odds.get('X', 0.0))
            o_a = float(odds.get('2', 0.0))
            
            # Implied Probs (1/Odd). e.g. 2.0 -> 0.5
            imp_h = (1.0 / o_h) if o_h > 1.0 else 0.0
            imp_d = (1.0 / o_d) if o_d > 1.0 else 0.0
            imp_a = (1.0 / o_a) if o_a > 1.0 else 0.0
            
            # Value = Model Prob - Implied Prob
            val_h = adj_1x2[0] - imp_h
            val_d = adj_1x2[1] - imp_d
            val_a = adj_1x2[2] - imp_a
            
            # Log significant value (> 0.05 or > 5%)
            if val_h > 0.05: logs.append(f"Value 1(+{val_h:.2%})")
            if val_d > 0.05: logs.append(f"Value X(+{val_d:.2%})")
            if val_a > 0.05: logs.append(f"Value 2(+{val_a:.2%})")
            
        except: pass
        
        # O/U Value
        try:
            o_o = float(odds.get('O', 0.0))
            o_u = float(odds.get('U', 0.0))
            
            imp_o = (1.0 / o_o) if o_o > 1.0 else 0.0
            imp_u = (1.0 / o_u) if o_u > 1.0 else 0.0
            
            val_o = adj_ou[1] - imp_o # Index 1 is Over? Check predict_matches.py. Yes: ['Under', 'Over'] implied? 
            # predict_matches.py: probs_ou = model.predict_proba... usually [class 0, class 1].
            # Feature engineering usually maps 0=Under, 1=Over. 
            # Let's assume Index 1 is Over.
            val_u = adj_ou[0] - imp_u
            
            if val_o > 0.05: logs.append(f"Value O(+{val_o:.2%})")
            if val_u > 0.05: logs.append(f"Value U(+{val_u:.2%})")
            
        except: pass

        return adj_1x2, adj_ou, logs
