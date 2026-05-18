from flask import Flask, render_template, request, redirect, url_for, flash, Blueprint
import pandas as pd
import os
import subprocess
import datetime
import glob
import json
import shutil
import sys
import time

from sports_config import (
    get_sport_config,
    get_bankroll,
    update_bankroll,
    all_bankrolls,
    all_lane_bankrolls,
    lane_bankrolls,
    sport_total,
    total_bankroll,
    LANES,
)

# Sport blueprints — each sport's routes mount under /<sport>/.
# Sport-agnostic routes (/, /status, /stop/<task>, /server/<action>) stay
# on the app itself. Adding a new sport = create a blueprint, register it.
NBA_TASKS = {}  # kept as empty stub so /status responses don't break

# List of sports surfaced on the landing page. To activate NBA, flip 'active'
# to True, uncomment the import + register_blueprint call further down, and
# the landing page card automatically becomes a working link.
SPORTS = [
    {'slug': 'football', 'label': 'Football', 'icon': '⚽', 'active': True,
     'bets_dir': 'output',
     'tagline': 'Daily 1X2 + Over/Under predictions, three-lane betting strategy.'},
    {'slug': 'nba',      'label': 'NBA',      'icon': '🏀', 'active': False,
     'bets_dir': 'output_basketball',
     'tagline': 'Dormant — code preserved at web_ui/nba/. Reactivation planned for next NBA season.'},
]

app = Flask(__name__)
app.secret_key = 'super_secret_key_flashscore'

football_bp = Blueprint('football', __name__)

# Register Blueprints
# from nba.routes import nba_bp, NBA_TASKS  # uncomment to reactivate NBA
# app.register_blueprint(nba_bp, url_prefix='/nba')

# Football blueprint registration happens at the bottom of this file,
# after all @football_bp.route handlers have been defined.

# Constants

# Constants
app.config['TEMPLATES_AUTO_RELOAD'] = True
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.append(PROJECT_ROOT) # Enable importing ml_project
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'output')
HISTORY_DIR = os.path.join(OUTPUT_DIR, 'history')
DATA_SETS_DIR = os.path.join(PROJECT_ROOT, 'data_sets')
app.config['DATA_SETS_DIR'] = DATA_SETS_DIR
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

@app.template_filter('to_float')
def to_float_filter(value):
    try:
        return float(value)
    except:
        return 0.0

# Global dictionary to track running processes
# Format: {'task_name': {'process': Popen_obj, 'start_time': datetime}}
TASKS = {
    'predict': {'process': None, 'log': 'predict.log'},
    'verify': {'process': None, 'log': 'verify.log'},
    'live': {'process': None, 'log': 'live_loop.log'},
    'update': {'process': None, 'log': 'update.log'},
    'leagues': {'process': None, 'log': 'leagues.log'},
    'retrain': {'process': None, 'log': 'retrain.log'}
}

@football_bp.route('/')
def index():
    # List prediction and verification files
    prediction_files = glob.glob(os.path.join(OUTPUT_DIR, 'predictions_*.csv'))
    verification_files = glob.glob(os.path.join(OUTPUT_DIR, 'verification_*.csv'))
    
    predictions = []
    for f in prediction_files:
        basename = os.path.basename(f)
        try:
            # Count matches (lines - header)
            with open(f, 'r') as fh:
                count = sum(1 for _ in fh) - 1
            count = max(0, count)

            # Extract date
            date_str = basename.replace('predictions_', '').replace('.csv', '')
            predictions.append({'filename': basename, 'date': date_str, 'type': 'Prediction', 'count': count})
        except:
             predictions.append({'filename': basename, 'date': 'Unknown', 'type': 'Prediction', 'count': 0})
             
    # matches_*.json files
    matches_files = glob.glob(os.path.join(OUTPUT_DIR, 'matches_*.json'))
    scraped_data = []
    for f in matches_files:
        basename = os.path.basename(f)
        try:
            with open(f, 'r') as fh:
                match_data = json.load(fh)
                count = len(match_data)
            scraped_data.append({'filename': basename, 'count': count})
        except:
             scraped_data.append({'filename': basename, 'count': 0})
    scraped_data.sort(key=lambda x: x['filename'], reverse=True)
             
    verifications = []
    for f in verification_files:
        basename = os.path.basename(f)
        # Extract date from verification_2025-01-20.csv
        try:
             date_str = basename.replace('verification_', '').replace('.csv', '')
             verifications.append({'filename': basename, 'date': date_str, 'type': 'Verification'})
        except Exception as e:
             print(f"Error parsing verification filename {basename}: {e}")
             verifications.append({'filename': basename, 'date': 'Unknown', 'type': 'Verification'})
    verifications.sort(key=lambda x: x['date'], reverse=True)
        
    predictions.sort(key=lambda x: x['date'], reverse=True)
    
    # Load Cumulative Stats for Dashboard
    league_stats = []
    stats_file = os.path.join(PROJECT_ROOT, 'data_sets/league_analytics.json')
    if os.path.exists(stats_file):
        try:
            with open(stats_file, 'r') as f:
                stats_data = json.load(f)
            
            for league, s in stats_data.items():
                total = s['total_matches']
                if total > 0:
                    acc_1x2 = round((s['correct_1x2'] / total) * 100, 2)
                    acc_ou = round((s['correct_ou'] / total) * 100, 2)
                else:
                    acc_1x2 = 0
                    acc_ou = 0
                    
                league_stats.append({
                    'League': league,
                    'Count': total,
                    'Acc_1X2': acc_1x2,
                    'Acc_OU': acc_ou
                })
            league_stats.sort(key=lambda x: x['Count'], reverse=True)
        except:
            pass
            
    # Load Live Live Data
    live_file = os.path.join(OUTPUT_DIR, "live_data.json")
    live_matches = []
    if os.path.exists(live_file):
        try:
            with open(live_file, 'r') as f:
                live_matches = json.load(f)
        except Exception as e:
            pass
            
    return render_template('dashboard.html', 
                          predictions=predictions, 
                          verifications=verifications,
                          league_stats=league_stats,
                          live_matches=live_matches[:50],
                          scraped_data=scraped_data)

