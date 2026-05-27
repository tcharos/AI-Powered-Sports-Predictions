"""D4 / N2 — SoFIFA importance join.

Takes the N1 availability output (Flashscore "Will not play" list per fixture,
`output/availability_<date>.json`) and attaches a per-absentee *importance*
weight derived from SoFIFA overall ratings (OVR). N3 (`adjust.py`) turns those
weights into a capped, post-model probability shift; this module only produces
the weights.

Importance model (v1)
---------------------
For each absentee we resolve them to a SoFIFA squad row (OVR + position), then::

    replacement_ovr = best OVR among squad-mates in the same position group
                      (excluding the absentee); falls back to squad median.
    importance      = max(0, absentee_ovr - replacement_ovr)

i.e. the *marginal* quality lost vs the player who actually steps in. A backup
(OVR below his position's best alternative) yields importance 0 — losing him
costs nothing — which is exactly the "Maxime Dupé OVR 75 but inactive backup
keeper = no real loss" finding from the N0 probe. OVR is the quality proxy
(SoFIFA exposes no minutes); the reason-class weighting (injury vs inactive) is
applied downstream in N3, not here.

Fetch strategy (the ~10× win confirmed in N0)
---------------------------------------------
The SoFIFA *team-overview* page (`/team/<id>`) carries the whole squad table —
name, OVR, position, value — in ONE load. soccerdata's `read_player_ratings`
fetches one page *per player* (~25-33/team); we instead fetch the team page once
(reusing soccerdata's Cloudflare-capable browser + on-disk cache via `.get()`)
and parse the squad table ourselves. Cache key is `(team_id, version_id)` =
`(team, fifa_update)`, shared with soccerdata's own `players_*.html` cache file.

Coverage gate
-------------
SoFIFA does NOT cover every league (no Mexico/Japan/Turkey; Finland/Greece/Brazil
are thin). A match is adjusted only if (a) its Flashscore league maps to a
SoFIFA-covered key AND (b) BOTH clubs resolve to a SoFIFA team. The per-club
resolve step naturally drops thin-league matches where a club is absent (e.g.
Finland's single covered club). Gated matches are returned with `covered=False`
and their absentees unchanged — N3 must no-op on those.

Usage::

    # enrich an N1 availability file (live SoFIFA fetch, headless):
    python3 ml_project/availability/sofifa_importance.py output/availability_2026-05-28.json
    # offline parser self-test against a cached SoFIFA team page:
    python3 ml_project/availability/sofifa_importance.py --parse-team ~/soccerdata/data/SoFIFA/players_1819_260033.html
"""

import json
import os
import sys
import unicodedata

# ---------------------------------------------------------------------------
# Coverage map: Flashscore "COUNTRY: League" → soccerdata SoFIFA config key.
# Only leagues SoFIFA actually carries (see N0 probe). Unmapped → gated out.
# Thin leagues (Brazil/Greece/Finland) are included but the per-club resolve
# step drops matches whose clubs SoFIFA doesn't have.
# ---------------------------------------------------------------------------
FLASHSCORE_TO_SOFIFA = {
    "ENGLAND: Premier League":      "ENG-Premier League",
    "ENGLAND: Championship":        "ENG-Championship-D4",
    "ENGLAND: League One":          "ENG-League One-D4",
    "ENGLAND: League Two":          "ENG-League Two-D4",
    "GERMANY: Bundesliga":          "GER-Bundesliga",
    "GERMANY: 2. Bundesliga":       "GER-2. Bundesliga-D4",
    "GERMANY: 3. Liga":             "GER-3. Liga-D4",
    "ITALY: Serie A":               "ITA-Serie A",
    "ITALY: Serie B":               "ITA-Serie B-D4",
    "FRANCE: Ligue 1":              "FRA-Ligue 1",
    "FRANCE: Ligue 2":              "FRA-Ligue 2-D4",
    "SPAIN: LaLiga":                "ESP-La Liga",
    "ARGENTINA: Liga Profesional":  "ARG-Liga Profesional",
    "BELGIUM: Jupiler Pro League":  "BEL-Pro League",
    "DENMARK: Superliga":           "DEN-Superliga",
    "AUSTRIA: Bundesliga":          "AUT-Bundesliga-D4",
    "BRAZIL: Serie A Betano":       "BRA-Serie A-D4",
    "CHINA: Super League":          "CHN-Super League",
    "GREECE: Super League":         "GRE-Super League",
    "IRELAND: Premier Division":    "IRL-Premier Division",
    "FINLAND: Veikkausliiga":       "FIN-Veikkausliiga",
}

