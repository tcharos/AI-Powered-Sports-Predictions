import pandas as pd
import numpy as np

class EloTracker:
    def __init__(self, k_factor=20, start_rating=1500):
        self.k_factor = k_factor
        self.start_rating = start_rating
        self.ratings = {}  # team -> rating

    def get_rating(self, team):
        return self.ratings.get(team, self.start_rating)

    def expected_result(self, rating_a, rating_b):
        """
        Calculate expected score for A when playing B.
        Formula: 1 / (1 + 10^((Rb - Ra) / 400))
        """
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    def update_ratings(self, home_team, away_team, goal_diff, result_home):
        """
        Update ratings based on match result.
        result_home: 1 (Win), 0.5 (Draw), 0 (Loss)
        """
        r_home = self.get_rating(home_team)
        r_away = self.get_rating(away_team)

        we_home = self.expected_result(r_home, r_away)
        
        # Goal difference multiplier (G)
        # 1 for draw or 1 goal win
        # For 2+ goals: G = (11 + N)/8 ? Or simpler?
        # World Football Elo Ratings uses:
        # G=1 if draw or 1 goal diff
        # G=1.5 if 2 goal diff
        # G= (11+N)/8 if 3+ goal diff
        
        abs_gd = abs(goal_diff)
        if abs_gd <= 1:
            G = 1
        elif abs_gd == 2:
            G = 1.5
        else:
            G = (11 + abs_gd) / 8

        # Calculate point exchange
        # P = K * G * (W - We)
        p_exchange = self.k_factor * G * (result_home - we_home)

        self.ratings[home_team] = r_home + p_exchange
        self.ratings[away_team] = r_away - p_exchange

    def process_history(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Iterates through the dataframe (must be sorted by date) and adds ELO ratings.
        Returns the dataframe with 'H_elo' and 'A_elo' columns.
        """
        # Ensure sorted
        # df = df.sort_values('date') # Assume caller sorts or we sort, let's sort to be safe but check if 'date' exists
        
        h_elos = []
        a_elos = []
        
        print("Calculating ELO ratings...")
        
        # Iterate over rows
        # Using itertuples for speed
        for row in df.itertuples():
            home = row.home_team
            away = row.away_team
            fthg = row.FTHG
            ftag = row.FTAG
            
            # Record CURRENT ratings (before match)
            h_elos.append(self.get_rating(home))
            a_elos.append(self.get_rating(away))
            
            # Determine Result index
            if fthg > ftag:
                res = 1.0
            elif fthg == ftag:
                res = 0.5
            else:
                res = 0.0
            
            # Update
            self.update_ratings(home, away, fthg - ftag, res)
            
        df = df.copy()
        df['H_elo'] = h_elos
        df['A_elo'] = a_elos
        
        return df
