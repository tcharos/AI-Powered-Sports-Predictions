import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.metrics import accuracy_score, mean_absolute_error, r2_score
import json
import os
import argparse
import warnings

# Suppress warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

DATA_PATH = "data_sets/NBA/training_data.csv"
MODEL_DIR = "models"
WINNER_PARAMS_FILE = os.path.join(MODEL_DIR, "nba_best_params_winner.json")
TOTAL_PARAMS_FILE = os.path.join(MODEL_DIR, "nba_best_params_total.json")

class NBATuner:
    def __init__(self):
        self.features = [
            'home_pts_l5', 'home_allowed_l5', 'home_win_l5',
            'away_pts_l5', 'away_allowed_l5', 'away_win_l5',
            'home_pts_l10', 'home_allowed_l10', 'home_win_l10',
            'away_pts_l10', 'away_allowed_l10', 'away_win_l10'
        ]
        self.tscv = TimeSeriesSplit(n_splits=5)

    def load_data(self):
        print(f"Loading data from {DATA_PATH}...")
        if not os.path.exists(DATA_PATH):
            print("Data file not found.")
            return None
            
        df = pd.read_csv(DATA_PATH)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        # Drop missing features
        df = df.dropna(subset=self.features)
        return df

    def tune_winner(self, df):
        print("\n🏀 Tuning Winner Model (Classifier)...")
        X = df[self.features]
        y = df['home_win']
        
        # Base Params
        best_params = {
            'objective': 'binary:logistic',
            'learning_rate': 0.1,
            'max_depth': 5,
            'eval_metric': 'logloss',
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'seed': 42,
            'n_jobs': -1
        }
        
        # Helper for Grid Search
        def run_grid_search(param_grid):
            model = xgb.XGBClassifier(**best_params, use_label_encoder=False)
            grid = GridSearchCV(estimator=model, param_grid=param_grid, scoring='accuracy', cv=self.tscv, verbose=1, n_jobs=-1)
            grid.fit(X, y)
            return grid.best_params_, grid.best_score_

        # 1. Tune Max Depth & Min Child Weight
        print("[Step 1] Tuning Max Depth & Min Child Weight...")
        grid1 = {
            'max_depth': [3, 5, 7],
            'min_child_weight': [1, 3, 5]
        }
        params1, score1 = run_grid_search(grid1)
        print(f"  -> Best: {params1} (Score: {score1:.4f})")
        best_params.update(params1)
        
        # 2. Tune Gamma
        print("[Step 2] Tuning Gamma...")
        grid2 = {'gamma': [0, 0.1, 0.5, 1]}
        params2, score2 = run_grid_search(grid2)
        print(f"  -> Best: {params2}")
        best_params.update(params2)
        
        # 3. Tune Subsample & Colsample
        print("[Step 3] Tuning Subsample & Colsample...")
        grid3 = {
            'subsample': [0.6, 0.8, 1.0],
            'colsample_bytree': [0.6, 0.8, 1.0]
        }
        params3, score3 = run_grid_search(grid3)
        print(f"  -> Best: {params3}")
        best_params.update(params3)
        
        # 4. Tune Learning Rate & n_estimators (via CV)
        print("[Step 4] Finalizing Learning Rate...")
        best_params['learning_rate'] = 0.01
        
        dtrain = xgb.DMatrix(X, label=y)
        cv_results = xgb.cv(
            best_params, dtrain, 
            num_boost_round=5000, 
            nfold=5, 
            early_stopping_rounds=50, 
            verbose_eval=False,
            seed=42
        )
        best_n = cv_results.shape[0]
        print(f"  -> Best n_estimators for LR 0.01: {best_n}")
        best_params['n_estimators'] = best_n
        
        # Save
        if not os.path.exists(MODEL_DIR): os.makedirs(MODEL_DIR)
        with open(WINNER_PARAMS_FILE, 'w') as f:
            json.dump(best_params, f, indent=4)
        print(f"✅ Saved Winner Params to {WINNER_PARAMS_FILE}")

    def tune_total(self, df):
        print("\n🔢 Tuning Totals Model (Regressor)...")
        X = df[self.features]
        y = df['total_points']
        
        # Base Params
        best_params = {
            'objective': 'reg:squarederror',
            'learning_rate': 0.1,
            'max_depth': 5,
            'eval_metric': 'mae',
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'seed': 42,
            'n_jobs': -1
        }
        
        # Helper for Grid Search
        def run_grid_search(param_grid):
            model = xgb.XGBRegressor(**best_params)
            grid = GridSearchCV(estimator=model, param_grid=param_grid, scoring='neg_mean_absolute_error', cv=self.tscv, verbose=1, n_jobs=-1)
            grid.fit(X, y)
            return grid.best_params_, grid.best_score_
            
        # 1. Tune Max Depth & Min Child Weight
        print("[Step 1] Tuning Max Depth & Min Child Weight...")
        grid1 = {
            'max_depth': [3, 4, 5, 6],
            'min_child_weight': [1, 3, 5]
        }
        params1, score1 = run_grid_search(grid1)
        print(f"  -> Best: {params1} (MAE: {-score1:.4f})")
        best_params.update(params1)
        
        # 2. Tune Gamma
        print("[Step 2] Tuning Gamma...")
        grid2 = {'gamma': [0, 0.1, 0.5]}
        params2, score2 = run_grid_search(grid2)
        print(f"  -> Best: {params2}")
        best_params.update(params2)
        
        # 3. Fine-tune Learning Rate
        print("[Step 3] Finalizing Learning Rate...")
        best_params['learning_rate'] = 0.01
        
        dtrain = xgb.DMatrix(X, label=y)
        cv_results = xgb.cv(
            best_params, dtrain,
            num_boost_round=5000,
            nfold=5,
            early_stopping_rounds=50,
            verbose_eval=False,
            seed=42
        )
        best_n = cv_results.shape[0]
        print(f"  -> Best n_estimators for LR 0.01: {best_n}")
        best_params['n_estimators'] = best_n
        
        # Save
        if not os.path.exists(MODEL_DIR): os.makedirs(MODEL_DIR)
        with open(TOTAL_PARAMS_FILE, 'w') as f:
            json.dump(best_params, f, indent=4)
        print(f"✅ Saved Totals Params to {TOTAL_PARAMS_FILE}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="all", help="all, winner, total")
    args = parser.parse_args()
    
    tuner = NBATuner()
    df = tuner.load_data()
    
    if df is not None:
        if args.model in ['all', 'winner']:
            tuner.tune_winner(df)
        if args.model in ['all', 'total']:
            tuner.tune_total(df)
