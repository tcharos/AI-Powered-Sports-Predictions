# Task List

## 1. Feature Engineering: League Info
- [x] **Capture League Name**: Modify `flashscore_spider.py` to extract the league name for each match. <!-- id: 0 -->
- [x] **Data Pipeline Update**: Ensure the league field is propagated through `matches_YYYY-MM-DD.json` and loaded correctly in the prediction script. <!-- id: 1 -->

## 2. User Interface (Flask App)
- [x] **Setup Flask**: Initialize a simple Flask app structure. <!-- id: 2 -->
- [x] **Dashboard**: Create a view to list available prediction/verification dates. <!-- id: 3 -->
- [x] **Action Triggers**: Add buttons/routes to trigger `run_predictions.sh` and `run_verification.sh`. <!-- id: 4 -->
- [x] **Results View**: Display prediction results (CSV) with color coding (Green for high confidence). <!-- id: 5 -->
- [x] **Validation View**: Display validation accuracy metrics per league. <!-- id: 6 -->

## 3. Data Enhancement: ELO Ratings
- [x] **Source ELO Data**: Scrape or fetch data from `soccer-rating.com`. <!-- id: 7 -->
- [x] **Entity Resolution**: Implement fuzzy matching to map Flashscore team names to ELO rating names. <!-- id: 8 -->
- [x] **Model Integration**: Add Home/Away ELO columns to the features and retrain/update the model logic. <!-- id: 9 -->

## 4. Optimization: Targeted Scraping
- [x] **Filter Logic**: Modify the scraper to check if a league exists in our historical `data_sets` before scraping match details. <!-- id: 10 -->

## 5. Future Planning: Live Prediction & Heuristics
- [ ] **Draft Architecture**: Document a plan for live data ingestion (xG, score, shots). <!-- id: 11 -->
- [ ] **Heuristic Design**: Define the "Adjustment Layer" rules (e.g., Dominance Modifier). <!-- id: 12 -->

## 6. Housekeeping
- [x] **Artifact Sync**: Ensure these agent artifacts (`task.md`, plans) are saved in a `docs/agent` directory within the project. <!-- id: 13 -->
