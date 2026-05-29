"""D4 / N2 — SoFIFA importance join.

Enriches the N1 availability JSON (``output/availability_<date>.json``, the
Flashscore "Will not play" list) with a per-absentee *importance* weight derived
from SoFIFA overall ratings (OVR). This is the "how good is the player we lost"
signal the N3 adjuster needs — an importance-blind absentee count is useless
(operator, 2026-05-26): a 4th-choice fullback must not weigh the same as a star.

Pipeline position (file-passing, mirrors N1):
    matches_<date>.json  --N1-->  availability_<date>.json
                         --N2-->  availability_importance_<date>.json  --N3--> adjuster

Importance model (v1):
    importance = max(0, absentee_OVR - replacement_OVR)
where replacement_OVR is the **squad median OVR** (the player a median squad
member represents). A star (OVR 85, squad median 75) → importance 10; a
benchwarmer (OVR 70) → max(0, 70-75) = 0. reason_class gating (injury vs
inactive) is applied later by the N3 adjuster as reason_weight × importance —
N2 only attaches the raw OVR-marginal importance + the OVR itself.

Coverage gate (MANDATORY, see FOOTBALL_NEXT_STEPS D4): SoFIFA covers most of our
target leagues at full depth but NOT all (Finland unusable, Greece/Brazil
partial, Mexico/Japan/Turkey/national-teams absent). A match is enriched only if
its league maps to a SoFIFA league AND both clubs resolve to a SoFIFA squad;
otherwise it's stamped ``covered=false`` with a ``gate_reason`` and the absentee
list passes through unweighted (N3 then no-ops on it — fail-safe).

Discretion: sofifa.com is Cloudflare-walled; soccerdata drives a headless
browser (~90s cold solve, ~4s/fetch warm). soccerdata caches every fetched page
under ~/soccerdata/data/SoFIFA, so a warm run needs NO browser. Each team costs
~1 squad-page fetch + ~1 fetch per squad player for ratings (one-time, cached).

Usage::
    # batch enrich the latest (or a given date's) availability file:
    python3 ml_project/availability/sofifa_importance.py [YYYY-MM-DD]
    # offline self-test of the pure logic (no network):
    python3 ml_project/availability/sofifa_importance.py --self-test
"""

import glob
import json
import os
import statistics
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "output")

# ---------------------------------------------------------------------------
# League coverage map: Flashscore "COUNTRY: League" -> soccerdata SoFIFA key.
# Only leagues present in SoFIFA's catalog (probed N0, 2026-05-27) appear here;
# anything not listed gates as league-not-covered. Keys mirror the registration
# values in TARGET_SOFIFA below. Exact Flashscore strings taken from
# ml_project/calibration/league_aliases.py.
# ---------------------------------------------------------------------------
FLASHSCORE_TO_SOFIFA = {
    "ENGLAND: Premier League":     "ENG-Premier League",
    "ENGLAND: Championship":       "ENG-Championship-D4",
    "ENGLAND: League One":         "ENG-League One-D4",
    "ENGLAND: League Two":         "ENG-League Two-D4",
    "GERMANY: Bundesliga":         "GER-Bundesliga",
    "GERMANY: 2. Bundesliga":      "GER-2. Bundesliga-D4",
    "GERMANY: 3. Liga":            "GER-3. Liga-D4",
    "ITALY: Serie A":              "ITA-Serie A",
    "ITALY: Serie B":              "ITA-Serie B-D4",
    "FRANCE: Ligue 1":             "FRA-Ligue 1",
    "FRANCE: Ligue 2":             "FRA-Ligue 2-D4",
    "SPAIN: LaLiga":               "ESP-La Liga",
    "ARGENTINA: Liga Profesional": "ARG-Liga Profesional",
    "BELGIUM: Jupiler Pro League": "BEL-Pro League",
    "DENMARK: Superliga":          "DEN-Superliga",
    "AUSTRIA: Bundesliga":         "AUT-Bundesliga-D4",
    "BRAZIL: Serie A Betano":      "BRA-Serie A-D4",   # partial coverage (~14/20) → team gate
    "CHINA: Super League":         "CHN-Super League",
    "GREECE: Super League":        "GRE-Super League", # partial (~4/14, big clubs) → team gate
    "IRELAND: Premier Division":   "IRL-Premier Division",
    # Finland (Veikkausliiga) is in the catalog but UNUSABLE (1 team) — omitted
    # on purpose so it gates as league-not-covered rather than thrashing fetches.
}

