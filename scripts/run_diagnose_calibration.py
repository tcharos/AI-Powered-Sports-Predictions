"""Diagnose per-league miscalibration (Phase C1).

Reuses ModelTrainer.prepare_data() so feature engineering matches the
trainer's pipeline exactly. Runs the same 5-fold TimeSeriesSplit the
trainer uses, collects out-of-fold predictions for every match, then
aggregates per-league calibration metrics.

Outputs CSV + Markdown to output/calibration/.

Usage:
    PYTHONPATH=$(pwd):$(pwd)/ml_project python3 scripts/run_diagnose_calibration.py
"""

import datetime
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


def _load_params(name: str, default: dict) -> dict:
    p = os.path.join(PROJECT_ROOT, 'models', f'best_params_{name}.json')
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return default


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print('Preparing training data (this may take a few minutes)...')
    trainer = ModelTrainer(data_dir=os.path.join(PROJECT_ROOT, 'data_sets', 'MatchHistory'))
    df_train = trainer.prepare_data()
    print(f'Prepared {len(df_train)} rows across {df_train["league"].nunique()} leagues.')

    # --- 1X2 OOF ---
    print('\n=== 1X2: running 5-fold OOF predictions ===')
    features_1x2 = ['B365H', 'B365D', 'B365A'] + trainer.common_features
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
    features_ou = trainer.common_features.copy()
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
    csv_1x2 = os.path.join(OUTPUT_DIR, f'diagnose_{ts}_1x2.csv')
    csv_ou  = os.path.join(OUTPUT_DIR, f'diagnose_{ts}_ou.csv')
    md_path = os.path.join(OUTPUT_DIR, f'diagnose_{ts}.md')

    df_1x2_metrics.to_csv(csv_1x2, index=False)
    df_ou_metrics.to_csv(csv_ou, index=False)
    write_markdown_report(
        md_path, df_1x2_metrics, df_ou_metrics,
        meta={
            'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
            'source': 'data_sets/MatchHistory/',
            'n_splits': N_SPLITS,
            'min_n': MIN_N_PER_LEAGUE,
        },
    )

    print(f'\nSaved:\n  {csv_1x2}\n  {csv_ou}\n  {md_path}')

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
