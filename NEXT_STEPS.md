# Next Steps — Cashout Roadmap

Living document. Keep it short. Update statuses inline as phases complete.

## Phase status

| #   | Phase                              | Status         | Notes |
| --- | ---------------------------------- | -------------- | ----- |
| 1   | Live-history persistence + on-demand | ✅ done        | `output/live_history_<date>.jsonl` append on every Refresh Live Snapshot. Daemon loop removed. |
| 2   | Backtest harness (engine + CLI)    | ✅ done        | `ml_project/backtest/` + `scripts/run_backtest.py`. Self-validation against stored slip P/L passes. |
| 5   | O/U adjuster                       | ✅ done (pulled forward) | `LiveAdjuster.adjust_ou_probabilities()` — Poisson goal model blended with pre-match. Harness now evaluates O/U bets. |
| 3   | Bet status migration (CASHED_OUT)  | ⏸ deferred     | Cheap (~30 min). Defer until just before Phase 7 — nothing uses it yet. |
| 6   | Bets↔live UI linkage (display only) | ⚙ partial      | Dashboard live rows show a per-match bet column: lane badge, type/selection/odds, stake, fair-value cashout (now wired for **both 1X2 and O/U** — `adj_ou_probs` are persisted to live snapshots since 2026-05-18), state badge (🟢 lock-in / 🔴 stop-loss / 🟡 hold). Pre/Live probs table also shows 5 markets (1/X/2/O/U) per match. No Cash-Out button, no auto-action. Same template fragment isn't shared with the `/football/live_analysis` standalone page yet. |
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

### Weekly backtest re-run — must be local

Tried a remote `/schedule` routine for this; it won't work. `output/` (bets, predictions, verifications, live history) is gitignored and lives only on the local machine — a cloud agent would see an empty `output/`. Run it yourself on the dates in the checkpoint table:

```bash
cd ~/Documents/projects/sports_predictor && \
  source venv/bin/activate && \
  PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/run_backtest.py --paths 50
```

Easiest reminder: macOS Calendar / Reminders entry for each checkpoint date. After the run:

1. Update the checkpoint row's third column with actual settled bets per lane (from the run's "Skipped — unsettled=N" line + the per-lane bet counts in the report).
2. Note Δ trends vs the 2026-05-18 baseline: `late_drift/value = +21.80`, `stop_loss/value = +16.83`, `lock_in_profit/value = −5.81` (n=10).
3. If a rule's Δ flips sign or shifts >50% as bets accumulate, that's a signal the synthetic trajectories are misleading and we should wait for more real `live_history_*.jsonl` data before trusting the harness.

## Real betting integration — Pamestoixima (DORMANT)

Target bookmaker: `pamestoixima.gr` (OPAP). Main account, **read-only operations only**.
End-goal deferred — revisit after dormant steps stay green for several weeks with no
anti-bot flags. Real bet placement, settlement, withdrawal are **explicitly out of scope**
until that re-evaluation.

Account confirmed to **not** use 2FA. If that changes, step 3 needs a manual cookie-bootstrap
revision before proceeding.

### Checklist

