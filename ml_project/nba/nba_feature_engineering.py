"""Build the NBA training matrix from the canonical corpus.

Reads ``data_sets/NBA/team_game_stats.csv`` (long-format per-team-per-game,
produced by ``process_archive.py`` and incrementally appended by
``fetch_nba_daily.py``) and writes ``data_sets/NBA/training_data.csv`` —
one row per game with leakage-free home_/away_ features plus the two targets
(``home_win``, ``total_points``).

Features (per team, all shifted to exclude the current game)
-----------------------------------------------------------
* **Schedule signals** (the #1 NBA scheduling effect): ``rest_days`` (days since
  the team's last game; season-opener defaults to 7), ``b2b`` flag (rest_days==1).
* **Pre-game ELO** (basketball-style, K=20, home-advantage 100): single
  chronological pass; each team's rating BEFORE this game is recorded, then
  updated post-game. Final state cached at ``data_sets/NBA/nba_elo.json`` for
  resume / inference.
* **Last-5 form**: pts, pts-allowed, win%, fg%, fg3%, ft%, reb, ast, tov, +/-.
* **Last-10 form**: pts, pts-allowed, win%, +/-.
* **Venue-matched form** (last 5): the team's last 5 *home* games (when this
  game is at home) or last 5 *away* games (when on the road) — captures recent
  performance under the venue conditions of tonight's game.

In wide format every game ends up with ``home_<f>`` and ``away_<f>`` cols
(≈40 features) plus the two targets. XGBoost handles any residual NaN
natively; we still drop rows where the long-window (l10/venue) features are
unpopulated to keep training honest about its early-season warmup.
"""

import json
import os
import sys

import numpy as np
import pandas as pd


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(_REPO, "data_sets", "NBA")
INPUT = os.path.join(DATA, "team_game_stats.csv")
OUTPUT = os.path.join(DATA, "training_data.csv")
ELO_CACHE = os.path.join(DATA, "nba_elo.json")

# ELO
ELO_INIT = 1500.0
ELO_K = 20.0
ELO_HOME_ADV = 100.0

# Rolling source-column map: short alias → source column name.
ROLL_METRICS = {
    "pts":         "teamScore",
    "pts_allowed": "opponentScore",
    "win":         "win",
    "fg_pct":      "fieldGoalsPercentage",
    "fg3_pct":     "threePointersPercentage",
    "ft_pct":      "freeThrowsPercentage",
    "reb":         "reboundsTotal",
    "ast":         "assists",
    "tov":         "turnovers",
    "plus_minus":  "plusMinusPoints",
}
L10_METRICS = ("pts", "pts_allowed", "win", "plus_minus")     # also rolled at l10
VENUE_METRICS = ("pts", "pts_allowed", "win")                  # home-only / away-only l5


# ---------------------------------------------------------------------------
# Pure helpers (testable)
# ---------------------------------------------------------------------------

def add_schedule_features(ts: pd.DataFrame) -> pd.DataFrame:
    """Add rest_days + b2b per team-row. Sorts by (teamId, date) in place."""
    ts = ts.sort_values(["teamId", "date"]).copy()
    ts["_d"] = pd.to_datetime(ts["date"])
    diff = ts.groupby("teamId")["_d"].diff().dt.days
    # Season opener / first game in dataset → 7 days (a "fresh" default, not B2B).
    ts["rest_days"] = diff.fillna(7).clip(lower=0, upper=14).astype(int)
    ts["b2b"] = (diff == 1).fillna(False).astype(int)
    return ts.drop(columns=["_d"])


def add_rolling(ts: pd.DataFrame) -> pd.DataFrame:
    """Add shifted rolling-5 (all ROLL_METRICS) + rolling-10 (L10_METRICS)."""
    ts = ts.sort_values(["teamId", "date"]).copy()
    for short, col in ROLL_METRICS.items():
        ts[f"l5_{short}"] = (
            ts.groupby("teamId")[col]
              .transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
        )
    for short in L10_METRICS:
        col = ROLL_METRICS[short]
        ts[f"l10_{short}"] = (
            ts.groupby("teamId")[col]
              .transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())
        )
    return ts


def add_venue_form(ts: pd.DataFrame) -> pd.DataFrame:
    """Add venue_l5_<m>: the team's last-5 form at the SAME venue type as the
    current game. Computed within home-only and away-only subsets, then
    forward-filled within each (teamId, home) bucket so the value carried at
    each row is the team's most-recent venue-matching form.
    """
    ts = ts.sort_values(["teamId", "home", "date"]).copy()
    for short in VENUE_METRICS:
        col = ROLL_METRICS[short]
        ts[f"venue_l5_{short}"] = (
            ts.groupby(["teamId", "home"])[col]
              .transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
        )
    return ts.sort_values(["date", "gameId"])


