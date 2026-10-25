"""Map Flashscore-format league names to keys in `data_sets/league_calibration.json`.

The training-time `league` column is either a football-data.co.uk short code
(E0, D1, SP1, etc., from "Div" in the standard format files, or recovered
from the file name for old files without one) OR, for the extra leagues, a
country-qualified "COUNTRY: League" label built by DataLoader from the
`Country` and `League` columns ("USA: MLS", "CHINA: Super League"). The
Flashscore scraper emits the same "COUNTRY: League" form, so most extra-league
entries below are identities, kept so every served league is listed; the
main leagues need a real mapping to their short code.

Before 2026-10-25 the extra-league label was the bare league name, which
collided across countries: CHINA and SWITZERLAND were both "Super League",
DENMARK and ROMANIA both "Superliga", so each pair shared one league_cat
category, one running league scoring average, and one calibration entry.

Leagues without an entry here (or whose calibration target doesn't exist in
`league_calibration.json`) fall back to raw probabilities at inference — safe
behaviour, but you'll miss the calibration benefit. Add new entries as Flashscore
scrapes new league names.
"""

LEAGUE_ALIASES = {
    'ARGENTINA: Liga Profesional':           'ARGENTINA: Liga Profesional',
    'ARGENTINA: Copa De La Liga Profesional': 'ARGENTINA: Copa De La Liga Profesional',
    'AUSTRIA: Bundesliga':                   'AUSTRIA: Bundesliga',
    'BELGIUM: Jupiler Pro League':           'B1',
    'BRAZIL: Serie A Betano':                'BRAZIL: Serie A',
    'CHINA: Super League':                   'CHINA: Super League',
    'DENMARK: Superliga':                    'DENMARK: Superliga',
    'ENGLAND: Championship':                 'E1',
    'ENGLAND: League One':                   'E2',
    'ENGLAND: League Two':                   'E3',
    'ENGLAND: National League':              'EC',
    'ENGLAND: Premier League':               'E0',
    'FINLAND: Veikkausliiga':                'FINLAND: Veikkausliiga',
    'FRANCE: Ligue 1':                       'F1',
    'FRANCE: Ligue 2':                       'F2',
    'GERMANY: 2. Bundesliga':                'D2',
    'GERMANY: Bundesliga':                   'D1',
    'GREECE: Super League':                  'G1',
    'IRELAND: Premier Division':             'IRELAND: Premier Division',
    'ITALY: Serie A':                        'I1',
    'ITALY: Serie B':                        'I2',
    'JAPAN: J1 League':                      'JAPAN: J1 League',
    'MEXICO: Liga MX':                       'MEXICO: Liga MX',
    'NETHERLANDS: Eredivisie':               'N1',
    'NORWAY: Eliteserien':                   'NORWAY: Eliteserien',
    'POLAND: Ekstraklasa':                   'POLAND: Ekstraklasa',
    'PORTUGAL: Liga Portugal':               'P1',
    'ROMANIA: Liga 1':                       'ROMANIA: Superliga',
    'ROMANIA: Superliga':                    'ROMANIA: Superliga',
    'RUSSIA: Premier League':                'RUSSIA: Premier League',
    'SCOTLAND: Premiership':                 'SC0',
    'SCOTLAND: Championship':                'SC1',
    'SCOTLAND: League One':                  'SC2',
    'SCOTLAND: League Two':                  'SC3',
    'SPAIN: LaLiga':                         'SP1',
    'SPAIN: LaLiga 2':                       'SP2',
    'SWEDEN: Allsvenskan':                   'SWEDEN: Allsvenskan',
    'SWITZERLAND: Super League':             'SWITZERLAND: Super League',
    'TURKEY: Super Lig':                     'T1',
    'USA: MLS':                              'USA: MLS',
}
