#!/bin/bash

# Change directory to project root
cd "$(dirname "$0")/.." || exit

# Default to Yesterday
if [ -z "$1" ]; then
    TARGET_DATE=$(date -v-1d +%Y-%m-%d)
else
    TARGET_DATE=$1
fi

echo "========================================"
echo "      NBA Prediction Validation         "
echo "========================================"
echo "Target Date: $TARGET_DATE"

# Activate Venv
source venv/bin/activate

# 1. Fetch Results
echo ""
echo "[*] Fetching NBA Results from ESPN..."
python3 ml_project/fetch_nba_results.py --date $TARGET_DATE

if [ $? -ne 0 ]; then
    echo "[-] Failed to fetch results."
    exit 1
fi

# 2. Evaluate
echo ""
echo "[*] Evaluating Predictions..."
python3 ml_project/evaluate_nba_predictions.py --date $TARGET_DATE

echo ""
echo "========================================"
echo "           Validation Finished          "
echo "========================================"
