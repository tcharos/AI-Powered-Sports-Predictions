"""nba_api-based daily refresh + fixtures for the NBA pipeline.

Two CLI modes:

* ``append-results [--date YYYY-MM-DD]`` (default yesterday)
    Pulls finished games' team stats via ``LeagueGameLog`` (one row per team per
    game), projects to the ``team_game_stats.csv`` schema, and appends to the
    canonical corpus. **Idempotent**: dedups on ``(gameId, teamId)`` and keeps
    the *first* occurrence — so the rich archive rows (with q1-q4 splits,
    biggestLead, etc.) are never overwritten by the slimmer LeagueGameLog
    projection for the same game. New gameIds are added; existing untouched.

* ``fixtures [--date YYYY-MM-DD]`` (default tomorrow)
    Pulls the day's schedule via ``ScoreboardV2`` and writes
    ``data_sets/NBA/fixtures_<date>.json`` — a small list the predictor reads
    for "tomorrow's matchups": ``{gameId, date, home_team_id, away_team_id,
    home_team, away_team, tipoff}``.

Throttle: ``time.sleep(1)`` before every NBA API call. stats.nba.com is
sensitive and may be geo-restricted — if your IP gets timeouts, run from a
US-region exit node (the project notes this constraint).
"""

import argparse
import datetime
import json
import os
import sys
import time

import pandas as pd


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_REPO, "data_sets", "NBA")
CORPUS = os.path.join(DATA_DIR, "team_game_stats.csv")

SLEEP_S = 1.0

# NBA gameId char-index 2 is the season-type code (10-char id: '00X<season><seq>').
_GAMETYPE_CODE = {
    "1": "Preseason",
    "2": "Regular Season",
    "3": "All-Star",
    "4": "Playoffs",
    "5": "Play-in Tournament",
    "6": "Emirates NBA Cup",
}


def _gametype_from_id(gid) -> str:
    s = str(gid).zfill(10) if gid is not None else ""
    return _GAMETYPE_CODE.get(s[2:3], "Unknown") if len(s) >= 3 else "Unknown"


def _season_year(d: datetime.date) -> int:
    return d.year if d.month >= 10 else d.year - 1


def _project_gamelog_row(r: dict, date_obj: datetime.date) -> dict:
    """LeagueGameLog row → team_game_stats.csv schema. Unavailable cols → None.

    Archive cols not in LeagueGameLog (q1-q4 splits, OT splits, biggestLead,
    benchPoints, blocksAgainst, personalFoulsDrawn, reboundsTeam, turnoversTeam,
    seriesGameNumber/seed/gameLabels, city split) are NaN — feature engineering
    must use the common subset.
    """
    matchup = r.get("MATCHUP", "") or ""
    home = 1 if " vs." in matchup or " vs " in matchup else 0
    win = 1 if r.get("WL") == "W" else 0
    gid = r.get("GAME_ID")
    gt = _gametype_from_id(gid)
    return {
        "gameId":           int(gid) if gid not in (None, "") else None,
        "gameType":         gt,
        "gameLabel":        None, "gameSubLabel": None,
        "seriesGameNumber": None, "seed": None,
        "teamId":           int(r["TEAM_ID"]),
        "teamCity":         None,
        "teamName":         r.get("TEAM_NAME"),
        "opponentTeamId":   None,
        "opponentTeamCity": None,
        "opponentTeamName": None,
        "home":             home, "win": win,
        "teamScore":        r.get("PTS"),
        "opponentScore":    None,
        "numMinutes":       r.get("MIN"),
        "assists":          r.get("AST"),
        "steals":           r.get("STL"),
        "blocks":           r.get("BLK"),
        "blocksAgainst":    None,
        "fieldGoalsMade":   r.get("FGM"),
        "fieldGoalsAttempted":     r.get("FGA"),
        "fieldGoalsPercentage":    r.get("FG_PCT"),
        "threePointersMade":       r.get("FG3M"),
        "threePointersAttempted":  r.get("FG3A"),
        "threePointersPercentage": r.get("FG3_PCT"),
        "freeThrowsMade":          r.get("FTM"),
        "freeThrowsAttempted":     r.get("FTA"),
        "freeThrowsPercentage":    r.get("FT_PCT"),
        "reboundsOffensive": r.get("OREB"),
        "reboundsDefensive": r.get("DREB"),
        "reboundsTotal":     r.get("REB"),
        "reboundsTeam":      None,
        "foulsPersonal":     r.get("PF"),
        "personalFoulsDrawn": None,
        "turnovers":         r.get("TOV"),
        "turnoversTeam":     None,
        "plusMinusPoints":   r.get("PLUS_MINUS"),
        "q1Points": None, "q2Points": None, "q3Points": None, "q4Points": None,
        "ot1Points": None, "ot2Points": None, "otAllPoints": None,
        "benchPoints": None, "biggestLead": None, "biggestScoringRun": None,
        "date":       date_obj.strftime("%Y-%m-%d"),
        "season":     _season_year(date_obj),
        "postseason": gt in ("Playoffs", "Play-in Tournament"),
    }


