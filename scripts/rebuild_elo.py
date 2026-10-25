"""Rebuild data_sets/elo_ratings.json from the current results corpus.

Only a retrain used to write this file, so between retrains every prediction
read ratings frozen at the last one. On 2026-10-25 they were 37 days old, and
the sides promoted into the 2026/27 corpus were missing entirely, so
predict_matches skipped their fixtures as unresolved. run_predictions.sh now
runs this after refreshing the results CSVs.

It goes through elo_engine.history_elo, the same code ModelTrainer uses, so
the ratings are exactly what a retrain on the same corpus would write. ~4 s.

Usage: python3 scripts/rebuild_elo.py   (from the repo root)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ml_project'))
from data_loader import DataLoader  # noqa: E402
from elo_engine import ELO_FILE, history_elo, save_ratings  # noqa: E402


def main():
    df = DataLoader('data_sets/MatchHistory').load_historical_data()
    _, tracker = history_elo(df)

    old = {}
    if os.path.exists(ELO_FILE):
        with open(ELO_FILE) as f:
            old = json.load(f)
    new_teams = sorted(set(tracker.ratings) - set(old))

    save_ratings(tracker.ratings)
    last = df.dropna(subset=['FTHG', 'FTAG'])['date'].max()
    print(f"[+] ELO rebuilt: {len(tracker.ratings)} teams, results through {last.date()}.")
    if new_teams:
        shown = ', '.join(new_teams[:12]) + (' ...' if len(new_teams) > 12 else '')
        print(f"    {len(new_teams)} team(s) new since the previous file: {shown}")


if __name__ == '__main__':
    main()
