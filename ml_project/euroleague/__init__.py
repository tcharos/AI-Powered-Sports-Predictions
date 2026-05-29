"""Euroleague + EuroCup pipeline (mirrors ml_project/nba/).

Phase 1 = data layer: build_corpus.py (raw euroleague-api CSVs → canonical
long-format team_game_stats.csv) + fetch_euroleague_daily.py (daily refresh).
The corpus shape matches what NBA's feature engineering consumes, plus a
``competition`` column (E = Euroleague, U = EuroCup) for the combined-model
per-competition split decided in Phase 0.
"""
