"""D4 / N1 — Availability extractor (READ-ONLY).

Reads the day's scraped fixtures (``output/matches_<date>.json``), visits each
Flashscore *predicted lineups* page, and extracts the structured
"Will not play" list per side.

Output → ``output/availability_<date>.json``::

    { "<match_id>": {
        "home_team": str, "away_team": str, "league": str,
        "lineups_url": str, "ts": iso8601,
        "home": [{"name","player_id","reason","reason_class"}, ...],
        "away": [...] }, ... }

This step does NOT touch the model, betting flow, or UI. It only writes the
availability JSON that a future importance join (N2, SoFIFA) and adjuster (N3)
will consume. Headless Chromium — Flashscore allows it (unlike Pamestoixima).

GOTCHAS baked in (learned 2026-05-27, see FOOTBALL_NEXT_STEPS D4):
  * Home/away come from the page ``<title>`` ("HOME v AWAY"), NOT the URL slug
    order. The slug ``nice/st-etienne`` was actually *St-Etienne home* — slug
    order is unreliable. We cross-check the title against matches_<date>.json.
  * Each participant sits in its own ``wcl-lineupsParticipantGeneral-left|right``
    wrapper; **left = home column, right = away column** (home shown on left).
  * The reason text is ``data-testid="wcl-scores-caption-05"``; an absentee is a
    participant wrapper that *contains* such a caption (starters/subs don't).
  * "Will not play" is available on the night-before *predicted* lineups, so
    this fits the existing pre-prediction cadence.

Usage::

    python3 scripts/d4_injuries/extract_availability.py [YYYY-MM-DD]
    # offline parser self-test against a cached lineups HTML:
    python3 scripts/d4_injuries/extract_availability.py --from-html <file.html>
"""

import asyncio
import datetime
import glob
import json
import os
import re
import sys

from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "output")

# ---------------------------------------------------------------------------
# Parsing (pure, testable offline against cached HTML)
# ---------------------------------------------------------------------------

_SIDE_TESTID = {"left": "home", "right": "away"}


def classify_reason(reason: str) -> str:
    """Map a Flashscore reason string to a coarse class the adjuster weights.

    injury / suspension are genuine availability hits (reason_weight ~1.0);
    inactive is ambiguous rotation/fitness (low weight); doubtful is partial.
    """
    r = (reason or "").lower()
    if any(k in r for k in ("injur", "knock", "strain", "fracture", "surgery",
                            "torn", "ruptur", "broken")):
        return "injury"
    if any(k in r for k in ("suspend", "yellow card", "red card", "ban")):
        return "suspension"
    if any(k in r for k in ("doubt", "question", "fitness test")):
        return "doubtful"
    if "inactive" in r:
        return "inactive"
    return "other"


def parse_absentees(html: str) -> dict:
    """Extract the per-side "Will not play" list from a lineups-page HTML.

    Returns ``{"home": [...], "away": [...]}`` where each entry is
    ``{name, player_id, reason, reason_class}``. An absentee is any
    participant wrapper that carries a reason caption.
    """
    soup = BeautifulSoup(html, "html.parser")
    out = {"home": [], "away": []}
    for testid_side, sidekey in _SIDE_TESTID.items():
        sel = f'[data-testid="wcl-lineupsParticipantGeneral-{testid_side}"]'
        for el in soup.select(sel):
            cap = el.select_one('[data-testid="wcl-scores-caption-05"]')
            if cap is None:
                continue  # a starter / substitute, not a "will not play" entry
            a = el.select_one('a[href^="/player/"]')
            if a is None:
                continue
            href = a.get("href", "")
            player_id = href.rstrip("/").split("/")[-1] or None
            name = a.get_text(strip=True)
            reason = cap.get_text(strip=True)
            if not name:
                continue
            out[sidekey].append({
                "name": name,
                "player_id": player_id,
                "reason": reason,
                "reason_class": classify_reason(reason),
            })
    return out


