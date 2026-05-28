"""Multi-season historical fetcher for Euroleague + EuroCup via `euroleague-api`.

Phase-1 corpus-seed builder. Iterates (competition, season) pairs and writes
per-season wide-format CSVs to `data_sets/Euroleague/raw/` named
`{COMP}_{SEASON}_{endpoint}.csv` (COMP ∈ {'E', 'U'}; SEASON = ending year).

Defaults: fetch seasons 2017 (= 2016-17) through 2025 (= 2024-25), both
competitions, two endpoints (game_report + game_stats). Quarter scores are
skipped — they're nice-to-have, not used by the NBA pipeline; rerun the
single-season probe (`probe_euroleague_api.py`) if you want them.

Behaviour:
- **Idempotent**: a per-(comp, season, endpoint) CSV already on disk with
  non-zero size is treated as done and skipped. Makes the script restartable
  after a kill / network hiccup; safe to re-run.
- **Per-(comp, season) error isolation**: if one fetch raises, log + continue.
  No partial-corpus risk because each completed fetch is saved before moving on.
- **Line-buffered stdout** so progress shows up in tail / Read of the
  background-task log file.

Usage:
    source venv/bin/activate
    python scripts/euroleague_probe/fetch_seasons.py
    # or override bounds / competitions:
    python scripts/euroleague_probe/fetch_seasons.py --start 2017 --end 2025 --comps E,U
"""
import argparse
import os
import sys
import time
import traceback

from euroleague_api.game_stats import GameStats


OUT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'data_sets', 'Euroleague', 'raw')
)

COMP_NAMES = {'E': 'Euroleague', 'U': 'EuroCup'}


def _endpoint_targets(season, gs):
    """Two endpoints worth promoting to the corpus seed. Returns (name, callable) pairs."""
    return [
        ('game_report', lambda: gs.get_game_report_single_season(season)),
        ('game_stats',  lambda: gs.get_game_stats_single_season(season)),
    ]


def fetch_one(competition, season):
    """Fetch every endpoint for a single (competition, season). Skip files already on disk."""
    gs = GameStats(competition=competition)
    for ep_name, ep_fn in _endpoint_targets(season, gs):
        out = os.path.join(OUT_DIR, f'{competition}_{season}_{ep_name}.csv')
        if os.path.exists(out) and os.path.getsize(out) > 0:
            print(f'  [skip] {os.path.basename(out)} already on disk ({os.path.getsize(out):,} bytes)', flush=True)
            continue
        t0 = time.time()
        df = ep_fn()
        df.to_csv(out, index=False)
        elapsed = time.time() - t0
        print(
            f'  [done] {os.path.basename(out)}: {len(df)} rows × {len(df.columns)} cols, '
            f'{os.path.getsize(out):,} bytes, {elapsed:.1f}s',
            flush=True,
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start', type=int, default=2017,
                   help='First season to fetch (ending year). Default 2017 = 2016-17.')
    p.add_argument('--end', type=int, default=2025,
                   help='Last season to fetch, inclusive. Default 2025 = 2024-25.')
    p.add_argument('--comps', default='E,U',
                   help='Comma-separated competition codes. Default "E,U".')
    args = p.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    comps = [c.strip() for c in args.comps.split(',') if c.strip()]
    seasons = list(range(args.start, args.end + 1))

    total = len(comps) * len(seasons)
    print(f'=== Euroleague multi-season fetch ===', flush=True)
    print(f'comps:   {comps} ({[COMP_NAMES.get(c, c) for c in comps]})', flush=True)
    print(f'seasons: {args.start}..{args.end} ({len(seasons)} seasons)', flush=True)
    print(f'output:  {OUT_DIR}', flush=True)
    print(f'plan:    {total} (comp, season) pairs × 2 endpoints = {total * 2} CSVs', flush=True)
    print('', flush=True)

    t_run = time.time()
    failures = []
    done = 0
    for comp in comps:
        for season in seasons:
            done += 1
            label = f'{season - 1}-{str(season)[-2:]}'
            print(
                f'[{done}/{total}] {COMP_NAMES.get(comp, comp)} {label} '
                f'(comp={comp}, season={season})',
                flush=True,
            )
            try:
                fetch_one(comp, season)
            except Exception as e:  # pylint: disable=broad-except
                msg = f'{comp}/{season}: {type(e).__name__}: {e}'
                print(f'  [ERROR] {msg}', flush=True)
                traceback.print_exc()
                failures.append(msg)
            print('', flush=True)

    elapsed = time.time() - t_run
    print('=== summary ===', flush=True)
    print(f'completed:  {done}/{total} (comp, season) pairs in {elapsed/60:.1f} min', flush=True)
    print(f'failures:   {len(failures)}', flush=True)
    for f in failures:
        print(f'  - {f}', flush=True)
    print('=== done ===', flush=True)
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
