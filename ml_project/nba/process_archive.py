"""Build the canonical NBA training corpus from the local archive.

Reads ``data_sets/NBA/archive/TeamStatisticsExtended.csv`` (per-team-per-game
stats going back decades), filters to **2000+** competitive games (drops
All-Star exhibitions; keeps Regular Season / Playoffs / Play-in / Emirates Cup),
and writes the long-format canonical corpus

    data_sets/NBA/team_game_stats.csv

Two rows per game (one per team), with derived ``date``, ``season``,
``postseason`` columns. ``teamId`` is the stable join key (survives franchise
relocations: Sonics→Thunder, Nets NJ→BK, Hornets→Pelicans, etc.).

This corpus is the single source of truth for the model:
* Feature engineering (P2a) builds rolling form / rest / B2B / home-away
  splits / net rating / ELO **only** from this CSV.
* Daily refresh (P1c, nba_api) appends new rows to this same file in the same
  schema.
* The predictor (P2b) computes serve-time features from this CSV, eliminating
  the train/serve skew that the old Flashscore-standings path introduced.

One-shot, offline, ~10 seconds. Idempotent (overwrites the output).
"""

import os
import sys

import pandas as pd


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(_REPO, "data_sets", "NBA", "archive", "TeamStatisticsExtended.csv")
OUT = os.path.join(_REPO, "data_sets", "NBA", "team_game_stats.csv")

# Competitive game types kept for training; All-Star is exhibition → dropped.
# Emirates NBA Cup is an in-season tournament — real games, kept.
KEEP_GAMETYPES = {"Regular Season", "Playoffs", "Play-in Tournament", "Emirates NBA Cup"}

# Curated columns: identity + outcome + per-team stats used (or likely to be
# used) by feature engineering. Drops the wide tail of duplicative fields.
KEEP_COLS = [
    "gameId", "gameDateTimeEst", "gameType", "gameLabel", "gameSubLabel",
    "seriesGameNumber", "seed",
    "teamId", "teamCity", "teamName",
    "opponentTeamId", "opponentTeamCity", "opponentTeamName",
    "home", "win", "teamScore", "opponentScore",
    "numMinutes",
    "assists", "steals", "blocks", "blocksAgainst",
    "fieldGoalsMade", "fieldGoalsAttempted", "fieldGoalsPercentage",
    "threePointersMade", "threePointersAttempted", "threePointersPercentage",
    "freeThrowsMade", "freeThrowsAttempted", "freeThrowsPercentage",
    "reboundsOffensive", "reboundsDefensive", "reboundsTotal", "reboundsTeam",
    "foulsPersonal", "personalFoulsDrawn",
    "turnovers", "turnoversTeam",
    "plusMinusPoints",
    "q1Points", "q2Points", "q3Points", "q4Points",
    "ot1Points", "ot2Points", "otAllPoints",
    "benchPoints", "biggestLead", "biggestScoringRun",
]


def _season_year(d: pd.Series) -> pd.Series:
    """NBA season runs Oct→June; label by *start* year.

    Oct/Nov/Dec stay in calendar year; Jan→Sep map to the prior year
    (mid/late-season games of a season that started the previous Oct).
    """
    yr = d.dt.year
    return yr.where(d.dt.month >= 10, yr - 1).astype(int)


def process(archive_path: str = ARCHIVE, out_path: str = OUT) -> pd.DataFrame:
    if not os.path.exists(archive_path):
        raise FileNotFoundError(archive_path)
    print(f"[process_archive] reading {archive_path} ...")
    df = pd.read_csv(archive_path, low_memory=False)
    print(f"  raw: {len(df):,} team-rows")

    missing = [c for c in KEEP_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"archive missing expected columns: {missing}")

    df = df[KEEP_COLS].copy()
    df["gameDateTimeEst"] = pd.to_datetime(df["gameDateTimeEst"], errors="coerce")
    df = df.dropna(subset=["gameDateTimeEst"])
    df["date"] = df["gameDateTimeEst"].dt.date.astype(str)
    df["season"] = _season_year(df["gameDateTimeEst"])
    df["postseason"] = df["gameType"].isin({"Playoffs", "Play-in Tournament"})

    df = df[df["date"] >= "2000-01-01"]
    before_gt = len(df)
    df = df[df["gameType"].isin(KEEP_GAMETYPES)]
    print(f"  after 2000-01-01 + gametype filter: {len(df):,} team-rows "
          f"({len(df) // 2:,} games); dropped {before_gt - len(df):,} non-competitive")

    df = df.drop(columns=["gameDateTimeEst"])
    df = df.sort_values(["date", "gameId", "home"], ascending=[True, True, False]).reset_index(drop=True)

    # Sanity: every game should have exactly 2 team-rows.
    counts = df.groupby("gameId").size()
    bad = counts[counts != 2]
    if not bad.empty:
        print(f"  ⚠ {len(bad)} gameIds with != 2 team-rows (sample: {bad.head().to_dict()})")

    # Merge-safe: if a corpus already exists, preserve any daily-only appended
    # rows (gameIds NOT in the archive) so a retrain doesn't lose recent games
    # that fetch_nba_daily.py append-results added on top. Archive rows win for
    # any gameIds the archive covers (the archive is the canonical source for
    # its era — typically richer columns than the daily projection).
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        existing = pd.read_csv(out_path, low_memory=False)
        archive_ids = set(df["gameId"])
        daily_only = existing[~existing["gameId"].isin(archive_ids)]
        if len(daily_only):
            df = pd.concat([df, daily_only], ignore_index=True)
            df = df.sort_values(["date", "gameId", "home"],
                                ascending=[True, True, False]).reset_index(drop=True)
            print(f"  merged: preserved {len(daily_only):,} daily-only rows "
                  f"({daily_only['gameId'].nunique():,} games) post-archive")
    df.to_csv(out_path, index=False)
    print(f"\n[process_archive] wrote {len(df):,} team-rows ({len(df) // 2:,} games) → {out_path}")
    print(f"  seasons: {int(df['season'].min())} → {int(df['season'].max())} "
          f"({df['season'].nunique()} unique)")
    print(f"  date: {df['date'].min()} → {df['date'].max()}")
    print(f"  postseason team-rows: {int(df['postseason'].sum()):,}")
    print(f"  unique teamIds: {df['teamId'].nunique()} "
          f"(franchise-stable across the 2000+ relocations)")
    return df


if __name__ == "__main__":
    try:
        process()
    except Exception as e:
        print(f"[process_archive] FAIL: {e}", file=sys.stderr)
        sys.exit(1)
