# Flashscore Predictor: Training Process & Methodology

This document outlines the end-to-end training process, from data ingestion to model refinement, including the specific features used.

## 1. Data Ingestion
*   **Source**: Historical match data provided by [Football-Data.co.uk](https://www.football-data.co.uk).
*   **Format**: CSV files per league/season.
*   **Setup**: `scripts/setup_historical_data.py` downloads data for:
    *   **Main Leagues**: 22 Major European leagues (from `data.zip`).
    *   **Extra Leagues**: 15 Additional leagues (e.g., USA, BRA, JPN) sourced directly from `/new/` URL endpoints.
*   **Update Mechanism**: `scripts/update_football_data.py` (and the UI button) fetches the latest data for **all** configured leagues. The main-league season is derived from today's date (seasons roll over on 1 July); it refreshes both the previous and the current season. Until 2026-10-25 it was pinned to 2025/26, so 2026/27 results never arrived.
*   **League labels**: main leagues are keyed by football-data's code (`E0`, `D1`, …). Season files before 2024/25 carry no `Div` column, so `DataLoader` recovers the code from the file name (`ml_project/football_data_leagues.py`). Extra leagues are keyed `"COUNTRY: League"` (`"USA: MLS"`), which is also the form Flashscore uses in a slate label.

## 2. Feature Engineering
The raw data is processed by `ml_project/feature_engineering.py` to generate the input features for the XGBoost models.

### Raw Features
*   **Date**: Match date.
*   **Home/Away Team**: Team names.
*   **FTHG / FTAG**: Full Time Home/Away Goals.
*   **FTR**: Full Time Result (H, D, A).
*   **B365H, B365D, B365A**: Betting odds (used as features).
    *   *Note*: For leagues where Bet365 odds are missing (e.g., some Extra Leagues), the loader automatically falls back to **Average Market Odds** (`AvgCH`/`AvgCD`/`AvgCA`) or **Max Odds** (`MaxCH`/`MaxCD`/`MaxCA`).

### Engineered Features
1.  **Inverse Odds (Implied Probability)**:
    *   `IP_H`, `IP_D`, `IP_A`: Calculated as $1 / Odds$.
    *   Helps linearize the input for the model.
    *   *Note*: until 2026-10-25 these were never built at serve time and were zero-filled, so every live prediction was made with the model's strongest features set to 0. `predict_matches` now builds them, and fails loudly if any model feature is missing.

2.  **ELO Ratings (`H_elo`, `A_elo`)**:
    *   Calculated via `ml_project/elo_engine.py` on full history (since ~2010).
    *   Dynamic K-factor formulation based on goal margin.
    
3.  **Rolling Form (Last 5 Games)**:
    *   `H_form_pts`, `A_form_pts`: Average points.
    *   `H_form_gf`, `A_form_gf`: Average goals scored.
    *   `H_form_ga`, `A_form_ga`: Average goals conceded.
    *   `H_form_ou`: Frequency of Over 2.5 matches.
    *   *Note*: Also calculated for Shots (`sf`, `sa`) and Corners (`cf`, `ca`) where available, but **not trained on** since 2026-10-25. Football-data publishes no shots for the extra leagues, so requiring them dropped every extra-league row from training while those leagues were still served.

4.  **Season-to-Date Stats**:
    *   `H_ppg`, `A_ppg`: Points Per Game current season.
    *   `H_att`, `A_att`: Attack Strength (Avg GF / League Avg GF).
    *   `H_def`, `A_def`: Defense Weakness (Avg GA / League Avg GF).

5.  **Specific Form**:
    *   `H_home_...`: Home team's form *only in home games*.
    *   `A_away_...`: Away team's form *only in away games*.

## 3. Model Training
*   **Training window**: 2012-01-01 onward, every league in the corpus (~177k matches), with a one-year feature buffer before it. ELO is computed over the full history.
*   **Algorithm**: XGBoost (`XGBClassifier` / `XGBRegressor`) by default — but the model *family* is pluggable (see "Swappable model families" below).
*   **Models**:
    1.  **1X2 Model**: Multi-class classification (Home, Draw, Away).
    2.  **O/U Model**: Binary classification (Under 2.5, Over 2.5).
*   **Swappable model families (estimator seam)**:
    *   `ml_project/model_registry.py` decouples the model family from the pipeline. `REGISTRY[market][family]` (markets `1x2`/`ou`/`draw`) returns a `ModelSpec` whose `build()` yields a fresh estimator with a uniform contract (`predict_proba` for `1x2`/`draw`, `predict`→Poisson λ for `ou`).
    *   `train_model.py` builds every head through the seam and writes a `models/model_meta_<market>.json` sidecar; `predict_matches.py` loads each head from that sidecar (legacy `xgb_model_<market>.json` fallback). `scripts/benchmark_models.py` compares families on a value-bet backtest.
    *   Pick a family per head with the env vars `MODEL_FAMILY_1X2` / `MODEL_FAMILY_OU` / `MODEL_FAMILY_DRAW` (default `xgboost`, byte-identical to the pre-seam path). Registered: 1X2 `xgboost`/`logreg`/`rf`; O/U `xgboost`/`poisson_glm`; draw `xgboost`/`logreg`.
*   **Hyperparameter Tuning**:
    *   Executed via `ml_project/tune_model.py`.
    *   Parameters are optimized in 6 steps (Trees -> Depth -> Gamma -> Sampling -> Reg -> LR).
    *   Optimized configuration is saved to `models/best_params_*.json`.
*   **Evaluation Metrics** (per fold):
    *   **Accuracy**: % correct predictions.
    *   **Log Loss**: Measures uncertainty (lower is better).
    *   **Brier Score**: Mean Squared Error of probabilities (lower is better).
    *   **Calibration Error**: Difference between confidence and accuracy.
    *   **ROI %**: Profitability simulation using flat betting (1X2 model only).
*   **Validation**: Time-Series Split (5 Folds), ensuring no data leakage from future matches.

## 4. Post-processing (none)

The model's probabilities are used as they are, apart from per-league Platt calibration (currently disabled for football, see CLAUDE.md).

Until 2026-10-25 a rule layer, `HeuristicAdjuster.adjust_probabilities`, post-processed them. It shrank P(Draw) toward the league draw rate and capped it, and added capped boosts for standings rank gaps, form streaks, L5-vs-L10 trend and high-scoring pairs (O/U). It was removed because replaying it exactly from the logged `Adj Logs` over the settled slates showed it was neutral (−0.0014 nats, CI [−0.006, +0.003]). Every rule family measured ≈0, and the trend rule could never fire because the `last_10` standings tables are copies of `last_5` (`scripts/experiment_heuristic_audit.py`). The `Adj Logs` CSV column remains for schema stability and is empty for club fixtures.

### Inference-time consistency
At training, season-to-date PPG / attack strength / defense weakness are computed from full match history, with seasons taken from the file's own `Season` column where it has one (calendar-year leagues) and Aug→Jul otherwise. At inference, `predict_matches.py` calls `HeuristicAdjuster.get_team_strength(league, team)`, which reads the same fields from the current standings JSON, keeping train and serve aligned on these features. The class name is historical; this is all it does now.

## 5. Output
*   **Predictions CSV**: Contains original odds, final probabilities and confidence, and the raw (pre-calibration) probabilities for audit.
*   **Analysis**: Final decision (1, X, 2) is based on the highest probability; a Draw is picked only when P(Draw) exceeds both other outcomes by 0.05.
*   **Skipped fixtures**: a fixture is not predicted when a team does not resolve to a corpus club, or resolves to a club from another country. The run prints each one; add the name to `ml_project/team_aliases.py`.