- [x] **1. Module skeleton** — `real_betting/` package with `Bookmaker` ABC, `credentials.py` (Keychain stubs + working `mask_username`), `session.py` (working `session_lock` context manager + `BrowserSession` stub), CLI entrypoint with four subcommands stubbed. `python -m real_betting.cli --help` works; subcommands return exit 1 with NEXT_STEPS pointers. `.env` + `*.session_state` added to `.gitignore`.
- [x] **2. Credentials wired** — `keyring==25.7.0` added to `requirements.txt`. `credentials.py` implements `set/get/has/delete_credentials` against the macOS Keychain. CLI subcommands `set-credentials` (prompts for username + password via `getpass`), `get-credentials` (masked output, never echoes password), `delete-credentials` (confirmation prompt). Round-trip verified against the real Keychain. `.env` + `*.session_state` were added to `.gitignore` in step 1. To store your Pamestoixima credentials: `python -m real_betting.cli set-credentials pamestoixima` — macOS may prompt "Always Allow" the first time the Python process accesses the new keyring service.
- [x] **3. Pamestoixima login (headed mode)** — login confirmed end-to-end on 2026-05-18 against the live site (English UI at `/en`). `bookmakers/pamestoixima.py` drives cookie banner, fills credentials, submits, detects post-login state via `#logged-in-menu` / `.pli-logged-in` / `.pli-profile__avatar`. Headed Chromium, randomised 800–2500ms delays, single-session lockfile, no auth retry. **Balance scraping is best-effort**: confirmed working for €0,00 (deposit button visible) but the positive-balance selector is unverified — will update `BALANCE_SELECTORS_POSITIVE` after first deposit + re-login.
- [ ] **4. Session persistence** — save Playwright storage state to encrypted, gitignored file. Second run reuses cookie until expiry; falls back to fresh login on cookie rejection.
- [ ] **5. 6a — Locale handling** — switch UI language to English if Pamestoixima supports it; otherwise extend `entity_resolver.py` with a Greek↔English team-name normalisation table. Validate against today's `predictions_*.csv`.
- [ ] **6. 6b — Fixture discovery** — navigate today's football fixtures, scrape `{home, away, league, kickoff, fixture_url, market_ids}`. Output JSON to `output/real_betting/fixtures_<date>.json`.
- [ ] **7. 6c — Predictions ↔ Pamestoixima fixtures matching** — fuzzy-match against `predictions_*.csv`. Fetch current 1X2 + O/U 2.5 odds per matched fixture; compare to the odds we used in the prediction. Report on stdout + saved to `output/real_betting/match_report_<date>.json`. Acceptance: ≥80% fixture-match rate on a typical 20-match day.
- [ ] **8. 6d — Headless mode validation** — once steps 3–7 are stable in headed mode for ~1 week, re-run end-to-end with `--headless`. Watch for selector failures, behavioural detection, captcha challenges. If clean for another week, headless becomes default.

### Anti-bot mitigations baked in from day one

- Headed mode default for steps 3–7. Headless gated by step 8.
- 800–2500ms randomised delays between any action.
- Single-session lockfile prevents concurrent runs from the same machine.
- No auth retries — one failed login attempt, stop and surface for human.
- Screenshot + DOM dump on failure to `output/real_betting/failures/`.
- **playwright-stealth (`Stealth().apply_stealth_sync`)** applied to every browser context — covers navigator.webdriver, plugins, languages, hardware concurrency, WebGL vendor, chrome runtime, iframe contentWindow, and a dozen other JS-level fingerprint vectors. Verified against bot.sannysoft.com (4 passed / 0 failed in headless mode).

### Optional escalation if anti-bot becomes a problem

If we ever hit consistent challenge pages or behavioural detection that playwright-stealth can't defeat, the next escalation is **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** — a Chromium fork with **C++-source-level fingerprint patches** (49 patches covering canvas, WebGL, audio, fonts, GPU, screen, WebRTC, network timing, automation signals + humanised mouse curves and per-character keyboard timing). Free, self-hosted, no API costs, ~200MB auto-downloaded binary, MIT wrapper. Compatible with Playwright via patchright backend. Trade-off: another dependency + binary auto-download + macOS Gatekeeper `xattr -cr` one-time step + headed mode recommended for aggressive sites. **Don't adopt preemptively** — the current playwright-stealth + launch flags handle most cases; reach for CloakBrowser only if specific detection persists (Imperva/Datadome/Akamai walls). Even with CloakBrowser, behavioural detection (regular polling, identical action timings, navigation patterns) is **not** addressed — the real fix is "infrequent, demand-driven, single-session" usage, not "more stealth".

### Eventually (placeholder — DO NOT START)

When/if we revisit the end-goal decision:

- [ ] **9. Bet placement** — design phase. New plan required. Conviction-lane only as starting point per earlier recommendation.
- [ ] **10. Settlement reconciliation** — match Pamestoixima's settled-bet history against our `bets_*.json`.
- [ ] **11. Withdrawal flow** — **never automated**. Manual only, by design.

## Enriched live stats & adjuster v2 (proposal)

Question: more granular live stats (xGOT, big chances, touches in opp box, shots inside/outside box, woodwork, etc.) should improve cashout decisions. Approach is phased to avoid over-fitting heuristics on a small sample.

