import requests
import zipfile
import io
import os
import sys

# Config
BASE_URL = "https://www.football-data.co.uk/mmz4281"
TARGET_DIR = "data_sets/MatchHistory"

# 1. Main Leagues (from data.zip) -> Mapped to match legacy data_sets/MatchHistory names
MAIN_MAPPING = {
    "E0": "ENG-Premier_League",
    "E1": "ENG-Championship",
    "E2": "ENG-League_1",      # Legacy: League_1
    "E3": "ENG-League_2",      # Legacy: League_2
    "EC": "ENG-Conference",    # Legacy: Conference
    "D1": "GER-Bundesliga",
    "D2": "GER-Bundesliga_2",  # Legacy: Bundesliga_2
    "I1": "ITA-Serie_A",
    "I2": "ITA-Serie_B",
    "SP1": "ESP-La_Liga",      # Legacy: La_Liga
    "SP2": "ESP-Segunda",      # Legacy: Segunda
    "F1": "FRA-Ligue_1",
    "F2": "FRA-Ligue_2",
    "N1": "NED-Eredivisie",
    "B1": "BEL-Jupiler_League",# Legacy: Jupiler_League
    "P1": "POR-Liga_1",        # Legacy: Liga_1
    "T1": "TUR-Ligi_1",        # Legacy: Ligi_1
    "SC0": "SCO-Premier_League",# Legacy: Premier_League
    "SC1": "SCO-Division_1",    # Legacy: Division_1
    "SC2": "SCO-Division_2",    # Legacy: Division_2
    "SC3": "SCO-Division_3",    # Legacy: Division_3
    "G1": "GR-Super_League"     # Legacy: GR-, not GRE-
}

# 2. Extra Leagues (Direct URLs)
# Mapped to: (URL_CODE, TARGET_FILENAME_BASE)
EXTRA_LEAGUES_MAP = {
    "ARG": ("ARG", "ARG-Liga_Profesional"),
    "AUT": ("AUT", "AUT-Bundesliga"),
    "BRA": ("BRA", "BRA-Serie_A"),
    "CHN": ("CHN", "CHN-Super_League"),
    "DNK": ("DNK", "DEN-Superliga"), # Country code DEN in target_leagues? "DENMARK: Superliga" -> DEN-Superliga probably standard
    "FIN": ("FIN", "FIN-Veikkausliiga"),
    "IRL": ("IRL", "IRL-Premier_Division"),
    "JPN": ("JPN", "JPN-J1_League"),
    "MEX": ("MEX", "MEX-Liga_MX"),
    "NOR": ("NOR", "NOR-Eliteserien"),
    "POL": ("POL", "POL-Ekstraklasa"),
    "ROU": ("ROU", "ROU-Liga_1"),
    "RUS": ("RUS", "RUS-Premier_League"),
    "SWZ": ("SWZ", "SUI-Super_League"), # SWZ usually Switzerland in football-data context
    "SWE": ("SWE", "SWE-Allsvenskan"),
    "USA": ("USA", "USA-MLS")
}

def download_file(url):
    print(f"Fetching {url}...")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[-] Failed to download {url}: {e}")
        return None

def setup_data(season):
    # Season input format: "2526" (for 2025/2026) -> used for filenames
    if len(season) != 4:
        print("[-] Invalid season format. Use '2526' for 2025-2026.")
        return

    season_str = f"{season[:2]}-{season[2:]}" # 25-26
    print(f"[*] Setting up data for Season {season_str}...")
    
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)
        print(f"[+] Created directory {TARGET_DIR}")

    # --- Step 1: Main Data Zip (European Major Leagues) ---
    # Usually standard URL structure: mmz4281/{season}/data.zip
    zip_url = f"{BASE_URL}/{season}/data.zip"
    zip_content = download_file(zip_url)
    
    if zip_content:
        print("[*] Extracting Main Leagues...")
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                for filename in z.namelist():
                    if not filename.endswith(".csv"):
                        continue
                    
                    code = os.path.splitext(os.path.basename(filename))[0]
                    
                    if code in MAIN_MAPPING:
                        target_name = f"{MAIN_MAPPING[code]}_{season_str}.csv"
                        target_path = os.path.join(TARGET_DIR, target_name)
                        
                        with open(target_path, 'wb') as f:
                            f.write(z.read(filename))
                        print(f"    -> Saved {target_name}")
        except Exception as e:
            print(f"[-] Error parsing zip: {e}")
    else:
        print("[-] Main data zip not found (common for very early season or extra leagues only).")

    # --- Step 2: Extra Leagues (Direct /new/ URLs) ---
    # Note: /new/ directory usually contains the *current* active season for these leagues.
    # If downloading for past seasons, the URL might differ (e.g. mmz4281/2425/ARG.csv).
    # However, user explicitly provided /new/ links. We will adhere to that for simplicity 
    # but strictly speaking this might fetch "latest" instead of "matched historical".
    # For now, we fetch from /new/ as requested.
    
    print("\n[*] Fetching Extra Leagues...")
    for code, (url_code, base_name) in EXTRA_LEAGUES_MAP.items():
        # User provided: https://www.football-data.co.uk/new/{CODE}.csv
        # We can try /new/ first. If creating a strictly historical dataset for specific past season,
        # we might need logic to try mmz4281/{season}/{code}.csv if available.
        
        # Strategy: Try /new/ (Latest/Current)
        csv_url = f"https://www.football-data.co.uk/new/{url_code}.csv"
        
        csv_content = download_file(csv_url)
        
        if csv_content:
            # Extra leagues from /new/ are consolidated histories (all seasons).
            # Do NOT append season suffix. Use base name directly.
            target_name = f"{base_name}.csv"
            target_path = os.path.join(TARGET_DIR, target_name)
            
            with open(target_path, 'wb') as f:
                f.write(csv_content)
            print(f"    -> Saved {target_name} (Consolidated History)")
    
    print("\n[+] Setup Complete.")

if __name__ == "__main__":
    print("--- Football Data Downloader ---")
    if len(sys.argv) > 1:
        s = sys.argv[1]
    else:
        s = input("Enter Season (e.g. 2425, 2526): ").strip()
    
    setup_data(s)