def parse_title_teams(title: str):
    """Authoritative home/away from the page title: "... | HOME v AWAY <date>...".

    Returns (home, away) or (None, None). Title order is trustworthy; URL slug
    order is NOT (see module docstring).
    """
    if not title:
        return None, None
    m = re.search(r"\|\s*(.+?)\s+v\s+(.+?)\s+\d{1,2}[/.]\d{1,2}[/.]\d{2,4}", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _lineups_url(match: dict) -> str:
    base = (match.get("base_url") or "").rstrip("/")
    return f"{base}/summary/lineups/?mid={match['match_id']}"


def _latest_matches_file(date_str: str | None) -> str:
    if date_str:
        path = os.path.join(OUT_DIR, f"matches_{date_str}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return path
    files = sorted(glob.glob(os.path.join(OUT_DIR, "matches_*.json")))
    if not files:
        raise FileNotFoundError("no output/matches_*.json found")
    return files[-1]


async def _accept_cookies(page):
    for sel in ("#onetrust-accept-btn-handler",
                'button:has-text("I Accept")',
                'button:has-text("Accept")'):
        try:
            btn = page.locator(sel)
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                await page.wait_for_timeout(400)
                return
        except Exception:
            pass


async def _scrape_one(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await _accept_cookies(page)
    for sel in ('[class*="lineUp"]', '.lf__lineUp', '[class*="lineup"]'):
        try:
            await page.wait_for_selector(sel, timeout=8000)
            break
        except Exception:
            continue
    await page.wait_for_timeout(1800)
    html = await page.content()
    title = await page.title()
    return html, title


async def run(date_str=None):
    from playwright.async_api import async_playwright

    matches_path = _latest_matches_file(date_str)
    date = re.search(r"matches_(\d{4}-\d{2}-\d{2})", matches_path).group(1)
    matches = json.load(open(matches_path))
    print(f"[avail] {len(matches)} fixtures from {os.path.basename(matches_path)}")

    result = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"))
        page = await ctx.new_page()
        for m in matches:
            mid = m.get("match_id")
            if not mid or not m.get("base_url"):
                continue
            url = _lineups_url(m)
            try:
                html, title = await _scrape_one(page, url)
            except Exception as e:
                print(f"  ! {mid} {m.get('home_team')} v {m.get('away_team')}: {e!r}")
                continue
            avail = parse_absentees(html)
            t_home, t_away = parse_title_teams(title)
            # cross-check: title order vs matches JSON (warn, don't flip silently)
            warn = ""
            if t_home and m.get("home_team") and t_home.split()[0].lower() not in m["home_team"].lower() \
               and m["home_team"].split()[0].lower() not in t_home.lower():
                warn = f"  ⚠ title home {t_home!r} != json home {m['home_team']!r}"
            result[mid] = {
                "home_team": m.get("home_team"),
                "away_team": m.get("away_team"),
                "league": m.get("league"),
                "lineups_url": url,
                "title_teams": [t_home, t_away],
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "home": avail["home"],
                "away": avail["away"],
            }
            print(f"  ✓ {mid} {m.get('home_team')} v {m.get('away_team')}: "
                  f"home={len(avail['home'])} away={len(avail['away'])} out{warn}")
        await browser.close()

    out_path = os.path.join(OUT_DIR, f"availability_{date}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    tot = sum(len(v["home"]) + len(v["away"]) for v in result.values())
    print(f"\n[avail] {len(result)} matches, {tot} absentees → {out_path}")


def _selftest(html_path):
    """Offline parser check against a cached lineups HTML dump."""
    html = open(html_path).read()
    out = parse_absentees(html)
    print(f"=== parse_absentees({os.path.basename(html_path)}) ===")
    for side in ("home", "away"):
        print(f"  {side}: {len(out[side])}")
        for a in out[side]:
            print(f"    {a['name']:<18} [{a['reason_class']:<10}] {a['reason']!r}  id={a['player_id']}")
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--from-html":
        _selftest(args[1])
    else:
        date_arg = args[0] if args and re.match(r"\d{4}-\d{2}-\d{2}", args[0]) else None
        asyncio.run(run(date_arg))
