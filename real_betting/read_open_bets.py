"""Pamestoixima open-bets scraper (scenario #3B from
real_betting/test_case_scenarios.md).

Drives a logged-in Pamestoixima session to the My Bets page, iterates
every OPEN bet (`<li id="my-bets-O-<uuid>">`), and writes a per-bet
record to `output/real_betting/open_bets_snapshot.json` (latest-wins)
plus an append-only line to `output/real_betting/open_bets_history.jsonl`
for drift tracking and future cashout-decision backtests.

The output schema is consumed by `web_ui/app.py:_load_bookmaker_offers`
(scenario #3A — shipped 2026-05-25). The moment this scraper produces
a fresh snapshot AND `cashout_source='bookmaker'` is set in
`data_sets/betting_config.json`, the live dashboard switches every
matched-match cashout value from the synthetic `stake × odds × adj_prob
× 0.95` formula to the real Pamestoixima offer (with a green `real`
badge alongside the value).

Read-only. The only interactions are: navigation, scroll, clicking
the "My Bets" entry, and (optionally) clicking the "Open" tab to
narrow the listing. Never clicks `.full-cashout-root` — that's the
cashout button itself, see scenario #2 for the click flow.

Validation reference: as of 2026-05-25 the user has two real OPEN
bets on the account (Paderborn–Wolfsburg, Sandefjord–Fredrikstad,
both €2 placed via scenario #5). Both should appear in the snapshot
with their `my-bets-O-<uuid>` row id and a current `cashout_offer`.

Per the existing real_betting policy: /en URL only — no locale
fork. Headed mode forced by the CLI entrypoint.

Run:
    python -m real_betting read-open-bets
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import config
from .bookmakers.pamestoixima import Pamestoixima
from .session import session_lock


# --- selectors (inherited from scenario #2 cashout discovery) ---------------

# "My Bets" entry. Verified 2026-05-22 against the live site —
# `span:text-is("My Bets")` was the first selector to match.
MY_BETS_ENTRY_SELECTORS = (
    'span:text-is("My Bets")',
    'span:has-text("My Bets")',
    'a:has-text("My Bets")',
    'button:has-text("My Bets")',
    '[class*="my-bets" i]',
    '[class*="myBets" i]',
)

# Best-effort "Open" tab — the My Bets list usually opens on the
# Open subset already, so this is mostly defensive.
OPEN_BETS_TAB_SELECTORS = (
    'button:has-text("Open"):not([disabled])',
    'button:has-text("Active"):not([disabled])',
    'a:has-text("Open Bets")',
    'a:has-text("Active Bets")',
    '[role="tab"]:has-text("Open")',
    '[role="tab"]:has-text("Active")',
)

# Per-bet row id pattern. Verified 2026-05-22.
# Example: `<li id="my-bets-O-82525f37-4132-422e-9720-fbb5fdb824b0">`
OPEN_BET_ROW_SELECTOR = 'li[id^="my-bets-O-"]'

# Output paths.
OUT_DIR = os.path.join(config.OUTPUT_DIR, 'real_betting') \
    if not config.OUTPUT_DIR.endswith('real_betting') \
    else config.OUTPUT_DIR
SNAPSHOT_PATH = os.path.join(OUT_DIR, 'open_bets_snapshot.json')
HISTORY_PATH = os.path.join(OUT_DIR, 'open_bets_history.jsonl')


# --- runner ----------------------------------------------------------------

class OpenBetsReader:
    def __init__(self, pm: Pamestoixima):
        self.pm = pm
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.run_dir = os.path.join(
            config.OUTPUT_DIR, f'open_bets_read_{ts}'
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
            print(f"[open-bets] screenshot {label} failed: {e}")
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
        print(f"[open-bets] dump → {base}.{{png,html,url}}")

    # -- click helper ------------------------------------------------------

    def _click_first_visible(self, selectors,
                             timeout_ms: int = 4000) -> Optional[str]:
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                loc.click()
                return sel
            except PlaywrightTimeoutError:
                continue
            except Exception as e:
                print(f"[open-bets] click via {sel!r} raised: {e!r}")
                continue
        return None

    # -- flow --------------------------------------------------------------

    def run(self) -> Optional[dict]:
        print(f"[open-bets] Output dir: {self.run_dir}")
        self._shot('00_after_login')
        self.pm._dismiss_overlays()

        # Step 1: open My Bets.
        sel = self._click_first_visible(MY_BETS_ENTRY_SELECTORS, timeout_ms=5000)
        if not sel:
            print(f"[open-bets] Could not find My Bets entry.")
            self._dump('01_my_bets_not_found')
            return None
        print(f"[open-bets] My Bets entry clicked via {sel!r}")
        self.pm._session.human_pause()
        try:
            self.page.wait_for_load_state('networkidle', timeout=8000)
        except PlaywrightTimeoutError:
            pass

        # Step 1b (best effort): switch to Open tab if not already.
        tab_used = self._click_first_visible(OPEN_BETS_TAB_SELECTORS,
                                              timeout_ms=2000)
        if tab_used:
            print(f"[open-bets] Open tab clicked via {tab_used!r}")
            self.pm._session.human_pause()

        # Step 2: wait for at least one open-bet row OR an explicit
        # empty-state. Either is a valid terminal state. Empty just
        # means no open bets right now.
        try:
            self.page.locator(OPEN_BET_ROW_SELECTOR).first.wait_for(
                state='visible', timeout=5000)
            row_count = self.page.locator(OPEN_BET_ROW_SELECTOR).count()
        except PlaywrightTimeoutError:
            row_count = 0
        print(f"[open-bets] Open-bet rows visible: {row_count}")

        # Always dump the My Bets page HTML — useful for selector
        # iteration if downstream extraction misses anything.
        self._dump('02_my_bets_page')

        # Step 3: extract every open-bet record in a single page.evaluate
        # round-trip so we don't pay CDP latency per row.
        bets = self._extract_open_bets()
        print(f"[open-bets] Extracted {len(bets)} open-bet record(s).")

        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        snapshot = {
            'ts': ts,
            'count': len(bets),
            'bets': bets,
        }

        os.makedirs(OUT_DIR, exist_ok=True)
        with open(SNAPSHOT_PATH, 'w') as f:
            json.dump(snapshot, f, indent=2)
        print(f"[open-bets] Snapshot → {SNAPSHOT_PATH}")

        # Append every bet as a separate JSONL line for drift tracking +
        # future scenario #4 backtests.
        try:
            with open(HISTORY_PATH, 'a') as f:
                for b in bets:
                    f.write(json.dumps(dict(b, ts=ts)) + '\n')
            print(f"[open-bets] Appended {len(bets)} line(s) → {HISTORY_PATH}")
        except OSError as e:
            print(f"[open-bets] Could not append history: {e}")

        # Also save a copy of the snapshot inside the run dir for
        # post-mortem alongside the HTML dump.
        try:
            with open(os.path.join(self.run_dir, 'snapshot.json'), 'w') as f:
                json.dump(snapshot, f, indent=2)
        except OSError:
            pass

        # Preview.
        for i, b in enumerate(bets[:5]):
            offer = f"€{b['cashout_offer']:.2f}" if b.get('cashout_offer') is not None else (
                'PAUSED' if b.get('paused') else 'n/a')
            print(f"  [{i}] {b['home']} vs {b['away']} — "
                  f"{b.get('market') or '?'} {b.get('selection') or '?'} "
                  f"@ {b.get('odds') or '?'} — stake €{b.get('stake_eur') or '?'} "
                  f"— cashout {offer} — uuid {b['pamestoixima_uuid'][:8]}…")
        if len(bets) > 5:
            print(f"  ... and {len(bets) - 5} more.")

        return snapshot

    # -- extraction --------------------------------------------------------

    def _extract_open_bets(self) -> list[dict]:
        """For every `<li id="my-bets-O-<uuid>">`, build a record. Single
        page.evaluate round-trip; richer than chained Playwright queries."""
        js = """
        () => {
          const rows = document.querySelectorAll('li[id^="my-bets-O-"]');
          const out = [];
          for (const row of rows) {
            const id = row.getAttribute('id') || '';
            const uuidMatch = id.match(/^my-bets-O-(.+)$/);
            const uuid = uuidMatch ? uuidMatch[1] : null;

            // Fixture link — try the canonical pre-match or live URL pattern.
            let fixture_url = null;
            let match_id = null;
            let league_slug = null;
            let home_slug = null;
            let away_slug = null;
            for (const a of row.querySelectorAll('a[href]')) {
              const h = a.getAttribute('href') || '';
              const m = h.match(/\\/en\\/(?:football|live\\/football)\\/([^\\/]+)\\/([^\\/]+)\\/(\\d+)/);
              if (m) {
                league_slug = m[1];
                const teams = m[2];
                match_id = m[3];
                fixture_url = h.startsWith('http') ? h
                  : ('https://www.pamestoixima.gr' + h);
                const sep = teams.indexOf('-v-');
                if (sep > 0) {
                  home_slug = teams.slice(0, sep);
                  away_slug = teams.slice(sep + 3);
                }
                break;
              }
            }

            // Display team names — DOM gives correct caps where slug-derive
            // would title-case "IK" → "Ik".
            const slugToName = (slug) => {
              if (!slug) return null;
              return slug.split('-').map(w =>
                w.length === 0 ? w : w[0].toUpperCase() + w.slice(1)
              ).join(' ');
            };
            let home = null, away = null;
            const selName = row.querySelector('.selectionName, [class*="selectionName" i]');
            // The selectionName on My Bets rows is the bet's pick (e.g.
            // "Over 2.5"), NOT the home team. Don't trust it for teams.
            // Try participantHome / participantAway / homeTeam / awayTeam.
            const homeEl = row.querySelector(
              '[class*="participantHome" i], .homeTeam, [class*="homeTeam" i], ' +
              '[class*="home-name" i]');
            const awayEl = row.querySelector(
              '[class*="participantAway" i], .awayTeam, [class*="awayTeam" i], ' +
              '[class*="away-name" i]');
            if (homeEl && awayEl) {
              home = (homeEl.innerText || '').trim() || null;
              away = (awayEl.innerText || '').trim() || null;
            }
            if (!home) home = slugToName(home_slug);
            if (!away) away = slugToName(away_slug);

            // Market + selection — both live on the bet row. The .selectionName
            // span is the user's pick string ("Over 2.5", "Home", etc.). The
            // betTypeName / marketName span is the market label ("Total Goals
            // Over/Under", "Match Result", etc.). Names may vary; collect any
            // class containing "selectionName" or "marketName" / "betType".
            let selection = null, market = null;
            const selEl = row.querySelector(
              '.selectionName, [class*="selectionName" i]');
            if (selEl) selection = (selEl.innerText || '').trim() || null;
            const marketEl = row.querySelector(
              '[class*="marketName" i], [class*="betTypeName" i], ' +
              '[class*="marketType" i]');
            if (marketEl) market = (marketEl.innerText || '').trim() || null;

            // Stake and odds. Pamestoixima renders e.g. "Stake: 2,00€" and
            // "@2.00" / "Odds: 2.00". Best-effort regex over the row text.
            const text = (row.innerText || '').replace(/\\s+/g, ' ').trim();
            let stake_eur = null;
            const stakeMatch = text.match(/(?:stake|ποσό)[:\\s]*([\\d.,]+)\\s*€?/i);
            if (stakeMatch) {
              const v = parseFloat(stakeMatch[1].replace(',', '.'));
              if (!isNaN(v)) stake_eur = v;
            }
            let odds = null;
            const oddsMatch = text.match(/(?:@|odds[:\\s]+)([\\d.,]+)/i);
            if (oddsMatch) {
              const v = parseFloat(oddsMatch[1].replace(',', '.'));
              if (!isNaN(v)) odds = v;
            }

            // Cashout offer. The .full-cashout-root button is the canonical
            // selector (verified scenario #2). Text format: "Cash Out\\n€1.47"
            // pre-click; "Confirm Cash Out\\n€1.47" post-click (we should never
            // be in that state on a fresh page load).
            let cashout_offer = null;
            let cashout_offer_text = null;
            let paused = false;
            const co = row.querySelector('.full-cashout-root, [class*="full-cashout-root" i]');
            if (co) {
              cashout_offer_text = (co.innerText || '').trim() || null;
              // Disabled state.
              const disabled = co.hasAttribute('disabled');
              const ariaDisabled = (co.getAttribute('aria-disabled') || '').toLowerCase();
              const cls = co.getAttribute('class') || '';
              if (disabled || ariaDisabled === 'true' || /disabled|paused/i.test(cls)) {
                paused = true;
              } else if (cashout_offer_text) {
                const m = cashout_offer_text.replace(/\\n/g, ' ').match(/€\\s*([\\d.,]+)/);
                if (m) {
                  const v = parseFloat(m[1].replace(',', '.'));
                  if (!isNaN(v)) cashout_offer = v;
                }
              }
            } else {
              // No cashout button at all on the row → offer not available
              // for this bet (event in-play and offer fully withdrawn, or
              // pre-match state where cashout isn't surfaced).
              paused = true;
            }

            out.push({
              pamestoixima_uuid: uuid,
              match_id,
              home, away,
              league_slug,
              home_slug, away_slug,
              fixture_url,
              market,
              selection,
              odds,
              stake_eur,
              cashout_offer,
              cashout_offer_text,
              paused,
            });
          }
          return out;
        }
        """
        try:
            return self.page.evaluate(js) or []
        except Exception as e:
            print(f"[open-bets] extraction JS raised: {e!r}")
            return []


# --- CLI ---------------------------------------------------------------------

def cmd_read_open_bets(args) -> int:
    """CLI entrypoint. Headed mode forced. Reads OPEN bets only; the
    consumer (`web_ui/app.py:_load_bookmaker_offers`) doesn't care
    about CASHED_OUT / SETTLED rows."""
    print(f"[open-bets] Headed mode forced.")
    try:
        with session_lock():
            pm = Pamestoixima(headless=False, reuse_session=True)
            try:
                if not pm.login():
                    print(f"[open-bets] Login failed; aborting.")
                    return 1
                runner = OpenBetsReader(pm)
                snap = runner.run()
                return 0 if snap is not None else 1
            finally:
                pm.close()
    except RuntimeError as e:
        print(f"[open-bets] Error: {e}", file=sys.stderr)
        return 1
