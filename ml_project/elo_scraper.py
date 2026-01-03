import requests
from bs4 import BeautifulSoup
import json
import os
import time

class EloScraper:
    BASE_URL = "https://www.soccer-rating.com"
    # We will generate URLs dynamically
    
    def __init__(self, output_file="data_sets/elo_ratings.json"):
        self.output_file = output_file
        self.ratings = {}

    def fetch_ratings(self):
        print("Fetching ELO Ratings...")
        
        # 1. Scrape Europe Top 1000 (0 to 1000 in steps of 100)
        # URL format: https://www.soccer-rating.com/ranking.php?start=0
        for start in range(0, 1100, 100):
            url = f"https://www.soccer-rating.com/ranking.php?start={start}"
            self.scrape_url(url)
            
        # 2. Add other regions if necessary (South America)
        # https://www.soccer-rating.com/South-America/
        self.scrape_url("https://www.soccer-rating.com/South-America/")
        
        # 3. Add International (Top 100)
        self.scrape_url("https://www.soccer-rating.com/International/")

        self.save_ratings()
        print(f"Saved {len(self.ratings)} ratings to {self.output_file}")
        
    def scrape_url(self, url):
        try:
            print(f"Scraping {url}...")
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                self.parse_page(resp.text)
            time.sleep(1) # Be polite
        except Exception as e:
            print(f"Error scraping {url}: {e}")

    def parse_page(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        # Structure: Team Name is in <a>, Rating follows standard pattern
        # Common row structure: <td class="rp"> 1 </td> <td> <a href="...">Team</a> </td> <td> Code </td> <td> Rating </td>
        # Actually from research, it seemed more like text flow. Let's try flexible parsing.
        
        # Look for the main table content.
        # Often rows are <tr>
        rows = soup.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 4:
                # Check if it looks like a ranking row
                # Col 0: Rank (number)
                # Col 1: Team Name (link)
                # Col 2: League Code
                # Col 3: Rating
                
                try:
                    rank_txt = cols[0].get_text(strip=True).replace('.', '')
                    if not rank_txt.isdigit():
                         continue
                         
                    team_link = cols[1].find('a')
                    if team_link:
                        team_name = team_link.get_text(strip=True)
                        # Column 2 is Flag IMG
                        # Column 3 is League Code
                        # Column 4 is Rating
                        if len(cols) >= 5:
                            rating_txt = cols[4].get_text(strip=True)
                        else:
                            # Fallback if no flag
                            rating_txt = cols[3].get_text(strip=True)
                        
                        # Clean rating
                        rating = float(rating_txt.replace(',', ''))
                        
                        self.ratings[team_name] = rating
                except:
                    continue

    def save_ratings(self):
        # Create dir if needed
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        with open(self.output_file, 'w') as f:
            json.dump(self.ratings, f, indent=4)

if __name__ == "__main__":
    scraper = EloScraper()
    scraper.fetch_ratings()
