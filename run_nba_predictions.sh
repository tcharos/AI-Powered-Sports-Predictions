#!/bin/bash

# Configuration
VENV_PATH="venv/bin/activate"
# Spider defaults to tomorrow, so we match that date for the filename
TARGET_DATE=$(date -v+1d +%Y-%m-%d)
OUTPUT_DIR="output_basketball"
OUTPUT_FILE="${OUTPUT_DIR}/nba_matches_${TARGET_DATE}.json"
FINAL_FILE="${OUTPUT_DIR}/nba_matches_${TARGET_DATE}_final.json"

echo "========================================"
echo "      NBA Prediction Pipeline           "
echo "========================================"
echo "Target Date: $TARGET_DATE"
echo "Pipeline Started: $(date "+%Y-%m-%d %H:%M:%S")"

# 1. Activate Virtual Environment
if [ -f "$VENV_PATH" ]; then
    source $VENV_PATH
else
    echo "[-] Error: Virtual Environment not found at $VENV_PATH"
    exit 1
fi

# 2. Run Scraper
echo ""
echo "[*] Starting NBA Scraper..."
# We use -O to overwrite the final file with the Scrapy ITEMS (dictionaries), 
# ignoring the side-effect ID-list file the spider creates.
scrapy crawl basketball -O "$FINAL_FILE" -L WARNING

if [ $? -eq 0 ]; then
    echo "[+] Scraper Finished. Data saved to $FINAL_FILE"
else
    echo "[-] Scraper Failed."
    exit 1
fi

# 3. (Skipped) Rename step removed as we output directly to FINAL_FILE

# 4. Run Prediction
echo ""
echo "[*] Running NBA Predictor..."
export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/ml_project
python3 ml_project/predict_nba.py

echo ""
echo "========================================"
echo "           Pipeline Finished            "
echo "========================================"
