"""Shared Euroleague helpers: paths + a stable integer team-id registry.

The feature engineering / ELO code keys teams by an integer ``teamId`` (NBA
inherited this from stats.nba.com). Euroleague clubs are identified by short
codes ("MAD", "BAR", "PAN", …), so we assign each ``(competition, club_code)``
pair a stable integer, persisted to ``data_sets/Euroleague/team_ids.json`` so
the id never changes across rebuilds (ELO caches reference it).

ELO ladders are kept separate per competition (a club's Euroleague rating ≠ its
EuroCup rating — different opponent pools), so the id is namespaced by
competition: the same club gets different ids in 'E' vs 'U'.
"""

import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_REPO, "data_sets", "Euroleague")
RAW_DIR = os.path.join(DATA_DIR, "raw")
CORPUS = os.path.join(DATA_DIR, "team_game_stats.csv")
TEAM_IDS = os.path.join(DATA_DIR, "team_ids.json")

COMPETITIONS = ("E", "U")  # E = Euroleague, U = EuroCup
COMPETITION_NAMES = {"E": "Euroleague", "U": "EuroCup"}


def _load_registry():
    if os.path.exists(TEAM_IDS):
        return json.load(open(TEAM_IDS))
    return {}


class TeamIdRegistry:
    """Stable ``(competition, club_code) -> int`` map, persisted to disk.

    Ids start at 1 and only ever grow (append-only) so existing ids — and the
    ELO caches that reference them — stay valid across corpus rebuilds.
    """

    def __init__(self, path=TEAM_IDS):
        self.path = path
        self._map = _load_registry()
        self._dirty = False

    @staticmethod
    def _key(competition, code):
        return f"{competition}:{code}"

    def get(self, competition, code):
        k = self._key(competition, code)
        if k not in self._map:
            self._map[k] = (max(self._map.values()) + 1) if self._map else 1
            self._dirty = True
        return self._map[k]

    def save(self):
        if self._dirty:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            json.dump(self._map, open(self.path, "w"), indent=2, ensure_ascii=False)
            self._dirty = False