| Step | Effort | Status | Notes |
| ---- | ------ | ------ | ----- |
| Scrape all available extended stats; persist to JSONL | small | ✅ done | `flashscore_spider.py` now extracts xgot, big_chances, shots_inside_box, shots_outside_box, woodwork, touches_opp_box, saves, yellow_cards, fouls. Misses leave the key absent (no crash). Every refresh appends to `live_history_<date>.jsonl`, future-proofing the backtest harness regardless of which stats we currently use in rules. |
| Display the most informative stats in dashboard | small | ✅ done | Stats table on each live row shows xG / **xGOT** / Poss / **Tch** (touches in opp box). BigCh and total Shots were briefly displayed then dropped as redundant with xG/xGOT. Headers have full-name tooltips. |
| Browser-driven auto-refresh while tab open | small | ✅ done | "Auto 5m" checkbox in the Live header. Polls `/football/refresh_live` every 5 min while `document.visibilityState === 'visible'`. Pauses when tab is hidden or minimised. State persists via `localStorage`. Skips when a previous run is still in `'running'` state (re-checks `/status` before each trigger). No server-side daemon. |
| Wire new stats into LiveAdjuster heuristics | medium | ⏸ hold | Don't add new handcrafted layers until backtest harness has ≥50 settled bets per lane to score variants against. Premature tuning of weights on a 10-bet sample is just adding parameters to over-fit. |
| Replace heuristics with a learned model | large | ⏸ hold | Logistic regression or gradient boost on `(snapshot_state, final_outcome)` pairs from `live_history_*.jsonl`. Needs weeks of accumulated history first. Eventual right answer for adjuster v2, but data-gated. |

## Per-league probability recalibration (proposal)

**Why**: The model produces `P(home/draw/away)` and `P(over/under)` per match. EV = `our_prob × odds − 1`. When the model overestimates a probability — typically in leagues with thin training data or high outcome variance — the EV signal it produces is inflated, which can dominate the slip via Option B sizing. The current `ev_cap_value = 0.05` is a downstream guard; this work is the proper upstream fix.

Calibration is an **offline** step: it uses our existing `data_sets/MatchHistory/` corpus (≥1000 matches per major league since ~2010), not real-bet outcomes, so the small settled-bet sample doesn't gate it.

### Phases

