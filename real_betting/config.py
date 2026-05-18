"""Paths, timing constants, and bookmaker registry for the real-betting module.

Per-bookmaker URLs and selectors live in `bookmakers/<name>.py`.
"""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# All real-betting artifacts (screenshots, fixture dumps, session state,
# failure DOM dumps) go here. Covered by the existing output/ gitignore.
OUTPUT_DIR        = os.path.join(PROJECT_ROOT, 'output', 'real_betting')
FAILURES_DIR      = os.path.join(OUTPUT_DIR, 'failures')
SESSION_STATE_DIR = os.path.join(OUTPUT_DIR, 'sessions')

# Single-session lockfile — prevents concurrent runs from the same machine
# from triggering bookmaker anti-bot heuristics.
LOCKFILE = os.path.join(OUTPUT_DIR, '.session.lock')

# Anti-bot timing. Randomised delay between any two browser actions.
ACTION_DELAY_MIN_MS = 800
ACTION_DELAY_MAX_MS = 2500

# Page-load / navigation timeouts. Generous because Cloudflare challenges
# can add seconds even to the happy path.
PAGE_LOAD_TIMEOUT_MS = 45_000
NAVIGATION_TIMEOUT_MS = 30_000

# Headed mode is the default through step 7 of NEXT_STEPS.md. Step 8 flips
# this to optional-headless. Override per-invocation via --headless flag.
DEFAULT_HEADLESS = False

# Keychain service prefix. Each bookmaker uses
# f"{KEYCHAIN_SERVICE_PREFIX}:{bookmaker_slug}" as its keyring service name.
KEYCHAIN_SERVICE_PREFIX = 'sports_predictor:real_betting'

# Known bookmaker slugs. Adding one = drop a new file in bookmakers/ and
# register it here.
BOOKMAKERS = (
    'pamestoixima',
)


def ensure_output_dirs():
    """Create the output dirs if missing. Idempotent."""
    for d in (OUTPUT_DIR, FAILURES_DIR, SESSION_STATE_DIR):
        os.makedirs(d, exist_ok=True)
