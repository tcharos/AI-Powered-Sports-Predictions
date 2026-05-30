"""Walk a bet through a trajectory under a cashout rule."""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import config
from .trajectories import Snapshot


@dataclass
class Outcome:
    bet_id: str
    lane: str
    bet_type: str
    rule_name: str
    triggered: bool
    trigger_minute: Optional[int]
    cashout_value: Optional[float]
    baseline_pnl: float
    rule_pnl: float
    delta: float
    note: str = ''  # 'unsupported_ou', 'bad_selection', etc.


def baseline_pnl_from_result(bet: dict) -> float:
    """Compute baseline P/L from first principles (stake + odds + result).

    Doesn't trust the on-disk `pnl` field — older slips store None for `pnl`
    and put the value in `profit` instead. From-principles is also our
    null_rule self-validation hook.
    """
    stake = float(bet.get('stake_units', 0))
    odds = float(bet.get('odds', 0))
    result = bet.get('result', '')
    status = bet.get('status', '')
    if result == 'WON' or status == 'WON':
        return round(stake * (odds - 1), 2)
    if result == 'LOST' or status == 'LOST':
        return round(-stake, 2)
    # VOID, OPEN, anything else: 0
    return 0.0


def selection_key(bet: dict) -> Optional[str]:
    """Map a bet's selection to a LiveAdjuster prob key. None if unsupported."""
    if bet.get('type') == '1X2':
        return config.SELECTION_MAP_1X2.get(bet.get('selection'))
    if bet.get('type') == 'O/U':
        return config.SELECTION_MAP_OU.get(bet.get('selection'))
    return None


def fair_cashout(stake: float, odds: float, adj_prob: float, haircut: Optional[float] = None) -> float:
    haircut = haircut if haircut is not None else config.HOUSE_HAIRCUT
    return stake * odds * adj_prob * haircut


def walk_bet(bet: dict,
             pre_probs: Dict[str, float],
             trajectory: List[Snapshot],
             rule: Callable,
             adjuster,
             rule_name: Optional[str] = None,
             haircut: Optional[float] = None) -> Outcome:
    """Walk one bet through one trajectory under one rule. Returns Outcome.

    If the rule never fires, `rule_pnl == baseline_pnl` and `delta == 0`.
    O/U bets short-circuit (no adjuster support yet — Phase 5).
    """
    bet_id = bet.get('_bet_id', '?')
    lane = bet.get('lane', 'value')
    bet_type = bet.get('type', '1X2')
    stake = float(bet.get('stake_units', 0))
    odds = float(bet.get('odds', 0))
    rule_name = rule_name or getattr(rule, '__name__', 'unnamed')
    baseline = baseline_pnl_from_result(bet)

    def _untriggered(note: str = '') -> Outcome:
        return Outcome(
            bet_id=bet_id, lane=lane, bet_type=bet_type, rule_name=rule_name,
            triggered=False, trigger_minute=None, cashout_value=None,
            baseline_pnl=baseline, rule_pnl=baseline, delta=0.0, note=note,
        )

    sel_key = selection_key(bet)
    if sel_key is None:
        return _untriggered('bad_selection')

    if bet_type == '1X2':
        adj_fn = adjuster.adjust_probabilities
    elif bet_type == 'O/U':
        adj_fn = adjuster.adjust_ou_probabilities
    else:
        return _untriggered(f'unsupported_type:{bet_type}')

    # Rolling per-snapshot history of THIS bet's walk so far (prior snapshots
    # only — the current one isn't appended until after the rule is consulted).
    # Lets trajectory-aware rules (e.g. momentum_fade) compute pace/velocity.
    # Pre-existing rules ignore ctx['history'], so this is backward-compatible.
    history: List[dict] = []
    for snap in trajectory:
        adj = adj_fn(pre_probs, snap.stats, snap.minute, snap.score)
        adj_prob = adj.get(sel_key, 0.0)
        fc = fair_cashout(stake, odds, adj_prob, haircut)
        ctx = {
            'minute': snap.minute, 'score': snap.score, 'stats': snap.stats,
            'adj_probs': adj, 'pre_probs': pre_probs, 'sel_key': sel_key,
            'history': history,
        }
        if rule(bet, ctx, fc):
            rule_pnl = round(fc - stake, 2)
            return Outcome(
                bet_id=bet_id, lane=lane, bet_type=bet_type, rule_name=rule_name,
                triggered=True, trigger_minute=snap.minute, cashout_value=round(fc, 2),
                baseline_pnl=baseline, rule_pnl=rule_pnl,
                delta=round(rule_pnl - baseline, 2),
            )
        # Record this snapshot for the NEXT iteration's pace computation.
        history.append({
            'minute': snap.minute, 'score': snap.score, 'stats': snap.stats,
            'adj_prob': adj_prob, 'fair_cashout': fc,
        })

    return _untriggered()