@app.route('/status')
def get_status():
    status = {}
    
    # Merge NBA Tasks into checking logic
    # NBA_TASKS format: {'retrain': Popen, 'verify': Popen}
    # We prefix them to distinction: 'nba_retrain', 'nba_verify'
    
    # Check Standard Tasks
    for task_name, task_info in TASKS.items():
        if task_info and task_info.get('process'):
            poll = task_info['process'].poll()
            if poll is None:
                status[task_name] = {'state': 'running'}
            elif poll == 0:
                status[task_name] = {'state': 'completed'}
            else:
                # Capture last lines of log directly
                log_file = os.path.join(LOG_DIR, f"{task_name}.log")
                error_msg = 'Unknown error'
                if os.path.exists(log_file):
                     try:
                         # Get last 3 lines
                         lines = subprocess.check_output(['tail', '-n', '3', log_file]).decode('utf-8')
                         error_msg = lines.strip()
                     except:
                         pass
                status[task_name] = {'state': 'error', 'msg': error_msg}
        elif task_info and task_info.get('state'): # Thread tasks wrapper
             status[task_name] = {'state': task_info['state'], 'msg': task_info.get('msg', '')}
        else:
            status[task_name] = {'state': 'idle'}
            
    # Check NBA Tasks
    for nba_task, proc in NBA_TASKS.items():
        key = f"nba_{nba_task}"
        if proc:
            poll = proc.poll()
            if poll is None:
                status[key] = {'state': 'running'}
            elif poll == 0:
                status[key] = {'state': 'completed'}
            else:
                status[key] = {'state': 'error'}
        else:
             status[key] = {'state': 'idle'}
             
    return status

@app.route('/stop/<task_name>', methods=['POST'])
def stop_task(task_name):
    if task_name in TASKS and TASKS[task_name] and TASKS[task_name]['process']:
        try:
            # Send SIGTERM
            TASKS[task_name]['process'].terminate()
            time.sleep(1)
            # If still alive, SIGKILL
            if TASKS[task_name]['process'].poll() is None:
                TASKS[task_name]['process'].kill()
            
            TASKS[task_name]['process'] = None # Set process to None to indicate it's stopped
            flash(f'Task {task_name} stopped.', 'warning')
        except Exception as e:
            flash(f'Error stopping task: {str(e)}', 'danger')
    else:
        flash(f'No running {task_name} task found.', 'secondary')
    return redirect(url_for('football.index'))

def process_bet_verification(verification_file_path):
    """
    Checks if there is a 'bets_YYYY-MM-DD.json' corresponding to this verification file.
    If so, verifies the bets, calculates P/L, and updates the bankroll.
    """
    try:
        # verification_file_path: .../verify_2025-12-11.csv
        basename = os.path.basename(verification_file_path)
        # Extract date string "2025-12-11"
        date_str = basename.replace('verification_', '').replace('.csv', '') # Changed from 'verify_' to 'verification_'
        
        bets_file = os.path.join(OUTPUT_DIR, f"bets_{date_str}.json")
        if not os.path.exists(bets_file):
            print(f"No bets file found for {date_str} to verify.")
            return

        with open(bets_file, 'r') as f:
            bets_data = json.load(f)
            
        if bets_data.get('status') == 'CLOSED':
            print(f"Bets for {date_str} already processed.")
            return

        # Load verification data (Actual Results)
        df_verify = pd.read_csv(verification_file_path)
        
        # Simple lookup dict
        results_map = {}
        for _, row in df_verify.iterrows():
            key = f"{row['Home Team']} vs {row['Away Team']}"
            results_map[key] = {
                '1X2': row['Actual 1X2'],
                'O/U': row['Actual O/U']
            }
            
        total_pnl = 0.0
        total_return = 0.0
        return_by_lane = {lane: 0.0 for lane in LANES}
        pnl_by_lane = {lane: 0.0 for lane in LANES}
        won_bets = 0
        lost_bets = 0

        bets = bets_data.get('bets', [])
        for bet in bets:
            lane = bet.get('lane', 'value')
            if lane not in LANES:
                lane = 'value'
            match_key = bet['match']
            stake = float(bet.get('stake_units', 0))

            if match_key not in results_map:
                bet['status'] = 'VOID'
                return_by_lane[lane] += stake
                total_return += stake
                bet['pnl'] = 0.0
                continue

            actual = results_map[match_key]
            odds = float(bet.get('odds', 0))
            selection = bet['selection']

            won = False
            if bet['type'] == '1X2':
                if str(selection) == str(actual['1X2']):
                    won = True
            elif bet['type'] == 'O/U':
                if str(selection) == str(actual['O/U']):
                    won = True

            if won:
                payout = stake * odds
                profit = payout - stake
                return_by_lane[lane] += payout
                pnl_by_lane[lane] += profit
                total_return += payout
                total_pnl += profit
                bet['result'] = 'WON'
                bet['pnl'] = profit
                won_bets += 1
            else:
                pnl_by_lane[lane] -= stake
                total_pnl -= stake
                bet['result'] = 'LOST'
                bet['pnl'] = -stake
                lost_bets += 1

        # Settlement: credit each lane's returns back to its own bankroll.
        new_lane_br = {}
        for lane, ret in return_by_lane.items():
            if ret > 0:
                new_lane_br[lane] = update_bankroll('football', ret, lane=lane)
            else:
                new_lane_br[lane] = get_bankroll('football', lane=lane)

        bets_data['status'] = 'CLOSED'
        bets_data['pnl'] = total_pnl
        bets_data['total_return'] = total_return
        bets_data['return_by_lane'] = {k: round(v, 2) for k, v in return_by_lane.items()}
        bets_data['pnl_by_lane'] = {k: round(v, 2) for k, v in pnl_by_lane.items()}
        with open(bets_file, 'w') as f:
            json.dump(bets_data, f, indent=4)

        new_bankroll = round(sum(new_lane_br.values()), 2)
        print(f"Processed Bets for {date_str}: Returns {total_return:.2f} (Net P/L {total_pnl:.2f}). "
              f"Lane balances: {new_lane_br}. Total: {new_bankroll:.2f}")

    except Exception as e:
        print(f"Error processing bet verification: {e}")

