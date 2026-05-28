# NBA Next Steps

NBA-specific roadmap. Sibling to `FOOTBALL_NEXT_STEPS.md` (football / cashout / cross-cutting). Living document — keep it short, update statuses inline as work completes.

For the broader project picture (cashout phases, real-betting integration, football model experiments D2/D3/D4/D5/D6/D7) see `FOOTBALL_NEXT_STEPS.md`. NBA reactivation history is preserved there in the "NBA reactivation" table; this doc takes over from Phase 3 onward.

## Phase status

| # | Phase | Status | Notes |
| --- | --- | --- | --- |
| 1 | Data layer (local archive + nba_api) | ✅ DONE (2026-05-28) | `process_archive.py` → `data_sets/NBA/team_game_stats.csv` (32,359 games / 27 seasons / merge-safe rebuild); `fetch_nba_daily.py` (`ScoreboardV3` for fixtures + `LeagueGameLog` for append-results, idempotent dedup that preserves the archive's richer rows, `time.sleep(1)`). pbpstats dropped (stats.nba.com geo-blocked; nba_api's headers work against `data.nba.com`). 3 retired fetchers → `ml_project/nba/legacy/`. Branch `feat/nba-reactivation-data-model`. |
| 2 | Model rework + calibration | ✅ DONE (2026-05-28) | ~40-feature set (rest_days / b2b / ELO_pre + L5 [pts/allowed/win + FG/3P/FT% + reb/ast/tov/plus_minus] + L10 [subset] + venue-matched L5). Winner 66.21% acc / Brier 0.2127 on 5-fold TimeSeriesSplit; total MAE 14.85. `predict_nba.py` rewritten to compute serve-time features from the **same corpus** the trainer used — train/serve skew killed (Δ=0.0000 on every rolling/rest/venue feature for a verified backtest game). Single global Platt (`apply_home_win_platt` returns the same `(prob, applied, source)` 3-tuple as football's `apply_platt_1x2` so the UI / betting layer stays sport-agnostic). |
| 3 | UI + betting integration (Phase 3 v1) | ✅ DONE (2026-05-28) | `NbaBettingBackend(VirtualBettingBackend)` subclass; `nba` entry in `betting_config.json` (additive); `nba_bp` blueprint reactivated per the "Adding a new sport" steps in `CLAUDE.md`. Predictions dashboard + paper-money moneyline betting flow live. Football routes, templates, shared betting code untouched (operator's "very important" football-isolation guarantee held). |
| 3.5 | Betting dashboard tab-ification — NBA tab | 🟡 PARTIAL (2026-05-28) | Phase A shipped: `/betting` renders `templates/betting_tabbed.html` with **All / Football / NBA** tabs. Football tab includes `_betting_football_panel.html` verbatim (regression-verified: bankrolls + 196 KB render preserved). **NBA tab is a placeholder card** pointing at `/nba/`. Navbar's "📊 Betting Dashboard" repointed at `/betting` with a "Football-only view (legacy URL)" fallback so deep-links survive. `?tab=football|nba|all` picks the initial tab. **See Phase B below to finish.** |

## Active queue (ordered)

### Phase B — Build out the NBA betting tab

Replace the NBA placeholder in `templates/betting_tabbed.html` with a real per-bet panel mirroring football's lane comparison + slip-history table, adapted for NBA's **moneyline-only** market in v1 (simpler than football's 1X2 + O/U).

Cleanest port path: once the NBA bits get complex enough, extract them into `_betting_nba_panel.html`, mirroring the `_betting_football_panel.html` pattern. Until then, keep the markup inline in `betting_tabbed.html`.

When the v1 totals market lands (Open item below — P(Over) added to `predict_nba.py`), extend the panel with a second market section.

**Acceptance**: an NBA-only operator landing on `/betting?tab=nba` sees lane comparison + slip history with the same shape and feel as the football tab, no football noise.

### Date inputs on the NBA dashboard

Parity with football. Today the NBA `Predict` / `Verify` buttons are bare submit-forms with no date input — `/nba/predict` always runs for tomorrow, `/nba/verify` for yesterday, because `_kick()` in `web_ui/nba/routes.py` calls the bin script with no args.

Mirror football's pattern: an `<input type="date" name="date">` next to each button (input-group, sm) in `web_ui/templates/nba/index.html`; have `/nba/predict` + `/nba/verify` read the `date` form field and pass it through to `subprocess.Popen`. Wrappers `bin/run_nba_predictions.sh` and `bin/run_nba_verification.sh` already accept `[YYYY-MM-DD]` as `$1` (Phase 4 rewrite). Empty input = current default.

Small (~30 lines across template + routes).

### Retune `tune_nba_models.py` for the new 40-feature set

Current `best_params_*.json` were tuned on the old 12-feature era and load gracefully (training ran), but optimal hyperparameters likely shifted. Run after the new feature set has stabilised for a couple of training cycles.

### Update `evaluate_nba_predictions.py` to the new prediction-CSV columns

New columns: `Home Win Prob` + `Home Win Prob (raw)` + `Cal Source` + `Predicted Winner` / `Predicted Total`, plus a `gameId` join. Evaluator is currently flagged non-fatal in `run_nba_verification.sh`; flip back to required once schema-aligned.

## Open / deferred

- **Advanced stats** (pace / four-factors / per-possession) would help the model, but stats.nba.com (the only practical source from this machine) is geo-blocked. Revisit if a Cloudflare-friendly alternative surfaces. (`data.nba.com` works via nba_api headers — that's the seam we already use; the geoblock is specifically on the `stats.nba.com` rich endpoints.)
- **Totals market (P(Over))** — the total regressor is well-calibrated diagnostically but not currently emitted as a betting probability. Adding it unlocks Over/Under betting + a richer NBA betting panel (see Phase B). Cheapest path: convert the total regressor's prediction into a P(Over) via a normal-approximation around the line, validate calibration on a holdout, plumb through `predict_nba.py` to `predictions_nba_<date>.csv`.
- **Forward-only ESPN odds caveat** — `fetch_espn_odds.py` was rewritten 2026-05-28 as a plain-HTTP JSON client over `site.api.espn.com/.../scoreboard?dates=YYYYMMDD` (moneyline + spread + total + per-side juice, American→decimal; per-date output). Wired as a non-fatal step in `run_nba_predictions.sh`. **ESPN doesn't preserve historical odds** (`odds: []` for completed games), so we can't backfill — odds-aware features / EV-graded backtests need to accumulate forward from here.
- **Phase 3 NBA pipeline lessons → bring to football?** Once Phase B settles, audit whether anything proven in NBA's clean re-build (especially the serve-time-from-corpus pattern that killed train/serve skew) is worth retrofitting into football's `feature_engineering.py` / `HeuristicAdjuster.get_team_strength` serve path. Not on the active queue.

## Bugs / fixes queue

_(empty)_

## How to update this doc

Same rules as `FOOTBALL_NEXT_STEPS.md`:
1. Flip rows to ✅ with a one-line summary when phases complete.
2. New work goes under "Open / deferred" unless it's actively queued.
3. Don't grow this into a changelog — git history is the changelog. This file is the *forward-looking* NBA roadmap.
