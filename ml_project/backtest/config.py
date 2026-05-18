"""Backtest tuning constants. Kept tiny so they're easy to find."""

# Bookmaker's "house haircut" on theoretical fair-value cashout. 0.95 = bookie
# offers 95% of the math-fair price. Real bookies haircut 5-15%.
HOUSE_HAIRCUT = 0.95

# Sample the trajectory every N minutes. 5 is plenty for rule evaluation.
DEFAULT_TICK = 5

# Monte Carlo paths per bet on synthetic mode. Higher = tighter CI, slower.
DEFAULT_MC_PATHS = 50

# Goal time distribution for synthetic trajectories. ~52% of football goals
# happen in the second half (empirically well-established).
GOAL_TIME_WEIGHTS = [
    ((1, 45),  0.48),
    ((46, 90), 0.52),
]

# xG typically overshoots actual goals by ~10-20%.
XG_OVERSHOOT = 1.15

# Shots ≈ xG × this. Crude but the adjuster only cares about ratios.
SHOTS_PER_XG = 6

# 1X2 selection (from predictions CSV / bets JSON) → LiveAdjuster key.
# Stored as int by pandas in the bet JSON, hence both forms accepted.
SELECTION_MAP_1X2 = {
    '1': 'home', 1: 'home',
    'X': 'draw', 'x': 'draw',
    '2': 'away', 2: 'away',
}
