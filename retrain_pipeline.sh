#!/bin/bash

# Configuration
VENV_PATH="venv/bin/activate"

echo "========================================"
echo "      Flashscore Retrain Pipeline       "
echo "========================================"
echo "Date: $(date)"

# 1. Activate Virtual Environment
if [ -f "$VENV_PATH" ]; then
    source $VENV_PATH
    echo "[+] Virtual Environment Activated"
else
    echo "[-] Error: Virtual Environment not found at $VENV_PATH"
    exit 1
fi

export PYTHONPATH=$PYTHONPATH:$(pwd)

# 2. Update Current Season Results
echo ""
echo "[*] Step 1/3: Updating Current Season Results..."
python3 scripts/update_football_data.py
if [ $? -ne 0 ]; then
    echo "[-] Error updating football data. Continuing..."
fi

# 3. Update Standings & Form
echo ""
echo "[*] Step 2/3: Updating Standings & Form..."
chmod +x update_leagues_data.sh
./update_leagues_data.sh
if [ $? -ne 0 ]; then
    echo "[-] Error updating standings/form. Continuing..."
fi

# 4. Retrain Model
echo ""
echo "[*] Step 3/3: Retraining Model..."
python3 ml_project/train_model.py
if [ $? -eq 0 ]; then
    echo "[+] Model Retrained Successfully."
else
    echo "[-] Model Training Failed!"
    exit 1
fi

echo ""
echo "========================================"
echo "           Pipeline Finished            "
echo "========================================"
