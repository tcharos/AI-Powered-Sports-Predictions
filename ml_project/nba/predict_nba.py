"""NBA daily predictor — winner + total, trained-feature-parity at serve time.

Reads ``data_sets/NBA/fixtures_<date>.json`` (the upcoming games written by
``fetch_nba_daily.py fixtures``), computes per-fixture features from the **same
corpus** ``data_sets/NBA/team_game_stats.csv`` the model was trained on (no
Flashscore-standings season-average proxy), runs the winner + total models, and
writes ``output_basketball/predictions_nba_<date>.csv``.

**Train/serve parity** is the design point. The features are computed by
mirroring ``nba_feature_engineering`` exactly: shifted/rolled over each team's
prior games in the corpus, venue-matched l5 form, rest_days + b2b from the
date-of-last-game, and pre-game ELO from the cached final ratings in
``nba_elo.json``. The predictor reads the same ``features_{winner,total}.json``
manifests the trainer wrote, so column order/identity drift is impossible.

Odds / EV / Kelly are intentionally out of scope here — Phase 3 wires the
betting flow that joins ESPN odds and computes EV. The predictor's CSV carries
the model outputs only (`Home Win Prob`, `Predicted Winner`, `Predicted Total`,
plus the pre-game ELOs for explainability).
"""

import argparse
import datetime
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(_REPO, "data_sets", "NBA")
CORPUS = os.path.join(DATA, "team_game_stats.csv")
ELO_CACHE = os.path.join(DATA, "nba_elo.json")
FIXTURES_TMPL = os.path.join(DATA, "fixtures_{date}.json")

MODEL_DIR = os.path.join(_REPO, "models", "nba")
WINNER_MODEL = os.path.join(MODEL_DIR, "winner_model.pkl")
TOTAL_MODEL = os.path.join(MODEL_DIR, "total_model.pkl")
WINNER_FEATURES = os.path.join(MODEL_DIR, "features_winner.json")
TOTAL_FEATURES = os.path.join(MODEL_DIR, "features_total.json")
CALIBRATION_PATH = os.path.join(DATA, "nba_calibration.json")

OUT_DIR = os.path.join(_REPO, "output_basketball")

# Mirrors nba_feature_engineering — kept duplicate here to keep the predictor
# self-contained (no cross-module dependency on a re-import of the trainer).
ELO_INIT = 1500.0
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
L10_METRICS = ("pts", "pts_allowed", "win", "plus_minus")
VENUE_METRICS = ("pts", "pts_allowed", "win")


def team_features(team_id: int, is_home: bool, game_date: pd.Timestamp,
                  team_rows: pd.DataFrame, elo: dict) -> dict:
    """Serve-time feature vector for one team for a fixture on ``game_date``.

    Filters the team's rows to ``date < game_date`` (so the upcoming game's own
    truth, if for some reason already in the corpus, can't leak), then computes
    the same shift+rolling means + venue-matched form the trainer did. Returns
    a flat dict without the ``home_``/``away_`` prefix — caller adds it.
    """
    prior = team_rows[team_rows["date"] < game_date.strftime("%Y-%m-%d")].sort_values("date")

    out: dict = {}
    # Schedule signals
    if len(prior):
        last_date = pd.to_datetime(prior["date"].iloc[-1])
        rd = max(0, min(14, (game_date - last_date).days))
    else:
        rd = 7
    out["rest_days"] = int(rd)
    out["b2b"] = 1 if rd == 1 else 0
    out["elo_pre"] = float(elo.get(str(team_id), ELO_INIT))

    last5 = prior.tail(5)
    for short, col in ROLL_METRICS.items():
        out[f"l5_{short}"] = float(last5[col].mean()) if len(last5) else np.nan

    last10 = prior.tail(10)
    for short in L10_METRICS:
        out[f"l10_{short}"] = float(last10[col := ROLL_METRICS[short]].mean()) if len(last10) else np.nan

    # Venue-matched: prior games at the same venue type (home vs away) as this one.
    venue_prior = prior[prior["home"] == (1 if is_home else 0)].tail(5)
    for short in VENUE_METRICS:
        col = ROLL_METRICS[short]
        out[f"venue_l5_{short}"] = float(venue_prior[col].mean()) if len(venue_prior) else np.nan

    return out


def build_predict_row(home_team_id: int, away_team_id: int, game_date: pd.Timestamp,
                      corpus: pd.DataFrame, elo: dict) -> dict:
    home_rows = corpus[corpus["teamId"] == home_team_id]
    away_rows = corpus[corpus["teamId"] == away_team_id]
    row: dict = {}
    for k, v in team_features(home_team_id, True, game_date, home_rows, elo).items():
        row[f"home_{k}"] = v
    for k, v in team_features(away_team_id, False, game_date, away_rows, elo).items():
        row[f"away_{k}"] = v
    return row


