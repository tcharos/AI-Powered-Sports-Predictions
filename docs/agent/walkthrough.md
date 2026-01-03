# League Info Extraction Walkthrough

## Feature Overview
Added functionality to extract the League Name (e.g., "EUROPE: Champions League") for each match from Flashscore and propagate it through the prediction pipeline to the final CSV output.

## Changes Validation

### 1. Data Structure Update
Updated `MatchItem` to include the `league` field.
```python
class MatchItem(scrapy.Item):
    # ...
    league = scrapy.Field()
```

### 2. Scraper Logic
Modified `flashscore_spider.py` to iterate sequentially through the DOM to correctly associate League Headers with the matches that follow them.

### 3. Prediction Output
Updated `predict_matches.py` to include the `League` column in the output.

---

# Flask User Interface Walkthrough

## Overview
Implemented a web-based dashboard to manage predictions and verifications.

## Features
1.  **Dashboard**: Lists all generated prediction and verification reports.
2.  **Action Buttons**: Triggers `run_predictions.sh` and `run_verification.sh` directly from the browser.
3.  **Result Visualization**:
    - Displays CSV data in a responsive table.
    - **Green Highlighting**: Rows/Cells with >70% confidence or "Correct" verification statuses are highlighted in green.
4.  **Validation View**: Now supports detailed verification CSVs (`verification_YYYY-MM-DD.csv`) which include "Correct 1X2" and "Correct O/U" columns.

## How to Run
```bash
python3 web_ui/app.py
```
Access at: `http://127.0.0.1:5000`

### Error Handling & Logging
- **Validation**: The UI checks if the prediction file exists before attempting verification.
- **Logs**: Execution logs for background tasks are available at `/logs/predict.log` and `/logs/verify.log` (linked in flash messages).

---

# Optimization: Targeted Scraping

## Feature Overview
To reduce scraping time and resource usage, the scraper can now be configured to **only** visit match details for specific leagues.

## Configuration
1.  **Whitelist File**: `data_sets/target_leagues.json`
    - Add exact league names (e.g., "ENGLAND: Premier League") to this JSON array.
2.  **Usage**:
    - Pass `-a filter_leagues=true` to the spider.
    - Example: `scrapy crawl flashscore -a filter_leagues=true`

## Logic
- The spider parses the initial list of matches.
- If a league header is found that is NOT in the whitelist, the spider **skips leading match detail pages** for that league.
- This significantly speeds up the run when only interested in major leagues.


---

# ELO Ratings Integration Walkthrough

## Feature Overview
Integrated Team ELO ratings from `soccer-rating.com` into the prediction pipeline. Matches now include `Home ELO` and `Away ELO` columns to assist in decision making.

## Components
1.  **Scraper (`elo_scraper.py`)**: Fetches current ratings from `soccer-rating.com`.
2.  **Entity Resolver (`entity_resolver.py`)**: Uses fuzzy matching (`rapidfuzz`) to map Flashscore team names to ELO source names (e.g. `Villarreal` -> `Villarreal CF`).
3.  **Integration**: `predict_matches.py` now resolves and includes ELO ratings in the prediction CSV.

## Verification
Output shows ELO ratings populated for matched teams:
```text
            Date                                  League    Home Team       Away Team Home ELO Away ELO
10.12.2025 19:45 EUROPE: Champions League - League phase   Villarreal   FC Copenhagen  2305.72  2140.56
10.12.2025 22:00 EUROPE: Champions League - League phase   Bayer Leverkusen  Newcastle  2316.35        -
```
*(Note: Teams not matched or not in top list show `-`)*
