"""Concrete bookmaker implementations. One file per bookmaker."""

from typing import Type

from ..bookmaker_base import Bookmaker
from .pamestoixima import Pamestoixima


_REGISTRY = {
    Pamestoixima.SLUG: Pamestoixima,
}


def get_bookmaker_class(slug: str) -> Type[Bookmaker]:
    """Return the Bookmaker subclass registered under `slug`."""
    if slug not in _REGISTRY:
        raise ValueError(f"No bookmaker registered for slug {slug!r}. "
                         f"Known: {sorted(_REGISTRY)}")
    return _REGISTRY[slug]
