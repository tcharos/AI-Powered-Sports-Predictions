# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

All Python commands must run inside the project's `venv`. Most shell wrappers in `bin/` source `venv/bin/activate` automatically; when running Python directly, activate it first:

```bash
source venv/bin/activate
```

Scrapy spiders need Playwright browsers installed (`playwright install`) — this is a one-time setup separate from `pip install -r requirements.txt`.

`PYTHONPATH` needs to include both the repo root and `ml_project/` for the ML scripts to resolve imports. The `bin/run_predictions.sh` and `bin/run_nba_predictions.sh` wrappers export this; replicate it when invoking `ml_project/*.py` files directly:

```bash
export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/ml_project
```

## Common Commands

| Task | Command |
| :--- | :--- |
| Football predictions (tomorrow) | `./bin/run_predictions.sh` |
| Football predictions for a date | `./bin/run_predictions.sh 2026-05-15` |
| Force-rescrape (ignore cached JSON) | `./bin/run_predictions.sh --force` |
| Verify yesterday's predictions | `./bin/run_verification.sh` |
| Full football retrain pipeline | `./bin/retrain_pipeline.sh` (data update → standings → train) |
| Train football models only | `python3 ml_project/train_model.py` |
| Tune football hyperparameters | `python3 ml_project/tune_model.py` |
| Update standings/form only | `./bin/update_leagues_data.sh` |
| Download a season of history | `./bin/setup_data.sh 2526` (season code = end year) |
| NBA predictions / verification / retrain | `./bin/run_nba_predictions.sh`, `./bin/run_nba_verification.sh`, `./bin/retrain_nba_pipeline.sh` |
| Live in-play snapshot (one-shot) | `python3 scripts/run_live_analysis.py` — also exposed in the UI as the "Refresh Live Snapshot" button |
| Cashout backtest harness | `python3 scripts/run_backtest.py --start YYYY-MM-DD --end YYYY-MM-DD` (CLI only; see `ml_project/backtest/`) |
| Start/stop web UI (Flask, port 5001) | `./bin/manage_server.sh {start\|stop\|restart\|status}` — logs to `logs/ui.log` |

Run a single spider manually:
```bash
scrapy crawl flashscore -O output/matches_YYYY-MM-DD.json -L WARNING -a filter_leagues=true -a day_diff=1
scrapy crawl flashscore -a live_ids="id1,id2,..." -a mode=verification -O <file>
scrapy crawl standings -L WARNING
scrapy crawl nba -O output_basketball/nba_matches_YYYY-MM-DD_final.json -L WARNING
```

There is no test suite — `tests/` only contains a `Notes for Football Data` reference text, not Python tests. Treat correctness checks as: run the relevant pipeline end-to-end, then inspect `output/predictions_*.csv`, `output/report_*.txt`, or `output/verification_*.csv`.

## Architecture

The project is three loosely-coupled subsystems sharing a filesystem-based data layer (CSV/JSON files under `data_sets/`, `output/`, `output_basketball/`, `models/`). There is no database.

### 1. Scrapers — `flashscore_scraper/` (Scrapy + Playwright)

- `spiders/flashscore_spider.py` — daily football matches, 1X2 + O/U 2.5 odds, results. Modes:
  - **Date-based**: `-a day_diff=N` (relative to today: `+1` = tomorrow, `-1` = yesterday).
  - **ID-based**: `-a live_ids="..."` — scrape specific match IDs (used by verification to reuse the IDs captured during prediction).
  - `-a mode=verification` flips parsing to read final scores.
  - `-a filter_leagues=true` restricts to `data_sets/target_leagues.json`.
- `spiders/standings_spider.py` — league tables and form; outputs are written via `pipelines.StandingsPipeline`, so do not use `-O`.
- `spiders/nba_spider.py` — NBA equivalent (spider name = `nba`).
- `settings.py` sets `CONCURRENT_REQUESTS=4`, `DOWNLOAD_DELAY=1`, headless Chromium via `scrapy_playwright`. `ROBOTSTXT_OBEY=False`.
- `scripts/update_football_data.py` and `scripts/setup_historical_data.py` pull CSVs from football-data.co.uk — not Scrapy spiders, just HTTP downloaders feeding `data_sets/MatchHistory/`.

### 2. ML pipeline — `ml_project/`

