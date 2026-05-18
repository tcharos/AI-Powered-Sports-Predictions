"""Generate (or load) per-minute trajectories of match state."""

import json
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config


@dataclass
class Snapshot:
    minute: int
    score: str
    stats: Dict[str, float] = field(default_factory=dict)


def _sample_goal_minute(rng: random.Random) -> int:
    r = rng.random()
    cumul = 0.0
    for (start, end), w in config.GOAL_TIME_WEIGHTS:
        cumul += w
        if r <= cumul:
            return rng.randint(start, end)
    return 90


def _stats_at(minute: int, home_goals: int, away_goals: int) -> Dict[str, float]:
    """Plausible mid-match stats consistent with the final goal count."""
    frac = minute / 90.0
    xg_h = home_goals * config.XG_OVERSHOOT * frac
    xg_a = away_goals * config.XG_OVERSHOOT * frac
    total = xg_h + xg_a
    if total > 0.01:
        # Squash possession toward 50% so even one-sided games stay ±15.
        poss_h_raw = xg_h / total
        poss_h = 50 + (poss_h_raw - 0.5) * 30
    else:
        poss_h = 50
    return {
        'xg_home':         round(xg_h, 2),
        'xg_away':         round(xg_a, 2),
        'shots_home':      int(xg_h * config.SHOTS_PER_XG),
        'shots_away':      int(xg_a * config.SHOTS_PER_XG),
        'possession_home': round(poss_h),
        'possession_away': round(100 - poss_h),
    }


class SyntheticTrajectory:
    """Build a plausible per-minute trajectory from just the final score.

    Goal timings are sampled from a 1st-half/2nd-half weighting. Stats are
    interpolated linearly. Use `seed` to make a single trajectory reproducible
    or pass distinct seeds for Monte Carlo paths.
    """

    @staticmethod
    def generate(home_goals: int,
                 away_goals: int,
                 seed: Optional[int] = None,
                 tick: Optional[int] = None) -> List[Snapshot]:
        tick = tick or config.DEFAULT_TICK
        rng = random.Random(seed)
        home_minutes = sorted(_sample_goal_minute(rng) for _ in range(home_goals))
        away_minutes = sorted(_sample_goal_minute(rng) for _ in range(away_goals))

        snapshots = []
        for m in range(0, 91, tick):
            h = sum(1 for gm in home_minutes if gm <= m)
            a = sum(1 for gm in away_minutes if gm <= m)
            snapshots.append(Snapshot(
                minute=m,
                score=f"{h}-{a}",
                stats=_stats_at(m, home_goals, away_goals),
            ))
        return snapshots


class RealTrajectory:
    """Load a real per-tick trajectory from `live_history_<date>.jsonl`."""

    @staticmethod
    def from_jsonl(jsonl_path: str, match_id: str) -> Optional[List[Snapshot]]:
        if not match_id or not os.path.exists(jsonl_path):
            return None
        snapshots = []
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get('match_id') != match_id:
                    continue
                snapshots.append(Snapshot(
                    minute=d.get('minute', 0),
                    score=d.get('score', '0-0'),
                    stats=d.get('stats', {}),
                ))
        if not snapshots:
            return None
        snapshots.sort(key=lambda s: s.minute)
        return snapshots
