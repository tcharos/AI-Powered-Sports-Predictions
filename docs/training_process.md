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
*   **Algorithm**: XGBoost (`XGBClassifier`).
*   **Models**:
    1.  **1X2 Model**: Multi-class classification (Home, Draw, Away).
    2.  **O/U Model**: Binary classification (Under 2.5, Over 2.5).
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

A post-processing step applied to model probabilities:
1.  **Standings Differential**: Boosts favorite if rank gap > 5.
2.  **Form Momentum**: Boosts team with >= 4 wins in last 5 games.
3.  **High Scoring Trend**: Boosts Over 2.5 if combined avg goals > 3.5.
4.  **Form Trend Analysis (NEW)**:
    *   **Heating Up**: Boosts team if Last 5 form is significantly better than Last 10.
    *   **Cooling Down**: Penalizes team if Last 5 form is significantly worse than Last 10.
    *   **Consistency**: Rewards teams with high win rates in *both* Last 5 and Last 10.
5.  **Specific Form**: Checks Home/Away specific form for stronger signals.

| Heuristic | Condition | Adjustment | Weight (Approx) |
| :--- | :--- | :--- | :--- |
| **Rank Diff** | Rank Diff $\ge$ 5 places | Boost stronger team | +2-10% (scaled) |
| **Spec Rank Diff** | Home Rank (Home Table) vs Away Rank (Away Table) $\ge$ 5 | Boost stronger team | +3-10% (scaled) |
| **Form Momentum** | Last 5: Wins $\ge$ 4 | Boost team | +5% |
| **Form Fade** | Last 5: Losses $\ge$ 4 | Fade team | (Boost Opponent +5% / Fade Team) |
| **Specific Form** | Home Wins @ Home $\ge$ 4 | Boost Home | +6% |
| **Goal Fest** | Combined Avg GF > 3.5 | Boost "Over 2.5" | +5% |

*Note*: Probabilities are re-normalized after adjustments.

## 5. Output
*   **Predictions CSV**: Contains original odds, ML confidence, and heuristic-adjusted confidence.
*   **Analysis**: Final decision (1, X, 2) is based on the highest *adjusted* probability.