# The "[Nation] League" value soccerdata expects, per config key. Kept in sync
# with the N0 probe's TARGET_SOFIFA (the registration step needs both halves).
SOFIFA_LEAGUE_VALUE = {
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

# Position-group buckets for the replacement calc (SoFIFA `span.pos` codes).
_POS_GROUP = {}
for _p in ("GK",):                                            _POS_GROUP[_p] = "GK"
for _p in ("RB", "RWB", "RCB", "CB", "LCB", "LB", "LWB"):     _POS_GROUP[_p] = "DEF"
for _p in ("CDM", "RDM", "LDM", "RCM", "CM", "LCM", "RM",
           "LM", "CAM", "RAM", "LAM"):                        _POS_GROUP[_p] = "MID"
for _p in ("RW", "LW", "RF", "CF", "LF", "RS", "ST", "LS"):   _POS_GROUP[_p] = "FWD"

# Club-name join: rapidfuzz WRatio (0-100). WRatio handles Flashscore short/city
# names vs SoFIFA full official names far better than token_set_ratio (e.g.
# "Lyon"→"Olympique Lyonnais" 90 vs token_set's broken "Lyon"→"FC Lorient" 43,
# and "Paris SG"/"Paris FC" disambiguate cleanly). The gate philosophy makes a
# false-negative (fail to resolve → no adjustment) safe and a false-positive
# (wrong squad) dangerous, so the threshold errs high; systematic city-name
# misses that fall below it go in TEAM_NAME_OVERRIDES.
_TEAM_MATCH_MIN = 80

# Flashscore club name → SoFIFA full name, for the cases WRatio can't reach
# (no shared token: Flashscore city name vs SoFIFA official). Looked up before
# the fuzzy match. Grow as live scrapes surface misses (logged in `gate_reason`).
TEAM_NAME_OVERRIDES = {
    "Rennes": "Stade Rennais FC",
}

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
DEFAULT_OUT_DIR = os.path.join(ROOT, "output")


# ---------------------------------------------------------------------------
# soccerdata league registration (config is read at import time → register first)
# ---------------------------------------------------------------------------

def _config_path():
    base = os.environ.get("SOCCERDATA_DIR", os.path.expanduser("~/soccerdata"))
    return os.path.join(base, "config", "league_dict.json")


def ensure_leagues():
    """Register the covered SoFIFA league keys in soccerdata's league_dict.json.

    Idempotent. MUST run before `import soccerdata` in this process — soccerdata
    reads the config at import time.
    """
    path = _config_path()
    with open(path) as f:
        ld = json.load(f)
    added = []
    for key, val in SOFIFA_LEAGUE_VALUE.items():
        if key not in ld:
            ld[key] = {"SoFIFA": val, "season_start": "Aug", "season_end": "May"}
            added.append(key)
    if added:
        with open(path, "w") as f:
            json.dump(ld, f, indent=4, ensure_ascii=False)
    return added


# ---------------------------------------------------------------------------
# Name normalisation + matching (pure)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().replace(".", " ").replace("-", " ").strip()


def _surname_initial(flashscore_name: str):
    """Split a Flashscore "Surname X." into (surname, initial-or-None)."""
    parts = _norm(flashscore_name).split()
    if len(parts) >= 2 and len(parts[-1]) == 1:
        return " ".join(parts[:-1]), parts[-1]
    return " ".join(parts), None


def _name_matches(flashscore_name: str, sofifa_full: str, sofifa_disp: str) -> bool:
    """Surname+initial match of a Flashscore name against a SoFIFA player.

    SoFIFA gives a full name ("Gautier Larsonneur") and a display short name
    ("G. Larsonneur"); Flashscore gives "Larsonneur G." (surname-first). We test
    surname containment + first-initial agreement against the full name (richest).
    """
    surname, initial = _surname_initial(flashscore_name)
    target = _norm(sofifa_full) or _norm(sofifa_disp)
    if not surname or not target:
        return False
    if surname not in target:
        return False
    if initial is None:
        return True
    return any(tok and tok[0] == initial for tok in target.split())


def position_group(pos: str) -> str:
    return _POS_GROUP.get((pos or "").upper().strip(), "MID")


# ---------------------------------------------------------------------------
# Squad-table parsing (pure, testable offline)
# ---------------------------------------------------------------------------

def _parse_value(txt: str):
    """'€3.6M'/'€800K'/'€0' → float euros (None if unparseable)."""
    t = (txt or "").strip().replace("€", "").replace(",", "")
    if not t:
        return None
    mult = 1.0
    if t[-1] in "MmKk":
        mult = 1e6 if t[-1] in "Mm" else 1e3
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def parse_squad(html_text: str) -> list:
    """Parse a SoFIFA team-overview page into a squad list.

    Each entry: {player_id, name, full_name, ovr, potential, position, group,
    value}. `name`/`full_name` come from the player link (display + tooltip),
    `ovr` from the `data-col="oa"` cell, `position` from `span.pos`.
    """
    from lxml import html as lxhtml

    tree = lxhtml.fromstring(html_text)
    tables = tree.xpath("//article/table")
    if not tables:
        return []
    squad = []
    for tr in tables[0].xpath(".//tbody/tr"):
        link = tr.xpath('.//td[2]//a[contains(@href, "/player/")]')
        if not link:
            continue
        link = link[0]
        href = link.get("href", "")
        pid = None
        bits = [b for b in href.split("/") if b]
        for b in bits:
            if b.isdigit():
                pid = b
                break
        disp = (link.text or "").strip()
        full = (link.get("data-tippy-content") or "").strip()
        oa = tr.xpath('.//td[@data-col="oa"]/em/@title') or tr.xpath('.//td[@data-col="oa"]/em/text()')
        pt = tr.xpath('.//td[@data-col="pt"]/em/@title') or tr.xpath('.//td[@data-col="pt"]/em/text()')
        pos = tr.xpath('.//span[contains(@class, "pos")]/text()')
        val = tr.xpath('.//td[@data-col="vl"]/text()')
        try:
            ovr = int(str(oa[0]).strip())
        except (ValueError, IndexError):
            continue  # no OVR → useless row
        pos_s = (pos[0].strip() if pos else "")
        squad.append({
            "player_id": pid,
            "name": disp,
            "full_name": full or disp,
            "ovr": ovr,
            "potential": int(str(pt[0]).strip()) if pt and str(pt[0]).strip().isdigit() else None,
            "position": pos_s,
            "group": position_group(pos_s),
            "value": _parse_value(val[0] if val else ""),
        })
    return squad


# ---------------------------------------------------------------------------
# Importance computation (pure, given a parsed squad)
# ---------------------------------------------------------------------------

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def replacement_ovr(squad: list, player: dict) -> float:
    """Best OVR among squad-mates in the absentee's position group (excluding the
    absentee). Falls back to the squad-wide median when the group is a singleton.
    """
    group = player.get("group")
    others = [p["ovr"] for p in squad
              if p["group"] == group and p.get("player_id") != player.get("player_id")]
    if others:
        return float(max(others))
    med = _median([p["ovr"] for p in squad if p.get("player_id") != player.get("player_id")])
    return float(med) if med is not None else float(player["ovr"])


def match_absentee(flashscore_name: str, squad: list):
    """Resolve a Flashscore absentee name to a squad row (surname+initial)."""
    for p in squad:
        if _name_matches(flashscore_name, p.get("full_name", ""), p.get("name", "")):
            return p
    return None


def importance_for_absentees(absentees: list, squad: list) -> list:
    """Annotate each absentee with its SoFIFA match + importance weight.

    Returns a NEW list of dicts (original fields + matched_to/matched_ovr/
    replacement_ovr/importance/value). Unmatched absentees get importance None
    and matched_to None (N3 treats unknown importance as 0).
    """
    out = []
    for a in absentees:
        p = match_absentee(a.get("name", ""), squad)
        rec = dict(a)
        if p is None:
            rec.update(matched_to=None, matched_ovr=None,
                       replacement_ovr=None, importance=None, value=None)
        else:
            repl = replacement_ovr(squad, p)
            rec.update(
                matched_to=p.get("full_name") or p.get("name"),
                matched_ovr=p["ovr"],
                replacement_ovr=round(repl, 1),
                importance=round(max(0.0, p["ovr"] - repl), 1),
                position=p.get("position") or a.get("position"),
                value=p.get("value"),
            )
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Live SoFIFA fetch (one team-overview page per club, browser+cache reused)
# ---------------------------------------------------------------------------

_TEAMS_CACHE = {}   # league_key → teams DataFrame (per process)
_SQUAD_CACHE = {}   # (league_key, team_id) → parsed squad list (per process)


def _client(league_key: str, headless: bool = True):
    import soccerdata as sd
    return sd.SoFIFA(leagues=league_key, versions="latest", headless=headless)


def _teams(client, league_key: str):
    if league_key not in _TEAMS_CACHE:
        _TEAMS_CACHE[league_key] = client.read_teams()
    return _TEAMS_CACHE[league_key]


def resolve_league(flashscore_league: str):
    """Flashscore "COUNTRY: League" → SoFIFA config key (None if not covered)."""
    return FLASHSCORE_TO_SOFIFA.get((flashscore_league or "").strip())


def resolve_team(teams_df, flashscore_name: str):
    """Resolve a Flashscore club name to (team_id, sofifa_name) or None.

    Explicit TEAM_NAME_OVERRIDES first (exact SoFIFA name), then WRatio fuzzy
    gated at `_TEAM_MATCH_MIN`. Returns None when unsure — the caller gates the
    match out rather than risk attaching the wrong squad.
    """
    from rapidfuzz import process, fuzz

    names = list(teams_df["team"])
    if not names:
        return None

    override = TEAM_NAME_OVERRIDES.get((flashscore_name or "").strip())
    if override:
        for i, n in enumerate(names):
            if _norm(n) == _norm(override):
                row = teams_df.iloc[i]
                return int(row.name), row["team"]

    best = process.extractOne(
        _norm(flashscore_name),
        {i: _norm(n) for i, n in enumerate(names)},
        scorer=fuzz.WRatio,
    )
    if not best or best[1] < _TEAM_MATCH_MIN:
        return None
    row = teams_df.iloc[best[2]]
    return int(row.name), row["team"]


def fetch_squad(league_key: str, team_id: int, client):
    """Fetch + parse one club's squad (OVR/pos/value). Cached per process and on
    disk (shared with soccerdata's `players_<team>_<version>.html`)."""
    ckey = (league_key, team_id)
    if ckey in _SQUAD_CACHE:
        return _SQUAD_CACHE[ckey]
    from soccerdata.sofifa import SO_FIFA_API

    version_id = client.versions.index[0]
    url = SO_FIFA_API + f"/team/{team_id}/?r={version_id}&set=true"
    filepath = client.data_dir / f"players_{team_id}_{version_id}.html"
    reader = client.get(url, filepath)
    html_text = reader.read()
    if isinstance(html_text, bytes):
        html_text = html_text.decode("utf-8", "ignore")
    squad = parse_squad(html_text)
    _SQUAD_CACHE[ckey] = squad
    return squad


def attach_importance(availability: dict, *, enabled: bool = True,
                      headless: bool = True, verbose: bool = False) -> dict:
    """Enrich an N1 availability dict in place-ish (returns the same dict).

    For each match: gate on league coverage + both clubs resolving to SoFIFA.
    Covered matches get `covered=True` and each absentee annotated with
    importance; gated matches get `covered=False` and absentees untouched.
    """
    if not enabled:
        for m in availability.values():
            m["covered"] = False
            m["gate_reason"] = "disabled"
        return availability

    ensure_leagues()
    clients = {}  # league_key → client (one browser context per league)

    for mid, m in availability.items():
        league_key = resolve_league(m.get("league", ""))
        if not league_key:
            m["covered"] = False
            m["gate_reason"] = "league-not-covered"
            if verbose:
                print(f"  - {mid} {m.get('home_team')} v {m.get('away_team')}: league not covered ({m.get('league')})")
            continue
        try:
            client = clients.get(league_key) or _client(league_key, headless=headless)
            clients[league_key] = client
            teams_df = _teams(client, league_key)
        except Exception as e:
            m["covered"] = False
            m["gate_reason"] = f"sofifa-error: {e!r}"
            if verbose:
                print(f"  ! {mid}: SoFIFA error {e!r}")
            continue

        home_r = resolve_team(teams_df, m.get("home_team", ""))
        away_r = resolve_team(teams_df, m.get("away_team", ""))
        if home_r is None or away_r is None:
            m["covered"] = False
            miss = []
            if home_r is None:
                miss.append(m.get("home_team"))
            if away_r is None:
                miss.append(m.get("away_team"))
            m["gate_reason"] = f"club-not-resolved: {miss}"
            if verbose:
                print(f"  - {mid} {m.get('home_team')} v {m.get('away_team')}: club not resolved {miss}")
            continue

        try:
            home_squad = fetch_squad(league_key, home_r[0], client)
            away_squad = fetch_squad(league_key, away_r[0], client)
        except Exception as e:
            m["covered"] = False
            m["gate_reason"] = f"squad-fetch-error: {e!r}"
            if verbose:
                print(f"  ! {mid}: squad fetch error {e!r}")
            continue

        m["covered"] = True
        m["sofifa_home"] = home_r[1]
        m["sofifa_away"] = away_r[1]
        m["home"] = importance_for_absentees(m.get("home", []), home_squad)
        m["away"] = importance_for_absentees(m.get("away", []), away_squad)
        if verbose:
            hi = sum(a.get("importance") or 0 for a in m["home"])
            ai = sum(a.get("importance") or 0 for a in m["away"])
            print(f"  ✓ {mid} {home_r[1]} v {away_r[1]}: "
                  f"home Σimp={hi:.1f} ({len(m['home'])} out), "
                  f"away Σimp={ai:.1f} ({len(m['away'])} out)")

    return availability


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_file(path: str):
    with open(path) as f:
        avail = json.load(f)
    print(f"[N2] {len(avail)} matches from {os.path.basename(path)}")
    attach_importance(avail, enabled=True, headless=True, verbose=True)
    out_path = path.replace("availability_", "availability_importance_")
    if out_path == path:
        out_path = path.rsplit(".", 1)[0] + "_importance.json"
    with open(out_path, "w") as f:
        json.dump(avail, f, indent=2, ensure_ascii=False)
    covered = sum(1 for m in avail.values() if m.get("covered"))
    print(f"\n[N2] {covered}/{len(avail)} matches covered → {out_path}")


def _parse_team_selftest(html_path: str):
    with open(html_path) as f:
        squad = parse_squad(f.read())
    print(f"=== parse_squad({os.path.basename(html_path)}) — {len(squad)} players ===")
    for p in sorted(squad, key=lambda x: -x["ovr"])[:30]:
        v = f"€{p['value']/1e6:.1f}M" if p.get("value") else "-"
        print(f"  {p['ovr']:>3} {p['position']:<4}{p['group']:<4} {p['name']:<22} {v:>8}  "
              f"({p['full_name']})")
    # quick replacement-calc demo on the top-OVR player
    if squad:
        star = max(squad, key=lambda x: x["ovr"])
        repl = replacement_ovr(squad, star)
        print(f"\n  demo: {star['name']} OVR {star['ovr']} @ {star['group']} → "
              f"replacement {repl:.0f} → importance {max(0, star['ovr']-repl):.0f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--parse-team":
        _parse_team_selftest(args[1])
    elif args:
        _run_file(args[0])
    else:
        print(__doc__)
