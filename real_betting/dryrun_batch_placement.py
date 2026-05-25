"""DRY-RUN: place multiple single bets, each as its own slip.

Validates scenario #5 from `real_betting/test_case_scenarios.md`
against the live Pamestoixima account.

Hardcoded batch (do NOT generalise — this is a one-shot test artifact):

    1. Paderborn vs Wolfsburg     O/U Over 2.5  @ 1.98  €2.00
    2. Sandefjord vs Fredrikstad  O/U Over 2.5  @ 1.70  €2.00

Each bet becomes its OWN slip on Pamestoixima — no multi/parlay
accumulator. Between iterations the script:
  - clears any leftover selection from the betslip,
  - re-asserts the betslip counter is `(0)` BEFORE clicking the next
    outcome (silent-parlay guard — if a previous Place Bet left an
    odd in the slip, the next iteration would wrongly accumulate),
  - re-reads the balance and refuses to proceed if it can't cover
    the next bet's stake,
  - aborts the whole batch if the odds at click-time have drifted
    more than 5% from `odds_at_plan` (the value below).

Safety gates (asserted at module load; refusal exits cleanly):
    EXECUTE_PLACE_BETS    = False   ← flip to True only when running
                                       the real test. Default safe.
    MAX_BETS_PER_RUN      = 2       ← script refuses if BETS exceeds.
    MAX_STAKE_PER_BET_EUR = 2.00    ← per-bet cap; per-iteration check.
    MAX_TOTAL_STAKE_EUR   = 4.00    ← sum-of-stakes cap across the
                                       whole batch.

Per-bet flow (mirrors scenario #1 selectors, iterated):
    1. Pre-clear slip.
    2. Navigate to the bet's match URL directly. NEVER reuse the
       coupon page across iterations.
    3. Scroll until the `.market-box-root[class*="TOTAL_GOALS_OVER"]`
       container appears, expand its accordion if collapsed (toggle
       button: `event-page-market-box-collapseBtn:has-text("Total
       Goals Over/Under")`).
    4. Click the outcome scoped by `.market-box-root[class*="TOTAL_
       GOALS_OVER"]:has(.oddLine:has-text("2.5")) button[name="Over"]`.
    5. Verify `outcome-box-root.selected` count == 1 in the box.
    6. Verify Betslip counter == `(1)`.
    7. Fill stake input via `[class*="stake" i] input`; read back
       with `input_value()` and strip the currency symbol.
    8. Click Place Bet through the dedicated `_execute_place_bet`
       method (bypasses FORBIDDEN_CLICK_LABELS — that's the point).
    9. Wait for `SLIP_EMPTY_INDICATORS` (counter back to `(0)` or
       `.empty-message-betslipEmpty` visible). 15 s timeout per bet.
    10. Read balance, append per-bet audit record, randomised
        `BETWEEN_BETS_PAUSE_S` (4-8 s) before the next iteration.

On partial-batch failure (e.g., bet 2 of 2 fails): bet 1 stays
committed at Pamestoixima. The script does NOT attempt rollback.
The audit JSON records per-bet success so the operator can
reconcile manually.

Anti-bot:
    - Single session for the whole batch (no re-login per iteration).
    - Headed mode forced; `--headless` is not exposed.
    - Randomised 800-2500 ms `human_pause()` between any two
      browser actions (existing infrastructure from session.py),
      plus a longer randomised between-bets pause.

Run:
    python -m real_betting dry-run-batch-placement
"""

from __future__ import annotations

import datetime
import json
import os
import random
import sys
import time
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import config
from .bookmakers.pamestoixima import Pamestoixima
from .discover_fixtures import find_fixture_url
from .session import session_lock


# --- bet specs (match_url is optional; falls back to discoverer lookup) -----
#
# Each bet entry needs `home`, `away`, `market`, `selection`,
# `odds_at_plan`, `stake_eur`. The `match_url` field is now OPTIONAL:
#
# - When `match_url` is set (a literal Pamestoixima fixture URL),
#   the script uses it directly. Faster, deterministic, immune to
#   discoverer coverage gaps. Pattern (per PAMESTOIXIMA_NOTES.md):
#     /en/football/<league-slug>/<home>-v-<away>/<match-id>
#
# - When `match_url` is None (or absent), the script calls
#   `find_fixture_url(home, away)` against the latest
#   `output/real_betting/fixtures_<today>.json` snapshot produced by
#   `python -m real_betting discover-fixtures`. Requires the
#   discoverer to have been run recently. Fuzzy team-name match via
#   rapidfuzz token_set_ratio with a min_score of 80.
#
# Recommended workflow: leave `match_url` as None for fixtures that
# are inside the discoverer's 24h window; hardcode it for matches
# further out (where the discoverer doesn't yet cover them) or as
# a deterministic override.

