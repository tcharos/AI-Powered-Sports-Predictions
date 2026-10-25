import json
import os
import re
import pandas as pd

# --- League-label reconciliation (2026-09-26) -------------------------------
# Standings rows are labelled from the hand-typed LEAGUE column of
# data_sets/standings_form_flashscore_direct_links.csv, while the daily slate
# is labelled from Flashscore's live DOM. The two have drifted, so the exact
# "COUNTRY|League" key lookup below missed 9 whitelisted leagues and silently
# returned the neutral (0.0, 1.0, 1.0) strength for every team in them —
# measured 2026-09-26 at 69 fixtures, 24% of all matches on record.
#
# The old substring fallback cannot rescue these: it needs the slate label to
# be contained in the standings key, and the slate label is usually the LONGER
# one ("Jupiler Pro League" is not inside "Jupiler Pro").
#
# Most of the drift is cosmetic, so normalise both sides instead of
# enumerating pairs — that also absorbs the next cosmetic rename:
#     "League Two"         -> league2     == "League 2"
#     "LaLiga" / "LaLiga 2"-> laliga(2)   == "La Liga" / "La Liga 2"
#     "2. Bundesliga"      -> bundesliga2 == "Bundesliga 2"  (leading number moved)
#     "Jupiler Pro League" -> jupilerpro  == "Jupiler Pro"   (trailing "league" cut)
_NUM_WORDS = (("one", "1"), ("two", "2"), ("three", "3"), ("four", "4"))


def _norm_league(name):
    """Canonical form of a league label for cross-source comparison."""
    s = name.lower()
    for word, digit in _NUM_WORDS:
        s = re.sub(rf"\b{word}\b", digit, s)
    s = re.sub(r"[^a-z0-9]", "", s)
    m = re.match(r"^(\d+)(.+)$", s)          # "2bundesliga" -> "bundesliga2"
    if m:
        s = m.group(2) + m.group(1)
    if s.endswith("league") and s != "league":
        s = s[:-len("league")]
    return s


# Irreducible cases — a genuinely different name, not a different spelling,
# so no normalisation can bridge them. Keyed COUNTRY|<normalised slate label>.
STANDINGS_LEAGUE_ALIASES = {
    "PORTUGAL|ligaportugal": "Liga 1",       # slate "Liga Portugal"
    # target_leagues.json carries both "ROMANIA: Liga 1" and "ROMANIA:
    # Superliga" for the same competition. Flashscore's page and every slate
    # seen so far say Superliga, which is what the crawl row is labelled, so
    # this only covers the older label resurfacing.
    "ROMANIA|liga1": "Superliga",
}


def _norm_index(keys):
    """{"COUNTRY|normalised": "COUNTRY|Actual League"} over a set of keys.

    A normalised form claimed by two different leagues of the same country is
    dropped rather than guessed — an ambiguous match would silently attach one
    league's table to another's fixtures, which is worse than no match at all.
    """
    index, clashes = {}, set()
    for k in keys:
        country, _, league = k.partition("|")
        nk = f"{country}|{_norm_league(league)}"
        if nk in index and index[nk] != k:
            clashes.add(nk)
        index[nk] = k
    for nk in clashes:
        index.pop(nk, None)
    return index


class _LeagueLookup(dict):
    """{"COUNTRY|League": {team: row}} plus a `.norm` index over its keys.

    A dict subclass so every existing caller keeps working unchanged.
    """
    norm = {}


