from flask import Blueprint, render_template, current_app, jsonify, request, flash, redirect, url_for
import os
import json
import glob
import pandas as pd
import subprocess
import datetime
from datetime import timedelta

basketball_bp = Blueprint('basketball', __name__, template_folder='templates')

NBA_TASKS = {}

def load_nba_analytics():
    path = os.path.join(current_app.config['DATA_SETS_DIR'], 'NBA', 'nba_analytics.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return None

def load_nba_stats():
    # Helper to load the historical stats
    data_path = os.path.join(current_app.config['DATA_SETS_DIR'], 'NBA', 'nba_history_stats.json')
    if os.path.exists(data_path):
        with open(data_path, 'r') as f:
            return json.load(f)
    return []

def load_latest_predictions():
    project_root = os.path.dirname(current_app.root_path)
    output_dir = os.path.join(project_root, 'output_basketball')
    
    pattern = os.path.join(output_dir, "predictions_nba_*.csv")
    files = glob.glob(pattern)
    
    if not files:
        return None, None
        
    latest_file = max(files, key=os.path.getctime)
    try:
        df = pd.read_csv(latest_file)
        
        # Check for verification file
        basename = os.path.basename(latest_file)
        # predictions_nba_2025-12-13.csv -> verification_nba_2025-12-13.csv
        ver_filename = basename.replace('predictions_', 'verification_')
        ver_path = os.path.join(output_dir, ver_filename)
        
        if os.path.exists(ver_path):
            try:
                v_df = pd.read_csv(ver_path)
                # We need to merge. Common key: "Home Team" and "Away Team" or "Match"
                # predictions csv has 'Home Team', 'Away Team'
                # verification csv has 'Match' (e.g. "Hawks vs Knicks"), but let's see. 
                # evaluate_nba_predictions.py saves 'Match' but also 'Pred Winner', etc.
                # Actually evaluate_nba doesn't save raw team names in separate cols, just 'Match'.
                # But the order should be identical if we just processed it? No, unsafe.
                # Let's clean 'Match' in verification header to get Home Team for merge.
                
                # Better: In evaluate_nba_predictions.py, I should have saved Home/Away cols to make this easier.
                # But I can parse "Team A vs Team B" from 'Match' column in verification.
                
                v_to_merge = v_df[['Match', 'Correct Winner', 'Correct Total', 'Actual Winner', 'Actual O/U', 'Score']]
                # split Match to get key? 
                # Or just rely on row order if 1:1? No.
                
                # Let's map v_df by Match string.
                # Pred df needs a Match string column.
                df['Match'] = df['Home Team'] + " vs " + df['Away Team']
                
                # Merge
                df = pd.merge(df, v_to_merge, on='Match', how='left')
                
                # Handle boolean conversion for JSON
                df['Correct Winner'] = df['Correct Winner'].replace({float('nan'): None})
                df['Correct Total'] = df['Correct Total'].replace({float('nan'): None})
                
            except Exception as ex:
                print(f"Error merging verification: {ex}")

        return df.to_dict(orient='records'), os.path.basename(latest_file)
    except Exception as e:
        print(f"Error reading predictions: {e}")
        return None, None

@basketball_bp.route('/')
def index():
    # NBA Dashboard
    stats = load_nba_stats()
    game_count = len(stats)
    
    predictions, filename = load_latest_predictions()
    analytics = load_nba_analytics()
    
    return render_template('nba_index.html', 
                           game_count=game_count, 
                           predictions=predictions,
                           pred_file=filename,
                           analytics=analytics)

@basketball_bp.route('/verify', methods=['POST'])
def verify_nba():
    if NBA_TASKS.get('verify') and NBA_TASKS['verify'].poll() is None:
        flash("NBA Verification is already running.", "warning")
        return redirect(url_for('basketball.index'))
        
    try:
        project_root = os.path.dirname(current_app.root_path)
        script_path = os.path.join(project_root, 'bin', 'run_nba_verification.sh')
        
        # Determine Date (Default Yesterday, or from Form)
        # For now, default to Yesterday
        
        log_file = open(os.path.join(project_root, 'logs', 'nba_verify.log'), 'w')
        
        proc = subprocess.Popen(['/bin/bash', script_path], cwd=project_root, stdout=log_file, stderr=subprocess.STDOUT)
        NBA_TASKS['verify'] = proc
        
        flash("Started NBA Validation Pipeline (Yesterday).", "success")
    except Exception as e:
        flash(f"Error starting verification: {e}", "danger")
        
    return redirect(url_for('basketball.index'))

@basketball_bp.route('/retrain', methods=['POST'])
def retrain_nba():
    if NBA_TASKS.get('retrain') and NBA_TASKS['retrain'].poll() is None:
        flash("NBA Retraining is already running.", "warning")
        return redirect(url_for('basketball.index'))
        
    try:
        project_root = os.path.dirname(current_app.root_path)
        script_path = os.path.join(project_root, 'bin', 'retrain_nba_pipeline.sh')
        
        log_file = open(os.path.join(project_root, 'logs', 'nba_retrain.log'), 'w')
        
        # Use bash
        proc = subprocess.Popen(['/bin/bash', script_path], cwd=project_root, stdout=log_file, stderr=subprocess.STDOUT)
        NBA_TASKS['retrain'] = proc
        
        flash("Started NBA Retraining Pipeline (Update Data + Train Models). Check logs for progress.", "success")
    except Exception as e:
        flash(f"Error starting retraining: {e}", "danger")
        
    return redirect(url_for('basketball.index'))

@basketball_bp.route('/predict', methods=['POST'])
def predict_nba():
    if NBA_TASKS.get('predict') and NBA_TASKS['predict'].poll() is None:
        flash("NBA Prediction is already running.", "warning")
        return redirect(url_for('basketball.index'))
        
    try:
        project_root = os.path.dirname(current_app.root_path)
        script_path = os.path.join(project_root, 'bin', 'run_nba_predictions.sh')
        
        log_file = open(os.path.join(project_root, 'logs', 'nba_predict.log'), 'w')
        
        # Use bash
        proc = subprocess.Popen(['/bin/bash', script_path], cwd=project_root, stdout=log_file, stderr=subprocess.STDOUT)
        NBA_TASKS['predict'] = proc
        
        flash("Started NBA Prediction Pipeline (Tomorrow's Games). Check logs for progress.", "success")
    except Exception as e:
        flash(f"Error starting prediction: {e}", "danger")
        
    return redirect(url_for('basketball.index'))

@basketball_bp.route('/api/stats')
def api_stats():
    stats = load_nba_stats()
    return jsonify(stats)
