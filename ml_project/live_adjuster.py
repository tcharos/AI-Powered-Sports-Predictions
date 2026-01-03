import numpy as np

class LiveAdjuster:
    """
    Adjusts pre-match probabilities based on live match statistics and game state.
    """
    def __init__(self):
        # Heuristic Weights
        self.WEIGHT_XG = 1.5
        self.WEIGHT_SOG = 0.5
        self.WEIGHT_POSSESSION = 0.05
        
        # Thresholds
        self.DOMINANCE_THRESHOLD = 1.5 # Significant advantage
        
    def adjust_probabilities(self, pre_probs, live_stats, minute, current_score):
        """
        Adjusts probabilities.
        
        Args:
            pre_probs (dict): {'home': 0.45, 'draw': 0.30, 'away': 0.25}
            live_stats (dict): {
                'xg_home': 1.2, 'xg_away': 0.4,
                'shots_home': 10, 'shots_away': 2,
                'possession_home': 60, 'possession_away': 40
            }
            minute (int): Current minute (0-90+)
            current_score (str): "1-0", "0-0", etc.
            
        Returns:
            dict: Adjusted probabilities {'home': ..., 'draw': ..., 'away': ...}
        """
        
        # Parse Score
        try:
            h_score, a_score = map(int, current_score.split('-'))
        except:
            return pre_probs # Fail safe
            
        # 1. Calculate Dominance Score (Positive = Home Dominance, Negative = Away)
        dominance = self._calculate_dominance(live_stats)
        
        # 2. Base Adjustment on Game State (Time Decay)
        # As time passes, the probability of the CURRENT outcome increases.
        adjusted_probs = self._apply_time_decay(pre_probs, h_score, a_score, minute)
        
        # 3. Apply Dominance Modifier
        adjusted_probs = self._apply_dominance_modifier(adjusted_probs, dominance, h_score, a_score, minute)
        
        # 4. Apply Sterile Possession Penalty
        adjusted_probs = self._apply_sterile_possession(adjusted_probs, live_stats, minute)
        
        # Normalize
        total = sum(adjusted_probs.values())
        return {k: v/total for k, v in adjusted_probs.items()}
        
    def _calculate_dominance(self, stats):
        xg_diff = stats.get('xg_home', 0) - stats.get('xg_away', 0)
        shot_diff = stats.get('shots_home', 0) - stats.get('shots_away', 0)
        poss_diff = (stats.get('possession_home', 50) - stats.get('possession_away', 50)) / 10 # Scale down
        
        score = (xg_diff * self.WEIGHT_XG) + (shot_diff * self.WEIGHT_SOG) + (poss_diff * self.WEIGHT_POSSESSION)
        return score

    def _apply_time_decay(self, probs, h_score, a_score, minute):
        """
        Shifts probabilities toward the current result.
        """
        # Identify current winning state
        curr_winner = 'draw'
        if h_score > a_score: curr_winner = 'home'
        elif a_score > h_score: curr_winner = 'away'
        
        new_probs = probs.copy()
        
        # 1. Immediate Goal Impact (The "Scoreboard Pressure")
        # Even at minute 0, a goal changes the baseline probability significantly.
        if curr_winner != 'draw':
            # Boost the leader immediately. 
            # If they were 0.33, they become higher.
            # Reduced aggressiveness: 0.25 -> 0.15 -> 0.08
            initial_boost = 0.08
            new_probs[curr_winner] = min(0.95, new_probs[curr_winner] + initial_boost)
            
            # Renormalize immediately to keep math sane before decay
            total = sum(new_probs.values())
            for k in new_probs:
                new_probs[k] /= total

        # 2. Time Decay
        # As time passes, certainty increases
        decay_factor = min(minute / 95.0, 1.0)
        # Target certainty at full time: Reduced 0.98 -> 0.92 to allow late drama potential
        target_prob = 0.92
        
        current_prob = new_probs[curr_winner]
        new_probs[curr_winner] = current_prob + (target_prob - current_prob) * decay_factor
        
        # Reduce others proportionally
        remaining_prob = 1.0 - new_probs[curr_winner]
        other_keys = [k for k in probs.keys() if k != curr_winner]
        sum_others = sum(probs[k] for k in other_keys) # Use original weights for distribution? 
        # Better to use current new_probs ratio essentially, but sum_others of new_probs is just (1-current_prob)
        # So we just scale down the others.
        
        sum_current_others = sum(new_probs[k] for k in other_keys)
        if sum_current_others > 0:
            for k in other_keys:
                new_probs[k] = (new_probs[k] / sum_current_others) * remaining_prob
        else:
            # Edge case where others were 0
            for k in other_keys: new_probs[k] = remaining_prob / len(other_keys)
        
        return new_probs
        
        return new_probs
        
    def _apply_dominance_modifier(self, probs, dominance, h_score, a_score, minute):
        """
        Boosts probable winner based on dominance if they haven't secured the win yet.
        """
        new_probs = probs.copy()
        
        # --- 1. PRESSURE COOKER (xG divergence) ---
        # If drawing late (60+) but one team has huge xG advantage (> 1.0 diff), 
        # probability of them winning should be significantly higher than standard dominance.
        # "Knocking on the door"
        
        # Calculate raw xG diff roughly from dominance (or pass strictly? Let's infer from dominance approx or just use dominance)
        # Dominance ~ xG*1.5 + Shots*0.5. 
        # A dominance of 3.0 usually means xG diff ~1.0+ and shot diff ~5+.
        
        PRESSURE_THRESHOLD = 2.5
        
        # Case: Drawing but Home Piling Pressure
        if h_score == a_score and dominance > PRESSURE_THRESHOLD and minute > 55:
            # Massive Boost -> Reduced
            boost = 0.15
            new_probs['home'] += boost
            new_probs['draw'] -= (boost * 0.7)
            new_probs['away'] -= (boost * 0.3)
            
        # Case: Drawing but Away Piling Pressure
        elif h_score == a_score and dominance < -PRESSURE_THRESHOLD and minute > 55:
            boost = 0.15
            new_probs['away'] += boost
            new_probs['draw'] -= (boost * 0.7)
            new_probs['home'] -= (boost * 0.3)

        # --- 2. STANDARD DOMINANCE ---
        elif h_score == a_score and dominance > self.DOMINANCE_THRESHOLD:
            # Standard Boost -> Reduced
            boost = 0.07 * (dominance / 2.0)
            new_probs['home'] += boost
            new_probs['draw'] -= (boost / 2)
            new_probs['away'] -= (boost / 2)
            
        elif h_score == a_score and dominance < -self.DOMINANCE_THRESHOLD:
            boost = 0.07 * (abs(dominance) / 2.0)
            new_probs['away'] += boost
            new_probs['draw'] -= (boost / 2)
            new_probs['home'] -= (boost / 2)
            
        # --- 3. LATE EQUALIZER POTENTIAL ---
        # Home Losing 0-1 but Dominating
        if (a_score - h_score) == 1 and dominance > self.DOMINANCE_THRESHOLD and minute > 60:
             new_probs['draw'] += 0.08
             new_probs['away'] -= 0.08
             
        # Away Losing 1-0 but Dominating
        if (h_score - a_score) == 1 and dominance < -self.DOMINANCE_THRESHOLD and minute > 60:
             new_probs['draw'] += 0.08
             new_probs['home'] -= 0.08
             
        # Clamp values 0-1
        for k in new_probs:
            new_probs[k] = max(0.01, min(0.99, new_probs[k]))
            
        return new_probs

    def _apply_sterile_possession(self, probs, live_stats, minute):
        """
        Penalize teams with high possession but low xG (Ineffective).
        """
        if minute < 45: return probs
        
        new_probs = probs.copy()
        
        poss_h = live_stats.get('possession_home', 50)
        poss_a = live_stats.get('possession_away', 50)
        xg_h = live_stats.get('xg_home', 0)
        xg_a = live_stats.get('xg_away', 0)
        
        # Home Sterile: >65% poss, <0.3 xG (at 45m+)
        if poss_h > 65 and xg_h < 0.4:
            penalty = 0.08
            new_probs['home'] -= penalty
            new_probs['draw'] += (penalty * 0.6)
            new_probs['away'] += (penalty * 0.4) # Counter attack risk
            
        # Away Sterile
        if poss_a > 65 and xg_a < 0.4:
            penalty = 0.08
            new_probs['away'] -= penalty
            new_probs['draw'] += (penalty * 0.6)
            new_probs['home'] += (penalty * 0.4)
            
        return new_probs