def compute_elo(games_wide: pd.DataFrame) -> tuple[dict, dict]:
    """Single chronological pass. Returns ((gameId, teamId)→pre_elo, final_ratings).

    games_wide: one row per game with home_teamId, away_teamId, home_score,
    away_score, date, gameId. Sorted by date inside.
    """
    pre: dict[tuple, float] = {}
    ratings: dict[int, float] = {}
    g = games_wide.sort_values(["date", "gameId"]).itertuples(index=False)
    for row in g:
        h, a = int(row.home_teamId), int(row.away_teamId)
        rh = ratings.get(h, ELO_INIT)
        ra = ratings.get(a, ELO_INIT)
        pre[(row.gameId, h)] = rh
        pre[(row.gameId, a)] = ra
        # Win-prob with home advantage embedded in the rating diff.
        exp_h = 1.0 / (1.0 + 10 ** ((ra - rh - ELO_HOME_ADV) / 400.0))
        actual_h = 1.0 if row.home_score > row.away_score else 0.0
        delta = ELO_K * (actual_h - exp_h)
        ratings[h] = rh + delta
        ratings[a] = ra - delta
    return pre, ratings


# ---------------------------------------------------------------------------
# Pivot long → wide
# ---------------------------------------------------------------------------

def _pivot_wide(ts: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """One row per game with home_<f> / away_<f> feature cols + targets."""
    keep = ["gameId", "date", "season", "postseason", "teamId", "home",
            "teamScore", "opponentScore"] + feature_cols
    ts = ts[keep].copy()
    home = ts[ts["home"] == 1].drop(columns=["home"]).copy()
    away = ts[ts["home"] == 0].drop(columns=["home"]).copy()

    # Prefix everything except the join keys (game-level metadata).
    GAME_KEYS = ("gameId", "date", "season", "postseason")
    home = home.rename(columns={c: f"home_{c}" for c in home.columns if c not in GAME_KEYS})
    away = away.rename(columns={c: f"away_{c}" for c in away.columns if c not in GAME_KEYS})

    wide = home.merge(away, on=list(GAME_KEYS), how="inner")
    wide["home_win"] = (wide["home_teamScore"] > wide["away_teamScore"]).astype(int)
    wide["total_points"] = wide["home_teamScore"] + wide["away_teamScore"]
    return wide


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build(input_path: str = INPUT, output_path: str = OUTPUT,
          elo_cache_path: str = ELO_CACHE) -> pd.DataFrame:
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)
    print(f"[features] reading {input_path}")
    ts = pd.read_csv(input_path, low_memory=False)
    print(f"  loaded {len(ts):,} team-rows ({ts['gameId'].nunique():,} games, "
          f"seasons {int(ts['season'].min())}→{int(ts['season'].max())})")

    # 1. Schedule + 2. Rolling + 3. Venue form (per-team-row)
    ts = add_schedule_features(ts)
    ts = add_rolling(ts)
    ts = add_venue_form(ts)

    feature_cols = (
        ["rest_days", "b2b"] +
        [f"l5_{m}" for m in ROLL_METRICS] +
        [f"l10_{m}" for m in L10_METRICS] +
        [f"venue_l5_{m}" for m in VENUE_METRICS]
    )

    # 4. Pivot long → wide (one row per game).
    wide = _pivot_wide(ts, feature_cols)

    # 5. ELO (needs the wide view's home/away assignment).
    elo_input = wide[["gameId", "date", "home_teamId", "away_teamId",
                      "home_teamScore", "away_teamScore"]].rename(
        columns={"home_teamScore": "home_score", "away_teamScore": "away_score"})
    pre_elo, final_ratings = compute_elo(elo_input)
    wide["home_elo_pre"] = wide.apply(lambda r: pre_elo.get((r["gameId"], int(r["home_teamId"])), ELO_INIT), axis=1)
    wide["away_elo_pre"] = wide.apply(lambda r: pre_elo.get((r["gameId"], int(r["away_teamId"])), ELO_INIT), axis=1)

    # 6. Drop early-season warmup rows: require all l10/venue features populated.
    required = [f"home_l10_{m}" for m in L10_METRICS] + [f"away_l10_{m}" for m in L10_METRICS] \
             + [f"home_venue_l5_{m}" for m in VENUE_METRICS] + [f"away_venue_l5_{m}" for m in VENUE_METRICS]
    before = len(wide)
    wide = wide.dropna(subset=required)
    print(f"  warmup-drop: {before - len(wide):,} early-season rows; "
          f"{len(wide):,} training games remain")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wide = wide.sort_values(["date", "gameId"]).reset_index(drop=True)
    wide.to_csv(output_path, index=False)
    print(f"\n[features] wrote {len(wide):,} games × {wide.shape[1]} cols → {output_path}")
    print(f"  date range: {wide['date'].min()} → {wide['date'].max()}")
    print(f"  home_win rate: {wide['home_win'].mean():.3f}   total_points μ={wide['total_points'].mean():.1f}")

    # Cache final ELO for predict-time / resume.
    with open(elo_cache_path, "w") as f:
        json.dump({str(k): round(v, 2) for k, v in final_ratings.items()}, f, indent=2)
    print(f"  ELO cache: {len(final_ratings)} teams → {elo_cache_path}")
    return wide


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"[features] FAIL: {e}", file=sys.stderr)
        sys.exit(1)