Football flow (1X2 + Over/Under 2.5):
1. `data_loader.py` reads `data_sets/MatchHistory/**.csv`. When Bet365 odds are absent (some extra leagues), it falls back to `AvgC*` then `MaxC*` columns.
2. `elo_engine.py` builds ELO ratings across full history (~since 2010) with goal-margin K-factor; cached at `data_sets/elo_ratings.json`.
3. `feature_engineering.py` produces: implied probabilities (`IP_H/D/A`), ELO, rolling last-5 form (points/GF/GA/OU/shots/corners), season-to-date PPG and attack/defense strength vs league average (`H_ppg`/`A_ppg`/`H_att`/`A_att`/`H_def`/`A_def`/`ppg_diff`/`abs_ppg_diff`/`att_def_diff`), and home-only / away-only specific form.
4. `train_model.py` trains XGBoost models: multi-class 1X2, binary draw, and Poisson O/U 2.5. Time-series 5-fold CV. Models saved to `models/xgb_model_{1x2,draw,ou}.json`, with feature lists in `models/features_*.json`. `tune_model.py` runs a 6-stage stepwise hyperparameter search and writes `models/best_params_*.json`.
5. `predict_matches.MatchPredictor` (invoked by `run_predictions.sh`) loads models, fetches/derives features for the scraped upcoming-matches JSON, and writes `output/predictions_YYYY-MM-DD.csv`. Season-to-date PPG/strength at inference is mirrored from the standings JSON via `HeuristicAdjuster.get_team_strength` to avoid train/serve skew.
6. `heuristic_adjuster.py` post-processes raw model probabilities with league-aware draw calibration + cap, rank-gap boosts, symmetric form-momentum (winning streak → boost; losing streak → split fade between opponent and Draw via `_fade()`), heating/cooling trend, and a goal-fest O/U boost. All H1–H6 deltas are accumulated and capped at `MAX_TOTAL_BOOST_PER_CLASS = 0.15` per outcome before being applied. Probabilities re-normalized; the *adjusted* confidence drives the final pick. See `docs/training_process.md` for the heuristic table.
7. `resolve_daily_bets.py` (called by `run_verification.sh:96`) settles open bet slips in `output/bets_*.json` against scraped results. **Note**: `ml_project/betting_engine.py` is legacy/reference code — the *active* bet-placement and settlement flow lives in `web_ui/app.py` (`/auto_wager`, `/place_bets`, `process_bet_verification`).
8. `live_adjuster.py` applies in-play heuristics (shots/xG/possession/dominance + time decay) on top of model 1X2 output. Invoked by `scripts/run_live_analysis.py` (one-shot, user-triggered via the dashboard's **Refresh Live Snapshot** button or run directly). Each refresh writes the latest snapshot to `output/live_data.json` (consumed by the UI) AND appends one JSON line per scraped match to `output/live_history_<YYYY-MM-DD>.jsonl` (append-only, feeds the forthcoming cashout backtest harness).

NBA pipeline lives entirely under `ml_project/nba/` (`fetch_nba_results.py`, `fetch_nba_history_stats.py`, `fetch_nba_stats_tables.py` (uses `pbpstats`), `fetch_espn_odds.py`, `nba_feature_engineering.py`, `nba_utils.py`, `train_nba_models.py`, `tune_nba_models.py`, `predict_nba.py`, `evaluate_nba_predictions.py`). Models at `models/nba/{winner,total}_model.pkl` + `best_params_*.json`. Outputs at `output_basketball/`. Bin scripts (`run_nba_*.sh`, `retrain_nba_pipeline.sh`) export `PYTHONPATH=...:ml_project/nba` so the in-package imports (`from nba_utils import ...`) keep resolving without prefixes.

**NBA UI is currently DETACHED** — the blueprint code lives at `web_ui/nba/routes.py` (template at `web_ui/templates/nba/index.html`), but `app.py` does not register it. Old `output_basketball/` artifacts have been soft-archived to `output_basketball/history/`. To reactivate the NBA UI: re-add `from nba.routes import nba_bp, NBA_TASKS` and `app.register_blueprint(nba_bp, url_prefix='/nba')` in `web_ui/app.py`, plus restore the nav link in `web_ui/templates/layout.html`.

`entity_resolver.py` + `team_mapping.py` + `data_sets/team_mappings.json` handle name reconciliation between Flashscore and football-data.co.uk (fuzzy match via `rapidfuzz`/`thefuzz`).

### 3. Web UI — `web_ui/app.py` (Flask, port 5001)

Multi-sport-aware. Run as a backgrounded process via `bin/manage_server.sh` — direct `python3 web_ui/app.py` works but won't daemonize.

**URL structure:**
- `/` — sport-picker landing page. Lists everything in `SPORTS = [...]` (defined near the top of `app.py`); active sports are clickable cards, dormant ones are greyed out. Also renders a **Portfolio Summary table** aggregating bets / stake / P/L / ROI / bankroll across every sport.
- `/football/*` — football blueprint (`football_bp`). Every football route — dashboard, betting, predict, verify, retrain, place_bets, auto_wager, view, delete_file, refresh_live, etc. — lives here.
- `/nba/*` — NBA blueprint (currently DETACHED; code at `web_ui/nba/routes.py`, see "NBA reactivation" below).
- `/status`, `/stop/<task>`, `/server/<action>` — sport-agnostic, registered directly on `app`.

**Adding a new sport** (forward-compatible by design):
1. Create `web_ui/<sport>/routes.py` with a `Blueprint('<sport>', __name__)` and the routes you want.
2. Add an entry to `SPORTS` in `app.py`: `{'slug': '<sport>', 'label': '...', 'icon': '...', 'active': True, 'tagline': '...'}`.
3. Import + register: `from <sport>.routes import <sport>_bp` then `app.register_blueprint(<sport>_bp, url_prefix='/<sport>')`.
4. The landing page card and navbar Sport ▾ dropdown pick it up automatically.

**NBA reactivation** (next NBA season): in `web_ui/app.py` flip `nba` entry's `active: False` → `True`, uncomment the `from nba.routes import nba_bp, NBA_TASKS` and `app.register_blueprint(nba_bp, url_prefix='/nba')` lines.

**Betting flow** (lives in `football_bp`, all under `/football/...`):
- `/football/auto_wager` reads the latest `output/predictions_*.csv` and builds three parallel slips:
  - **Value lane** — EV-gated, Option B sizing (`value_bankroll × EV × Conf × stake_multiplier`), per-bet cap 3%.
  - **Conviction lane** — Conf ≥ 0.65 AND odds ≥ 1.40 (EV ignored), flat 0.5% of conviction bankroll.
  - **Model lane** — broad coverage: stakes every prediction with `model_bankroll × model_base_pct × Conf × ev_factor` where `ev_factor = clamp(Conf × odds, 0.5, 1.5)`. Has its own lower min-stake floor (`model_min_stake_eur`, default €1) so wide coverage isn't killed by the shared €2 floor.
  - Each lane has its **own bankroll** (independent buckets) and its **own daily exposure cap** (defaults: value 10%, conviction 10%, model 15%). Per-session overrides via query params `bankroll_<lane>` / `cap_<lane>` (cap as 0–1 fraction). When a lane's stakes exceed its cap, that lane scales pro-rata; the other lanes are untouched.
- `/football/place_bets` writes a combined slip to `output/bets_<date>.json` with each bet tagged `lane: 'value' | 'conviction' | 'model'` and a `stake_by_lane` summary. Debits each lane's bankroll separately.
- `process_bet_verification` (called after a verification CSV is produced) settles bets and credits returns **per lane** back to that lane's bankroll. Stores `return_by_lane` and `pnl_by_lane` on the closed slip. **Only looks in `output/`** — archived slips will not settle.
- `/football/betting` page shows a three-row Strategy Comparison table ("Strategy Comparison · Football (cumulative)"). Aggregation logic lives in `compute_sport_summary(bets_dir)` (module-level in `app.py`); the same helper feeds the landing page's Portfolio Summary table.
- `/football/delete_file/<filename>` is a **soft delete**: moves the file to `output/history/`. The Archive button only appears on CLOSED slips so OPEN slips can't be archived before settlement.

**Tunables** live in `data_sets/betting_config.json`, **sport-keyed**, **lane-aware**:

```json
{
    "sports": {
        "football": {
            "bankrolls": {
                "value":      {"current": 1000.0, "initial": 1000.0},
                "conviction": {"current": 1000.0, "initial": 1000.0},
                "model":      {"current": 1000.0, "initial": 1000.0}
            },
            "min_confidence": 0.45, "stake_multiplier": 0.4,
            "min_stake_eur": 2.0, "max_stake_pct": 0.03,
            "conviction_min_confidence": 0.65, "conviction_min_odds": 1.4, "conviction_stake_pct": 0.005,
            "model_base_pct": 0.005, "model_max_stake_pct": 0.015, "model_min_stake_eur": 1.0,
            "model_ev_factor_min": 0.5, "model_ev_factor_max": 1.5,
            "value_max_daily_exposure_pct": 0.10,
            "conviction_max_daily_exposure_pct": 0.10,
            "model_max_daily_exposure_pct": 0.15
        }
    }
}
```

Each sport has three independent lane bankrolls (no cross-contamination between lanes or sports) and its own tunables. Adding a fourth lane = new entry under `bankrolls`, new entry in `LANES` in `sports_config.py`, and a new builder in `/auto_wager`.

**All bankroll/config access goes through `web_ui/sports_config.py`** — never read or mutate the JSON directly. The schema lives only in this module:
- `get_sport_config(slug)` — full per-sport config dict (defaults merged with overrides, includes `bankrolls`).
- `get_bankroll(slug, lane='value')` / `update_bankroll(slug, delta, lane='value')` — atomic lane-scoped mutations.
- `lane_bankrolls(slug)` → `{lane: current}` for one sport.
- `sport_total(slug)` → sum across lanes.
- `all_bankrolls()` → `{sport: sport_total}` (flat per-sport view for navbar/landing).
- `all_lane_bankrolls()` → `{sport: {lane: current}}` (detailed breakdown).
- `total_bankroll()` → sum across every lane of every sport.

The legacy flat keys (`base_unit`, `confidence_threshold_*`, `max_kelly_fraction`, `ev_threshold`, `league_performance_threshold`, `min_matches_for_stats`) used to live at the root for the orphan `betting_engine.py`; dropped in the per-sport migration. The orphan module is fully dead.

### Data layout cheatsheet

- `data_sets/MatchHistory/` — raw historical CSVs (one per league/season). Source of truth for training.
- `data_sets/standings/` — JSON files written by `standings_spider` (current standings + form tables).
- `data_sets/target_leagues.json` — whitelist for the daily match filter.
- `data_sets/elo_ratings.json`, `league_analytics.json`, `team_mappings.json` — derived/config caches.
- `data_sets/betting_config.json` — bankroll + strategy tunables (see Web UI section).
- `data_sets/bets.json` — present but unused (read by orphan `betting_engine.py`).
- `output/matches_<date>.json` — scraper output (predictions or results, depending on mode).
- `output/predictions_<date>.csv`, `output/verification_<date>.csv`, `output/report_<date>.txt` — prediction artifacts.
- `output/bets_<date>.json` — placed bet slips (active). Each bet has a `lane` tag.
- `output/live_data.json` — latest live-snapshot results (overwritten each refresh; what the UI reads).
- `output/live_history_<date>.jsonl` — append-only per-match snapshots from every Refresh Live Snapshot run. One JSON object per line. Schema: `ts, date, match_id, home_team, away_team, league, minute, score, stats, pre_probs, adj_probs`. Source data for cashout backtesting.
- `output/backtests/<timestamp>.{json,txt}` — cashout backtest outputs from `scripts/run_backtest.py`. JSON has raw outcomes + aggregate by `(rule, lane)`; .txt is the pretty-printed report. Engine lives in `ml_project/backtest/` (`trajectories.py`, `simulator.py`, `rules.py`, `report.py`). Built-in rules: `null` (self-validation, must equal stored P/L), `lock_in_profit`, `stop_loss`, `late_drift`. Both 1X2 and O/U bets are evaluated — see `LiveAdjuster.adjust_ou_probabilities` (Poisson goal model from observed xG pace, blended with the pre-match Over % via a sigmoid centred at minute 30).

## Roadmap

Cashout feature is built in phases. See **`NEXT_STEPS.md`** at the repo root for the current state of each phase and the data-accrual wait that gates phases 3, 6, 7. Update that file when phases complete.
- `output/history/` — soft-delete destination. Files moved here are hidden from UI lists but still counted by `/betting` Strategy Comparison stats.
- `output_basketball/` — NBA artifacts (currently empty; old slate archived under `output_basketball/history/`).
- `models/` — trained XGBoost JSON / sklearn pickle artifacts and tuned hyperparameters.
- `logs/` — pipeline, scraper status, UI logs.

### Date handling gotcha

`run_predictions.sh` and `run_verification.sh` compute `day_diff` (target − today, in days) and pass it to the spider. Scripts use MacOS `date -v` syntax with a Linux `date -d` fallback — when modifying these wrappers preserve both branches. The scraper treats `day_diff` as the source of truth, not the date string, so the date in the output filename and the day actually scraped can drift if the diff math is wrong.
