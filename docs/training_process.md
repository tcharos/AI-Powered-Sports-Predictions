# Flashscore Predictor: Training Process & Methodology

This document outlines the end-to-end training process, from data ingestion to model refinement, including the specific features used and the heuristic rules applied post-prediction.

## 1. Data Ingestion
*   **Source**: Historical match data provided by [Football-Data.co.uk](https://www.football-data.co.uk).
*   **Format**: CSV files per league/season.
*   **Setup**: `scripts/setup_historical_data.py` downloads data for:
    *   **Main Leagues**: 22 Major European leagues (from `data.zip`).
    *   **Extra Leagues**: 15 Additional leagues (e.g., USA, BRA, JPN) sourced directly from `/new/` URL endpoints.
*   **Update Mechanism**: `scripts/update_football_data.py` (and the UI button) fetches the latest data for **all** configured leagues.

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

2.  **ELO Ratings (`H_elo`, `A_elo`)**:
    *   Calculated via `ml_project/elo_engine.py` on full history (since ~2010).
    *   Dynamic K-factor formulation based on goal margin.
    
3.  **Rolling Form (Last 5 Games)**:
    *   `H_form_pts`, `A_form_pts`: Average points.
    *   `H_form_gf`, `A_form_gf`: Average goals scored.
    *   `H_form_ga`, `A_form_ga`: Average goals conceded.
    *   `H_form_ou`: Frequency of Over 2.5 matches.
    *   *Note*: Also calculated for Shots (`sf`, `sa`) and Corners (`cf`, `ca`) where available.

4.  **Season-to-Date Stats**:
    *   `H_ppg`, `A_ppg`: Points Per Game current season.
    *   `H_att`, `A_att`: Attack Strength (Avg GF / League Avg GF).
    *   `H_def`, `A_def`: Defense Weakness (Avg GA / League Avg GF).

5.  **Specific Form**:
    *   `H_home_...`: Home team's form *only in home games*.
    *   `A_away_...`: Away team's form *only in away games*.

## 3. Model Training
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

## 4. Heuristic Adjuster

A post-processing step applied to model probabilities (`ml_project/heuristic_adjuster.py`). Heuristics propose **deltas** to an accumulator rather than mutating probabilities directly; the accumulator is **capped** before being applied so no single match can have its outcome distorted by more than `MAX_TOTAL_BOOST_PER_CLASS` (default 0.15) per class.

### Pre-heuristic calibration (always runs)
1.  **League-aware draw shrinkage**: nudges P(Draw) 15% toward the league's historical draw rate, redistributing the delta proportionally between Home/Away.
2.  **Draw cap**: hard-caps P(Draw) at `league_draw_rate + 0.05`. Excess redistributed proportionally to Home/Away.

### H1–H6 (proposed deltas, accumulated then capped)

| Heuristic | Condition | Action |
| :--- | :--- | :--- |
| **H1 — Rank Diff (Overall)** | Standings rank gap ≥ 5 | Boost stronger team `+0.02 × (gap/5)` capped at 0.10 |
| **H2 — Rank Diff (Specific)** | Home-table rank vs Away-table rank gap ≥ 5 | Boost stronger team `+0.03 × (gap/5)` capped at 0.10 |
| **H3 — Form (Overall)** | Last 5: Wins ≥ 4 → boost; Losses ≥ 4 → **fade** | Win: `+0.05` to the team. Loss: `_fade()` — 70% to opponent + 30% to Draw |
| **H4 — Form (Specific venue)** | Home form at home / Away form at away: same W/L rules | Win: `+0.06`. Loss: `_fade()` 70/30 |
| **H6 — Trend (L5 vs L10)** | L5 win-rate ≥ L10 + 0.30 → heating up. L5 ≤ L10 − 0.30 → cooling. Both ≥ 0.70/0.60 → consistent | Heat: `+0.04`. Cool: `_fade()` 70/30. Consistent: `+0.03` |

Symmetric design: every "winning team boost" has a mirror "opposing team fade", split between the opposing outcome and Draw. Previously the fade went entirely to the opponent, producing a structural pro-home bias when away teams were on losing streaks.

### O/U and value logging (separate from the cap)

| Heuristic | Condition | Action |
| :--- | :--- | :--- |
| **H5 — Goal Fest (O/U)** | Combined avg GF > 3.5 | Boost P(Over 2.5) by `+0.05`, then renormalize O/U |
| **H7 — Value flagging** | `adj_prob − implied_book_prob > 0.05` | Log only (no probability change) |

### Application order

1. Apply calibration + draw cap to `adj_1x2` directly.
2. Look up team standings/form. Bail out early with "No Standings Data" if missing.
3. Run H1–H6, each pushing to `delta = [Δhome, Δdraw, Δaway]`.
4. Clip each component of `delta` to `[-0.15, +0.15]`. If anything clipped, log `Boost Cap: H+x.xx→...`.
5. Apply `delta` to `adj_1x2`, clip negatives, normalize.
6. Run H5 on `adj_ou` and normalize.
7. Run H7 (logging only).

### Inference-time consistency
At training, season-to-date PPG / attack strength / defense weakness are computed from full match history. At inference, `predict_matches.py` calls `HeuristicAdjuster.get_team_strength(league, team)` which reads the same fields from current standings JSON — keeping train and serve aligned on these features (no proxy-from-form-points anymore).

## 5. Output
*   **Predictions CSV**: Contains original odds, ML confidence, and heuristic-adjusted confidence.
*   **Analysis**: Final decision (1, X, 2) is based on the highest *adjusted* probability.
