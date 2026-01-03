import os
import json

HIST_DIR = "data_sets/MatchHistory"
OUTPUT_FILE = "data_sets/target_leagues.json"

# Mapping from File Prefix to Flashscore League Name
# Using "COUNTRY: League" format common in Flashscore
MAPPING = {
    "ARG": "ARGENTINA: Liga Profesional",
    "AUT": "AUSTRIA: Bundesliga",
    "BEL-Jupiler_League": "BELGIUM: Jupiler Pro League", # Flashscore often adds 'Pro'
    "BRA": "BRAZIL: Serie A",
    "CHN": "CHINA: Super League",
    "DNK": "DENMARK: Superliga",
    "ENG-Premier_League": "ENGLAND: Premier League",
    "ENG-Championship": "ENGLAND: Championship",
    "ENG-League_1": "ENGLAND: League One",
    "ENG-League_2": "ENGLAND: League Two", 
    "ENG-Conference": "ENGLAND: National League",
    "ESP-La_Liga": "SPAIN: LaLiga",
    "ESP-Segunda": "SPAIN: LaLiga 2",
    "FIN": "FINLAND: Veikkausliiga",
    "FRA-Ligue_1": "FRANCE: Ligue 1",
    "FRA-Ligue_2": "FRANCE: Ligue 2",
    "GER-Bundesliga": "GERMANY: Bundesliga",
    "GER-Bundesliga_2": "GERMANY: 2. Bundesliga",
    "GR-Super_League": "GREECE: Super League",
    "IRL": "IRELAND: Premier Division",
    "ITA-Serie_A": "ITALY: Serie A",
    "ITA-Serie_B": "ITALY: Serie B",
    "JPN": "JAPAN: J1 League",
    "MEX": "MEXICO: Liga MX",
    "NED-Eredivisie": "NETHERLANDS: Eredivisie",
    "NOR": "NORWAY: Eliteserien",
    "POL": "POLAND: Ekstraklasa",
    "POR-Liga_1": "PORTUGAL: Liga Portugal",
    "ROU": "ROMANIA: Liga 1",
    "RUS": "RUSSIA: Premier League",
    "SCO-Premier_League": "SCOTLAND: Premiership",
    "SCO-Division_1": "SCOTLAND: Championship",
    "SCO-Division_2": "SCOTLAND: League One",
    "SCO-Division_3": "SCOTLAND: League Two",
    "SWE": "SWEDEN: Allsvenskan",
    "SWZ": "SWITZERLAND: Super League",
    "TUR-Ligi_1": "TURKEY: Super Lig",
    "USA": "USA: MLS"
}

# Add standard European cups always
ALWAYS_INCLUDE = [
    "EUROPE: Champions League",
    "EUROPE: Europa League",
    "EUROPE: Conference League"
]

def generate():
    found_leagues = set()
    
    # Add defaults
    for l in ALWAYS_INCLUDE:
        found_leagues.add(l)
        
    # Scan directory
    files = os.listdir(HIST_DIR)
    for f in files:
        if not f.endswith(".csv"):
            continue
            
        # Match filenames
        mapped = False
        # Try finding longest matching prefix
        # Sort keys by length desc to match "ENG-Premier_League" before "ENG" (if existed)
        for prefix in sorted(MAPPING.keys(), key=len, reverse=True):
            if f.startswith(prefix):
                found_leagues.add(MAPPING[prefix])
                mapped = True
                break
        
        if not mapped:
            print(f"Warning: Could not map file {f}")

    # Save
    sorted_leagues = sorted(list(found_leagues))
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(sorted_leagues, f, indent=4)
    
    print(f"Generated {len(sorted_leagues)} target leagues in {OUTPUT_FILE}")

if __name__ == "__main__":
    generate()
