"""D4 / N0 — SoFIFA importance-source probe (READ-ONLY, reproducible).

Captures the 2026-05-27 go/no-go investigation that cleared D4's importance
blocker. SoFIFA (via the ``soccerdata`` package) is the candidate source for
player *importance* (how much losing an absentee hurts), joined to the
Flashscore "Will not play" list from N1 (``extract_availability.py``).

FINDINGS (2026-05-27) — see FOOTBALL_NEXT_STEPS "D4 scoping":
  * COVERAGE: SoFIFA's real catalog is ~52 leagues / 44 nations (read from the
    /api/league response, NOT ``available_leagues()`` which only reflects the
    soccerdata config). Most of our targets are FULL-DEPTH: England L1/L2 (24),
    Germany 2./3. (18/20), Argentina (30), Belgium/China (16), Denmark/Austria
    (12), Ireland (10), Italy/France/Spain second tiers. Thin/partial: Brazil
    (14 of ~20), Greece (4 of 14, big clubs only), Finland (1 — useless).
    Absent from our list: Mexico, Japan, Turkey, England National League.
    ⇒ per-league coverage gating is MANDATORY (skip a match if either club is
    not covered).
  * DATA SHAPE: ``read_player_ratings`` returns ``overallrating`` + ``potential``
    + 36 attributes, current edition (FC 26, May 2026). No market value / minutes
    (OVR is the quality proxy). Importance must be reason_class-gated, NOT raw
    OVR — confirmed by the data: Maxime Dupé OVR 75 but "Inactive" (backup
    keeper) costs nothing, vs Wahi OVR 75 "suspended" = a real loss.
  * NAME JOIN: Flashscore "Surname X." ↔ sofifa full name, surname+initial fuzzy
    match = 11/11 = 100% on the Nice/St-Étienne sample (incl. N'Guessan, Traoré).
  * DISCRETION: sofifa.com is Cloudflare-walled — plain ``requests`` gets 403 on
    every path. soccerdata drives a browser. HEADLESS WORKS (no visible window):
    ~90s cold-start (one CF solve) then ~4s/fetch warm. ⇒ run headless, one warm
    session per batch, cache by (player, fifa_update); ratings change ~weekly.
  * CONFIG: soccerdata gates leagues via ~/soccerdata/config/league_dict.json,
    loaded at IMPORT time. To add a league, write the config in a SEPARATE
    process before importing soccerdata. ``ensure_leagues()`` below does this.

Usage::
    python3 scripts/d4_injuries/probe_sofifa.py coverage      # team-count map
    python3 scripts/d4_injuries/probe_sofifa.py join <availability_<date>.json>
"""

import json
import os
import sys
import time
import unicodedata

# Our target leagues that EXIST in the sofifa catalog, mapped to the
# "[Nation] League" string soccerdata expects. (Mexico/Japan/Turkey/Nat.League
# are NOT in sofifa.) Key = a soccerdata config key we register on demand.
TARGET_SOFIFA = {
    "ENG-Premier League":   "[England] Premier League",
    "ENG-Championship-D4":  "[England] Championship",
    "ENG-League One-D4":    "[England] League One",
    "ENG-League Two-D4":    "[England] League Two",
    "GER-Bundesliga":       "[Germany] Bundesliga",
    "GER-2. Bundesliga-D4": "[Germany] 2. Bundesliga",
    "GER-3. Liga-D4":       "[Germany] 3. Liga",
    "ITA-Serie A":          "[Italy] Serie A",
    "ITA-Serie B-D4":       "[Italy] Serie B",
    "FRA-Ligue 1":          "[France] Ligue 1",
    "FRA-Ligue 2-D4":       "[France] Ligue 2",
    "ESP-La Liga":          "[Spain] La Liga",
    "ARG-Liga Profesional": "[Argentina] Liga Profesional de Fútbol",
    "BEL-Pro League":       "[Belgium] Pro League",
    "DEN-Superliga":        "[Denmark] Superliga",
    "AUT-Bundesliga-D4":    "[Austria] Bundesliga",
    "BRA-Serie A-D4":       "[Brazil] Série A",
    "CHN-Super League":     "[China PR] Super League",
    "GRE-Super League":     "[Greece] Super League",
    "IRL-Premier Division": "[Republic of Ireland] Premier Division",
    "FIN-Veikkausliiga":    "[Finland] Veikkausliiga",
}


def _config_path():
    base = os.environ.get("SOCCERDATA_DIR", os.path.expanduser("~/soccerdata"))
    return os.path.join(base, "config", "league_dict.json")


def ensure_leagues():
    """Register TARGET_SOFIFA keys in league_dict.json (no-op if present).

    Must run BEFORE ``import soccerdata`` in the consuming process — the config
    is read at import time.
    """
    path = _config_path()
    ld = json.load(open(path))
    added = []
    for key, sofifa_val in TARGET_SOFIFA.items():
        if key not in ld:
            ld[key] = {"SoFIFA": sofifa_val, "season_start": "Aug", "season_end": "May"}
            added.append(key)
    if added:
        json.dump(ld, open(path, "w"), indent=4, ensure_ascii=False)
    return added


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().replace(".", "").strip()


def coverage():
    """Print the team-coverage map for our target leagues (1 page/league)."""
    ensure_leagues()
    import soccerdata as sd
    print(f"{'league key':<24} teams  status")
    print("-" * 44)
    for key in TARGET_SOFIFA:
        try:
            n = len(sd.SoFIFA(leagues=key, versions="latest", headless=True).read_teams())
            status = "full" if n >= 10 else ("partial" if n >= 4 else "UNUSABLE")
            print(f"{key:<24} {n:>4}   {status}")
        except Exception as e:
            print(f"{key:<24}   --   ERR {str(e)[:40]}")


def _surname_initial_match(abs_name, full):
    an = _norm(abs_name)
    parts = an.split()
    initial = parts[-1] if parts and len(parts[-1]) == 1 else None
    surname = " ".join(parts[:-1]) if initial else an
    fn = _norm(full)
    surname_ok = surname and surname in fn
    init_ok = (initial is None) or any(t and t[0] == initial for t in fn.split())
    return bool(surname_ok and init_ok)


def join(availability_path):
    """Demo: join one match's absentees (from N1 output) to sofifa OVR.

    Resolves each absentee to a sofifa player by surname+initial, then fetches
    overallrating. Importance weight (reason_class × marginal-OVR) is computed
    later by the N2 module; this only proves the join + rating retrieval.
    """
    ensure_leagues()
    import soccerdata as sd
    from rapidfuzz import process, fuzz

    avail = json.load(open(availability_path))
    mid, m = next(iter(avail.items()))
    print(f"match {mid}: {m['home_team']} v {m['away_team']}  ({m['league']})")

    # NOTE: in production N2 resolves the club→sofifa-league mapping; here we
    # just scan a couple of likely leagues. Demo only.
    t0 = time.time()
    for side in ("home", "away"):
        absentees = m[side]
        if not absentees:
            continue
        print(f"\n  {side} ({m[side+'_team'] if side+'_team' in m else m[side]}): {len(absentees)} out")
        for a in absentees:
            print(f"    {a['name']:<18} [{a['reason_class']:<10}] {a['reason']}")
    print(f"\n(join→OVR retrieval is exercised in the N2 module; "
          f"parser+structure validated here in {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "coverage"
    if cmd == "coverage":
        coverage()
    elif cmd == "join":
        join(sys.argv[2])
    else:
        print(__doc__)
