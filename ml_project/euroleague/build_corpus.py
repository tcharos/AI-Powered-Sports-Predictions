"""Euroleague Phase 1 — canonical corpus builder (ETL).

Reads the raw ``euroleague-api`` season CSVs in ``data_sets/Euroleague/raw/``
(``{COMP}_{SEASON}_game_report.csv`` + ``{COMP}_{SEASON}_game_stats.csv`` for
COMP ∈ {E, U}, fetched in Phase 0) and writes the canonical long-format corpus
``data_sets/Euroleague/team_game_stats.csv`` — **one row per team per game** —
in the same column shape NBA's ``nba_feature_engineering`` consumes, plus a
``competition`` column for the combined-model per-competition split.

Schema contract (what the feature engineering needs):
    gameId, date, season, postseason, teamId, home, win,
    teamScore, opponentScore, fieldGoalsPercentage, threePointersPercentage,
    freeThrowsPercentage, reboundsTotal, assists, turnovers, plusMinusPoints
plus Euroleague extras kept for later use (competition, round, phase, team
codes/names, FG2/3 split, FT, off/def rebounds, steals, blocks, valuation=PIR).

Key facts baked in (verified on the raw data, 2026-05-29):
  * Box-score totals live in ``{side}.total.*`` — ``{side}.team.*`` is zeroed.
  * Final score comes from the report (``{side}.score``); ``total.points`` agrees.
  * ``plusMinusPoints = teamScore - opponentScore`` (NBA-consistent margin) —
    the API's ``total.plusMinus`` is summed-player noise, not the team margin.
  * Join report↔stats on (Season, Gamecode); gameId = ``{COMP}{Season}_{Gamecode}``.
  * postseason = Phase != 'RS' (RS regular / PO playoffs / FF final-four / PI play-in).

Merge-safe: a rebuild preserves any rows already in the corpus whose gameId is
NOT regenerated here (i.e. daily-fetcher appends for in-progress seasons).

Usage::
    python3 ml_project/euroleague/build_corpus.py
"""

import glob
import os
import re
import sys

import numpy as np
import pandas as pd

from euroleague_utils import RAW_DIR, CORPUS, COMPETITIONS, TeamIdRegistry

# Canonical column order written to the corpus.
CANONICAL_COLS = [
    "gameId", "competition", "date", "season", "postseason", "round", "phase",
    "teamId", "teamCode", "teamName", "opponentCode",
    "home", "win", "teamScore", "opponentScore",
    "fieldGoalsPercentage", "threePointersPercentage", "freeThrowsPercentage",
    "reboundsTotal", "reboundsOffensive", "reboundsDefensive",
    "assists", "steals", "turnovers", "blocks",
    "fieldGoalsMade2", "fieldGoalsAttempted2",
    "fieldGoalsMade3", "fieldGoalsAttempted3",
    "freeThrowsMade", "freeThrowsAttempted",
    "valuation",  # team total PIR (Euroleague's efficiency metric)
    "plusMinusPoints",
]


def _safe_div(num, den):
    """Element-wise num/den as a 0..1 fraction; 0/NaN denominators → NaN."""
    den = den.replace(0, np.nan)
    return (num / den).replace([np.inf, -np.inf], np.nan)


def _side_rows(m: pd.DataFrame, comp: str, side: str) -> pd.DataFrame:
    """Build canonical per-team rows for one side ('local'=home / 'road'=away)."""
    opp = "road" if side == "local" else "local"
    p = f"{side}.total."
    pts = m[f"{side}.score"].astype(float)
    opp_pts = m[f"{opp}.score"].astype(float)
    out = pd.DataFrame({
        "gameId": comp + m["Season"].astype(str) + "_" + m["Gamecode"].astype(str),
        "competition": comp,
        "date": m["localDate"].fillna(m["date"]),
        "season": m["Season"].astype(int),
        "postseason": (m["Phase"].astype(str) != "RS").astype(int),
        "round": m["Round"],
        "phase": m["Phase"],
        "teamCode": m[f"{side}.club.code"].astype(str),
        "teamName": m[f"{side}.club.name"].astype(str),
        "opponentCode": m[f"{opp}.club.code"].astype(str),
        "home": 1 if side == "local" else 0,
        "teamScore": pts,
        "opponentScore": opp_pts,
        "win": (pts > opp_pts).astype(int),
        "fieldGoalsPercentage": _safe_div(m[p + "fieldGoalsMadeTotal"], m[p + "fieldGoalsAttemptedTotal"]),
        "threePointersPercentage": _safe_div(m[p + "fieldGoalsMade3"], m[p + "fieldGoalsAttempted3"]),
        "freeThrowsPercentage": _safe_div(m[p + "freeThrowsMade"], m[p + "freeThrowsAttempted"]),
        "reboundsTotal": m[p + "totalRebounds"],
        "reboundsOffensive": m[p + "offensiveRebounds"],
        "reboundsDefensive": m[p + "defensiveRebounds"],
        "assists": m[p + "assistances"],
        "steals": m[p + "steals"],
        "turnovers": m[p + "turnovers"],
        "blocks": m[p + "blocksFavour"],
        "fieldGoalsMade2": m[p + "fieldGoalsMade2"],
        "fieldGoalsAttempted2": m[p + "fieldGoalsAttempted2"],
        "fieldGoalsMade3": m[p + "fieldGoalsMade3"],
        "fieldGoalsAttempted3": m[p + "fieldGoalsAttempted3"],
        "freeThrowsMade": m[p + "freeThrowsMade"],
        "freeThrowsAttempted": m[p + "freeThrowsAttempted"],
        "valuation": m[p + "valuation"],
        "plusMinusPoints": pts - opp_pts,
    })
    return out


