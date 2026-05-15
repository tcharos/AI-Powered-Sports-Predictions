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
| Live in-play loop | `python3 scripts/run_live_loop.py` (daemon, ~10 min cycles) |
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
8. `live_adjuster.py` applies in-play heuristics (shots/xG) on top of model output during the live loop.

NBA flow mirrors football with separate modules: `fetch_nba_results.py`, `fetch_nba_history_stats.py`, `fetch_nba_stats_tables.py` (uses `pbpstats`), `nba_feature_engineering.py`, `train_nba_models.py`, `tune_nba_models.py`, `predict_nba.py`, `evaluate_nba_predictions.py`. Models live at `models/nba_*_model.pkl`.

`entity_resolver.py` + `team_mapping.py` + `data_sets/team_mappings.json` handle name reconciliation between Flashscore and football-data.co.uk (fuzzy match via `rapidfuzz`/`thefuzz`).

### 3. Web UI — `web_ui/app.py` (Flask, port 5001)

Wraps the CLI pipelines (predict / verify / retrain / standings update) plus a bet-tracking dashboard. `nba_routes.py` adds NBA equivalents. Run as a backgrounded process via `bin/manage_server.sh` — direct `python3 web_ui/app.py` works but won't daemonize.

**Betting flow** (active path, all in `web_ui/app.py`):
- `/auto_wager` reads the latest `output/predictions_*.csv` and builds two parallel slips: a **value lane** (Option B sizing — `bankroll × EV × Conf × stake_multiplier`, EV-gated) and a **conviction lane** (Conf ≥ 0.65 AND odds ≥ 1.40, flat 0.5% bankroll). Both subject to per-bet cap (3% bankroll), min-stake floor (€2), and combined per-day exposure cap (10% bankroll, value lane prioritized when over).
- `/place_bets` writes the combined slip to `output/bets_<date>.json` with each bet tagged `lane: 'value' | 'conviction'`, deducts total stake from `data_sets/betting_config.json:current_bankroll`.
- `process_bet_verification` (called after a verification CSV is produced) settles bets and credits returns. **Only looks in `output/`** — archived slips will not settle.
- `/betting` page shows a per-lane Strategy Comparison table (aggregates from both `output/` and `output/history/`) plus the visible active slip list.
- `/delete_file/<filename>` is a **soft delete**: moves the file to `output/history/`. Used by all delete buttons across the UI (slips, predictions, verifications, scraped data). The Archive button only appears on CLOSED slips so OPEN slips can't be archived before settlement.

**Tunables** live in `data_sets/betting_config.json`:
- `min_confidence`, `stake_multiplier`, `min_stake_eur`, `max_stake_pct`, `max_daily_exposure_pct` — value lane + shared.
- `conviction_min_confidence`, `conviction_min_odds`, `conviction_stake_pct` — conviction lane.
- Several legacy keys (`base_unit`, `confidence_threshold_*`, `max_kelly_fraction`, `ev_threshold`, `league_performance_threshold`, `min_matches_for_stats`) are read only by the orphan `betting_engine.py` and have no effect on the active path.

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
- `output/history/` — soft-delete destination. Files moved here are hidden from UI lists but still counted by `/betting` Strategy Comparison stats.
- `output_basketball/` — NBA equivalents.
- `models/` — trained XGBoost JSON / sklearn pickle artifacts and tuned hyperparameters.
- `logs/` — pipeline, scraper status, UI logs.

### Date handling gotcha

`run_predictions.sh` and `run_verification.sh` compute `day_diff` (target − today, in days) and pass it to the spider. Scripts use MacOS `date -v` syntax with a Linux `date -d` fallback — when modifying these wrappers preserve both branches. The scraper treats `day_diff` as the source of truth, not the date string, so the date in the output filename and the day actually scraped can drift if the diff math is wrong.
