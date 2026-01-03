# NBA Data Source Evaluation

This document compares 5 potential sources for NBA data (Historic & Current) to power the "Basketball Expansion" prediction model.

## 🌟 The Critical Requirement: Betting Odds
For a betting model, **Historical Odds** are just as important as Game Stats. Without them, we cannot backtest the strategy or calculate EV (Expected Value).

## 1. NBA API (KnowledgeOwl / Official)
*   **Type**: Documented endpoints of the official `stats.nba.com`.
*   **Stats Coverage**: 🏆 **Best**. Includes tracking data, shot charts, and every possible metric.
*   **Odds Coverage**: ❌ **None**. The NBA does not publish official betting lines via this API.
*   **Verdict**: Essential for *feature engineering* (Pace, Efficiency), but useless for *betting targets*.

## 2. pbpstats (Python Library)
*   **Type**: Open-source wrapper for NBA stats.
*   **Stats Coverage**: ⭐ **Excellent**. Great for possession-by-possession data (Lineups, Q1/Q2/Q3/Q4 splits).
*   **Odds Coverage**: ❌ **None**. The library documentation explicitly states it does not support betting data.
*   **Verdict**: Best tool for building the "Brain" of the model, but needs a separate "Odds" source.

## 3. Basketball Reference Scraper
*   **Type**: Web Scraper.
*   **Stats Coverage**: ✅ **Good**. Basic box scores and advanced stats.
*   **Odds Coverage**: ❌ **None**. Basketball Reference focuses on records, not gambling.
*   **Reliability**: ⚠️ **Low**. Scrapers break often when the website layout changes.
*   **Verdict**: Not recommended. `pbpstats` relies on the API which is more stable than HTML scraping.

## 4. API-Sports (NBA v2)
*   **Type**: Commercial API.
*   **Stats Coverage**: ✅ **Good**. Standard box scores, quarters, and player stats.
*   **Odds Coverage**: ✅ **Yes**. Includes pre-match odds from major bookmakers.
*   **Cost**: 💰 **Freemium**. Free tier (100 req/day) is enough for *daily updates*, but might be tight for *historical backfilling*.
*   **Verdict**: **strong Contender**. It is the only "All-in-One" solution on this list.

## 5. Kaggle Dataset (Wyatt Walsh)
*   **Type**: Static SQLite/CSV Database.
*   **Stats Coverage**: ✅ **Great**. Daily updated dump of NBA Stats endpoints.
*   **Odds Coverage**: ❌ **None**. Purely performance statistics.
*   **Verdict**: Good for initial model training (learning "who wins"), but cannot calculate "ROI".

---

## 🏆 Recommendation

### Option A: The "All-in-One" (Easiest)
**Use API-Sports (Source 4)**.
*   **Pros**: Single API for Stats + Odds. Stable.
*   **Cons**: Free limit (100/day) makes downloading 10 years of history slow (would take months at 100/day).

### Option B: The "Hybrid" (Best for Analysis)
**Combine `pbpstats` (Source 2) + Our Own Flashscore Scraper**.
*   **Stats**: Use `pbpstats` to get deep possession data for the "Brain".
*   **Odds**: Use the `basketball_spider.py` (from our Plan) to scrape Flashscore for historical odds.
*   **Pros**: Free, Unlimited History, Deepest Stats (pbpstats is better than API-Sports).
*   **Cons**: Requires maintaining the scraper (which we already planned to do).

### 🚀 Recommendation: Option B
Since we already have a robust Scrapy pipeline, **Option B** gives us the competitive edge (better stats from pbpstats + specific bookmaker odds from Flashscore) for $0.
