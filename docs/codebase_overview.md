# Flashscore Predictor: Codebase Overview

This document provides a summary of all Python (`.py`) and Shell (`.sh`) scripts in the project.

## 1. Root Directory Scripts

| File | Description |
| :--- | :--- |
| `manage_server.sh` | **Server Control**: Starts, stops, and restarts the Flask Web UI in the background (`nohup`). |
| `retrain_pipeline.sh` | **Automation**: Runs the full pipeline: Update Results &rarr; Update Standings &rarr; Retrain Model. |
| `run_predictions.sh` | **Prediction**: Daily driver. Scrapes tomorrow's matches and generates `predictions_YYYY-MM-DD.csv`. |
| `run_verification.sh` | **Verification**: Scrapes results for a past date (default: yesterday) and compares them with predictions. |
| `update_leagues_data.sh` | **Data Update**: Runs the `standings` spider to update league tables and form JSONs. |
| `run_live_analysis.py` | **Live Mode**: Standalone script to fetch live match stats and predict outcome in real-time. |
| `run_live_loop.py` | **Live Mode**: Daemon that runs `run_live_analysis.py` in a loop (every 10 mins). |
| `setup_data.sh` | **Setup**: Wrapper script to download historical data for a specific season. |


## 2. Machine Learning Project (`ml_project/`)

| File | Description |
| :--- | :--- |
| `betting_engine.py` | **Simulation**: Manages the virtual bankroll, places bets, and resolves them based on results. |
| `data_loader.py` | **IO**: Utility class to load raw CSV match data into Pandas DataFrames. |
| `elo_engine.py` | **Feature**: Calculates historical ELO ratings for all teams. |
| `elo_scraper.py` | **Utility**: (Deprecated/Optional) Scraper for external ELO sources. |
| `entity_resolver.py` | **Utility**: Fuzzy matching logic to map team names between different data sources. |
| `evaluate_predictions.py` | **Verification**: Compares predicted vs actual results and generates accuracy reports. |
| `feature_engineering.py` | **Core**: Transforms raw match data into rolling features (Form, PPG, Strength) for the model. |
| `generate_target_leagues.py`| **Config**: Helper to generate the list of active leagues (not actively used in runtime). |
| `heuristic_adjuster.py` | **Logic**: Applies post-prediction heuristic rules (Form, Standings) to adjust probabilities. |
| `live_adjuster.py` | **Live**: Heuristics specifically for in-play stats (Analysis of Shots/xG). |
| `predict_matches.py` | **Core**: Main prediction CLI. Loads model, fetches features for upcoming games, and predicts. |
| `team_mapping.py` | **Config**: Static dictionary for known team name variations. |
| `train_model.py` | **Training**: Defines and trains the XGBoost 1X2 and O/U models, saving them to JSON. |
| `tune_model.py` | **Optimization**: Performs stepwise hyperparameter tuning for XGBoost and saves best parameters. |

## 3. Web Interface (`web_ui/`)

| File | Description |
| :--- | :--- |
| `app.py` | **Flask App**: The main web server. Handles routes (`/`, `/predict`, `/betting`, `/retrain` etc.). |

## 4. Scrapers (`flashscore_scraper/`, `scripts/`)

| File | Description |
| :--- | :--- |
| `spiders/flashscore_spider.py` | **Scraper**: Main spider. Scrapes Daily Matches, 1X2 Odds, O/U 2.5 Odds, and Results using Playwright. |
| `spiders/standings_spider.py`| **Scraper**: Scrapes League Standings and Form Tables. |
| `scripts/update_football_data.py` | **Data Update**: Downloads and updates the historical CSV dataset from *Football-Data.co.uk*. |
| `scripts/setup_historical_data.py` | **Setup**: Downloads main and extra league CSVs for a specific season. |
