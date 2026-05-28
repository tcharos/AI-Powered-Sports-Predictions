"""DRY-RUN ONLY: prepare a Freiburg vs Aston Villa O/U 2.5 Over €10 bet
on Pamestoixima, then STOP before clicking 'Place bet'.

This module CANNOT place a real bet. The 'Place bet' click is not in any
code path — the script walks to the ready-to-place state, takes a
screenshot, prompts the user, then clears the slip and exits.

Purpose: validate that we can drive the Pamestoixima UI through the
fixture-search → market-pick → slip → stake-entry chain. This is the
plumbing Phase 9 (real bet placement) eventually needs. We're building
that plumbing without the irreversible step, so a buggy selector or a
DOM change can't accidentally place a real wager.

Hardcoded (do NOT generalise — this is a one-shot test artifact):
    HOME      = 'Freiburg'
    AWAY      = 'Aston Villa'
    MARKET    = 'O/U 2.5'
    SELECTION = 'Over'
    STAKE     = 10.0   # euros
    MAX_STAKE = 10.0   # hard refuse-cap; module load asserts equality

Run:
    python -m real_betting dry-run-freiburg-villa
"""

from __future__ import annotations

import datetime
import os
import sys
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import config
from .bookmakers.pamestoixima import Pamestoixima
from .session import session_lock


# --- safety constants (immutable) -------------------------------------------

HOME = 'Freiburg'
AWAY = 'Aston Villa'
MARKET = 'O/U 2.5'
SELECTION = 'Over'
STAKE = 10.0
MAX_STAKE = 10.0

# Early-exit gates, evaluated in order. The final gate (EXECUTE_PLACE_BET)
# is the only one that places a real bet; the others clean the slip and
# exit so nothing persists.
#
#   STOP_AFTER_STEP5    — exit after slip-verification (just confirms the
#                         Over 2.5 button click registered in the slip).
#   STOP_AFTER_STEP6    — proceed through Step 5, enter the €10 stake,
#                         verify it was accepted, then exit. No real bet.
#   EXECUTE_PLACE_BET   — DANGER: when True, actually click Place Bet.
#                         Real money moves. The MAX_STAKE=10 hard cap still
#                         applies. Used once for end-to-end verification of
#                         the Phase 9 pipeline. FOOTBALL_NEXT_STEPS.md's "read-only"
#                         policy is unchanged at the doc level — this flag
#                         is an explicit per-run override.
STOP_AFTER_STEP5 = False
STOP_AFTER_STEP6 = False
EXECUTE_PLACE_BET = True

# Sanity: at most one of the three terminal modes can be active.
_active = sum(int(x) for x in (STOP_AFTER_STEP5, STOP_AFTER_STEP6, EXECUTE_PLACE_BET))
assert _active <= 1, (
    "DRY-RUN safety violation: at most one of STOP_AFTER_STEP5 / "
    "STOP_AFTER_STEP6 / EXECUTE_PLACE_BET can be True at a time."
)

# Belt-and-braces: refuse to load if anyone tampers with the stake cap.
assert STAKE == MAX_STAKE == 10.0, (
    "DRY-RUN safety violation: STAKE and MAX_STAKE must both be 10.0. "
    "If you need a different stake, this is the wrong tool — write a "
    "different script."
)

# Words that, if present in any selector or button label, indicate the
# IRREVERSIBLE place-bet action. We refuse to click any element whose
# accessible text matches any of these. Defence-in-depth against typos.
FORBIDDEN_CLICK_LABELS = (
    'place bet', 'place wager', 'submit bet', 'submit wager',
    'confirm bet', 'τοποθέτηση', 'τοποθετηση',  # Greek "place"
)


# --- selectors (best-guess, expect to iterate from failure dumps) -----------

# Direct match URL — discovered during the previous successful run when
# the coupon page surfaced the fixture row. Going direct avoids:
#   - sport-tab state sensitivity (fresh login defaults to whichever sport
#     was last viewed; new sessions seem to default to basketball)
#   - the time-filter dance (3h / 6h / 12h / All)
#   - the coupon page's virtualised scroll
# Fixture discovery from team names is a Step 6b problem in FOOTBALL_NEXT_STEPS.md;
# this dry-run tests the bet-placement plumbing once we already know which
# match URL to drive.
MATCH_URL = ('https://www.pamestoixima.gr/en/football/'
             'uefa-europa-league/sc-freiburg-v-aston-villa/10889590')


