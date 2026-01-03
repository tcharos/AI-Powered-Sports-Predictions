import asyncio
from playwright.async_api import async_playwright
import json
import datetime
import os

OUTPUT_FILE = "output_basketball/espn_odds.json"

async def fetch_espn_odds():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Calculate Tomorrow's Date
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        date_str = tomorrow.strftime("%Y%m%d")
        url = f"https://www.espn.com/nba/odds/_/date/{date_str}"
        
        print(f"Loading ESPN NBA Odds for {tomorrow} ({url})...")
        await page.goto(url, timeout=60000)
        
        # Wait for meaningful content or potential error
        try:
            await page.wait_for_selector('.Odds__Matchup', timeout=10000)
        except:
            print("Timeout waiting for '.Odds__Matchup'. Taking debug screenshot...")
            await page.screenshot(path="espn_debug.png")
            # Dump HTML to see what's actually there
            content = await page.content()
            print(f"Page Content Length: {len(content)}")
            # DEBUG: Find "Hawks" to locate structure
            if "Hawks" in content:
                idx = content.find("Hawks")
                print(f"--- FOUND 'Hawks' at index {idx} ---")
                start = max(0, idx - 300)
                end = min(len(content), idx + 300)
                print(content[start:end])
                print("--- END SNIPPET ---")
            else:
                print("--- 'Hawks' NOT FOUND in HTML content ---")
        # Strategy: Iterate through the page content linearly to track Date Headers
        # The structure usually is:
        # Header (Day) -> Container with Games -> Header (Next Day) -> ...
        
        # We'll select all relevant elements: Headers (h3, .Table__Title) and Game Links
        # It's hard to get a flat list of different types in order easily with simple selectors if they are nested differently.
        # But usually these tables are distinct.
        
        # Alternative: Get all "Card" wrappers or "Table" wrappers.
        # Screenshot shows: "Sunday, December 14" then a block. "Monday, December 15" then a block.
        
        # Let's try to get all text content of H3s to see the dates.
        # And try to match games to the nearest preceding H3.
        
        # Implementation:
        # 1. Get all Game Links (with data-track-extras).
        # 2. For each link, evaluate its Y-position or strict DOM order relative to headers? 
        #    Playwright 'evaluate' can help find the 'closest' header or previous sibling.
        
        # Better:
        # Select all elements that could be headers OR game rows? No.
        
        # Let's use JS evaluation in browser to map each game link to its date section.
        
        games_data = await page.evaluate('''() => {
            const games = [];
            let currentDate = "Unknown";
            
            // Helper to clean text
            const clean = (text) => text ? text.replace(/\\n/g, ' ').trim() : "";

            // ESPN Odds Structure Strategy:
            // Look for headers `h3` or elements containing Date-like text.
            // Look for `a` tags with `data-track-extras`.
            
            // We iterate all elements in body? Too slow.
            // Let's iterate distinct sections if possible.
            // Screenshot shows headers are likely `div` or `h3` with class `Card__Header` or simply text.
            
            // Let's iterate all `h3` and `div.Table__Title` and `a[data-track-extras]` in document order.
            // We can use `document.querySelectorAll('h3, .Table__Title, a[data-track-extras*="game_detail"]')`
            
            const elements = document.querySelectorAll('div, h3, h2, h4, .Table__Title, a[data-track-extras*="game_detail"]');
            
            // Debug: Capture what text looks like a date
            // Strategy: Iterate and if text matches "Monday, December" etc.
            
            elements.forEach(el => {
                const text = clean(el.innerText);
                const isDateLike = (text.includes("Monday") || text.includes("Sunday") || text.includes("Tuesday") || text.includes("Today") || text.includes("Tomorrow")) && text.length < 50;
                
                if (isDateLike) {
                    currentDate = text;
                    try {
                        // Debug log to python via some way? Just assign it.
                        // We assume strict document order.
                    } catch(e) {}
                } else if (el.tagName === 'A' && el.hasAttribute('data-track-extras')) {
                    const extras = el.getAttribute('data-track-extras');
                    games.push({
                        date_header: currentDate,
                        extras: extras,
                        raw_text: text
                    });
                }
            });
            return games;
        }''')
        
        print(f"Extracted {len(games_data)} raw items via JS evaluation.")
        
        games_map = {}
        
        for item in games_data:
            try:
                date_header = item["date_header"]
                extras_str = item["extras"]
                cell_text = item["raw_text"]
                
                # Parse Date Header "Sunday, December 14" -> "2025-12-14"
                # We need the Year. Assume "Upcoming" means current year or next year.
                # Since we are in Dec 2025 (per User Context), we can parse smart.
                # Header might be "Today, ..." or just "NBA Odds".
                # If "NBA Odds", skip.
                
                if "NBA Odds" in date_header:
                    continue 

                # Parse Extras (Logic reused)
                if "game_detail" not in extras_str:
                    continue
                
                # ... same parsing logic ...
                # Extract "Team A vs Team B"
                # Locate start of value
                marker = '&quot;game_detail&quot;:&quot;'
                start = extras_str.find(marker)
                if start == -1:
                    marker = '"game_detail":"' # Try unescaped
                    start = extras_str.find(marker)
                
                if start != -1:
                    start += len(marker)
                    end = extras_str.find('&quot;', start)
                    if end == -1: end = extras_str.find('"', start)
                    
                    game_detail = extras_str[start:end]
                    parts = game_detail.split(' ', 1)
                    if len(parts) < 2: continue
                    matchup = parts[1]
                    if " vs " not in matchup: continue
                    team_a, team_b = matchup.split(' vs ')
                    
                    # Store
                    if matchup not in games_map:
                        games_map[matchup] = {
                            "home_team": team_b,
                            "away_team": team_a,
                            "odds_data": [],
                            "date_raw": date_header
                        }
                    games_map[matchup]["odds_data"].append(cell_text)

            except Exception as e:
                pass
        
        # Convert map to list
        games = []
        for matchup, data in games_map.items():
            games.append({
                "matchup": matchup,
                "home_team": data["home_team"],
                "away_team": data["away_team"],
                "raw_odds": " | ".join(data["odds_data"]),
                "date_header": data["date_raw"],
                "source": "espn"
            })
            
        print(f"Extracted {len(games)} unique games with dates.")
        
        current_date_str = "Tomorrow" # Default fallback
        
        # Save placeholder for now to avoid empty file error causing crashes elsewhere
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(games, f, indent=2)
            
        print(f"Saved to {OUTPUT_FILE}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(fetch_espn_odds())
