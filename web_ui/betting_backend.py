"""Betting backend abstraction.

Provides a single interface for the operations the betting UI needs:
balance read, bet placement, cashout, settlement. Selected by URL
prefix at request time:

    /football/*       → VirtualBettingBackend (today's behaviour)
    /football/live/*  → PamestoiximaBackend (Phase 9, currently stubbed)

This file is the contract. The full design (atomicity, schema, bet ID
format, refactor checklist for Phase 9) lives in
`docs/LIVE_BETTING_TRANSITION.md` — read it before changing anything
here.

Current state (Phase 7, Option C):
- VirtualBettingBackend implements `get_cashout_amount` and
  `execute_cashout` fully. The other three methods are thin wrappers
  around existing code paths so the contract is in place but routes
  don't need to be refactored yet.
- PamestoiximaBackend raises NotImplementedError on every method
  with a pointer at the transition doc.
"""

from __future__ import annotations

import datetime
import json
import os
from abc import ABC, abstractmethod
from typing import Optional


# ---- bet ID helper (canonical, mode-agnostic) -----------------------------

def _slug(s: str) -> str:
    """Lowercased, non-alphanumeric → underscore, trimmed."""
    if not s:
        return ''
    out = ''.join(c if c.isalnum() else '_' for c in str(s).lower())
    # Collapse consecutive underscores + strip leading/trailing.
    while '__' in out:
        out = out.replace('__', '_')
    return out.strip('_')


def make_bet_id(date: str, home: str, away: str,
                bet_type: str, selection: str) -> str:
    """Canonical bet ID format. Stable across virtual and live modes.

    `<date>:<home>:<away>:<type>:<selection>` (all slugified except date).

    Example: 2026-05-20:sc_freiburg:aston_villa:ou:over_2_5

    Per docs/LIVE_BETTING_TRANSITION.md "Bet ID format" section.
    """
    return f"{date}:{_slug(home)}:{_slug(away)}:{_slug(bet_type)}:{_slug(selection)}"


# ---- shared constants (fair-value cashout) --------------------------------

# Selection → adjusted-probs key. Duplicated from app.py's
# `_SELECTION_TO_PROB_KEY` so the backend is self-contained. If you
# change one, change the other.
_SELECTION_TO_PROB_KEY = {
    '1': 'home', 1: 'home',
    'X': 'draw', 'x': 'draw',
    '2': 'away', 2: 'away',
    'Over 2.5':  'over',  'Over': 'over',
    'Under 2.5': 'under', 'Under': 'under',
}

# Haircut applied to fair-value cashout (virtual mode only — real
# bookmaker quotes their own number). Matches the existing dashboard
# display formula.
_CASHOUT_HOUSE_HAIRCUT = 0.95


# ---- ABC ------------------------------------------------------------------

class BettingBackend(ABC):
    """v1 contract — see docs/LIVE_BETTING_TRANSITION.md §3.

    All methods may raise on hard failure. Soft-failure indicators
    (e.g. "no cashout available for this bet right now") use
    Optional[T] returning None.
    """

    @abstractmethod
    def get_balance(self, lane: Optional[str] = None) -> float: ...

    @abstractmethod
    def place_bet(self, date: str, lane: str, match: str, match_id: str,
                  type: str, selection: str, odds: float, stake_eur: float,
                  meta: Optional[dict] = None) -> str: ...

    @abstractmethod
    def get_cashout_amount(self, bet: dict,
                           live_match: Optional[dict]) -> Optional[float]: ...

    @abstractmethod
    def execute_cashout(self, bet: dict,
                        live_match: Optional[dict]) -> bool: ...

    @abstractmethod
    def settle_bets(self, date: str, verification_data: object) -> dict: ...


# ---- virtual implementation -----------------------------------------------

