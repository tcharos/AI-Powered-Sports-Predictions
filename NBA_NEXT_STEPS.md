# NBA Next Steps

NBA-specific roadmap. Sibling to `FOOTBALL_NEXT_STEPS.md` (football / cashout / cross-cutting). Living document — keep it short, update statuses inline as work completes.

For the broader project picture (cashout phases, real-betting integration, football model experiments D2/D3/D4/D5/D6/D7) see `FOOTBALL_NEXT_STEPS.md`. NBA reactivation history is preserved there in the "NBA reactivation" table; this doc takes over from Phase 3 onward.

## Phase status

| # | Phase | Status | Notes |
| --- | --- | --- | --- |
| 1 | Data layer (local archive + nba_api) | ✅ DONE (2026-05-28) | `process_archive.py` → `data_sets/NBA/team_game_stats.csv` (32,359 games / 27 seasons / merge-safe rebuild); `fetch_nba_daily.py` (`ScoreboardV3` for fixtures + `LeagueGameLog` for append-results, idempotent dedup that preserves the archive's richer rows, `time.sleep(1)`). pbpstats dropped (stats.nba.com geo-blocked; nba_api's headers work against `data.nba.com`). 3 retired fetchers → `ml_project/nba/legacy/`. Branch `feat/nba-reactivation-data-model`. |
| 2 | Model rework + calibration | ✅ DONE (2026-05-28) | ~40-feature set (rest_days / b2b / ELO_pre + L5 [pts/allowed/win + FG/3P/FT% + reb/ast/tov/plus_minus] + L10 [subset] + venue-matched L5). Winner 66.21% acc / Brier 0.2127 on 5-fold TimeSeriesSplit; total MAE 14.85. `predict_nba.py` rewritten to compute serve-time features from the **same corpus** the trainer used — train/serve skew killed (Δ=0.0000 on every rolling/rest/venue feature for a verified backtest game). Single global Platt (`apply_home_win_platt` returns the same `(prob, applied, source)` 3-tuple as football's `apply_platt_1x2` so the UI / betting layer stays sport-agnostic). |
| 3 | UI + betting integration (Phase 3 v1) | ✅ DONE (2026-05-28) | `NbaBettingBackend(VirtualBettingBackend)` subclass; `nba` entry in `betting_config.json` (additive); `nba_bp` blueprint reactivated per the "Adding a new sport" steps in `CLAUDE.md`. Predictions dashboard + paper-money moneyline betting flow live. Football routes, templates, shared betting code untouched (operator's "very important" football-isolation guarantee held). |
| 3.5 | Betting dashboard tab-ification — NBA tab | ✅ DONE (2026-05-30) | Phase A (2026-05-28): `/betting` → `templates/betting_tabbed.html` with **All / Football / Euroleague / NBA** tabs; football tab includes `_betting_football_panel.html` verbatim. **Phase B (2026-05-30): NBA placeholder replaced with the shared `_betting_basketball_panel.html`** — lane comparison + collapsible slip history, **ML + O/U** markets, read-only (no void/cashout — NBA has no live feed in v1). Shared verbatim with Euroleague's Phase B. `betting_tabbed()` route computes the full `compute_sport_summary` + per-sport `lane_defaults` (helper `_lane_defaults_for(slug)`); the tab `{% with %}`-scopes them into the partial. `?tab=football|euroleague|nba|all` picks the initial tab. Smoke-tested 200 + panel present; renders empty-state cleanly. |

## Active queue (ordered)

### Phase B — Build out the NBA betting tab — ✅ DONE (2026-05-30)

Replaced the NBA placeholder in `templates/betting_tabbed.html` with the shared **`_betting_basketball_panel.html`** — lane comparison + collapsible slip history, adapted for NBA's **moneyline + O/U** markets (`type` badge per bet; read-only, no void/cashout since NBA has no live feed in v1). The panel is shared verbatim with Euroleague (both are basketball, same market shape), `{% with %}`-scoped per sport. Route `betting_tabbed()` computes `compute_sport_summary` + `lane_defaults` for each basketball sport. **Acceptance met**: `/betting?tab=nba` shows lane comparison + slip history with the same shape/feel as the football tab, no football noise.

### Date inputs on the NBA dashboard — ✅ DONE (2026-05-30)

Parity with football + Euroleague. `web_ui/nba/routes.py` `_kick()` now takes an `args` list forwarded to the bin script; `/nba/predict` + `/nba/verify` read `request.form['date']` (empty = default tomorrow/yesterday). Template `web_ui/templates/nba/index.html` has an `<input type="date" name="date">` input-group next to each Run button. Wrappers already accept `[YYYY-MM-DD]` as `$1`. Verified: 2 date inputs render on `/nba/`.

### Retune `tune_nba_models.py` for the new 40-feature set

Current `best_params_*.json` were tuned on the old 12-feature era and load gracefully (training ran), but optimal hyperparameters likely shifted. Run after the new feature set has stabilised for a couple of training cycles.

### Update `evaluate_nba_predictions.py` to the new prediction-CSV columns

New columns: `Home Win Prob` + `Home Win Prob (raw)` + `Cal Source` + `Predicted Winner` / `Predicted Total`, plus a `gameId` join. Evaluator is currently flagged non-fatal in `run_nba_verification.sh`; flip back to required once schema-aligned.

## Player availability + on/off impact adjuster (NEW — scoped 2026-05-29, re-homed from football D4)

**Origin.** Football D4 (an injury/availability adjuster) was shelved at N2 because its only cheap player-quality signal — SoFIFA **OVR** — is a static quality rating, not a marginal-impact metric, and the right metric (xPAR / on-off xG) isn't available cheaply for football (see `FOOTBALL_NEXT_STEPS.md` → "D4 — SHELVED at N2"). **NBA is the opposite case** and the natural home for the idea.

**Why it's worth it for NBA (where it wasn't for football):**
- **We already have real impact data locally** — no scraping, no video-game proxy. The archive (`data_sets/NBA/archive/`) has `PlayByPlay.parquet` (932 MB → lineup stints → true **on/off net rating**, the gold standard) and `PlayerStatistics(Extended).csv` (840 MB → per-game **plus-minus**, minutes → cheap BPM-like proxies). `Players.csv` / `Games.csv` give the join keys.
- **5-man game → one star is 25–40% of production** → a single absence is a large, low-noise, *estimable* swing (vs football's 11-man dilution).
- **Margin↔win-prob is a clean, principled mapping** in NBA (final margin ≈ Normal, SD ≈ 12; ~1 pt ≈ 2.5–3% win prob near pickem) — so "team loses X net points without player" converts directly to a prob shift. Football had no such clean conversion.
- **Late injury / rest / load-management news is a known market soft spot** — the line is slow to confirmed inactives, so this is a genuine new-edge candidate, not a market-priced dead end.

**The one gap = forward availability.** The archive gives *historical* impact (great for computing the metric) but not "who's inactive tomorrow." That feed is the make-or-break, exactly as Flashscore lineups were for football's N1.

**Phases (parallel to football's N-series, prefixed NA):**
- **NA0 — Availability source probe (make-or-break, do first).** Find a reliable forward "who's out" feed: the NBA official **Injury Report** and/or `nba_api` player status. Confirm coverage + cadence. **Cadence caveat:** confirmed inactives land ~30–60 min pre-tip — the late-news gap *is* the edge, but it implies a **near-tip prediction refresh path**, not the current night-before flow. Resolve this in NA0 (a "late refresh" mode for `predict_nba.py`).
- **NA1 — Player impact metric (offline, from archive).** v1 = season **plus-minus per-100 / simple BPM** + **minutes share** per (player, season), cached — cheap, skips parsing the 932 MB PBP. v2 = **true on/off net rating** from PlayByPlay stints (better, heavier). Output a per-player impact table.
- **NA2 — Replacement + depletion.** importance = impact − replacement level (replacement ≈ the player who absorbs the minutes, or a fixed replacement baseline ≈ −2 to −3 net pts/100). Team depletion = Σ over inactives of (importance × expected minutes_share).
- **NA3 — Adjuster (post-model, capped, flagged).** Convert team depletion (net points) → shift in `Home Win Prob` (via the margin↔prob mapping) **and** `Predicted Total`. Insert post-model in `predict_nba.py` behind `use_availability_adjustment` (default off) in `betting_config.json`; cap the shift; add audit columns. Single channel like football's Platt/heuristic — EV/Kelly inherit it. (Mirrors the shelved football N3 signature, but on a real metric + principled conversion.)
- **NA4 — Forward validation.** Log (availability, pre/post probs, outcome); adjusted-vs-unadjusted Brier forward; tune the cap.

**Risks / watch-outs:**
- **NA0 reliability + cadence is the whole ballgame** — no feed, no project (same gate football's importance source was).
- **Double-counting:** the team L5/L10 rolling features already partly absorb a *persistent* absence; the adjuster should add value mainly on *new/changed* availability — validate it isn't just re-encoding what rolling already knows.
- **Minutes-share** for the replacement is itself a mini-model; v1 can distribute the absentee's season minutes-share proportionally across the rest.
- **Margin↔prob mapping** must match the winner model's calibration — verify against the total regressor's implied spread.
- Reuse from football D4: the **N-series file-passing shape** (availability JSON → importance JSON → adjuster reads it) and the coverage-gate/fail-safe discipline transfer; the SoFIFA-specific code does not.

**Not started.** NA0 is the next concrete action if this is greenlit; everything downstream is gated on a working availability feed.

## Open / deferred

- **Per-sport tuning of the betting strategy tunables — check if it has value (NEW 2026-05-30).** The `sports.<slug>` block in `betting_config.json` is already per-sport *structurally*, but NBA carries football's *values* (`min_confidence`, `stake_multiplier`, `max_stake_pct`, `ev_cap_value`, conviction `min_odds`/`min_confidence`/`stake_pct`, `model_*` base/ev factors, the three `*_max_daily_exposure_pct` caps). Those were tuned for football's 1X2 + O/U markets and odds distribution; NBA is **moneyline + totals** with a different odds shape — heavy favourites (tight ML ~1.1–1.4), fewer long shots — so the inherited gates may mis-size: e.g. conviction `min_odds 1.40` rarely fires on NBA favourites, and the EV cap is calibrated to football's variance. **Action**: once a live slate accrues settled NBA bets, run the football-style lane backtest (`run_value_lane_backtest`-equivalent) on NBA odds to check whether per-sport tuned values beat the football defaults, and set them if so. Gated on settled-bet volume (the same data wait football had). Mirror in Euroleague (`EUROLEAGUE_NEXT_STEPS.md`).
- **Advanced stats** (pace / four-factors / per-possession) would help the model, but stats.nba.com (the only practical source from this machine) is geo-blocked. Revisit if a Cloudflare-friendly alternative surfaces. (`data.nba.com` works via nba_api headers — that's the seam we already use; the geoblock is specifically on the `stats.nba.com` rich endpoints.)
- **Totals market (P(Over)) — ✅ DONE (2026-05-29).** `nba_calibration.py` now estimates the total-regressor residual **σ** from OOF predictions and validates the normal-approx (σ=18.9; z̄≈0.03, z-std=1.00, over-rate@μ=0.500, ±1σ=0.699 / ±2σ=0.956 — essentially Gaussian), stored under `total_over_under` in `nba_calibration.json`. `prob_over(μ, line, σ)` helper added. `predict_nba.py` emits `Total Sigma` / `Over Line` / `P(Over)` / `P(Under)` (the latter three when ESPN odds carry a total line). `/nba/auto_wager` builds an **O/U market** alongside moneyline (same 3-lane sizing). Live the moment ESPN odds have a total. Shared identically with Euroleague.
- **Forward-only ESPN odds caveat** — `fetch_espn_odds.py` was rewritten 2026-05-28 as a plain-HTTP JSON client over `site.api.espn.com/.../scoreboard?dates=YYYYMMDD` (moneyline + spread + total + per-side juice, American→decimal; per-date output). Wired as a non-fatal step in `run_nba_predictions.sh`. **ESPN doesn't preserve historical odds** (`odds: []` for completed games), so we can't backfill — odds-aware features / EV-graded backtests need to accumulate forward from here.
- **Phase 3 NBA pipeline lessons → bring to football?** Once Phase B settles, audit whether anything proven in NBA's clean re-build (especially the serve-time-from-corpus pattern that killed train/serve skew) is worth retrofitting into football's `feature_engineering.py` / `HeuristicAdjuster.get_team_strength` serve path. Not on the active queue.

## Bugs / fixes queue

_(empty)_

## How to update this doc

Same rules as `FOOTBALL_NEXT_STEPS.md`:
1. Flip rows to ✅ with a one-line summary when phases complete.
2. New work goes under "Open / deferred" unless it's actively queued.
3. Don't grow this into a changelog — git history is the changelog. This file is the *forward-looking* NBA roadmap.
