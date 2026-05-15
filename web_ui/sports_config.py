"""
Sport-aware betting config access.

The on-disk config (`data_sets/betting_config.json`) groups every tunable
under `sports.<slug>` so each sport has its own bankroll and strategy
parameters. This module is the only place that knows the schema — every
route should go through these helpers.
"""

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'data_sets', 'betting_config.json')

# Defaults that get merged with the per-sport overrides on read. Keeps
# callers free from None-checks for keys that haven't been set yet.
DEFAULT_SPORT_CONFIG = {
    'current_bankroll': 0.0,
    'initial_bankroll': 0.0,
    'min_confidence': 0.45,
    'stake_multiplier': 0.4,
    'min_stake_eur': 2.0,
    'max_stake_pct': 0.03,
    'max_daily_exposure_pct': 0.10,
    'conviction_min_confidence': 0.65,
    'conviction_min_odds': 1.40,
    'conviction_stake_pct': 0.005,
}


def _load():
    if not os.path.exists(CONFIG_PATH):
        return {'sports': {}}
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {'sports': {}}


def _save(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=4)


def get_sport_config(sport_slug):
    """Return the full per-sport config (defaults + overrides)."""
    cfg = _load()
    sport_cfg = cfg.get('sports', {}).get(sport_slug, {})
    out = dict(DEFAULT_SPORT_CONFIG)
    out.update(sport_cfg)
    return out


def get_bankroll(sport_slug):
    """Return current bankroll for a sport (0.0 if sport unknown)."""
    return get_sport_config(sport_slug).get('current_bankroll', 0.0)


def update_bankroll(sport_slug, delta):
    """Add `delta` to a sport's current_bankroll, return the new value.

    Use negative delta when placing bets (debit), positive when settling
    wins / void refunds (credit).
    """
    cfg = _load()
    sports = cfg.setdefault('sports', {})
    sport = sports.setdefault(sport_slug, {})
    new_value = round(sport.get('current_bankroll', 0.0) + delta, 2)
    sport['current_bankroll'] = new_value
    _save(cfg)
    return new_value


def all_bankrolls():
    """Return {sport_slug: current_bankroll} for every sport in config."""
    cfg = _load()
    return {k: v.get('current_bankroll', 0.0)
            for k, v in cfg.get('sports', {}).items()}


def total_bankroll():
    """Sum bankroll across all sports — useful for the navbar/landing."""
    return round(sum(all_bankrolls().values()), 2)
