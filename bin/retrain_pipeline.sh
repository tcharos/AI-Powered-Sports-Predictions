#!/bin/bash

# Change directory to project root
cd "$(dirname "$0")/.." || exit

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

export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/ml_project

# 1. Update Current Season Results
echo ""
echo "[*] Step 1/5: Updating Current Season Results..."
python3 scripts/update_football_data.py
if [ $? -ne 0 ]; then
    echo "[-] Error updating football data. Continuing..."
fi

# 2. Update Standings & Form
echo ""
echo "[*] Step 2/5: Updating Standings & Form..."
chmod +x bin/update_leagues_data.sh
bin/update_leagues_data.sh
if [ $? -ne 0 ]; then
    echo "[-] Error updating standings/form. Continuing..."
fi

# 3. Retrain Model
echo ""
echo "[*] Step 3/5: Retraining Model..."
python3 ml_project/train_model.py
if [ $? -eq 0 ]; then
    echo "[+] Model Retrained Successfully."
else
    echo "[-] Model Training Failed!"
    exit 1
fi

# 4. Refit per-league Platt calibrators (Phase C5).
# Writes data_sets/league_calibration.json. ~6 min. Non-fatal if it
# fails — production keeps the previous calibration file.
echo ""
echo "[*] Step 4/5: Fitting per-league calibrators..."
python3 scripts/run_fit_calibration.py
if [ $? -eq 0 ]; then
    echo "[+] Calibrators fitted."
else
    echo "[-] Calibration fit failed. Previous calibration file remains in place."
fi

# 5. Validate calibrators on chronological holdout + auto-filter failing
# entries from data_sets/league_calibration.json. ~6 min.
echo ""
echo "[*] Step 5/5: Validating calibrators on holdout..."
python3 scripts/run_validate_calibration.py
if [ $? -eq 0 ]; then
    echo "[+] Calibrators validated and filtered if needed."
else
    echo "[-] Calibration validation failed. Unfiltered fits remain in place."
fi

echo ""
echo "========================================"
echo "           Pipeline Finished            "
echo "========================================"
