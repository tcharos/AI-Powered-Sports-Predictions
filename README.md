# Flashscore Predictor

A comprehensive Machine Learning pipeline to scrape soccer data, simulate betting strategies, and predict match outcomes using XGBoost and Heuristic Adjustments.

## Features
*   **Data Scraping**: Automated scrapers for Flashscore (Results, Odds, Standings, Form).
*   **Machine Learning**: XGBoost models (1X2 & Over/Under 2.5) trained on historical data.
*   **Heuristic Adjustments**: Post-prediction logic (Form Momentum, Standings Differential) to refine standard ML probabilities.
*   **Betting Dashboard**: Web UI to view predictions, manage bankroll, and track betting history.
*   **Live Analysis**: Real-time stats processing for in-play insights.

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/flashscore-scraper.git
    cd flashscore-scraper
    ```

2.  **Create and Activate Virtual Environment**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    playwright install
    ```
    > **Note**: `playwright install` is required to download the browser binaries (Chromium, Firefox, etc.) needed for scraping. This is executed separately from the python package installation.

4.  **Setup Historical Data**:
    Download match results for the current season (e.g., 2025/2026):
    ```bash
    ./setup_data.sh 2526
    ```
    *This populates `data_sets/MatchHistory` with main and extra leagues.*

## Usage

### Web Dashboard
Start the UI to manage everything visually:
```bash
./bin/manage_server.sh start
```
*   Access at: `http://localhost:5001`

### CLI Commands (Football)
*   **Run Prediction** (Tomorrow's Matches):
    ```bash
    ./bin/run_predictions.sh
    ```
*   **Run Verification** (Yesterday's Results vs Predictions):
    ```bash
    ./bin/run_verification.sh
    ```
*   **Retrain Model**:
    ```bash
    ./bin/retrain_pipeline.sh
    ```

### CLI Commands (NBA)
*   **Run Prediction** (Tomorrow's Matches):
    ```bash
    ./bin/run_nba_predictions.sh
    ```
*   **Run Verification** (Yesterday's Results vs Predictions):
    ```bash
    ./bin/run_nba_verification.sh
    ```
*   **Retrain Model**:
    ```bash
    ./bin/retrain_nba_pipeline.sh
    ```

### Live Analysis
*   **Start Live Loop** (Monitors in-play games):
    ```bash
    python3 scripts/run_live_loop.py
    ```

## Documentation
See the `docs/` folder for detailed guides:
*   [Training Process](docs/training_process.md)
*   [UI Manual](docs/ui_manual.md)
*   [Codebase Overview](docs/codebase_overview.md)

## Disclaimer
This project was built with the assistance of **Antigravity** and **Gemini**, leveraging advanced AI for code generation and architectural planning.

