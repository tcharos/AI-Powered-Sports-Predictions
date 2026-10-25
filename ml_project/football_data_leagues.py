"""football-data.co.uk league codes and season arithmetic, in one place.

Shared by scripts/update_football_data.py (which writes the corpus) and
DataLoader (which reads it), so the file-name convention and the league code
it encodes cannot drift apart.
"""
import datetime
import os
import re

# Main leagues (mmz4281/<season>/data.zip) -> corpus file-name prefix. Files
# are written as f"{prefix}_{yy-yy}.csv", e.g. ENG-Premier_League_26-27.csv.
MAIN_MAPPING = {
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
    "G1": "GR-Super_League",
}
PREFIX_TO_CODE = {v: k for k, v in MAIN_MAPPING.items()}

_SEASON_FILE = re.compile(r'^(?P<prefix>.+)_\d{2}-\d{2}\.csv$')


def season_of(date):
    """(code, suffix) of the European season `date` falls in, e.g.
    2026-10-25 -> ("2627", "26-27"). Seasons roll over on 1 July: by then the
    old season has finished and football-data publishes the new folder in
    July/August."""
    start = date.year if date.month >= 7 else date.year - 1
    a, b = start % 100, (start + 1) % 100
    return f"{a:02d}{b:02d}", f"{a:02d}-{b:02d}"


def seasons_to_refresh(today=None):
    """[previous, current] season. The previous one is refreshed too so the
    tail of a season (late results, corrections) is never lost across the
    rollover, and the current one is picked up the day football-data
    publishes it."""
    today = today or datetime.date.today()
    cur = season_of(today)
    prev = season_of(today.replace(year=today.year - 1))
    return [prev, cur]


def league_code_from_filename(path):
    """Main-league code for a per-season corpus file, or None.

    Every main-league season file before 2024/25 was written pre-renamed and
    without a `Div` column; this recovers the code from the file name."""
    m = _SEASON_FILE.match(os.path.basename(path))
    return PREFIX_TO_CODE.get(m.group('prefix')) if m else None


# File-name prefix -> the country as Flashscore writes it in a slate label.
_PREFIX_COUNTRY = {
    "ENG": "ENGLAND", "GER": "GERMANY", "ITA": "ITALY", "ESP": "SPAIN",
    "FRA": "FRANCE", "NED": "NETHERLANDS", "BEL": "BELGIUM", "POR": "PORTUGAL",
    "TUR": "TURKEY", "SCO": "SCOTLAND", "GR": "GREECE",
}
CODE_COUNTRY = {code: _PREFIX_COUNTRY[prefix.split('-')[0]]
                for code, prefix in MAIN_MAPPING.items()}


def league_country(league):
    """Country of a corpus league label: a main-league code ("E0") or an
    extra-league "COUNTRY: League" label. None if neither."""
    if league in CODE_COUNTRY:
        return CODE_COUNTRY[league]
    if isinstance(league, str) and ':' in league:
        return league.split(':', 1)[0].strip().upper()
    return None
