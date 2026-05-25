"""Pamestoixima fixture discovery (real-betting step 6b).

Logs into Pamestoixima, navigates to a football-only landing page,
scrolls through every listed fixture, and writes a per-fixture record
({home, away, league, kickoff_text, fixture_url, match_id, ts}) to
`output/real_betting/fixtures_<date>.json`. Read-only — no clicks on
odds buttons, no navigation into individual match pages.

The output is intended to be consumed by:
  - `dryrun_batch_placement.py` (scenario #5) — replaces hardcoded
    match_url constants with a (home, away) → fixture_url lookup.
  - Real-betting step 6c (predictions ↔ Pamestoixima matching) —
    joins this snapshot against `output/predictions_<date>.csv` to
    pair our model output with live bookmaker odds.

Anti-patterns that this script deliberately avoids (per
PAMESTOIXIMA_NOTES.md):
  - Clicking the match-row anchor (it captures the click and adds an
    odds button) — we read the anchor's `href` only.
  - Selecting on auto-generated MUI hash classes (`css-<hash>`) —
    we use the Pamestoixima-namespaced `event-box-root` class.
  - Trusting `page.content()` for content existence checks — we use
    Playwright `locator` traversal.

URL strategy: try a sequence of plausible football-only entry URLs in
order; the first one that produces a populated fixture list wins. The
list is broad on first run to absorb the unknown — once the right
landing URL is confirmed, the others become dead weight and can be
trimmed.

Usage:
    python -m real_betting discover-fixtures
    python -m real_betting discover-fixtures --date YYYY-MM-DD  # not used yet
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import config
from .bookmakers.pamestoixima import Pamestoixima
from .session import session_lock


# --- selectors --------------------------------------------------------------

# Football-only landing URLs to try in order. The first one that yields
# a usable fixture list is kept.
#
# These are best-guesses informed by:
#   - The match-URL pattern confirmed in PAMESTOIXIMA_NOTES.md:
#     /en/football/<league-slug>/<teams-slug>/<match-id>
#   - The "sport tab" mention (PAMESTOIXIMA_NOTES.md anti-pattern #2)
#     suggests there IS a sport selector in the navigation; clicking
#     it lands on a sport-specific URL — we go direct.
FOOTBALL_LANDING_CANDIDATES = (
    'https://www.pamestoixima.gr/en/sport/football',
    'https://www.pamestoixima.gr/en/sports/football',
    'https://www.pamestoixima.gr/en/football',
    'https://www.pamestoixima.gr/en/sport/football/upcoming',
    'https://www.pamestoixima.gr/en/sport/football/all',
)

# Per-fixture container. `event-box-root` is the stable namespaced
# class confirmed in PAMESTOIXIMA_NOTES.md. The auto-generated MUI
# `css-<hash>` companion classes are NOT used here on purpose.
EVENT_BOX_SELECTORS = (
    '[class*="event-box-root" i]',
    '.event-box-root',
)

# Indicators that the page rendered SOMETHING — used as a "page is alive"
# signal before we start scrolling.
PAGE_ALIVE_SELECTORS = (
    '[class*="event-box-root" i]',
    '[class*="MuiContainer-root"]',
    'h1',
    'main',
)


# --- runner ----------------------------------------------------------------

class FixtureDiscoverer:
    def __init__(self, pm: Pamestoixima):
        self.pm = pm
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.run_dir = os.path.join(
            config.OUTPUT_DIR, f'fixture_discovery_{ts}'
        )
        os.makedirs(self.run_dir, exist_ok=True)

    @property
    def page(self):
        return self.pm._session.page

    # -- IO helpers --------------------------------------------------------

    def _shot(self, label: str) -> str:
        path = os.path.join(self.run_dir, f'{label}.png')
        try:
            self.page.screenshot(path=path, full_page=True)
        except Exception as e:
            print(f"[discover] screenshot {label} failed: {e}")
        return path

    def _dump(self, label: str) -> None:
        base = os.path.join(self.run_dir, label)
        try:
            self.page.screenshot(path=base + '.png', full_page=True)
        except Exception:
            pass
        try:
            with open(base + '.html', 'w', encoding='utf-8') as f:
                f.write(self.page.content())
        except Exception:
            pass
        try:
            with open(base + '.url', 'w') as f:
                f.write(self.page.url)
        except Exception:
            pass
        print(f"[discover] dump → {base}.{{png,html,url}}")

    # -- navigation --------------------------------------------------------

    def _navigate_to_football_landing(self) -> Optional[str]:
        """Try each candidate URL in order. Return the URL that loaded a
        page containing at least one event-box-root, else None."""
        for url in FOOTBALL_LANDING_CANDIDATES:
            print(f"[discover] Trying landing: {url}")
            try:
                self.page.goto(url)
            except Exception as e:
                print(f"[discover]   goto raised: {e!r}")
                continue
            self.pm._session.human_pause()
            try:
                self.page.wait_for_load_state('networkidle', timeout=15000)
            except PlaywrightTimeoutError:
                pass
            self.pm._dismiss_overlays()

            # Is the page alive at all?
            page_alive = False
            for sel in PAGE_ALIVE_SELECTORS:
                try:
                    if self.page.locator(sel).count() > 0:
                        page_alive = True
                        break
                except Exception:
                    continue
            if not page_alive:
                print(f"[discover]   page didn't render anything recognisable.")
                continue

            # Are there fixtures here?
            n = 0
            for sel in EVENT_BOX_SELECTORS:
                try:
                    n = self.page.locator(sel).count()
                    if n > 0:
                        break
                except Exception:
                    continue
            print(f"[discover]   event-box-root count: {n}")
            if n > 0:
                return url

        return None

    def _scroll_until_stable(self, max_passes: int = 30) -> int:
        """Scroll the page until the event-box-root count stops growing
        for 2 consecutive passes (or we hit max_passes). Returns the
        final count."""
        try:
            vp = self.page.viewport_size
            self.page.mouse.move((vp['width'] or 1280) // 2,
                                  (vp['height'] or 800) // 2)
        except Exception:
            pass

        last_count = -1
        stable_passes = 0
        for i in range(max_passes):
            # Triple-poke per PAMESTOIXIMA_NOTES.md (different scrollables
            # respond to different events).
            try:
                self.page.mouse.wheel(0, 800)
            except Exception:
                pass
            try:
                self.page.keyboard.press('PageDown')
            except Exception:
                pass
            try:
                self.page.evaluate(
                    'window.scrollBy(0, Math.floor(window.innerHeight * 0.8))')
            except Exception:
                pass
            self.pm._session.human_pause()

            count = 0
            for sel in EVENT_BOX_SELECTORS:
                try:
                    count = self.page.locator(sel).count()
                    if count > 0:
                        break
                except Exception:
                    continue
            if count == last_count:
                stable_passes += 1
                if stable_passes >= 2:
                    print(f"[discover]   scroll stable at {count} fixtures "
                          f"(after {i + 1} pass(es)).")
                    return count
            else:
                stable_passes = 0
            last_count = count

        print(f"[discover]   max scroll passes reached at {last_count} fixtures.")
        return last_count

    # -- extraction --------------------------------------------------------

    def _extract_fixtures(self) -> list[dict]:
        """For every event-box-root on the page, extract a fixture record.

        Uses page.evaluate so we can walk the DOM richly in a single
        round-trip rather than chained Playwright .locator() calls
        (which each pay a CDP latency cost).
        """
        # The JS below is intentionally conservative — it captures the
        # element's outerHTML in addition to the parsed fields so the
        # caller can post-hoc parse anything we missed.
        js = """
        () => {
          const boxes = document.querySelectorAll(
            '[class*="event-box-root" i], .event-box-root'
          );
          const seen = new Set();
          const out = [];
          for (const box of boxes) {
            // Find the anchor whose href looks like a fixture URL.
            // Pattern: /en/football/<slug>/<slug>/<digits>.
            let href = null;
            const anchors = box.querySelectorAll('a[href]');
            for (const a of anchors) {
              const h = a.getAttribute('href') || '';
              if (/\\/en\\/(football|live\\/football)\\/[^\\/]+\\/[^\\/]+\\/\\d+/.test(h)) {
                href = h;
                break;
              }
            }
            if (!href) continue;
            if (seen.has(href)) continue;
            seen.add(href);

            // Extract teams. Look for team-named children first (Pamestoixima
            // typically has `.participantHome` / `.participantAway` or
            // class-substring variants); fall back to splitting the box's
            // innerText on " v " / " vs ".
            const text = (box.innerText || '').trim();
            let home = null, away = null;
            const candHome = box.querySelector(
              '[class*="participantHome" i], [class*="homeName" i], ' +
              '[class*="home-name" i]'
            );
            const candAway = box.querySelector(
              '[class*="participantAway" i], [class*="awayName" i], ' +
              '[class*="away-name" i]'
            );
            if (candHome && candAway) {
              home = (candHome.innerText || '').trim();
              away = (candAway.innerText || '').trim();
            } else {
              // Heuristic split: the visible team text usually appears as
              // "Home\\nAway" on two lines, with odds / market on subsequent
              // lines. Take the first two non-empty lines that don't look
              // like times or scores.
              const lines = text.split(/\\n+/).map(s => s.trim()).filter(Boolean);
              const teamLines = lines.filter(s =>
                !/^\\d{1,2}[:.]\\d{2}/.test(s) &&        // not a clock
                !/^\\d{1,2}\\/\\d{1,2}/.test(s) &&       // not a date
                !/^(LIVE|FT|HT)$/i.test(s) &&            // not a status badge
                !/^\\d+(\\.\\d+)?$/.test(s)              // not an odd
              );
              if (teamLines.length >= 2) {
                home = teamLines[0];
                away = teamLines[1];
              }
            }

            // Kickoff text: look for any child whose text matches HH:MM.
            let kickoff_text = null;
            const all = box.querySelectorAll('*');
            for (const el of all) {
              const t = (el.textContent || '').trim();
              const m = t.match(/^(\\d{1,2}[:.]\\d{2})$/);
              if (m) { kickoff_text = m[1].replace('.', ':'); break; }
            }

            // League — best effort: walk up to the nearest ancestor with a
            // header-like child. League grouping varies; this is informational.
            let league = null;
            let ancestor = box.parentElement;
            let walked = 0;
            while (ancestor && walked < 6) {
              const header = ancestor.querySelector(
                '[class*="leagueHeader" i], [class*="competitionName" i], ' +
                '[class*="category" i] h6, [class*="title" i]'
              );
              if (header) {
                league = (header.innerText || '').trim();
                if (league) break;
              }
              ancestor = ancestor.parentElement;
              walked += 1;
            }

            // Extract match_id from the href's trailing digits.
            const idMatch = href.match(/(\\d+)\\/?$/);
            const match_id = idMatch ? idMatch[1] : null;

            // Absolute URL.
            const absUrl = href.startsWith('http') ? href
              : ('https://www.pamestoixima.gr' + href);

            out.push({
              home, away, league, kickoff_text,
              fixture_url: absUrl,
              match_id,
            });
          }
          return out;
        }
        """
        try:
            fixtures = self.page.evaluate(js)
        except Exception as e:
            print(f"[discover] extraction JS raised: {e!r}")
            return []
        # Drop entries missing either team or fixture_url — we can't use them.
        clean = [
            f for f in fixtures
            if f.get('home') and f.get('away') and f.get('fixture_url')
        ]
        dropped = len(fixtures) - len(clean)
        if dropped:
            print(f"[discover] dropped {dropped} entries with missing teams/URL.")
        return clean

    # -- flow --------------------------------------------------------------

    def run(self) -> bool:
        print(f"[discover] Output dir: {self.run_dir}")
        landing = self._navigate_to_football_landing()
        if landing is None:
            print(f"[discover] No football landing URL produced fixtures. "
                  f"Candidates tried: {FOOTBALL_LANDING_CANDIDATES}")
            self._dump('00_no_landing')
            return False
        print(f"[discover] Using landing: {landing}")
        self._shot('01_on_landing')

        n_after_scroll = self._scroll_until_stable()
        self._shot('02_after_scroll')

        fixtures = self._extract_fixtures()
        print(f"[discover] Extracted {len(fixtures)} fixtures "
              f"(scroll-final count {n_after_scroll}).")

        # Persist.
        today_iso = datetime.date.today().isoformat()
        out_path = os.path.join(config.OUTPUT_DIR,
                                f'fixtures_{today_iso}.json')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        record = {
            'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'landing_url': landing,
            'scroll_final_count': n_after_scroll,
            'count': len(fixtures),
            'fixtures': fixtures,
        }
        with open(out_path, 'w') as f:
            json.dump(record, f, indent=2)
        print(f"[discover] Wrote → {out_path}")

        # Also write a copy inside the run dir for easy post-mortem.
        with open(os.path.join(self.run_dir, 'fixtures_snapshot.json'), 'w') as f:
            json.dump(record, f, indent=2)

        # Sanity preview: first 5 fixtures.
        for i, f in enumerate(fixtures[:5]):
            print(f"  [{i}] {f['home']} vs {f['away']} — "
                  f"{f.get('league') or '?'} — {f.get('kickoff_text') or '?'} "
                  f"→ {f['fixture_url']}")
        if len(fixtures) > 5:
            print(f"  ... and {len(fixtures) - 5} more.")
        return True


# --- lookup helper -----------------------------------------------------------

def find_fixture_url(home: str, away: str,
                     fixtures_path: Optional[str] = None,
                     min_score: int = 80) -> Optional[dict]:
    """Locate a Pamestoixima fixture by team names. Fuzzy-matches both
    home and away against the latest fixtures_<date>.json snapshot via
    rapidfuzz. Returns the matched fixture record (with `fixture_url`)
    or None.

    Default snapshot path: output/real_betting/fixtures_<today>.json.
    If that's missing, falls back to the most recent fixtures_*.json
    under output/real_betting/.

    `min_score` is the cutoff for the *worse* of the two team-name
    matches (so both home AND away must clear it). 80 = generous,
    typical for the Pamestoixima ↔ Flashscore name mapping problem.
    """
    import glob
    from rapidfuzz import fuzz

    if fixtures_path is None:
        today_path = os.path.join(
            config.OUTPUT_DIR,
            f'fixtures_{datetime.date.today().isoformat()}.json',
        )
        if os.path.exists(today_path):
            fixtures_path = today_path
        else:
            candidates = sorted(
                glob.glob(os.path.join(config.OUTPUT_DIR, 'fixtures_*.json')),
                reverse=True,
            )
            if not candidates:
                return None
            fixtures_path = candidates[0]
    if not os.path.exists(fixtures_path):
        return None

    try:
        with open(fixtures_path) as f:
            snap = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    best_score = 0
    best_record = None
    for f in snap.get('fixtures', []):
        h = (f.get('home') or '').lower().strip()
        a = (f.get('away') or '').lower().strip()
        if not h or not a:
            continue
        s_home = fuzz.token_set_ratio(home.lower().strip(), h)
        s_away = fuzz.token_set_ratio(away.lower().strip(), a)
        worse = min(s_home, s_away)
        if worse >= min_score and worse > best_score:
            best_score = worse
            best_record = dict(f, _match_score=worse)
    return best_record


# --- CLI ---------------------------------------------------------------------

def cmd_discover_fixtures(args) -> int:
    """CLI entrypoint. Headed mode forced — fixture-listing pages
    sometimes have promo modals that we want to see + dismiss visually
    during selector iteration."""
    print(f"[discover] Headed mode forced.")
    try:
        with session_lock():
            pm = Pamestoixima(headless=False, reuse_session=True)
            try:
                if not pm.login():
                    print(f"[discover] Login failed; aborting.")
                    return 1
                runner = FixtureDiscoverer(pm)
                ok = runner.run()
                return 0 if ok else 1
            finally:
                pm.close()
    except RuntimeError as e:
        print(f"[discover] Error: {e}", file=sys.stderr)
        return 1
