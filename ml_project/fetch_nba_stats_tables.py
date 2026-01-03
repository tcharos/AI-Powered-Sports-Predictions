
import csv
import json
import os
import sys
from playwright.sync_api import sync_playwright

CSV_PATH = "data_sets/NBA/nba_standings_form_links.csv"
OUTPUT_DIR = "data_sets/NBA"

def scrape_table(page, url, type_name):
    print(f"Stats: Navigating to {type_name} ({url})...")
    page.goto(url)
    try:
        page.wait_for_selector(".ui-table__row", timeout=15000)
    except:
        print(f"⚠️ Timeout waiting for table: {type_name}")
        return {}
    
    # Handle Cookie Banner
    try:
        if page.locator("button#onetrust-accept-btn-handler").count() > 0:
            page.click("button#onetrust-accept-btn-handler")
            page.wait_for_timeout(500)
    except:
        pass

    data = {}
    rows = page.locator(".ui-table__row")
    count = rows.count()
    print(f"Found {count} rows.")
    
    for i in range(count):
        row = rows.nth(i)
        try:
            team_name = row.locator(".tableCellParticipant__name").text_content().strip()
            
            # Extract basic stats if available
            # Try multiple selectors for cell values
            cells = row.locator(".ui-table__cell, .table__cell, .tableCellValue")
            cell_values = []
            count_cells = cells.count()
            
            for j in range(count_cells):
                txt = cells.nth(j).text_content().strip()
                # formatting: remove newlines/extra spaces if any
                txt = " ".join(txt.split())
                cell_values.append(txt)
            
            # Debug: if empty, print row html
            if not cell_values and i == 0:
                print(f"DEBUG: First row HTML: {row.inner_html()[:200]}...")

            # Common structure: Rank, Team, Games, Wins, Losses, Goals, Points, Form
            item = {
                "raw_cells": cell_values
            }
            
            # Form extraction (last 5 icons)
            form_icons = row.locator(".tableCellFormIcon")
            if form_icons.count() > 0:
                form_seq = []
                for k in range(form_icons.count()):
                    cls = form_icons.nth(k).get_attribute("class")
                    if "w" in cls.lower(): form_seq.append("W")
                    elif "l" in cls.lower(): form_seq.append("L")
                    else: form_seq.append("?")
                item["form_sequence"] = form_seq
            
            data[team_name] = item
            
        except Exception as e:
            print(f"Error parsing row {i}: {e}")
            continue
            
    return data

def main():
    if not os.path.exists(CSV_PATH):
        print(f"CSV not found: {CSV_PATH}")
        return

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        with open(CSV_PATH, 'r') as f:
            reader = csv.reader(f, delimiter=';')
            headers = next(reader)
            row = next(reader)
            
            for header, url in zip(headers, row):
                if not url or "flashscore" not in url:
                    continue
                
                print(f"Processing {header}...")
                stats_data = scrape_table(page, url.strip(), header)
                
                filename = os.path.join(OUTPUT_DIR, f"{header.lower()}.json")
                with open(filename, 'w') as out:
                    json.dump(stats_data, out, indent=2)
                print(f"Saved to {filename}")

        browser.close()

if __name__ == "__main__":
    main()
