from flask import Flask, render_template, request, redirect, url_for, flash, Blueprint, g
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
    set_tunables,
    DEFAULT_SPORT_CONFIG,
    LANES,
)

# Betting backend abstraction (Phase 7 + transition to live betting —
# see docs/LIVE_BETTING_TRANSITION.md). Today only the virtual
# implementation is wired; live blueprint isn't registered.
from betting_backend import VirtualBettingBackend, make_bet_id

# In-UI docs renderer — converts the Markdown source under docs/ to HTML
# on demand for the 📚 Docs nav menu. Source files stay in Markdown.
import markdown as _md

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


# Inject the appropriate BettingBackend on every football request.
# `g.backend` is the abstraction routes use for cashout (Phase 7) and,
# eventually, for balance + place + settle (Phase 9, when the
# `/football/live/` blueprint lands with the same hook returning
# `PamestoiximaBackend`). See docs/LIVE_BETTING_TRANSITION.md.
@football_bp.before_request
def _inject_backend():
    g.backend = VirtualBettingBackend(output_dir=OUTPUT_DIR)
    g.mode = 'virtual'


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
    'live': {'process': None, 'log': 'live.log'},
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

    # Enrich each live match with any OPEN bets we have on it.
    # Lets the dashboard show stake / odds / fair-value cashout per bet
    # alongside the live stats. Read-only — actual cashout action is
    # gated behind NEXT_STEPS phase 7.
    _attach_open_bets(live_matches)

    return render_template('dashboard.html',
                          predictions=predictions,
                          verifications=verifications,
                          league_stats=league_stats,
                          live_matches=live_matches[:50],
                          scraped_data=scraped_data)


# Selection → adjusted-probs key for fair-value cashout estimation.
_SELECTION_TO_PROB_KEY = {
    '1': 'home', 1: 'home',
    'X': 'draw', 'x': 'draw',
    '2': 'away', 2: 'away',
    'Over 2.5':  'over',  'Over': 'over',
    'Under 2.5': 'under', 'Under': 'under',
}

_CASHOUT_HOUSE_HAIRCUT = 0.95


