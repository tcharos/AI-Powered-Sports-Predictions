import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import TimeSeriesSplit
import numpy as np
import json
import os
import gc
from data_loader import DataLoader
from feature_engineering import FeatureEngineer
import feature_engineering
print(f"DEBUG: Loaded feature_engineering from {feature_engineering.__file__}")

class ModelTrainer:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.common_features = [
            'H_form_pts', 'H_form_gf', 'H_form_ga',
            'A_form_pts', 'A_form_gf', 'A_form_ga',
            # ELO Ratings
            'H_elo', 'A_elo', 
            # 'elo_diff', # Removed
            
            # League Encoding
            'league_cat', # Re-added
            
            # H/A Specific Basic
            'H_home_pts', 'H_home_gf', 'H_home_ga', 'H_home_sf', 'H_home_sa',
            'A_away_pts', 'A_away_gf', 'A_away_ga', 'A_away_sf', 'A_away_sa',
            
            # New Stats: Shots and Corners (Overall Form)
            # New Stats: Shots and Corners (Overall Form)
            'H_form_sf', 'H_form_sa', 'H_form_cf', 'H_form_ca',
            'A_form_sf', 'A_form_cf', 'A_form_ca',
            
            # Implied Probabilities
            'IP_H', 'IP_D', 'IP_A',

            # NEW: Advanced Features (Re-enabled/Added for Draw Detection)
            'abs_elo_diff', 'abs_ppg_diff', 'abs_form_pts_diff',
            'elo_diff', # Re-added for context
        ]

    def prepare_data(self):
        print("Loading data...")
        loader = DataLoader(self.data_dir)
        df = loader.load_historical_data()

        # NOTE: We need full history for correct ELO calculation, but treating the huge DF triggers OOM.
        # OPTIMIZATION:
        # 1. Create a lightweight DF for ELO calculation (2010-Present)
        # 2. Filter the main DF early for Training (2020-Present) to save memory
        # 3. Merge ELOs back into the Training DF

        # 1. Lightweight History for ELO
        cols_for_elo = ['date', 'home_team', 'away_team', 'FTHG', 'FTAG']
        existing_cols = [c for c in cols_for_elo if c in df.columns]
        df_elo_history = df[existing_cols].copy().sort_values('date')

        print("Calculating ELO ratings on full history...")
        from elo_engine import EloTracker
        elo_tracker = EloTracker()
        df_elo_history = elo_tracker.process_history(df_elo_history)

        # Save current ELO ratings for prediction usage
        with open("data_sets/elo_ratings.json", "w") as f:
            json.dump(elo_tracker.ratings, f)
        print("Saved final ELO ratings to data_sets/elo_ratings.json")

        # 2. Filter Main DF for Feature Engineering (Buffer)
        # We need prior data to calculate rolling features for the start of the training period.
        buffer_date = pd.Timestamp("2019-01-01")
        training_start = pd.Timestamp("2020-01-01")
        print(f"Filtering data for FE since {buffer_date}...")
        df_fe = df[df['date'] >= buffer_date].copy()
        
        # Free up original huge DF
        del df
        gc.collect()
        
        # 3. Merge ELOs into df_fe
        home_elos = df_elo_history[['date', 'home_team', 'H_elo']].copy()
        df_fe = pd.merge(df_fe, home_elos, on=['date', 'home_team'], how='left')
        
        away_elos = df_elo_history[['date', 'away_team', 'A_elo']].copy()
        df_fe = pd.merge(df_fe, away_elos, on=['date', 'away_team'], how='left')
        
        # Clean up history df
        del df_elo_history
        gc.collect()
        
        # 4. Feature Engineering on the buffered DF
        print("Engineering rolling features...")
        fe = FeatureEngineer()
        df_fe = fe.add_rolling_features(df_fe)
        
        # 5. Filter for Final Training Set (2020-Present)
        print(f"Filtering final training set since {training_start}...")
        df_train = df_fe[df_fe['date'] >= training_start].copy()
        
        # Targets
        df_train['target_1x2'] = df_train['FTR'].map({'H': 0, 'D': 1, 'A': 2})
        df_train['target_draw'] = (df_train['FTR'] == 'D').astype(int)
        df_train['total_goals'] = df_train['FTHG'] + df_train['FTAG']
        df_train['target_ou'] = df_train.apply(lambda x: 1 if (x['FTHG'] + x['FTAG']) > 2.5 else 0, axis=1) # Keep for legacy check if needed
        
        return df_train

    def train_draw(self, df):
        print("\n--- Training Binary Draw Model (Stage A) ---")
        features = ['B365D', 'abs_elo_diff', 'abs_ppg_diff', 'abs_form_pts_diff'] + self.common_features
        features = list(dict.fromkeys(features)) # Remove duplicates while preserving order
        
        # We focus on features relevant to balance/uncertainty
        df_train = df.dropna(subset=features + ['target_draw']).copy()
        df_train = df_train.sort_values('date')

        if 'league_cat' in df_train.columns:
            df_train['league_cat'] = df_train['league_cat'].astype('category')
            
        params = {
            'objective': 'binary:logistic',
            'n_estimators': 100,
            'learning_rate': 0.05, # Lower LR for stability
            'max_depth': 4, # Shallower trees
            'eval_metric': 'logloss',
            # 'scale_pos_weight': 3.5, # Removed to prevent overcalibration
            'tree_method': 'hist',
            'enable_categorical': True
        }
        
        # Quick Train (Validation split)
        split_idx = int(len(df_train) * 0.90)
        train_data = df_train.iloc[:split_idx]
        valid_data = df_train.iloc[split_idx:]
        
        model = xgb.XGBClassifier(**params)
        model.fit(
            train_data[features], train_data['target_draw'],
            eval_set=[(valid_data[features], valid_data['target_draw'])],
            verbose=False
        )
        
        # Metrics
        probs = model.predict_proba(valid_data[features])[:, 1]
        preds = (probs > 0.5).astype(int)
        acc = accuracy_score(valid_data['target_draw'], preds)
        
        # Specific Recall (Did we catch the draws?)
        from sklearn.metrics import recall_score, precision_score
        rec = recall_score(valid_data['target_draw'], preds)
        prec = precision_score(valid_data['target_draw'], preds)
        
        print(f"Draw Model | Acc: {acc:.4f} | Recall (Draws): {rec:.4f} | Precision: {prec:.4f}")
        
        model.save_model("models/xgb_model_draw.json")
        with open("models/features_draw.json", "w") as f:
            json.dump(features, f)
        print("Saved Draw model.")

    def train_1x2(self, df):
        print("\n--- Training 1X2 Model ---")
        # Ensure unique features
        features = ['B365H', 'B365D', 'B365A'] + self.common_features
        features = list(dict.fromkeys(features))
        
        df_train = df.dropna(subset=features + ['target_1x2']).copy()
        df_train = df_train.sort_values('date')

        # Enable Categorical for XGBoost
        if 'league_cat' in df_train.columns:
            df_train['league_cat'] = df_train['league_cat'].astype('category')

        # 1. Cross-Validation
        print("Running Time-Series Cross-Validation (5 Splits)...")
        tscv = TimeSeriesSplit(n_splits=5)
        accuracies = []

        # Load Best Params if available
        param_file = "models/best_params_1x2.json"
        if os.path.exists(param_file):
            print(f"Loading tuned parameters from {param_file}...")
            with open(param_file, "r") as f:
                params = json.load(f)
        else:
            print("Using default parameters (No tuning found)...")
            params = {
                'objective': 'multi:softprob',
                'num_class': 3,
                'n_estimators': 100,
                'learning_rate': 0.1,
                'max_depth': 5,
                'eval_metric': 'mlogloss',
                'early_stopping_rounds': 10,
                'tree_method': 'hist', # Required for categorical
                'enable_categorical': True
            }

        for fold, (train_idx, test_idx) in enumerate(tscv.split(df_train)):
            cv_train = df_train.iloc[train_idx]
            cv_test = df_train.iloc[test_idx]

            model = xgb.XGBClassifier(**params)
            model.fit(
                cv_train[features], cv_train['target_1x2'],
                eval_set=[(cv_test[features], cv_test['target_1x2'])],
                verbose=False
            )
            # Predictions
            preds_prob = model.predict_proba(cv_test[features])
            preds_class = model.predict(cv_test[features])
            
            # 1. Accuracy
            acc = accuracy_score(cv_test['target_1x2'], preds_class)
            accuracies.append(acc)
            
            # 2. Log Loss (Multiclass)
            from sklearn.metrics import log_loss
            ll = log_loss(cv_test['target_1x2'], preds_prob, labels=[0, 1, 2])
            
            # 3. Brier Score (Multiclass definition: mean squared error of probability vector)
            # sklearn brier_score_loss is binary only. We implement simple multiclass MSE.
            # Convert target to one-hot for MSE calculation
            y_true_onehot = np.eye(3)[cv_test['target_1x2'].astype(int)]
            brier_score = np.mean(np.sum((preds_prob - y_true_onehot)**2, axis=1))

            # 4. Calibration (Avg Confidence vs Accuracy)
            # We take max prob as confidence
            confidences = np.max(preds_prob, axis=1)
            is_correct = (preds_class == cv_test['target_1x2'])
            avg_conf = np.mean(confidences)
            avg_acc_conf = np.mean(is_correct)
            calibration_error = avg_conf - avg_acc_conf
            
            # 5. ROI Simulation (Flat betting on prediction)
            # Odds columns: B365H (index 0), B365D (index 1), B365A (index 2)
            odds_cols = ['B365H', 'B365D', 'B365A']
            # Get odds for the predicted class
            # preds_class is array of 0, 1, 2. We need to select corresponding column.
            # Advanced indexing:
            rows = np.arange(len(preds_class))
            pred_odds = cv_test[odds_cols].values[rows, preds_class]
            
            # Profit logic: if correct, profit = odds - 1. If wrong, profit = -1.
            profits = np.where(is_correct, pred_odds - 1.0, -1.0)
            roi = (np.sum(profits) / len(profits)) * 100.0 if len(profits) > 0 else 0.0
            
            print(f"Fold {fold+1} | Acc: {acc:.4f} | LogLoss: {ll:.4f} | Brier: {brier_score:.4f} | ROI: {roi:.2f}% | CalibErr: {calibration_error:.4f}")
        
        print(f"Average CV Accuracy: {np.mean(accuracies):.4f}")
        
        # 2. Final Training on Full Dataset (with small validation split for early stopping)
        print("Retraining Final Model on Full Dataset (2020-Present)...")
        # Use last 5% as validation to prevent overfitting on the very last chunk, but maximize training data
        split_idx = int(len(df_train) * 0.95)
        final_train = df_train.iloc[:split_idx]
        final_valid = df_train.iloc[split_idx:]
        
        final_model = xgb.XGBClassifier(**params)
        final_model.fit(
            final_train[features], final_train['target_1x2'],
            eval_set=[(final_valid[features], final_valid['target_1x2'])],
            verbose=False
        )
        
        final_model.save_model("models/xgb_model_1x2.json")
        with open("models/features_1x2.json", "w") as f:
            json.dump(features, f)
        print("Saved final 1X2 model.")

    def train_ou(self, df):
        print("\n--- Training O/U 2.5 Model (Poisson Regression) ---")
        features = ['B365H', 'B365D', 'B365A', 'H_form_ou', 'A_form_ou'] + self.common_features
        features = list(dict.fromkeys(features))
        
        # Target: Total Goals
        df['total_goals'] = df['FTHG'] + df['FTAG']
        
        df_train = df.dropna(subset=features + ['total_goals']).copy()
        df_train = df_train.sort_values('date')
        
        # Enable Categorical for XGBoost
        if 'league_cat' in df_train.columns:
            df_train['league_cat'] = df_train['league_cat'].astype('category')
        
        # 1. Cross-Validation
        print("Running Time-Series Cross-Validation (5 Splits)...")
        tscv = TimeSeriesSplit(n_splits=5)
        accuracies = []
        log_losses = []
        
        # Load Best Params if available
        param_file = "models/best_params_ou.json"
        
        # Using Regression Parameters suitable for Count Data
        # We try to load, but override the objective if it was binary
        if os.path.exists(param_file):
            print(f"Loading tuned parameters from {param_file}...")
            with open(param_file, "r") as f:
                params = json.load(f)
            # FORCE Poisson or Regression objective
            params['objective'] = 'count:poisson' 
            # Remove binary-specific metrics if present
            if 'eval_metric' in params and params['eval_metric'] in ['logloss', 'error']:
                params['eval_metric'] = 'poisson-nloglik'
        else:
            print("Using default Poisson parameters...")
            params = {
                'objective': 'count:poisson',
                'n_estimators': 100,
                'learning_rate': 0.1,
                'max_depth': 5,
                'eval_metric': 'poisson-nloglik',
                'early_stopping_rounds': 10,
                'tree_method': 'hist',
                'enable_categorical': True
            }
        
        for fold, (train_idx, test_idx) in enumerate(tscv.split(df_train)):
            cv_train = df_train.iloc[train_idx]
            cv_test = df_train.iloc[test_idx]
            
            # Use XGBRegressor
            model = xgb.XGBRegressor(**params)
            
            model.fit(
                cv_train[features], cv_train['total_goals'],
                eval_set=[(cv_test[features], cv_test['total_goals'])],
                verbose=False
            )
            
            preds_lambda = model.predict(cv_test[features])
            
            # --- Convert Lambda to Probability (Poisson) ---
            # P(Over 2.5) = 1 - P(X <= 2)
            # P(X=k) = e^-lam * lam^k / k!
            # P(X<=2) = e^-lam * (1 + lam + lam^2/2)
            
            prob_le_2 = np.exp(-preds_lambda) * (1 + preds_lambda + (preds_lambda**2 / 2))
            prob_over = 1.0 - prob_le_2
            
            # Clip for safety
            prob_over = np.clip(prob_over, 0.001, 0.999)
            
            # Evaluation against Binary Targets (for consistency)
            binary_target = (cv_test['total_goals'] > 2.5).astype(int)
            preds_class = (prob_over > 0.5).astype(int)
            
            # 1. Accuracy
            acc = accuracy_score(binary_target, preds_class)
            accuracies.append(acc)
            
            # 2. Log Loss
            from sklearn.metrics import log_loss
            ll = log_loss(binary_target, prob_over)
            log_losses.append(ll)
            
            # 3. Regression Error (RMSE)
            rmse = np.sqrt(np.mean((cv_test['total_goals'] - preds_lambda)**2))

            print(f"Fold {fold+1} | Acc: {acc:.4f} | LogLoss: {ll:.4f} | RMSE: {rmse:.4f}")
            
        print(f"Average CV Accuracy: {np.mean(accuracies):.4f} | Avg LogLoss: {np.mean(log_losses):.4f}")
        
        # 2. Final Training
        print("Retraining Final Model on Full Dataset (2020-Present)...")
        split_idx = int(len(df_train) * 0.95)
        final_train = df_train.iloc[:split_idx]
        final_valid = df_train.iloc[split_idx:]
        
        final_model = xgb.XGBRegressor(**params)
        final_model.fit(
            final_train[features], final_train['total_goals'],
            eval_set=[(final_valid[features], final_valid['total_goals'])],
            verbose=False
        )
        
        final_model.save_model("models/xgb_model_ou.json")
        with open("models/features_ou.json", "w") as f:
            json.dump(features, f)
        print("Saved final O/U model (Regression/Poisson).")

if __name__ == "__main__":
    trainer = ModelTrainer("data_sets/MatchHistory")
    data = trainer.prepare_data()
    trainer.train_1x2(data)
    trainer.train_draw(data)
    trainer.train_ou(data)
