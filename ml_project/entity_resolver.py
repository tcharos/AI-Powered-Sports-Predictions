import json
import os
from rapidfuzz import process, fuzz

try:
    from team_aliases import TEAM_ALIASES
except ImportError:
    from ml_project.team_aliases import TEAM_ALIASES

class EntityResolver:
    def __init__(self, elo_file="data_sets/elo_ratings.json", mapping_file="data_sets/team_mappings.json"):
        self.elo_file = elo_file
        self.mapping_file = mapping_file
        self.elo_data = {}
        self.mappings = {}
        self._failed = set()
        self.load_data()
        
    def load_data(self):
        # team_mappings.json is gitignored deployment state; seed it from the
        # committed template so a fresh clone starts with the 553 accumulated
        # mappings instead of re-learning every club from scratch.
        try:
            from config_bootstrap import seed_all
            seed_all()
        except Exception:
            try:
                from ml_project.config_bootstrap import seed_all
                seed_all()
            except Exception:
                pass    # degrade to fuzzy matching rather than fail to load

        if os.path.exists(self.elo_file):
            with open(self.elo_file, 'r') as f:
                self.elo_data = json.load(f)
        else:
            print("Warning: ELO file not found. Run elo_scraper.py first.")
            
        if os.path.exists(self.mapping_file):
            with open(self.mapping_file, 'r') as f:
                # A failed lookup used to be cached as None and was then
                # returned forever, so a club that entered the corpus later
                # (a promoted side, a new season file) never resolved. Drop
                # them on load; the next save rewrites the file without them.
                self.mappings = {k: v for k, v in json.load(f).items() if v}
                
    def save_mappings(self):
        os.makedirs(os.path.dirname(self.mapping_file), exist_ok=True)
        with open(self.mapping_file, 'w') as f:
            json.dump(self.mappings, f, indent=4)
            
    def get_canonical_name(self, team_name):
        """
        Resolves the scraper team name to the canonical name in our database.
        Returns None if no match found.
        """
        if not team_name:
            return None
            
        # 1. Direct key match
        if team_name in self.elo_data:
            return team_name

        # 2. Hand-checked aliases (code, not cache), so they also override a
        # wrong mapping cached in team_mappings.json.
        alias = TEAM_ALIASES.get(team_name)
        if alias in self.elo_data:
            return alias

        # 3. Cached Mapping — only if the target is still a known team. A
        # target missing from elo_data (renamed, or learned against an older
        # ELO file) falls through and is re-matched.
        mapped_name = self.mappings.get(team_name)
        if mapped_name in self.elo_data:
            return mapped_name

        # 4. Fuzzy Match
        if team_name in self._failed:
            return None
        choices = list(self.elo_data.keys())
        if not choices:
            return None
            
        match = process.extractOne(team_name, choices, scorer=fuzz.token_set_ratio)
        
        if match and match[1] >= 80:
            best_match = match[0]
            self.mappings[team_name] = best_match
            self.save_mappings()
            return best_match
        # A miss is remembered for this run only, never persisted: the corpus
        # gains teams every season and the next run should try again.
        self._failed.add(team_name)
        return None

    def get_elo(self, team_name):
        canon = self.get_canonical_name(team_name)
        if canon:
            return self.elo_data.get(canon)
        return None

if __name__ == "__main__":
    # Test
    resolver = EntityResolver()
    test_teams = ["Real Madrid", "Man City", "Barca", "Liverpool FC"]
    for t in test_teams:
        elo = resolver.get_elo(t)
        print(f"{t}: {elo}")
