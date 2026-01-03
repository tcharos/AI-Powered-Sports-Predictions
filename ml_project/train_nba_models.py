import pandas as pd
import numpy as np
import pickle
import os
import json
from xgboost import XGBClassifier, XGBRegressor
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report, mean_absolute_error, r2_score

DATA_PATH = "data_sets/NBA/training_data.csv"
MODEL_DIR = "models"
WINNER_MODEL_PATH = os.path.join(MODEL_DIR, "nba_winner_model.pkl")
TOTAL_MODEL_PATH = os.path.join(MODEL_DIR, "nba_total_model.pkl")

WINNER_PARAMS_FILE = os.path.join(MODEL_DIR, "nba_best_params_winner.json")
TOTAL_PARAMS_FILE = os.path.join(MODEL_DIR, "nba_best_params_total.json")

def load_params(filepath):
    if os.path.exists(filepath):
        print(f"Loading params from {filepath}")
        with open(filepath, 'r') as f:
            return json.load(f)
    print(f"Warning: {filepath} not found. Using defaults.")
    return None

def train_models():
    print(f"Loading data from {DATA_PATH}...")
    if not os.path.exists(DATA_PATH):
        print("Data file not found.")
        return 

    df = pd.read_csv(DATA_PATH)
    
    # Sort just in case
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    # Define Features
    features = [
        'home_pts_l5', 'home_allowed_l5', 'home_win_l5',
        'away_pts_l5', 'away_allowed_l5', 'away_win_l5',
        'home_pts_l10', 'home_allowed_l10', 'home_win_l10',
        'away_pts_l10', 'away_allowed_l10', 'away_win_l10'
    ]
    
    df = df.dropna(subset=features)
    
    X = df[features]
    y_winner = df['home_win']
    y_total = df['total_points']
    
    # ---------------------------
    # 1. Winner Model (XGBClassifier)
    # ---------------------------
    print("\n🏀 Training Winner Model (XGBoost)...")
    
    params_winner = load_params(WINNER_PARAMS_FILE)
    if not params_winner:
        params_winner = {
            'n_estimators': 100, 
            'max_depth': 5, 
            'learning_rate': 0.1,
            'eval_metric': 'logloss',
            'random_state': 42
        }
        
    # Time Series Cross Validation (5 Folds) for Reporting
    tscv = TimeSeriesSplit(n_splits=5)
    accuracies = []
    print("Running TimeSeries Cross-Validation (5 Splits)...")
    
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_cv_train, X_cv_test = X.iloc[train_idx], X.iloc[test_idx]
        y_cv_train, y_cv_test = y_winner.iloc[train_idx], y_winner.iloc[test_idx]
        
        clf_cv = XGBClassifier(**params_winner, use_label_encoder=False)
        clf_cv.fit(X_cv_train, y_cv_train, verbose=False)
        pred = clf_cv.predict(X_cv_test)
        acc = accuracy_score(y_cv_test, pred)
        accuracies.append(acc)
        print(f"  Fold {fold+1} Accuracy: {acc:.4f}")
        
    print(f"✅ Average Winner Accuracy: {np.mean(accuracies):.4f}")
    
    # Final Training on Full Data (2020+)
    print("Retraining Final Winner Model on Full Dataset...")
    # Use simple slit for validation set in final training (early stopping optional but good practice)
    split_idx = int(len(df) * 0.95)
    X_train, X_valid = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_valid = y_winner.iloc[:split_idx], y_winner.iloc[split_idx:]
    
    final_clf = XGBClassifier(**params_winner, use_label_encoder=False)
    final_clf.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False) # early_stopping_rounds=10 implicit via verbose? No need to explicit if we fixed n_estimators via tuner
    
    # Feature Importance
    importances = list(zip(features, final_clf.feature_importances_))
    importances.sort(key=lambda x: x[1], reverse=True)
    print("Top Predictors (Winner):", importances[:3])

    # ---------------------------
    # 2. Total Points Model (XGBRegressor)
    # ---------------------------
    print("\n🔢 Training Totals Model (XGBoost)...")
    
    params_total = load_params(TOTAL_PARAMS_FILE)
    if not params_total:
        params_total = {
            'n_estimators': 100, 
            'max_depth': 5, 
            'learning_rate': 0.1,
            'random_state': 42
        }

    maes = []
    print("Running TimeSeries Cross-Validation (5 Splits)...")
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_cv_train, X_cv_test = X.iloc[train_idx], X.iloc[test_idx]
        y_cv_train, y_cv_test = y_total.iloc[train_idx], y_total.iloc[test_idx]
        
        reg_cv = XGBRegressor(**params_total)
        reg_cv.fit(X_cv_train, y_cv_train, verbose=False)
        pred = reg_cv.predict(X_cv_test)
        mae = mean_absolute_error(y_cv_test, pred)
        maes.append(mae)
        print(f"  Fold {fold+1} MAE: {mae:.2f}")

    print(f"✅ Average Totals MAE: {np.mean(maes):.2f}")
    
    # Final Training
    print("Retraining Final Totals Model on Full Dataset...")
    y_tot_train, y_tot_valid = y_total.iloc[:split_idx], y_total.iloc[split_idx:]
    
    final_reg = XGBRegressor(**params_total)
    final_reg.fit(X_train, y_tot_train, eval_set=[(X_valid, y_tot_valid)], verbose=False)
    
    # ---------------------------
    # Save Models
    # ---------------------------
    if not os.path.exists(MODEL_DIR):
        os.makedirs(MODEL_DIR)
        
    with open(WINNER_MODEL_PATH, 'wb') as f:
        pickle.dump(final_clf, f)
    print(f"Saved Winner model to {WINNER_MODEL_PATH}")
    
    with open(TOTAL_MODEL_PATH, 'wb') as f:
        pickle.dump(final_reg, f)
    print(f"Saved Totals model to {TOTAL_MODEL_PATH}")

if __name__ == "__main__":
    train_models()
