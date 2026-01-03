#!/bin/bash

# Configuration
VENV_PATH="venv/bin/activate"
DATE=$(date -v+1d +%Y-%m-%d)
OUTPUT_JSON="output/matches_$DATE.json"

echo "========================================"
echo "    Flashscore ML Prediction Pipeline   "
echo "========================================"
echo "Date: $DATE"
echo "Pipeline Started: $(date "+%Y-%m-%d %H:%M:%S")"

# 1. Activate Virtual Environment
# 1. Activate Virtual Environment
if [ -f "$VENV_PATH" ]; then
    source $VENV_PATH
    echo "[+] Virtual Environment Activated"
else
    echo "[-] Error: Virtual Environment not found at $VENV_PATH"
    exit 1
fi

# Create logs directory
mkdir -p logs

# Redirect all output to log file (and stdout) - Overwrite mode
exec > >(tee logs/pipeline_output.log) 2>&1

# Check for --force flag
FORCE_SCRAPE=false
for arg in "$@"; do
    if [ "$arg" == "--force" ] || [ "$arg" == "-f" ]; then
        FORCE_SCRAPE=true
    fi
done

# 2. Run Scraper or Skip
if [ -f "$OUTPUT_JSON" ] && [ "$FORCE_SCRAPE" == "false" ]; then
    echo "[*] Output file $OUTPUT_JSON already exists. Skipping Scraper."
    echo "[*] Use --force or -f to overwrite."
    # Log the skip?
else
    echo "[*] Starting Scraper..."
    start_ts=$(date +%s)
    start_date=$(date "+%Y-%m-%d %H:%M:%S")
    echo "[$start_date] Status: Started" >> logs/scraper_status.log

    scrapy crawl flashscore -O $OUTPUT_JSON -L WARNING -a filter_leagues=true

    end_ts=$(date +%s)
    end_date=$(date "+%Y-%m-%d %H:%M:%S")
    duration=$((end_ts - start_ts))

    if [ $? -eq 0 ]; then
        echo "[+] Scraper Finished. Data saved to $OUTPUT_JSON"
        echo "[$end_date] Status: Success | Start: $start_date | End: $end_date | Duration: ${duration}s" >> logs/scraper_status.log
    else
        echo "[-] Scraper Failed."
        echo "[$end_date] Status: Failed | Start: $start_date | End: $end_date | Duration: ${duration}s" >> logs/scraper_status.log
        exit 1
    fi
fi

# 3. Run Prediction
echo ""
echo "[*] Running ML Prediction Engine..."

# Export PYTHONPATH to include project root and ml_project so imports work
export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/ml_project

# Check if JSON is valid (rudimentary check) or just run script
if [ ! -s "$OUTPUT_JSON" ]; then
    echo "[-] Error: Output JSON is empty. Scraper likely failed."
    exit 1
fi

python3 -c "from ml_project.predict_matches import MatchPredictor; predictor = MatchPredictor(scraper_output='$OUTPUT_JSON'); predictor.predict()"

if [ $? -eq 0 ]; then
    echo "[+] Prediction Complete."
else
    echo "[-] Prediction Failed!"
    exit 1
fi

echo ""
echo "========================================"
echo "           Pipeline Finished            "
echo "Pipeline Ended: $(date "+%Y-%m-%d %H:%M:%S")"
echo "========================================"