def _season_from_name(path: str):
    mo = re.search(r"([EU])_(\d{4})_game_report\.csv$", os.path.basename(path))
    return (mo.group(1), int(mo.group(2))) if mo else (None, None)


def _build_competition(comp: str) -> pd.DataFrame:
    """ETL every season of one competition into canonical long-format rows."""
    reports = sorted(glob.glob(os.path.join(RAW_DIR, f"{comp}_*_game_report.csv")))
    frames = []
    for rep_path in reports:
        c, season = _season_from_name(rep_path)
        stats_path = rep_path.replace("_game_report.csv", "_game_stats.csv")
        if not os.path.exists(stats_path):
            print(f"  ! {os.path.basename(rep_path)}: no matching game_stats — skipped")
            continue
        rep = pd.read_csv(rep_path, low_memory=False)
        sts = pd.read_csv(stats_path, low_memory=False)
        # Only played games with real scores.
        rep = rep[rep["played"] == True].copy()  # noqa: E712
        rep = rep.dropna(subset=["local.score", "road.score"])
        merged = rep.merge(
            sts, on=["Season", "Gamecode"], how="inner", suffixes=("", "_stats"))
        if merged.empty:
            print(f"  ! {comp} {season}: 0 games after merge — skipped")
            continue
        rows = pd.concat([_side_rows(merged, comp, "local"),
                          _side_rows(merged, comp, "road")], ignore_index=True)
        frames.append(rows)
        print(f"  {comp} {season}: {len(merged):,} games → {len(rows):,} team-rows")
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLS)
    return pd.concat(frames, ignore_index=True)


def finalize_rows(corpus: pd.DataFrame) -> pd.DataFrame:
    """Assign stable teamIds, normalise dates, enforce column order + dedup.

    Shared by the full rebuild (``build``) and the incremental daily append
    (``fetch_euroleague_daily.append-results``) so both produce identical schema.
    """
    reg = TeamIdRegistry()
    corpus = corpus.copy()
    corpus["teamId"] = [reg.get(c, code)
                        for c, code in zip(corpus["competition"], corpus["teamCode"])]
    reg.save()
    # Normalise date to ISO date (drop the time component for the corpus).
    corpus["date"] = pd.to_datetime(corpus["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    corpus = corpus.dropna(subset=["date"])
    corpus = corpus[CANONICAL_COLS]
    return corpus.drop_duplicates(subset=["gameId", "teamId"], keep="last")


def rows_for_merged(merged: pd.DataFrame, comp: str) -> pd.DataFrame:
    """Canonical (un-finalised) home+away rows from a report↔stats merge."""
    return pd.concat([_side_rows(merged, comp, "local"),
                      _side_rows(merged, comp, "road")], ignore_index=True)


def build(raw_dir=RAW_DIR, out_path=CORPUS) -> pd.DataFrame:
    print(f"[corpus] reading raw CSVs from {raw_dir}")
    parts = []
    for comp in COMPETITIONS:
        df = _build_competition(comp)
        if not df.empty:
            parts.append(df)
    if not parts:
        raise FileNotFoundError(f"no raw game_report CSVs found in {raw_dir}")
    corpus = finalize_rows(pd.concat(parts, ignore_index=True))

    # Merge-safe: keep any existing corpus rows whose gameId we did NOT rebuild
    # (e.g. daily-fetcher appends for an in-progress season the raw set lacks).
    if os.path.exists(out_path):
        try:
            existing = pd.read_csv(out_path, low_memory=False)
            rebuilt_ids = set(corpus["gameId"])
            keep = existing[~existing["gameId"].isin(rebuilt_ids)]
            if len(keep):
                print(f"  merge-safe: preserving {len(keep):,} non-rebuilt rows")
                corpus = pd.concat([corpus, keep], ignore_index=True)
        except Exception as e:
            print(f"  ! could not read existing corpus for merge ({e}); overwriting")

    corpus = (corpus.sort_values(["date", "gameId", "home"], ascending=[True, True, False])
              .reset_index(drop=True))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    corpus.to_csv(out_path, index=False)

    n_games = corpus["gameId"].nunique()
    print(f"\n[corpus] wrote {len(corpus):,} team-rows ({n_games:,} games) → {out_path}")
    by_comp = corpus.groupby("competition")["gameId"].nunique().to_dict()
    print(f"  games by competition: {by_comp}")
    print(f"  seasons: {int(corpus['season'].min())}→{int(corpus['season'].max())}  "
          f"| teams: {corpus['teamId'].nunique()}")
    print(f"  date range: {corpus['date'].min()} → {corpus['date'].max()}")
    hr = corpus[corpus["home"] == 1]["win"].mean()
    print(f"  home win rate: {hr:.3f}  | avg team score: {corpus['teamScore'].mean():.1f}")
    return corpus


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"[corpus] FAIL: {e}", file=sys.stderr)
        sys.exit(1)