class VirtualBettingBackend(BettingBackend):
    """Internal bankroll + local slip files. Today's behaviour.

    Reads/writes:
      - data_sets/betting_config.json (via sports_config helpers)
      - output/bets_<date>.json (the slip)
    """

    SPORT = 'football'

    def __init__(self, output_dir: str):
        self.output_dir = output_dir

    # ---- balance -----------------------------------------------------

    def get_balance(self, lane: Optional[str] = None) -> float:
        """Thin wrapper around sports_config — kept here so callers
        can move to backend.get_balance() over time without app.py
        churn (per transition doc §4 refactor table)."""
        from sports_config import get_bankroll, sport_total
        if lane is None:
            return sport_total(self.SPORT)
        return get_bankroll(self.SPORT, lane=lane)

    # ---- placement ---------------------------------------------------

    def place_bet(self, date: str, lane: str, match: str, match_id: str,
                  type: str, selection: str, odds: float, stake_eur: float,
                  meta: Optional[dict] = None) -> str:
        """Construct a bet record and append to output/bets_<date>.json.
        Stamps bet_id, mode='virtual', and Phase 3 schema fields.
        Debits the lane bankroll. Returns the bet_id.

        NOTE: this is currently only used by the cashout-test path
        (smoke tests) — the production /place_bets route in app.py
        still builds its own slips. Phase 9 will refactor that route
        to call this method per the transition doc §4.
        """
        from sports_config import update_bankroll

        bet_id = make_bet_id(date, *_split_match(match), type, selection)
        home, away = _split_match(match)
        record = {
            'lane': lane,
            'mode': 'virtual',
            'bet_id': bet_id,
            'status': 'OPEN',
            'match': match,
            'home': home,
            'away': away,
            'match_id': match_id,
            'type': type,
            'selection': selection,
            'odds': odds,
            'stake_units': stake_eur,
            'date': date,
        }
        if meta:
            record.update(meta)

        slip_path = os.path.join(self.output_dir, f'bets_{date}.json')
        if os.path.exists(slip_path):
            with open(slip_path) as f:
                slip = json.load(f)
        else:
            slip = {'date': date, 'bets': [], 'count': 0,
                    'total_stake': 0.0, 'status': 'OPEN'}

        slip['bets'].append(record)
        slip['count'] = len(slip['bets'])
        slip['total_stake'] = round(
            sum(float(b.get('stake_units', 0)) for b in slip['bets']), 2)
        with open(slip_path, 'w') as f:
            json.dump(slip, f, indent=4)

        update_bankroll(self.SPORT, -float(stake_eur), lane=lane)
        return bet_id

    # ---- cashout (FULLY IMPLEMENTED) --------------------------------

    def get_cashout_amount(self, bet: dict,
                           live_match: Optional[dict]) -> Optional[float]:
        """Fair-value cashout estimate: `stake × odds × adj_prob × 0.95`.

        Returns None if the match isn't live yet (no live_match data)
        or if we can't map the selection to a probability key.
        Refuses to estimate for already-settled bets.
        """
        if live_match is None or live_match.get('message'):
            return None
        status = bet.get('status', 'OPEN')
        if status != 'OPEN':
            return None

        stake = float(bet.get('stake_units', 0) or 0)
        odds = float(bet.get('odds', 0) or 0)
        if stake <= 0 or odds <= 0:
            return None

        selection = bet.get('selection')
        prob_key = _SELECTION_TO_PROB_KEY.get(selection)
        if not prob_key:
            return None

        bet_type = bet.get('type', '1X2')
        if bet_type == '1X2' and prob_key in ('home', 'draw', 'away'):
            adj = live_match.get('adj_probs', {}) or {}
        elif bet_type == 'O/U' and prob_key in ('over', 'under'):
            adj = live_match.get('adj_ou_probs', {}) or {}
        else:
            return None

        adj_prob = float(adj.get(prob_key, 0) or 0)
        if adj_prob <= 0:
            return None
        return round(stake * odds * adj_prob * _CASHOUT_HOUSE_HAIRCUT, 2)

    def execute_cashout(self, bet: dict,
                        live_match: Optional[dict]) -> bool:
        """Commit the cashout. Per Phase 3 schema:
          - bet['status'] = 'CASHED_OUT'
          - bet['cashout_amount'] = <fair-value EUR>
          - bet['cashout_profit'] = amount - stake
          - bet['cashout_timestamp'] = ISO now
          - bet['pnl'] = cashout_profit (for aggregation parity)

        Credits the lane bankroll by the cashout amount, persists the
        slip, returns True on success. Returns False if the bet can't
        be cashed out (already settled, no live data, etc.).
        """
        amount = self.get_cashout_amount(bet, live_match)
        if amount is None:
            return False

        # Resolve the slip file's date. Order of preference:
        #   1. bet_id prefix — canonical YYYY-MM-DD by construction.
        #   2. bet['date'] — may be "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
        #      (Flashscore's format includes kickoff time); slice to 10.
        #   3. Today as last resort.
        date = None
        bid = bet.get('bet_id') or ''
        if ':' in bid:
            candidate = bid.split(':', 1)[0]
            if len(candidate) == 10:
                date = candidate
        if date is None:
            raw = bet.get('date') or bet.get('match_date') or ''
            if isinstance(raw, str) and len(raw) >= 10:
                date = raw[:10]
        if date is None:
            date = _date_from_bet(bet) or datetime.date.today().isoformat()
        slip_path = os.path.join(self.output_dir, f'bets_{date}.json')
        if not os.path.exists(slip_path):
            return False
        try:
            with open(slip_path) as f:
                slip = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False

        # Locate the bet inside the slip — match by bet_id when present,
        # fall back to (match, type, selection) tuple for pre-Phase-7
        # bets that don't carry a bet_id yet.
        target_id = bet.get('bet_id')
        target_tuple = (bet.get('match'), bet.get('type'), bet.get('selection'))
        for b in slip.get('bets', []):
            if target_id and b.get('bet_id') == target_id:
                hit = b
                break
            if (b.get('match'), b.get('type'),
                    b.get('selection')) == target_tuple:
                hit = b
                break
        else:
            return False

        if hit.get('status') != 'OPEN':
            return False  # idempotent guard

        stake = float(hit.get('stake_units', 0) or 0)
        cashout_profit = round(float(amount) - stake, 2)

        hit['status'] = 'CASHED_OUT'
        hit['result'] = 'CASHED_OUT'
        hit['cashout_amount'] = float(amount)
        hit['cashout_profit'] = cashout_profit
        hit['cashout_timestamp'] = datetime.datetime.now().isoformat(timespec='seconds')
        hit['pnl'] = cashout_profit

        with open(slip_path, 'w') as f:
            json.dump(slip, f, indent=4)

        # Credit the lane bankroll with the cashout amount.
        from sports_config import update_bankroll
        lane = hit.get('lane', 'value')
        update_bankroll(self.SPORT, float(amount), lane=lane)
        return True

    # ---- settlement --------------------------------------------------

    def settle_bets(self, date: str, verification_data: object) -> dict:
        """Thin wrapper around app.process_bet_verification.

        Today's verification flow runs through that function directly;
        moving the call site through this method is a Phase 9 refactor
        (transition doc §4). For now this method exists to satisfy the
        contract; production code does not call it yet.
        """
        # Lazy import to avoid circular dep at module load time.
        from app import process_bet_verification
        # verification_data is expected to be a path to the CSV.
        process_bet_verification(verification_data)
        return {'note': 'see app.process_bet_verification logs for details'}