def _attach_open_bets(live_matches):
    """Mutate each live match dict, adding `open_bets` (list).

    Each entry: {lane, type, selection, stake, odds, adj_prob,
    fair_cashout, status_badge}.

    Joined by match string ("Home vs Away"). Today's slip only;
    archived slips are skipped because OPEN bets can't be there.
    """
    today_slip = os.path.join(OUTPUT_DIR, f"bets_{datetime.date.today().isoformat()}.json")
    if not os.path.exists(today_slip):
        for m in live_matches:
            m['open_bets'] = []
        return

    try:
        with open(today_slip, 'r') as f:
            slip = json.load(f)
    except (OSError, json.JSONDecodeError):
        for m in live_matches:
            m['open_bets'] = []
        return

    # One-time backfill: stamp bet_id + mode='virtual' onto any pre-Phase-7
    # OPEN bets in this slip. Without bet_id the Cash Out button can't
    # target them (the cashout endpoint looks bets up by bet_id). Persist
    # the slip if we stamped anything.
    slip_date = slip.get('date') or datetime.date.today().isoformat()
    mutated = False
    for bet in slip.get('bets', []):
        if bet.get('status') != 'OPEN':
            continue
        if not bet.get('bet_id'):
            match_str = bet.get('match', '')
            home = bet.get('home') or (match_str.split(' vs ')[0].strip()
                                       if ' vs ' in match_str else '')
            away = bet.get('away') or (match_str.split(' vs ')[1].strip()
                                       if ' vs ' in match_str else '')
            if home and away:
                bet['bet_id'] = make_bet_id(
                    slip_date, home, away,
                    bet.get('type', '1X2'),
                    bet.get('selection', ''),
                )
                mutated = True
        if not bet.get('mode'):
            bet['mode'] = 'virtual'
            mutated = True
    if mutated:
        try:
            with open(today_slip, 'w') as f:
                json.dump(slip, f, indent=4)
        except OSError as e:
            print(f"Warning: could not persist bet_id backfill: {e}")

    # Build lookup: match_str → list of OPEN bets.
    # IMPORTANT: only OPEN bets show on live rows. Any terminal status
    # (WON, LOST, VOID, CASHED_OUT) is excluded — including a per-bet
    # CASHED_OUT inside a match that has other still-open bets on it
    # (different lane, different market). The earlier "skip if result
    # in WON/LOST/VOID" form leaked CASHED_OUT through because
    # result='CASHED_OUT' wasn't in that set. Be explicit instead.
    by_match = {}
    for bet in slip.get('bets', []):
        status = bet.get('status', '')
        if status not in ('OPEN', ''):
            continue
        match_str = bet.get('match', '').strip()
        if match_str:
            by_match.setdefault(match_str, []).append(bet)

    for m in live_matches:
        if m.get('message'):
            m['open_bets'] = []
            continue
        match_str = m.get('match', '').strip()
        related = by_match.get(match_str, [])

        enriched = []
        for bet in related:
            stake = float(bet.get('stake_units', 0))
            odds = float(bet.get('odds', 0))
            bet_type = bet.get('type', '1X2')
            selection = bet.get('selection')
            prob_key = _SELECTION_TO_PROB_KEY.get(selection)

            # 1X2 reads adj_probs; O/U reads adj_ou_probs (persisted by
            # run_live_analysis.py via LiveAdjuster.adjust_ou_probabilities).
            adj_prob = None
            fair_cashout = None
            if bet_type == '1X2' and prob_key in ('home', 'draw', 'away'):
                adj_prob = float(m.get('adj_probs', {}).get(prob_key, 0))
                fair_cashout = round(stake * odds * adj_prob * _CASHOUT_HOUSE_HAIRCUT, 2)
            elif bet_type == 'O/U' and prob_key in ('over', 'under'):
                adj_prob = float(m.get('adj_ou_probs', {}).get(prob_key, 0))
                fair_cashout = round(stake * odds * adj_prob * _CASHOUT_HOUSE_HAIRCUT, 2)

            # Status badge (informational only; no auto-action attached).
            badge = 'hold'
            if fair_cashout is not None and stake > 0:
                if fair_cashout / stake >= 1.5:
                    badge = 'lock_in'
                elif adj_prob is not None and adj_prob < 0.20:
                    badge = 'stop_loss'

            enriched.append({
                'lane': bet.get('lane', 'value'),
                'type': bet_type,
                'selection': str(selection),
                'stake': round(stake, 2),
                'odds': odds,
                'adj_prob': round(adj_prob, 3) if adj_prob is not None else None,
                'fair_cashout': fair_cashout,
                'badge': badge,
                # Pass bet_id through so the dashboard's Cash Out button
                # can target the right bet via /football/cashout/<bet_id>.
                # Pre-Phase-7 slips don't carry bet_id; the template
                # hides the button in that case.
                'bet_id': bet.get('bet_id'),
            })
        m['open_bets'] = enriched

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

# NOTE: `process_bet_verification` used to live here. It was deleted on
# 2026-05-21 after being identified as long-time dead code (called with
# the wrong filename, silently no-op'd). Bet settlement is the canonical
# responsibility of `ml_project/resolve_daily_bets.py:resolve_all_bets`,
# which is invoked by `bin/run_verification.sh` at the end of the
# verification flow. `VirtualBettingBackend.settle_bets` also delegates
# to that function for the Phase 7+ backend abstraction.

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
    bets_file = os.path.join(OUTPUT_DIR, f"bets_{target_date}.json")

    # Allow verification when EITHER predictions or open bets exist for
    # the target date. `bin/run_verification.sh` falls back to bet-
    # derived match IDs when predictions are missing — same UI gate
    # should reflect that.
    if not os.path.exists(pred_file) and not os.path.exists(bets_file):
        flash(f'Error: neither predictions ({os.path.basename(pred_file)}) nor '
              f'bets ({os.path.basename(bets_file)}) exists for {target_date}. '
              f'Nothing to verify.', 'danger')
        return redirect(url_for('football.index'))
    if not os.path.exists(pred_file):
        flash(f'No predictions file for {target_date}; verifying against open '
              f'bets only (no prediction-accuracy report will be produced).',
              'info')

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