BETS = [
    # Paderborn vs Wolfsburg — PLACED 2026-05-25 in run
    # batch_placement_20260525-141135 (€2.00 @ 1.94, Bet 1/2 of the
    # original batch). Commented out for the re-run that places bet 2
    # so we don't accidentally re-place it.
    # {
    #     'home': 'Paderborn',
    #     'away': 'Wolfsburg',
    #     'market': 'O/U 2.5',
    #     'selection': 'Over',
    #     'odds_at_plan': 1.98,
    #     'stake_eur': 2.00,
    #     'match_url': 'https://www.pamestoixima.gr/en/football/bundesliga/sc-paderborn-07-v-vfl-wolfsburg/11012505',
    # },
    {
        'home': 'Sandefjord',
        'away': 'Fredrikstad',
        'market': 'O/U 2.5',
        'selection': 'Over',
        'odds_at_plan': 1.70,
        'stake_eur': 2.00,
        'match_url': 'https://www.pamestoixima.gr/en/football/eliteserien/sandefjord-fotball-v-fredrikstad-fk/10595954',
    },
]


# --- safety constants (immutable) --------------------------------------------

EXECUTE_PLACE_BETS = False
MAX_BETS_PER_RUN = 2
MAX_STAKE_PER_BET_EUR = 2.00
MAX_TOTAL_STAKE_EUR = 4.00

# Per-bet timings.
SLIP_CLEAR_WAIT_MS = 5000
PLACE_WAIT_MS = 15000
BETWEEN_BETS_PAUSE_S = (4.0, 8.0)  # uniform random in [lo, hi]

# Aborts the batch if the odds at click-time differ from odds_at_plan
# by more than ODDS_DRIFT_MAX_PCT * odds_at_plan. 5% is generous enough
# to tolerate normal pre-kickoff movement but tight enough to refuse a
# fundamentally different price.
ODDS_DRIFT_MAX_PCT = 0.05


# --- module-load assertions --------------------------------------------------

assert len(BETS) <= MAX_BETS_PER_RUN, (
    f"DRY-RUN safety: BETS has {len(BETS)} entries but MAX_BETS_PER_RUN "
    f"= {MAX_BETS_PER_RUN}. Refusing to load."
)

_total_stake = sum(b['stake_eur'] for b in BETS)
assert _total_stake <= MAX_TOTAL_STAKE_EUR, (
    f"DRY-RUN safety: sum of stakes = {_total_stake} > MAX_TOTAL_STAKE_EUR "
    f"= {MAX_TOTAL_STAKE_EUR}. Refusing to load."
)

for _i, _b in enumerate(BETS):
    assert _b['stake_eur'] <= MAX_STAKE_PER_BET_EUR, (
        f"DRY-RUN safety: BETS[{_i}] stake_eur = {_b['stake_eur']} > "
        f"MAX_STAKE_PER_BET_EUR = {MAX_STAKE_PER_BET_EUR}. Refusing to load."
    )

# Forbidden labels — defence-in-depth against any selector that ends up
# clicking the Place Bet button outside the dedicated `_execute_place_bet`
# call site. Inherited from scenario #1.
FORBIDDEN_CLICK_LABELS = (
    'place bet', 'place wager', 'submit bet', 'submit wager',
    'confirm bet', 'τοποθέτηση', 'τοποθετηση',
)


# --- selectors (lifted verbatim from dryrun_freiburg_villa.py) --------------

OU_MARKET_BOX_SELECTORS = (
    '.market-box-root[class*="TOTAL_GOALS_OVER"]',
    '[class*="TOTAL_GOALS_OVER"]',
)

# Over 2.5 scoped to its market box. Each O/U line (0.5, 1.5, 2.5, 3.5)
# is a separate sibling box — filter to the box whose oddLine contains
# "2.5" before picking the button.
OVER_25_SELECTORS = (
    '.market-box-root[class*="TOTAL_GOALS_OVER"]:has(.oddLine:has-text("2.5")) '
    'button[name="Over"]',
    '.market-box-root[class*="TOTAL_GOALS_OVER"] button[name="Over"]'
    ':has(.oddLine:has-text("2.5"))',
    '.market-box-root[class*="TOTAL_GOALS_OVER"] button.outcome-box-root'
    ':has(.name:has-text("Over")):has(.oddLine:has-text("2.5"))',
)

STAKE_INPUT_SELECTORS = (
    'input[name*="stake" i]',
    'input[name*="amount" i]',
    'input[placeholder*="stake" i]',
    'input[placeholder*="ποσό" i]',
    'input[type="number"]',
    '[class*="stake" i] input',
)

