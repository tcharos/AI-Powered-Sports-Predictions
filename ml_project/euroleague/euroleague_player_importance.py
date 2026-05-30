"""Euroleague / EuroCup per-player importance from official PIR (valuation).

Groundwork for the deferred player-availability adjuster
(EUROLEAGUE_NEXT_STEPS.md → "Player availability + PIR-impact adjuster").
This is the *importance* half — the data-backed, feed-independent piece,
analogous to football's N2 (`ml_project/availability/sofifa_importance.py`).

It does NOT adjust any prediction: the adjuster is gated on a forward
"who's out" availability feed that does not exist yet (and the season is
off). When that feed lands, the adjuster reads this table to weight each
absentee.

Source: the raw box-score CSVs already on disk
(`data_sets/Euroleague/raw/{E,U}_<season>_game_stats.csv`), whose
`local.players` / `road.players` columns carry one serialized JSON object
per player per game, including the official **valuation (PIR)**,
`timePlayed` (seconds) and `plusMinus`.

Metric (mirrors the football N2 shape):
    pir_pg     = mean PIR per game for the (competition, season, player)
    importance = max(0, pir_pg - team_replacement_level)
where the team replacement level is the **roster median pir_pg** for that
(competition, season, club) — a robust v1 proxy for "the guy who absorbs
the minutes", same choice football N2 made with squad-median OVR.

Output: `data_sets/Euroleague/player_importance.json`
    {competition: {season: {
        "players": {player_code: {name, club, games, mpg, pir_pg,
                                  plus_minus_pg, importance}},
        "replacement_by_club": {club_code: median_pir_pg},
    }}}

CLI:
    python3 ml_project/euroleague/euroleague_player_importance.py            # build
    python3 ml_project/euroleague/euroleague_player_importance.py --self-test
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import statistics
import sys
from collections import defaultdict

try:
    from euroleague_utils import EUROLEAGUE_DIR  # type: ignore
except Exception:  # pragma: no cover - fall back to a repo-relative path
    EUROLEAGUE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data_sets", "Euroleague",
    )

RAW_DIR = os.path.join(EUROLEAGUE_DIR, "raw")
OUTPUT_PATH = os.path.join(EUROLEAGUE_DIR, "player_importance.json")

# Players with fewer than this many games in a season are excluded from the
# team replacement-level median (tiny samples make the median noisy) but are
# still scored — a 2-game call-up just gets importance≈0 against the median.
_MIN_GAMES_FOR_REPLACEMENT = 5

_FNAME_RE = re.compile(r"^([EU])_(\d{4})_game_stats\.csv$")


def _parse_players(cell) -> list:
    """Parse one `local.players` / `road.players` cell → list of dicts."""
    if cell is None or (isinstance(cell, float)):
        return []
    s = str(cell).strip()
    if not s or s in ("[]", "nan"):
        return []
    try:
        obj = ast.literal_eval(s)
    except (ValueError, SyntaxError):
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            return []
    return obj if isinstance(obj, list) else []


def _accumulate_file(path: str, acc: dict) -> int:
    """Read one game_stats CSV, fold per-player rows into ``acc``.

    ``acc`` is keyed (comp, season, player_code) → running totals dict.
    Returns the number of player-game rows folded in.
    """
    import pandas as pd

    m = _FNAME_RE.match(os.path.basename(path))
    if not m:
        return 0
    comp, season = m.group(1), int(m.group(2))

    df = pd.read_csv(path)
    n = 0
    for col in ("local.players", "road.players"):
        if col not in df.columns:
            continue
        for cell in df[col]:
            for entry in _parse_players(cell):
                if not isinstance(entry, dict):
                    continue
                person = (entry.get("player") or {}).get("person") or {}
                code = person.get("code")
                if not code:
                    continue
                stats = entry.get("stats") or {}
                # Skip DNPs (no minutes → not a meaningful sample).
                tp = stats.get("timePlayed") or 0
                if not tp:
                    continue
                club = ((entry.get("player") or {}).get("club") or {}).get("code") or "?"
                key = (comp, season, code)
                rec = acc[key]
                rec["name"] = person.get("name") or rec.get("name") or code
                rec["club"] = club
                rec["games"] += 1
                rec["sum_pir"] += float(stats.get("valuation") or 0.0)
                rec["sum_sec"] += float(tp)
                rec["sum_pm"] += float(stats.get("plusMinus") or 0.0)
                n += 1
    return n


def build(output_path: str = OUTPUT_PATH) -> dict:
    """Build the importance table from every raw game_stats CSV on disk."""
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*_game_stats.csv")))
    if not files:
        raise FileNotFoundError(
            f"No *_game_stats.csv under {RAW_DIR}. Run the Phase-0 multi-season "
            "fetch (scripts/euroleague_probe/fetch_seasons.py) first."
        )

    acc: dict = defaultdict(lambda: {"name": None, "club": "?", "games": 0,
                                     "sum_pir": 0.0, "sum_sec": 0.0, "sum_pm": 0.0})
    total_rows = 0
    for path in files:
        total_rows += _accumulate_file(path, acc)

    # Per-player per-game rates.
    players_by_cs: dict = defaultdict(dict)  # (comp, season) -> {code: rec}
    for (comp, season, code), rec in acc.items():
        g = rec["games"]
        players_by_cs[(comp, season)][code] = {
            "name": rec["name"],
            "club": rec["club"],
            "games": g,
            "mpg": round(rec["sum_sec"] / 60.0 / g, 1),
            "pir_pg": round(rec["sum_pir"] / g, 2),
            "plus_minus_pg": round(rec["sum_pm"] / g, 2),
        }

    out: dict = {"E": {}, "U": {}}
    for (comp, season), players in players_by_cs.items():
        # Replacement level per club = roster median pir_pg over rotation players.
        by_club: dict = defaultdict(list)
        for p in players.values():
            if p["games"] >= _MIN_GAMES_FOR_REPLACEMENT:
                by_club[p["club"]].append(p["pir_pg"])
        replacement = {club: round(statistics.median(v), 2)
                       for club, v in by_club.items() if v}

        for p in players.values():
            repl = replacement.get(p["club"], 0.0)
            p["importance"] = round(max(0.0, p["pir_pg"] - repl), 2)

        out.setdefault(comp, {})[str(season)] = {
            "players": players,
            "replacement_by_club": replacement,
        }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2)

    n_players = sum(len(s["players"]) for c in out.values() for s in c.values())
    print(f"Built {output_path}: {total_rows} player-game rows → "
          f"{n_players} (competition, season, player) records "
          f"across {len(files)} files.")
    return out


def importance_for(table: dict, competition: str, season, player_code: str):
    """Look up one player's record. Returns the dict or None."""
    return ((table.get(competition) or {}).get(str(season)) or {}).get("players", {}).get(player_code)


