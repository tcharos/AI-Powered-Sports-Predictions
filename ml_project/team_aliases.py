"""Flashscore team name -> football-data.co.uk corpus name, for names the
fuzzy matcher in EntityResolver gets wrong or cannot find.

Checked BEFORE the learned cache in data_sets/team_mappings.json, so an entry
here also overrides a wrong mapping cached there. Every target must be a team
name as it appears in data_sets/MatchHistory (the resolver ignores an alias
whose target is not in elo_ratings.json, which is built from that corpus).

Found by replaying every saved slate through the resolver on 2026-10-25.
Two kinds of failure:

- No match at all (cached as None, then never retried): abbreviations and
  full names that share too few tokens with football-data's short forms.
- A confident WRONG match, cached and reused on every slate. These are the
  worse case, because the fixture is predicted from another club's ELO and
  form with nothing in the output to show it.
"""

TEAM_ALIASES = {
    # Unmatched — served as ELO 1500 with zero form.
    'Manchester City':     'Man City',
    'Manchester Utd':      'Man United',
    'Nottingham':          "Nott'm Forest",
    'MK Dons':             'Milton Keynes Dons',
    'Airdrieonians':       'Airdrie Utd',
    'PSG':                 'Paris SG',
    'B. Monchengladbach':  "M'gladbach",
    'Hamburger SV':        'Hamburg',
    'Basaksehir':          'Buyuksehyr',
    'Olympiacos Piraeus':  'Olympiakos',
    'G.A. Eagles':         'Go Ahead Eagles',
    'St. Liege':           'Standard',
    'Royale Union SG':     'St. Gilloise',
    # Cached mapping pointed at a name the corpus does not use.
    'Celta Vigo':          'Celta',
    'Braga':               'Sp Braga',
    # Cached mapping pointed at the WRONG club.
    'Sporting CP':         'Sp Lisbon',       # was Sporting Kansas City (MLS)
    'Eintracht Frankfurt': 'Ein Frankfurt',   # was Frankfurt FSV
    'Volos':               'Volos NFC',       # was Niki Volos
    'Dundee Utd':          'Dundee United',   # was Dundee — its city rival
}
