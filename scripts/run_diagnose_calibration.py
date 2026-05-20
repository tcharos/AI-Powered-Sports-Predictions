"""Diagnose per-league miscalibration (Phase C1).

Reuses ModelTrainer.prepare_data() so feature engineering matches the
trainer's pipeline exactly. Runs the same 5-fold TimeSeriesSplit the
trainer uses, collects out-of-fold predictions for every match, then
aggregates per-league calibration metrics.

Two modes:
    (default)            — full feature set (matches train_model.py). 20 leagues
                            survive dropna; reflects calibration of the production
                            model on leagues whose CSVs include shot/corner data.
    --minimal-features   — strips shot/corner-dependent features. Every league
                            survives, including thin-data leagues like Veikkausliiga
                            (Finland), Argentina, Brazil. Reflects a hypothetical
                            simpler model; used to source calibration for leagues
                            the full-features run can't see.

Outputs CSV + Markdown to output/calibration/, filenames include the mode tag.

Usage:
    PYTHONPATH=$(pwd):$(pwd)/ml_project python3 scripts/run_diagnose_calibration.py
    PYTHONPATH=$(pwd):$(pwd)/ml_project python3 scripts/run_diagnose_calibration.py --minimal-features
"""

import argparse
import datetime
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'ml_project'))

from train_model import ModelTrainer
from ml_project.calibration.diagnose import (
    out_of_fold_predictions,
    per_league_metrics,
    write_markdown_report,
)

OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'output', 'calibration')
N_SPLITS = 5
MIN_N_PER_LEAGUE = 100

# Features that require shot/corner data — present only in football-data.co.uk's
# "standard" league CSVs (England, Germany, Italy, Spain, France, etc.). Extra
# leagues (Finland, Argentina, Brazil, MLS, Japan, etc.) ship without HS/AS/HC/AC
# so any rolling feature derived from those columns comes out NaN and the row
# dies in dropna. Strip these in --minimal-features mode.
SHOT_CORNER_FEATURES = {
    'H_form_sf', 'H_form_sa', 'H_form_cf', 'H_form_ca',
    'A_form_sf', 'A_form_sa', 'A_form_cf', 'A_form_ca',
    'H_home_sf', 'H_home_sa',
    'A_away_sf', 'A_away_sa',
}


