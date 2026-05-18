"""Cashout backtest harness.

Phase 2 of the live-cashout roadmap. Replays historical bets through
synthetic (or, where available, real) per-minute trajectories under
candidate cashout rules, and reports rule-vs-baseline P/L deltas.

CLI entrypoint: scripts/run_backtest.py
"""
