#!/bin/bash
# Full NBA retrain pipeline.
#
#   process_archive (TeamStatisticsExtended.csv → team_game_stats.csv)
#     → nba_feature_engineering (→ training_data.csv + nba_elo.json cache)
#     → train_nba_models (winner XGBClassifier + total XGBRegressor)
#     → nba_calibration (Platt fit, non-fatal)
#
# Mirrors football's retrain_pipeline.sh non-fatal chaining: calibration
# failure leaves the prior nba_calibration.json in place rather than blocking
# the retrain.
#
# Tune (tune_nba_models.py) is deferred — the current best_params_*.json were
# tuned on the old 12-feature set; retune via a separate invocation once the
# feature set has stabilized for a couple of cycles.

set -u
cd "$(dirname "$0")/.." || exit 1

VENV_PATH="venv/bin/activate"

echo "========================================"
echo "        NBA Retrain Pipeline            "
echo "========================================"
date

if [ ! -f "$VENV_PATH" ]; then
    echo "[-] venv not found at $VENV_PATH"; exit 1
fi
source "$VENV_PATH"
export PYTHONPATH="${PYTHONPATH:-}:$(pwd):$(pwd)/ml_project:$(pwd)/ml_project/nba"

# 1. Process the local archive into the canonical corpus. Merge-safe: if an
# existing corpus has daily-only appended rows (gameIds not in the archive),
# they're preserved on top of the archive rebuild.
echo ""
echo "[1/4] Processing archive ..."
if ! python3 ml_project/nba/process_archive.py; then
    echo "[-] Archive processing failed."; exit 1
fi

# 2. Build feature matrix + ELO cache.
echo ""
echo "[2/4] Building features ..."
if ! python3 ml_project/nba/nba_feature_engineering.py; then
    echo "[-] Feature build failed."; exit 1
fi

# 3. Train winner + total models.
echo ""
echo "[3/4] Training winner + total ..."
if ! python3 ml_project/nba/train_nba_models.py; then
    echo "[-] Training failed."; exit 1
fi

# 4. Fit Platt calibration (non-fatal — prior calibration json stays on disk).
echo ""
echo "[4/4] Fitting calibration ..."
if python3 ml_project/nba/nba_calibration.py; then
    echo "[+] Calibration fit."
else
    echo "[!] Calibration step failed (non-fatal); predictor will use the prior calibration json (or raw if none)."
fi

echo ""
echo "========================================"
echo "        Retrain Pipeline Finished       "
echo "========================================"
