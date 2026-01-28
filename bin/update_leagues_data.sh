#!/bin/bash

# Change directory to project root
cd "$(dirname "$0")/.." || exit
# Activate virtual environment
source venv/bin/activate

# Run the standings spider
# No -O because the pipeline handles the files.
# Run the standings spider (Silenced)
scrapy crawl standings -L WARNING

echo "Standings update complete."
