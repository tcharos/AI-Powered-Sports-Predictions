#!/bin/bash
# Wrapper to run the data setup script

echo "Activate VENV?"
source venv/bin/activate

if [ -z "$1" ]; then
    echo "Usage: ./setup_data.sh [SeasonCode]"
    echo "Example: ./setup_data.sh 2425 (for Season 2024/2025)"
    echo "Defaulting to interactive mode..."
    python3 scripts/setup_historical_data.py
else
    python3 scripts/setup_historical_data.py "$1"
fi