- [x] **C1 — Diagnose per-league miscalibration.** `ml_project/calibration/diagnose.py` + `scripts/run_diagnose_calibration.py`. Two modes, both via 5-fold TimeSeriesSplit OOF predictions (no leakage). Outputs CSV + Markdown to `output/calibration/diagnose_<ts>_<mode>.{csv,md}`. <br>**Full-features mode (default)** — uses the same feature set as `train_model.py` (43 features incl. shots/corners). 20 leagues survive dropna; reflects production-model calibration for leagues whose CSVs include in-match stats (England, Germany, Italy, Spain, France, etc.). <br>**Minimal-features mode (`--minimal-features`)** — strips 12 shot/corner-dependent features. 35 leagues survive; covers thin-data leagues including Veikkausliiga (Finland), Argentine leagues, MLS, J-League. Reflects a hypothetical simpler model; calibrations here feed C2 for leagues the full-features run can't see. <br>**Headline findings (first runs, 2026-05-19)**: (a) **Liga Profesional (Argentina)** is the most-biased O/U market — model says ~50% under, actual ~65%, inflating Over EVs by ~+14.8pp; (b) **D1 / Bundesliga** confirmed at +10.9pp over-Under bias; (c) **Veikkausliiga (Finland)** appears in minimal mode with +6.6pp over-Under bias — accounts for roughly half of the Lahti vs VPS EV=+0.12 that prompted the cap. The `ev_cap_value=0.05` guard handled this case reasonably; (d) Italian leagues (I1, I2) worst on 1X2 calibration in both modes. <br>**For C2 sourcing**: major leagues calibrate off the full-features OOF, thin leagues off the minimal-features OOF; each league ends up with one Platt `(a, b)` in `data_sets/league_calibration.json`. No new production model added (see "When would we end up with two production models?" thread in commit history if revisiting).
- [x] **C2 — Fit Platt scaling per league + market.** `ml_project/calibration/fit.py` (per-market fit helpers + merge) + `scripts/run_fit_calibration.py` (CLI: runs both OOF modes, fits, merges, writes `data_sets/league_calibration.json`). 1X2 uses per-class one-vs-rest Platt then renormalises; O/U uses single binary Platt on P(over). Merge prefers full-mode calibrator where available, falls back to minimal-mode for thin-data leagues. **First fit (2026-05-19)**: 35 leagues with calibrators (20 from full-mode, 15 backfilled from minimal-mode). All 110 (league, market) pairs improved in-sample Brier, 0 regressions. Headline: Liga Profesional O/U Brier 0.511 → 0.450 (biggest correction); Bundesliga O/U 0.499 → 0.471; Veikkausliiga O/U 0.508 → 0.490 (the Lahti VPS pathology). In-sample numbers are optimistic by construction — C3 will validate on a chronological holdout before deployment. Output JSON is ~40 KB, gitignored under `data_sets/`, regenerated by re-running the script.
- [x] **C3 — Validate on holdout.** `ml_project/calibration/validate.py` + `scripts/run_validate_calibration.py`. Per-league chronological 80/20 split, refit Platt on the first 80%, score Brier/ECE on the last 20%. Acceptance gate: ≥60% improvement rate AND no league regresses >5%. **First run (2026-05-19)**: improvement rates **80% (full) / 91% (minimal)** — both well above 60%. One entry exceeded the 5% regression cap: `SC3 / O/U` at +8.58% (n_test=60 — Scottish League 2 OU; thin sample, calibrator unstable). The script auto-rewrites `data_sets/league_calibration.json` to drop entries that fail the holdout test; `SC3|ou` removed, all other 69 / 70 entries deployed. Backup saved as `*.prefilter.bak`. Production C4 will fall back to raw probs for filtered entries. Pass `--no-filter` to skip the rewrite (e.g., to inspect raw fits).
- [x] **C4 — Apply at inference.** `ml_project/calibration/apply.py` + `league_aliases.py`. `predict_matches.py` applies Platt to raw probs between `model.predict_proba()` and the heuristic adjuster, recomputes Conf / EV / Kelly downstream. Flashscore league names (`"COUNTRY: League"`) map to calibration-JSON keys via an explicit alias table — no fuzzy strip-prefix fallback (would alias-collide e.g. `GREECE: Super League` → Chinese SL). Unmapped leagues, missing entries, or `use_league_calibration: false` (per-sport in `betting_config.json`, default `true`) pass raw probs through. New CSV columns in `predictions_*.csv`: `Home Win % (raw)`, `Draw % (raw)`, `Away Win % (raw)`, `Over % (raw)`, `Under % (raw)`, `Cal 1X2 Source`, `Cal O/U Source`. Verified: predictor instantiates, loads 35 leagues, applies correctly on sample inputs (Bundesliga full-mode, Veikkausliiga minimal-mode), and falls back cleanly for Greek SL / Brazilian SA / European cups. Live CSV column verification waits for the next fresh `run_predictions.sh` cycle.
- [x] **C5 — Wire into retrain pipeline.** `bin/retrain_pipeline.sh` extended from 3 steps to 5: data update → standings → train → **fit calibrators** (`scripts/run_fit_calibration.py`) → **validate + auto-filter calibrators** (`scripts/run_validate_calibration.py`). Both calibration steps are non-fatal: if either fails the existing `data_sets/league_calibration.json` (or the post-fit version) remains in place — production never loses calibration mid-cycle. Adds ~12 min to a full retrain (acceptable; retrains are infrequent). PYTHONPATH now also exports `$(pwd)/ml_project` so the calibration package's relative imports resolve. CLAUDE.md command table + ML pipeline step 4 updated.
- [ ] **C6 — Revisit `ev_cap_value`.** Once C4 ships, raise `ev_cap_value` back up (or remove it) — the downstream cap exists only to compensate for upstream miscalibration. Decide empirically: re-run the backtest harness with `ev_cap_value ∈ {0.05, 0.08, 0.15, ∞}` on calibrated probs and pick the one that maximises Δ vs baseline.

