import requests
import zipfile
import io
import os
import shutil

# Config - RESTORING 24-25
DATA_URL = "https://www.football-data.co.uk/mmz4281/2425/data.zip"
TARGET_DIR = "data_sets/MatchHistory"
SEASON_SUFFIX = "24-25"

# Mapping: Source Code -> Target Base Name (without season suffix)
MAPPING = {
    "E0": "ENG-Premier_League",
    "E1": "ENG-Championship",
    "E2": "ENG-League_1",
    "E3": "ENG-League_2",
    "EC": "ENG-Conference",
    "D1": "GER-Bundesliga",
    "D2": "GER-Bundesliga_2",
    "I1": "ITA-Serie_A",
    "I2": "ITA-Serie_B",
    "SP1": "ESP-La_Liga",
    "SP2": "ESP-Segunda",
    "F1": "FRA-Ligue_1",
    "F2": "FRA-Ligue_2",
    "N1": "NED-Eredivisie",
    "B1": "BEL-Jupiler_League",
    "P1": "POR-Liga_1",
    "T1": "TUR-Ligi_1",
    "SC0": "SCO-Premier_League",
    "SC1": "SCO-Division_1",
    "SC2": "SCO-Division_2",
    "SC3": "SCO-Division_3",
    "G1": "GR-Super_League"
}

def update_data():
    print(f"Restoring 24-25 data from {DATA_URL}...")
    try:
        r = requests.get(DATA_URL)
        r.raise_for_status()
    except Exception as e:
        print(f"Error downloading data: {e}")
        return

    print("Extracting and processing files...")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for filename in z.namelist():
            if not filename.endswith(".csv"):
                continue
                
            code = os.path.splitext(filename)[0]
            
            if code in MAPPING:
                target_base = MAPPING[code]
                target_filename = f"{target_base}_{SEASON_SUFFIX}.csv"
                target_path = os.path.join(TARGET_DIR, target_filename)
                
                print(f"Restoring {target_filename}...")
                
                with open(target_path, 'wb') as f_out:
                    f_out.write(z.read(filename))

    print("Restoration complete.")

if __name__ == "__main__":
    if not os.path.exists(TARGET_DIR):
        print(f"Error: Target directory {TARGET_DIR} does not exist.")
    else:
        update_data()
