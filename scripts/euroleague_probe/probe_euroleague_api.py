"""Phase-0 probe of euroleague-api.

Pulls one season of game reports + game stats + quarter scores from the
Euroleague Basketball stats API via the `euroleague-api` PyPI package
(giasemidis/euroleague_api). Writes wide-format CSVs to `data_sets/Euroleague/raw/`.

Usage:
    source venv/bin/activate
    python scripts/euroleague_probe/probe_euroleague_api.py [SEASON]

Where SEASON is the ending year (default 2024 -> 2023-24 season). For EuroCup
swap `competition='E'` -> `'U'` in the constructor calls below.

Why this exists: validates the source's schema and depth before committing to it
as the Phase-1 corpus origin. See EUROLEAGUE_NEXT_STEPS.md, Phase 0 item 2.

Rate-limit posture: the `*_single_season` helpers iterate gamecodes one at a time
(~330 calls per Euroleague season). No `time.sleep` is added here — the API
hasn't pushed back on this pace in initial testing. Add a delay if a future
season hits a throttle.
"""
import os
import sys
import time

from euroleague_api.boxscore_data import BoxScoreData
from euroleague_api.game_stats import GameStats

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data_sets', 'Euroleague', 'raw')
OUT_DIR = os.path.abspath(OUT_DIR)


def main(season: int) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    label = f'{season - 1}-{str(season)[-2:]}'
    print(f'=== Probing season {season} ({label}) — competition code "E" (Euroleague) ===\n')

    gs = GameStats(competition='E')
    bs = BoxScoreData(competition='E')

    targets = [
        ('game_report',     lambda: gs.get_game_report_single_season(season)),
        ('game_stats',      lambda: gs.get_game_stats_single_season(season)),
        ('quarter_scores',  lambda: bs.get_teams_boxscore_quarter_scores_single_season(season)),
    ]
    for i, (name, fn) in enumerate(targets, 1):
        print(f'[{i}/{len(targets)}] {name} …', flush=True)
        t0 = time.time()
        df = fn()
        out = os.path.join(OUT_DIR, f'{season}_{name}.csv')
        df.to_csv(out, index=False)
        print(f'  -> {len(df)} rows, {len(df.columns)} cols, {time.time() - t0:.1f}s')
        print(f'  saved {out} ({os.path.getsize(out):,} bytes)\n', flush=True)

    print('=== done ===')


if __name__ == '__main__':
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    main(season)