def _fill_opponent(df: pd.DataFrame) -> pd.DataFrame:
    """Self-join on gameId to fill opponentTeamId/Name/Score on each row."""
    by_game = dict(tuple(df.groupby("gameId")))
    for idx, row in df.iterrows():
        partner = by_game.get(row["gameId"])
        if partner is None or len(partner) != 2:
            continue
        other = partner[partner["teamId"] != row["teamId"]]
        if len(other) == 1:
            o = other.iloc[0]
            df.at[idx, "opponentTeamId"]   = o["teamId"]
            df.at[idx, "opponentTeamName"] = o["teamName"]
            df.at[idx, "opponentScore"]    = o["teamScore"]
    return df


def append_results(date_str: str) -> int:
    from nba_api.stats.endpoints import LeagueGameLog

    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    season = _season_year(date_obj)
    season_str = f"{season}-{(season + 1) % 100:02d}"

    print(f"[append-results] {date_str} (season {season_str}) — fetching team game logs ...")

    rows: list[dict] = []
    # The day might contain Regular Season OR Playoffs OR Play-in games — query
    # each type. Most days only one will return rows; the others are no-ops.
    for season_type in ("Regular Season", "Playoffs", "PlayIn"):
        time.sleep(SLEEP_S)
        try:
            df = LeagueGameLog(
                season=season_str,
                season_type_all_star=season_type,
                date_from_nullable=date_str,
                date_to_nullable=date_str,
                timeout=30,
            ).get_data_frames()[0]
        except Exception as e:
            print(f"  ⚠ {season_type}: {type(e).__name__}: {str(e)[:140]}")
            continue
        if len(df):
            print(f"  {season_type}: {len(df)} team-rows")
        for r in df.to_dict("records"):
            rows.append(_project_gamelog_row(r, date_obj))

    if not rows:
        print(f"[append-results] no games on {date_str}.")
        return 0

    new = pd.DataFrame(rows)
    new = _fill_opponent(new)

    if os.path.exists(CORPUS):
        old = pd.read_csv(CORPUS, low_memory=False)
        # concat [old, new]; keep='first' so archive's richer rows win on dup gameIds.
        merged = pd.concat([old, new], ignore_index=True)
        before = len(merged)
        merged = merged.drop_duplicates(subset=["gameId", "teamId"], keep="first")
        added = len(merged) - len(old)
        print(f"[append-results] appended {added} new team-rows; "
              f"{before - len(merged)} dedup'd against existing corpus")
    else:
        merged = new
        print(f"[append-results] created corpus with {len(new)} team-rows")

    merged = merged.sort_values(["date", "gameId", "home"],
                                ascending=[True, True, False]).reset_index(drop=True)
    merged.to_csv(CORPUS, index=False)
    print(f"[append-results] wrote {CORPUS} ({len(merged):,} total team-rows)")
    return 0


def fixtures(date_str: str) -> int:
    # ScoreboardV3 — V2 has known issues with 2025-26 Oct-Dec line scores; V3 is
    # fully backward compatible. See https://github.com/swar/nba_api/issues/596
    from nba_api.stats.endpoints import ScoreboardV3

    print(f"[fixtures] {date_str} — fetching scoreboard ...")
    time.sleep(SLEEP_S)
    try:
        sb = ScoreboardV3(game_date=date_str, league_id="00", timeout=30)
        # V3 returns a single nested dict per game under .get_dict()['scoreboard']['games'].
        games = sb.get_dict().get("scoreboard", {}).get("games", []) or []
    except Exception as e:
        print(f"[fixtures] FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    out: list[dict] = []
    for g in games:
        home = g.get("homeTeam", {}) or {}
        away = g.get("awayTeam", {}) or {}
        out.append({
            "gameId":       int(g["gameId"]) if str(g.get("gameId", "")).isdigit() else g.get("gameId"),
            "date":         date_str,
            "home_team_id": home.get("teamId"),
            "away_team_id": away.get("teamId"),
            "home_team":    " ".join(filter(None, [home.get("teamCity"), home.get("teamName")])) or home.get("teamName"),
            "away_team":    " ".join(filter(None, [away.get("teamCity"), away.get("teamName")])) or away.get("teamName"),
            "tipoff":       g.get("gameStatusText") or g.get("gameTimeUTC"),
        })

    out_path = os.path.join(DATA_DIR, f"fixtures_{date_str}.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"[fixtures] wrote {len(out)} fixtures → {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("append-results", help="Append finished games for a date to the corpus (default yesterday).")
    r.add_argument("--date", default=None, help="YYYY-MM-DD")

    f = sub.add_parser("fixtures", help="Write the day's scheduled fixtures (default tomorrow).")
    f.add_argument("--date", default=None, help="YYYY-MM-DD")

    args = ap.parse_args()
    today = datetime.date.today()

    if args.mode == "append-results":
        d = args.date or (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        return append_results(d)
    else:
        d = args.date or (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        return fixtures(d)


if __name__ == "__main__":
    sys.exit(main())
