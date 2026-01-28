import subprocess
import time
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# Assumes this script is in project root or scripts/ ? 
# Let's put it in project root for simplicity or scripts/
# If in root:
CMD = ["venv/bin/python", "scripts/run_live_analysis.py"]

def main():
    print("Starting Live Analysis Loop (Interval: 600s)...")
    while True:
        print(f"[{time.ctime()}] Running analysis...")
        try:
            subprocess.run(CMD, cwd=PROJECT_ROOT, check=False)
        except Exception as e:
            print(f"Error executing analysis: {e}")
            
        print(f"[{time.ctime()}] Sleeping for 10 minutes...")
        time.sleep(600)

if __name__ == "__main__":
    main()
