#!/bin/bash

# Change directory to project root (one level up from bin/)
cd "$(dirname "$0")/.." || exit

# Configuration
VENV_PATH="venv/bin/activate"
# Check for --force flag and Date Arg
FORCE_SCRAPE=false
TARGET_DATE=""

for arg in "$@"; do
    if [ "$arg" == "--force" ] || [ "$arg" == "-f" ]; then
        FORCE_SCRAPE=true
    elif [[ "$arg" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        TARGET_DATE="$arg"
    fi
done

# Date Logic
if [ -z "$TARGET_DATE" ]; then
    # Default: Tomorrow
    if date -v+1d >/dev/null 2>&1; then
        # MacOS
        DATE=$(date -v+1d +%Y-%m-%d)
    else
        # Linux
        DATE=$(date -d "tomorrow" +%Y-%m-%d)
    fi
else
    DATE="$TARGET_DATE"
    echo "[*] Using Custom Date: $DATE"
fi

OUTPUT_JSON="output/matches_$DATE.json"

echo "========================================"
echo "    Flashscore ML Prediction Pipeline   "
echo "========================================"
echo "Date: $DATE"
echo "Pipeline Started: $(date "+%Y-%m-%d %H:%M:%S")"

# Calculate Day Difference (for Spiderman)
# CURRENT - TARGET ?? No, we want TARGET - CURRENT.
# If Target is tomorrow, Diff = +1.
CURRENT_SEC=$(date +%s)
# Need portable date to sec? MacOS `date -j -f ...` vs Linux `date -d ...`
if date -j -f "%Y-%m-%d" "$DATE" +%s >/dev/null 2>&1; then
    # MacOS
    TARGET_SEC=$(date -j -f "%Y-%m-%d" "$DATE" +%s)
else
    # Linux
    TARGET_SEC=$(date -d "$DATE" +%s)
fi

DIFF_SEC=$((TARGET_SEC - CURRENT_SEC))
# Rounding
DAY_DIFF=$(( (DIFF_SEC + 43200) / 86400 ))

echo "[*] Target Offset: $DAY_DIFF days from today."


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

# 2. Run Scraper or Skip
NEED_SCRAPE=false

# Check if we should scrape
if [ "$FORCE_SCRAPE" == "true" ]; then
    NEED_SCRAPE=true
elif [ ! -s "$OUTPUT_JSON" ]; then
    echo "[*] Output file missing or empty."
    NEED_SCRAPE=true
else
    # File exists and size > 0. Check for JSON Corruption.
    if ! python3 -c "import json; json.load(open('$OUTPUT_JSON'))" > /dev/null 2>&1; then
        echo "[!] Output file exists but contains corrupt JSON. Forcing re-scrape."
        NEED_SCRAPE=true
    else
        echo "[*] valid Output file found. Skipping Scraper."
    fi
fi

if [ "$NEED_SCRAPE" == "true" ]; then
    echo "[*] Starting Scraper..."
    start_ts=$(date +%s)
    start_date=$(date "+%Y-%m-%d %H:%M:%S")
    echo "[$start_date] Status: Started" >> logs/scraper_status.log

    # Pass day_diff
    scrapy crawl flashscore -O $OUTPUT_JSON -L WARNING -a filter_leagues=true -a day_diff=$DAY_DIFF

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

# Export PYTHONPATH to include project root and ml_project so imports work
# (needed by both the availability step below and the predictor).
export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/ml_project

# 2b. Player availability → importance (D4 / N1 + N2). Non-fatal.
# N1 scrapes the Flashscore "Will not play" list per fixture (--covered-only:
# only SoFIFA-covered leagues, to skip wasted scrapes); N2 joins SoFIFA OVR to
# produce output/availability_importance_<date>.json. The predictor loads that
# and, by default, runs LOG-ONLY — it records the would-be injury shift for N4
# forward validation WITHOUT changing predictions (set
# use_availability_adjustment=true in betting_config.json to actually apply it).
echo ""
echo "[*] Extracting player availability (D4 / N1+N2)..."
if python3 scripts/d4_injuries/extract_availability.py "$DATE" --covered-only \
   && python3 ml_project/availability/sofifa_importance.py "output/availability_$DATE.json"; then
    echo "[+] Availability importance ready."
else
    echo "[!] Availability step failed (non-fatal); predictions proceed without injury data."
fi

# 3. Run Prediction
echo ""
echo "[*] Running ML Prediction Engine..."

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

# 4. National-Team Predictions (router): the club predictor skips international
# competitions (World Cup / Euro / Nations League); this appends their rows to
# the same predictions_<date>.csv using the eloratings model. Non-fatal — if it
# fails, club predictions remain intact.
echo ""
echo "[*] Running National-Team Prediction (eloratings model)..."
python3 scripts/national_teams/predict_nt_batch.py --matches "$OUTPUT_JSON" \
    && echo "[+] National-Team Prediction Complete." \
    || echo "[!] NT prediction step failed (non-fatal); club predictions intact."

echo ""
echo "========================================"
echo "           Pipeline Finished            "
echo "Pipeline Ended: $(date "+%Y-%m-%d %H:%M:%S")"
echo "========================================"
