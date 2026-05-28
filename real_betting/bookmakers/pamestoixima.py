"""Pamestoixima (OPAP) — login + read-only operations.

Status: step 3 (login only). find_fixtures / get_odds are stubs that land
in steps 6b/6c.

Selectors are best-guess. The first time you run `login`, expect to
iterate: failures dump screenshot + HTML to output/real_betting/failures/
so you can inspect what the page actually looks like and adjust selectors.

To explore the page manually with Playwright's recorder, run:
    venv/bin/playwright codegen --target python-async --browser chromium \
        https://www.pamestoixima.gr/
"""

from typing import List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .. import config
from ..bookmaker_base import Bookmaker
from ..credentials import get_credentials, mask_username
from ..session import BrowserSession


class Pamestoixima(Bookmaker):
    SLUG = 'pamestoixima'
    DISPLAY_NAME = 'Pamestoixima (OPAP)'
    # /en gives us the English UI — labels and team names come back in English,
    # which sidesteps the Greek↔English normalisation that step 6a was scoped to.
    BASE_URL = 'https://www.pamestoixima.gr/en'

    # --- selectors --------------------------------------------------------
    #
    # Ordered tuples = "try each until one is visible". This is the cheapest
    # way to be resilient to small DOM changes between site updates. If all
    # fail, the login routine dumps screenshot + HTML so we can update.

    COOKIE_ACCEPT_SELECTORS = (
        '#onetrust-accept-btn-handler',          # OneTrust standard ID (stable)
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("Αποδοχή όλων")',       # Greek fallback
        'button:has-text("Αποδοχή")',
        'button[id*="accept" i]',
    )

    # Promotional / ad modals frequently cover the Login button on first load
    # (welcome bonus, deposit promo, etc.). Best-effort dismissal — none of
    # these are fatal if absent, but if present they block everything below.
    # ESC key is tried first because it closes the bulk of Vue-mounted modals
    # for free; the explicit close selectors are the fallback.
    PROMO_DISMISS_SELECTORS = (
        'button[aria-label*="close" i]',
        'button[aria-label*="κλείσιμο" i]',      # Greek "close"
        'button[aria-label="X"]',
        'button:has-text("No thanks")',
        'button:has-text("Όχι ευχαριστώ")',      # Greek "no thanks"
        'button:has-text("Close")',
        'button:has-text("Κλείσιμο")',
        'button:has-text("Skip")',
        'button:has-text("Παράλειψη")',
        '[class*="modal" i] [class*="close" i]',
        '[class*="popup" i] [class*="close" i]',
        '[class*="overlay" i] button:has-text("X")',
        '[class*="dialog" i] button[class*="close" i]',
    )

    LOGIN_OPEN_SELECTORS = (
        '#quick_login_login',                    # stable Vue-generated ID
        'button:has-text("LOGIN")',              # uppercase EN
        'button:has-text("Login")',
        'button.opap-base-button:has-text("LOGIN")',
        'button:has-text("ΣΥΝΔΕΣΗ")',             # Greek fallback
        'button:has-text("Σύνδεση")',
    )

    USERNAME_INPUT_SELECTORS = (
        'input[name="username"]',
        'input[name="login"]',
        'input[name="user"]',
        'input[id*="username" i]',
        'input[id*="login" i]',
        'input[autocomplete="username"]',
    )

    PASSWORD_INPUT_SELECTORS = (
        'input[name="password"]',
        'input[type="password"]',
        'input[autocomplete="current-password"]',
    )

    SUBMIT_SELECTORS = (
        'button[type="submit"]:not([disabled])',
        'button:has-text("Σύνδεση"):not([disabled])',
        'button:has-text("Login"):not([disabled])',
        'input[type="submit"]:not([disabled])',
    )

    # Authenticated-only — finding any of these = login succeeded.
    # Verified against an actual logged-in page dump (2026-05-18). These
    # three are state-independent — they appear regardless of balance value.
    # NOTE: `.pli-deposit-button` was intentionally removed from this list
    # because it only renders when balance == 0; using it as a login-success
    # indicator would false-negative on any user with positive balance.
    POST_LOGIN_SELECTORS = (
        '#logged-in-menu',                       # user menu wrapper
        '.pli-logged-in',                        # widget root class
        '.pli-profile__avatar',                  # avatar SVG container
    )

    # Balance read is best-effort. The deposit button only renders when
    # balance == 0 (and shows "€0,00"). With a positive balance the deposit
    # button is hidden and the actual balance element lives elsewhere — we
    # don't have a confirmed selector for that yet; will be filled in once
    # we capture HTML from a logged-in session with funds.
    BALANCE_SELECTORS_ZERO = ('.pli-deposit-button',)
    BALANCE_SELECTORS_POSITIVE = (
        # Guesses based on Vue component naming convention. Will iterate.
        '.pli-balance',
        '[class*="balance" i]:visible',
        '#logged-in-menu .opap-base-button',
    )

    # If we land on one of these, login definitely failed.
    LOGIN_ERROR_SELECTORS = (
        '[class*="error" i]:visible',
        '[class*="invalid" i]:visible',
    )

    # --- lifecycle --------------------------------------------------------

    def __init__(self, headless: bool = config.DEFAULT_HEADLESS,
                 reuse_session: bool = True):
        super().__init__(headless=headless)
        self._session: Optional[BrowserSession] = None
        self._reuse_session = reuse_session
        self._storage_path = self._storage_state_path()

    def _storage_state_path(self) -> str:
        return f"{config.SESSION_STATE_DIR}/{self.SLUG}.session_state.json"

    def _ensure_session(self):
        if self._session is not None:
            return
        path = self._storage_path if self._reuse_session else None
        self._session = BrowserSession(headless=self.headless, storage_state_path=path)
        self._session.start()

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

    # --- helpers ----------------------------------------------------------

    def _try_click(self, selectors, timeout_ms: int = 5000) -> bool:
        """Try each selector in order; click the first one that's visible."""
        page = self._session.page
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                loc.click()
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    def _try_fill(self, selectors, value: str, timeout_ms: int = 5000) -> bool:
        page = self._session.page
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state='visible', timeout=timeout_ms)
                loc.fill(value)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    def _any_visible(self, selectors, timeout_ms: int = 5000) -> bool:
        page = self._session.page
        for sel in selectors:
            try:
                page.locator(sel).first.wait_for(state='visible', timeout=timeout_ms)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return False

    def _dismiss_overlays(self, max_passes: int = 3) -> int:
        """Dismiss promo / ad / consent modals that block the page.

        Strategy: press ESC (kills most Vue-mounted modals for free), then
        try each PROMO_DISMISS_SELECTORS. Multiple passes because closing
        one modal sometimes reveals another underneath. Returns the count
        of overlays that were actually dismissed.
        """
        page = self._session.page
        dismissed = 0
        for _ in range(max_passes):
            # ESC is cheap; harmless if no modal is open.
            try:
                page.keyboard.press('Escape')
            except Exception:
                pass

            # Try each close selector; if anything is clickable, click it.
            progress = False
            for sel in self.PROMO_DISMISS_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=500):
                        loc.click(timeout=2000)
                        dismissed += 1
                        progress = True
                        self._session.human_pause()
                        break  # restart the pass — modal stack may have shifted
                except PlaywrightTimeoutError:
                    continue
                except Exception:
                    continue
            if not progress:
                break  # nothing left to dismiss
        if dismissed:
            print(f"[{self.SLUG}] Dismissed {dismissed} overlay(s).")
        return dismissed

    # --- login ------------------------------------------------------------

    def login(self) -> bool:
        creds = get_credentials(self.SLUG)
        if not creds:
            print(f"[{self.SLUG}] No credentials stored. Run: "
                  f"python -m real_betting set-credentials {self.SLUG}")
            return False

        print(f"[{self.SLUG}] Logging in as {mask_username(creds['username'])} "
              f"({'headless' if self.headless else 'headed'} mode)")

        self._ensure_session()
        page = self._session.page

        # 0. If we already have a stored session, check whether it still works.
        if self._reuse_session:
            try:
                page.goto(self.BASE_URL)
                self._session.human_pause()
                if self._any_visible(self.POST_LOGIN_SELECTORS, timeout_ms=3000):
                    print(f"[{self.SLUG}] Reused saved session — already logged in.")
                    self._session.screenshot('login_resumed')
                    return True
            except Exception as e:
                print(f"[{self.SLUG}] Stored session check failed: {e}. Falling through to fresh login.")

        # 1. Navigate to the homepage.
        try:
            page.goto(self.BASE_URL)
        except Exception as e:
            print(f"[{self.SLUG}] Homepage load failed: {e}")
            self._session.dump_failure('login_homepage')
            return False
        self._session.human_pause()

        # 2. Cookie consent — best-effort, not fatal if absent.
        if self._try_click(self.COOKIE_ACCEPT_SELECTORS, timeout_ms=3000):
            print(f"[{self.SLUG}] Accepted cookie banner.")
            self._session.human_pause()

        # 2b. Promotional / ad modals (welcome bonus etc.) sometimes cover
        # the Login button on first load. Dismiss any that appear before
        # we try to interact with the page underneath.
        self._dismiss_overlays()

        # 3. Open the login form (modal or page).
        if not self._try_click(self.LOGIN_OPEN_SELECTORS, timeout_ms=8000):
            # One more dismissal pass in case a slow-loading modal arrived
            # between the first dismissal and now.
            if self._dismiss_overlays() > 0:
                print(f"[{self.SLUG}] Dismissed late-arriving overlay; retrying Login button.")
                if self._try_click(self.LOGIN_OPEN_SELECTORS, timeout_ms=5000):
                    self._session.human_pause()
                    # Fall through to step 4 (credentials).
                else:
                    print(f"[{self.SLUG}] Could not find the Login button after retry. "
                          f"Selectors tried: {self.LOGIN_OPEN_SELECTORS}")
                    self._session.dump_failure('login_button_not_found')
                    return False
            else:
                print(f"[{self.SLUG}] Could not find the Login button. "
                      f"Selectors tried: {self.LOGIN_OPEN_SELECTORS}")
                self._session.dump_failure('login_button_not_found')
                return False
        self._session.human_pause()

        # 4. Fill credentials.
        if not self._try_fill(self.USERNAME_INPUT_SELECTORS, creds['username']):
            print(f"[{self.SLUG}] Username field not found.")
            self._session.dump_failure('login_username_field')
            return False
        self._session.human_pause()
        if not self._try_fill(self.PASSWORD_INPUT_SELECTORS, creds['password']):
            print(f"[{self.SLUG}] Password field not found.")
            self._session.dump_failure('login_password_field')
            return False
        self._session.human_pause()

        # 5. Submit. NO RETRY on auth failure — humans don't brute-force.
        if not self._try_click(self.SUBMIT_SELECTORS):
            print(f"[{self.SLUG}] Submit button not found.")
            self._session.dump_failure('login_submit')
            return False

        # 6. Wait briefly for the post-login state to settle.
        self._session.human_pause()
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except PlaywrightTimeoutError:
            pass  # Some sites never go idle; not fatal.

        # 7. Verify success / failure.
        if self._any_visible(self.POST_LOGIN_SELECTORS, timeout_ms=8000):
            shot = self._session.screenshot('login_success')
            balance = self.get_balance()
            print(f"[{self.SLUG}] Login OK. Screenshot: {shot}. Balance: {balance}")
            if self._reuse_session:
                self._session.save_storage_state(self._storage_path)
                print(f"[{self.SLUG}] Saved session state to {self._storage_path}")
            return True

        if self._any_visible(self.LOGIN_ERROR_SELECTORS, timeout_ms=2000):
            print(f"[{self.SLUG}] Login failed — site reported an error.")
        else:
            print(f"[{self.SLUG}] Login outcome unclear — no post-login element found.")
        self._session.dump_failure('login_post_submit')
        return False

    # --- balance ----------------------------------------------------------

    def get_balance(self) -> Optional[float]:
        """Best-effort balance scrape. Returns euros, or None if not found.

        Tries the zero-balance deposit button first (verified DOM, format
        '€0,00'), then a few guesses at the positive-balance element. The
        latter list is unverified — when we get a logged-in HTML dump with
        funds, update BALANCE_SELECTORS_POSITIVE accordingly.
        """
        if self._session is None:
            return None
        import re

        def _parse_euros(text: str) -> Optional[float]:
            m = re.search(r'([\d.,]+)', text)
            if not m:
                return None
            # Greek/EU format: thousands separator is '.', decimal is ','.
            raw = m.group(1).replace('.', '').replace(',', '.')
            try:
                return float(raw)
            except ValueError:
                return None

        for sel in self.BALANCE_SELECTORS_ZERO + self.BALANCE_SELECTORS_POSITIVE:
            try:
                loc = self._session.page.locator(sel).first
                if loc.is_visible(timeout=500):
                    text = loc.inner_text()
                    val = _parse_euros(text)
                    if val is not None:
                        return val
            except Exception:
                continue
        return None

    # --- stubs (later steps) ----------------------------------------------

    def find_fixtures(self, date: str) -> List[dict]:
        raise NotImplementedError("Fixture discovery lands in step 6b. See FOOTBALL_NEXT_STEPS.md.")

    def get_odds(self, fixture_url: str) -> dict:
        raise NotImplementedError("Odds scrape lands in step 6c. See FOOTBALL_NEXT_STEPS.md.")