def predict(date_str: str | None = None) -> int:
    date_str = date_str or (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    fix_path = FIXTURES_TMPL.format(date=date_str)
    if not os.path.exists(fix_path):
        print(f"[predict] no fixtures file: {fix_path}\n"
              f"  run: python3 ml_project/nba/fetch_nba_daily.py fixtures --date {date_str}",
              file=sys.stderr)
        return 1
    fixtures = json.load(open(fix_path))
    print(f"[predict] {len(fixtures)} fixtures from {os.path.basename(fix_path)}")
    if not fixtures:
        print("[predict] no games — nothing to predict.")
        return 0

    print(f"[predict] reading corpus {CORPUS}")
    corpus = pd.read_csv(CORPUS, low_memory=False, usecols=[
        "gameId", "date", "teamId", "home", "teamScore", "opponentScore",
        "fieldGoalsPercentage", "threePointersPercentage", "freeThrowsPercentage",
        "reboundsTotal", "assists", "turnovers", "plusMinusPoints", "win",
    ])
    elo = json.load(open(ELO_CACHE)) if os.path.exists(ELO_CACHE) else {}

    with open(WINNER_MODEL, "rb") as f: winner = pickle.load(f)
    with open(TOTAL_MODEL, "rb") as f: total = pickle.load(f)
    win_features = json.load(open(WINNER_FEATURES))
    tot_features = json.load(open(TOTAL_FEATURES))

    # Platt calibration is applied if the file exists; otherwise raw passes through.
    # (Phase 3 will gate this behind a use_nba_calibration flag in betting_config.)
    from nba_calibration import load_calibration_data, apply_home_win_platt, prob_over, total_sigma
    cal = load_calibration_data(CALIBRATION_PATH)
    if cal:
        print(f"[predict] calibration loaded (Brier improvement "
              f"{cal.get('brier_before',0) - cal.get('brier_after',0):+.4f}); will apply.")
    else:
        print("[predict] no calibration file — raw probabilities will be served.")
    sigma = total_sigma(cal)   # totals normal-approx σ (None if uncalibrated)

    # Odds (optional): join the per-date ESPN odds for the total LINE so we can
    # emit P(Over) per game. The predictor works fine without it (P(Over) blank).
    odds_path = os.path.join(OUT_DIR, f"espn_odds_{date_str}.json")
    odds_by_pair = {}
    if os.path.exists(odds_path):
        try:
            odds_by_pair = {(r.get("home_team"), r.get("away_team")): r
                            for r in (json.load(open(odds_path)) or [])}
        except Exception:
            pass

    game_date = pd.Timestamp(date_str)
    rows: list[dict] = []
    for fx in fixtures:
        h_id, a_id = fx.get("home_team_id"), fx.get("away_team_id")
        if not h_id or not a_id:
            print(f"  ⚠ skipping fixture with missing team_id: {fx}")
            continue
        feats = build_predict_row(int(h_id), int(a_id), game_date, corpus, elo)
        x_win = pd.DataFrame([{f: feats.get(f, np.nan) for f in win_features}])
        x_tot = pd.DataFrame([{f: feats.get(f, np.nan) for f in tot_features}])
        raw_p = float(winner.predict_proba(x_win)[0, 1])
        cal_p, cal_applied, cal_src = apply_home_win_platt(raw_p, cal, enabled=bool(cal))
        pred_total = float(total.predict(x_tot)[0])
        # P(Over) at the posted line (normal-approx around pred_total, σ from calibration).
        odds_row = odds_by_pair.get((fx.get("home_team"), fx.get("away_team"))) or {}
        over_line = odds_row.get("total")
        p_over = prob_over(pred_total, over_line, sigma) if (over_line is not None and sigma) else None
        rows.append({
            "Date":               date_str,
            "Home Team":          fx.get("home_team"),
            "Away Team":          fx.get("away_team"),
            "Home ELO":           int(feats.get("home_elo_pre", ELO_INIT)),
            "Away ELO":           int(feats.get("away_elo_pre", ELO_INIT)),
            "Home Win Prob":      round(cal_p, 4),
            "Home Win Prob (raw)": round(raw_p, 4),
            "Cal Source":         cal_src or "",
            "Predicted Winner":   "HOME" if cal_p >= 0.5 else "AWAY",
            "Predicted Total":    round(pred_total, 2),
            "Total Sigma":        round(sigma, 2) if sigma else "",
            "Over Line":          over_line if over_line is not None else "",
            "P(Over)":            round(p_over, 4) if p_over is not None else "",
            "P(Under)":           round(1.0 - p_over, 4) if p_over is not None else "",
            "Home Rest":          feats.get("home_rest_days"),
            "Away Rest":          feats.get("away_rest_days"),
            "Home B2B":           feats.get("home_b2b"),
            "Away B2B":           feats.get("away_b2b"),
            "gameId":             fx.get("gameId"),
        })

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"predictions_nba_{date_str}.csv")
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    print(f"\n[predict] wrote {len(rows)} predictions → {out_path}")
    if len(df):
        print(df[["Home Team", "Away Team", "Home ELO", "Away ELO",
                  "Home Win Prob", "Predicted Total", "Home Rest", "Away Rest"]]
              .to_string(index=False))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; default tomorrow")
    args = ap.parse_args()
    sys.exit(predict(args.date))
