#!/bin/bash

# Change directory to project root
cd "$(dirname "$0")/.." || exit

echo "========================================"
echo "      NBA Full Retraining Pipeline      "
echo "========================================"
date

# Activate Venv
source venv/bin/activate

# 1. Update Standings & Form (UI/Leagues)
echo ""
echo "[1/4] Updating Standings & Form..."
python3 ml_project/fetch_nba_stats_tables.py
if [ $? -ne 0 ]; then
    echo "[-] Error updating standings."
    # Continue anyway? Yes, training data is separate
fi

# 2. Update Historical Data (Training Source)
echo ""
echo "[2/4] Updating Historical Match Data..."
python3 ml_project/fetch_nba_history_stats.py
if [ $? -ne 0 ]; then
    print "[-] Error updating history."
    exit 1
fi

# 3. Feature Engineering
echo ""
echo "[3/4] Generating Features..."
python3 ml_project/nba_feature_engineering.py
if [ $? -ne 0 ]; then
    echo "[-] Error generating features."
    exit 1
fi

# 4. Train Models
echo ""
echo "[4/4] Training Models..."
python3 ml_project/train_nba_models.py
if [ $? -ne 0 ]; then
    echo "[-] Error training models."
    exit 1
fi

echo ""
echo "========================================"
echo "          Pipeline Completed            "
echo "========================================"
