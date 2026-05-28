#!/bin/bash
# NBA daily verification: append fresh results to the corpus, then evaluate
# yesterday's predictions vs actuals.
#
#   yesterday's results (nba_api LeagueGameLog → idempotent corpus append)
#     → evaluate_nba_predictions.py (settles slips when Phase 3 betting wires in)
#
# Usage: ./bin/run_nba_verification.sh [YYYY-MM-DD]
#   (default: yesterday)

set -u
cd "$(dirname "$0")/.." || exit 1

VENV_PATH="venv/bin/activate"

if [ -n "${1:-}" ] && [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    TARGET_DATE="$1"
elif date -v-1d >/dev/null 2>&1; then
    TARGET_DATE=$(date -v-1d +%Y-%m-%d)
else
    TARGET_DATE=$(date -d "yesterday" +%Y-%m-%d)
fi

echo "========================================"
echo "      NBA Verification Pipeline         "
echo "========================================"
echo "Target Date: $TARGET_DATE"

if [ ! -f "$VENV_PATH" ]; then
    echo "[-] venv not found at $VENV_PATH"; exit 1
fi
source "$VENV_PATH"
export PYTHONPATH="${PYTHONPATH:-}:$(pwd):$(pwd)/ml_project:$(pwd)/ml_project/nba"

# 1. Append yesterday's results to the canonical corpus (idempotent — preserves
# archive's richer rows on dup gameIds).
echo ""
echo "[*] Appending fresh results to corpus ..."
if ! python3 ml_project/nba/fetch_nba_daily.py append-results --date "$TARGET_DATE"; then
    echo "[!] Result fetch failed (non-fatal) — evaluation will still try against existing data."
fi

# 2. Evaluate predictions vs actuals.
# Note: evaluate_nba_predictions.py was written against the OLD prediction CSV
# schema; it may need column-name updates for the new schema
# (Home Win Prob / Predicted Winner / Predicted Total + raw + Cal Source).
# Treated as non-fatal here; the corpus append above is the more important step.
echo ""
echo "[*] Evaluating predictions ..."
if python3 ml_project/nba/evaluate_nba_predictions.py --date "$TARGET_DATE" 2>&1; then
    echo "[+] Evaluation complete."
else
    echo "[!] Evaluator failed (likely schema mismatch with new CSV cols — see Phase 3 / follow-up)."
fi

echo ""
echo "========================================"
echo "      NBA Verification Finished         "
echo "========================================"
