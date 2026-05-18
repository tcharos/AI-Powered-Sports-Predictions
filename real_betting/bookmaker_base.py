"""Abstract Bookmaker interface.

Each concrete bookmaker lives in `bookmakers/<slug>.py` and subclasses
`Bookmaker`. The base class handles session lifecycle plumbing; subclasses
fill in URLs, selectors, and login-form details.

In step 1 (module skeleton) only the interface is defined. Concrete
implementations land in steps 3+.
"""

from abc import ABC, abstractmethod
from typing import List, Optional


class Bookmaker(ABC):
    """Read-only bookmaker integration.

    The interface deliberately excludes bet placement, settlement, and
    withdrawal — those are out of scope for the dormant phase.
    """

    #: Lowercase slug matching the filename in bookmakers/ and the entry
    #: in config.BOOKMAKERS. Required override.
    SLUG: str = ''

    #: Display name (used in logs and CLI output).
    DISPLAY_NAME: str = ''

    #: Root URL (e.g., 'https://www.pamestoixima.gr').
    BASE_URL: str = ''

    def __init__(self, headless: bool = False):
        if not self.SLUG:
            raise TypeError(f"{type(self).__name__} must define SLUG")
        self.headless = headless

    # --- lifecycle --------------------------------------------------------

    @abstractmethod
    def login(self) -> bool:
        """Authenticate. Return True on success.

        Implementations must:
        - load credentials via real_betting.credentials.get_credentials
        - drive the bookmaker's login form
        - verify post-login by reading a known authenticated-only element
          (typically the visible balance)
        - take a screenshot to output/real_betting/<ts>_login.png
        - on failure, save a DOM dump to output/real_betting/failures/

        Must not retry on bad credentials — one attempt only, by design.
        """

    @abstractmethod
    def get_balance(self) -> Optional[float]:
        """Return the visible account balance, or None if it can't be read."""

    @abstractmethod
    def find_fixtures(self, date: str) -> List[dict]:
        """Return today's football fixtures.

        Each fixture is a dict with at least: `home`, `away`, `league`,
        `kickoff`, `fixture_url`, `market_ids`. Date format: YYYY-MM-DD.
        """

    @abstractmethod
    def get_odds(self, fixture_url: str) -> dict:
        """Return current 1X2 + O/U 2.5 odds for one fixture.

        Returns: `{'1x2': {'home': ..., 'draw': ..., 'away': ...},
                   'ou25': {'over': ..., 'under': ...}}`.
        """

    @abstractmethod
    def close(self) -> None:
        """Tear down the Playwright session. Safe to call from contexts."""

    # --- context manager support so callers can use `with` ----------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            pass  # Closing should never mask the original exception.
        return False
