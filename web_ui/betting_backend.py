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

    Example: 2026-05-20:sc_freiburg:aston_villa:o_u:over_2_5

    **Lane is intentionally NOT part of the bet_id.** The bet_id
    identifies a *conceptual wager* (date + match + market + selection)
    rather than a specific storage record. Multiple lanes (value /
    conviction / model) can hold the same conceptual wager — they all
    share the same bet_id. Cashout and void operations cascade across
    every lane's record with the matching bet_id, so a single click
    settles the whole wager. See execute_cashout / void_bet below
    and docs/LIVE_BETTING_TRANSITION.md "Bet ID format".
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

    @abstractmethod
    def void_bet(self, bet: dict) -> bool:
        """Mark an OPEN bet as VOID (e.g. match postponed / cancelled
        and won't settle). Stake is refunded to the lane bankroll in
        virtual mode; live mode no-ops the bankroll (the bookmaker
        already auto-refunded). Returns True on success, False if the
        bet isn't found or isn't OPEN.

        Phase 3 schema: bet['status'] = 'VOID', bet['result'] = 'VOID',
        bet['pnl'] = 0.0. process_bet_verification leaves VOID bets
        alone, so once marked they're terminal.
        """


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
        """Cash out EVERY OPEN bet sharing this bet_id (or, for
        pre-Phase-7 records lacking bet_id, every OPEN bet matching the
        (match, type, selection) tuple). Cascades across lanes so a
        single click settles a wager held in multiple lanes.

        For each OPEN sibling: stamps Phase 3 schema fields
        (status='CASHED_OUT', cashout_amount, cashout_profit,
        cashout_timestamp, pnl) and credits THAT lane's bankroll with
        its own cashout_amount (each lane's amount is computed
        independently — different stakes → different payouts even at
        the same odds × adj_prob).

        Returns True if at least one bet was cashed out, False if no
        OPEN siblings remained (already-cascaded or stale state).
        """
        # NB: don't precondition on the representative bet's own
        # cashout_amount — if it's already CASHED_OUT (status != OPEN),
        # get_cashout_amount returns None and we'd bail out before the
        # cascade runs. The cascade loop below calls get_cashout_amount
        # on each OPEN sibling individually; if none of them can be
        # priced we'll naturally end with cashed_count == 0 and return
        # False. Cheap precondition kept: a live_match must exist.
        if live_match is None or live_match.get('message'):
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

        # Identify all sibling bets for this conceptual wager.
        target_id = bet.get('bet_id')
        target_tuple = (bet.get('match'), bet.get('type'),
                        bet.get('selection'))

        def _matches(b):
            if target_id and b.get('bet_id') == target_id:
                return True
            return (b.get('match'), b.get('type'),
                    b.get('selection')) == target_tuple

        from sports_config import update_bankroll
        now_iso = datetime.datetime.now().isoformat(timespec='seconds')
        cashed_count = 0
        for b in slip.get('bets', []):
            if not _matches(b):
                continue
            if b.get('status') != 'OPEN':
                continue
            # Per-bet cashout amount — different lanes can have
            # different stakes → different payouts at the same odds.
            per_amount = self.get_cashout_amount(b, live_match)
            if per_amount is None:
                continue
            stake = float(b.get('stake_units', 0) or 0)
            per_profit = round(float(per_amount) - stake, 2)
            b['status'] = 'CASHED_OUT'
            b['result'] = 'CASHED_OUT'
            b['cashout_amount'] = float(per_amount)
            b['cashout_profit'] = per_profit
            b['cashout_timestamp'] = now_iso
            b['pnl'] = per_profit
            # Credit the bet's own lane.
            lane = b.get('lane', 'value')
            update_bankroll(self.SPORT, float(per_amount), lane=lane)
            cashed_count += 1

        if cashed_count == 0:
            return False  # nothing OPEN matched; nothing to write

        with open(slip_path, 'w') as f:
            json.dump(slip, f, indent=4)
        return True

    # ---- void --------------------------------------------------------

    def void_bet(self, bet: dict) -> bool:
        """Cascade-void: mark every OPEN bet sharing this bet_id (or
        match/type/selection tuple) as VOID and refund each lane's
        stake. Used for postponed / cancelled matches that will never
        settle. Idempotent — already-terminal siblings are skipped.

        Returns True if at least one bet was voided.
        """
        # Date resolution (same logic as execute_cashout).
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

        target_id = bet.get('bet_id')
        target_tuple = (bet.get('match'), bet.get('type'),
                        bet.get('selection'))

        def _matches(b):
            if target_id and b.get('bet_id') == target_id:
                return True
            return (b.get('match'), b.get('type'),
                    b.get('selection')) == target_tuple

        from sports_config import update_bankroll
        now_iso = datetime.datetime.now().isoformat(timespec='seconds')
        voided_count = 0
        for b in slip.get('bets', []):
            if not _matches(b):
                continue
            if b.get('status') != 'OPEN':
                continue
            stake = float(b.get('stake_units', 0) or 0)
            b['status'] = 'VOID'
            b['result'] = 'VOID'
            b['pnl'] = 0.0
            b['voided_timestamp'] = now_iso
            lane = b.get('lane', 'value')
            update_bankroll(self.SPORT, stake, lane=lane)
            voided_count += 1

        if voided_count == 0:
            return False

        with open(slip_path, 'w') as f:
            json.dump(slip, f, indent=4)
        return True

    # ---- cancel slip -------------------------------------------------

    def cancel_slip(self, date: str) -> tuple[bool, str]:
        """Cancel an entire virtual slip when every bet on it is still
        OPEN. Refunds each bet's stake to its lane bankroll, marks each
        bet VOID with a `cancelled_timestamp`, and flips the slip to
        CLOSED so it can be archived through the usual path.

        Returns (True, message) on success, (False, reason) on refusal.
        Refuses if any bet has already moved past OPEN (WON / LOST /
        VOID / CASHED_OUT) — those slips must be settled normally.
        """
        if not isinstance(date, str) or len(date) != 10:
            return False, 'Invalid slip date.'

        slip_path = os.path.join(self.output_dir, f'bets_{date}.json')
        if not os.path.exists(slip_path):
            return False, 'Slip not found.'
        try:
            with open(slip_path) as f:
                slip = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False, 'Slip could not be read.'

        bets = slip.get('bets', [])
        if not bets:
            return False, 'Slip has no bets.'

        non_open = [b for b in bets if b.get('status', 'OPEN') != 'OPEN']
        if non_open:
            return False, (
                f'{len(non_open)} bet(s) already settled or cashed out — '
                'cancel is only allowed when every bet is OPEN.')

        from sports_config import update_bankroll
        now_iso = datetime.datetime.now().isoformat(timespec='seconds')
        refunded_by_lane: dict[str, float] = {}
        for b in bets:
            stake = float(b.get('stake_units', 0) or 0)
            lane = b.get('lane', 'value')
            b['status'] = 'VOID'
            b['result'] = 'VOID'
            b['pnl'] = 0.0
            b['voided_timestamp'] = now_iso
            b['cancelled_timestamp'] = now_iso
            update_bankroll(self.SPORT, stake, lane=lane)
            refunded_by_lane[lane] = refunded_by_lane.get(lane, 0.0) + stake

        slip['status'] = 'CLOSED'
        slip['cancelled_timestamp'] = now_iso
        with open(slip_path, 'w') as f:
            json.dump(slip, f, indent=4)

        parts = ', '.join(f'{lane} €{amt:.2f}'
                          for lane, amt in sorted(refunded_by_lane.items()))
        total = sum(refunded_by_lane.values())
        return True, f'Refunded €{total:.2f} ({parts}).'

    # ---- settlement --------------------------------------------------

    def settle_bets(self, date: str, verification_data: object) -> dict:
        """Thin wrapper around ml_project.resolve_daily_bets.resolve_all_bets,
        which is the canonical multi-slip settlement implementation.

        `verification_data` here is expected to be the path to the
        scraped results JSON (output/matches_<date>.json) — that's the
        input resolve_all_bets actually needs, not the verification
        CSV. A verification CSV can be passed via the `verification_file`
        keyword if the caller has one.
        """
        # Lazy import to avoid module-load coupling.
        import sys, os as _os
        _ml_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                 '..', 'ml_project')
        if _ml_path not in sys.path:
            sys.path.insert(0, _ml_path)
        from resolve_daily_bets import resolve_all_bets
        resolve_all_bets(self.output_dir, results_file=verification_data)
        return {'note': 'see resolve_daily_bets logs for details'}


