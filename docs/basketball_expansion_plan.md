# 🏀 Basketball Expansion Plan (Parallel Stack Strategy)

This plan outlines the architecture for adding **Basketball (NBA + others)** support to the platform.
**Core Principle**: **Zero Interference**. We will create *new* separate components for Basketball to ensure the existing Football logic remains untouched and stable.

## 1. Data Collection (Hybrid Strategy)
We will combine a professional stats library with a targeted odds solution.

### A. Game Statistics: `pbpstats` (Python Library)
*   **Source**: Official NBA API wrapper (via `pbpstats`).
*   **Why**: Provides granular possession data (Lineups, Q1-Q4 splits, Pace) unattainable via scraping.
*   **Implementation**: New script `ml_project/fetch_nba_stats.py` to download seasons 2018-2024.

### B. Betting Odds: The "Spread" Problem
**Question**: Do we need historical odds?
**Answer**: **YES**. To train a model to "Beat the Spread", we must know what the spread was.
*   *Solution 1 (Preferred)*: **Static CSV Import**. Instead of scraping 5 years (slow/risky), we will import a bulk CSV dataset of historical odds (available on Kaggle or SBR).
*   *Solution 2 (Backup)*: **Scraper**. `basketball_spider.py` will exist but strictly for **Current/Upcoming Games** to fetch live lines.

### New Component: `basketball_spider.py`
*   **Role**: Fetch **Live/Today's Odds** only.
*   **Path**: `flashscore_scraper/spiders/basketball_spider.py`
*   **Logic**: Targeted scrape of `flashscore.com/basketball/` for Moneyline, Spread, and Totals.

## 2. Machine Learning (New Brain)
Basketball analytics are fundamentally different from Football (Continuous scoring vs Discrete events). We need a dedicated ML stack.

### New Components
1.  **Feature Engineering**: `ml_project/basketball_features.py`
    *   **Pace**: Possessions per 48 minutes.
    *   **Efficiency**: Offensive/Defensive Ratings (Points per 100 possessions).
    *   **Four Factors**: eFG%, Turnover %, Off Rebound %, FT Rate.
    *   **Schedule**: Days of Rest (0 = Back-to-Back), Travel Distance.
2.  **Model Training**: `ml_project/basketball_train.py`
    *   **Target**: Point Spread and Total Points (Regression) or Win/Loss (Classification).
    *   **Model**: XGBoost/LightGBM tuned for high-variance scoring data.
3.  **Predictor**: `ml_project/basketball_predict.py`
    *   Dedicated script to generate `basketball_predictions_YYYY-MM-DD.csv`.

## 3. Data Storage (New Datasets)
To prevent polluting the football datasets, we will use a separate namespace.

*   `data_sets/basketball/` (Directory)
    *   `basketball_match_history.csv` (Historical training data)
    *   `basketball_standings.json` (Current season stats)
    *   `basketball_team_mapping.json` (Name normalization)

## 4. User Interface (Integration)
We will integrate the new sport without "breaking" the `app.py`.

### Strategy: `Blueprints`
1.  **New Module**: `web_ui/basketball_routes.py`
    *   Contains all routes starting with `/basketball/...` (e.g., `/basketball/predictions`, `/basketball/betting`).
2.  **App Integration**:
    *   Minimal change to `app.py`: Just `app.register_blueprint(basketball_bp)`.
3.  **Templates**:
    *   `web_ui/templates/basketball/` folder for specific views.
    *   Shared `layout.html` updated to include the Toggle Switch.

## 5. Summary of New Files
The expansion is additive. No existing files are modified significantly.

|- **Live Odds Scraper**:
  - Script: `flashscore_scraper/spiders/basketball_spider.py`
  - Target: `flashscore.com/basketball/usa/nba`
  - Logic: **Navigate to "Next Day"**.
  - **Odds**: Extract **Stoiximan** odds (Moneyline/Spread/Total).
  - **Deep Stats**:
    - Iterate `data_sets/NBA/nba_standings_form_links.csv`.
    - Scrape tables for Overall/Home/Away Standings & Form.
  - Output: `output_basketball/nba_matches_YYYY-MM-DD.json`
| **Logic** | `ml_project/basketball_features.py` | Calculates Pace, Ratings, 4 Factors. |
| **Logic** | `ml_project/basketball_train.py` | Trains the NBA/Basketball model. |
| **Logic** | `ml_project/basketball_predict.py` | Generates daily predictions. |
| **UI** | `web_ui/basketball_routes.py` | Handles web requests for Basketball. |
| **UI** | `web_ui/templates/basketball/*.html` | Basketball-specific dashboards. |
