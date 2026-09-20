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
    # An empty `[]` is 4 bytes on disk, so it passes both the -s and the
    # json.load checks above and would silently short-circuit the scraper into
    # reusing a failed run's output forever. Treat it as no cache at all.
    elif [ "$(python3 -c "import json; print(len(json.load(open('$OUTPUT_JSON'))))" 2>/dev/null)" == "0" ]; then
        echo "[!] Output file exists but holds 0 matches (failed prior scrape). Forcing re-scrape."
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

    # The spider drops a sidecar describing what the day page held (rows before
    # filtering, rows inside target_leagues, rows kept). Remove any stale one
    # first so a previous run's file can never be read as this run's result.
    SCRAPE_STATS="logs/last_scrape_stats.json"
    rm -f "$SCRAPE_STATS"

    # Pass day_diff
    scrapy crawl flashscore -O $OUTPUT_JSON -L WARNING -a filter_leagues=true -a day_diff=$DAY_DIFF
    # Capture immediately: any intervening command (even `date`) clobbers $?.
    SCRAPY_RC=$?

    end_ts=$(date +%s)
    end_date=$(date "+%Y-%m-%d %H:%M:%S")
    duration=$((end_ts - start_ts))

    # Scrapy exits 0 even when every request errored out (e.g. Playwright's
    # browser binary is missing after a version bump), leaving a well-formed
    # but EMPTY `[]` on disk. So the exit code alone is not enough — count the
    # scraped matches. But 0 matches has two very different causes, and the
    # sidecar is what tells them apart:
    #
    #   rows_on_page == 0        the day page never rendered -> BROKEN, exit 1
    #   in_target_leagues == 0   the day holds fixtures, none whitelisted
    #   kept == 0 (but > 0 above) whitelisted fixtures exist but all already
    #                            kicked off / finished (or are women's games)
    #
    # The last two are normal days with nothing to predict, not failures, and
    # they exit EXIT_NO_FIXTURES so the UI can say so instead of "Prediction
    # failed". A missing/mismatched sidecar falls back to treating 0 as broken.
    EXIT_NO_FIXTURES=3
    SCRAPED_COUNT=$(python3 -c "import json; print(len(json.load(open('$OUTPUT_JSON'))))" 2>/dev/null || echo "-1")

    # "rows in_target kept" for this day_diff, or "" when unusable.
    SCRAPE_FACTS=$(python3 - "$SCRAPE_STATS" "$DAY_DIFF" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
if int(d.get("day_diff", -999)) != int(sys.argv[2]) or not d.get("filtered"):
    sys.exit(1)          # sidecar belongs to some other crawl - do not trust it
print(d.get("rows_on_page", 0), d.get("in_target_leagues", 0), d.get("kept", 0))
PY
)

    if [ "$SCRAPY_RC" -eq 0 ] && [ "$SCRAPED_COUNT" -gt 0 ]; then
        echo "[+] Scraper Finished. $SCRAPED_COUNT matches saved to $OUTPUT_JSON"
        echo "[$end_date] Status: Success | Matches: $SCRAPED_COUNT | Start: $start_date | End: $end_date | Duration: ${duration}s" >> logs/scraper_status.log
    elif [ "$SCRAPY_RC" -eq 0 ] && [ "$SCRAPED_COUNT" -eq 0 ] && [ -n "$SCRAPE_FACTS" ] \
         && [ "$(echo "$SCRAPE_FACTS" | cut -d' ' -f1)" -gt 0 ]; then
        ROWS=$(echo "$SCRAPE_FACTS" | cut -d' ' -f1)
        IN_TARGET=$(echo "$SCRAPE_FACTS" | cut -d' ' -f2)
        echo "[=] No fixtures to predict for $DATE."
        if [ "$IN_TARGET" -eq 0 ]; then
            echo "    The day page loaded fine ($ROWS matches listed), but none are in"
            echo "    your target leagues (data_sets/target_leagues.json) — typically a"
            echo "    domestic-cup or international-break day."
        else
            echo "    The day page loaded fine ($ROWS matches listed, $IN_TARGET in your target"
            echo "    leagues), but every one has already kicked off or finished."
        fi
        echo "    The scraper is healthy; there is simply nothing to predict."
        echo "[$end_date] Status: No fixtures | Matches: 0 | On page: $ROWS | In target: $IN_TARGET | Start: $start_date | End: $end_date | Duration: ${duration}s" >> logs/scraper_status.log
        exit $EXIT_NO_FIXTURES
    else
        if [ "$SCRAPY_RC" -ne 0 ]; then
            echo "[-] Scraper Failed (scrapy exit code $SCRAPY_RC)."
        elif [ "$SCRAPED_COUNT" -lt 0 ]; then
            echo "[-] Scraper Failed: $OUTPUT_JSON is missing or not valid JSON."
        else
            echo "[-] Scraper Failed: 0 matches scraped and the day page was empty."
            if [ -z "$SCRAPE_FACTS" ]; then
                echo "    (no usable $SCRAPE_STATS — the crawl did not reach the day list)"
            fi
            echo "    Check logs/pipeline_output.log — a missing Playwright browser"
            echo "    (after a playwright upgrade) is the usual cause; fix with:"
            echo "        source venv/bin/activate && playwright install chromium"
        fi
        echo "[$end_date] Status: Failed | Matches: $SCRAPED_COUNT | Start: $start_date | End: $end_date | Duration: ${duration}s" >> logs/scraper_status.log
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