PLACE_BET_SELECTORS = (
    'button:has-text("Place Bet"):not([disabled])',
    'button:has-text("PLACE BET"):not([disabled])',
    'button:has-text("Place bet"):not([disabled])',
    'button:has-text("Στοιχημάτισε"):not([disabled])',
    'button:has-text("ΣΤΟΙΧΗΜΑΤΙΣΕ"):not([disabled])',
    'button:has-text("Τοποθέτηση"):not([disabled])',
    'button[class*="placeBet" i]:not([disabled])',
    'button[class*="submit-bet" i]:not([disabled])',
    'button[class*="confirm-bet" i]:not([disabled])',
)

SLIP_EMPTY_INDICATORS = (
    # PRIMARY success signal — observed 2026-05-25 (run
    # batch_placement_20260525-141135): on a successful Place Bet,
    # Pamestoixima keeps the betslip alive and renders a placement
    # receipt header. The receipt-overlay class is stable; the slip
    # counter does NOT drop to (0) post-success because the receipt
    # consumes the slip surface.
    '[class*="placementNotification" i]',
    '.slip-receipt-header-placementNotification',
    # Legacy fallbacks (kept for compatibility with other layouts —
    # match the older test_case_scenarios.md spec). Will likely never
    # match on the current site but won't hurt.
    '.slip-button-root:has(span:has-text("Betslip")) span:text-is("(0)")',
    '.empty-message-betslipEmpty',
    'body2:has-text("Your betslip is empty")',
)

SLIP_CLEAR_SELECTORS = (
    'button:has-text("Clear all")',
    'button:has-text("Remove all")',
    'button:has-text("Καθαρισμός")',
    'button:has-text("Διαγραφή")',
    'button[aria-label*="remove" i]',
    'button[aria-label*="clear" i]',
    '[class*="betslip" i] button:has-text("X")',
    '[class*="betslip" i] [class*="close" i]',
)


# --- runner ------------------------------------------------------------------