# ---- Pamestoixima stub (Phase 9) ------------------------------------------

class PamestoiximaBackend(BettingBackend):
    """Phase 9 placeholder. Every method raises NotImplementedError
    pointing at the transition doc + the bookmaker notes.

    When real-betting integration matures (NEXT_STEPS.md Pamestoixima
    Step 9 re-evaluation), these methods will:
      - get_balance: call Pamestoixima.get_balance() (already
                     implemented for read-only login flow).
      - place_bet: drive browser per real_betting/PAMESTOIXIMA_NOTES.md
                   placement checklist.
      - get_cashout_amount: scrape the bookmaker's actual offer.
      - execute_cashout: click their Cash Out button via the same
                         browser session pattern.
      - settle_bets: same behaviour as virtual (compute P/L from match
                     result; no bookmaker-history sync — decided
                     2026-05-20, see transition doc §3 settle_bets).
    """

    _NOT_READY = (
        "PamestoiximaBackend is not implemented yet. See "
        "docs/LIVE_BETTING_TRANSITION.md and "
        "real_betting/PAMESTOIXIMA_NOTES.md. "
        "Live betting is gated behind betting_config 'live_betting_enabled'."
    )

    def get_balance(self, lane=None):
        raise NotImplementedError(self._NOT_READY)

    def place_bet(self, *args, **kwargs):
        raise NotImplementedError(self._NOT_READY)

    def get_cashout_amount(self, bet, live_match):
        raise NotImplementedError(self._NOT_READY)

    def execute_cashout(self, bet, live_match):
        raise NotImplementedError(self._NOT_READY)

    def settle_bets(self, date, verification_data):
        raise NotImplementedError(self._NOT_READY)


# ---- helpers (private) ----------------------------------------------------

def _split_match(match_str: str) -> tuple:
    """Best-effort split of 'Home vs Away' into ('Home', 'Away')."""
    if not match_str or ' vs ' not in match_str:
        return (match_str or '', '')
    parts = match_str.split(' vs ', 1)
    return (parts[0].strip(), parts[1].strip())


def _date_from_bet(bet: dict) -> Optional[str]:
    """Try to extract YYYY-MM-DD from a bet record's various date fields."""
    for k in ('date', 'match_date', 'kickoff'):
        v = bet.get(k)
        if isinstance(v, str) and len(v) >= 10:
            return v[:10]
    return None