### Risks and watch-outs

- **Time-leakage**: must split holdout chronologically, not randomly. Random splits leak information from "the future" of a league into "the past" and overstate calibration quality.
- **Low-data leagues**: with N < 100 matches, Platt scaling is unstable. Fall back to a parent-tier calibrator (e.g., a single "minor European leagues" bucket fitted across pooled data) rather than per-league.
- **O/U threshold drift**: scoring environments change over years. Older Premier League seasons averaged ~2.5 g/match, recent ones run higher. Consider weighting recent data more heavily (sample weights `exp(-(today - match_date).days / decay_days)`).
- **Calibration is not bias correction**: Platt scaling fixes calibration (predicted 60% → actual 60%), not accuracy (the model's pick frequency). If a league's model genuinely picks the wrong outcome more often, calibration won't help — that's a feature-engineering problem upstream.

### Sequencing

C1–C2 are independent of every other roadmap item and can start whenever. C3 gates C4 (don't deploy uncalibrated calibration). C5 and C6 wait until C4 has run for a couple of weeks on live predictions.

## Future model improvements (proposal — NOT scheduled)

Recorded here so it doesn't get lost; not on the active queue. Captured 2026-05-19 after a "should we try non-XGBoost models / ensemble / multimodal?" question. **TL;DR**: XGBoost is hard to beat on tabular; the cheap wins still on the table beat any architectural pivot for at least the next quarter.

### Cheap wins worth doing first

- [ ] **D0 — Draw model has poor discriminative power.** Investigated 2026-05-19. The original "0% recall" finding turned out to be a **metric-reporting artifact**, not a production bug: the model's `predict_proba` outputs are well-calibrated to the actual draw rate (mean P(draw) ≈ 0.260 vs actual 0.254 — 1.02× calibration), but max output is only ~0.45, so threshold-0.5 metrics show 0% recall. Production uses raw probabilities (not the threshold), so the model has been contributing reasonable averaged probability values all along.
   The real limitation is **discriminative power**: even at threshold 0.3, recall is only 0.23 — the model can't pick out which matches are likely draws from features, just outputs ~base rate for everything. A sweep of `scale_pos_weight ∈ {1.0, 1.3, 1.5, 1.7, 2.0, 2.5, 3.0}` showed the textbook tradeoff: higher weight → higher recall but worse calibration (spw=3.0 gives 1.96× overcalibration). Not solvable via class weighting alone — would just shift bias.
   **Real fix paths** (deferred, no quick win):
   - Richer features specifically for draw detection (style mismatch, mid-table congestion, late-season motivation, recent draw rate per team).
   - Different architecture: ensemble of draw-specialized models trained on subsets, or temperature-scaled multi-class output without a separate binary stage at all.
   - Remove the 2-stage logic entirely: the multi-class model is already well-calibrated for draws; the averaging adds little. Simpler, equivalent.
   While investigating, `train_draw` got an instrumentation upgrade (metrics at thresholds 0.30/0.40/0.50, calibration ratio, automatic warnings if mean P(draw) drifts >1.5× or <0.7× actual rate) so this kind of misdiagnosis doesn't recur.
- [x] **D1 — Re-run `tune_model.py`.** Done 2026-05-19. Feature list synced with the trainer (was missing `A_form_sa` plus the entire ppg/strength block — 9 features). New hyperparameters: 1X2 `n_estimators 470 → 827`, `reg_lambda 0.01 → 1.0`, `min_child_weight 3 → 7`; O/U `n_estimators 234 → 386`, regularisation swapped L2-heavy → L1-heavy. Old params backed up as `models/best_params_*.json.bak_20260519`. **Cosmetic warning during run** (`"Parameters: { 'enable_categorical' } are not used"`): looks scary but is just XGBoost's C++ booster complaining about an unrecognised parameter. The categorical-ness is encoded in three other places (DMatrix `enable_categorical=True`, sklearn wrapper `enable_categorical=True`, pandas `astype('category')`), and the booster's `feature_types` correctly reports `'c'` for `league_cat`. Tuning was correct; only the log noise is wrong. Future cleanup: filter the warning in tune_model.py or pop `enable_categorical` out of `best_params` before passing to `xgb.cv`. **Deployment**: model must be retrained for new params to take effect; run `./bin/retrain_pipeline.sh` to also refit calibrators against the new model (otherwise calibrators are stale by construction).
- [ ] **D2 — Feature engineering pass.** Each ~1–2 days, often 1–3% Brier each:
  - **Variable rolling windows per league** (currently fixed last-5 globally — Premier League is more stable than Brazil; window should reflect that).
  - **Opponent-adjusted form** — currently form is "vs anyone"; adjusting by opponent strength would distinguish "won 5 in a row against relegation candidates" from "won 5 in a row against top six".
  - **Head-to-head specifics** — last 2–3 H2H matches as features (rivalries, stylistic matchups).
  - **Manager-change indicator** — binary flag for "new manager in last N games" (well-documented predictive signal).
  - **Promoted-team indicator** for league-rookies in their first season (model usually mis-prices these).

### Architectural pivots — only if cheap wins are exhausted

- [ ] **D3 — Bivariate Poisson / Dixon-Coles.** The football-domain inductive bias for joint home/away goal counts. Properties: single model produces 1X2 + O/U + BTTS + correct-score consistently; per-league attack/defence strength parameters that mean something; per-league fitting natural (auto-calibrated by construction, likely makes the C* Platt layer redundant). Catch: native form is parametric and can't use ELO / xG / shots; would need a hybrid (Karlis-Ntzoufras: XGBoost-derived strength estimates feeding Poisson rates). ~3–4 weeks for a working v1. Best candidate IF we eventually want to outgrow XGBoost.
- [ ] **D4 — More data, not different model.** "Multimodal" in our context = ingest non-tabular signal. Each is a data-acquisition project, not a model project; any signal added would benefit XGBoost too:
  - **News sentiment** features (manager statements, injury reports).
  - **Player availability** / lineup feeds (key player out is highly predictive but currently invisible to the model).
  - **Bookmaker line movement** as a derived feature (line drifts before kickoff carry signal).

### Things explicitly NOT worth doing pre-emptively

- **Ensemble stacking with XGBoost + LightGBM + CatBoost.** All three are gradient boosting variants — too correlated to give diversity gains. ~2 weeks of work for sub-1% Brier improvement.
- **Tabular neural networks** (TabNet, FT-Transformer). Decade of evidence: GBT > NN on tabular unless you're combining with non-tabular features.
- **Generic ensembling / model stacking** without genuine model-family diversity. Diminishing returns.

### Decision rule for when to revisit

Pull D3 (bivariate Poisson) off the shelf if **and only if**:
1. D1 + D2 have been done (within the last ~6 months), AND
2. Current Brier is stable across multiple retrains (no easy headroom left), AND
3. Per-market inconsistencies (1X2 says one thing, O/U says another for the same match) become an observed problem.

Until then, keep XGBoost + Platt calibration + ev_cap_value as the deployed stack.

## Future analysis ideas (not yet scoped)

- **Scrape real cashout value from the bookmaker** — current dashboard shows an **internal fair-value estimate** (`stake × odds × adj_prob × 0.95`), not what Pamestoixima would actually pay. The real offer is what matters for the decision; the bookie applies their own haircut and may differ materially from our model. The decision rule becomes "their offer > our estimate ⇒ accept; their offer < our estimate ⇒ hold." Implementation: once `real_betting/bookmakers/pamestoixima.py` can navigate to a fixture's bet-slip area (requires real-betting steps 6b/6c to be done first), add a `get_cashout(bet_url)` method that returns the live offer, and surface both side-by-side in the dashboard ("Bookie €X.XX · Est. €Y.YY"). Gated by: real-betting integration maturity + Phase 3 bet schema linking each placed bet to a bookmaker bet/slip ID for lookup.

- **"Place bet now?" shortcut on live rows** — when a live match has no open bet, show a one-click action that takes you to `/football/auto_wager` (or a future bet-placement modal) pre-filtered to that match. Useful for value-discovery on in-progress games where the score state has shifted the EV. Caveat: couples live analytical view with virtual betting action; needs design before building. Revisit when /auto_wager UI is generalised enough to accept a per-match filter.

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
