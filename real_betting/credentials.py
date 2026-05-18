"""macOS Keychain-backed credential storage.

Each bookmaker stores two items under its keyring service:
- username
- password

Service name: f"{config.KEYCHAIN_SERVICE_PREFIX}:{bookmaker}".

Step 1 stub: the public interface is defined; the keyring round-trip is
wired but the actual `keyring` import + Keychain access is gated behind
step 2. Until then, every accessor raises NotImplementedError with a
pointer to the checklist.
"""

from typing import Optional

from . import config


def _service_name(bookmaker: str) -> str:
    if bookmaker not in config.BOOKMAKERS:
        raise ValueError(f"Unknown bookmaker: {bookmaker!r}. Known: {config.BOOKMAKERS}")
    return f"{config.KEYCHAIN_SERVICE_PREFIX}:{bookmaker}"


def set_credentials(bookmaker: str, username: str, password: str) -> None:
    """Store username + password for a bookmaker in the system keyring."""
    raise NotImplementedError(
        "Credentials storage not implemented yet. See NEXT_STEPS.md → "
        "'Real betting integration' step 2."
    )


def get_credentials(bookmaker: str) -> Optional[dict]:
    """Return `{'username': ..., 'password': ...}` or None if unset."""
    raise NotImplementedError(
        "Credentials retrieval not implemented yet. See NEXT_STEPS.md → "
        "'Real betting integration' step 2."
    )


def has_credentials(bookmaker: str) -> bool:
    """True iff both username and password are present in the keyring."""
    raise NotImplementedError(
        "Credential check not implemented yet. See NEXT_STEPS.md → "
        "'Real betting integration' step 2."
    )


def mask_username(username: str) -> str:
    """Return a redacted form suitable for logs (e.g., 'cha***@gmail.com')."""
    if not username or '@' not in username:
        # Non-email username: keep first 3 chars
        return (username[:3] + '***') if username else '***'
    local, domain = username.split('@', 1)
    return f"{local[:3]}***@{domain}"
