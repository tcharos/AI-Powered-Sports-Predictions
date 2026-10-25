"""Build and cache the engineered training frame on the FRESH corpus.

Same code path as ModelTrainer.prepare_data(), pointed at
output/experiments/_fresh_history (corpus + the 2026/27 season), with one
difference: prepare_data() normally overwrites data_sets/elo_ratings.json —
the file the live predictor reads — so that write is redirected into
output/experiments/ here. Nothing deployed is modified.

Also keeps every row (no 2020 cut) of the ELO history so the replay can take
as-of-date ratings.

Usage: python3 scripts/audit_prepare_frames.py [--fixed]
Writes output/experiments/_prepared_fresh.pkl (or _prepared_fixed.pkl with --fixed)
"""
import builtins
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import EXP_DIR, PROJECT_ROOT, fresh_history_dir  # noqa: E402
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'ml_project'))
import train_model  # noqa: E402

ELO_LIVE = 'data_sets/elo_ratings.json'
ELO_SANDBOX = os.path.join(EXP_DIR, '_elo_fresh.json')


def _guarded_open(path, mode='r', *a, **kw):
    if 'w' in mode and os.path.normpath(str(path)).endswith(os.path.normpath(ELO_LIVE)):
        path = ELO_SANDBOX
    return builtins.open(path, mode, *a, **kw)


FIXED_DIR = os.path.join(EXP_DIR, '_fixed_history')


def fixed_history_dir():
    """The fresh corpus with the league code restored on old season files.

    Every main-league file before 2024/25 was written pre-renamed and without
    a `Div` column, so DataLoader gives those 108k rows league=NaN, hence
    league_cat=NaN, hence ModelTrainer._training_rows drops all of them. This
    writes copies with `Div` injected from the filename (MAIN_MAPPING
    inverse) and symlinks everything else.
    """
    import glob
    import re
    import pandas as pd
    from update_football_data import MAIN_MAPPING
    prefix_to_code = {v: k for k, v in MAIN_MAPPING.items()}
    src = fresh_history_dir()
    os.makedirs(FIXED_DIR, exist_ok=True)
    for p in glob.glob(os.path.join(src, '*.csv')):
        name = os.path.basename(p)
        dst = os.path.join(FIXED_DIR, name)
        if os.path.lexists(dst):
            continue
        m = re.match(r'^(.*)_\d{2}-\d{2}\.csv$', name)
        code = prefix_to_code.get(m.group(1)) if m else None
        head = pd.read_csv(p, nrows=0, encoding_errors='ignore').columns
        if code and 'Div' not in head and 'league' not in head:
            d = pd.read_csv(p, encoding_errors='ignore')
            d.insert(0, 'Div', code)
            d.to_csv(dst, index=False)
        else:
            os.symlink(os.path.realpath(p), dst)
    return FIXED_DIR


def prepare(history_dir, buffer_date, training_start):
    """ModelTrainer.prepare_data() with configurable dates (its are hardcoded
    2019/2020) and the elo_ratings.json write redirected."""
    import gc
    import json
    import pandas as pd
    from data_loader import DataLoader
    from elo_engine import EloTracker
    from feature_engineering import FeatureEngineer
    df = DataLoader(history_dir).load_historical_data()
    elo = df[['date', 'home_team', 'away_team', 'FTHG', 'FTAG']].copy().sort_values('date')
    elo = EloTracker().process_history(elo)
    df_fe = df[df['date'] >= pd.Timestamp(buffer_date)].copy()
    del df
    gc.collect()
    df_fe = pd.merge(df_fe, elo[['date', 'home_team', 'H_elo']], on=['date', 'home_team'], how='left')
    df_fe = pd.merge(df_fe, elo[['date', 'away_team', 'A_elo']], on=['date', 'away_team'], how='left')
    df_fe = FeatureEngineer().add_rolling_features(df_fe)
    out = df_fe[df_fe['date'] >= pd.Timestamp(training_start)].copy()
    out['target_1x2'] = out['FTR'].map({'H': 0, 'D': 1, 'A': 2})
    out['target_draw'] = (out['FTR'] == 'D').astype(int)
    out['total_goals'] = out['FTHG'] + out['FTAG']
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--fixed', action='store_true',
                    help='restore league codes on old files and start in 2012')
    args = ap.parse_args()
    os.chdir(PROJECT_ROOT)
    if args.fixed:
        df = prepare(fixed_history_dir(), '2011-01-01', '2012-01-01')
        out = os.path.join(EXP_DIR, '_prepared_fixed.pkl')
    else:
        hist = fresh_history_dir()
        train_model.open = _guarded_open       # module-global shadows the builtin
        df = train_model.ModelTrainer(hist).prepare_data()
        out = os.path.join(EXP_DIR, '_prepared_fresh.pkl')
    df.to_pickle(out)
    print(f'cached {len(df)} rows -> {out}  (max date {df.date.max()})')


if __name__ == '__main__':
    main()
