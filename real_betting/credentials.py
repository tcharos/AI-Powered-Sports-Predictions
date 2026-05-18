"""macOS Keychain-backed credential storage.

Each bookmaker stores two items under its keyring service:
- username
- password

Service name: f"{config.KEYCHAIN_SERVICE_PREFIX}:{bookmaker}".

The keyring library auto-selects the macOS Keychain backend when running
on macOS (verified at import time). On other platforms it falls back to
its default backend; we warn rather than fail because secondary dev
machines (Linux CI, etc.) might want to use the same code path.
"""

import sys
from typing import Optional

import keyring
import keyring.errors

from . import config


def _service_name(bookmaker: str) -> str:
    if bookmaker not in config.BOOKMAKERS:
        raise ValueError(f"Unknown bookmaker: {bookmaker!r}. Known: {config.BOOKMAKERS}")
    return f"{config.KEYCHAIN_SERVICE_PREFIX}:{bookmaker}"


def _backend_warning() -> Optional[str]:
    """Return a warning message if the keyring backend isn't macOS Keychain.

    Used by the CLI to surface the situation clearly rather than silently
    storing credentials in a less-secure place.
    """
    backend = type(keyring.get_keyring()).__module__
    if sys.platform == 'darwin' and 'macOS' not in backend:
        return (f"Warning: expected macOS Keychain backend but got {backend}. "
                "Credentials will still be stored, but possibly not where you expect.")
    return None


def set_credentials(bookmaker: str, username: str, password: str) -> None:
    """Store username + password for a bookmaker in the system keyring."""
    service = _service_name(bookmaker)
    keyring.set_password(service, 'username', username)
    keyring.set_password(service, 'password', password)


def get_credentials(bookmaker: str) -> Optional[dict]:
    """Return `{'username': ..., 'password': ...}` or None if either is unset."""
    service = _service_name(bookmaker)
    username = keyring.get_password(service, 'username')
    password = keyring.get_password(service, 'password')
    if not username or not password:
        return None
    return {'username': username, 'password': password}


def has_credentials(bookmaker: str) -> bool:
    """True iff both username and password are present in the keyring."""
    service = _service_name(bookmaker)
    return bool(keyring.get_password(service, 'username')
                and keyring.get_password(service, 'password'))


def delete_credentials(bookmaker: str) -> bool:
    """Remove stored credentials for a bookmaker. Returns True if anything was deleted."""
    service = _service_name(bookmaker)
    deleted = False
    for key in ('username', 'password'):
        try:
            keyring.delete_password(service, key)
            deleted = True
        except keyring.errors.PasswordDeleteError:
            # Already absent — that's fine.
            pass
    return deleted


def mask_username(username: str) -> str:
    """Return a redacted form suitable for logs (e.g., 'cha***@gmail.com')."""
    if not username or '@' not in username:
        return (username[:3] + '***') if username else '***'
    local, domain = username.split('@', 1)
    return f"{local[:3]}***@{domain}"
