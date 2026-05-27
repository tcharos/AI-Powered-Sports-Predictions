"""National-teams / probe — READ-ONLY exploration of eloratings.net.

Source for the national-team subsystem (scope: bettable = qualifiers + Nations
League, per operator 2026-05-27). eloratings.net is **plain HTTP, no browser,
no Cloudflare** — discreet by default. It serves tab-separated files:

  * ``World.tsv``        — current world rankings (rank, country, elo, stats).
  * ``<YEAR>.tsv``       — end-of-year ranking snapshots (1950 → present).
  * ``<Country>.tsv``    — **match-by-match history** for one nation (back to
                           ~1872/1920), the training corpus.

Per-country match row schema (16 cols, confirmed 2026-05-27 against Spain):
  [0] year  [1] month  [2] day  [3] home_cc  [4] away_cc  [5] home_score
  [6] away_score  [7] competition_code  [8] neutral/host flag (blank=home)
  [9] match elo delta  [10] home_elo  [11] away_elo  [12..15] rank deltas/pos

So results + competition filter + **per-match point-in-time ELO** come from one
file. No odds anywhere (eloratings has none) → the NT model is odds-free by
necessity; live odds come from Flashscore (add NT comps to target_leagues.json).

Usage::
    python3 scripts/national_teams/probe_eloratings.py            # summary + comp codes
    python3 scripts/national_teams/probe_eloratings.py <Country>   # e.g. Spain, Germany
"""

import sys
from collections import Counter

import requests

BASE = "https://www.eloratings.net"
HDR = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36")}

# Competition codes (enumerated 2026-05-27 from Spain + Finland; Nations League
# verified by date — all editions are 2018+).
COMP_LABELS = {
    "F": "Friendly", "WQ": "World Cup qualifier", "EQ": "Euro qualifier",
    "ENA": "Nations League A", "ENB": "Nations League B", "ENC": "Nations League C",
    "END": "Nations League D", "ENL": "Nations League finals",
    "WC": "World Cup finals", "EC": "Euro finals", "CC": "Confederations Cup",
    "OG": "Olympics", "HHC": "British Home Championship",
}
# The operator's "bettable" scope — qualifiers + Nations League (all divisions).
BETTABLE = {"WQ", "EQ", "ENA", "ENB", "ENC", "END", "ENL"}


def _get(path):
    r = requests.get(f"{BASE}/{path}", headers=HDR, timeout=20)
    r.raise_for_status()
    return r.content.decode("utf-8").replace("−", "-")  # normalize minus


def parse_country(country: str):
    """Return list of match dicts for one nation from <Country>.tsv."""
    rows = [r.split("\t") for r in _get(f"{country}.tsv").strip().split("\n")]
    out = []
    for r in rows:
        if len(r) < 12:
            continue
        try:
            out.append({
                "date": f"{r[0]}-{int(r[1]):02d}-{int(r[2]):02d}",
                "home": r[3], "away": r[4],
                "hs": int(r[5]), "as": int(r[6]),
                "comp": r[7], "neutral": bool(r[8].strip()),
                "home_elo": int(r[10]), "away_elo": int(r[11]),
            })
        except (ValueError, IndexError):
            continue
    return out


def summary(country="Spain"):
    matches = parse_country(country)
    print(f"=== {country}.tsv: {len(matches)} matches "
          f"({matches[0]['date']} → {matches[-1]['date']}) ===")

    comps = Counter(m["comp"] for m in matches)
    print("\ncompetition codes (count) — ★ = in 'bettable' scope:")
    for code, n in comps.most_common():
        star = " ★" if code in BETTABLE else ""
        print(f"  {code:<4} {COMP_LABELS.get(code,'?'):<26} {n:>4}{star}")

    bettable = [m for m in matches if m["comp"] in BETTABLE]
    print(f"\nbettable subset (qualifiers + Nations League): {len(bettable)} matches")
    print("recent bettable rows (results + per-match ELO):")
    for m in bettable[-6:]:
        print(f"  {m['date']}  {m['home']} {m['hs']}-{m['as']} {m['away']}  "
              f"[{m['comp']}]  elo {m['home_elo']} v {m['away_elo']}"
              + ("  (neutral)" if m["neutral"] else ""))


if __name__ == "__main__":
    summary(sys.argv[1] if len(sys.argv) > 1 else "Spain")