@football_bp.route('/predict', methods=['POST'])
def run_prediction():
    if TASKS['predict'] and TASKS['predict']['process'] and TASKS['predict']['process'].poll() is None:
         flash('Prediction is already running!', 'warning')
         return redirect(url_for('football.index'))

    try:
        # Execute run_predictions.sh from project root
        script_path = os.path.join(PROJECT_ROOT, 'bin', 'run_predictions.sh')
        log_file = open(os.path.join(LOG_DIR, 'predict.log'), 'w')
        
        cmd = ['/bin/bash', script_path]
        
        # Add Date Arg
        date_arg = request.form.get('date')
        if date_arg:
            cmd.append(date_arg)
            
        if request.form.get('force'):
            cmd.append('--force')
            
        proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        
        TASKS['predict'] = {'process': proc, 'start_time': datetime.datetime.now()}
        
        flash('Prediction pipeline started! Check <a href="/logs/predict.log">logs</a> for status.', 'success')
    except Exception as e:
        flash(f'Error starting prediction: {e}', 'danger')
        
    return redirect(url_for('football.index'))

@football_bp.route('/verify', methods=['POST'])
def run_verification():
    if TASKS['verify'] and TASKS['verify']['process'] and TASKS['verify']['process'].poll() is None:
         flash('Verification is already running!', 'warning')
         return redirect(url_for('football.index'))

    # 1. Retrieve Date
    date_arg = request.form.get('date')
    if date_arg:
        target_date = date_arg
    else:
        # Default to yesterday
        target_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
    pred_file = os.path.join(OUTPUT_DIR, f"predictions_{target_date}.csv")
    
    # Simple check for the most likely target. 
    # If the user provides a custom date to the script, this check is bypassed, 
    # but the UI button is for "Yesterday".
    if not os.path.exists(pred_file):
        flash(f'Error: Predictions file for {target_date} not found. Cannot verify.', 'danger')
        return redirect(url_for('football.index'))

    try:
        script_path = os.path.join(PROJECT_ROOT, 'bin', 'run_verification.sh')
        log_file = open(os.path.join(LOG_DIR, 'verify.log'), 'w')
        
        cmd = ['/bin/bash', script_path]
        if date_arg:
            cmd.append(date_arg)
            
        proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        
        TASKS['verify'] = {'process': proc, 'start_time': datetime.datetime.now()}
        
        flash('Verification pipeline started! Check <a href="/logs/verify.log">logs</a> for status.', 'success')
    except Exception as e:
        flash(f'Error starting verification: {e}', 'danger')
        
    return redirect(url_for('football.index'))

