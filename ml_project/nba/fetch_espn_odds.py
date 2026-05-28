"""Fetch NBA pre-game odds from ESPN's public JSON scoreboard API.

Reliable, plain-HTTP, no browser, no DOM selectors — replaces the
Playwright/DOM-based exploratory scraper that broke on every ESPN page redesign.

Endpoint
--------
``https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard``
with ``?dates=YYYYMMDD``. Public, unauthenticated, JSON. (The same endpoint the
ESPN website itself uses for the scoreboard widget.)

Caveats
-------
* **Odds are populated only for upcoming / in-progress games.** Completed games
  return ``odds: []`` — ESPN doesn't preserve historical odds. So this fetcher
  is purely a *forward* source for tomorrow's EV computation; the betting
  flow can't reconstruct yesterday's odds after the fact.
* Provider varies per market (currently DraftKings, priority 1). We capture
  the provider name in the output for traceability.
* American odds in the response are strings like ``"-155"`` / ``"+130"``;
  we parse to int and also write decimal equivalents for downstream EV/Kelly.

Output
------
``output_basketball/espn_odds_<YYYY-MM-DD>.json`` — per-date file (no more
singleton overwrite that lost old data on each run). Each entry::

    {game_id_espn, date, status,
     home_team, home_team_abbr, home_team_id_espn,
     away_team, away_team_abbr, away_team_id_espn,
     provider,
     spread, total,
     home_ml_american, home_ml_decimal,
     away_ml_american, away_ml_decimal,
     over_line, over_ml_american, over_ml_decimal,
     under_line, under_ml_american, under_ml_decimal}
"""

import argparse
import datetime
import json
import os
import sys
from typing import Optional

import requests


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(_REPO, "output_basketball")
ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# Generic browser UA + JSON Accept — ESPN's public API is permissive but a real
# UA avoids the rare bot heuristic.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept":     "application/json",
}


# ---------------------------------------------------------------------------
# Parsing helpers (pure, testable)
# ---------------------------------------------------------------------------

def american_to_decimal(american: Optional[int]) -> Optional[float]:
    """+130 → 2.30, -150 → 1.667. ``None`` for missing / invalid / 0."""
    if american is None:
        return None
    try:
        a = int(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a > 0:
        return round(1.0 + a / 100.0, 3)
    return round(1.0 + 100.0 / abs(a), 3)


def _to_american(odds_str) -> Optional[int]:
    """Parse ESPN's odds strings ('-155', '+130') → int. None on missing/invalid."""
    if odds_str in (None, ""):
        return None
    try:
        return int(str(odds_str).replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def _ml_close(side: Optional[dict]) -> Optional[int]:
    """Closing moneyline American odds from ``moneyline.{home,away}``."""
    if not side:
        return None
    return _to_american((side.get("close") or {}).get("odds"))


def _total_close(side: Optional[dict]) -> dict:
    """Closing over/under from ``total.{over,under}`` → ``{line, american, decimal}``.

    Line strings look like ``"o219.5"`` / ``"u219.5"``; strip the leading letter.
    """
    out = {"line": None, "american": None, "decimal": None}
    if not side:
        return out
    close = side.get("close") or {}
    out["american"] = _to_american(close.get("odds"))
    out["decimal"] = american_to_decimal(out["american"])
    line_raw = close.get("line")
    if line_raw is not None:
        try:
            out["line"] = float(str(line_raw).lstrip("oOuU"))
        except (TypeError, ValueError):
            pass
    return out


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

# Provider preference: prefer the major US books when ESPN exposes multiple.
# Falls back to whatever's first if none match.
PROVIDER_PREF = ("ESPN BET", "DraftKings", "FanDuel", "Caesars Sportsbook", "BetMGM")


def _pick_odds(odds_list: list) -> Optional[dict]:
    """Pick one provider's odds entry from the list (highest preference; else first)."""
    if not odds_list:
        return None
    by_name = {(o.get("provider") or {}).get("name"): o for o in odds_list}
    for pref in PROVIDER_PREF:
        if pref in by_name:
            return by_name[pref]
    return odds_list[0]


def _project_event(ev: dict, date_str: str) -> Optional[dict]:
    comps = ev.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    teams = comp.get("competitors") or []
    home = next((t for t in teams if t.get("homeAway") == "home"), None)
    away = next((t for t in teams if t.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    odds = _pick_odds(comp.get("odds") or [])
    if odds is None:
        # Completed games have odds: []. Still record the event so the caller
        # sees the gap; downstream odds-dependent steps will skip on None.
        ml = total_over = total_under = {}
        provider = None
        spread = total = None
    else:
        provider = (odds.get("provider") or {}).get("name")
        spread   = odds.get("spread")
        total    = odds.get("overUnder")
        ml       = odds.get("moneyline") or {}
        total_over  = (odds.get("total") or {}).get("over") or {}
        total_under = (odds.get("total") or {}).get("under") or {}

    home_t = home.get("team") or {}
    away_t = away.get("team") or {}
    home_ml = _ml_close(ml.get("home"))
    away_ml = _ml_close(ml.get("away"))
    over    = _total_close(total_over)
    under   = _total_close(total_under)

    return {
        "game_id_espn":      ev.get("id"),
        "date":              date_str,
        "status":            ((comp.get("status") or {}).get("type") or {}).get("description"),
        "home_team":         home_t.get("displayName"),
        "home_team_abbr":    home_t.get("abbreviation"),
        "home_team_id_espn": home_t.get("id"),
        "away_team":         away_t.get("displayName"),
        "away_team_abbr":    away_t.get("abbreviation"),
        "away_team_id_espn": away_t.get("id"),
        "provider":          provider,
        "spread":            spread,
        "total":             total,
        "home_ml_american":  home_ml,
        "home_ml_decimal":   american_to_decimal(home_ml),
        "away_ml_american":  away_ml,
        "away_ml_decimal":   american_to_decimal(away_ml),
        "over_line":         over["line"],
        "over_ml_american":  over["american"],
        "over_ml_decimal":   over["decimal"],
        "under_line":        under["line"],
        "under_ml_american": under["american"],
        "under_ml_decimal":  under["decimal"],
    }


def fetch_odds(date_str: str) -> list:
    date_url = date_str.replace("-", "")
    print(f"[odds] GET {ENDPOINT}?dates={date_url}")
    r = requests.get(ENDPOINT, params={"dates": date_url}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    events = (r.json() or {}).get("events", []) or []
    print(f"[odds] {len(events)} events in scoreboard for {date_str}")
    return [g for g in (_project_event(ev, date_str) for ev in events) if g is not None]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD; default tomorrow (matches the predictor's default).")
    args = ap.parse_args()
    today = datetime.date.today()
    date_str = args.date or (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        games = fetch_odds(date_str)
    except Exception as e:
        print(f"[odds] FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"espn_odds_{date_str}.json")
    with open(out_path, "w") as f:
        json.dump(games, f, indent=2)
    with_ml = sum(1 for g in games if g.get("home_ml_decimal") is not None)
    print(f"[odds] wrote {len(games)} games ({with_ml} with moneyline) → {out_path}")
    if with_ml:
        print(f"  provider mix: " + ", ".join(sorted({g['provider'] for g in games if g.get('provider')})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
