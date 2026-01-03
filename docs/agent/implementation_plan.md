# Optimization: Targeted Scraping Plan

## Goal
Improve scraper efficiency by fetching matches ONLY for leagues where we have historical training data.

## User Review Required
> [!NOTE]
> This optimization assumes that if a league is not in `data_sets/MatchHistory`, we cannot predict it reliably anyway, so we skip scraping its details.

## Proposed Changes

### 1. Identify Target Leagues
- **Refined Logic**:
    - We implemented `ml_project/generate_target_leagues.py` which scans `data_sets/MatchHistory`.
    - It maps filename codes (e.g. `BEL-Jupiler`) to Flashscore names (`BELGIUM: Jupiler Pro League`).
    - It generates `data_sets/target_leagues.json`.
    - This ensures our scraping target matches our historical data coverage.

### 2. Implementation Details
- **File**: `flashscore_scraper/spiders/flashscore_spider.py`
- **Change**:
    - Add `known_leagues` set loaded from `data_sets/target_leagues.json`.
    - In `parse_match_list`, when a league header is found:
        - Check if `league_name` is in the whitelist.
        - If not, skip processing matches until next header.

## Verification
- Run `generate_target_leagues.py`.
- Run scraper with `-a filter_leagues=true`.
- Verify output only contains those leagues.
