#!/bin/bash
# Full Euroleague/EuroCup retrain pipeline.
#
#   build_corpus (raw euroleague-api CSVs → team_game_stats.csv, merge-safe)
#     → euroleague_feature_engineering (→ training_data.csv + euroleague_elo.json)
#     → train_euroleague_models (combined winner + total, is_eurocup feature)
#     → euroleague_calibration (per-competition Platt fit, non-fatal)
#
# Mirrors football/NBA non-fatal chaining: a calibration failure leaves the
# prior euroleague_calibration.json in place rather than blocking the retrain.
#
# Usage: ./bin/retrain_euroleague_pipeline.sh

set -u
cd "$(dirname "$0")/.." || exit 1

VENV_PATH="venv/bin/activate"

echo "========================================"
echo "    Euroleague Retrain Pipeline         "
echo "========================================"
date

if [ ! -f "$VENV_PATH" ]; then
    echo "[-] venv not found at $VENV_PATH"; exit 1
fi
source "$VENV_PATH"
export PYTHONPATH="${PYTHONPATH:-}:$(pwd):$(pwd)/ml_project:$(pwd)/ml_project/euroleague"

# 1. Build the canonical corpus from the raw season CSVs. Merge-safe (preserves
#    daily-appended rows whose gameId isn't in the raw set).
echo ""
echo "[1/4] Building corpus ..."
if ! python3 ml_project/euroleague/build_corpus.py; then
    echo "[-] Corpus build failed."; exit 1
fi

# 2. Feature matrix + ELO cache.
echo ""
echo "[2/4] Building features ..."
if ! python3 ml_project/euroleague/euroleague_feature_engineering.py; then
    echo "[-] Feature build failed."; exit 1
fi

# 3. Train combined winner + total models.
echo ""
echo "[3/4] Training winner + total ..."
if ! python3 ml_project/euroleague/train_euroleague_models.py; then
    echo "[-] Training failed."; exit 1
fi

# 4. Per-competition Platt calibration (non-fatal).
echo ""
echo "[4/4] Fitting calibration ..."
if python3 ml_project/euroleague/euroleague_calibration.py; then
    echo "[+] Calibration fit."
else
    echo "[!] Calibration step failed (non-fatal); predictor will use the prior calibration json (or raw if none)."
fi

echo ""
echo "========================================"
echo "    Retrain Pipeline Finished           "
echo "========================================"
