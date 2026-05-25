"""DRY-RUN ONLY: discover Pamestoixima's My Bets / cashout DOM.

Goal: find the selectors and value format needed to read the cashout
offer for a specific OPEN bet that the user has already placed manually
via the Pamestoixima website. All other match info (score, minute, stats)
is already covered by the Flashscore scraper; the bookmaker-specific bit
is the cashout offer, so that is the only thing we discover here.

Hardcoded target (the bet the user placed on 2026-05-22):
    HOME = 'FC Machida Zelvia'
    AWAY = 'Urawa Red Diamonds'
    LEAGUE = 'J League' (J1 on Flashscore)

Flow:
    1.  Login (reuse saved session if valid).
    2.  Dismiss overlays.
    3.  Find the "My Bets" entry-point — the user confirmed it's a
        visible <span> after login. Click it; open bets render below.
    4.  Dump the My Bets page (full-page screenshot + raw HTML + URL).
    5.  Locate the target bet's container by team text — prefer the
        smallest ancestor that contains BOTH team tokens AND a
        .full-cashout-root button (= the <li id="my-bets-O-<uuid>">
        row on Pamestoixima).
    6.  Dump that container's outerHTML in isolation.
    7.  Probe for a Cash Out button on the container (selector:
        button.full-cashout-root).
        - If found + enabled: snapshot its pre-click text/classes
          (the text already contains the offer value), click ONCE,
          snapshot the post-click "Confirm cash out" yellow state,
          then DEFUSE: remove every .full-cashout-root from the DOM
          and navigate to the homepage. A second commit-click is
          structurally impossible after defuse.
        - If not found / disabled (event in-play, offer paused), wait
          5s and retry up to MAX_RETRIES times.
    8.  Write a JSON probe report with pre- and post-click text +
        classes + selectors that matched.

Safety:
    - Confirmed 2026-05-22: Pamestoixima uses a TWO-CLICK same-button
      flow, NOT a separate modal. The .full-cashout-root button shows
      "Cash Out €X.XX" pre-click, then mutates to "Confirm cash out"
      (yellow background) after the first click. A SECOND click on
      that same button COMMITS the cashout — real money moves.
    - This script clicks the button ONCE to surface the confirm state
      (useful to confirm the value/text is identical pre- and post-
      click), then IMMEDIATELY:
        (a) reads the post-click text,
        (b) removes every .full-cashout-root from the DOM via
            page.evaluate('… .forEach(b => b.remove())') so no further
            click can land on it,
        (c) navigates away from My Bets to the homepage.
      A second commit-click is structurally impossible after step (a).
    - The pre-click button text already contains the value, so the
      click step is informational only. If anything looks off post-
      click, abort.

Run:
    python -m real_betting dry-run-cashout-discovery
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from typing import List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import config
from .bookmakers.pamestoixima import Pamestoixima
from .session import session_lock


# --- target bet (the one placed on Pamestoixima on 2026-05-22) -------------

HOME = 'FC Machida Zelvia'
AWAY = 'Urawa Red Diamonds'
# Substring tokens used to locate the bet row in My Bets. Use the
# distinctive words from each name — "FC" / "Diamonds" alone would
# match many other rows.
HOME_TOKEN = 'Machida'
AWAY_TOKEN = 'Urawa'

# Time the button needs to repaint into its "Confirm cash out" yellow
# state after the first click. 500–800ms is comfortable.
POST_CLICK_SETTLE_MS = 800

# After-cashout-state safe URL — navigated to immediately after reading
# the post-click button state, as a belt-and-braces measure on top of
# removing the button from the DOM.
SAFE_URL = 'https://www.pamestoixima.gr/en/'

# Retry cadence when the Cash Out button is missing/disabled because
# the event is in-play and the offer is paused.
MAX_RETRIES = 4
RETRY_DELAY_S = 5

# DANGER: when True, click the .full-cashout-root.confirmation button a
# SECOND time to actually commit the cashout. Real money moves.
#
# Discovery mode (EXECUTE_CASHOUT=False, default) ends after reading
# the post-click state and defusing the button. Toggle this to True
# only when you specifically want the end-to-end cashout test on the
# currently-open bet, and never leave it True between runs.
EXECUTE_CASHOUT = False

# How long to wait after the confirm click for a success signal (the
# button disappearing, or a toast notification, or the row moving from
# Open Bets to Settled Bets).
CASHOUT_SUCCESS_WAIT_MS = 15000


# --- selectors -------------------------------------------------------------

# "My Bets" entry. The user said it's a visible <span> after login —
# could be in the header, account dropdown, or sidebar. Try the most
# specific first.
MY_BETS_ENTRY_SELECTORS = (
    # English text (we're on /en)
    'span:text-is("My Bets")',
    'span:has-text("My Bets")',
    'a:has-text("My Bets")',
    'button:has-text("My Bets")',
    # Greek fallback
    'span:has-text("Τα Δελτία μου")',
    'span:has-text("Δελτία μου")',
    'a:has-text("Τα Δελτία μου")',
    'a:has-text("Δελτία μου")',
    # Class-based hints (best-guess, expect to iterate)
    '[class*="my-bets" i]',
    '[class*="myBets" i]',
)

# Once the My Bets pane is open, an "Open" / "Active" tab usually
# segments open vs settled bets. The user said open bets are shown
# directly below the My Bets click, so this is best-effort only.
OPEN_BETS_TAB_SELECTORS = (
    'button:has-text("Open"):not([disabled])',
    'button:has-text("Active"):not([disabled])',
    'button:has-text("Ενεργά"):not([disabled])',
    'button:has-text("Ανοιχτά"):not([disabled])',
    'a:has-text("Open Bets")',
    'a:has-text("Active Bets")',
    '[role="tab"]:has-text("Open")',
    '[role="tab"]:has-text("Active")',
    '[role="tab"]:has-text("Ενεργά")',
)

# Cash Out button on a single bet row. We search WITHIN the matched
# bet container, not the whole page (otherwise we'd hit other rows).
# Greek labels included because OPAP UI mixes languages.
#
# `full-cashout-root` is the Pamestoixima-namespaced stable class
# (confirmed 2026-05-22). The surrounding MuiButton-* / css-1mz3gsl
# classes are auto-generated and must NOT be selected on.
CASHOUT_BUTTON_SELECTORS = (
    'button.full-cashout-root',
    '.full-cashout-root',
    'button:has-text("Cash Out")',
    'button:has-text("Cashout")',
    'button:has-text("CASH OUT")',
    'button:has-text("Εξαργύρωση")',
    'button:has-text("Πληρωμή")',
    'button[class*="cashout" i]',
    'button[class*="cash-out" i]',
    'button[class*="cashOut" i]',
    '[class*="cashout" i] button',
    '[data-testid*="cashout" i]',
)

# THE LIST OF DEATH. Substrings that, if present in any button we are
# about to click, mean a real-money commit. Listed in EN + Greek; the
# post-click "Confirm cash out" text on the .full-cashout-root button
# is the main thing this list catches if anyone re-enters the flow
# without removing the button from the DOM first.
FORBIDDEN_CLICK_LABELS = (
    'confirm',
    'επιβεβαίωση',
    'επιβεβαιωση',
    'επιβεβαιώνω',
    'accept',
    'αποδοχή',
    'αποδοχη',
    'ναι',                  # "Yes"
    'place bet',
    'submit',
    'υποβολή',
)


# --- runner ----------------------------------------------------------------

class CashoutDiscovery:
    def __init__(self, pm: Pamestoixima):
        self.pm = pm
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.dryrun_dir = os.path.join(
            config.OUTPUT_DIR, f'cashout_discovery_{ts}'
        )
        os.makedirs(self.dryrun_dir, exist_ok=True)
        self.report: dict = {
            'timestamp': datetime.datetime.now().isoformat(),
            'target': {
                'home': HOME,
                'away': AWAY,
                'home_token': HOME_TOKEN,
                'away_token': AWAY_TOKEN,
            },
            'steps': [],
        }
        self._refused_clicks = 0

    @property
    def page(self):
        return self.pm._session.page

    # -- IO helpers --------------------------------------------------------

    def _shot(self, label: str) -> str:
        path = os.path.join(self.dryrun_dir, f'{label}.png')
        try:
            self.page.screenshot(path=path, full_page=True)
            print(f"[cashout-discovery] screenshot → {path}")
        except Exception as e:
            print(f"[cashout-discovery] screenshot {label} failed: {e}")
        return path

    def _dump(self, label: str, locator=None) -> str:
        """Dump screenshot + URL + HTML (full page, or locator outerHTML)."""
        base = os.path.join(self.dryrun_dir, label)
        try:
            if locator is None:
                self.page.screenshot(path=base + '.png', full_page=True)
            else:
                try:
                    locator.screenshot(path=base + '.png')
                except Exception:
                    self.page.screenshot(path=base + '.png', full_page=True)
        except Exception:
            pass
        try:
            if locator is None:
                content = self.page.content()
            else:
                content = locator.evaluate('el => el.outerHTML')
            with open(base + '.html', 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"[cashout-discovery] dump html for {label} failed: {e}")
        try:
            with open(base + '.url', 'w') as f:
                f.write(self.page.url)
        except Exception:
            pass
        print(f"[cashout-discovery] dump → {base}.{{png,html,url}}")
        return base

    def _record(self, step: str, **fields):
        entry = {'step': step, **fields}
        self.report['steps'].append(entry)
        print(f"[cashout-discovery] {step}: {fields}")

    def _write_report(self):
        path = os.path.join(self.dryrun_dir, 'cashout_discovery_report.json')
        try:
            with open(path, 'w') as f:
                json.dump(self.report, f, indent=2)
            print(f"[cashout-discovery] report → {path}")
        except Exception as e:
            print(f"[cashout-discovery] could not write report: {e}")

    # -- click guards ------------------------------------------------------

    def _is_forbidden(self, text: str) -> Optional[str]:
        low = (text or '').lower().strip()
        for f in FORBIDDEN_CLICK_LABELS:
            if f in low:
                return f
        return None

    def _safe_click_first(self, selectors, timeout_ms: int = 4000,
                          step_label: str = 'click') -> Optional[str]:
        """Click the first visible match whose text is NOT forbidden.
        Returns the selector that worked, or None."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                try:
                    text = (loc.inner_text(timeout=500) or '').strip()
                except Exception:
                    text = ''
                forbidden = self._is_forbidden(text)
                if forbidden:
                    self._refused_clicks += 1
                    print(f"[cashout-discovery] REFUSED click on '{sel}' "
                          f"— text {text!r} matched forbidden '{forbidden}'.")
                    self._dump(f'{step_label}_refused_{forbidden}')
                    continue
                loc.click()
                return sel
            except PlaywrightTimeoutError:
                continue
            except Exception as e:
                print(f"[cashout-discovery] {step_label} via '{sel}' raised: {e!r}")
                continue
        return None

    # -- flow --------------------------------------------------------------

    def run(self) -> bool:
        print(f"[cashout-discovery] Output dir: {self.dryrun_dir}")
        print(f"[cashout-discovery] Target: {HOME} vs {AWAY}")
        self._shot('00_after_login')

        self.pm._dismiss_overlays()

        # Step 1: find and click the "My Bets" entry.
        print(f"\n[cashout-discovery] Step 1: opening My Bets...")
        sel_used = self._safe_click_first(
            MY_BETS_ENTRY_SELECTORS, timeout_ms=5000, step_label='01_my_bets_entry',
        )
        if not sel_used:
            self._record('my_bets_entry', matched=False)
            self._dump('01_my_bets_not_found')
            print(f"[cashout-discovery] Could not find My Bets entry "
                  f"after trying {len(MY_BETS_ENTRY_SELECTORS)} selectors.")
            return False
        self._record('my_bets_entry', matched=True, selector=sel_used)
        self.pm._session.human_pause()
        try:
            self.page.wait_for_load_state('networkidle', timeout=8000)
        except PlaywrightTimeoutError:
            pass
        self._dump('01_my_bets_page')

        # Step 1b (best-effort): click "Open" tab if there is one. The
        # user said the open bets show directly under the My Bets click,
        # so this is only useful if the default tab is "All" or "Settled".
        tab_used = self._safe_click_first(
            OPEN_BETS_TAB_SELECTORS, timeout_ms=2000,
            step_label='01b_open_bets_tab',
        )
        if tab_used:
            self._record('open_bets_tab', matched=True, selector=tab_used)
            self.pm._session.human_pause()
            self._dump('01b_open_bets_view')
        else:
            self._record('open_bets_tab', matched=False,
                         note='No tab matched — likely already on Open view.')

        # Step 2: locate the target bet's container by team token.
        print(f"\n[cashout-discovery] Step 2: locating row for "
              f"{HOME_TOKEN!r} / {AWAY_TOKEN!r}...")
        # Try to find an element containing BOTH team tokens. If that
        # fails, fall back to either token alone (still useful for the
        # HTML dump).
        bet_container = None
        try:
            # Pick the smallest ancestor that contains BOTH team tokens
            # AND a full-cashout-root button. The previous "smallest with
            # both tokens" heuristic chose the inline .selectionName div,
            # which sits next to (not around) the cashout button.
            #
            # On Pamestoixima's My Bets page the canonical wrapper is
            # `<li id="my-bets-O-<uuid>">` inside `<ul class="my-bets-container-root">`
            # — but rather than depending on that exact tag/id, we look
            # for any ancestor that visibly groups the bet's text with
            # its cashout button. Fall back to "both tokens only" if no
            # cashout button is rendered anywhere yet (in-play paused).
            js = """
            (tokens) => {
              const [t1, t2] = tokens;
              const all = Array.from(document.querySelectorAll('*'));
              const haveBoth = all.filter(el => {
                const txt = (el.innerText || '');
                return txt.includes(t1) && txt.includes(t2);
              });
              if (!haveBoth.length) return null;
              // Prefer ancestors that also contain a cashout button.
              const withCashout = haveBoth.filter(
                el => el.querySelector('.full-cashout-root, [class*="cashout" i]')
              );
              const pool = withCashout.length ? withCashout : haveBoth;
              // Smallest text length = deepest / tightest wrapper that
              // still satisfies the criterion.
              pool.sort((a, b) => (a.innerText.length) - (b.innerText.length));
              const el = pool[0];
              el.setAttribute('data-cashout-target', '1');
              el.scrollIntoView({behavior: 'instant', block: 'center'});
              return {
                tag: el.tagName,
                id: el.id || null,
                cls: el.className,
                len: el.innerText.length,
                hadCashoutSibling: withCashout.length > 0,
              };
            }
            """
            info = self.page.evaluate(js, [HOME_TOKEN, AWAY_TOKEN])
            if info:
                bet_container = self.page.locator('[data-cashout-target="1"]').first
                self._record('bet_container_found', match='both_tokens', info=info)
                self._dump('02_target_bet_container', locator=bet_container)
            else:
                self._record('bet_container_found', match='neither',
                             note='Neither token appeared together.')
        except Exception as e:
            print(f"[cashout-discovery] container search raised: {e}")
            self._record('bet_container_found', match='error', error=str(e))

        if bet_container is None:
            # Fall back to single-token search for the HTML dump only.
            for token in (HOME_TOKEN, AWAY_TOKEN):
                try:
                    n = self.page.get_by_text(token, exact=False).count()
                    self._record('token_count', token=token, count=n)
                except Exception:
                    pass
            self._dump('02_no_container_full_page')
            print(f"[cashout-discovery] Could not find a container with both "
                  f"team tokens. The bet may not be visible (page filtered to "
                  f"settled / different league) or the tokens need adjusting.")
            return False

        # Step 3: probe for Cash Out button within the container.
        # Retry up to MAX_RETRIES times if it's missing/disabled (event
        # in-play → offer temporarily unavailable).
        cashout_button_sel: Optional[str] = None
        cashout_text: str = ''
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"\n[cashout-discovery] Step 3 (attempt {attempt}/{MAX_RETRIES}): "
                  f"probing for cashout button...")
            for sel in CASHOUT_BUTTON_SELECTORS:
                try:
                    loc = bet_container.locator(sel).first
                    if not loc.is_visible(timeout=800):
                        continue
                    # Inspect the disabled state.
                    disabled = loc.get_attribute('disabled')
                    aria_disabled = (loc.get_attribute('aria-disabled') or '').lower()
                    if disabled is not None or aria_disabled == 'true':
                        self._record('cashout_button_disabled', selector=sel,
                                     disabled=disabled, aria_disabled=aria_disabled)
                        continue
                    text = (loc.inner_text(timeout=500) or '').strip()
                    cashout_button_sel = sel
                    cashout_text = text
                    self._record('cashout_button_visible', selector=sel,
                                 text=text)
                    break
                except Exception:
                    continue
            if cashout_button_sel:
                break
            if attempt < MAX_RETRIES:
                print(f"[cashout-discovery] No cashout offer visible "
                      f"(probably in-play / paused). Waiting {RETRY_DELAY_S}s...")
                time.sleep(RETRY_DELAY_S)
                # Re-render: scroll a bit to nudge MUI lazy lists.
                try:
                    bet_container.scroll_into_view_if_needed()
                except Exception:
                    pass

        if not cashout_button_sel:
            print(f"[cashout-discovery] Could not find an enabled Cash Out "
                  f"button after {MAX_RETRIES} attempts. The HTML dump in "
                  f"02_target_bet_container.html is the next thing to inspect.")
            self._dump('03_no_cashout_button')
            self._write_report()
            return False

        # Step 4: click the row's Cash Out button ONCE.
        # Pamestoixima uses a two-click same-button flow (no modal): the
        # button mutates to a yellow "Confirm cash out" state on first
        # click. A SECOND click would commit. We click once to capture
        # that mutation, then immediately remove the button from the DOM
        # so a second click is impossible — see Step 6.
        #
        # We snapshot pre-click classes too. The pre-click text already
        # carries the offer value ("Cash Out €X.XX"); the click step is
        # purely to confirm the post-click behaviour for future work.
        print(f"\n[cashout-discovery] Step 4: snapshot pre-click, then "
              f"click Cash Out ONCE (no modal expected; same button mutates).")
        pre_click_loc = bet_container.locator(cashout_button_sel).first
        try:
            pre_classes = pre_click_loc.get_attribute('class') or ''
        except Exception:
            pre_classes = ''
        self._record('pre_click_button_state',
                     text=cashout_text, classes=pre_classes[:300])
        self._shot('04a_pre_click')

        try:
            pre_click_loc.click()
        except Exception as e:
            print(f"[cashout-discovery] Cash Out click raised: {e}")
            self._dump('04_cashout_click_failed')
            self._write_report()
            return False

        # Wait for the button to repaint into its "Confirm cash out" state.
        time.sleep(POST_CLICK_SETTLE_MS / 1000)
        self._shot('04b_after_first_click')

        # Step 5: read the post-click state (text + classes).
        # The button is the SAME element; .full-cashout-root persists in
        # the class list. Read by class — selector doesn't change.
        print(f"\n[cashout-discovery] Step 5: reading post-click state...")
        post_text = ''
        post_classes = ''
        try:
            post_loc = bet_container.locator('.full-cashout-root').first
            post_text = (post_loc.inner_text(timeout=1000) or '').strip()
            post_classes = post_loc.get_attribute('class') or ''
        except Exception as e:
            print(f"[cashout-discovery] Could not re-read button state: {e}")
        self._record('post_click_button_state',
                     text=post_text, classes=post_classes[:300])
        try:
            self._dump('05_after_first_click_button',
                       locator=bet_container.locator('.full-cashout-root').first)
        except Exception:
            self._dump('05_after_first_click_full_page')

        # Detect whether the button entered its confirm state. The text
        # is the most reliable signal (contains "Confirm" / "Επιβεβαίωση"
        # or similar). Classes can also help — yellow state typically
        # adds a "warning"/"selected"/state-class.
        post_text_low = post_text.lower()
        in_confirm_state = any(s in post_text_low for s in (
            'confirm', 'επιβεβ', 'πληρ', 'ναι',
        ))
        self._record('confirm_state_detected', value=in_confirm_state,
                     note='Heuristic — based on post-click button text.')

        # Branch on EXECUTE_CASHOUT.
        if EXECUTE_CASHOUT:
            # Real-money path. Validates confirm state, parses the
            # cashout amount (must be > 0), prints a loud banner,
            # sleeps briefly so the user can ctrl+C, then clicks the
            # same button a second time. No upper cap — the decision
            # belongs to the live-stats decision engine (see scenarios
            # #3/#4 in test_case_scenarios.md); EXECUTE_CASHOUT is the
            # only kill switch.
            executed = self._execute_confirm_click(
                bet_container, post_text, in_confirm_state,
            )
            self.report['summary'] = {
                'cashout_button_selector': cashout_button_sel,
                'pre_click_text': cashout_text,
                'pre_click_classes_snippet': pre_classes[:300],
                'post_click_text': post_text,
                'post_click_classes_snippet': post_classes[:300],
                'confirm_state_detected': in_confirm_state,
                'execute_cashout': True,
                'committed': bool(executed),
                'refused_clicks': self._refused_clicks,
            }
            self._write_report()
            print()
            print("=" * 72)
            print("✓ Cashout end-to-end test complete." if executed else
                  "✗ Cashout commit did NOT complete.")
            print(f"  Bet:    {HOME} vs {AWAY}")
            print(f"  Pre-click  text: {cashout_text!r}")
            print(f"  Post-click text: {post_text!r}")
            print(f"  Committed: {bool(executed)}")
            print(f"  Output: {self.dryrun_dir}")
            print("=" * 72)
            return bool(executed)

        # DRY-RUN path — defuse and exit. Remove every .full-cashout-root
        # from the DOM so a second click is structurally impossible, then
        # navigate to a safe URL as belt-and-braces.
        print(f"\n[cashout-discovery] Step 6 (dry-run): defusing — removing "
              f"button from DOM, then navigating away.")
        removed_count = 0
        try:
            removed_count = self.page.evaluate(
                """() => {
                    const nodes = document.querySelectorAll('.full-cashout-root');
                    nodes.forEach(n => n.remove());
                    return nodes.length;
                }"""
            )
        except Exception as e:
            print(f"[cashout-discovery] DOM removal raised: {e}")
        self._record('dom_defuse', removed_full_cashout_root=removed_count)
        self._shot('06a_after_dom_defuse')

        try:
            self.page.goto(SAFE_URL)
            self.pm._session.human_pause()
        except Exception as e:
            print(f"[cashout-discovery] Navigation to safe URL raised: {e}")
        self._record('safe_url_navigated', url=SAFE_URL)
        self._shot('06b_after_safe_navigation')

        # Step 7: write the final report.
        self.report['summary'] = {
            'cashout_button_selector': cashout_button_sel,
            'pre_click_text': cashout_text,
            'pre_click_classes_snippet': pre_classes[:300],
            'post_click_text': post_text,
            'post_click_classes_snippet': post_classes[:300],
            'confirm_state_detected': in_confirm_state,
            'dom_removed': removed_count,
            'execute_cashout': False,
            'refused_clicks': self._refused_clicks,
        }
        self._write_report()

        print()
        print("=" * 72)
        print("✓ Cashout discovery complete.")
        print(f"  Bet:    {HOME} vs {AWAY}")
        print(f"  Pre-click  text: {cashout_text!r}")
        print(f"  Post-click text: {post_text!r}")
        print(f"  Confirm state detected (heuristic): {in_confirm_state}")
        print(f"  DOM-defuse removed: {removed_count} button(s)")
        print(f"  Output: {self.dryrun_dir}")
        print(f"  Safety-net refusals: {self._refused_clicks}")
        print("=" * 72)
        return True

    # -- end-to-end commit path -------------------------------------------

    def _execute_confirm_click(self, bet_container, post_text: str,
                               in_confirm_state: bool) -> bool:
        """DANGER: click the .full-cashout-root button a second time to
        commit the cashout. Real money moves.

        Pre-flight checks (any failure → abort, no second click):
          - in_confirm_state must be True (button text contains
            'confirm'/'επιβεβ' after the first click).
          - The button's class must still include 'confirmation'.
          - The parsed cashout amount must be > 0 (any positive
            offer is accepted — there is no upper cap; the cash/hold
            decision belongs to the live-stats decision engine).

        On success: writes a placement_record.json into the dryrun dir
        (same shape as the Freiburg placement audit). Balance pre/post
        is best-effort.
        """
        import re

        if not in_confirm_state:
            print(f"[cashout-discovery] ABORT: post-click button text "
                  f"{post_text!r} doesn't look like a confirm state. "
                  f"Refusing to click a second time.")
            self._record('execute_aborted', reason='not_in_confirm_state')
            self._dump('06_execute_aborted_not_confirm')
            return False

        # Re-locate the button by class — it's the same DOM node, now
        # carrying the 'confirmation' class.
        try:
            confirm_btn = bet_container.locator(
                'button.full-cashout-root.confirmation'
            ).first
            confirm_btn.wait_for(state='visible', timeout=2000)
        except PlaywrightTimeoutError:
            # Fall back to .full-cashout-root if the .confirmation class
            # didn't make it; we already verified the text earlier.
            confirm_btn = bet_container.locator('.full-cashout-root').first
            try:
                confirm_btn.wait_for(state='visible', timeout=2000)
            except PlaywrightTimeoutError:
                print(f"[cashout-discovery] ABORT: confirm button not visible.")
                self._record('execute_aborted', reason='confirm_btn_gone')
                self._dump('06_execute_aborted_no_btn')
                return False

        # Parse the cashout amount out of the post-click text.
        # Expected format: 'Confirm Cash Out\n€1.47' (€ + number; locale
        # may use ',' as decimal separator).
        m = re.search(r'[€]?\s*([\d.,]+)', post_text.replace('\n', ' '))
        amount: Optional[float] = None
        if m:
            try:
                amount = float(m.group(1).replace(',', '.'))
            except ValueError:
                amount = None
        if amount is None or amount <= 0:
            print(f"[cashout-discovery] ABORT: could not parse cashout "
                  f"amount from {post_text!r}.")
            self._record('execute_aborted', reason='amount_unparseable',
                         text=post_text)
            return False

        # Loud banner — last chance to ctrl+C.
        print()
        print("!" * 72)
        print(f"!!! EXECUTING REAL CASHOUT — committing €{amount:.2f} on    !!!")
        print(f"!!! {HOME} vs {AWAY} !!!")
        print(f"!!! This is a real-money click. 3 seconds to ctrl+C…       !!!")
        print("!" * 72)
        print()
        time.sleep(3)

        # Best-effort pre-click balance read.
        balance_before = None
        try:
            balance_before = self.pm.get_balance()
        except Exception:
            pass
        print(f"[cashout-discovery] Balance BEFORE commit: "
              f"{('€' + str(balance_before)) if balance_before is not None else '<unreadable>'}")

        self._shot('06_pre_commit')
        try:
            confirm_btn.click()
        except Exception as e:
            print(f"[cashout-discovery] Commit click raised: {e}")
            self._dump('06_commit_click_failed')
            self._record('commit_click', success=False, error=str(e))
            return False
        self._shot('06_immediately_after_commit')

        # Wait for a success signal. Easiest: the .full-cashout-root
        # button is gone from the bet row (Pamestoixima typically replaces
        # it with a 'Cashed out' badge), OR a toast appears.
        print(f"[cashout-discovery] Waiting up to "
              f"{CASHOUT_SUCCESS_WAIT_MS}ms for success signal...")
        success = False
        deadline = time.time() + CASHOUT_SUCCESS_WAIT_MS / 1000
        while time.time() < deadline:
            try:
                still_present = bet_container.locator(
                    '.full-cashout-root'
                ).count()
                if still_present == 0:
                    success = True
                    print(f"[cashout-discovery] Cashout button is gone — "
                          f"treating as success.")
                    break
            except Exception:
                pass
            # Toast/notification fallback.
            for sel in (
                ':text-matches("(?i)cash.?out.*(successful|complete|paid|placed)")',
                ':text-matches("(?i)εξαργ.*(επιτυχ|ολοκλ))")',
                '[class*="toast" i]:has-text("Cash")',
                '[class*="snackbar" i]:has-text("Cash")',
            ):
                try:
                    if self.page.locator(sel).first.is_visible(timeout=200):
                        success = True
                        print(f"[cashout-discovery] Success toast matched: {sel}")
                        break
                except Exception:
                    continue
            if success:
                break
            time.sleep(0.4)
        self._shot('07_after_success_wait')

        balance_after = None
        try:
            balance_after = self.pm.get_balance()
        except Exception:
            pass
        print(f"[cashout-discovery] Balance AFTER commit: "
              f"{('€' + str(balance_after)) if balance_after is not None else '<unreadable>'}")

        record = {
            'timestamp': datetime.datetime.now().isoformat(),
            'match': f'{HOME} vs {AWAY}',
            'cashout_amount_eur': amount,
            'pre_click_text': post_text,
            'balance_before': balance_before,
            'balance_after': balance_after,
            'success_signal': success,
            'dryrun_dir': self.dryrun_dir,
        }
        path = os.path.join(self.dryrun_dir, 'cashout_placement_record.json')
        try:
            with open(path, 'w') as f:
                json.dump(record, f, indent=2)
            print(f"[cashout-discovery] Audit record → {path}")
        except Exception as e:
            print(f"[cashout-discovery] Could not write audit record: {e}")

        self._record('commit_click', success=success, amount=amount,
                     balance_before=balance_before, balance_after=balance_after)
        return success


def cmd_dry_run_cashout_discovery(args) -> int:
    """CLI entrypoint. Headed mode is forced; supervised one-shot."""
    print(f"[cashout-discovery] Headed mode forced (one-shot discovery).")
    try:
        with session_lock():
            pm = Pamestoixima(headless=False, reuse_session=True)
            try:
                if not pm.login():
                    print(f"[cashout-discovery] Login failed; aborting.")
                    return 1
                runner = CashoutDiscovery(pm)
                ok = runner.run()
                return 0 if ok else 1
            finally:
                pm.close()
    except RuntimeError as e:
        print(f"[cashout-discovery] Error: {e}", file=sys.stderr)
        return 1