# soccerdata config keys we register on demand (key -> "[Nation] League" value
# the package expects). Must be registered BEFORE `import soccerdata`.
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
}

# A reason class with no genuine availability hit gets no importance (the N3
# adjuster owns the final reason_weight; this just skips the OVR fetch for
# obviously-irrelevant entries to save work). Kept permissive — N3 decides.
_FETCH_REASON_CLASSES = {"injury", "suspension", "doubtful"}

# Tokens dropped when deriving a club acronym ("Paris Saint-Germain" -> "psg")
# and when normalising for token-set matching (corporate/legal/positional fluff).
_TEAM_STOPWORDS = {"de", "fc", "ac", "sc", "osc", "rc", "aj", "cf", "ca", "ud",
                   "sd", "cd", "as", "ogc", "sco", "of", "the", "club", "calcio",
                   "city", "united", "afc", "cp", "sl", "if", "bk", "fk"}

# Flashscore→SoFIFA team-name overrides for the fuzzy long tail (morphological
# variants the scorers can't bridge, e.g. "Rennes"↔"Stade Rennais"). Keyed by
# normalised Flashscore name. Extend as live scrapes surface misses (the gate
# fail-safes meanwhile — an unresolved club just skips enrichment).
_TEAM_OVERRIDES = {
    "rennes": "Stade Rennais FC",
}


# ---------------------------------------------------------------------------
# Pure helpers (offline-testable, no network)
# ---------------------------------------------------------------------------

def sofifa_league_for(flashscore_league: str):
    """Map a Flashscore league string to a SoFIFA key, or None if uncovered.

    Handles sub-phase suffixes ("PORTUGAL: Liga Portugal - Relegation",
    "ENGLAND: Premier League - Play Offs") by also trying the base name before
    the " - " separator.
    """
    if not flashscore_league:
        return None
    if flashscore_league in FLASHSCORE_TO_SOFIFA:
        return FLASHSCORE_TO_SOFIFA[flashscore_league]
    base = flashscore_league.split(" - ")[0].strip()
    return FLASHSCORE_TO_SOFIFA.get(base)


