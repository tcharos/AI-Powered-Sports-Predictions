"""euroleague-api daily refresh + fixtures for the Euroleague/EuroCup pipeline.

Mirrors ``ml_project/nba/fetch_nba_daily.py``. Two modes:

* ``append-results [--date YYYY-MM-DD]`` (default yesterday)
    Pulls the current season's schedule+box-scores for BOTH competitions (E, U),
    keeps games PLAYED on the target date, transforms them to the canonical
    long-format via ``build_corpus`` helpers, and appends to
    ``data_sets/Euroleague/team_game_stats.csv`` — idempotent (dedups on
    (gameId, teamId), keeping the freshest row).

* ``fixtures [--date YYYY-MM-DD]`` (default tomorrow)
    Writes ``data_sets/Euroleague/fixtures_<date>.json`` — the upcoming (not-yet
    -played) games on the target date, in the same shape NBA's predictor reads:
    {gameId, competition, date, home_team_id, away_team_id, home_team,
     away_team, tipoff}.

The euroleague-api ``get_game_report_single_season`` returns the FULL season
schedule (played + unplayed, with dates) so both modes derive from it; results
also need ``get_game_stats_single_season`` for the box scores. ``time.sleep(1)``
before each API call. Season code = the ending year (2026 = the 2025-26 season).

Usage::
    python3 ml_project/euroleague/fetch_euroleague_daily.py append-results [--date YYYY-MM-DD]
    python3 ml_project/euroleague/fetch_euroleague_daily.py fixtures [--date YYYY-MM-DD]
"""

import argparse
import datetime
import json
import os
import sys
import time

import pandas as pd

from euroleague_utils import DATA_DIR, CORPUS, COMPETITIONS, COMPETITION_NAMES, TeamIdRegistry
from build_corpus import rows_for_merged, finalize_rows, CANONICAL_COLS

SLEEP_S = 1.0


def _season_code(d: datetime.date) -> int:
    """Euroleague season runs ~Oct→May; euroleague-api codes it by STARTING year.

    A game in Aug–Dec belongs to season ``year``; Jan–Jul to ``year-1``.
    e.g. 2024-10-03 → 2024 (the 2024-25 season); 2026-05-24 → 2025 (2025-26).
    (Verified against the raw data: E_2024_game_report has games dated Oct 2024.)
    """
    return d.year if d.month >= 8 else d.year - 1


def _on_date(rep: pd.DataFrame, date_str: str) -> pd.Series:
    """Boolean mask: report rows whose local/utc date matches date_str."""
    col = "localDate" if "localDate" in rep.columns else "date"
    d = pd.to_datetime(rep[col], errors="coerce").dt.strftime("%Y-%m-%d")
    return d == date_str


def append_results(date_str: str) -> int:
    season = _season_code(datetime.date.fromisoformat(date_str))
    print(f"[append-results] {date_str} (season {season}) — both competitions")
    from euroleague_api.game_stats import GameStats
    new_parts = []
    for comp in COMPETITIONS:
        gs = GameStats(competition=comp)
        try:
            time.sleep(SLEEP_S)
            rep = gs.get_game_report_single_season(season)  # cheap schedule (bulk)
        except Exception as e:
            print(f"  ! {COMPETITION_NAMES[comp]}: report fetch failed ({type(e).__name__}: {e})")
            continue
        played = rep[(rep["played"] == True) & _on_date(rep, date_str)].copy()  # noqa: E712
        played = played.dropna(subset=["local.score", "road.score"])
        if played.empty:
            print(f"  {COMPETITION_NAMES[comp]}: no finished games on {date_str}")
            continue
        # Fetch box scores for ONLY the day's gamecodes (not the whole season).
        stat_rows = []
        for gc in played["Gamecode"].astype(int):
            try:
                time.sleep(SLEEP_S)
                stat_rows.append(gs.get_game_stats(season, int(gc)))
            except Exception as e:
                print(f"  ! {COMPETITION_NAMES[comp]} game {gc}: stats fetch failed ({e})")
        if not stat_rows:
            print(f"  {COMPETITION_NAMES[comp]}: box scores not available yet")
            continue
        sts = pd.concat(stat_rows, ignore_index=True)
        merged = played.merge(sts, on=["Season", "Gamecode"], how="inner", suffixes=("", "_stats"))
        if merged.empty:
            print(f"  {COMPETITION_NAMES[comp]}: results not in box-score feed yet")
            continue
        new_parts.append(rows_for_merged(merged, comp))
        print(f"  {COMPETITION_NAMES[comp]}: {len(merged)} finished game(s)")

    if not new_parts:
        print("[append-results] nothing to append.")
        return 0
    new = finalize_rows(pd.concat(new_parts, ignore_index=True))

    if os.path.exists(CORPUS):
        existing = pd.read_csv(CORPUS, low_memory=False)
        before = len(existing)
        merged = pd.concat([existing, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["gameId", "teamId"], keep="last")
        added = len(merged) - before
    else:
        merged, added = new, len(new)
    merged = (merged.sort_values(["date", "gameId", "home"], ascending=[True, True, False])
              .reset_index(drop=True))
    merged.to_csv(CORPUS, index=False)
    print(f"[append-results] +{added} new team-rows → {CORPUS} ({len(merged):,} total)")
    return 0


def fixtures(date_str: str) -> int:
    season = _season_code(datetime.date.fromisoformat(date_str))
    print(f"[fixtures] {date_str} (season {season}) — both competitions")
    reg = TeamIdRegistry()
    out = []
    for comp in COMPETITIONS:
        try:
            from euroleague_api.game_stats import GameStats
            time.sleep(SLEEP_S)
            rep = GameStats(competition=comp).get_game_report_single_season(season)
        except Exception as e:
            print(f"  ! {COMPETITION_NAMES[comp]}: fetch failed ({type(e).__name__}: {e})")
            continue
        upcoming = rep[(rep["played"] != True) & _on_date(rep, date_str)]  # noqa: E712
        for _, g in upcoming.iterrows():
            out.append({
                "gameId": f"{comp}{int(g['Season'])}_{int(g['Gamecode'])}",
                "competition": comp,
                "date": date_str,
                "home_team_id": reg.get(comp, str(g["local.club.code"])),
                "away_team_id": reg.get(comp, str(g["road.club.code"])),
                "home_team": g.get("local.club.name"),
                "away_team": g.get("road.club.name"),
                "home_code": g.get("local.club.code"),
                "away_code": g.get("road.club.code"),
                "tipoff": g.get("confirmedHour") or g.get("localDate") or g.get("date"),
            })
        print(f"  {COMPETITION_NAMES[comp]}: {len(upcoming)} fixture(s)")
    reg.save()
    out_path = os.path.join(DATA_DIR, f"fixtures_{date_str}.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str, ensure_ascii=False)
    print(f"[fixtures] wrote {len(out)} fixtures → {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("append-results", help="Append finished games for a date (default yesterday).")
    r.add_argument("--date", default=None, help="YYYY-MM-DD")
    f = sub.add_parser("fixtures", help="Write the day's scheduled fixtures (default tomorrow).")
    f.add_argument("--date", default=None, help="YYYY-MM-DD")
    args = ap.parse_args()

    today = datetime.date.today()
    if args.mode == "append-results":
        d = args.date or (today - datetime.timedelta(days=1)).isoformat()
        return append_results(d)
    else:
        d = args.date or (today + datetime.timedelta(days=1)).isoformat()
        return fixtures(d)


if __name__ == "__main__":
    sys.exit(main())
