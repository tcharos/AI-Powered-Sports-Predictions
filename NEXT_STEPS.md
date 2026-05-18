# Next Steps — Cashout Roadmap

Living document. Keep it short. Update statuses inline as phases complete.

## Phase status

| #   | Phase                              | Status         | Notes |
| --- | ---------------------------------- | -------------- | ----- |
| 1   | Live-history persistence + on-demand | ✅ done        | `output/live_history_<date>.jsonl` append on every Refresh Live Snapshot. Daemon loop removed. |
| 2   | Backtest harness (engine + CLI)    | ✅ done        | `ml_project/backtest/` + `scripts/run_backtest.py`. Self-validation against stored slip P/L passes. |
| 5   | O/U adjuster                       | ✅ done (pulled forward) | `LiveAdjuster.adjust_ou_probabilities()` — Poisson goal model blended with pre-match. Harness now evaluates O/U bets. |
| 3   | Bet status migration (CASHED_OUT)  | ⏸ deferred     | Cheap (~30 min). Defer until just before Phase 7 — nothing uses it yet. |
| 6   | Bets↔live UI linkage (display only) | ⏸ deferred    | Show open bets joined to live matches with fair-value cashout number. No button yet. |
| 7   | Manual cashout endpoint + button   | ⏸ deferred     | Per-bet (not per-slip). Settlement on cashout credits the specific lane. |

## The data wait

This is the blocker for Phases 3+. Statistical signal on cashout rules requires roughly **50+ settled bets per lane**.

| Date checkpoint | Settled bets (target ≥50/lane) | Action |
| --------------- | ------------------------------ | ------ |
| 2026-05-18 (today) | value: 9, conviction: 1, model: 0 | wait |
| 2026-05-25  (+1 wk) | rerun backtest, eyeball Δ stability | wait |
| 2026-06-01  (+2 wk) | likely enough → start Phase 3 + 6 | proceed |

While waiting:
- Keep clicking **Refresh Live Snapshot** when matches we predicted are live. Builds `output/live_history_*.jsonl` for real-trajectory backtests.
- Re-run `scripts/run_backtest.py` weekly. If a rule's Δ stays positive across multiple weekly runs, it's a candidate for default-on at Phase 7.

## Open / deferred work (smaller items)

- **Calibration spot-check on real data** — once we have ~3 days of `live_history`, write a quick script that runs `LiveAdjuster` on real snapshots from games we know the outcome of, to see if "aggressive prob swings near full-time" survives real-game noise or was a synthetic-trajectory artifact.
- **Backtest report polish** — `bets_by_type` breakdown (1X2 vs O/U), sortable JSON output.
- **OS-level integration** — `live_data.json` currently overwritten each refresh; consider keeping last N snapshots in memory for the UI to show "trend" arrows.

## Known limitations of the current backtest

- **Synthetic trajectories** are crude: linear xG accumulation, no in-match drama (red cards, momentum swings). Use harness for *directional* signals only, not for tuning rule thresholds to the third decimal.
- **No counterfactual stake redeployment** — when a rule cashes out at min 60, the freed bankroll could in principle be reused. The harness reports a lower bound; actual edge is somewhat higher.
- **Adjuster bias near full-time** — late goals produce very large prob swings (home 0.32 → 0.84 at min 73). Rules built on this may over-fire after late equalisers. Calibrate against real data before trusting.

## How to update this doc

When a phase completes:
1. Flip its row to ✅ done with a one-line summary.
2. Add any newly-discovered work to "Open / deferred work."
3. Don't grow the doc into a changelog — git history is the changelog. This file is the *forward-looking* roadmap.
