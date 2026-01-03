from rapidfuzz import process, fuzz
import json
import os

class TeamMapper:
    def __init__(self, historical_teams: list, map_file: str = "models/team_map.json"):
        self.historical_teams = historical_teams
        self.map_file = map_file
        self.mapping = {}
        self.load_mapping()

    def load_mapping(self):
        if os.path.exists(self.map_file):
            with open(self.map_file, 'r') as f:
                self.mapping = json.load(f)

    def save_mapping(self):
        with open(self.map_file, 'w') as f:
            json.dump(self.mapping, f, indent=4)

    def get_historical_name(self, scraper_name: str) -> str:
        """
        Returns the historical name for a scraper name.
        If mapped, returns mapped value.
        If not mapped, finds best fuzzy match.
        """
        # Check explicit mapping first
        if scraper_name in self.mapping:
            return self.mapping[scraper_name]

        # Exact match check
        if scraper_name in self.historical_teams:
            return scraper_name

        # Fuzzy matching
        match = process.extractOne(scraper_name, self.historical_teams, scorer=fuzz.ratio)
        if match:
            best_match, score, _ = match
            if score >= 80: # High confidence threshold
                self.mapping[scraper_name] = best_match
                self.save_mapping() # Persist mapping
                return best_match
        
        # If confidence is low, return None or Original name?
        # Returning None forces manual intervention or skips.
        # Returning best match < 80 might be risky.
        print(f"Warning: Low confidence match for '{scraper_name}' (Best: {match})")
        return None 
