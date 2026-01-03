# Task List

## 1. Feature Engineering: League Info
- [x] **Capture League Name**: Modify `flashscore_spider.py` to extract the league name for each match. <!-- id: 0 -->
- [x] **Data Pipeline Update**: Ensure the league field is propagated through `matches_YYYY-MM-DD.json` and loaded correctly in the prediction script. <!-- id: 1 -->

## 2. User Interface (Flask App)
- [x] **Setup Flask**: Initialize- [x] Create Flask application structure
- [x] Build dashboard for managing operations
- [x] Implement log streaming/viewing
- [x] Add server management controls (Start/Stop/Restart)
- [x] Add data management controls (Update History) to list available prediction/verification dates. <!-- id: 3 -->
- [x] **Action Triggers**: Add buttons/routes to trigger `run_predictions.sh` and `run_verification.sh`. <!-- id: 4 -->
- [x] **Results View**: Display prediction results (CSV) with color coding (Green for high confidence). <!-- id: 5 -->
- [x] **Validation View**: Display validation accuracy metrics per league. <!-- id: 6 -->

## 3. Data Enhancement: ELO Ratings
- [ ] **Source ELO Data**: Scrape or fetch data from `soccer-rating.com`. <!-- id: 7 -->
- [ ] **Entity Resolution**: Implement fuzzy matching to map Flashscore team names to ELO rating names. <!-- id: 8 -->
- [x] **Model Integration**: Add Home/Away ELO columns to the features and retrain/update the model logic. <!-- id: 9 -->
- [x] **Model Update**: Incorporate 25/26 season data into training.
- [x] **Feature Engineering (Base)**: Improve features using detailed match stats (Shots, Corners, etc). <!-- id: 7 -->
- [x] **Feature Engineering (H/A Specific)**: Add last 5 Home-only and Away-only stats (Goals, Shots, Corners) to training. <!-- id: 11 -->

## 4. Optimization: Targeted Scraping
- [x] **Filter Logic**: Modify the scraper to check if a league exists in our historical `data_sets` before scraping match details. <!-- id: 10 -->

## 5. Automation & Live Analysis
- [x] **Live Loop**: Implement 10-minute automated loop for live analysis with Start/Stop controls. <!-- id: 12 -->
- [x] **Betting Engine (Paper Trading)**:
    - [x] **Config**: Set confidence threshold and base bet units.
    - [x] **Logic**: Auto-place bets on high-confidence matches.
    - [x] **UI**: Show historical bets, outcomes, and P/L. <!-- id: 13 -->

## 6. Future Planning: Live Prediction & Heuristics
- [x] **Draft Architecture**: Document a plan for live data ingestion (xG, score, shots). <!-- id: 11 -->
- [x] **Heuristic Design**: Define the "Adjustment Layer" rules (e.g., Dominance Modifier). <!-- id: 12 -->

## 7. Verification Enhancements
- [x] **Cumulative Stats**: Implement `LeagueStatsManager` to persist correct/incorrect counts per league in `league_analytics.json`. <!-- id: 14 -->
- [x] **Visual Feedback**: Update `results.html` to clearly mark rows Green (Success) / Red (Fail) and display the cumulative stats table. <!-- id: 15 -->
- [x] **Scraping Optimization**: Implement `mode=verification` in spider to extract scores from list view and skip detail pages. <!-- id: 16 -->

## 7. Form & UI Enhancements
- [x] **Form Visualization**: Add "Last 5 Matches" (W/D/L) badges to prediction results. <!-- id: 17 -->
- [x] **Dashboard Layout**: Move Cumulative Stats table below Actions panel. <!-- id: 18 -->
- [x] **H2H Scraping Fix**: Fix blank score extraction by handling newline separators in `flashscore_spider.py`. <!-- id: 19 -->

## 8. Refinement & Bug Fixes
- [x] **Form Order Check**: Verify if "Last 5" string is Newest->Oldest. Verified: Left is Newest. <!-- id: 20 -->
- [x] **League Filtering**: Exclude matches from leagues with no historical data (e.g., Chile, Ecuador) in predictions. <!-- id: 21 -->
- [x] **Bet365 Debug**: Fix "O/U 2.5 not found" warning in scraper. Enhanced navigation with direct URL fallback. <!-- id: 22 -->
- [x] **O/U Extraction Fix**: Debug and fix O/U 2.5 odds extraction for all matches (specifically fixing match `0Eajeswq` failure).
- [x] **Performance Optimization**: Scrape match URLs directly from list to skip initial navigation and reduce timeouts. <!-- id: 26 -->
- [x] **Navigation Fix**: Resolved 90s timeout in `parse_match_list` by replacing `networkidle` with `domcontentloaded`.
- [x] **Stop Button**: Added Stop functionality for Prediction and Verification tasks in the Web UI. <!-- id: 27 -->
- [x] **UI Refinement**: Reorder prediction columns (1X2 then O/U) and add match count to filenames in UI. <!-- id: 23 -->
- [x] **Sticky Header**: Make predictions table header sticky on scroll. <!-- id: 24 -->
- [x] **Verification UI**: Reorder verification columns (Grouped 1X2/OU). <!-- id: 25 -->
- [x] **Prediction Odd**: Add "Prediction 1X2 Odd" column showing the odd of the predicted result. <!-- id: 26 -->
- [x] **Debug League Filter**: Added 'EUROPE' and 'WORLD' to supported countries. Confirmed Eng Champ/L1/L2 have NO matches on Dec 11th. <!-- id: 27 -->
- [x] **Debug O/U**: Refined JS to search for Bet365 ID (16) first, then validate '2.5' row. <!-- id: 28 -->

## 9. Housekeeping
- [x] **Artifact Sync**: Ensure these agent artifacts (`task.md`, plans) are saved in a `docs/agent` directory within the project. <!-- id: 13 -->

# Phase 3: Live Prediction & Adjustments
- [x] **Live Data Scraper**: Extend scraper to fetch live statistics (Score, xG, Shots, Possession) for a specific match. <!-- id: 28 -->
- [x] **Debugging & Live Data Fixes**
    - [x] Debug why `run_live_analysis.py` fails to find live matches (Fix selectors/iframe)
    - [x] Fix `web_ui` "Refresh Live Data" button feedback (make it show loading state)
    - [x] Investigate why `minute: 0` / early scores don't adjust probabilities (Logic check)
    - [x] Implement "Batch Scraping" for live stats to speed up the loop (Fetch 20 matches in 1 browser session)
    - [x] Tune `LiveAdjuster` logic (Reduced aggressiveness of 1-0 boost)
    - [x] Filter Women's Leagues from Live Analysis & Predictions (Global exclusion)
    - [x] Fix Missing Stats (Clicked Stats tab, robust text search)
    - [x] Fix Stale Data (Implemented explicit Cache Busting)
    - [x] Fix Time Extraction (Regex for `mm:ss` support)
- [x] **Adjustment Engine**: Implement `LiveAdjuster` class to modify pre-match probabilities based on "Dominance" and "Time/Score". <!-- id: 29 -->
    - [x] Implement "Dominance Modifier" (e.g., High Shots/xG = boost win prob).
    - [x] Implement "Draw Tuning" (Time decay + Score equality logic).
    - [x] Implement "Pressure Cooker" & "Sterile Possession" logic.
- [x] **UI Integration**: Add "Live Update" button to the Dashboard for active matches. <!-- id: 30 -->