class NbaBettingBackend(VirtualBettingBackend):
    """NBA-flavoured ``VirtualBettingBackend`` — same machinery, ``SPORT='nba'``.

    Subclass (vs constructor parameter) by design: keeps the football class
    literally unchanged byte-for-byte, so reading or grepping for
    ``VirtualBettingBackend(output_dir=OUTPUT_DIR)`` still finds only football
    callers. The shared infrastructure (`make_bet_id`, `place_bet`, `execute_cashout`,
    `void_bet`, `settle_bets`, bankroll updates via `sports_config`) is reused
    verbatim — only the ``SPORT`` slug differs, which is what routes the
    bankroll mutations / slip storage to ``sports.nba`` in `betting_config.json`
    and to whichever ``output_dir`` the caller passes (``output_basketball`` for
    NBA, vs ``output`` for football). Storage is therefore fully slug-separated:
    an NBA bet can never touch a football bankroll or slip.

    Use as::

        backend = NbaBettingBackend(output_dir='output_basketball')

    in NBA blueprint routes; football continues to call
    ``VirtualBettingBackend(output_dir=OUTPUT_DIR)`` exactly as before.
    """

    SPORT = 'nba'


class EuroleagueBettingBackend(VirtualBettingBackend):
    """Euroleague/EuroCup-flavoured ``VirtualBettingBackend`` — ``SPORT='euroleague'``.

    Same one-line-subclass pattern as ``NbaBettingBackend``: the football class
    is untouched, all machinery is reused verbatim, and only the ``SPORT`` slug
    differs — which routes bankroll mutations / slip storage to
    ``sports.euroleague`` in ``betting_config.json`` and to the ``output_dir``
    the caller passes (``output_euroleague``). Storage is fully slug-separated:
    a Euroleague bet can never touch a football or NBA bankroll/slip.

    Use as ``EuroleagueBettingBackend(output_dir='output_euroleague')`` in the
    euroleague blueprint routes.
    """

    SPORT = 'euroleague'


# ---- Pamestoixima stub (Phase 9) ------------------------------------------

class PamestoiximaBackend(BettingBackend):
    """Phase 9 placeholder. Every method raises NotImplementedError
    pointing at the transition doc + the bookmaker notes.

    When real-betting integration matures (FOOTBALL_NEXT_STEPS.md Pamestoixima
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

    def void_bet(self, bet):
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
