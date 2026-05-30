"""Candidate cashout rules.

A rule is `(bet: dict, ctx: dict, fair_cashout: float) -> bool`.
Returns True at the FIRST snapshot it fires; simulator short-circuits.
"""


def null_rule(bet, ctx, fair_cashout):
    """Never fires. Used to validate that baseline_pnl matches actual slip P/L."""
    return False


def lock_in_profit(bet, ctx, fair_cashout):
    """Cash out if we'd lock in ≥50% gain even right now."""
    stake = float(bet.get('stake_units', 0))
    if stake <= 0:
        return False
    return fair_cashout / stake >= 1.5


def stop_loss(bet, ctx, fair_cashout):
    """Cash out if our selection's adj prob has collapsed below 0.20 AND we
    aren't currently winning. Floors the loss; never triggers on a winning bet."""
    sel = ctx['sel_key']
    adj_prob = ctx['adj_probs'].get(sel, 0.0)
    if adj_prob >= 0.20:
        return False
    try:
        h, a = map(int, ctx['score'].split('-'))
    except (ValueError, AttributeError):
        return False
    if sel == 'home' and h > a:
        return False
    if sel == 'away' and a > h:
        return False
    if sel == 'draw' and h == a:
        return False
    return True


def late_drift(bet, ctx, fair_cashout):
    """Cash out if our selection's adj prob fell ≥0.15 below pre-match
    AND we're past minute 60. Model has lost confidence; bail before full time."""
    if ctx['minute'] < 60:
        return False
    sel = ctx['sel_key']
    drift = ctx['adj_probs'].get(sel, 0.0) - ctx['pre_probs'].get(sel, 0.0)
    return drift <= -0.15


# --- Pace / momentum (EXPERIMENTAL stub — FOOTBALL_NEXT_STEPS phase 8d) -------
# First-cut thresholds, NOT tuned. The whole point of the stub is to start
# accruing backtest deltas as live_history grows; do not promote into
# `_cashout_decision()` until it clears the gate in phase 8d.
_MOM_MIN_MINUTE = 30        # ignore early-game noise (mirrors auto-cashout floor)
_MOM_MIN_PROFIT_RATIO = 1.0  # only act while in (unrealized) profit: fc/stake > 1
_MOM_FADE_VELOCITY = -0.010  # adj-prob slope ≤ -1.0 pt/min over the recent window
_MOM_WINDOW_MIN = 8          # look back ~one refresh interval (snapshots are ~10m)


def momentum_fade(bet, ctx, fair_cashout):
    """EXPERIMENTAL (unvalidated): lock in profit when the selection's LIVE
    probability is *falling fast* over the recent window — i.e. the game's pace
    has turned against us — even before the static `stop_loss` / `late_drift`
    *level* bars trip. Where those rules read the level (prob now, drift-from-pre),
    this reads the *slope* (Δprob/Δminute), the signal a single snapshot misses.

    Needs ≥1 prior snapshot (`ctx['history']`, supplied by the simulator). Acts
    only while in profit (so it's a profit-protection lock, not a panic stop) and
    after minute 30. Thresholds (`_MOM_*`) are first-cut guesses — see phase 8d.

    NOTE: slope on `adj_prob` carries a time-decay confound near full time (the
    LiveAdjuster bleeds probability toward the clock regardless of momentum); the
    intended next iteration computes the slope on a RAW pace stat (opponent
    xG-per-minute) instead, which is decay-free. Kept on adj_prob here for the
    cheapest first pass.
    """
    if ctx['minute'] < _MOM_MIN_MINUTE:
        return False
    stake = float(bet.get('stake_units', 0))
    if stake <= 0 or fair_cashout / stake <= _MOM_MIN_PROFIT_RATIO:
        return False  # not in profit → nothing to lock in
    history = ctx.get('history') or []
    if not history:
        return False
    sel = ctx['sel_key']
    now_prob = ctx['adj_probs'].get(sel, 0.0)
    # Earliest snapshot still inside the look-back window (else the most recent).
    ref = next((h for h in history if ctx['minute'] - h['minute'] <= _MOM_WINDOW_MIN),
               history[-1])
    dt = ctx['minute'] - ref['minute']
    if dt <= 0:
        return False
    velocity = (now_prob - ref['adj_prob']) / dt  # prob per minute
    return velocity <= _MOM_FADE_VELOCITY


RULES = {
    'null': null_rule,
    'lock_in_profit': lock_in_profit,
    'stop_loss': stop_loss,
    'late_drift': late_drift,
    'momentum_fade': momentum_fade,  # EXPERIMENTAL — phase 8d
}