class BatchPlacement:
    """Walks Pamestoixima through N independent slip placements,
    stopping on the first failure."""

    def __init__(self, pm: Pamestoixima):
        self.pm = pm
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.dryrun_dir = os.path.join(
            config.OUTPUT_DIR, f'batch_placement_{ts}'
        )
        os.makedirs(self.dryrun_dir, exist_ok=True)
        self._refused_clicks = 0
        self.records: list[dict] = []
        self.cumulative_stake = 0.0

    @property
    def page(self):
        return self.pm._session.page

    # -- IO helpers --------------------------------------------------------

    def _shot(self, label: str) -> str:
        path = os.path.join(self.dryrun_dir, f'{label}.png')
        try:
            self.page.screenshot(path=path, full_page=True)
        except Exception as e:
            print(f"[batch] screenshot {label} failed: {e}")
        return path

    def _dump(self, label: str) -> None:
        base = os.path.join(self.dryrun_dir, label)
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
        print(f"[batch] dump → {base}.{{png,html,url}}")

    # -- click guards ------------------------------------------------------

    def _safe_click_first(self, selectors, timeout_ms: int = 5000,
                          step_label: str = 'click') -> bool:
        """Click the first visible match, refusing if its text matches
        any FORBIDDEN_CLICK_LABELS substring."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                try:
                    text = (loc.inner_text(timeout=500) or '').lower()
                except Exception:
                    text = ''
                for forbidden in FORBIDDEN_CLICK_LABELS:
                    if forbidden in text:
                        self._refused_clicks += 1
                        print(f"[batch] REFUSED click on '{sel}' — text "
                              f"matched forbidden '{forbidden}'.")
                        self._dump(f'{step_label}_refused')
                        return False
                loc.click()
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception as e:
                print(f"[batch] {step_label} via '{sel}' raised: {e!r}")
                continue
        return False

    def _safe_fill_first(self, selectors, value: str,
                         timeout_ms: int = 5000) -> bool:
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                loc.fill(value)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    def _any_visible(self, selectors, timeout_ms: int = 5000) -> bool:
        for sel in selectors:
            try:
                self.page.locator(sel).first.wait_for(
                    state='visible', timeout=timeout_ms)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    # -- per-bet operations ------------------------------------------------

    def _resolve_match_url(self, bet: dict) -> tuple[Optional[str], str]:
        """Return (url, source) for a BETS entry.
          source == 'hardcoded' — the entry's `match_url` was set; use it.
          source == 'lookup'    — looked up via the discoverer snapshot
                                  (output/real_betting/fixtures_<today>.json).
          source == 'missing'   — neither hardcoded nor resolvable; caller
                                  must abort the batch.
        The hardcoded URL wins when present so the operator can deterministically
        override the snapshot (useful for out-of-window fixtures or A/B tests)."""
        hardcoded = bet.get('match_url')
        if hardcoded:
            return hardcoded, 'hardcoded'
        try:
            hit = find_fixture_url(bet['home'], bet['away'])
        except Exception as e:
            print(f"[batch] find_fixture_url raised: {e!r}")
            return None, 'missing'
        if hit and hit.get('fixture_url'):
            print(f"[batch]   lookup hit: {bet['home']!r} vs {bet['away']!r} "
                  f"→ {hit['home']!r} vs {hit['away']!r} "
                  f"(score={hit.get('_match_score')})")
            return hit['fixture_url'], 'lookup'
        return None, 'missing'

    def _read_slip_counter(self) -> Optional[str]:
        """Read the Betslip counter text (e.g. '(0)' or '(1)'). None on
        failure — treat as 'don't know' and force a defensive clear."""
        try:
            loc = self.page.locator(
                '.slip-button-root:has(span:has-text("Betslip")) '
                'span:has-text("(")'
            ).first
            return (loc.inner_text(timeout=1500) or '').strip()
        except Exception:
            return None

    def _pre_iteration_clear_slip(self, step_label: str) -> None:
        """Defensive clear before each new selection. Idempotent — if
        the slip is already empty the clear-button selectors won't match
        and we just move on."""
        self._safe_click_first(SLIP_CLEAR_SELECTORS, timeout_ms=2000,
                                step_label=step_label)
        self.pm._session.human_pause()

    def _execute_place_bet(self) -> bool:
        """Click the Place Bet button. DELIBERATELY bypasses the
        FORBIDDEN_CLICK_LABELS guard — every call is a real-money
        click, loudly logged."""
        print()
        print("!" * 72)
        print("!!! EXECUTING REAL PLACEMENT (one bet of the batch) !!!")
        print("!" * 72)
        print()
        time.sleep(2)
        self._shot(f'iter_{len(self.records):02d}_07a_about_to_place')
        for sel in PLACE_BET_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=3000)
                aria_disabled = (loc.get_attribute('aria-disabled') or '').lower()
                if aria_disabled == 'true':
                    print(f"[batch] Skipping '{sel}' — aria-disabled.")
                    continue
                print(f"[batch] !!! Clicking Place Bet via '{sel}' !!!")
                loc.click()
                self._shot(f'iter_{len(self.records):02d}_07b_after_click')
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception as e:
                print(f"[batch] Place Bet '{sel}' raised: {e!r}")
                continue
        print("[batch] !!! No Place Bet button matched. !!!")
        return False

    # -- the actual flow ----------------------------------------------------

    def run(self) -> bool:
        """Walk through every entry in BETS. Stop on first failure;
        leave already-placed bets intact at Pamestoixima."""
        print(f"[batch] Output dir: {self.dryrun_dir}")
        print(f"[batch] Bets queued: {len(BETS)}, total stake "
              f"€{self.cumulative_stake_planned():.2f}")

        # Up-front URL resolution — for each bet, either use the
        # hardcoded match_url or look it up via the discoverer snapshot.
        # Resolve everything BEFORE any browser work so failures surface
        # as a coherent batch validation result, not mid-flight.
        self._resolved_urls: dict[int, tuple[str, str]] = {}
        for i, b in enumerate(BETS):
            url, source = self._resolve_match_url(b)
            if not url:
                print(f"[batch] ABORT: BETS[{i}] ({b['home']} vs "
                      f"{b['away']}) — no match_url set AND no match found "
                      f"in the latest fixtures snapshot. Either:")
                print(f"[batch]   (a) set 'match_url' explicitly on this "
                      f"BETS entry, or")
                print(f"[batch]   (b) run `python -m real_betting "
                      f"discover-fixtures` first so the snapshot covers "
                      f"this fixture (only works for in-window fixtures, "
                      f"currently ~24h rolling).")
                return False
            self._resolved_urls[i] = (url, source)
            print(f"[batch] BET {i} URL ({source}): {url}")

        self._shot('00_start')

        for i, bet in enumerate(BETS):
            print()
            print("=" * 72)
            print(f"[batch] BET {i+1}/{len(BETS)}: "
                  f"{bet['home']} vs {bet['away']} — {bet['market']} "
                  f"{bet['selection']} @ {bet['odds_at_plan']} — "
                  f"€{bet['stake_eur']:.2f}")
            print("=" * 72)

            ok = self._place_one(i, bet)
            if not ok:
                print(f"\n[batch] BET {i+1} FAILED. Halting batch — "
                      f"bets 1..{i} (if any) remain committed at "
                      f"Pamestoixima. See per-iter audit records.")
                self._write_audit(success=False, halted_at=i)
                return False

            self.cumulative_stake += bet['stake_eur']

            # Between-bet pause (skip after the last bet).
            if i < len(BETS) - 1:
                pause = random.uniform(*BETWEEN_BETS_PAUSE_S)
                print(f"\n[batch] Between-bet pause: {pause:.1f}s")
                time.sleep(pause)

        print(f"\n[batch] All {len(BETS)} bets placed.")
        self._write_audit(success=True, halted_at=None)
        return True

    def cumulative_stake_planned(self) -> float:
        return sum(b['stake_eur'] for b in BETS)

    def _place_one(self, i: int, bet: dict) -> bool:
        """One iteration of the per-bet flow. Returns True on success.
        Appends an audit record either way (with `success` field)."""
        record = {
            'index': i,
            'match': f"{bet['home']} vs {bet['away']}",
            'market': bet['market'],
            'selection': bet['selection'],
            'odds_at_plan': bet['odds_at_plan'],
            'odds_at_select': None,
            'odds_at_place': None,
            'stake_eur': bet['stake_eur'],
            'balance_before': None,
            'balance_after': None,
            'slip_cleared': False,
            'success': False,
            'failure_reason': None,
            # Filled in below from self._resolved_urls — preserves
            # which path (hardcoded vs lookup) supplied the URL.
            'match_url': None,
            'match_url_source': None,
        }
        resolved = self._resolved_urls.get(i)
        if resolved:
            record['match_url'], record['match_url_source'] = resolved

        # Step 1: pre-iteration slip-empty check + defensive clear.
        self._pre_iteration_clear_slip(step_label=f'iter_{i:02d}_pre_clear')
        counter = self._read_slip_counter()
        if counter and counter not in ('(0)', '', None):
            record['failure_reason'] = (
                f"Pre-iter slip counter != (0): {counter!r}. Refusing "
                f"to add another selection (silent-parlay guard).")
            print(f"[batch] {record['failure_reason']}")
            self._dump(f'iter_{i:02d}_pre_iter_slip_dirty')
            self.records.append(record)
            return False

        # Step 2: pre-iteration balance check (best-effort; treat
        # unreadable balance as "fall back to locally tracked budget").
        balance_before = self.pm.get_balance()
        record['balance_before'] = balance_before
        remaining_budget = MAX_TOTAL_STAKE_EUR - self.cumulative_stake
        if balance_before is not None and balance_before < bet['stake_eur']:
            record['failure_reason'] = (
                f"Balance €{balance_before} < next stake €{bet['stake_eur']}.")
            print(f"[batch] {record['failure_reason']}")
            self.records.append(record)
            return False
        if remaining_budget < bet['stake_eur'] - 1e-6:
            record['failure_reason'] = (
                f"Local budget €{remaining_budget:.2f} < next stake "
                f"€{bet['stake_eur']} — MAX_TOTAL_STAKE_EUR would be "
                f"exceeded.")
            print(f"[batch] {record['failure_reason']}")
            self.records.append(record)
            return False

        # Step 3: navigate. The URL was resolved up-front in run() and
        # cached on self._resolved_urls (either hardcoded `match_url`
        # on the bet, or looked up via find_fixture_url from the
        # discoverer snapshot).
        match_url, url_source = self._resolved_urls[i]
        print(f"\n[batch] Navigating to {match_url}  (source: {url_source})")
        try:
            self.page.goto(match_url)
        except Exception as e:
            record['failure_reason'] = f"goto raised: {e!r}"
            print(f"[batch] {record['failure_reason']}")
            self._dump(f'iter_{i:02d}_goto_failed')
            self.records.append(record)
            return False
        self.pm._session.human_pause()
        try:
            self.page.wait_for_load_state('networkidle', timeout=15000)
        except PlaywrightTimeoutError:
            pass
        self.pm._dismiss_overlays()
        self._shot(f'iter_{i:02d}_01_on_match_page')

        # Confirm we're on the right page via team-text lookup.
        try:
            home_n = self.page.get_by_text(bet['home'], exact=False).count()
            away_n = self.page.get_by_text(bet['away'], exact=False).count()
            if home_n == 0 or away_n == 0:
                record['failure_reason'] = (
                    f"Match page missing team names: home_n={home_n}, "
                    f"away_n={away_n}. URL may be stale.")
                print(f"[batch] {record['failure_reason']}")
                self._dump(f'iter_{i:02d}_match_url_stale')
                self.records.append(record)
                return False
        except Exception as e:
            print(f"[batch] match-page verify raised: {e!r} — continuing.")

        # Step 4: scroll until the O/U market box appears.
        if not self._reveal_ou_market(i):
            record['failure_reason'] = 'O/U market box not found on page.'
            self.records.append(record)
            return False

        # Step 5: expand accordion if collapsed.
        if not self._expand_ou_accordion(i):
            record['failure_reason'] = (
                'O/U accordion failed to expand within timeout.')
            self.records.append(record)
            return False

        # Step 6: capture odds at select-time, refuse if drift > cap.
        odds_at_select = self._read_over_25_odds()
        record['odds_at_select'] = odds_at_select
        if odds_at_select is None:
            print(f"[batch] Could not read Over 2.5 odds before "
                  f"clicking; continuing without drift check.")
        else:
            drift_pct = abs(odds_at_select - bet['odds_at_plan']) \
                / bet['odds_at_plan']
            if drift_pct > ODDS_DRIFT_MAX_PCT:
                record['failure_reason'] = (
                    f"Odds drift: {odds_at_select} vs plan "
                    f"{bet['odds_at_plan']} = {drift_pct*100:.1f}% "
                    f"> {ODDS_DRIFT_MAX_PCT*100:.0f}%.")
                print(f"[batch] {record['failure_reason']}")
                self._dump(f'iter_{i:02d}_odds_drift')
                self.records.append(record)
                return False
            print(f"[batch] Odds drift OK: {odds_at_select} vs plan "
                  f"{bet['odds_at_plan']} = {drift_pct*100:.2f}%")

        # Step 7: click Over 2.5.
        if not self._safe_click_first(OVER_25_SELECTORS, timeout_ms=8000,
                                       step_label=f'iter_{i:02d}_click_over_25'):
            record['failure_reason'] = 'Over 2.5 selector did not match.'
            self._dump(f'iter_{i:02d}_over25_not_found')
            self.records.append(record)
            return False
        self.pm._session.human_pause()
        self._shot(f'iter_{i:02d}_04_after_over25_click')

        # Step 8: post-click sanity — selected count + slip counter.
        try:
            sel_count = self.page.locator(
                '.market-box-root[class*="TOTAL_GOALS_OVER"] '
                'button.outcome-box-root.selected'
            ).count()
            if sel_count == 0:
                record['failure_reason'] = (
                    'Click did not register — no outcome-box-root.selected.')
                print(f"[batch] {record['failure_reason']}")
                self._dump(f'iter_{i:02d}_click_did_not_register')
                self.records.append(record)
                return False
        except Exception:
            pass

        slip_counter = self._read_slip_counter()
        if slip_counter in ('(0)', '', None):
            record['failure_reason'] = (
                f"Slip counter unchanged after click: {slip_counter!r}.")
            self._dump(f'iter_{i:02d}_slip_unchanged')
            self.records.append(record)
            return False
        if slip_counter not in ('(1)',):
            record['failure_reason'] = (
                f"Slip counter unexpected after click: {slip_counter!r}. "
                f"Refusing to fire a multi/parlay.")
            self._dump(f'iter_{i:02d}_slip_multi')
            self.records.append(record)
            return False
        print(f"[batch] Slip counter: {slip_counter} (good — single selection)")

        # Step 9: stake.
        if not self._safe_fill_first(STAKE_INPUT_SELECTORS,
                                      f"{bet['stake_eur']:.2f}",
                                      timeout_ms=5000):
            record['failure_reason'] = 'Stake input not found.'
            self._dump(f'iter_{i:02d}_stake_input_not_found')
            self.records.append(record)
            return False
        self.pm._session.human_pause()
        self._shot(f'iter_{i:02d}_06_stake_entered')

        # Read back the stake input value. Pamestoixima sometimes
        # renders the stake as "2,00€" or "2€"; strip non-numerics
        # before float-parsing.
        stake_value = ''
        for sel in STAKE_INPUT_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible(timeout=500):
                    v = (loc.input_value(timeout=1000) or '').strip()
                    if v:
                        stake_value = v
                        break
            except Exception:
                continue
        import re as _re
        m = _re.search(r'[\d.,]+', stake_value)
        normalised = (m.group(0).replace(',', '.') if m else '').strip()
        try:
            stake_read = float(normalised)
        except ValueError:
            stake_read = None
        if stake_read is None or abs(stake_read - bet['stake_eur']) > 0.01:
            record['failure_reason'] = (
                f"Stake read-back {stake_value!r} != €{bet['stake_eur']}.")
            print(f"[batch] {record['failure_reason']}")
            self._dump(f'iter_{i:02d}_stake_verify_failed')
            self.records.append(record)
            return False

        # Step 10: place (gated by EXECUTE_PLACE_BETS).
        if not EXECUTE_PLACE_BETS:
            # Dry-mode: clear the slip and report success-without-placement.
            print("[batch] EXECUTE_PLACE_BETS=False — clearing slip without "
                  "placing.")
            self._safe_click_first(SLIP_CLEAR_SELECTORS, timeout_ms=4000,
                                    step_label=f'iter_{i:02d}_clear_dry_mode')
            self.pm._session.human_pause()
            self._shot(f'iter_{i:02d}_08_dry_mode_cleared')
            record['failure_reason'] = '(dry-mode — no placement attempted)'
            record['success'] = True  # the plumbing reached the gate
            self.records.append(record)
            return True

        # Re-read odds one more time before the click — final drift check.
        odds_at_place = self._read_over_25_odds()
        record['odds_at_place'] = odds_at_place
        if odds_at_place is not None:
            drift_pct = abs(odds_at_place - bet['odds_at_plan']) \
                / bet['odds_at_plan']
            if drift_pct > ODDS_DRIFT_MAX_PCT:
                record['failure_reason'] = (
                    f"Odds drift at place: {odds_at_place} vs plan "
                    f"{bet['odds_at_plan']} = {drift_pct*100:.1f}%.")
                print(f"[batch] {record['failure_reason']}")
                self._dump(f'iter_{i:02d}_odds_drift_at_place')
                self.records.append(record)
                return False

        if not self._execute_place_bet():
            record['failure_reason'] = 'Place Bet button not found / not clickable.'
            self._dump(f'iter_{i:02d}_placebet_no_target')
            self.records.append(record)
            return False

        # Step 11: wait for slip-empty (success signal).
        slip_cleared = False
        for indicator in SLIP_EMPTY_INDICATORS:
            try:
                self.page.locator(indicator).first.wait_for(
                    state='visible', timeout=PLACE_WAIT_MS)
                slip_cleared = True
                print(f"[batch] Slip cleared (matched: '{indicator}').")
                break
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        record['slip_cleared'] = slip_cleared
        self._shot(f'iter_{i:02d}_09_after_settle')

        if not slip_cleared:
            record['failure_reason'] = (
                f"Slip did NOT clear within {PLACE_WAIT_MS}ms. Bet status "
                f"uncertain — verify manually before re-running batch.")
            self._dump(f'iter_{i:02d}_slip_did_not_clear')
            self.records.append(record)
            return False

        # Step 12: read balance + record success.
        record['balance_after'] = self.pm.get_balance()
        if record['balance_before'] is not None and record['balance_after'] is not None:
            diff = round(record['balance_before'] - record['balance_after'], 2)
            print(f"[batch] Balance: €{record['balance_before']} → "
                  f"€{record['balance_after']} (Δ €{diff})")
            if abs(diff - bet['stake_eur']) > 0.50:
                print(f"[batch] !!! Balance Δ ({diff}) doesn't match stake "
                      f"({bet['stake_eur']}) within €0.50 — verify manually !!!")

        record['success'] = True
        self.records.append(record)
        return True

    # -- selector helpers --------------------------------------------------

    def _reveal_ou_market(self, i: int) -> bool:
        """Scroll the match page until the O/U market box is in the DOM."""
        try:
            vp = self.page.viewport_size
            self.page.mouse.move((vp['width'] or 1280) // 2,
                                  (vp['height'] or 800) // 2)
        except Exception:
            pass

        for _ in range(30):
            for sel in OU_MARKET_BOX_SELECTORS:
                try:
                    if self.page.locator(sel).count() > 0:
                        try:
                            self.page.locator(sel).first.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue
            # Triple-poke.
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

        # Last-resort: scroll every overflow container to bottom.
        try:
            self.page.evaluate("""
                () => {
                    const els = document.querySelectorAll('*');
                    for (const el of els) {
                        const s = getComputedStyle(el);
                        if (s.overflowY === 'auto' || s.overflowY === 'scroll') {
                            el.scrollTop = el.scrollHeight;
                        }
                    }
                }
            """)
            self.pm._session.human_pause()
        except Exception:
            pass
        for sel in OU_MARKET_BOX_SELECTORS:
            try:
                if self.page.locator(sel).count() > 0:
                    try:
                        self.page.locator(sel).first.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    return True
            except Exception:
                continue

        self._dump(f'iter_{i:02d}_03_ou_market_not_found')
        print(f"[batch] O/U market box not found.")
        return False

    def _expand_ou_accordion(self, i: int) -> bool:
        """Expand the Total Goals Over/Under accordion if collapsed."""
        collapse_loc = self.page.locator(
            '.MuiCollapse-root:has(.market-box-root[class*="TOTAL_GOALS_OVER"])'
        ).first
        try:
            collapse_classes = collapse_loc.get_attribute('class', timeout=2000) or ''
        except Exception:
            collapse_classes = ''
        if 'MuiCollapse-hidden' not in collapse_classes:
            return True  # already expanded

        toggle = self.page.locator(
            'button.event-page-market-box-collapseBtn'
            ':has-text("Total Goals Over/Under")'
        ).first
        try:
            toggle.scroll_into_view_if_needed(timeout=5000)
            self.pm._session.human_pause()
            toggle.click()
        except Exception as e:
            print(f"[batch] Accordion toggle click failed: {e!r}")
            self._dump(f'iter_{i:02d}_toggle_click_failed')
            return False

        try:
            self.page.locator(
                '.MuiCollapse-entered:has(.market-box-root[class*="TOTAL_GOALS_OVER"])'
            ).first.wait_for(state='visible', timeout=5000)
        except PlaywrightTimeoutError:
            print(f"[batch] Accordion did not expand within timeout.")
            self._dump(f'iter_{i:02d}_expand_timeout')
            return False

        self.pm._session.human_pause()
        return True

    def _read_over_25_odds(self) -> Optional[float]:
        """Read the displayed price for Over 2.5 from the matched
        market box. Best-effort — returns None if it can't parse."""
        try:
            loc = self.page.locator(
                '.market-box-root[class*="TOTAL_GOALS_OVER"]'
                ':has(.oddLine:has-text("2.5")) button[name="Over"] .price'
            ).first
            text = (loc.inner_text(timeout=1500) or '').strip()
            return float(text.replace(',', '.'))
        except Exception:
            return None

    # -- audit -------------------------------------------------------------

    def _write_audit(self, success: bool, halted_at: Optional[int]) -> None:
        path = os.path.join(self.dryrun_dir, 'batch_placement_record.json')
        try:
            with open(path, 'w') as f:
                json.dump({
                    'timestamp': datetime.datetime.now().isoformat(),
                    'success': success,
                    'execute_place_bets': EXECUTE_PLACE_BETS,
                    'halted_at_index': halted_at,
                    'total_bets_planned': len(BETS),
                    'total_bets_placed': sum(1 for r in self.records if r['success']),
                    'total_stake_committed_eur': round(self.cumulative_stake, 2),
                    'records': self.records,
                    'refused_clicks': self._refused_clicks,
                    'dryrun_dir': self.dryrun_dir,
                }, f, indent=2)
            print(f"[batch] Audit record → {path}")
        except Exception as e:
            print(f"[batch] Could not write audit record: {e}")

        summary_path = os.path.join(self.dryrun_dir, 'batch_summary.txt')
        try:
            with open(summary_path, 'w') as f:
                f.write(f"Batch placement summary — {datetime.datetime.now().isoformat()}\n")
                f.write(f"  EXECUTE_PLACE_BETS: {EXECUTE_PLACE_BETS}\n")
                f.write(f"  Planned: {len(BETS)} bet(s), "
                        f"€{self.cumulative_stake_planned():.2f} total\n")
                f.write(f"  Placed:  {sum(1 for r in self.records if r['success'])} bet(s), "
                        f"€{self.cumulative_stake:.2f} total\n")
                if halted_at is not None:
                    f.write(f"  Halted at index {halted_at}.\n")
                f.write("\nPer-bet:\n")
                for r in self.records:
                    f.write(f"  [{r['index']}] {r['match']} — {r['market']} {r['selection']} "
                            f"@ {r['odds_at_plan']} — €{r['stake_eur']} — "
                            f"{'OK' if r['success'] else 'FAIL'}")
                    if not r['success']:
                        f.write(f"\n        reason: {r['failure_reason']}")
                    f.write("\n")
            print(f"[batch] Summary → {summary_path}")
        except Exception:
            pass


# --- CLI ---------------------------------------------------------------------

def cmd_dry_run_batch_placement(args) -> int:
    """CLI entrypoint. Headed mode forced; supervised one-shot test."""
    print(f"[batch] Headed mode forced (supervised one-shot batch test).")
    print(f"[batch] EXECUTE_PLACE_BETS = {EXECUTE_PLACE_BETS}")
    print(f"[batch] Planned: {len(BETS)} bet(s), "
          f"€{sum(b['stake_eur'] for b in BETS):.2f} total")
    print()

    # Up-front sanity: report which bets will use hardcoded URLs vs
    # the discoverer snapshot. The actual hard validation (URL set OR
    # lookup hits) happens inside BatchPlacement.run() so we can also
    # report login state in the same flow.
    needs_lookup = [i for i, b in enumerate(BETS) if not b.get('match_url')]
    if needs_lookup:
        print(f"[batch] {len(needs_lookup)} bet(s) without hardcoded match_url; "
              f"will look up via find_fixture_url() from the latest snapshot. "
              f"If the snapshot is stale or missing the fixture, run "
              f"`python -m real_betting discover-fixtures` first.")

    try:
        with session_lock():
            pm = Pamestoixima(headless=False, reuse_session=True)
            try:
                if not pm.login():
                    print(f"[batch] Login failed — aborting before any bet work.")
                    return 1
                runner = BatchPlacement(pm)
                ok = runner.run()
                return 0 if ok else 1
            finally:
                pm.close()
    except RuntimeError as e:
        print(f"[batch] Error: {e}", file=sys.stderr)
        return 1