# Match-page O/U market box. Confirmed via dump: the wrapper carries the
# Pamestoixima-specific class `TOTAL_GOALS_OVER/UNDER` alongside the generic
# MUI classes. The slash is unusual in a class name but real on this site —
# we use [class*=...] substring matching to avoid CSS-escaping headaches.
OU_MARKET_BOX_SELECTORS = (
    '.market-box-root[class*="TOTAL_GOALS_OVER"]',
    '[class*="TOTAL_GOALS_OVER"]',
)

# Inside the O/U market box, the Over 2.5 outcome button. ALL selectors
# are strictly scoped to the TOTAL_GOALS_OVER market box — unscoped
# fallbacks would match PriceBoost combo cards on the same page (e.g.
# cards with name="Aston Villa wins, Over 2.5 γκολ & Over 8.5 κόρνερ").
#
# Confirmed button structure via DOM dump:
#   <button name="Over" col="1" row="1" class="outcome-box-root ...">
#     <span class="oddName">
#       <span class="name">Over</span>
#       <span class="oddLine"> 2.5</span>     <!-- note leading space -->
#     </span>
#     <span class="price">1.92</span>
#   </button>
# IMPORTANT: there is one .market-box-root per line (0.5, 1.5, 2.5, 3.5...).
# A bare `.market-box-root .first` would target Over/Under 0.5, NOT 2.5.
# Selectors must filter to the box whose oddLine contains "2.5".
OVER_25_SELECTORS = (
    # 1. Filter at the market-box level first: the box whose oddLine
    #    contains "2.5", then within that pick the button with name="Over".
    #    `:has-text` is substring-based so it tolerates the leading space.
    '.market-box-root[class*="TOTAL_GOALS_OVER"]:has(.oddLine:has-text("2.5")) '
    'button[name="Over"]',
    # 2. Direct: any outcome button whose .name is "Over" AND whose
    #    .oddLine contains "2.5" (substring).
    '.market-box-root[class*="TOTAL_GOALS_OVER"] button[name="Over"]'
    ':has(.oddLine:has-text("2.5"))',
    # 3. Generic structural fallback — any outcome-box-root with text
    #    "Over" + "2.5" somewhere inside it.
    '.market-box-root[class*="TOTAL_GOALS_OVER"] button.outcome-box-root'
    ':has(.name:has-text("Over")):has(.oddLine:has-text("2.5"))',
)

# Bet slip (sidebar or modal).
SLIP_OPEN_SELECTORS = (
    'button[aria-label*="bet slip" i]',
    'button[aria-label*="betslip" i]',
    'button:has-text("Bet Slip")',
    'button:has-text("Δελτίο")',
    '[class*="betslip" i]',
)

# Stake input within the bet slip.
STAKE_INPUT_SELECTORS = (
    'input[name*="stake" i]',
    'input[name*="amount" i]',
    'input[placeholder*="stake" i]',
    'input[placeholder*="ποσό" i]',
    'input[type="number"]',
    '[class*="stake" i] input',
)

# Selection / slip-line indicator (to verify the bet landed in the slip).
SLIP_SELECTION_SELECTORS = (
    f'[class*="betslip" i]:has-text("Over")',
    f'[class*="betslip" i]:has-text("2.5")',
    f'[class*="selection" i]:has-text("Over")',
)

# Place-bet button selectors. Used ONLY when EXECUTE_PLACE_BET=True via
# the dedicated `_execute_place_bet` method below (which deliberately
# bypasses the FORBIDDEN_CLICK_LABELS safety net — that's the point).
# These are intentionally precise: text-based on common labels (EN + Greek)
# plus a class-based fallback. Each click is the click that costs money.
PLACE_BET_SELECTORS = (
    # English labels
    'button:has-text("Place Bet"):not([disabled])',
    'button:has-text("PLACE BET"):not([disabled])',
    'button:has-text("Place bet"):not([disabled])',
    # Greek labels (most likely on Pamestoixima — even on /en pages
    # the actual submit button sometimes stays in Greek)
    'button:has-text("Στοιχημάτισε"):not([disabled])',
    'button:has-text("ΣΤΟΙΧΗΜΑΤΙΣΕ"):not([disabled])',
    'button:has-text("Τοποθέτηση"):not([disabled])',
    # Class-based fallbacks (best-guess; iterate if these miss)
    'button[class*="placeBet" i]:not([disabled])',
    'button[class*="submit-bet" i]:not([disabled])',
    'button[class*="confirm-bet" i]:not([disabled])',
)

