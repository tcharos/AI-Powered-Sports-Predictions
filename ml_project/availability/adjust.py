"""D4 / N3 — Post-model injury adjuster (capped, behind a flag).

Turns the per-absentee importance weights from N2 (`sofifa_importance`) into a
small, capped shift of the 1X2 probability vector. Applied at inference in
`predict_matches.py` *after* the per-league Platt calibration and *before* the
heuristic adjuster — same layering principle as the live red-card modifier
(`LiveAdjuster._apply_red_card_modifier`): a structural game-state signal the
pre-match model can't see, layered on top of the model output and capped, NOT a
trained feature.

Shift model
-----------
Per side, `depletion = Σ reason_weight[class] × importance` (importance is the
marginal OVR lost vs the stand-in, from N2). The more-depleted side is the
disadvantaged one::

    net      = home_depletion − away_depletion        # >0 ⇒ home disadvantaged
    shift    = min(|net| × WEIGHT_AVAIL_PER_OVR, MAX_AVAIL_SHIFT) × efficiency
    taken    = min(shift, p[down])                    # can't take more than held
    p[down] −= taken
    p[up]   += taken × AVAIL_TO_OPPONENT              # toward the opponent
    p[draw] += taken × AVAIL_TO_DRAW                  # ... and the draw
    renormalize

`efficiency` damps the shift HARD in markets that price injuries well (Big-5),
and runs at full strength in the long tail — the D2 lesson that the edge is only
where the market is slow. All magnitudes are conservative defaults to be
calibrated forward in N4 (no historical availability to OOF-fit against), so the
direction is trustworthy but the exact numbers are not yet load-bearing.

Returns the same `(probs, applied, source)` 3-tuple as `apply_platt_1x2` so the
predictor consumes it identically. O/U is intentionally omitted in v1 (a player
out shifts the *winner* clearly but total-goals direction is ambiguous — left to
the xG-pace O/U adjuster and the eventual learned model).
"""

from typing import Optional, Tuple

import numpy as np

_EPS = 1e-9

# Reason → trust weight on the importance. injury/suspension are genuine hits;
# inactive (rotation/long-term/fitness) is mostly already absorbed by the squad;
# doubtful is partial; other contributes nothing.
REASON_WEIGHT = {
    "injury": 1.0,
    "suspension": 1.0,
    "doubtful": 0.5,
    "inactive": 0.2,
    "other": 0.0,
}

# Probability shift per net OVR-point of depletion, before the cap and the
# league-efficiency damp. ~0.008 means a clean "lose one player ~8 OVR above his
# replacement, opponent fully fit" → ~6pp pre-damp; a balanced absence list nets
# to ~0. Conservative; N4 calibrates.
WEIGHT_AVAIL_PER_OVR = 0.008

# Absolute cap on the shift BEFORE the efficiency damp (long-tail ceiling).
MAX_AVAIL_SHIFT = 0.12

# How the taken probability is redistributed off the depleted side.
AVAIL_TO_OPPONENT = 0.65
AVAIL_TO_DRAW = 0.35

# League efficiency damp: Big-5 markets price availability well → shrink hard;
# everything else (the long tail, where the edge lives) runs at full strength.
BIG5_LEAGUES = {
    "ENGLAND: Premier League",
    "SPAIN: LaLiga",
    "ITALY: Serie A",
    "GERMANY: Bundesliga",
    "FRANCE: Ligue 1",
}
BIG5_EFFICIENCY = 0.4
DEFAULT_EFFICIENCY = 1.0


def side_depletion(absentees: Optional[list]) -> float:
    """Σ reason_weight[class] × importance over a side's absentees.

    Unmatched absentees (importance None, no SoFIFA join) and reason classes with
    weight 0 contribute nothing.
    """
    total = 0.0
    for a in (absentees or []):
        imp = a.get("importance")
        if imp is None:
            continue
        total += REASON_WEIGHT.get(a.get("reason_class", "other"), 0.0) * float(imp)
    return total


def league_efficiency(league_name: str) -> float:
    """Efficiency damp for a Flashscore-format league name (canonical, suffix-stripped)."""
    return BIG5_EFFICIENCY if (league_name or "").strip() in BIG5_LEAGUES else DEFAULT_EFFICIENCY


def apply_availability_adjustment(probs_1x2: np.ndarray,
                                  home_avail: Optional[list],
                                  away_avail: Optional[list],
                                  league_name: str,
                                  config: Optional[dict] = None,
                                  *,
                                  enabled: bool = True) -> Tuple[np.ndarray, bool, Optional[str]]:
    """Shift a 1X2 vector [P(H), P(D), P(A)] toward the less-depleted side.

    Returns (probs, applied, source). `applied=False` (raw probs returned) when
    disabled, the input is malformed, or the net depletion is ~0. `source` is the
    league-efficiency tier ('big5' / 'long-tail') when applied, else None — the
    actual shift magnitude is recoverable by the caller from the prob delta.
    """
    if (not enabled or not isinstance(probs_1x2, np.ndarray) or probs_1x2.size != 3):
        return probs_1x2, False, None

    home_dep = side_depletion(home_avail)
    away_dep = side_depletion(away_avail)
    net = home_dep - away_dep
    if abs(net) < _EPS:
        return probs_1x2, False, None

    eff = league_efficiency(league_name)
    shift = min(abs(net) * WEIGHT_AVAIL_PER_OVR, MAX_AVAIL_SHIFT) * eff
    if shift < _EPS:
        return probs_1x2, False, None

    p = probs_1x2.astype(float).copy()
    # net > 0 ⇒ home more depleted ⇒ home disadvantaged (down). idx 0=H, 1=D, 2=A.
    down, up = (0, 2) if net > 0 else (2, 0)
    taken = min(shift, p[down])
    if taken < _EPS:
        return probs_1x2, False, None
    p[down] -= taken
    p[up] += taken * AVAIL_TO_OPPONENT
    p[1] += taken * AVAIL_TO_DRAW

    s = p.sum()
    if s <= 0:
        return probs_1x2, False, None
    p = p / s
    source = "big5" if eff == BIG5_EFFICIENCY else "long-tail"
    return p, True, source
