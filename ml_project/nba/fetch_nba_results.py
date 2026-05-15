import asyncio
from playwright.async_api import async_playwright
import json
import datetime
import os
import argparse

OUTPUT_DIR = "output_basketball"

async def fetch_nba_results(target_date_str):
    # Format date for URL: 2025-12-13 -> 20251213
    dt = datetime.datetime.strptime(target_date_str, "%Y-%m-%d")
    date_url_str = dt.strftime("%Y%m%d")
    
    url = f"https://www.espn.com/nba/scoreboard/_/date/{date_url_str}"
    print(f"Loading ESPN NBA Scoreboard for {target_date_str} ({url})...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto(url, timeout=60000)
        
        # Wait for scoreboard
        try:
            await page.wait_for_selector('.ScoreboardScoreCell', timeout=15000)
        except:
            print("Timeout waiting for Scoreboard. Taking screenshot...")
            await page.screenshot(path="espn_results_debug.png")
        
        # Extract Data via JS
        games = await page.evaluate('''() => {
            const results = [];
            // Select all game containers
            // Structure: .ScoreboardScoreCell contains .ScoreboardScoreCell__Competitors
            
            const containers = document.querySelectorAll('.ScoreboardScoreCell');
            
            containers.forEach(container => {
                const teamNames = container.querySelectorAll('div[class*="ScoreCell__TeamName"]');
                const scores = container.querySelectorAll('div[class*="ScoreCell__Score"]');
                
                if (teamNames.length >= 2 && scores.length >= 2) {
                    const awayTeam = teamNames[0].innerText.trim();
                    const homeTeam = teamNames[1].innerText.trim();
                    const awayScore = scores[0].innerText.trim();
                    const homeScore = scores[1].innerText.trim();
                    
                    results.push({
                        "home_team": homeTeam,
                        "away_team": awayTeam,
                        "home_score": parseInt(homeScore),
                        "away_score": parseInt(awayScore),
                        # Usually ESPN shows Away then Home in vertical stack
                    });
                }
            });
            return results;
        }''')
        
        print(f"Extracted {len(games)} games.")
        
        # Save
        filename = f"results_nba_{target_date_str}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        with open(filepath, 'w') as f:
            json.dump(games, f, indent=2)
            
        print(f"Saved results to {filepath}")
        await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    args = parser.parse_args()
    
    asyncio.run(fetch_nba_results(args.date))
