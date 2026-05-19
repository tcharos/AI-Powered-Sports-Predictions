"""Apply per-league Platt calibration at inference time (Phase C4).

`predict_matches.py` calls into here right after `model.predict_proba()`,
before the heuristic adjuster. The flow becomes:

    raw_probs  →  apply_platt  →  heuristic_adjuster  →  final probs

Lookup chain for a Flashscore league name:
1. Exact match in LEAGUE_ALIASES → calibration key.
2. Strip "COUNTRY: " prefix and try direct match on calibration keys.
3. No match → return raw probs unchanged.

`use_league_calibration=False` (config flag) bypasses lookup entirely.
"""

import json
import os
from functools import lru_cache
from typing import Optional, Tuple

import numpy as np

from .league_aliases import LEAGUE_ALIASES

_EPS = 1e-6


def _safe_logit(p):
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


@lru_cache(maxsize=4)
def load_calibration_data(path: str) -> dict:
    """Load `data_sets/league_calibration.json`. Cached.

    Returns {} if the file is missing — callers should treat that as
    "no calibration available, use raw".
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_calibration_key(league_name: str, calibration_data: dict) -> Optional[str]:
    """Map a Flashscore-format league name to a key in `calibration_data['leagues']`.

    Only the explicit `LEAGUE_ALIASES` mapping is used. A strip-prefix fallback
    is tempting but unsafe — e.g., "GREECE: Super League" would otherwise map to
    "Super League" (Chinese SL) via name collision. Unmapped leagues fall back
    to raw probabilities at inference; add an entry to LEAGUE_ALIASES to enable
    calibration for a new league.
    """
    if not league_name or not calibration_data:
        return None
    leagues = calibration_data.get('leagues', {})
    key = LEAGUE_ALIASES.get(league_name)
    if key and key in leagues:
        return key
    return None


def apply_platt_1x2(raw_probs: np.ndarray,
                    league_name: str,
                    calibration_data: dict,
                    enabled: bool = True) -> Tuple[np.ndarray, bool, Optional[str]]:
    """Apply per-class Platt to a 1X2 probability vector [P(H), P(D), P(A)].

    Returns (probs, applied, source_mode).
    `applied=False` means we returned raw probs (no calibrator or disabled).
    `source_mode` is 'full' or 'minimal' when applied; None otherwise.
    """
    if not enabled or not isinstance(raw_probs, np.ndarray) or raw_probs.size != 3:
        return raw_probs, False, None
    key = resolve_calibration_key(league_name, calibration_data)
    if not key:
        return raw_probs, False, None
    entry = calibration_data['leagues'].get(key, {}).get('oneXtwo')
    if not entry:
        return raw_probs, False, None
    platt = entry['platt']
    cal = np.zeros(3)
    for i, c in enumerate(('home', 'draw', 'away')):
        a = platt[c]['a']
        b = platt[c]['b']
        cal[i] = _sigmoid(a * _safe_logit(raw_probs[i]) + b)
    s = cal.sum()
    if s <= 0:
        return raw_probs, False, None
    cal = cal / s
    return cal, True, entry.get('source_mode')


def apply_platt_ou(raw_probs: np.ndarray,
                   league_name: str,
                   calibration_data: dict,
                   enabled: bool = True) -> Tuple[np.ndarray, bool, Optional[str]]:
    """Apply binary Platt to O/U probabilities [P(under), P(over)].

    Returns (probs, applied, source_mode).
    """
    if not enabled or not isinstance(raw_probs, np.ndarray) or raw_probs.size != 2:
        return raw_probs, False, None
    key = resolve_calibration_key(league_name, calibration_data)
    if not key:
        return raw_probs, False, None
    entry = calibration_data['leagues'].get(key, {}).get('ou')
    if not entry:
        return raw_probs, False, None
    a = entry['platt']['over']['a']
    b = entry['platt']['over']['b']
    cal_over = float(_sigmoid(a * _safe_logit(raw_probs[1]) + b))
    cal_over = max(_EPS, min(1.0 - _EPS, cal_over))
    return np.array([1.0 - cal_over, cal_over]), True, entry.get('source_mode')