def _self_test() -> int:
    """Offline sanity checks on the parser + metric (no network, no write)."""
    ok = True

    # 1. Player-cell parser handles list / empty / junk.
    assert _parse_players("[]") == []
    assert _parse_players("nan") == []
    assert _parse_players(float("nan")) == []
    sample = "[{'player': {'person': {'code': 'X1', 'name': 'DOE, J'}, " \
             "'club': {'code': 'ABC'}}, 'stats': {'valuation': 20.0, " \
             "'timePlayed': 1800, 'plusMinus': 5}}]"
    parsed = _parse_players(sample)
    assert len(parsed) == 1 and parsed[0]["stats"]["valuation"] == 20.0
    print("  [ok] player-cell parser")

    # 2. Accumulate + rate maths on a tiny synthetic frame.
    import pandas as pd
    import tempfile
    star = "{'player': {'person': {'code': 'S', 'name': 'STAR'}, 'club': {'code': 'AAA'}}, " \
           "'stats': {'valuation': 25.0, 'timePlayed': 1800, 'plusMinus': 10}}"
    def role(i):
        return ("{'player': {'person': {'code': 'R%d', 'name': 'ROLE%d'}, 'club': {'code': 'AAA'}}, "
                "'stats': {'valuation': %d.0, 'timePlayed': 1200, 'plusMinus': 0}}" % (i, i, i))
    roster = "[" + ", ".join([star] + [role(i) for i in (4, 6, 8, 10, 12)]) + "]"
    with tempfile.TemporaryDirectory() as d:
        # 6 identical games so every player clears the replacement min-games gate.
        p = os.path.join(d, "E_2099_game_stats.csv")
        pd.DataFrame({"local.players": [roster] * 6,
                      "road.players": ["[]"] * 6}).to_csv(p, index=False)
        acc = defaultdict(lambda: {"name": None, "club": "?", "games": 0,
                                   "sum_pir": 0.0, "sum_sec": 0.0, "sum_pm": 0.0})
        _accumulate_file(p, acc)
        out_path = os.path.join(d, "imp.json")
        # Build directly from this temp dir by monkeypatching RAW_DIR via glob input.
        global RAW_DIR
        _orig = RAW_DIR
        RAW_DIR = d
        try:
            table = build(out_path)
        finally:
            RAW_DIR = _orig
    star_rec = importance_for(table, "E", 2099, "S")
    assert star_rec is not None, "star record missing"
    # Replacement median over {25,4,6,8,10,12} = 9.0; star importance = 25-9 = 16.
    assert star_rec["pir_pg"] == 25.0, star_rec
    assert star_rec["importance"] == 16.0, star_rec
    r4 = importance_for(table, "E", 2099, "R4")
    assert r4["importance"] == 0.0, r4  # below-median role player → no loss
    print("  [ok] accumulate + importance maths (star imp=16.0, role imp=0.0)")

    print("SELF-TEST PASSED" if ok else "SELF-TEST FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="run offline parser + metric checks, no write")
    ap.add_argument("--output", default=OUTPUT_PATH, help="output JSON path")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    build(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
