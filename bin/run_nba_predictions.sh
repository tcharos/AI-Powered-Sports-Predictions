#!/bin/bash
# NBA daily prediction pipeline (Phase 1+2 — odds/EV/betting wired in Phase 3).
#
#   tomorrow's fixtures (nba_api ScoreboardV3)
#     → predictor (corpus-derived features, calibrated P(home_win))
#     → output_basketball/predictions_nba_<date>.csv
#
# Usage: ./bin/run_nba_predictions.sh [YYYY-MM-DD]
#   (default: tomorrow)

set -u
cd "$(dirname "$0")/.." || exit 1

VENV_PATH="venv/bin/activate"

# Date handling: portable across macOS / Linux (same pattern as run_predictions.sh).
if [ -n "${1:-}" ] && [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    TARGET_DATE="$1"
elif date -v+1d >/dev/null 2>&1; then
    TARGET_DATE=$(date -v+1d +%Y-%m-%d)
else
    TARGET_DATE=$(date -d "tomorrow" +%Y-%m-%d)
fi

echo "========================================"
echo "       NBA Prediction Pipeline          "
echo "========================================"
echo "Target Date: $TARGET_DATE"
echo "Pipeline Started: $(date "+%Y-%m-%d %H:%M:%S")"

# 1. venv
if [ ! -f "$VENV_PATH" ]; then
    echo "[-] venv not found at $VENV_PATH"; exit 1
fi
source "$VENV_PATH"

mkdir -p logs output_basketball
export PYTHONPATH="${PYTHONPATH:-}:$(pwd):$(pwd)/ml_project:$(pwd)/ml_project/nba"

# 2. Fixtures via nba_api (writes data_sets/NBA/fixtures_<date>.json)
echo ""
echo "[*] Fetching fixtures (nba_api ScoreboardV3) ..."
if ! python3 ml_project/nba/fetch_nba_daily.py fixtures --date "$TARGET_DATE"; then
    echo "[-] Fixture fetch failed."
    exit 1
fi

# 3. Predict (corpus-derived features + Platt-calibrated P(home_win))
echo ""
echo "[*] Running predictor ..."
if python3 ml_project/nba/predict_nba.py --date "$TARGET_DATE"; then
    echo "[+] Prediction complete."
else
    echo "[-] Prediction failed."
    exit 1
fi

echo ""
echo "========================================"
echo "       NBA Pipeline Finished            "
echo "========================================"