@football_bp.route('/logs/<filename>')
def view_log(filename):
    filepath = os.path.join(LOG_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            content = f.read()
        return f"<pre>{content}</pre>"
    else:
        return "Log file not found."

def _archive_file(filepath, filename):
    """
    Soft-delete: move filepath to output/history/, returning the archived path.
    On name collision in history, append a timestamp suffix so nothing gets clobbered.
    """
    os.makedirs(HISTORY_DIR, exist_ok=True)
    target = os.path.join(HISTORY_DIR, filename)
    if os.path.exists(target):
        base, ext = os.path.splitext(filename)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        target = os.path.join(HISTORY_DIR, f"{base}.{ts}{ext}")
    shutil.move(filepath, target)
    return target


@football_bp.route('/delete_file/<filename>', methods=['POST'])
def delete_file(filename):
    """
    Soft-delete: archive the file to output/history/ instead of removing.
    Bet slips remain readable by the cumulative comparison aggregator;
    they just disappear from the visible UI lists.
    """
    if os.path.sep in filename or '..' in filename:
        flash('Invalid filename!', 'danger')
        return redirect(url_for('football.index'))

    filepath = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(filepath):
        try:
            _archive_file(filepath, filename)
            flash(f'Archived {filename} (moved to history/).', 'success')
        except OSError as e:
            flash(f'Error archiving file: {e}', 'danger')
    else:
        flash('File not found.', 'warning')

    return redirect(request.referrer or url_for('football.index'))

@football_bp.route('/view/<filename>')
def view_file(filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        flash('File not found!', 'danger')
        return redirect(url_for('football.index'))
        
    try:
        df = pd.read_csv(filepath)
        df = df.fillna('')  # Ensure NaNs are empty strings so template .split() works
        
        # Load Cumulative Stats from JSON
        league_stats = []
        stats_file = os.path.join(PROJECT_ROOT, 'data_sets/league_analytics.json')
        if os.path.exists(stats_file):
            try:
                with open(stats_file, 'r') as f:
                    stats_data = json.load(f)
                    
                # Convert dict to list for template
                for league, s in stats_data.items():
                    # Calculate percentages
                    total = s['total_matches']
                    if total > 0:
                        acc_1x2 = round((s['correct_1x2'] / total) * 100, 2)
                        acc_ou = round((s['correct_ou'] / total) * 100, 2)
                    else:
                        acc_1x2 = 0
                        acc_ou = 0
                        
                    league_stats.append({
                        'League': league,
                        'Count': total,
                        'Acc_1X2': acc_1x2,
                        'Acc_OU': acc_ou
                    })
                # Sort by Count desc
                league_stats.sort(key=lambda x: x['Count'], reverse=True)
            except Exception as e:
                print(f"Error loading league stats json: {e}")

        # Convert to list of dicts for easy iteration
        data = df.to_dict(orient='records')
        
        # Define Preferred Order
        # prediction_cols starts with basic info
        # prediction_cols starts with basic info
        base_cols = ['Date', 'League', 'Home Team', 'Away Team', 'Home', 'Away', 'Score']
        
        # User requested 1x2 cluster then O/U cluster
        target_cols = [
            'Prediction 1X2', 'Prediction 1X2 Odd', 'Conf 1X2', 'EV 1X2', 'Kelly 1X2', 'Home Win %', 'Draw %', 'Away Win %', 
            'Pred 1X2', 'Actual 1X2', 'Correct 1X2 Label', # Verification variants
            
            'Prediction O/U', 'Prediction O/U Odd', 'Conf O/U', 'EV O/U', 'Kelly O/U', 'Over %', 'Under %',
            'Pred O/U', 'Actual O/U', 'Correct O/U Label' # Verification variants
        ]
        
        final_cols = []
        # Add base columns if they exist
        for c in base_cols:
            if c in df.columns:
                final_cols.append(c)
        
        # Add target columns if they exist
        for c in target_cols:
             if c in df.columns:
                 final_cols.append(c)
                 
        # Add any remaining columns (e.g. Form), but exclude user-requested removals
        # Excluding: Home ELO, Away ELO, Home Form, Away Form, Match, and redundant booleans
        exclude_cols = ['Home ELO', 'Away ELO', 'Home Form', 'Away Form', 'Adj Logs', 'Match', 'Correct 1X2', 'Correct O/U']
        existing = set(final_cols)
        for c in df.columns:
            if c not in existing and c not in exclude_cols:
                final_cols.append(c)
        
        columns = final_cols
        count = len(data)
        return render_template('results.html', filename=filename, columns=columns, data=data, league_stats=league_stats, count=count)
    except Exception as e:
        flash(f'Error reading file: {e}', 'danger')
        return redirect(url_for('football.index'))

@football_bp.route('/live_analysis')
def live_analysis():
    live_file = os.path.join(OUTPUT_DIR, "live_data.json")
    matches_data = []
    
    if os.path.exists(live_file):
        try:
            with open(live_file, 'r') as f:
                matches_data = json.load(f)
        except Exception as e:
            print(f"Error loading live data: {e}")
            
    # Fallback/Empty state handled in template
    return render_template('live.html', matches=matches_data)

@football_bp.route('/refresh_live', methods=['POST'])
def refresh_live():
    # Trigger the script
    script_path = os.path.join(PROJECT_ROOT, 'scripts', 'run_live_analysis.py')
    try:
        # Run in background or wait?
        # User said "UI takes too long", so background is better, but then we need polling.
        # For simplicity now, let's wait (blocking) but user knows it takes time.
        # Or spawn Popen.
        if TASKS.get('live') and TASKS['live']['process'] and TASKS['live']['process'].poll() is None:
             flash('Live analysis is already running!', 'warning')
             return redirect(url_for('football.index'))
             
        log_file = open(os.path.join(LOG_DIR, 'live.log'), 'w')
        proc = subprocess.Popen(['venv/bin/python', script_path], cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        
        TASKS['live'] = {'process': proc, 'start_time': datetime.datetime.now()}
        
        flash('Live analysis started! Auto-refreshing...', 'info')
    except Exception as e:
        flash(f"Error starting live analysis: {e}", 'danger')
        
    return redirect(url_for('football.index'))

@football_bp.route('/clear_live', methods=['POST'])
def clear_live():
    live_file = os.path.join(OUTPUT_DIR, "live_data.json")
    try:
        with open(live_file, 'w') as f:
            json.dump([], f)
        flash('Live data cleared.', 'success')
    except Exception as e:
        flash(f'Error clearing live data: {e}', 'danger')
        
    return redirect(url_for('football.index'))

@football_bp.route('/reset_stats', methods=['POST'])
def reset_stats():
    stats_file = os.path.join(PROJECT_ROOT, 'data_sets/league_analytics.json')
    check_file = os.path.join(PROJECT_ROOT, 'data_sets/league_analytics_check.json')
    try:
        deleted = False
        if os.path.exists(stats_file):
            os.remove(stats_file)
            deleted = True
        if os.path.exists(check_file):
            os.remove(check_file)
            deleted = True
        if deleted:
            flash('Cumulative League Statistics have been reset.', 'success')
        else:
            flash('No statistics file found to reset.', 'warning')
    except Exception as e:
        flash(f'Error resetting stats: {e}', 'danger')
    return redirect(url_for('football.index'))

@football_bp.route('/update_leagues', methods=['POST'])
def update_leagues():
    script_path = os.path.join(PROJECT_ROOT, 'bin', 'update_leagues_data.sh')
    try:
        if TASKS.get('leagues') and TASKS['leagues']['process'] and TASKS['leagues']['process'].poll() is None:
             flash('Leagues update is already running!', 'warning')
             return redirect(url_for('football.index'))
             
        log_file = open(os.path.join(LOG_DIR, 'leagues.log'), 'w')
        # Use simple bash execution
        proc = subprocess.Popen(['/bin/bash', script_path], cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        
        TASKS['leagues'] = {'process': proc, 'start_time': datetime.datetime.now()}
        
        flash('Leagues data update started! Check <a href="/logs/leagues.log">logs</a> for status.', 'success')
    except Exception as e:
        flash(f"Error starting leagues update: {e}", 'danger')
        
    return redirect(url_for('football.index'))

@app.route('/server/<action>', methods=['POST'])
def server_control(action):
    if action not in ['restart', 'stop']:
        return "Invalid action", 400
        
    try:
        # Use nohup to ensure the script survives the server killing itself
        # We need to detach properly.
        script = os.path.join(PROJECT_ROOT, 'bin', 'manage_server.sh')
        cmd = ['nohup', '/bin/bash', script, action]
        
        # Popen with start_new_session=True is key
        subprocess.Popen(cmd, cwd=PROJECT_ROOT, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if action == 'stop':
            return "Server stopping...", 200
        else:
             # Return HTML that auto-redirects after 5 seconds
             return """
             <html>
             <head>
                <meta http-equiv="refresh" content="5;url=/" />
                <title>Restarting...</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
             </head>
             <body class="d-flex justify-content-center align-items-center vh-100 bg-light">
               <div class="text-center">
                 <h1 class="display-4">🔄 Restarting...</h1>
                 <p class="lead">The server is rebooting. You will be redirected to the dashboard in 5 seconds.</p>
                 <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                 </div>
               </div>
             </body>
             </html>
             """, 200
             
    except Exception as e:
        return f"Error: {e}", 500


@football_bp.route('/place_bets', methods=['POST'])
def place_bets():
    try:
        data = request.get_json()
        bets = data.get('bets', [])

        extracted_date = None
        if bets:
            first_date = bets[0].get('date', '')
            if first_date:
                try:
                    extracted_date = first_date.split(' ')[0]
                except: pass

        date_str = extracted_date if extracted_date else data.get('date', datetime.datetime.now().strftime('%Y-%m-%d'))

        if not bets:
            return jsonify({'error': 'No bets provided.'}), 400

        # Group stakes by lane, validate funds per lane, then debit each lane.
        stake_by_lane = {lane: 0.0 for lane in LANES}
        for b in bets:
            lane = b.get('lane', 'value')
            if lane not in LANES:
                lane = 'value'
                b['lane'] = lane
            stake_by_lane[lane] += float(b.get('stake_units', 0))

        current_lane_br = lane_bankrolls('football')
        for lane, stake in stake_by_lane.items():
            if stake > current_lane_br[lane] + 1e-6:
                return jsonify({'error': (
                    f"Insufficient {lane} funds. Stake ({stake:.2f}) exceeds "
                    f"{lane} bankroll ({current_lane_br[lane]:.2f})."
                )}), 400

        new_lane_br = {}
        for lane, stake in stake_by_lane.items():
            if stake > 0:
                new_lane_br[lane] = update_bankroll('football', -stake, lane=lane)
            else:
                new_lane_br[lane] = current_lane_br[lane]

        total_stake = sum(stake_by_lane.values())

        filename = f"bets_{date_str}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)

        with open(filepath, 'w') as f:
            json.dump({
                'date': date_str,
                'count': len(bets),
                'bets': bets,
                'total_stake': total_stake,
                'stake_by_lane': {k: round(v, 2) for k, v in stake_by_lane.items()},
                'status': 'OPEN',
                'pnl': 0.0,
                'settled': False
            }, f, indent=4)

        return jsonify({
            'message': f"Successfully placed {len(bets)} virtual bets! Deducted {total_stake:.2f} across lanes.",
            'file': filename,
            'new_balance': round(sum(new_lane_br.values()), 2),
            'lane_bankrolls': {k: round(v, 2) for k, v in new_lane_br.items()},
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _load_slip(filepath):
    """Load a single bets_*.json with backfilled fields."""
    try:
        with open(filepath, 'r') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    data['filename'] = os.path.basename(filepath)
    if 'total_stake' not in data:
        data['total_stake'] = sum(float(b.get('stake_units', 0)) for b in data.get('bets', []))
    if 'total_return' not in data:
        data['total_return'] = (
            data.get('total_stake', 0) + data.get('pnl', 0)
            if data.get('status') == 'CLOSED' else 0.0
        )
    return data


def compute_sport_summary(bets_dir):
    """
    Aggregate stats for one sport from its bets directory.
    Reads ACTIVE slips (bets_dir/bets_*.json) AND ARCHIVED slips
    (bets_dir/history/bets_*.json) so soft-deleted slips still count.

    Returns: {
        'history': [active slips, sorted desc by date],
        'lane_stats': {lane: {bets, settled, won, lost, void, stake,
                              returned, pnl, win_rate, roi}},
        'totals': {bets, settled, stake, returned, pnl, roi},
    }
    """
    abs_dir = bets_dir if os.path.isabs(bets_dir) else os.path.join(PROJECT_ROOT, bets_dir)
    history_dir = os.path.join(abs_dir, 'history')

    history = []
    for f in glob.glob(os.path.join(abs_dir, "bets_*.json")):
        s = _load_slip(f)
        if s: history.append(s)
    history.sort(key=lambda x: x.get('date', ''), reverse=True)

    archived_slips = []
    for f in glob.glob(os.path.join(history_dir, "bets_*.json")):
        s = _load_slip(f)
        if s: archived_slips.append(s)

    def _empty():
        return {'bets': 0, 'settled': 0, 'won': 0, 'lost': 0, 'void': 0,
                'stake': 0.0, 'returned': 0.0, 'pnl': 0.0}
    lane_stats = {lane: _empty() for lane in LANES}

    for slip in (history + archived_slips):
        for bet in slip.get('bets', []):
            lane = bet.get('lane', 'value')
            s = lane_stats.setdefault(lane, _empty())
            stake = float(bet.get('stake_units', 0))
            status = bet.get('status', 'OPEN')
            result = bet.get('result', '')

            s['bets'] += 1
            s['stake'] += stake

            if status == 'OPEN':
                continue

            s['settled'] += 1
            if result == 'WON' or status == 'WON':
                s['won'] += 1
                s['returned'] += stake + float(bet.get('pnl', 0))
                s['pnl'] += float(bet.get('pnl', 0))
            elif result == 'LOST' or status == 'LOST':
                s['lost'] += 1
                s['pnl'] += float(bet.get('pnl', -stake))
            else:  # VOID
                s['void'] += 1
                s['returned'] += stake

    for lane, s in lane_stats.items():
        decided = s['won'] + s['lost']
        s['win_rate'] = round((s['won'] / decided * 100) if decided > 0 else 0.0, 1)
        s['roi'] = round((s['pnl'] / s['stake'] * 100) if s['stake'] > 0 else 0.0, 1)
        s['stake'] = round(s['stake'], 2)
        s['returned'] = round(s['returned'], 2)
        s['pnl'] = round(s['pnl'], 2)

    totals = {
        'bets': sum(s['bets'] for s in lane_stats.values()),
        'settled': sum(s['settled'] for s in lane_stats.values()),
        'stake': round(sum(s['stake'] for s in lane_stats.values()), 2),
        'returned': round(sum(s['returned'] for s in lane_stats.values()), 2),
        'pnl': round(sum(s['pnl'] for s in lane_stats.values()), 2),
    }
    totals['roi'] = round((totals['pnl'] / totals['stake'] * 100) if totals['stake'] > 0 else 0.0, 1)

    return {'history': history, 'lane_stats': lane_stats, 'totals': totals}


@football_bp.route('/betting')
def betting_page():
    summary = compute_sport_summary(OUTPUT_DIR)
    return render_template('betting.html',
                           history=summary['history'],
                           lane_stats=summary['lane_stats'],
                           sport_label='Football')

# Update Server Routes logic...
@football_bp.route('/live_analysis', methods=['POST'])
def run_live_analysis():
    task_name = 'live'
    if TASKS[task_name] and TASKS[task_name]['process'] and TASKS[task_name]['process'].poll() is None:
        flash('Live Analysis Loop is already running!', 'warning')
        return redirect(url_for('football.index'))

    cmd = [sys.executable, 'scripts/run_live_loop.py']
    log_file = open(os.path.join(LOG_DIR, TASKS[task_name]['log']), 'w')
    
    # Start process
    TASKS[task_name]['process'] = subprocess.Popen(
        cmd, 
        stdout=log_file, 
        stderr=subprocess.STDOUT,
        cwd=PROJECT_ROOT
    )
    
    flash('Live Analysis Loop started (running every 10 mins).', 'success')
    return redirect(url_for('football.index'))

@football_bp.route('/update_data', methods=['POST'])
def update_data():
    if TASKS.get('update') and TASKS['update']['process'] and TASKS['update']['process'].poll() is None:
         flash('Data update is already running!', 'warning')
         return redirect(url_for('football.index'))

    try:
        script_path = os.path.join(PROJECT_ROOT, 'scripts', 'update_football_data.py')
        log_file = open(os.path.join(LOG_DIR, 'update_data.log'), 'w')
        proc = subprocess.Popen(['venv/bin/python', script_path], cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        
        TASKS['update'] = {'process': proc, 'start_time': datetime.datetime.now()}
        
        flash('Data update started! Check <a href="/logs/update_data.log">logs</a> for status.', 'success')
    except Exception as e:
        flash(f"Error starting update: {e}", 'danger')
        
    return redirect(url_for('football.index'))

@football_bp.route('/retrain_model', methods=['POST'])
def retrain_model():
    if TASKS.get('retrain') and TASKS['retrain']['process'] and TASKS['retrain']['process'].poll() is None:
         flash('Model Retraining is already running!', 'warning')
         return redirect(url_for('football.index'))

    try:
        script_path = os.path.join(PROJECT_ROOT, 'bin', 'retrain_pipeline.sh')
        log_file = open(os.path.join(LOG_DIR, 'retrain.log'), 'w')
        # Using bash directly
        proc = subprocess.Popen(['/bin/bash', script_path], cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        
        TASKS['retrain'] = {'process': proc, 'start_time': datetime.datetime.now()}
        
        flash('Full Retrain Pipeline started! Check <a href="/logs/retrain.log">logs</a> for progress.', 'success')
    except Exception as e:
        flash(f"Error starting retrain: {e}", 'danger')
        
    return redirect(url_for('football.index'))

import threading

def run_verify_task_thread(date_arg):
    try:
        cmd = ["/bin/bash", "run_verification.sh"]
        if date_arg:
            cmd.extend(["-d", date_arg])
            
        # Run process synchronously in this thread
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getcwd()
        )
        
        # We store the process in global TASKS so we can kill it if needed
        TASKS['verify']['process'] = process
        TASKS['verify']['state'] = 'running'
        
        stdout, stderr = process.communicate()
        
        if process.returncode == 0:
            TASKS['verify']['state'] = 'completed'
            TASKS['verify']['msg'] = 'Verification completed successfully.'
            
            # --- AUTO SETTLE BETS ---
            # Determine the verification filename.
            # run_verification.sh usually produces verify_YYYY-MM-DD.csv (Yesterday)
            # If date_arg is passed, it uses that date.
            target_date = date_arg if date_arg else (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            verify_file = os.path.join(OUTPUT_DIR, f"verify_{target_date}.csv")
            
            # Call settlement logic
            print(f"Triggering bet settlement for {verify_file}...")
            process_bet_verification(verify_file)
            # ------------------------
            
        else:
            TASKS['verify']['state'] = 'error'
            TASKS['verify']['msg'] = f"Error: {stderr}"
            
    except Exception as e:
        TASKS['verify']['state'] = 'error'
        TASKS['verify']['msg'] = str(e)
    finally:
        TASKS['verify']['process'] = None

@football_bp.route('/verify', methods=['POST'])
def run_verify():
    if TASKS['verify'] and TASKS['verify']['process'] and TASKS['verify']['process'].poll() is None:
        flash('Verification task is already running.', 'warning')
        return redirect(url_for('football.index'))

    # Reset state
    TASKS['verify'] = {'state': 'running', 'process': None, 'msg': ''}
    
    # Launch in thread
    thread = threading.Thread(target=run_verify_task_thread, args=(None,))
    thread.start()
    
    flash('Verification task started! Bankroll will update upon completion.', 'success')
    return redirect(url_for('football.index'))

import glob
import pandas as pd
from flask import jsonify

@football_bp.route('/auto_wager')
def auto_wager():
    try:
        pred_files = glob.glob(os.path.join(OUTPUT_DIR, 'predictions_*.csv'))
        if not pred_files:
            return jsonify({'error': 'No prediction files found.'})

        pred_files.sort(reverse=True)
        latest_file = pred_files[0]
        df = pd.read_csv(latest_file)

        def _to_float(v):
            try:
                if isinstance(v, str):
                    v = v.strip().rstrip('%')
                    if not v:
                        return 0.0
                return float(v)
            except (ValueError, TypeError):
                return 0.0

        def parse_kelly(k_str):
            v = _to_float(k_str)
            return v / 100.0 if isinstance(k_str, str) and '%' in k_str else v

        config = get_sport_config('football')
        lane_br = lane_bankrolls('football')

        # Shared
        min_stake_eur            = config['min_stake_eur']
        # Value lane
        min_confidence           = config['min_confidence']
        stake_multiplier         = config['stake_multiplier']
        max_stake_pct            = config['max_stake_pct']
        # Conviction lane
        conv_min_confidence      = config['conviction_min_confidence']
        conv_min_odds            = config['conviction_min_odds']
        conv_stake_pct           = config['conviction_stake_pct']
        # Model lane
        model_base_pct           = config['model_base_pct']
        model_max_stake_pct      = config['model_max_stake_pct']
        model_min_stake_eur      = config['model_min_stake_eur']
        ev_factor_min            = config['model_ev_factor_min']
        ev_factor_max            = config['model_ev_factor_max']

        def _override_bankroll(lane, default):
            """Allow ?bankroll_<lane>=N as a per-session override, capped at saved balance."""
            raw = request.args.get(f'bankroll_{lane}')
            if raw is None or raw == '':
                return default, None
            try:
                v = float(raw)
            except ValueError:
                return default, None
            if v <= 0:
                raise ValueError(f"{lane} session bankroll must be positive.")
            if v > default:
                raise ValueError(
                    f"{lane} session bankroll ({v}) cannot exceed saved balance ({default})."
                )
            return v, v

        def _override_cap_pct(lane, default):
            """?cap_<lane>=0.15 overrides this lane's daily exposure pct."""
            raw = request.args.get(f'cap_{lane}')
            if raw is None or raw == '':
                return default
            try:
                v = float(raw)
            except ValueError:
                return default
            if v <= 0 or v > 1.0:
                return default
            return v

        try:
            value_br, value_session     = _override_bankroll('value', lane_br['value'])
            conv_br, conv_session       = _override_bankroll('conviction', lane_br['conviction'])
            model_br, model_session     = _override_bankroll('model', lane_br['model'])
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        value_cap_pct = _override_cap_pct('value', config['value_max_daily_exposure_pct'])
        conv_cap_pct  = _override_cap_pct('conviction', config['conviction_max_daily_exposure_pct'])
        model_cap_pct = _override_cap_pct('model', config['model_max_daily_exposure_pct'])

        max_value_per_bet = value_br * max_stake_pct
        max_model_per_bet = model_br * model_max_stake_pct
        conv_flat_stake   = conv_br * conv_stake_pct

        def _common_fields(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col):
            return {
                'date': row.get('Date', ''),
                'match': f"{row['Home Team']} vs {row['Away Team']}",
                'home': row['Home Team'],
                'away': row['Away Team'],
                'match_id': row.get('match_id', ''),
                'league': row['League'],
                'type': bet_type,
                'selection': row[selection_col],
                'odds': row[odd_col],
                'odd': row[odd_col],
                'conf': f"{_to_float(row.get(conf_col, 0)):.2f}",
                'ev': f"{_to_float(row.get(ev_col, 0)):+.2f}",
                'kelly': f"{parse_kelly(row.get(kelly_col, '0%')):.2%}",
                'status': 'OPEN',
            }

        def build_value_bet(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col):
            """Option B: EV-gated, stake = bankroll * EV * conf * multiplier, capped & floored."""
            ev = _to_float(row.get(ev_col, 0))
            conf = _to_float(row.get(conf_col, 0))
            if ev <= 0 or conf < min_confidence:
                return None
            raw_stake = value_br * ev * conf * stake_multiplier
            stake = min(raw_stake, max_value_per_bet)
            if stake < min_stake_eur:
                return None
            bet = _common_fields(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col)
            bet.update({'lane': 'value', 'stake_units': round(stake, 2), 'stake': round(stake, 2)})
            return bet

        def build_conviction_bet(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col):
            """Conviction lane: high conf, odds ≥ floor, EV ignored. Flat stake."""
            conf = _to_float(row.get(conf_col, 0))
            odd = _to_float(row.get(odd_col, 0))
            if conf < conv_min_confidence or odd < conv_min_odds:
                return None
            stake = conv_flat_stake
            if stake < min_stake_eur:
                return None
            bet = _common_fields(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col)
            bet.update({'lane': 'conviction', 'stake_units': round(stake, 2), 'stake': round(stake, 2)})
            return bet

        def build_model_bet(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col):
            """Model lane: bet every prediction with valid odds. Sized by conf × ev_factor."""
            conf = _to_float(row.get(conf_col, 0))
            odd = _to_float(row.get(odd_col, 0))
            if conf <= 0 or odd <= 1.0:
                return None
            ev_factor = max(ev_factor_min, min(ev_factor_max, conf * odd))
            raw_stake = model_br * model_base_pct * conf * ev_factor
            stake = min(raw_stake, max_model_per_bet)
            if stake < model_min_stake_eur:
                return None
            bet = _common_fields(row, bet_type, selection_col, odd_col, conf_col, ev_col, kelly_col)
            bet.update({'lane': 'model', 'stake_units': round(stake, 2), 'stake': round(stake, 2)})
            return bet

        value_bets, conviction_bets, model_bets = [], [], []
        for _, row in df.iterrows():
            for bet_type, sel, odd, conf, ev, kelly in [
                ('1X2', 'Prediction 1X2', 'Prediction 1X2 Odd', 'Conf 1X2', 'EV 1X2', 'Kelly 1X2'),
                ('O/U', 'Prediction O/U', 'Prediction O/U Odd', 'Conf O/U', 'EV O/U', 'Kelly O/U'),
            ]:
                vb = build_value_bet(row, bet_type, sel, odd, conf, ev, kelly)
                if vb: value_bets.append(vb)
                cb = build_conviction_bet(row, bet_type, sel, odd, conf, ev, kelly)
                if cb: conviction_bets.append(cb)
                mb = build_model_bet(row, bet_type, sel, odd, conf, ev, kelly)
                if mb: model_bets.append(mb)

        def _enforce_daily_cap(bets, bankroll, cap_pct, lane_name, floor):
            """Per-lane daily cap: scale this lane proportionally if it exceeds cap. Drop sub-floor."""
            cap = bankroll * cap_pct
            total = sum(b['stake_units'] for b in bets)
            action = None
            if cap > 0 and total > cap:
                scale = cap / total
                for b in bets:
                    b['stake_units'] = round(b['stake_units'] * scale, 2)
                    b['stake'] = b['stake_units']
                bets = [b for b in bets if b['stake_units'] >= floor]
                action = f"{lane_name}-scaled"
            return bets, cap, action

        value_bets, value_cap, value_action     = _enforce_daily_cap(value_bets, value_br, value_cap_pct, 'value', min_stake_eur)
        conviction_bets, conv_cap, conv_action  = _enforce_daily_cap(conviction_bets, conv_br, conv_cap_pct, 'conviction', min_stake_eur)
        model_bets, model_cap, model_action     = _enforce_daily_cap(model_bets, model_br, model_cap_pct, 'model', model_min_stake_eur)

        value_total      = sum(b['stake_units'] for b in value_bets)
        conviction_total = sum(b['stake_units'] for b in conviction_bets)
        model_total      = sum(b['stake_units'] for b in model_bets)
        all_bets = value_bets + conviction_bets + model_bets
        total_stake = value_total + conviction_total + model_total

        return jsonify({
            'filename': os.path.basename(latest_file),
            'bankroll': round(value_br + conv_br + model_br, 2),
            'count': len(all_bets),
            'total_stake': round(total_stake, 2),
            'bets': all_bets,
            'lanes': {
                'value': {
                    'count': len(value_bets),
                    'total_stake': round(value_total, 2),
                    'bankroll': round(value_br, 2),
                    'bets': value_bets,
                },
                'conviction': {
                    'count': len(conviction_bets),
                    'total_stake': round(conviction_total, 2),
                    'bankroll': round(conv_br, 2),
                    'bets': conviction_bets,
                },
                'model': {
                    'count': len(model_bets),
                    'total_stake': round(model_total, 2),
                    'bankroll': round(model_br, 2),
                    'bets': model_bets,
                },
            },
            'guardrails': {
                'value': {
                    'min_confidence': min_confidence,
                    'stake_multiplier': stake_multiplier,
                    'max_stake_per_bet': round(max_value_per_bet, 2),
                    'daily_cap_pct': value_cap_pct,
                    'daily_cap_eur': round(value_cap, 2),
                    'cap_action': value_action,
                },
                'conviction': {
                    'min_confidence': conv_min_confidence,
                    'min_odds': conv_min_odds,
                    'flat_stake_eur': round(conv_flat_stake, 2),
                    'daily_cap_pct': conv_cap_pct,
                    'daily_cap_eur': round(conv_cap, 2),
                    'cap_action': conv_action,
                },
                'model': {
                    'base_pct': model_base_pct,
                    'max_stake_per_bet': round(max_model_per_bet, 2),
                    'ev_factor_range': [ev_factor_min, ev_factor_max],
                    'daily_cap_pct': model_cap_pct,
                    'daily_cap_eur': round(model_cap, 2),
                    'cap_action': model_action,
                },
                'shared': {
                    'min_stake_eur': min_stake_eur,
                },
            },
        })

    except Exception as e:
        return jsonify({'error': f"Internal Error: {str(e)}"})

@app.context_processor
def inject_bankroll():
    """
    Sport-aware navbar bankroll. On a sport page, show that sport's
    balance; on the landing page (or any sport-agnostic page), show the
    portfolio total. Templates also get `bankrolls` (per-sport map) so
    they can render a breakdown if they want.
    """
    bankrolls = all_bankrolls()
    bp = request.blueprint  # 'football', 'nba', or None
    if bp and bp in bankrolls:
        active = bankrolls[bp]
        label = bp.capitalize()
    else:
        active = total_bankroll()
        label = 'Total'
    return dict(bankroll=active, bankroll_label=label, bankrolls=bankrolls)


@app.context_processor
def inject_sports():
    """Make the SPORTS list available to every template (for navbar picker)."""
    return dict(sports=SPORTS)


# --- Sport-agnostic landing page ---
@app.route('/')
def landing():
    """Sport picker + portfolio summary. Active sports link to their dashboards.
    Per-sport stats are aggregated from each sport's bets directory (active +
    archived slips), so the portfolio totals stay correct after soft-deletes."""
    sport_summaries = {}
    for sport in SPORTS:
        bets_dir = sport.get('bets_dir')
        if bets_dir:
            sport_summaries[sport['slug']] = compute_sport_summary(bets_dir)['totals']
    return render_template('landing.html', sport_summaries=sport_summaries)


# Register sport blueprints after their routes have been defined.
app.register_blueprint(football_bp, url_prefix='/football')


if __name__ == '__main__':
    # Disable debug for performance
    app.run(debug=False, port=5001, host='0.0.0.0')