def _load_params(name: str, default: dict) -> dict:
    p = os.path.join(PROJECT_ROOT, 'models', f'best_params_{name}.json')
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return default


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--minimal-features', action='store_true',
                    help='Strip shot/corner-dependent features so thin-data leagues '
                         '(Finland, Argentina, Brazil, MLS, etc.) survive dropna.')
    args = ap.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    mode_tag = 'minimal' if args.minimal_features else 'full'

    print(f'Mode: {mode_tag} features')
    print('Preparing training data (this may take a few minutes)...')
    trainer = ModelTrainer(data_dir=os.path.join(PROJECT_ROOT, 'data_sets', 'MatchHistory'))
    df_train = trainer.prepare_data()
    print(f'Prepared {len(df_train)} rows across {df_train["league"].nunique()} leagues.')

    common = trainer.common_features
    if args.minimal_features:
        common = [f for f in common if f not in SHOT_CORNER_FEATURES]
        print(f'Stripped {len(SHOT_CORNER_FEATURES)} shot/corner features; '
              f'common feature count: {len(common)}')

    # --- 1X2 OOF ---
    print('\n=== 1X2: running 5-fold OOF predictions ===')
    features_1x2 = ['B365H', 'B365D', 'B365A'] + common
    features_1x2 = list(dict.fromkeys(features_1x2))
    params_1x2 = _load_params('1x2', {
        'objective': 'multi:softprob', 'num_class': 3,
        'n_estimators': 100, 'learning_rate': 0.1, 'max_depth': 5,
        'eval_metric': 'mlogloss', 'early_stopping_rounds': 10,
        'tree_method': 'hist', 'enable_categorical': True,
    })
    df_1x2_input = df_train.dropna(subset=features_1x2 + ['target_1x2']).copy()
    df_1x2_input = df_1x2_input.sort_values('date').reset_index(drop=True)
    oof_1x2, _ = out_of_fold_predictions(df_1x2_input, features_1x2, 'target_1x2',
                                          params_1x2, n_splits=N_SPLITS)
    df_1x2_metrics = per_league_metrics(df_1x2_input, oof_1x2, 'target_1x2',
                                         outcome_labels=['home', 'draw', 'away'],
                                         min_n=MIN_N_PER_LEAGUE)
    print(f'Computed 1X2 metrics for {len(df_1x2_metrics)} leagues.')

    # --- O/U OOF ---
    print('\n=== O/U 2.5: running 5-fold OOF predictions ===')
    features_ou = common.copy()
    features_ou = list(dict.fromkeys(features_ou))
    params_ou = _load_params('ou', {
        'objective': 'binary:logistic',
        'n_estimators': 100, 'learning_rate': 0.1, 'max_depth': 5,
        'eval_metric': 'logloss', 'early_stopping_rounds': 10,
        'tree_method': 'hist', 'enable_categorical': True,
    })
    df_ou_input = df_train.dropna(subset=features_ou + ['target_ou']).copy()
    df_ou_input = df_ou_input.sort_values('date').reset_index(drop=True)
    oof_ou, _ = out_of_fold_predictions(df_ou_input, features_ou, 'target_ou',
                                         params_ou, n_splits=N_SPLITS)
    df_ou_metrics = per_league_metrics(df_ou_input, oof_ou, 'target_ou',
                                        outcome_labels=['under', 'over'],
                                        min_n=MIN_N_PER_LEAGUE)
    print(f'Computed O/U metrics for {len(df_ou_metrics)} leagues.')

    # --- write outputs ---
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    csv_1x2 = os.path.join(OUTPUT_DIR, f'diagnose_{ts}_{mode_tag}_1x2.csv')
    csv_ou  = os.path.join(OUTPUT_DIR, f'diagnose_{ts}_{mode_tag}_ou.csv')
    md_path = os.path.join(OUTPUT_DIR, f'diagnose_{ts}_{mode_tag}.md')

    df_1x2_metrics.to_csv(csv_1x2, index=False)
    df_ou_metrics.to_csv(csv_ou, index=False)
    write_markdown_report(
        md_path, df_1x2_metrics, df_ou_metrics,
        meta={
            'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
            'source': 'data_sets/MatchHistory/',
            'mode': f'{mode_tag} features',
            'n_splits': N_SPLITS,
            'min_n': MIN_N_PER_LEAGUE,
        },
    )

    print(f'\nSaved:\n  {csv_1x2}\n  {csv_ou}\n  {md_path}')

    # Prune older diagnose outputs — keep only the most recent set for
    # this mode. Each diagnose run is mode-tagged (full / minimal), so
    # we glob with the mode in the pattern and let the other mode's
    # files survive.
    for _patt in (f'diagnose_*_{mode_tag}_1x2.csv',
                  f'diagnose_*_{mode_tag}_ou.csv',
                  f'diagnose_*_{mode_tag}.md'):
        _old = sorted(glob.glob(os.path.join(OUTPUT_DIR, _patt)))[:-1]
        for _f in _old:
            try: os.remove(_f)
            except OSError: pass
    print(f'Pruned older diagnose_*_{mode_tag}_* outputs (kept latest set).')

    if not df_1x2_metrics.empty:
        print('\n=== Top 5 leagues by 1X2 miscalibration (severity) ===')
        cols = ['league', 'n', 'ece', 'max_abs_delta', 'severity',
                'delta_home', 'delta_draw', 'delta_away']
        print(df_1x2_metrics[cols].head().to_string(index=False))
    if not df_ou_metrics.empty:
        print('\n=== Top 5 leagues by O/U miscalibration (severity) ===')
        cols = ['league', 'n', 'ece', 'max_abs_delta', 'severity',
                'delta_under', 'delta_over']
        print(df_ou_metrics[cols].head().to_string(index=False))


if __name__ == '__main__':
    main()
