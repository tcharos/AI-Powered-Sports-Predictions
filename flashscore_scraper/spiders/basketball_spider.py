import scrapy
from scrapy_playwright.page import PageMethod
import json
import datetime
import os

class BasketballSpider(scrapy.Spider):
    name = "basketball"
    allowed_domains = ["flashscore.com"]
    start_urls = ["https://www.flashscore.com/basketball/"]

    def __init__(self, days_back=0, *args, **kwargs):
        super(BasketballSpider, self).__init__(*args, **kwargs)
        self.days_back = int(days_back)
        self.logger.info("🏀 Basketball Spider Initialized (Main Page Mode).")

    def start_requests(self):
        yield scrapy.Request(
            url="https://www.flashscore.com/basketball/",
            meta={
                "playwright": True,
                "playwright_include_page": True,
                "playwright_context": "nba_list",
            },
            callback=self.parse_match_list
        )

    async def parse_match_list(self, response):
        page = response.meta["playwright_page"]
        self.logger.info("📄 Processing Main Basketball Page...")

        try:
            # Wait for matches
            await page.wait_for_selector('[id^="g_"]', timeout=30000)

            # --- NEXT DAY NAVIGATION (Main Page Logic) ---
            self.logger.info("📅 Navigating to Next Day (Tomorrow)...")
            clicked = False
            try:
                # Try aria-label first (consistency)
                if await page.locator("button[aria-label='Next day']").count() > 0:
                     await page.locator("button[aria-label='Next day']").click()
                     clicked = True
                     self.logger.info("Clicked Next day using aria-label.")
                elif await page.locator(".calendar__navigation--tomorrow").count() > 0:
                     await page.locator(".calendar__navigation--tomorrow").click()
                     clicked = True
                     self.logger.info("Clicked tomorrow arrow (legacy class).")
                elif await page.get_by_role("button", name="Next day").count() > 0:
                    await page.get_by_role("button", name="Next day").click()
                    clicked = True
                    self.logger.info("Clicked Next day button (role).")
                
                if clicked:
                    self.logger.info("⏳ Waiting for tomorrow's games to load...")
                    # Wait for reload
                    await page.wait_for_timeout(3000) 
                else:
                    self.logger.warning("⚠️ Next day button NOT found with any selector!")
            except Exception as e:
                self.logger.warning(f"⚠️ Error navigating to Next Day: {e}")
            # ---------------------------------------------
            
            # --- FILTER FOR NBA MATCHES ---
            self.logger.info("🔍 Searching for NBA matches...")
            
            # Strategy: Get all event headers and matches in order
            # We look for the header that contains "NBA" (and specifically "USA")
            # Then we take all following "event__match" divs until the next header.
            
            matches_data = []
            nba_section_found = False
            
            # DEBUG: Dump HTML to see class names
            content = await page.content()
            self.logger.info(f"📄 Page Content Snippet: {content[:1000]}...") # Log start of body
            
            # Strategy: Get all div elements that look like headers or matches
            # Modern Flashscore uses hashed classes sometimes.
            # We'll try a broader query if specific classes fail.
            
            elements = await page.query_selector_all('div') # Get ALL divs (optimization risk, but necessary for debug)
            # Actually, let's use the subagent's finding: .headerLeague__title
            # or try to find by text.
            
            self.logger.info("🔍 Searching for headers using text content...")
            
            matches_data = []
            nba_section_found = False
            
            # Iterate through a broader set of elements to find the structure
            # Let's try to query logical blocks
            rows = await page.query_selector_all('div > div') # Generic rows
            
            found_header_count = 0
            
            # Using the specific selector found by the browser subagent earlier:
            # "USA : NBA"
            # It might be an 'a' tag: a.headerLeague__title
            # Or a div with class 'wcl-leagueHeader'
            
            # Let's try the modern 'wcl-' classes or legacy 'event__'
            possible_headers = await page.query_selector_all('.event__header, .wcl-leagueHeader_86hww, [class*="leagueHeader"], [class*="event__header"]')
            
            for element in possible_headers:
                 text = await element.text_content()
                 self.logger.info(f"Potential Header: {text.strip()}")
                 if "NBA" in text and "USA" in text:
                     nba_section_found = True # Start capturing
                     # But we need to capture matches *after* this. 
                     # This loop only iterates headers. This approach is flawed if we split the loop.
            
            # REVERTING TO SEQUENTIAL SCAN:
            # We need a selector that catches BOTH headers and matches in order.
            # Try a common parent or sibling selector.
            
            # New Plan: Get all children of the main container.
            # .sportName.basketball > div
            
            container = await page.query_selector('.sportName.basketball')
            if container:
                children = await container.query_selector_all(':scope > div')
                self.logger.info(f"Found {len(children)} children in main container.")
                
                for child in children:
                    text = await child.text_content()
                    class_name = await child.get_attribute("class")
                    # self.logger.info(f"Child ({class_name}): {text[:50]}...")
                    
                    if "header" in str(class_name).lower() or "title" in str(class_name).lower():
                        if "NBA" in text and "USA" in text:
                            nba_section_found = True
                            self.logger.info("✅ Found NBA Header (Verified)!")
                        else:
                            nba_section_found = False
                    
                    elif nba_section_found and ("match" in str(class_name).lower() or "event" in str(class_name).lower()):
                         # Extract ID
                         box_id = await child.get_attribute("id")
                         if box_id and "_" in box_id:
                            parts = box_id.split("_")
                            match_id = parts[-1] 
                            matches_data.append(match_id)
            else:
                 self.logger.warning("Could not find .sportName.basketball container!")



            self.logger.info(f"🏀 Found {len(matches_data)} NBA matches for Tomorrow.")
            
            # Save IDs for reference
            output_dir = "output_basketball"
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            tomorrow = datetime.date.today() + datetime.timedelta(days=1)
            filename = os.path.join(output_dir, f"nba_matches_{tomorrow}.json")
            with open(filename, 'w') as f:
                json.dump(matches_data, f)
            self.logger.info(f"💾 Saved match list to {filename}")

            await page.close()

            # --- EXTRACT SCHEDULE ONLY (FAST) ---
            # We need ID, Home Team, Away Team to drive the Predictor
            # The odds scraping failed, but we have Schedule + Stats.
            
            # Since regex "matches_data" only gives raw IDs, we need to locate the ELEMENTS to get names
            # But the original logic used regex on `match_ids_json`.
            # To get names, we are better off using Playwright locators on the summary page?
            # actually, the `matches_data` is just IDs.
            # To get names quickly without visiting every page, we can use the `list_` API if possible?
            # Flashscore lists are complex.
            # Alternative: Visit each summary page (fast, no odds tab) and yield item.
            
            for match_id in matches_data:
                url = f"https://www.flashscore.com/match/{match_id}/"
                yield scrapy.Request(
                    url=url,
                    meta={
                        "playwright": True,
                        "playwright_include_page": True,
                        "playwright_context": f"match_{match_id}", 
                        "match_id": match_id,
                        "param_mode": "schedule_only"
                    },
                    callback=self.parse_match_odds
                )

        except Exception as e:
            self.logger.error(f"❌ Error parsing match list: {e}")
            await page.close()

    async def parse_match_odds(self, response):
        page = response.meta["playwright_page"]
        match_id = response.meta["match_id"]
        param_mode = response.meta.get("param_mode", "std")
        
        self.logger.info(f"🎲 Processing {match_id} (Mode: {param_mode})...")

        try:
            # Handle Cookie Banner (Fast check)
            try:
                if await page.query_selector("button#onetrust-accept-btn-handler"):
                    await page.click("button#onetrust-accept-btn-handler")
                    await page.wait_for_timeout(200)
            except Exception:
                pass

            # Wait for content
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_selector('.participant__participantName', timeout=10000)

            # Get Team Names using specific selectors
            home_team = "Unknown"
            away_team = "Unknown"
            try:
                # Try specific home/away containers first
                home_el = page.locator('.duelParticipant__home .participant__participantName')
                away_el = page.locator('.duelParticipant__away .participant__participantName')
                
                if await home_el.count() > 0 and await away_el.count() > 0:
                     home_team = await home_el.first.text_content()
                     away_team = await away_el.first.text_content()
                else:
                    # Fallback to general list (maybe mobile view?)
                    names = page.locator('.participant__participantName')
                    if await names.count() >= 2:
                        home_team = await names.nth(0).text_content()
                        # Use nth(1) if different, else maybe nth(2)
                        t1 = await names.nth(0).text_content()
                        t2 = await names.nth(1).text_content()
                        if t1.strip() == t2.strip() and await names.count() > 2:
                             away_team = await names.nth(2).text_content()
                        else:
                             away_team = t2
                
                home_team = home_team.strip()
                away_team = away_team.strip()
            except:
                pass
            
            # If Schedule Only, Yield and Exit
            if param_mode == "schedule_only":
                self.logger.info(f"✅ Schedule Found: {home_team} vs {away_team}")
                yield {
                    "match_id": match_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "date": str(datetime.date.today() + datetime.timedelta(days=1)),
                    "sport": "basketball",
                    "league": "nba"
                }
                await page.close()
                return

            # --- BELOW IS ODDS LOGIC (Skipped if schedule_only) ---
            # Debug: List all tabs
            tabs = await page.locator('.tabs__tab').all_text_contents()
            self.logger.info(f"📑 Available Tabs for {match_id}: {tabs}")

            # Click Odds Tab
            try:
                odds_tab = page.locator('a[href*="/odds-comparison"]')
                if await odds_tab.count() > 0:
                     await odds_tab.first.click()
                     self.logger.info("🖱️ Clicked Odds Tab (via href)")
                else:
                    # Try text matching
                    await page.locator('.tabs__tab', has_text="Odds").click()
                    self.logger.info("🖱️ Clicked Odds Tab (via text)")
                
                # Wait for navigation/update
                await page.wait_for_timeout(2000)
            except Exception as e:
                 self.logger.warning(f"⚠️ Failed to click Odds tab: {e}")
                 # Screenshot
                 await page.screenshot(path=f"debug_tabs_{match_id}.png")

            # Wait for Odds content
            try:
                await page.wait_for_selector('.ui-table__row .oddsCell__odd', timeout=10000)
            except:
                self.logger.warning("Timeout waiting for odds cells - maybe empty?")

            # Scrape available providers
            # Just grab the first one we see to verify success
            rows = await page.locator('.ui-table__row').all()
            found_data = []
            
            for row in rows:
                # Check for odds cells
                odds_cells = row.locator('.oddsCell__odd')
                if await odds_cells.count() >= 2:
                    # Get provider name if possible
                    # Usually in a 'a.prematchLink' inside the row or previous cell
                    provider_node = row.locator('a.prematchLink')
                    provider_name = "Unknown"
                    if await provider_node.count() > 0:
                        provider_name = await provider_node.get_attribute("title")
                    
                    val1 = await odds_cells.nth(0).text_content()
                    val2 = await odds_cells.nth(1).text_content()
                    
                    found_data.append(f"{provider_name}: {val1}/{val2}")
                    
                    # We only need one decent one
                    if "Stoiximan" in provider_name or "bet365" in provider_name:
                        break
            
            self.logger.info(f"✅ Extracted Data: {found_data}")

            # Construct Item
            item = {
                "match_id": match_id,
                "raw_odds_data": found_data,
                "date": str(datetime.date.today() + datetime.timedelta(days=1)),
                "sport": "basketball",
                "league": "nba"
            }
            
            yield item

        except Exception as e:
            self.logger.error(f"❌ Error scraping {market_type} for {match_id}: {e}")
            # Screenshot on error
            shot_path = f"error_{market_type}_{match_id}.png"
            await page.screenshot(path=shot_path)
        finally:
            await page.close()
