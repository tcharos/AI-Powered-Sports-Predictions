# Live Prediction Architecture

## Overview
The Live Prediction system aims to adjust pre-match probabilities dynamically as the game progresses, using real-time statistics.

## Components

### 1. Data Ingestion (LiveScraper)
- **Role**: Fetch live stats from Flashscore.
- **Frequency**: Every 60 seconds.
- **Target**: Matches currently "In-Play".
- **Data Points**:
  - `Minute`: Current time (e.g., 23').
  - `Score`: (e.g., 1-0).
  - `xG`: Expected Goals (Home/Away).
  - `Shots`: Total and On Target.
  - `Red Cards`: Home/Away.
  - `Possession`: %.

### 2. The Adjustment Layer (Engine)
- **Input**: 
  - `PreMatch_Prob`: From XGBoost model (e.g., Home: 0.60).
  - `Live_Stats`: Current state.
- **Logic**:
  - Apply [Heuristic Rules](heuristics_rules.md) to modify `PreMatch_Prob`.
  - *Example*: If Home Team (0.60) is losing 0-1 but has xG 2.5 vs 0.1, maintain high probability (Value Bet).
  - *Example*: If Home Team gets Red Card, penalize probability by factor 0.6.

### 3. Frontend (Dashboard)
- **New Tab**: "Live".
- **Updates**: WebSocket or Polling (5s interval) to `app.py`.
- **Visuals**:
  - Flash "Goal" indicator.
  - Highlight "Value Bets" (where Model > Bookie Live Odds).

## Phase 1 Implementation Plan
1. Create `live_scraper.py` to target `https://www.flashscore.com/` main page or live tab.
2. Parse the WebSocket/XHR frames from Flashscore (complex) OR simple HTML parsing of the "Live" tab.
3. Store state in generic `live_matches` dict in `app.py`.
