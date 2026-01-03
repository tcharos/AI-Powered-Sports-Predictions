import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss
import json
import os
import argparse
from data_loader import DataLoader
from feature_engineering import FeatureEngineer
import warnings

# Suppress warnings for cleaner output
warnings.simplefilter(action='ignore', category=FutureWarning)

class StepwiseTuner:
    def __init__(self, data_dir="data_sets/MatchHistory"):
        self.data_dir = data_dir
        self.common_features = [
            'H_form_pts', 'H_form_gf', 'H_form_ga',
            'A_form_pts', 'A_form_gf', 'A_form_ga',
            'H_elo', 'A_elo', 
            'league_cat',
            'H_home_pts', 'H_home_gf', 'H_home_ga', 'H_home_sf', 'H_home_sa',
            'A_away_pts', 'A_away_gf', 'A_away_ga', 'A_away_sf', 'A_away_sa',
            'H_form_sf', 'H_form_sa', 'H_form_cf', 'H_form_ca',
            'A_form_sf', 'A_form_cf', 'A_form_ca',
            'IP_H', 'IP_D', 'IP_A',
        ]
        self.tscv = TimeSeriesSplit(n_splits=5)
    
    def load_data(self):
        print("Loading data for tuning...")
        # Reusing logic from ModelTrainer manually for now 
        # (Ideal world: refactor Trainer to share prepare_data)
        loader = DataLoader(self.data_dir)
        df = loader.load_historical_data()
        
        # ELO Handling (Simplified: reuse existing ELO json if possible, or recalculate)
        # For tuning, we assume ELOs are generated via retrain_pipeline roughly correctly.
        # But we need to attach them.
        # Check if we should fully re-engineer features or assume they exist?
        # The loader only loads CSVs. We strictly need FeatureEngineer here.
        
        # 1. ELO
        cols_for_elo = ['date', 'home_team', 'away_team', 'FTHG', 'FTAG']
        existing_cols = [c for c in cols_for_elo if c in df.columns]
        df_elo_history = df[existing_cols].copy().sort_values('date')
        
        from elo_engine import EloTracker
        elo_tracker = EloTracker()
        df_elo_history = elo_tracker.process_history(df_elo_history)
        
        # Merge ELOs
        home_elos = df_elo_history[['date', 'home_team', 'H_elo']].copy()
        df = pd.merge(df, home_elos, on=['date', 'home_team'], how='left')
        away_elos = df_elo_history[['date', 'away_team', 'A_elo']].copy()
        df = pd.merge(df, away_elos, on=['date', 'away_team'], how='left')

        # Feature Engineering
        print("Engineering features...")
        fe = FeatureEngineer()
        df = fe.add_rolling_features(df)
        
        # Filter for recent era (Training Set)
        training_start = pd.Timestamp("2020-01-01")
        df_train = df[df['date'] >= training_start].copy()
        
        # Targets
        df_train['target_1x2'] = df_train['FTR'].map({'H': 0, 'D': 1, 'A': 2})
        df_train['target_ou'] = df_train.apply(lambda x: 1 if (x['FTHG'] + x['FTAG']) > 2.5 else 0, axis=1)
        
        if 'league_cat' in df_train.columns:
            df_train['league_cat'] = df_train['league_cat'].astype('category')
            
        return df_train

    def tune_1x2(self, df):
        print("\n=== Tuning 1X2 Model ===")
        features = ['B365H', 'B365D', 'B365A'] + self.common_features
        df_train = df.dropna(subset=features + ['target_1x2']).sort_values('date')
        X = df_train[features]
        y = df_train['target_1x2']
        
        best_params = {
            'objective': 'multi:softprob',
            'num_class': 3,
            'learning_rate': 0.1,
            'tree_method': 'hist',
            'enable_categorical': True,
            'eval_metric': 'mlogloss',
            'seed': 42
        }
        
        # Step 1: Fix LR, Find n_estimators
        print("[Step 1] Initial n_estimators...")
        dtrain = xgb.DMatrix(X, label=y, enable_categorical=True)
        cv_results = xgb.cv(
            best_params, dtrain, 
            num_boost_round=1000, 
            nfold=5, 
            stratified=False, # TimeSeriesSplit ideal but xgb.cv defaults standard KFold or Stratified. 
                              # For pure stepwise check, standard CV is "okay" as proxy, or we manually loop.
            early_stopping_rounds=50,
            verbose_eval=False,
            seed=42
        )
        best_n = cv_results.shape[0]
        print(f"  -> Best n_estimators: {best_n}")
        best_params['n_estimators'] = best_n
        
        # Helper for Grid Search
        def run_grid_search(param_grid):
            # We use XGBClassifier wrapper for sklearn compatibility
            model = xgb.XGBClassifier(**best_params)
            # Remove n_estimators from kwargs if it's in the grid? No, set valid default.
            
            grid = GridSearchCV(estimator=model, param_grid=param_grid, scoring='neg_log_loss', cv=self.tscv, verbose=1, n_jobs=-1)
            grid.fit(X, y)
            return grid.best_params_, grid.best_score_

        # Step 2: Max Depth & Min Child Weight
        print("[Step 2] Tuning Max Depth & Min Child Weight...")
        grid2 = {
            'max_depth': [3, 5, 7, 9],
            'min_child_weight': [1, 3, 5, 7]
        }
        params2, score2 = run_grid_search(grid2)
        print(f"  -> Best: {params2} (Score: {score2:.4f})")
        best_params.update(params2)
        
        # Step 3: Gamma
        print("[Step 3] Tuning Gamma...")
        grid3 = {'gamma': [0, 0.1, 0.5, 1, 5]}
        params3, score3 = run_grid_search(grid3)
        print(f"  -> Best: {params3}")
        best_params.update(params3)
        
        # Step 4: Subsample & Colsample
        print("[Step 4] Tuning Subsample & Colsample...")
        grid4 = {
            'subsample': [0.6, 0.7, 0.8, 0.9, 1.0],
            'colsample_bytree': [0.6, 0.7, 0.8, 0.9, 1.0]
        }
        params4, score4 = run_grid_search(grid4)
        print(f"  -> Best: {params4}")
        best_params.update(params4)
        
        # Step 5: Regularization
        print("[Step 5] Tuning Alpha & Lambda...")
        grid5 = {
            'reg_alpha': [0, 0.001, 0.01, 0.1, 1],
            'reg_lambda': [0, 0.001, 0.01, 0.1, 1] # default is 1
        }
        params5, score5 = run_grid_search(grid5)
        print(f"  -> Best: {params5}")
        best_params.update(params5)
        
        # Step 6: Lower LR & Re-calc n_estimators
        print("[Step 6] Tuning Learning Rate...")
        best_params['learning_rate'] = 0.01
        # Removing n_estimators to re-find it
        if 'n_estimators' in best_params: del best_params['n_estimators']
        
        cv_results_final = xgb.cv(
            best_params, dtrain, 
            num_boost_round=5000, 
            nfold=5, 
            early_stopping_rounds=50,
            verbose_eval=False,
            seed=42
        )
        final_n = cv_results_final.shape[0]
        best_params['n_estimators'] = final_n
        print(f"  -> Final Learning Rate: 0.01, Final n_estimators: {final_n}")
        
        # Save
        os.makedirs("models", exist_ok=True)
        with open("models/best_params_1x2.json", "w") as f:
            json.dump(best_params, f, indent=4)
        print("Saved best_params_1x2.json")

    def tune_ou(self, df):
        print("\n=== Tuning O/U Model ===")
        features = ['B365H', 'B365D', 'B365A'] + self.common_features # Basic 1X2 odds are correlated with O/U too
        # Should add O/U specific odds if available, but staying consistent with trainer
        
        df_train = df.dropna(subset=features + ['target_ou']).sort_values('date')
        X = df_train[features]
        y = df_train['target_ou']
        
        best_params = {
            'objective': 'binary:logistic',
            'learning_rate': 0.1,
            'tree_method': 'hist',
            'enable_categorical': True,
            'eval_metric': 'logloss',
            'seed': 42
        }
        
        # Step 1
        print("[Step 1] Initial n_estimators...")
        dtrain = xgb.DMatrix(X, label=y, enable_categorical=True)
        cv_results = xgb.cv(best_params, dtrain, num_boost_round=1000, nfold=5, early_stopping_rounds=50, verbose_eval=False, seed=42)
        best_params['n_estimators'] = cv_results.shape[0]
        print(f"  -> Best n_estimators: {best_params['n_estimators']}")

        def run_grid_search(param_grid):
            model = xgb.XGBClassifier(**best_params)
            grid = GridSearchCV(estimator=model, param_grid=param_grid, scoring='neg_log_loss', cv=self.tscv, verbose=1, n_jobs=-1)
            grid.fit(X, y)
            return grid.best_params_, grid.best_score_
            
        # Step 2
        print("[Step 2] Depth & Child Weight...")
        params2, _ = run_grid_search({'max_depth': [3, 5, 7], 'min_child_weight': [1, 3, 5]})
        best_params.update(params2)
        
        # Step 3
        print("[Step 3] Gamma...")
        params3, _ = run_grid_search({'gamma': [0, 0.1, 0.5, 1]})
        best_params.update(params3)

        # Step 4
        print("[Step 4] Subsample...")
        params4, _ = run_grid_search({'subsample': [0.6, 0.8, 1.0], 'colsample_bytree': [0.6, 0.8, 1.0]})
        best_params.update(params4)
        
        # Step 5
        print("[Step 5] Regularization...")
        params5, _ = run_grid_search({'reg_alpha': [0, 0.1, 1], 'reg_lambda': [0.1, 1, 5]})
        best_params.update(params5)
        
        # Step 6
        print("[Step 6] Final Learning Rate...")
        best_params['learning_rate'] = 0.01
        if 'n_estimators' in best_params: del best_params['n_estimators']
        cv_final = xgb.cv(best_params, dtrain, num_boost_round=5000, nfold=5, early_stopping_rounds=50, verbose_eval=False, seed=42)
        best_params['n_estimators'] = cv_final.shape[0]
        print(f"  -> Final n_estimators: {best_params['n_estimators']}")
        
        with open("models/best_params_ou.json", "w") as f:
            json.dump(best_params, f, indent=4)
        print("Saved best_params_ou.json")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="all", help="all, 1x2, or ou")
    args = parser.parse_args()
    
    tuner = StepwiseTuner()
    df = tuner.load_data()
    
    if args.model in ['all', '1x2']:
        tuner.tune_1x2(df)
    if args.model in ['all', 'ou']:
        tuner.tune_ou(df)
