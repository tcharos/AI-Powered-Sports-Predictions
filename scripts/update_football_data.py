import requests
import zipfile
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ml_project'))
from football_data_leagues import MAIN_MAPPING, seasons_to_refresh  # noqa: E402

# Config
BASE_URL = "https://www.football-data.co.uk/mmz4281"
TARGET_DIR = "data_sets/MatchHistory"

# Extra Leagues (Direct URLs)
EXTRA_LEAGUES_MAP = {
    "ARG": ("ARG", "ARG-Liga_Profesional"),
    "AUT": ("AUT", "AUT-Bundesliga"),
    "BRA": ("BRA", "BRA-Serie_A"),
    "CHN": ("CHN", "CHN-Super_League"),
    "DNK": ("DNK", "DEN-Superliga"), 
    "FIN": ("FIN", "FIN-Veikkausliiga"),
    "IRL": ("IRL", "IRL-Premier_Division"),
    "JPN": ("JPN", "JPN-J1_League"),
    "MEX": ("MEX", "MEX-Liga_MX"),
    "NOR": ("NOR", "NOR-Eliteserien"),
    "POL": ("POL", "POL-Ekstraklasa"),
    "ROU": ("ROU", "ROU-Liga_1"),
    "RUS": ("RUS", "RUS-Premier_League"),
    "SWZ": ("SWZ", "SUI-Super_League"), 
    "SWE": ("SWE", "SWE-Allsvenskan"),
    "USA": ("USA", "USA-MLS")
}

def download_file(url, missing_ok=False):
    print(f"Fetching {url}...")
    try:
        r = requests.get(url, timeout=30)
        if missing_ok and r.status_code == 404:
            print(f"[=] Not published yet: {url}")
            return None
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[-] Failed to download {url}: {e}")
        return None

def update_main_season(season_code, season_suffix, missing_ok):
    """Extract one season's data.zip into per-league CSVs. Returns files written."""
    written = 0
    zip_content = download_file(f"{BASE_URL}/{season_code}/data.zip", missing_ok=missing_ok)
    if not zip_content:
        return 0

    print(f"[*] Extracting Main Leagues ({season_suffix})...")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
            for filename in z.namelist():
                if not filename.endswith(".csv"):
                    continue

                code = os.path.splitext(os.path.basename(filename))[0]

                if code in MAIN_MAPPING:
                    target_name = f"{MAIN_MAPPING[code]}_{season_suffix}.csv"
                    target_path = os.path.join(TARGET_DIR, target_name)

                    with open(target_path, 'wb') as f:
                        f.write(z.read(filename))
                    written += 1
                    print(f"    -> Updated {target_name}")
    except Exception as e:
        print(f"[-] Error parsing zip: {e}")
    return written

def update_data():
    """Refresh the football-data.co.uk CSVs. Returns the number of files written."""
    # The season is derived from today's date. It used to be a constant
    # ("2526"), which silently stopped tracking results when 2026/27 began.
    (prev_code, prev_suffix), (cur_code, cur_suffix) = seasons_to_refresh()
    print(f"[*] Starting Data Update for Seasons {prev_suffix} + {cur_suffix}...")
    written = 0
    
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)

    # --- Step 1: Main Data Zips ---
    # The previous season is refreshed too so its final weeks are never lost
    # across the rollover. The current one may not be published yet in early
    # July, so a 404 there is expected rather than a failure.
    written += update_main_season(prev_code, prev_suffix, missing_ok=False)
    written += update_main_season(cur_code, cur_suffix, missing_ok=True)
            
    # --- Step 2: Extra Leagues ---
    print("\n[*] Updating Extra Leagues...")
    for code, (url_code, base_name) in EXTRA_LEAGUES_MAP.items():
        # Using /new/ for latest updates
        csv_url = f"https://www.football-data.co.uk/new/{url_code}.csv"
        csv_content = download_file(csv_url)
        
        if csv_content:
            # Consolidated files - NO Suffix
            target_name = f"{base_name}.csv"
            target_path = os.path.join(TARGET_DIR, target_name)
            
            with open(target_path, 'wb') as f:
                f.write(csv_content)
            written += 1
            print(f"    -> Updated {target_name}")

    expected = 2 * len(MAIN_MAPPING) + len(EXTRA_LEAGUES_MAP)
    print(f"\n[+] Update complete. {written}/{expected} files written.")
    return written


if __name__ == "__main__":
    # Every download is wrapped in its own try/except, so a total outage (no
    # network, football-data.co.uk down, URL scheme changed) used to print a
    # wall of "[-] Failed to download" and still exit 0 — indistinguishable
    # from success to any caller. run_predictions.sh now chains this step, so
    # it needs a real exit code: nothing written is a failure.
    if update_data() == 0:
        print("[-] No files were written — every download failed. See errors above.")
        sys.exit(1)
