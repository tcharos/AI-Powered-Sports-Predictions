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


RULES = {
    'null': null_rule,
    'lock_in_profit': lock_in_profit,
    'stop_loss': stop_loss,
    'late_drift': late_drift,
}