# --- Docs renderer ---------------------------------------------------------
# Whitelist of doc names → source paths. Restricting to a known set avoids
# any path-traversal concerns and means we control which docs the UI
# exposes (NEXT_STEPS, internal planning docs, etc. don't show up here).
_DOCS = {
    'betting_strategy': {
        'title': 'Betting Strategy',
        'icon': '💸',
        'path': os.path.join(PROJECT_ROOT, 'docs', 'betting_strategy.md'),
    },
    'ui_manual': {
        'title': 'UI Manual',
        'icon': '🖥️',
        'path': os.path.join(PROJECT_ROOT, 'docs', 'ui_manual.md'),
    },
}


# --- Strategy tunables editor (Phase 1) ----------------------------------
# Lane-grouped form for the editable knobs in DEFAULT_SPORT_CONFIG.
# Bankroll state (current/initial) is NOT editable here — that's separate.
#
# Each entry: {key, label, lane, unit, step, min, max, kind, description}.
# - `kind`: 'pct' (UI shows %, stored as fraction), 'num' (raw decimal),
#           'eur' (EUR), 'odds' (decimal odds ≥ 1), 'bool' (checkbox)
# - `min`/`max` are in UI units; for `pct` they're 0-100, internally 0-1.
_TUNABLE_SPEC = [
    # Value lane
    {'key': 'min_confidence', 'label': 'Min confidence', 'lane': 'value',
     'kind': 'num', 'min': 0, 'max': 1, 'step': 0.01,
     'description': 'Entry filter — model confidence must be ≥ this to consider a Value-lane bet.'},
    {'key': 'stake_multiplier', 'label': 'Stake multiplier', 'lane': 'value',
     'kind': 'num', 'min': 0, 'max': 2, 'step': 0.05,
     'description': 'Multiplier in the Value-lane stake formula: stake = bankroll × EV × Conf × multiplier.'},
    {'key': 'min_stake_eur', 'label': 'Min stake', 'lane': 'value',
     'kind': 'eur', 'min': 0, 'max': 100, 'step': 0.5,
     'description': 'Picks whose computed stake falls below this floor are dropped from the slip.'},
    {'key': 'max_stake_pct', 'label': 'Max stake per bet', 'lane': 'value',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.1,
     'description': 'Hard cap per Value-lane bet, as a percentage of the lane bankroll.'},
    {'key': 'ev_cap_value', 'label': 'EV cap', 'lane': 'value',
     'kind': 'num', 'min': 0, 'max': 1, 'step': 0.01,
     'description': 'Clamp EV input to the stake formula. Stops a single high-EV pick from dominating the slip.'},
    {'key': 'value_max_daily_exposure_pct', 'label': 'Daily exposure cap', 'lane': 'value',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.5,
     'description': 'Max fraction of the Value-lane bankroll that can be staked in one day.'},

    # Conviction lane
    {'key': 'conviction_min_confidence', 'label': 'Min confidence', 'lane': 'conviction',
     'kind': 'num', 'min': 0, 'max': 1, 'step': 0.01,
     'description': 'Entry filter — confidence floor for Conviction lane (typically higher than Value).'},
    {'key': 'conviction_min_odds', 'label': 'Min odds', 'lane': 'conviction',
     'kind': 'odds', 'min': 1, 'max': 50, 'step': 0.05,
     'description': 'Conviction picks must have odds ≥ this. Filters out heavy favourites where stake is silly.'},
    {'key': 'conviction_stake_pct', 'label': 'Stake %', 'lane': 'conviction',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.1,
     'description': 'Flat stake fraction of the Conviction-lane bankroll, applied to every qualifying pick.'},
    {'key': 'conviction_max_daily_exposure_pct', 'label': 'Daily exposure cap', 'lane': 'conviction',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.5,
     'description': 'Max fraction of the Conviction-lane bankroll that can be staked in one day.'},

    # Model lane
    {'key': 'model_base_pct', 'label': 'Base stake %', 'lane': 'model',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.1,
     'description': 'Base stake fraction. Model lane: stake = bankroll × base_pct × Conf × ev_factor.'},
    {'key': 'model_max_stake_pct', 'label': 'Max stake per bet', 'lane': 'model',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.1,
     'description': 'Hard cap per Model-lane bet, as a percentage of the lane bankroll.'},
    {'key': 'model_min_stake_eur', 'label': 'Min stake', 'lane': 'model',
     'kind': 'eur', 'min': 0, 'max': 100, 'step': 0.5,
     'description': 'Model lane has its own (typically lower) floor so broad coverage isn\'t killed by the Value €2 floor.'},
    {'key': 'model_ev_factor_min', 'label': 'EV factor min', 'lane': 'model',
     'kind': 'num', 'min': 0, 'max': 5, 'step': 0.1,
     'description': 'Lower clamp for the Model-lane `ev_factor = clamp(Conf × odds, min, max)`.'},
    {'key': 'model_ev_factor_max', 'label': 'EV factor max', 'lane': 'model',
     'kind': 'num', 'min': 0, 'max': 5, 'step': 0.1,
     'description': 'Upper clamp for the Model-lane ev_factor.'},
    {'key': 'model_max_daily_exposure_pct', 'label': 'Daily exposure cap', 'lane': 'model',
     'kind': 'pct', 'min': 0, 'max': 100, 'step': 0.5,
     'description': 'Max fraction of the Model-lane bankroll that can be staked in one day.'},

    # Global (cross-lane)
    {'key': 'use_league_calibration', 'label': 'Apply per-league calibration', 'lane': 'global',
     'kind': 'bool',
     'description': 'When ON, per-league Platt scaling adjusts raw 1X2 / O/U probabilities before the heuristic adjuster.'},
]