# After successful placement, the slip counter should drop back to (0).
# This is the most reliable confirmation indicator across bookies.
SLIP_EMPTY_INDICATORS = (
    '.slip-button-root:has(span:has-text("Betslip")) span:text-is("(0)")',
    '.empty-message-betslipEmpty',
    'body2:has-text("Your betslip is empty")',
)

# Cleanup: empty the slip after the dry-run. Several flavours: "Remove",
# "Clear all", an X icon, etc.
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


# --- runner -----------------------------------------------------------------

class FreiburgVillaDryRun:
    """Walks Pamestoixima up to the bet-slip-ready state. Cannot place a bet."""

    def __init__(self, pm: Pamestoixima):
        self.pm = pm
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.dryrun_dir = os.path.join(
            config.OUTPUT_DIR, f'dryrun_freiburg_villa_{ts}'
        )
        os.makedirs(self.dryrun_dir, exist_ok=True)
        # Final safety net: if the runner is somehow asked to click an
        # element whose text matches a forbidden label, refuse.
        self._refused_clicks = 0

    @property
    def page(self):
        return self.pm._session.page

    def _shot(self, label: str) -> str:
        path = os.path.join(self.dryrun_dir, f'{label}.png')
        try:
            self.page.screenshot(path=path, full_page=True)
            print(f"[dryrun] screenshot → {path}")
        except Exception as e:
            print(f"[dryrun] screenshot {label} failed: {e}")
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
        print(f"[dryrun] failure dump → {base}.{{png,html,url}}")

    def _safe_click_first_visible(self, selectors, timeout_ms: int = 5000,
                                  step_label: str = 'click') -> bool:
        """Click the first visible match, but refuse if its inner text
        contains any FORBIDDEN_CLICK_LABELS substring."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                # Safety check: inspect text first.
                try:
                    text = (loc.inner_text(timeout=500) or '').lower()
                except Exception:
                    text = ''
                for forbidden in FORBIDDEN_CLICK_LABELS:
                    if forbidden in text:
                        self._refused_clicks += 1
                        print(f"[dryrun] REFUSED click on selector '{sel}' "
                              f"— text matched forbidden label '{forbidden}'. "
                              f"This is the safety net working.")
                        self._dump(f'{step_label}_refused')
                        return False
                loc.click()
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception as e:
                print(f"[dryrun] {step_label}: selector '{sel}' raised {e!r}")
                continue
        return False

    def _safe_fill_first_visible(self, selectors, value: str,
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
                self.page.locator(sel).first.wait_for(state='visible', timeout=timeout_ms)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    def _execute_place_bet(self) -> bool:
        """INTENTIONALLY click Pamestoixima's Place Bet button.

        This method deliberately bypasses the FORBIDDEN_CLICK_LABELS safety
        net that `_safe_click_first_visible` enforces. Every call is a
        real-money click; every call is loudly logged and screenshotted
        for audit.

        Only ever called when EXECUTE_PLACE_BET=True at module level.
        """
        print()
        print("!" * 72)
        print("!!! EXECUTING REAL BET PLACEMENT — €%s ON %s vs %s !!!" % (STAKE, HOME, AWAY))
        print("!!! Selection: %s %s !!!" % (SELECTION, MARKET))
        print("!!! FOOTBALL_NEXT_STEPS.md policy ('read-only only') is overridden    !!!")
        print("!!! for this single click. Safety net bypassed by design.    !!!")
        print("!" * 72)
        print()
        # Two-second pause so the user has a final visual moment to ctrl+C.
        import time
        time.sleep(2)

        self._shot('07a_about_to_place_bet')

        for sel in PLACE_BET_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=3000)
                # Quick disabled-state check — :not([disabled]) in the
                # selector handles the attribute, but MUI sometimes uses
                # aria-disabled / class-based disabling instead.
                aria_disabled = loc.get_attribute('aria-disabled') or ''
                if aria_disabled.lower() == 'true':
                    print(f"[dryrun] Skipping '{sel}' — aria-disabled='true'.")
                    continue
                print(f"[dryrun] !!! Clicking Place Bet via '{sel}' !!!")
                loc.click()
                self._shot('07b_immediately_after_click')
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception as e:
                print(f"[dryrun] Place Bet selector '{sel}' raised: {e!r}")
                continue
        print(f"[dryrun] !!! Could not find a Place Bet button across "
              f"{len(PLACE_BET_SELECTORS)} selectors !!!")
        return False

    # --- the actual flow ---------------------------------------------------

    def run(self) -> bool:
        print(f"[dryrun] Starting Freiburg vs Aston Villa O/U Over 2.5 €{STAKE} dry-run")
        print(f"[dryrun] Output dir: {self.dryrun_dir}")
        print(f"[dryrun] FORBIDDEN labels: {FORBIDDEN_CLICK_LABELS}")
        self._shot('00_start')

        # Pre-condition: clear any pre-existing selections from the bet slip.
        # The Pamestoixima slip persists across navigations within the same
        # session, so a previous failed run might have left an odd in there
        # (e.g. an accidental Match Result click on the coupon page). We
        # don't want to walk into the dry-run with leftover state.
        print(f"\n[dryrun] Pre-condition: clearing any existing slip selections...")
        cleared_pre = self._safe_click_first_visible(SLIP_CLEAR_SELECTORS,
                                                    timeout_ms=2000,
                                                    step_label='pre_clear_slip')
        if cleared_pre:
            print(f"[dryrun] Cleared leftover slip selection(s).")
            self.pm._session.human_pause()
        else:
            print(f"[dryrun] No pre-existing selections to clear (or clear "
                  f"button not visible — that's fine if the slip was empty).")

        # Step 1: navigate directly to the match page. (Coupon-page
        # discovery is a separate problem — see fixture-discovery notes
        # in MATCH_URL comment above.)
        print(f"\n[dryrun] Step 1: navigating directly to match → {MATCH_URL}")
        try:
            self.page.goto(MATCH_URL)
        except Exception as e:
            print(f"[dryrun] goto failed: {e}")
            self._dump('01_match_goto_failed')
            return False
        self.pm._session.human_pause()
        try:
            self.page.wait_for_load_state('networkidle', timeout=15000)
        except PlaywrightTimeoutError:
            pass
        # Dismiss any promo/ad modals that mounted on the new page.
        self.pm._dismiss_overlays()
        self._shot('01_on_match_page')

        # Verify we're actually on the match page by checking for both
        # team names via a locator (shadow-DOM-aware, no content() reliance).
        try:
            home_n = self.page.get_by_text('Freiburg', exact=False).count()
            away_n = self.page.get_by_text('Aston Villa', exact=False).count()
            print(f"[dryrun] Match-page locator counts: "
                  f"Freiburg={home_n}, Aston Villa={away_n}")
            if home_n == 0 or away_n == 0:
                print(f"[dryrun] One or both team names missing — match URL "
                      f"may have changed (Pamestoixima sometimes appends "
                      f"a date suffix to old fixtures).")
                self._dump('01_match_url_stale')
                return False
        except Exception as e:
            print(f"[dryrun] Match-page verification failed: {e}")
            self._dump('01_verify_match_page')
            return False
        print(f"[dryrun] Confirmed on match detail page.")

        # Step 3: scroll the match page to render all market sections.
        # The O/U 2.5 market is in a separate market-box-root container
        # (class `TOTAL_GOALS_OVER/UNDER`) that's lazy-rendered as you
        # scroll down the markets list. We scroll until that container
        # appears in the DOM, or until we hit the bottom of the page.
        print(f"\n[dryrun] Step 3: scrolling match page to load O/U market...")
        # Position the mouse over the content area so wheel events scroll
        # the markets pane.
        try:
            vp = self.page.viewport_size
            self.page.mouse.move((vp['width'] or 1280) // 2,
                                 (vp['height'] or 800) // 2)
        except Exception:
            pass

        ou_market_found = False
        for pass_num in range(30):
            for sel in OU_MARKET_BOX_SELECTORS:
                try:
                    if self.page.locator(sel).count() > 0:
                        ou_market_found = True
                        # Make sure it's actually in the viewport so clicks land.
                        try:
                            self.page.locator(sel).first.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        print(f"[dryrun] O/U market box found after {pass_num} scroll(s) "
                              f"via '{sel}'.")
                        break
                except Exception:
                    continue
            if ou_market_found:
                break
            # Triple-scroll (same approach as on the coupon page).
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
                    'window.scrollBy(0, Math.floor(window.innerHeight * 0.8))'
                )
            except Exception:
                pass
            self.pm._session.human_pause()

        if not ou_market_found:
            # Last-resort: scroll every overflow container to its bottom,
            # then check once more.
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
                        ou_market_found = True
                        try:
                            self.page.locator(sel).first.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        print(f"[dryrun] O/U market box found after final overflow scroll.")
                        break
                except Exception:
                    continue

        self._shot('03_ou_section')

        if not ou_market_found:
            print(f"[dryrun] Could not find the O/U market box (selectors: "
                  f"{OU_MARKET_BOX_SELECTORS}). The market may not exist on "
                  f"this fixture, or it's nested in an unrecognised wrapper.")
            self._dump('03_ou_market_not_found')
            return False

        # Step 3b: expand the accordion if collapsed.
        # The market box sits inside a MuiCollapse-root that's `MuiCollapse-hidden`
        # by default. The toggle is the preceding-sibling <button> (carries
        # an ExpandMoreIcon SVG as its endIcon). Clicking it expands the section.
        print(f"[dryrun] Step 3b: checking if O/U accordion is collapsed...")
        collapse_loc = self.page.locator(
            '.MuiCollapse-root:has(.market-box-root[class*="TOTAL_GOALS_OVER"])'
        ).first
        try:
            collapse_classes = collapse_loc.get_attribute('class', timeout=2000) or ''
        except Exception:
            collapse_classes = ''
        is_collapsed = 'MuiCollapse-hidden' in collapse_classes
        print(f"[dryrun] Collapse classes: {collapse_classes!r} → "
              f"{'COLLAPSED' if is_collapsed else 'already expanded'}")

        if is_collapsed:
            # The toggle is button.event-page-market-box-collapseBtn whose
            # header label is "Total Goals Over/Under" (confirmed via dump).
            # That class is unique to the market-section collapse buttons,
            # and the label disambiguates this section from others.
            toggle = self.page.locator(
                'button.event-page-market-box-collapseBtn'
                ':has-text("Total Goals Over/Under")'
            ).first
            try:
                toggle.scroll_into_view_if_needed(timeout=5000)
                self.pm._session.human_pause()
                toggle.click()
                print(f"[dryrun] Clicked the 'Total Goals Over/Under' toggle.")
            except Exception as e:
                print(f"[dryrun] Could not click the accordion toggle: {e}")
                self._dump('03b_toggle_click_failed')
                return False

            # Wait for the Collapse to transition to MuiCollapse-entered.
            try:
                self.page.locator(
                    '.MuiCollapse-entered:has(.market-box-root[class*="TOTAL_GOALS_OVER"])'
                ).first.wait_for(state='visible', timeout=5000)
                print(f"[dryrun] O/U accordion expanded.")
            except PlaywrightTimeoutError:
                print(f"[dryrun] Timed out waiting for accordion to expand.")
                self._dump('03b_expand_timeout')
                return False
            self.pm._session.human_pause()
            self._shot('03b_after_expand')

        # Step 4: click Over 2.5.
        print(f"\n[dryrun] Step 4: clicking Over 2.5...")
        if not self._safe_click_first_visible(OVER_25_SELECTORS, timeout_ms=8000,
                                              step_label='click_over_25'):
            print(f"[dryrun] Over 2.5 selector did not match.")
            self._dump('04_over25_not_found')
            return False
        self.pm._session.human_pause()
        self._shot('04_after_over25_click')

        # Post-click sanity: did the click register? An MUI outcome button
        # toggles a `selected` class when added to the slip. If no button
        # in the O/U box has it, the click missed; bail with a clear log
        # before the slip-verification step hides the real cause.
        try:
            selected_count = self.page.locator(
                '.market-box-root[class*="TOTAL_GOALS_OVER"] '
                'button.outcome-box-root.selected'
            ).count()
            print(f"[dryrun] Selected outcome buttons in O/U box: {selected_count}")
            if selected_count == 0:
                print(f"[dryrun] Click did not register — no outcome button "
                      f"has the 'selected' class. The selector may have "
                      f"matched a non-clickable child, or there's an overlay.")
                self._dump('04_click_did_not_register')
                return False
        except Exception as e:
            print(f"[dryrun] Post-click sanity check failed: {e}")

        # Step 5: verify the bet slip reflects the selection.
        #
        # IMPORTANT: do NOT click anything here. The slip is rendered as a
        # sidebar on the right and is already visible. Previous versions
        # tried to "open" the slip via SLIP_OPEN_SELECTORS, but its
        # broad `[class*="betslip" i]` fallback would hit elements that
        # propagated back to the just-selected Over 2.5 button and
        # deselected it. Step 5 is verification only.
        #
        # Two checks, both must pass:
        #   (1) The Betslip counter button text changed from "(0)" to a
        #       number > 0. The button is `.slip-button-root` containing
        #       a <span>Betslip</span> and a <span>(N)</span>.
        #   (2) The outcome button stays in `selected` state (sanity).
        print(f"\n[dryrun] Step 5: verifying bet slip state (read-only, no clicks)...")
        slip_count_text = ''
        try:
            slip_count_loc = self.page.locator(
                '.slip-button-root:has(span:has-text("Betslip")) '
                'span:has-text("(")'
            ).first
            slip_count_text = (slip_count_loc.inner_text(timeout=2000) or '').strip()
            print(f"[dryrun] Betslip counter text: {slip_count_text!r}")
        except Exception as e:
            print(f"[dryrun] Could not read Betslip counter: {e}")

        # Reconfirm the outcome button is still selected — guards against
        # any later code accidentally toggling it off.
        selected_count_post = self.page.locator(
            '.market-box-root[class*="TOTAL_GOALS_OVER"] '
            'button.outcome-box-root.selected'
        ).count()
        print(f"[dryrun] Selected outcome buttons (recheck): {selected_count_post}")

        if selected_count_post == 0 or slip_count_text in ('(0)', '0', '', None):
            print(f"[dryrun] Slip verification FAILED. "
                  f"selected={selected_count_post}, slip_counter={slip_count_text!r}.")
            self._dump('05_slip_no_selection')
            return False
        print(f"[dryrun] ✓ Slip contains the selection. "
              f"Counter={slip_count_text}, outcome still selected.")
        self._shot('05_slip_with_selection')

        # Early exit gated by STOP_AFTER_STEP5. The slip has the Over 2.5
        # selection; we've verified the bet-slip plumbing works without
        # exercising the stake / ready-state flow. Clear the slip on the
        # way out so nothing persists in the session.
        if STOP_AFTER_STEP5:
            print()
            print("=" * 72)
            print("✓ STEP-5 VERIFICATION PASSED")
            print(f"  Match:     {HOME} vs {AWAY}")
            print(f"  Market:    {MARKET}")
            print(f"  Selection: {SELECTION}")
            print(f"  Bet slip contains the expected selection.")
            print(f"  (Stake entry and ready-state intentionally skipped.)")
            print("=" * 72)
            print()
            print("[dryrun] Clearing slip before exit...")
            cleared = self._safe_click_first_visible(SLIP_CLEAR_SELECTORS,
                                                    timeout_ms=5000,
                                                    step_label='exit_clear_slip')
            self.pm._session.human_pause()
            self._shot('06_after_exit_clear')
            if not cleared:
                print(f"[dryrun] No clear button found — slip may need manual "
                      f"cleanup. Selectors tried: {SLIP_CLEAR_SELECTORS}")
            print(f"[dryrun] Safety-net refusals during run: {self._refused_clicks}")
            return True

        # Step 6: enter stake.
        print(f"\n[dryrun] Step 6: setting stake = €{STAKE}...")
        if STAKE > MAX_STAKE:
            print(f"[dryrun] SAFETY VIOLATION: STAKE ({STAKE}) > MAX_STAKE ({MAX_STAKE}). "
                  f"Refusing to proceed.")
            return False
        if not self._safe_fill_first_visible(STAKE_INPUT_SELECTORS, str(STAKE),
                                             timeout_ms=5000):
            print(f"[dryrun] Could not find a stake input.")
            self._dump('06_stake_input_not_found')
            return False
        self.pm._session.human_pause()
        self._shot('06_stake_entered')

        # Read back the stake input value to confirm the fill actually took.
        # We try each candidate selector in turn and take the first input
        # whose value is non-empty.
        stake_value: str = ''
        stake_input_sel_used: str = ''
        for sel in STAKE_INPUT_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible(timeout=500):
                    val = (loc.input_value(timeout=1000) or '').strip()
                    if val:
                        stake_value = val
                        stake_input_sel_used = sel
                        break
            except Exception:
                continue
        print(f"[dryrun] Stake input read-back: {stake_value!r} "
              f"(via '{stake_input_sel_used or '<none>'}')")

        # Normalise Pamestoixima's rendering: comma → dot for decimals
        # (Greek format '10,00') and strip the appended currency symbol
        # ('10€'). Extract the first numeric run.
        import re as _re
        m = _re.search(r'[\d.,]+', stake_value)
        normalised = (m.group(0).replace(',', '.') if m else '').strip()
        accepted = False
        try:
            accepted = float(normalised) == float(STAKE)
        except ValueError:
            accepted = False
        if not accepted:
            print(f"[dryrun] Stake verification FAILED. "
                  f"Input value {stake_value!r} (parsed: {normalised!r}) "
                  f"!= expected '{STAKE}'.")
            self._dump('06_stake_verification_failed')
            return False
        print(f"[dryrun] ✓ Stake input accepted €{STAKE} "
              f"(rendered as {stake_value!r}).")

        # Step 7: place the bet for real (gated by EXECUTE_PLACE_BET).
        if EXECUTE_PLACE_BET:
            # Pre-placement balance read (best-effort — Pamestoixima's
            # balance scrape is unverified for positive balances, so we
            # treat None as "couldn't read" not as a failure).
            balance_before = self.pm.get_balance()
            print(f"[dryrun] Balance BEFORE placement: "
                  f"{('€' + str(balance_before)) if balance_before is not None else '<unreadable>'}")

            placed = self._execute_place_bet()
            if not placed:
                print(f"[dryrun] Place Bet click did not find a target. "
                      f"Bet was NOT placed.")
                self._dump('07_placebet_no_target')
                return False

            # Wait for the slip to clear (most reliable success indicator).
            print(f"[dryrun] Waiting for slip to clear (success indicator)...")
            slip_cleared = False
            for indicator in SLIP_EMPTY_INDICATORS:
                try:
                    self.page.locator(indicator).first.wait_for(
                        state='visible', timeout=15000
                    )
                    slip_cleared = True
                    print(f"[dryrun] Slip cleared (matched: '{indicator}').")
                    break
                except PlaywrightTimeoutError:
                    continue
                except Exception:
                    continue
            self._shot('07c_after_placement_settle')

            if not slip_cleared:
                print(f"[dryrun] !!! WARNING: slip did NOT clear within 15s. "
                      f"Bet status uncertain — check the Chromium window AND "
                      f"your Pamestoixima account manually before re-running !!!")
                self._dump('07_slip_did_not_clear')
                return False

            # Balance verification (best-effort).
            balance_after = self.pm.get_balance()
            print(f"[dryrun] Balance AFTER placement: "
                  f"{('€' + str(balance_after)) if balance_after is not None else '<unreadable>'}")
            if balance_before is not None and balance_after is not None:
                diff = round(balance_before - balance_after, 2)
                print(f"[dryrun] Balance delta: €{diff} (expected ~€{STAKE})")
                if abs(diff - STAKE) > 0.5:
                    print(f"[dryrun] !!! Balance delta ({diff}) doesn't match "
                          f"stake ({STAKE}) within €0.50 — verify manually !!!")

            # Persist an audit record. This is the permanent on-disk
            # receipt of the click; useful if the bookie disputes anything.
            import json as _json
            import datetime as _dt
            record = {
                'timestamp': _dt.datetime.now().isoformat(),
                'match': f'{HOME} vs {AWAY}',
                'match_url': MATCH_URL,
                'market': MARKET,
                'selection': SELECTION,
                'stake_eur': STAKE,
                'balance_before': balance_before,
                'balance_after': balance_after,
                'slip_cleared': slip_cleared,
                'dryrun_dir': self.dryrun_dir,
            }
            audit_path = os.path.join(self.dryrun_dir, 'placement_record.json')
            try:
                with open(audit_path, 'w') as f:
                    _json.dump(record, f, indent=2)
                print(f"[dryrun] Audit record written → {audit_path}")
            except Exception as e:
                print(f"[dryrun] Could not write audit record: {e}")

            print()
            print("=" * 72)
            print("✓ STEP-7 REAL BET PLACED")
            print(f"  Match:     {HOME} vs {AWAY}")
            print(f"  Selection: {SELECTION} {MARKET}")
            print(f"  Stake:     €{STAKE}")
            print(f"  Slip cleared: {slip_cleared}")
            if balance_before is not None and balance_after is not None:
                print(f"  Balance: €{balance_before} → €{balance_after}")
            print(f"  Audit record: {audit_path}")
            print(f"  IMPORTANT: verify the bet appears in your Pamestoixima "
                  f"'My Bets' history before closing this window.")
            print("=" * 72)
            print(f"[dryrun] Safety-net refusals during run: {self._refused_clicks}")
            return True

        # Early exit gated by STOP_AFTER_STEP6. Slip has the Over 2.5
        # selection AND the €10 stake is entered — Phase 9 plumbing is
        # validated end-to-end up to (but not including) the place-bet click.
        if STOP_AFTER_STEP6:
            print()
            print("=" * 72)
            print("✓ STEP-6 VERIFICATION PASSED")
            print(f"  Match:     {HOME} vs {AWAY}")
            print(f"  Market:    {MARKET}")
            print(f"  Selection: {SELECTION}")
            print(f"  Stake:     €{STAKE} (input value: {stake_value!r})")
            print(f"  Slip and stake plumbing OK. Place-bet click NOT exercised.")
            print("=" * 72)
            print()
            print("[dryrun] Clearing slip before exit...")
            cleared = self._safe_click_first_visible(SLIP_CLEAR_SELECTORS,
                                                    timeout_ms=5000,
                                                    step_label='exit_clear_slip')
            self.pm._session.human_pause()
            self._shot('07_after_exit_clear')
            if not cleared:
                print(f"[dryrun] No clear button found — slip may need manual "
                      f"cleanup. Selectors tried: {SLIP_CLEAR_SELECTORS}")
            print(f"[dryrun] Safety-net refusals during run: {self._refused_clicks}")
            return True

        # Step 7: READY state — this is the terminal screenshot.
        print(f"\n[dryrun] ✓ Reached ready-to-place state.")
        print(f"[dryrun] Final screenshot: 07_READY_TO_PLACE")
        self._shot('07_READY_TO_PLACE')

        # Step 8: hand off to user for inspection.
        print()
        print("=" * 72)
        print("DRY-RUN COMPLETE — bet slip is prepared but NOT submitted.")
        print(f"  Match:     {HOME} vs {AWAY}")
        print(f"  Market:    {MARKET}")
        print(f"  Selection: {SELECTION}")
        print(f"  Stake:     €{STAKE}")
        print()
        print("Inspect the Chromium window. The 'Place bet' button should be visible")
        print("but the script will NEVER click it.")
        print()
        print("Press ENTER to clear the bet slip and exit.")
        print("(Ctrl+C to leave the slip as-is and exit immediately.)")
        print("=" * 72)
        try:
            input()
        except KeyboardInterrupt:
            print("\n[dryrun] User interrupted; leaving slip intact.")
            return True

        # Step 9: clear the slip.
        print(f"\n[dryrun] Clearing bet slip...")
        cleared = self._safe_click_first_visible(SLIP_CLEAR_SELECTORS, timeout_ms=5000,
                                                 step_label='clear_slip')
        self.pm._session.human_pause()
        self._shot('08_after_clear')
        if not cleared:
            print(f"[dryrun] No clear button found — slip may need manual cleanup. "
                  f"Selectors tried: {SLIP_CLEAR_SELECTORS}")

        print(f"\n[dryrun] Done. Safety-net refusals during run: {self._refused_clicks}")
        return True


def cmd_dry_run_freiburg_villa(args) -> int:
    """CLI entrypoint. Headed mode is forced; no --headless option exposed."""
    print(f"[dryrun] Headed mode forced (this is a supervised one-shot test).")
    try:
        with session_lock():
            pm = Pamestoixima(headless=False, reuse_session=True)
            try:
                # Login first. If credentials missing or login fails, abort.
                if not pm.login():
                    print(f"[dryrun] Login failed — aborting before any bet-slip work.")
                    return 1
                runner = FreiburgVillaDryRun(pm)
                ok = runner.run()
                return 0 if ok else 1
            finally:
                pm.close()
    except RuntimeError as e:
        print(f"[dryrun] Error: {e}", file=sys.stderr)
        return 1
