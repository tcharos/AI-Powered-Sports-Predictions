#!/bin/bash
# Euroleague/EuroCup daily verification.
#
#   yesterday's finished games (euroleague-api, both E + U)
#     → append-results into team_game_stats.csv (idempotent dedup)
#
# v1 = data-side verification only: it keeps the corpus current so the next
# retrain / serve-time feature computation sees the latest games. A
# predictions-vs-results EVALUATOR (Brier/acc on settled games) and bet
# settlement are Phase 3 / a later evaluator — flagged in EUROLEAGUE_NEXT_STEPS.
#
# Usage: ./bin/run_euroleague_verification.sh [YYYY-MM-DD]   (default: yesterday)

set -u
cd "$(dirname "$0")/.." || exit 1

VENV_PATH="venv/bin/activate"

# Portable date (macOS date -v / Linux date -d).
if [ -n "${1:-}" ] && [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    TARGET_DATE="$1"
elif date -v-1d >/dev/null 2>&1; then
    TARGET_DATE=$(date -v-1d +%Y-%m-%d)
else
    TARGET_DATE=$(date -d "yesterday" +%Y-%m-%d)
fi

echo "========================================"
echo "    Euroleague Verification             "
echo "========================================"
echo "Target Date: $TARGET_DATE"
echo "Started: $(date "+%Y-%m-%d %H:%M:%S")"

if [ ! -f "$VENV_PATH" ]; then
    echo "[-] venv not found at $VENV_PATH"; exit 1
fi
source "$VENV_PATH"

mkdir -p logs output_euroleague
export PYTHONPATH="${PYTHONPATH:-}:$(pwd):$(pwd)/ml_project:$(pwd)/ml_project/euroleague"

# Append finished games to the corpus (idempotent — dedups on gameId,teamId).
echo ""
echo "[*] Appending finished results (euroleague-api, E + U) ..."
if python3 ml_project/euroleague/fetch_euroleague_daily.py append-results --date "$TARGET_DATE"; then
    echo "[+] Results appended."
else
    echo "[-] Result append failed."
    exit 1
fi

echo ""
echo "========================================"
echo "    Verification Finished               "
echo "========================================"