def _norm(s: str) -> str:
    """Lowercase, strip accents and dots — for name matching."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().replace(".", "").strip()


def surname_initial_match(abs_name: str, full_name: str) -> bool:
    """True if a Flashscore "Surname X." matches a SoFIFA full name.

    Surname must appear in the full name; if the Flashscore form carries a
    trailing single-letter initial, some token of the full name must start with
    it. Proven 100% on the Nice/St-Étienne sample (incl. N'Guessan, Traoré).
    """
    an = _norm(abs_name)
    parts = an.split()
    initial = parts[-1] if parts and len(parts[-1]) == 1 else None
    surname = " ".join(parts[:-1]) if initial else an
    fn = _norm(full_name)
    surname_ok = bool(surname) and surname in fn
    init_ok = (initial is None) or any(t and t[0] == initial for t in fn.split())
    return bool(surname_ok and init_ok)


def _acronym(name: str) -> str:
    """Acronym from a club's significant words: 'Paris Saint-Germain' -> 'psg'."""
    words = [w for w in _norm(name).replace("-", " ").split()
             if w not in _TEAM_STOPWORDS]
    return "".join(w[0] for w in words if w)


def resolve_team_name(flashscore_team: str, candidates) -> str:
    """Resolve a Flashscore club name to a SoFIFA team name, or None.

    Flashscore emits abbreviated names ("PSG", "Nice", "Marseille") while SoFIFA
    uses official names ("Paris Saint-Germain", "OGC Nice", "Olympique de
    Marseille"). Strategy, in order: explicit override → token-set ratio (covers
    substring abbreviations) → acronym match (covers "PSG") → WRatio fallback.
    Returns None (→ fail-safe gate) when nothing clears the bar.
    """
    from rapidfuzz import process, fuzz
    if not flashscore_team or not candidates:
        return None
    cands = [str(c) for c in candidates]
    qn = _norm(flashscore_team)
    if qn in _TEAM_OVERRIDES and _TEAM_OVERRIDES[qn] in cands:
        return _TEAM_OVERRIDES[qn]
    m = process.extractOne(flashscore_team, cands, scorer=fuzz.token_set_ratio,
                           processor=_norm)
    if m and m[1] >= 85:
        return m[0]
    qacr = qn.replace(" ", "")
    acro_hits = [c for c in cands if _acronym(c) == qacr]
    if len(acro_hits) == 1:
        return acro_hits[0]
    m2 = process.extractOne(flashscore_team, cands, scorer=fuzz.WRatio,
                            processor=_norm)
    return m2[0] if m2 and m2[1] >= 88 else None


def compute_replacement_ovr(squad_overalls) -> float:
    """Replacement level = squad median OVR. None if no usable ratings."""
    vals = [float(v) for v in squad_overalls if v is not None and str(v) != ""]
    if not vals:
        return None
    return float(statistics.median(vals))


def best_ovr_for(abs_name, squad):
    """Resolve an absentee to a squad entry; return (overall, matched_name).

    ``squad`` is a list of {"name", "overall"} dicts. Picks the highest-OVR
    surname+initial match (ties → first), so a one-letter-initial collision
    resolves to the more important player. Returns (None, None) if unmatched.
    """
    cands = [s for s in squad if surname_initial_match(abs_name, s["name"])]
    if not cands:
        return None, None
    best = max(cands, key=lambda s: (s["overall"] if s["overall"] is not None else -1))
    return best["overall"], best["name"]


def attach_importance(absentees, squad):
    """Annotate each absentee with overall / replacement_ovr / importance.

    Returns (annotated_list, n_matched). ``squad`` = [{"name","overall"}].
    importance = max(0, overall - squad_median_ovr); unmatched players get
    overall=None, importance=0 (N3 no-ops on them).
    """
    replacement = compute_replacement_ovr([s["overall"] for s in squad])
    out = []
    matched = 0
    for a in absentees:
        ovr, mname = best_ovr_for(a["name"], squad)
        imp = 0.0
        if ovr is not None and replacement is not None:
            imp = max(0.0, float(ovr) - replacement)
            matched += 1
        out.append({**a,
                    "overall": ovr,
                    "matched_name": mname,
                    "replacement_ovr": replacement,
                    "importance": round(imp, 1)})
    return out, matched


# ---------------------------------------------------------------------------
# soccerdata config + fetch layer (network; cached on disk by soccerdata)
# ---------------------------------------------------------------------------

def _config_path():
    base = os.environ.get("SOCCERDATA_DIR", os.path.expanduser("~/soccerdata"))
    return os.path.join(base, "config", "league_dict.json")


def ensure_leagues():
    """Register TARGET_SOFIFA keys in soccerdata's league_dict.json.

    soccerdata reads this config at IMPORT time, so this MUST run before the
    first ``import soccerdata`` in the process. Idempotent.
    """
    path = _config_path()
    ld = json.load(open(path))
    added = []
    for key, val in TARGET_SOFIFA.items():
        if key not in ld:
            ld[key] = {"SoFIFA": val, "season_start": "Aug", "season_end": "May"}
            added.append(key)
    if added:
        json.dump(ld, open(path, "w"), indent=4, ensure_ascii=False)
    return added


class SquadRatingsFetcher:
    """Lazily fetch + cache SoFIFA squad ratings, scoped per SoFIFA league key.

    Caches the per-league SoFIFA reader and read_teams() in-process; soccerdata
    itself caches the underlying HTML on disk so reruns need no browser.
    """

    def __init__(self, headless=True):
        ensure_leagues()
        import soccerdata as sd  # imported after ensure_leagues
        self._sd = sd
        self._headless = headless
        self._readers = {}    # league_key -> SoFIFA reader
        self._teams = {}      # league_key -> read_teams() DataFrame
        self._squads = {}     # (league_key, sofifa_team) -> [{"name","overall"}]

    def _reader(self, league_key):
        if league_key not in self._readers:
            self._readers[league_key] = self._sd.SoFIFA(
                leagues=league_key, versions="latest", headless=self._headless)
        return self._readers[league_key]

    def resolve_team(self, league_key, flashscore_team):
        """Resolve a Flashscore club name to a SoFIFA team name, or None."""
        if league_key not in self._teams:
            self._teams[league_key] = self._reader(league_key).read_teams()
        names = list(self._teams[league_key]["team"].astype(str))
        return resolve_team_name(flashscore_team, names)

    def squad_overalls(self, league_key, sofifa_team):
        """Return [{"name","overall"}] for a SoFIFA team (cached)."""
        cache_key = (league_key, sofifa_team)
        if cache_key in self._squads:
            return self._squads[cache_key]
        df = self._reader(league_key).read_player_ratings(team=sofifa_team)
        # read_player_ratings is indexed by player name; OVR col standardized.
        ovr_col = next((c for c in ("overallrating", "overall_rating", "overall")
                        if c in df.columns), None)
        squad = []
        for name, row in df.iterrows():
            val = row.get(ovr_col) if ovr_col else None
            try:
                val = int(val) if val is not None and str(val) != "" else None
            except (TypeError, ValueError):
                val = None
            squad.append({"name": str(name), "overall": val})
        self._squads[cache_key] = squad
        return squad


# ---------------------------------------------------------------------------
# Per-match enrichment + batch driver
# ---------------------------------------------------------------------------

def enrich_match(match, fetcher):
    """Return a copy of one N1 match record enriched with importance.

    Adds ``covered`` (bool), ``gate_reason`` (str or None), and per-absentee
    ``overall`` / ``replacement_ovr`` / ``importance`` when covered.
    """
    out = dict(match)
    league_key = sofifa_league_for(match.get("league"))
    if league_key is None:
        out["covered"] = False
        out["gate_reason"] = "league-not-covered"
        return out

    sides = {}
    for side, team_name in (("home", match.get("home_team")),
                            ("away", match.get("away_team"))):
        sofifa_team = fetcher.resolve_team(league_key, team_name) if team_name else None
        if sofifa_team is None:
            out["covered"] = False
            out["gate_reason"] = f"team-not-found:{side}"
            return out
        sides[side] = sofifa_team

    out["covered"] = True
    out["gate_reason"] = None
    out["sofifa_league"] = league_key
    for side in ("home", "away"):
        squad = fetcher.squad_overalls(league_key, sides[side])
        annotated, matched = attach_importance(match.get(side, []), squad)
        out[side] = annotated
        out[f"{side}_sofifa_team"] = sides[side]
        out[f"{side}_squad_size"] = len(squad)
        out[f"{side}_matched"] = matched
    return out


def _latest_availability_file(date_str):
    if date_str:
        path = os.path.join(OUT_DIR, f"availability_{date_str}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return path
    files = sorted(f for f in glob.glob(os.path.join(OUT_DIR, "availability_*.json"))
                   if "_importance_" not in f)
    if not files:
        raise FileNotFoundError("no output/availability_*.json found")
    return files[-1]


def run(date_str=None, headless=True):
    in_path = _latest_availability_file(date_str)
    import re
    date = re.search(r"availability_(\d{4}-\d{2}-\d{2})", in_path).group(1)
    avail = json.load(open(in_path))
    print(f"[import] {len(avail)} matches from {os.path.basename(in_path)}")

    fetcher = SquadRatingsFetcher(headless=headless)
    result = {}
    n_covered = n_abs = n_matched = 0
    for mid, m in avail.items():
        try:
            enriched = enrich_match(m, fetcher)
        except Exception as e:
            enriched = dict(m)
            enriched["covered"] = False
            enriched["gate_reason"] = f"error:{type(e).__name__}"
            print(f"  ! {mid} {m.get('home_team')} v {m.get('away_team')}: {e!r}")
        result[mid] = enriched
        if enriched.get("covered"):
            n_covered += 1
            for side in ("home", "away"):
                n_abs += len(enriched.get(side, []))
                n_matched += enriched.get(f"{side}_matched", 0)
            tags = []
            for side in ("home", "away"):
                imp = [f"{a['matched_name'] or a['name']}={a['importance']}"
                       for a in enriched.get(side, []) if a.get("importance")]
                if imp:
                    tags.append(f"{side}:{', '.join(imp)}")
            print(f"  ✓ {mid} {m.get('home_team')} v {m.get('away_team')}"
                  + (f"  [{' | '.join(tags)}]" if tags else "  (no weighted absentees)"))
        else:
            print(f"  ⊘ {mid} {m.get('home_team')} v {m.get('away_team')}: "
                  f"{enriched.get('gate_reason')}")

    out_path = os.path.join(OUT_DIR, f"availability_importance_{date}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[import] {n_covered}/{len(avail)} covered; "
          f"{n_matched}/{n_abs} absentees matched to OVR → {out_path}")


# ---------------------------------------------------------------------------
# Offline self-test (no network) — exercises the pure logic.
# ---------------------------------------------------------------------------

def _self_test():
    print("=== sofifa_importance self-test (offline) ===")

    # league mapping (incl. sub-phase suffix)
    assert sofifa_league_for("FRANCE: Ligue 1") == "FRA-Ligue 1"
    assert sofifa_league_for("ENGLAND: Premier League - Play Offs") == "ENG-Premier League"
    assert sofifa_league_for("PORTUGAL: Liga Portugal - Relegation") is None
    assert sofifa_league_for("FINLAND: Veikkausliiga") is None  # omitted on purpose
    print("league mapping            ok")

    # team-name resolution (abbreviation, acronym, token-set, override, gate)
    L1 = ["Paris Saint-Germain", "Olympique de Marseille", "OGC Nice",
          "Stade Rennais FC", "RC Lens", "AS Monaco"]
    assert resolve_team_name("PSG", L1) == "Paris Saint-Germain"        # acronym
    assert resolve_team_name("Marseille", L1) == "Olympique de Marseille"  # token-set
    assert resolve_team_name("Nice", L1) == "OGC Nice"
    assert resolve_team_name("Rennes", L1) == "Stade Rennais FC"        # override
    assert resolve_team_name("St Etienne", L1) is None                  # not present → gate
    print("team-name resolution      ok  (PSG=acronym, Rennes=override, St-Etienne gated)")

    # name matching
    assert surname_initial_match("N'Guessan E.", "Evann N'Guessan")
    assert surname_initial_match("Traoré H.", "Hamari Traoré")
    assert not surname_initial_match("Smith J.", "John Brown")
    print("surname+initial match     ok")

    # importance: star vs benchwarmer vs unmatched, median replacement
    squad = [{"name": "Star Player", "overall": 85},
             {"name": "Mid A", "overall": 76},
             {"name": "Mid B", "overall": 75},
             {"name": "Mid C", "overall": 74},
             {"name": "Bench Guy", "overall": 70}]
    # median of [85,76,75,74,70] = 75
    assert compute_replacement_ovr([s["overall"] for s in squad]) == 75
    annotated, matched = attach_importance(
        [{"name": "Kowalski Z.", "reason_class": "injury"},     # unmatched (absent name)
         {"name": "Star P.", "reason_class": "injury"},         # "Star Player" 85 -> imp 10
         {"name": "Guy B.", "reason_class": "suspension"}],     # "Bench Guy" 70 -> imp 0
        squad)
    by = {a["name"]: a for a in annotated}
    assert by["Star P."]["overall"] == 85 and by["Star P."]["importance"] == 10.0, by["Star P."]
    assert by["Guy B."]["overall"] == 70 and by["Guy B."]["importance"] == 0.0, by["Guy B."]
    assert by["Kowalski Z."]["overall"] is None and by["Kowalski Z."]["importance"] == 0.0
    assert matched == 2, matched
    print("importance computation    ok  (star=+10, bench=0, unmatched=0)")

    # empty squad → graceful
    a2, m2 = attach_importance([{"name": "X Y."}], [])
    assert a2[0]["importance"] == 0.0 and m2 == 0
    print("empty-squad fallback      ok")
    print("\nALL OFFLINE TESTS PASSED")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--self-test":
        _self_test()
    else:
        import re
        date_arg = args[0] if args and re.match(r"\d{4}-\d{2}-\d{2}", args[0]) else None
        run(date_arg)