class HeuristicAdjuster:
    """Season-to-date team strength from the current standings tables.

    The name is historical. This class used to also post-process the model's
    probabilities with hand-written rules (`adjust_probabilities`: league draw
    shrinkage and cap, rank / form / heating-up boosts, a goal-fest O/U bump).
    That layer was removed on 2026-10-25 after it measured neutral on settled
    slates (scripts/experiment_heuristic_audit.py). What remains is the
    serve-time mirror of FeatureEngineer._add_ppg_strength_features, which
    produces model FEATURES, not adjustments.
    """

    def __init__(self, data_dir="data_sets/standings"):
        self.data_dir = data_dir

        # Load Data
        self.standings = self._load_json("standings_overall.json")

        # Create lookups based on "Country: League" keys for faster access
        self.standings_lookup = self._build_lookup(self.standings)

        # Per-league scoring baseline for att/def strength
        self.league_stats = self._calculate_league_stats(self.standings)
        # Same reconciliation for the per-league baseline used by
        # get_team_strength — without it a team could resolve through the
        # normalised index while its league's avg_gf silently fell back to the
        # 1.3 cross-league prior, leaving att/def scaled against the wrong base.
        self.league_stats_norm = _norm_index(self.league_stats.keys())

    def _calculate_league_stats(self, standings_data):
        """
        Calculates average stats per league (Avg Goals For per team-game).
        Returns: { "Country|League": { "avg_gf": 1.45 } }
        """
        stats = {}
        # Group by league
        leagues = {}
        for entry in standings_data:
            c = entry.get('country', '').upper()
            l = entry.get('league', '')
            key = f"{c}|{l}"

            if key not in leagues: leagues[key] = []
            leagues[key].append(entry)

        for key, entries in leagues.items():
            total_matches = 0
            total_gf = 0

            for t in entries:
                try:
                    mp = int(t.get('matches_played', 0))
                    total_matches += mp

                    # Goals For (format "GF:GA")
                    goals_str = t.get('goals', '0:0')
                    if ':' in goals_str:
                        gf = int(goals_str.split(':')[0])
                        total_gf += gf
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue

            # League avg goals scored per team-game. 1.3 is a reasonable cross-league prior.
            avg_gf = (total_gf / total_matches) if total_matches > 0 else 1.3

            stats[key] = {
                'avg_gf': avg_gf,
            }

        return stats

    def get_team_strength(self, league_name, team_name):
        """
        Season-to-date strength for a team, derived from current standings.
        Mirrors the training-time features built in
        FeatureEngineer._add_ppg_strength_features so model inputs match.

        Returns (ppg, att_strength, def_weakness).
        Defaults to (0.0, 1.0, 1.0) when the team or league is unavailable.
        """
        entry = self.find_team_stats(self.standings_lookup, "", league_name, team_name)
        if not entry:
            return 0.0, 1.0, 1.0

        try:
            mp = int(entry.get('matches_played', 0))
            if mp <= 0:
                return 0.0, 1.0, 1.0
            pts = int(entry.get('points', 0))
            gf_str, ga_str = entry.get('goals', '0:0').split(':')[:2]
            gf, ga = int(gf_str), int(ga_str)
        except (KeyError, ValueError, TypeError, AttributeError):
            return 0.0, 1.0, 1.0

        ppg = pts / mp
        avg_gf = gf / mp
        avg_ga = ga / mp

        baseline = 1.3
        if ":" in league_name:
            parts = league_name.split(":", 1)
            c_in = parts[0].strip().upper()
            l_in = parts[1].strip()
            key = f"{c_in}|{l_in}"
            league_stat = self.league_stats.get(key)
            if not league_stat:
                # Same three-step reconciliation as find_team_stats.
                norm_key = f"{c_in}|{_norm_league(l_in)}"
                alias = STANDINGS_LEAGUE_ALIASES.get(norm_key)
                resolved = (f"{c_in}|{alias}" if alias
                            else self.league_stats_norm.get(norm_key))
                league_stat = self.league_stats.get(resolved) if resolved else None
            if not league_stat:
                for k in self.league_stats:
                    if c_in in k and l_in in k:
                        league_stat = self.league_stats[k]
                        break
            if league_stat:
                baseline = league_stat.get('avg_gf', 1.3) or 1.3

        if baseline <= 0:
            baseline = 1.3

        return ppg, avg_gf / baseline, avg_ga / baseline

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
        lookup = _LeagueLookup()
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
        lookup.norm = _norm_index(lookup.keys())
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
            # The slate label and the standings label disagree. Try, in order
            # of decreasing confidence (see _norm_league above):
            #   1. an explicit alias for a genuinely different name
            #   2. the normalised index ("LaLiga" == "La Liga"), which refuses
            #      to resolve anything ambiguous
            #   3. the original substring fallback, kept for back-compat
            norm_key = f"{c_in}|{_norm_league(l_in)}"
            alias = STANDINGS_LEAGUE_ALIASES.get(norm_key)
            if alias:
                league_data = lookup.get(f"{c_in}|{alias}")

            if not league_data:
                resolved = getattr(lookup, 'norm', {}).get(norm_key)
                if resolved:
                    league_data = lookup.get(resolved)

            if not league_data:
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