def _ui_value(kind, fraction_value):
    """Convert stored value → UI value. Only `pct` needs conversion
    (stored as fraction 0-1, displayed as 0-100)."""
    if kind == 'pct':
        return round(float(fraction_value) * 100, 2)
    if kind == 'bool':
        return bool(fraction_value)
    return fraction_value


def _stored_value(kind, ui_value):
    """Convert UI value → stored value (inverse of _ui_value)."""
    if kind == 'pct':
        return float(ui_value) / 100.0
    if kind == 'bool':
        # Checkbox value: present in form when checked, absent when unchecked.
        return bool(ui_value)
    if kind in ('num', 'eur', 'odds'):
        return float(ui_value)
    return ui_value


@football_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    """Strategy tunables editor (Phase 1 — read/write, bankroll state
    untouched). On POST: parse, validate, save valid fields; on GET:
    render the form populated with current values + defaults shown."""
    sport = 'football'

    if request.method == 'POST':
        updates = {}
        errors = []
        for spec in _TUNABLE_SPEC:
            key = spec['key']
            kind = spec['kind']
            if kind == 'bool':
                # Unchecked checkboxes aren't in the form data — explicit False.
                updates[key] = (request.form.get(key) == 'on')
                continue
            raw = request.form.get(key, '').strip()
            if raw == '':
                continue  # left blank → no update, keep existing
            try:
                ui_val = float(raw)
            except ValueError:
                errors.append(f"{spec['label']} ({spec['lane']}): '{raw}' is not a number.")
                continue
            # Range check (UI units).
            if 'min' in spec and ui_val < spec['min']:
                errors.append(f"{spec['label']} ({spec['lane']}): {ui_val} < {spec['min']} (allowed min).")
                continue
            if 'max' in spec and ui_val > spec['max']:
                errors.append(f"{spec['label']} ({spec['lane']}): {ui_val} > {spec['max']} (allowed max).")
                continue
            updates[key] = _stored_value(kind, ui_val)

        # Cross-field validation: ev_factor_min < ev_factor_max.
        new_min = updates.get('model_ev_factor_min',
                              get_sport_config(sport).get('model_ev_factor_min'))
        new_max = updates.get('model_ev_factor_max',
                              get_sport_config(sport).get('model_ev_factor_max'))
        if new_min is not None and new_max is not None and new_min >= new_max:
            errors.append('Model EV factor min must be strictly less than max.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('football.settings'))

        set_tunables(sport, updates)
        flash(f'Saved {len(updates)} tunable(s).', 'success')
        return redirect(url_for('football.settings'))

    # GET — render form.
    cfg = get_sport_config(sport)
    fields = []
    for spec in _TUNABLE_SPEC:
        key = spec['key']
        kind = spec['kind']
        current_stored = cfg.get(key, DEFAULT_SPORT_CONFIG[key])
        default_stored = DEFAULT_SPORT_CONFIG[key]
        fields.append({
            **spec,
            'current_ui': _ui_value(kind, current_stored),
            'default_ui': _ui_value(kind, default_stored),
            'is_default': (current_stored == default_stored),
        })

    # Group fields by lane for the template.
    lanes = {'value': [], 'conviction': [], 'model': [], 'global': []}
    for f in fields:
        lanes[f['lane']].append(f)

    return render_template('settings.html', lanes=lanes)


@football_bp.route('/docs/')
@football_bp.route('/docs/<name>')
def docs(name=None):
    """Render one of the user-facing Markdown docs from `docs/` as HTML.

    Source files stay in Markdown; conversion happens on demand. Only docs
    in the `_DOCS` whitelist are reachable — random doc files in the repo
    aren't auto-exposed.
    """
    if name is None:
        # Index → redirect to the first doc.
        first_name = next(iter(_DOCS))
        return redirect(url_for('football.docs', name=first_name))

    entry = _DOCS.get(name)
    if entry is None:
        flash(f'Unknown doc: {name!r}.', 'warning')
        return redirect(url_for('football.index'))

    if not os.path.exists(entry['path']):
        flash(f'Doc source missing on disk: {entry["path"]}.', 'danger')
        return redirect(url_for('football.index'))

    with open(entry['path'], 'r', encoding='utf-8') as f:
        src = f.read()

    # `tables` enables GitHub-style pipe tables (we use these heavily).
    # `fenced_code` for ``` blocks. `toc` injects a table of contents.
    html = _md.markdown(
        src,
        extensions=['extra', 'tables', 'fenced_code', 'toc', 'sane_lists'],
        output_format='html5',
    )

    # Side-nav: list of (name, title, icon, is_current).
    side_nav = [
        {'name': k, 'title': v['title'], 'icon': v['icon'], 'current': (k == name)}
        for k, v in _DOCS.items()
    ]

    return render_template('docs.html',
                           html_content=html,
                           current=entry,
                           current_name=name,
                           side_nav=side_nav)

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


# --- Phase 7: manual cashout endpoint -------------------------------------
# Lets the user commit a cashout on an OPEN bet via the dashboard's
# Cash Out button. Delegates to `g.backend.execute_cashout()` so the
# call site is mode-agnostic — Phase 9 will register an identical
# endpoint under `/football/live/cashout/<bet_id>` against the live
# backend. See docs/LIVE_BETTING_TRANSITION.md for the full design.

def _find_bet_and_live_match(bet_id: str):
    """Locate a bet in any output/bets_*.json by its bet_id, plus the
    matching live_data entry. Returns (bet_dict, live_match_dict) or
    (None, None) if either can't be found."""
    # The bet_id starts with `<YYYY-MM-DD>:` — fast-path the right slip.
    date_prefix = bet_id.split(':', 1)[0] if ':' in bet_id else ''
    slip_candidates = []
    if date_prefix and len(date_prefix) == 10:
        slip_candidates.append(os.path.join(OUTPUT_DIR, f'bets_{date_prefix}.json'))
    # Fallback: any slip in output/ (NOT history — cashed-out bets can't
    # exist there because OPEN-only is the soft-delete invariant).
    slip_candidates.extend(sorted(glob.glob(os.path.join(OUTPUT_DIR, 'bets_*.json'))))

    target_bet = None
    for slip_path in slip_candidates:
        if not os.path.exists(slip_path):
            continue
        try:
            with open(slip_path) as f:
                slip = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for b in slip.get('bets', []):
            if b.get('bet_id') == bet_id:
                target_bet = b
                # Ensure the bet record carries a `date` field for the
                # backend (older records may not have one explicitly).
                if 'date' not in target_bet:
                    target_bet['date'] = slip.get('date') or date_prefix
                break
        if target_bet is not None:
            break
    if target_bet is None:
        return (None, None)

    # Find the matching live match (by 'match' string equality).
    # live_data.json is a top-level list (run_live_analysis writes
    # `json.dump(final_results, ...)` where final_results is a list).
    # Be defensive in case the shape ever changes — accept either a
    # list or a dict with a 'matches' key.
    live_path = os.path.join(OUTPUT_DIR, 'live_data.json')
    if not os.path.exists(live_path):
        return (target_bet, None)
    try:
        with open(live_path) as f:
            live_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return (target_bet, None)

    if isinstance(live_data, list):
        matches = live_data
    elif isinstance(live_data, dict):
        matches = live_data.get('matches') or []
    else:
        matches = []

    match_str = (target_bet.get('match') or '').strip()
    live_match = None
    for m in matches:
        if not isinstance(m, dict):
            continue
        if (m.get('match') or '').strip() == match_str:
            live_match = m
            break
    return (target_bet, live_match)


@football_bp.route('/cashout/<bet_id>', methods=['POST'])
def cashout(bet_id):
    """Cash out the bet identified by `bet_id`. Sets the Phase 3 schema
    fields (status='CASHED_OUT', cashout_amount, cashout_profit,
    cashout_timestamp), credits the lane bankroll with the cashout
    amount, persists the slip."""
    # Safety: bet_id is opaque text, but URL-pathing might be exploited.
    if '/' in bet_id or '..' in bet_id:
        flash('Invalid bet_id.', 'danger')
        return redirect(request.referrer or url_for('football.index'))

    bet, live_match = _find_bet_and_live_match(bet_id)
    if bet is None:
        flash(f'Bet not found: {bet_id}.', 'warning')
        return redirect(request.referrer or url_for('football.index'))
    # NOTE: do NOT gate on the representative bet's own status. The
    # cashout cascades across all lanes' OPEN bets with this bet_id —
    # the representative might already be CASHED_OUT but sibling lanes
    # could still be OPEN. Let execute_cashout decide.
    if live_match is None:
        flash(f'No live data for this match — cashout requires the match '
              f'to be in-play with current adjusted probabilities.', 'warning')
        return redirect(request.referrer or url_for('football.index'))

    # Snapshot the slip BEFORE the cashout so we can report exactly
    # what was cashed out (which lanes, totals).
    pre_open = []
    try:
        slip_date = bet_id.split(':', 1)[0] if ':' in bet_id else ''
        slip_path = os.path.join(OUTPUT_DIR, f'bets_{slip_date}.json')
        if os.path.exists(slip_path):
            with open(slip_path) as f:
                slip_snapshot = json.load(f)
            for b in slip_snapshot.get('bets', []):
                if b.get('bet_id') == bet_id and b.get('status') == 'OPEN':
                    pre_open.append({
                        'lane': b.get('lane', 'value'),
                        'stake': float(b.get('stake_units', 0) or 0),
                    })
    except Exception:
        pass

    ok = g.backend.execute_cashout(bet, live_match)
    if not ok:
        flash('No OPEN bets found to cash out (sibling lanes may have '
              'been cashed out already).', 'info')
        return redirect(request.referrer or url_for('football.index'))

    # Re-read slip to compute the actual cashed totals from the
    # post-state — execute_cashout writes the per-bet cashout_amount
    # and we want to report the sum.
    total_amount = 0.0
    total_profit = 0.0
    lanes_credited = set()
    try:
        with open(slip_path) as f:
            slip_after = json.load(f)
        for b in slip_after.get('bets', []):
            if b.get('bet_id') == bet_id and b.get('status') == 'CASHED_OUT':
                total_amount += float(b.get('cashout_amount', 0) or 0)
                total_profit += float(b.get('cashout_profit', 0) or 0)
                lanes_credited.add(b.get('lane', 'value'))
    except Exception:
        pass

    sign = '+' if total_profit >= 0 else ''
    lanes_label = ', '.join(sorted(lanes_credited)) if lanes_credited else 'unknown'
    flash(f'Cashed out €{total_amount:.2f} across {len(lanes_credited)} lane(s) '
          f'({lanes_label}); net P/L {sign}{total_profit:.2f}.',
          'success')
    return redirect(request.referrer or url_for('football.index'))


@football_bp.route('/void_bet/<bet_id>', methods=['POST'])
def void_bet(bet_id):
    """Mark an OPEN bet VOID — for matches that won't settle (postponed,
    cancelled, abandoned). Stake refunded to the lane. Uses the same
    backend abstraction as cashout, so the Phase 9 PamestoiximaBackend
    will get the same operation when it ships.
    """
    if '/' in bet_id or '..' in bet_id:
        flash('Invalid bet_id.', 'danger')
        return redirect(request.referrer or url_for('football.index'))

    bet, _ = _find_bet_and_live_match(bet_id)
    if bet is None:
        flash(f'Bet not found: {bet_id}.', 'warning')
        return redirect(request.referrer or url_for('football.index'))
    # Cascade across lanes — let void_bet check status itself.

    ok = g.backend.void_bet(bet)
    if not ok:
        flash('No OPEN bets found to void (sibling lanes may already '
              'be settled).', 'info')
        return redirect(request.referrer or url_for('football.index'))

    # Report what got refunded by re-reading the slip.
    total_refund = 0.0
    lanes_refunded = set()
    try:
        slip_date = bet_id.split(':', 1)[0] if ':' in bet_id else ''
        slip_path = os.path.join(OUTPUT_DIR, f'bets_{slip_date}.json')
        if os.path.exists(slip_path):
            with open(slip_path) as f:
                slip_after = json.load(f)
            for b in slip_after.get('bets', []):
                if b.get('bet_id') == bet_id and b.get('status') == 'VOID':
                    total_refund += float(b.get('stake_units', 0) or 0)
                    lanes_refunded.add(b.get('lane', 'value'))
    except Exception:
        pass

    lanes_label = ', '.join(sorted(lanes_refunded)) if lanes_refunded else 'unknown'
    flash(f'Voided across {len(lanes_refunded)} lane(s) ({lanes_label}); '
          f'€{total_refund:.2f} total refunded.', 'success')
    return redirect(request.referrer or url_for('football.index'))


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


# Bulk soft-delete: archive every file of the given kind. Each kind maps
# to one or more glob patterns. `predictions` covers both the CSV and the
# matching `report_<date>.txt` summary (generated together by every
# prediction run).
_ARCHIVE_ALL_PATTERNS = {
    'predictions':   ('predictions_*.csv', 'report_*.txt'),
    'verifications': ('verification_*.csv',),
    'scraped':       ('matches_*.json',),
}


@football_bp.route('/archive_all/<kind>', methods=['POST'])
def archive_all(kind):
    """Archive every file of `kind` (predictions / verifications / scraped)
    to output/history/. Per-file failures are collected and reported but
    don't abort the batch."""
    patterns = _ARCHIVE_ALL_PATTERNS.get(kind)
    if not patterns:
        flash(f'Unknown archive kind: {kind!r}.', 'danger')
        return redirect(url_for('football.index'))

    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(OUTPUT_DIR, pattern)))
    files = sorted(set(files))

    if not files:
        flash(f'No {kind} files to archive.', 'info')
        return redirect(request.referrer or url_for('football.index'))

    archived = 0
    errors = []
    for f in files:
        try:
            _archive_file(f, os.path.basename(f))
            archived += 1
        except OSError as e:
            errors.append(f"{os.path.basename(f)}: {e}")

    if archived:
        flash(f'Archived {archived} {kind} file(s) to history/.', 'success')
    if errors:
        # Cap displayed errors so a wholesale failure doesn't flood the UI.
        shown = '; '.join(errors[:3])
        suffix = '' if len(errors) <= 3 else f' (+{len(errors) - 3} more)'
        flash(f'Errors: {shown}{suffix}', 'warning')

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

    # Enrich with any OPEN bets on these matches (same data shape as the
    # dashboard's live rows — both pages now share the open-bets fragment).
    _attach_open_bets(matches_data)

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
        # ml_project imports need both repo root and ml_project/ on PYTHONPATH
        # (same convention as bin/run_predictions.sh).
        env = os.environ.copy()
        existing_pp = env.get('PYTHONPATH', '')
        ml_paths = [PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'ml_project')]
        env['PYTHONPATH'] = os.pathsep.join([p for p in ml_paths + [existing_pp] if p])
        proc = subprocess.Popen(
            ['venv/bin/python', script_path],
            cwd=PROJECT_ROOT, stdout=log_file, stderr=subprocess.STDOUT, env=env
        )
        
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
        # Also stamp each bet with a canonical bet_id + mode='virtual' so the
        # Phase 7 cashout endpoint can find it later. (Per the BettingBackend
        # contract — docs/LIVE_BETTING_TRANSITION.md "Bet ID format".)
        stake_by_lane = {lane: 0.0 for lane in LANES}
        for b in bets:
            lane = b.get('lane', 'value')
            if lane not in LANES:
                lane = 'value'
                b['lane'] = lane
            stake_by_lane[lane] += float(b.get('stake_units', 0))
            # Stamp the canonical bet_id + mode. Won't overwrite an
            # explicitly-set bet_id (e.g. on tests or future Phase 9 paths).
            if not b.get('bet_id'):
                b['bet_id'] = make_bet_id(
                    date_str,
                    b.get('home') or (b.get('match', '').split(' vs ')[0]
                                      if ' vs ' in b.get('match', '') else ''),
                    b.get('away') or (b.get('match', '').split(' vs ')[1]
                                      if ' vs ' in b.get('match', '') else ''),
                    b.get('type', '1X2'),
                    b.get('selection', ''),
                )
            b.setdefault('mode', 'virtual')

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
                'cashed_out': 0,
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
            # CASHED_OUT is checked first so it doesn't fall through to the
            # VOID else-branch. Cashout amount + pnl are stored at cashout
            # time (Phase 7 will populate them); we trust those over recomputing.
            if status == 'CASHED_OUT' or result == 'CASHED_OUT':
                s['cashed_out'] += 1
                cashout_amount = float(bet.get('cashout_amount', stake))
                s['returned'] += cashout_amount
                s['pnl'] += float(bet.get('pnl', cashout_amount - stake))
            elif result == 'WON' or status == 'WON':
                s['won'] += 1
                s['returned'] += stake + float(bet.get('pnl', 0))
                s['pnl'] += float(bet.get('pnl', 0))
            elif result == 'LOST' or status == 'LOST':
                s['lost'] += 1
                s['pnl'] += float(bet.get('pnl', -stake))
            else:  # VOID (or unrecognised terminal status)
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
    # Per-lane defaults for the override form (so the placeholder shows
    # the actual saved value, not the generic word "default").
    cfg = get_sport_config('football')
    lane_br = lane_bankrolls('football')
    lane_defaults = {
        'value':      {'bankroll': lane_br['value'],
                       'cap_pct':  cfg['value_max_daily_exposure_pct'] * 100},
        'conviction': {'bankroll': lane_br['conviction'],
                       'cap_pct':  cfg['conviction_max_daily_exposure_pct'] * 100},
        'model':      {'bankroll': lane_br['model'],
                       'cap_pct':  cfg['model_max_daily_exposure_pct'] * 100},
    }
    return render_template('betting.html',
                           history=summary['history'],
                           lane_stats=summary['lane_stats'],
                           lane_defaults=lane_defaults,
                           sport_label='Football')

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
            # Note: bin/run_verification.sh already invokes
            # ml_project/resolve_daily_bets.py at the end of its flow,
            # which is now the canonical settlement path (per-lane
            # bankroll updates via sports_config, idempotent across
            # re-runs, doesn't VOID matches that haven't finished).
            # No extra settle call needed here — this was previously a
            # buggy duplicate using a 'verify_' filename that never
            # matched the script's 'verification_' output.
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
        ev_cap_value             = config['ev_cap_value']
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
            """Option B: EV-gated, stake = bankroll * min(EV, cap) * conf * multiplier, capped & floored.

            The min(ev, ev_cap_value) clamp prevents a single high-EV pick (often
            from a low-data league) from dominating the slip. Real fix is
            per-league probability recalibration upstream — see NEXT_STEPS.md.
            """
            ev = _to_float(row.get(ev_col, 0))
            conf = _to_float(row.get(conf_col, 0))
            if ev <= 0 or conf < min_confidence:
                return None
            ev_for_sizing = min(ev, ev_cap_value)
            raw_stake = value_br * ev_for_sizing * conf * stake_multiplier
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
