"""Map Flashscore-format league names to keys in `data_sets/league_calibration.json`.

The training-time `league` column is either a football-data.co.uk short code
(E0, D1, SP1, etc., from "Div" in the standard format files) OR a full league
name (Veikkausliiga, MLS, J1 League, etc., from "League" in the extra-leagues
format). The Flashscore scraper emits a third form ("ENGLAND: Premier League").
This module bridges the third form to the first two.

Leagues without an entry here (or whose calibration target doesn't exist in
`league_calibration.json`) fall back to raw probabilities at inference — safe
behaviour, but you'll miss the calibration benefit. Add new entries as Flashscore
scrapes new league names.
"""

LEAGUE_ALIASES = {
    'ARGENTINA: Liga Profesional':           'Liga Profesional',
    'ARGENTINA: Copa De La Liga Profesional': 'Copa De La Liga Profesional',
    'AUSTRIA: Bundesliga':                   'Bundesliga',
    'BELGIUM: Jupiler Pro League':           'B1',
    'BRAZIL: Serie A Betano':                'Serie A',
    'CHINA: Super League':                   'Super League',
    'DENMARK: Superliga':                    'Superliga',
    'ENGLAND: Championship':                 'E1',
    'ENGLAND: League One':                   'E2',
    'ENGLAND: League Two':                   'E3',
    'ENGLAND: National League':              'EC',
    'ENGLAND: Premier League':               'E0',
    'FINLAND: Veikkausliiga':                'Veikkausliiga',
    'FRANCE: Ligue 1':                       'F1',
    'FRANCE: Ligue 2':                       'F2',
    'GERMANY: 2. Bundesliga':                'D2',
    'GERMANY: Bundesliga':                   'D1',
    'GREECE: Super League':                  'G1',
    'IRELAND: Premier Division':             'Premier Division',
    'ITALY: Serie A':                        'I1',
    'ITALY: Serie B':                        'I2',
    'JAPAN: J1 League':                      'J1 League',
    'MEXICO: Liga MX':                       'Liga MX',
    'NETHERLANDS: Eredivisie':               'N1',
    'NORWAY: Eliteserien':                   'Eliteserien',
    'POLAND: Ekstraklasa':                   'Ekstraklasa',
    'PORTUGAL: Liga Portugal':               'P1',
    'ROMANIA: Liga 1':                       'Superliga',
    'ROMANIA: Superliga':                    'Superliga',
    'RUSSIA: Premier League':                'Premier League',
    'SCOTLAND: Premiership':                 'SC0',
    'SCOTLAND: Championship':                'SC1',
    'SCOTLAND: League One':                  'SC2',
    'SCOTLAND: League Two':                  'SC3',
    'SPAIN: LaLiga':                         'SP1',
    'SPAIN: LaLiga 2':                       'SP2',
    'SWEDEN: Allsvenskan':                   'Allsvenskan',
    'SWITZERLAND: Super League':             'Super League',
    'TURKEY: Super Lig':                     'T1',
    'USA: MLS':                              'MLS',
}
