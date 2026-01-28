#!/bin/bash

# Configuration
VENV_PATH="venv/bin/activate"

# Date for verification (default: yesterday)
# Usage: ./run_verification.sh [YYYY-MM-DD]
if [ -z "$1" ]; then
    TARGET_DATE=$(date -v-1d +%Y-%m-%d) # MacOS version of 'yesterday'
    # Linux would be: date -d "yesterday" +%Y-%m-%d
else
    TARGET_DATE=$1
fi

RESULTS_JSON="output/matches_$TARGET_DATE.json"
PREDICTIONS_CSV="output/predictions_$TARGET_DATE.csv"

echo "========================================"
echo "    Flashscore Prediction Verification  "
echo "========================================"
echo "Verifying Date: $TARGET_DATE"

# 1. Activate Virtual Environment
if [ -f "$VENV_PATH" ]; then
    source $VENV_PATH
else
    echo "[-] Error: Virtual Environment not found at $VENV_PATH"
    exit 1
fi

# 2. Check if Predictions exist
if [ ! -f "$PREDICTIONS_CSV" ]; then
    echo "[-] Error: Predictions file not found: $PREDICTIONS_CSV"
    echo "    Cannot verify predictions that don't exist."
    exit 1
fi

# 3. Calculate Day Offset for Scraper
# Current Date - Target Date
# We want day_diff which is Target - Current.
# e.g. Yesterday - Today = -1.

CURRENT_DATE_SEC=$(date +%s)
# Assume MacOS date format for simplicity as environment is known
TARGET_DATE_SEC=$(date -j -f "%Y-%m-%d" "$TARGET_DATE" +%s)

DIFF_SEC=$((TARGET_DATE_SEC - CURRENT_DATE_SEC))
# Rounding
DAY_DIFF=$(( (DIFF_SEC - 43200) / 86400 )) 
# Note: For verification, we usually look back.
# If target is yesterday, diff is negative approx -86400. / 86400 = -1.

echo "[*] Target is $DAY_DIFF days from today. Running Scraper..."

# 4. Run Scraper to get Results

# New ID-based strategy
LIVE_IDS=$(python3 -c "import pandas as pd; df=pd.read_csv('$PREDICTIONS_CSV'); print(','.join(df['match_id'].dropna().astype(str).tolist()))" 2>/dev/null)

if [ ! -z "$LIVE_IDS" ]; then
    echo "[*] Found Match IDs in predictions. Using Direct ID Scraping Mode."
    # Reusing 'live_ids' argument logic which triggers batch scraping
    # We add mode=verification to ensure we parse scores properly (though batch logic usually does)
    scrapy crawl flashscore -a live_ids="$LIVE_IDS" -a mode=verification -O $RESULTS_JSON -L WARNING
else
    echo "[*] No Match IDs found. Using Date-based Scraper (Fallback)..."
    # Pass day_diff instead of days_back
    scrapy crawl flashscore -a day_diff=$DAY_DIFF -a mode=verification -O $RESULTS_JSON -L WARNING
fi

if [ $? -ne 0 ]; then
    echo "[-] Scraper Failed!"
    exit 1
fi

echo "[+] Results saved to $RESULTS_JSON"

REPORT_FILE="output/report_$TARGET_DATE.txt"

# 5. Run Evaluation
echo ""
echo "[*] Comparing Predictions vs Results..."
VERIFICATION_CSV="output/verification_$TARGET_DATE.csv"
python3 ml_project/evaluate_predictions.py --preds $PREDICTIONS_CSV --results $RESULTS_JSON --output $VERIFICATION_CSV | tee $REPORT_FILE

echo "[+] Verification Report saved to $REPORT_FILE"

echo "[+] Verification Report saved to $REPORT_FILE"

# 6. Resolve Bets (All Open Slips)
echo ""
echo "[*] Resolving Open Bets across all slips..."
python3 ml_project/resolve_daily_bets.py --bets_dir output --results $RESULTS_JSON --verification_csv $VERIFICATION_CSV

echo ""
echo "========================================"
echo "           Verification Finished        "
echo "========================================"
