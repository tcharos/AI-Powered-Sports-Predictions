"""Playwright session manager — single-session lockfile + cookie persistence.

The lockfile is the most important anti-bot mitigation in the skeleton:
two concurrent automated logins from the same machine is the kind of
pattern that gets accounts reviewed. Acquire the lock for the duration
of any browser session and release it in a finally block.

BrowserSession launches Chromium with a Greek locale and Europe/Athens
timezone (matches a real user in the target market), disables the
`AutomationControlled` blink feature, and offers a `human_pause()`
helper for realistic delays. Headed mode is the default through step 7;
flip to headless only after step 8 validation.
"""

import datetime
import errno
import os
import random
import time
from contextlib import contextmanager
from typing import Optional

from playwright.sync_api import (
    Browser, BrowserContext, Page, Playwright, sync_playwright,
)

from . import config


@contextmanager
def session_lock():
    """Acquire an exclusive lockfile for the duration of a browser session.

    Uses O_EXCL so it's atomic across processes. Raises RuntimeError if
    another session is already running.
    """
    config.ensure_output_dirs()
    try:
        fd = os.open(config.LOCKFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise RuntimeError(
                f"Another real-betting session is already running (lockfile: "
                f"{config.LOCKFILE}). If you're sure no other run is active, "
                f"delete the lockfile manually."
            )
        raise
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        try:
            os.unlink(config.LOCKFILE)
        except OSError:
            pass


class BrowserSession:
    """Playwright Chromium wrapper tuned for bookmaker scraping.

    Usage:
        with session_lock(), BrowserSession(headless=False) as s:
            s.page.goto(...)
            ...
    """

    def __init__(self, headless: bool = config.DEFAULT_HEADLESS,
                 storage_state_path: Optional[str] = None):
        self.headless = headless
        self.storage_state_path = storage_state_path
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # --- context manager --------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # --- lifecycle --------------------------------------------------------

    def start(self):
        if self._pw is not None:
            return  # idempotent
        self._pw = sync_playwright().start()

        # Anti-fingerprint nudges. Not bulletproof, but cheap.
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
        ]
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=launch_args,
        )

        # Greek locale + Athens timezone matches a real Pamestoixima user.
        context_kwargs = dict(
            viewport={'width': 1280, 'height': 800},
            locale='el-GR',
            timezone_id='Europe/Athens',
        )
        if self.storage_state_path and os.path.exists(self.storage_state_path):
            context_kwargs['storage_state'] = self.storage_state_path

        self._context = self._browser.new_context(**context_kwargs)
        self._context.set_default_timeout(config.PAGE_LOAD_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(config.NAVIGATION_TIMEOUT_MS)

        # Hide the navigator.webdriver flag (most basic bot signal).
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        self._page = self._context.new_page()

    def close(self):
        for attr in ('_page', '_context', '_browser'):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    # --- accessors --------------------------------------------------------

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession not started. Call .start() or use as context manager.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserSession not started.")
        return self._context

    # --- helpers ----------------------------------------------------------

    def human_pause(self):
        """Sleep a randomised ACTION_DELAY_MIN_MS..ACTION_DELAY_MAX_MS."""
        delay_ms = random.randint(config.ACTION_DELAY_MIN_MS, config.ACTION_DELAY_MAX_MS)
        time.sleep(delay_ms / 1000.0)

    def screenshot(self, label: str) -> str:
        """Save a full-page screenshot. Returns the path."""
        config.ensure_output_dirs()
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        path = os.path.join(config.OUTPUT_DIR, f"{ts}_{label}.png")
        self.page.screenshot(path=path, full_page=True)
        return path

    def dump_failure(self, label: str) -> str:
        """Save a screenshot + HTML dump to FAILURES_DIR. Returns the dir path."""
        config.ensure_output_dirs()
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        base = os.path.join(config.FAILURES_DIR, f"{ts}_{label}")
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
        return base

    def save_storage_state(self, path: str) -> None:
        """Persist cookies + localStorage to a file for reuse next run."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.context.storage_state(path=path)
