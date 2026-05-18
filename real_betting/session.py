"""Playwright session manager — single-session lockfile + cookie persistence.

Step 1 stub: interface defined, locking helper implemented (it's pure
filesystem so it's safe), but Playwright launch is gated behind step 3.

The lockfile is the most important anti-bot mitigation in the skeleton:
two concurrent automated logins from the same machine is the kind of
pattern that gets accounts reviewed. Acquire the lock for the duration
of any browser session and release it in a finally block.
"""

import errno
import os
from contextlib import contextmanager

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
    """Playwright browser session wrapper. Stub for step 1.

    Concrete implementation lands in step 3 (login). For now this exists
    only so the abstract Bookmaker base class can reference it.
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._browser = None
        self._context = None
        self._page = None

    def start(self):
        raise NotImplementedError(
            "Browser launch not implemented yet. See NEXT_STEPS.md → "
            "'Real betting integration' step 3."
        )

    def close(self):
        # Safe no-op until step 3 implements start().
        return None
