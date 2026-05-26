# Next Steps — Cashout Roadmap

Living document. Keep it short. Update statuses inline as phases complete.

## Phase status

| #   | Phase                              | Status         | Notes |
| --- | ---------------------------------- | -------------- | ----- |
| 1   | Live-history persistence + on-demand | ✅ done        | `output/live_history_<date>.jsonl` append on every Refresh Live Snapshot. Daemon loop removed. |
| 2   | Backtest harness (engine + CLI)    | ✅ done        | `ml_project/backtest/` + `scripts/run_backtest.py`. Self-validation against stored slip P/L passes. |
| 5   | O/U adjuster                       | ✅ done (pulled forward) | `LiveAdjuster.adjust_ou_probabilities()` — Poisson goal model blended with pre-match. Harness now evaluates O/U bets. |
| 3   | Bet status migration (CASHED_OUT)  | ✅ done (2026-05-20) | Schema prep only — no cashout endpoint yet (that's Phase 7). `CASHED_OUT` is now a recognized terminal status alongside `WON`/`LOST`/`VOID`. Settlement skips already-cashed bets, `compute_sport_summary` has a `cashed_out` counter and uses stored `cashout_amount`/`pnl` rather than recomputing, betting.html renders cashed-out rows with a distinct (info-blue) stripe and badge. Phase 7 populates `cashout_amount`, `cashout_profit`, `cashout_timestamp` on each affected bet. |
| 6   | Bets↔live UI linkage (display only) | ✅ done        | Dashboard live rows show a per-match bet column: lane badge, type/selection/odds, stake, fair-value cashout (1X2 + O/U), state badge (🟢 lock-in / 🔴 stop-loss / 🟡 hold), `real`/`est` source badge via scenario #3A. Cash Out button via Phase 7. Pre/Live probs table shows all 5 markets per match. The `/football/live_analysis` standalone page and `/football/dashboard` share `_open_bets_fragment.html` and both call `_attach_open_bets()` — verified 2026-05-25. |
| 7   | Manual cashout endpoint + button   | ✅ done (2026-05-22) | Per-bet (not per-slip), lane-aware credit. `VirtualBettingBackend.execute_cashout` (`web_ui/betting_backend.py:247`) + `POST /football/cashout/<bet_id>` route (`web_ui/app.py:870`) + Cash Out button in `_open_bets_fragment.html:45`. Multi-lane cascade per CLAUDE.md. Settlement integration: `resolve_daily_bets.py` skips CASHED_OUT for re-credit, includes them in slip totals. Currently cashes at **internal fair-value estimate** (`stake × odds × adj_prob × 0.95`); real bookmaker offer is a separate problem — see scenarios #3/#4 in `real_betting/test_case_scenarios.md`. `PamestoiximaBackend.execute_cashout` stub exists at `web_ui/betting_backend.py:494` for the Phase 9 swap. |
| 8a  | Scrape real Pamestoixima cashout offers | ✅ done (2026-05-25) | Scenario #3 from `real_betting/test_case_scenarios.md` shipped end-to-end. **3A** (consumer): `cashout_source` flag in `betting_config.json` (flipped to `'bookmaker'` for football 2026-05-25), `_load_bookmaker_offers` helper, `_attach_open_bets` rewired with synthetic fallback, `real`/`est` value badge. **3B** (scraper): `real_betting/read_open_bets.py` writes `output/real_betting/open_bets_snapshot.json` from a logged-in Pamestoixima session, live-validated against two real OPEN bets. **3C** (join + UI): discovered Flashscore vs Pamestoixima use different `match_id` schemes — added `_match_offer_by_teams()` fuzzy fallback via rapidfuzz. **3D** (link surface): green `🔗 linked` badge on every enriched bet that has a matching bookmaker record (independent of value source); standalone `/football/live_analysis` page filters to linked bets only. The link is **persisted** onto the bet on first match (`linked_to_bookmaker` + `pamestoixima_uuid` stamped on the slip) so it survives snapshot staleness and stays until the bet resolves (terminal → off the panel) — "link until resolved", not a timer. **3E** (refresh chain): manual "Refresh Live Snapshot" passes `?with_bookmaker=1` → `--with-bookmaker` to `run_live_analysis.py` (note: `triggerLive()` must fetch `form.action`, not a bare URL, or the param is dropped — that was a real bug). The script then chains the `read-open-bets` scrape **only if a live match intersects an OPEN bet** (2026-05-26) — skips the slow ~25s headed scrape when nothing relevant is live. Trade-off: when relevant matches are live, Pamestoixima now runs *after* Flashscore (~10s later) rather than in parallel. Auto-5m stays Flashscore-only because headless Pamestoixima is Akamai-blocked (step 6d ⛔). **Value freshness**: the real €offer shows whenever linked (no separate short cap — the on-disk value only changes on a manual re-scrape, so capping it just downgraded good data); the badge shows the snapshot age (`real · 30m`). Deferred minor: `market` / `odds` extraction in `read_open_bets.py` are informational-only and currently noisy. |
| 8b  | Cashout decision engine (HOLD / CASH_NOW / WARN) | ⏸ deferred (depends on multi-week real-offer data) | Scenario #4. Joins the scraped offer (8a) with live model output (`adj_probs` / `adj_ou_probs`) and the backtest rules (`stop_loss` / `late_drift` — both Δ-positive on the 2026-05-25 backtest) to produce a per-bet recommendation. Display-only; never auto-commits a cashout. Same feature-flag staging as 8a. Wait for ~2 weeks of real-offer snapshots in `open_bets_history.jsonl` so rule thresholds can be tuned on real bookmaker-haircut data instead of synthetic. |

## The data wait

The original blocker for the cashout phases: ≥50 settled bets per lane to score rule variants on real data rather than synthetic trajectories.

**Status (2026-05-25)**: Value (58 settled) and Model (164) cleared the threshold. Phase 7 (manual cashout endpoint + button) shipped. Conviction lane still starving at 1 settled bet — gate relaxed from 0.65 → 0.58 on 2026-05-25; ~30 bets/week expected once it kicks in. **Next checkpoint: 2026-06-08** — re-evaluate the relaxed conviction gate and re-run the weekly backtest harness.

### Weekly backtest re-run — must be local

`output/` (bets, predictions, verifications, live history) is gitignored and lives only on the local machine — a cloud `/schedule` agent would see an empty `output/`, so this re-run has to run on this machine. Run weekly:

```bash
cd ~/Documents/projects/sports_predictor && \
  source venv/bin/activate && \
  PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/run_backtest.py --paths 50
```

**Δ stability tracking**: a rule's directional sign should stay constant across weekly runs; a >50% magnitude shift OR sign flip is a "synthetic trajectories are misleading" signal — wait for more real `live_history_*.jsonl` data before tuning thresholds on it.

| Run date | Sample | `late_drift/value` | `stop_loss/value` | `lock_in_profit/value` |
| -------- | ------ | -----------------: | ----------------: | ---------------------: |
| 2026-05-18 baseline | n=10 (value only) | +21.80 | +16.83 | −5.81 |
| 2026-05-25 | n=264 (all synth) | +33.23 (+52% vs baseline) | +26.16 (+55%) | −22.63 (~4× more negative) |

Model-lane figures added at the 2026-05-25 run: `late_drift/model=+31.86`, `stop_loss/model=+42.95`, `lock_in_profit/model=−4.86`. **Direction stable**; magnitude shift >50% on every Value rule = synthetic-trajectory mistrust threshold engaged — real `live_history_*.jsonl` should drive future runs.

## Lane performance — live snapshot (2026-05-26)

Cumulative virtual P/L across all settled slips (`output/bets_*.json` + `output/history/`, timestamped backups excluded). ROI = pnl / total stake (VOID stake included in denominator, so it drags settled-only ROI slightly toward 0).

| Lane | Bets | W | L | Cashed | Void | Open | Stake € | P/L € | ROI |
| ---- | ---: | -: | -: | -----: | ---: | ---: | ------: | ----: | --: |
| **value** | 79 | 27 | 36 | 2 | 12 | 2 | 532.35 | −58.75 | **−11.0%** |
| **conviction** | 3 | 1 | 0 | 0 | 1 | 1 | 14.37 | +1.93 | +13.4% |
| **model** | 265 | 116 | 97 | 4 | 42 | 6 | 515.28 | +21.51 | **+4.2%** |
| TOTAL | 347 | 144 | 133 | | | | 1062.00 | −35.31 | −3.3% |

**Answer to "are conviction/model doing better than value?": yes — but read it carefully.**
- **Value −11.0%** (63 settled) is the only clearly-negative lane, and it tracks the calibrated value-lane backtest's bad-end expectation. This is the lane the EV-anti-predictivity verdict (below) is about — *no surgery fixes it; the model has no edge to EV-gate on.*
- **Model +4.2%** on 265 bets (213 settled) is the most meaningful live positive — but the odds-free/EV backtests established the model has **no durable market edge**, so this is most likely a favourable short run (10-day window), not a real signal. Don't extrapolate it; the honest prior is that the model lane also drifts toward −margin over a long sample. The broad-coverage flat-ish sizing just avoids the value lane's mistake of *sizing up on high-EV = overconfident* picks.
- **Conviction +13.4% on n=3 is statistically meaningless** — the 0.65→0.58 gate relaxation (2026-05-25) has barely started feeding it. Re-judge at the 2026-06-08 checkpoint once it has ~30+ bets.

**Bottom line:** the lane ordering (model > conviction > value) is consistent with the model-edge findings — the value lane's EV-sizing actively hurts, broad/flat coverage does least harm — but none of the three has a demonstrated positive edge, and the overall book is −3.3%. This is the expected outcome of betting an edgeless (market-matching) model, and it's the core go/no-go input for real betting (see the value-lane verdict under "Future model improvements").

## Real betting integration — Pamestoixima (DORMANT)

Target bookmaker: `pamestoixima.gr` (OPAP). Main account, **read-only operations only**.
End-goal deferred — revisit after dormant steps stay green for several weeks with no
anti-bot flags. Real bet placement, settlement, withdrawal are **explicitly out of scope**
until that re-evaluation.

Account confirmed to **not** use 2FA. If that changes, step 3 needs a manual cookie-bootstrap
revision before proceeding.

### Checklist

- [x] **1. Module skeleton** — `real_betting/` package with `Bookmaker` ABC, `credentials.py` (Keychain stubs + working `mask_username`), `session.py` (working `session_lock` context manager + `BrowserSession` stub), CLI entrypoint with four subcommands stubbed. `python -m real_betting --help` works (entrypoint: `bookmaker_cli.py`); subcommands return exit 1 with NEXT_STEPS pointers. `.env` + `*.session_state` added to `.gitignore`.
- [x] **2. Credentials wired** — `keyring==25.7.0` added to `requirements.txt`. `credentials.py` implements `set/get/has/delete_credentials` against the macOS Keychain. CLI subcommands `set-credentials` (prompts for username + password via `getpass`), `get-credentials` (masked output, never echoes password), `delete-credentials` (confirmation prompt). Round-trip verified against the real Keychain. `.env` + `*.session_state` were added to `.gitignore` in step 1. To store your Pamestoixima credentials: `python -m real_betting set-credentials pamestoixima` — macOS may prompt "Always Allow" the first time the Python process accesses the new keyring service.
- [x] **3. Pamestoixima login (headed mode)** — login confirmed end-to-end on 2026-05-18 against the live site (English UI at `/en`). `bookmakers/pamestoixima.py` drives cookie banner, fills credentials, submits, detects post-login state via `#logged-in-menu` / `.pli-logged-in` / `.pli-profile__avatar`. Headed Chromium, randomised 800–2500ms delays, single-session lockfile, no auth retry. **Balance scraping is best-effort**: confirmed working for €0,00 (deposit button visible) but the positive-balance selector is unverified — will update `BALANCE_SELECTORS_POSITIVE` after first deposit + re-login. **Promo / ad modal dismissal** added on 2026-05-20: `_dismiss_overlays()` runs ESC + 13 close-button selectors after the cookie banner, before searching for the Login button.
- [ ] **4. Session persistence** — save Playwright storage state to encrypted, gitignored file. Second run reuses cookie until expiry; falls back to fresh login on cookie rejection.
- [ ] **5. 6a — Locale handling** — switch UI language to English if Pamestoixima supports it; otherwise extend `entity_resolver.py` with a Greek↔English team-name normalisation table. Validate against today's `predictions_*.csv`.
- [ ] **6. 6b — Fixture discovery** — navigate today's football fixtures, scrape `{home, away, league, kickoff, fixture_url, market_ids}`. Output JSON to `output/real_betting/fixtures_<date>.json`.
- [ ] **7. 6c — Predictions ↔ Pamestoixima fixtures matching** — fuzzy-match against `predictions_*.csv`. Fetch current 1X2 + O/U 2.5 odds per matched fixture; compare to the odds we used in the prediction. Report on stdout + saved to `output/real_betting/match_report_<date>.json`. Acceptance: ≥80% fixture-match rate on a typical 20-match day.
- [⛔] **8. 6d — Headless mode validation — BLOCKED (2026-05-25)**. Tested headless: Pamestoixima returns a **300-char Akamai "Access Denied"** page (`errors.edgesuite.net`, `#18.<hex>` reference) — a network-edge bot block, served before any HTML renders. playwright-stealth defeats JS fingerprints but not Akamai's TLS/network/behavioural layer. **Headless is not achievable on the current stealth stack.** Headed mode stays mandatory for all Pamestoixima automation. Revisit only if a headless requirement becomes unavoidable, and then only via the CloakBrowser/patchright escalation below (C++ fingerprint patches). Evidence + full analysis in `PAMESTOIXIMA_NOTES.md` → "Headless is blocked by Akamai Bot Manager".

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

- [ ] **9. Bet placement** — design phase. New plan required. Conviction-lane only as starting point per earlier recommendation. **End-to-end plumbing was validated once on 2026-05-20** (single €10 real bet placed on SC Freiburg vs Aston Villa, O/U 2.5 Over, via a throwaway `real_betting/dryrun_freiburg_villa.py` script). The "read-only operations only" policy above is **unchanged at the doc level** — that run was a per-run `EXECUTE_PLACE_BET=True` override for verification, not a policy relaxation. Working selectors, DOM structures, and anti-patterns from that exploration are preserved in `real_betting/PAMESTOIXIMA_NOTES.md` and should be the starting point when Phase 9 is properly designed.
- [ ] **10. Settlement reconciliation** — match Pamestoixima's settled-bet history against our `bets_*.json`.
- [ ] **11. Withdrawal flow** — **never automated**. Manual only, by design.

## Enriched live stats & adjuster v2 (proposal)

Question: more granular live stats (xGOT, big chances, touches in opp box, shots inside/outside box, woodwork, etc.) should improve cashout decisions. Approach is phased to avoid over-fitting heuristics on a small sample.

| Step | Effort | Status | Notes |
| ---- | ------ | ------ | ----- |
| Scrape all available extended stats; persist to JSONL | small | ✅ done | `flashscore_spider.py` now extracts xgot, big_chances, shots_inside_box, shots_outside_box, woodwork, touches_opp_box, saves, yellow_cards, **red_cards** (added 2026-05-26), fouls. Misses leave the key absent (no crash). Every refresh appends to `live_history_<date>.jsonl`, future-proofing the backtest harness regardless of which stats we currently use in rules. |
| Display the most informative stats in dashboard | small | ✅ done | Stats table on each live row shows xG / **xGOT** / Poss / **Tch** (touches in opp box). BigCh and total Shots were briefly displayed then dropped as redundant with xG/xGOT. Headers have full-name tooltips. |
| Browser-driven auto-refresh while tab open | small | ✅ done | "Auto 5m" checkbox in the Live header. Polls `/football/refresh_live` every 5 min while `document.visibilityState === 'visible'`. Pauses when tab is hidden or minimised. State persists via `localStorage`. Skips when a previous run is still in `'running'` state (re-checks `/status` before each trigger). No server-side daemon. |
| Wire new stats into LiveAdjuster heuristics | medium | ⏸ hold (one exception shipped) | Don't add new handcrafted *tuned* layers until the backtest harness has ≥50 settled bets per lane to score variants against — premature weight-tuning on a 10-bet sample just over-fits. **Exception (2026-05-26): red cards.** `LiveAdjuster._apply_red_card_modifier` shifts 1X2 prob off the man-down team → opponent (0.65) + draw (0.35), scaled by remaining time, capped at `MAX_RED_SHIFT`. Justified as a *structural* signal (a sending-off is a large, unambiguous game-state change the pre-match model can't see), NOT a tuned weight — though the exact magnitudes (`WEIGHT_RED_CARD=0.18`, the 65/35 split) are still defaults to calibrate later. 🟥 badge on live rows. **O/U red-card effect deliberately NOT added** — its direction is state-dependent and the total-goals impact is small/ambiguous, and the xG-pace-driven O/U adjuster already absorbs most of it; left to the learned model below. |
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

- [x] **D0 — Draw model investigation + 2-stage removal.** Done 2026-05-19. Original "0% recall on draws" finding turned out to be a **metric-reporting artifact**: predict_proba mean = 0.260 vs actual 0.254 (1.02× — well calibrated), but max output ~0.45 so threshold-0.5 metrics show 0% recall. Production used the raw probability via predict_proba, so the model was contributing reasonable values — just not discriminative ones (rec@0.3 = 0.23 — basically outputs base rate for every match). A sweep of `scale_pos_weight ∈ {1.0, 1.3, 1.5, 1.7, 2.0, 2.5, 3.0}` confirmed the textbook tradeoff: higher weight → recall up but calibration ratio also up (3.0 gives 1.96× overcalibration). Class weighting alone can't fix this.
   The mechanical role of the draw model in production was a **50/50 average with the multi-class draw probability**, acting as an implicit *regularizer toward the global base rate*. With Phase C4's Platt calibration handling per-league bias correctly, this implicit regularization is redundant (and likely mildly counterproductive — pulling already-calibrated estimates toward a global mean). **Chosen fix**: removed the 2-stage averaging entirely; `predict_matches.py` now uses the multi-class output directly, Platt-calibrated. ~10 lines deleted from `predict()`, ~5 lines removed from `__init__`. The trained draw model file stays on disk for backward compatibility; reactivation is a one-line revert. `train_draw` got an instrumentation upgrade during the investigation (metrics at thresholds 0.30/0.40/0.50, calibration-ratio warnings) so this kind of misdiagnosis doesn't recur.
   Real fix paths for actually IMPROVING draw prediction (deferred — no quick win): richer features (style mismatch, mid-table congestion, late-season motivation, recent per-team draw rate); ensemble of draw-specialized models trained on subsets; or simply accept that draws are coin-flippy and don't try to predict them specifically.
- [x] **D1 — Re-run `tune_model.py`.** Done 2026-05-19. Feature list synced with the trainer (was missing `A_form_sa` plus the entire ppg/strength block — 9 features). New hyperparameters: 1X2 `n_estimators 470 → 827`, `reg_lambda 0.01 → 1.0`, `min_child_weight 3 → 7`; O/U `n_estimators 234 → 386`, regularisation swapped L2-heavy → L1-heavy. Old params backed up as `models/best_params_*.json.bak_20260519`. **Cosmetic warning during run** (`"Parameters: { 'enable_categorical' } are not used"`): looks scary but is just XGBoost's C++ booster complaining about an unrecognised parameter. The categorical-ness is encoded in three other places (DMatrix `enable_categorical=True`, sklearn wrapper `enable_categorical=True`, pandas `astype('category')`), and the booster's `feature_types` correctly reports `'c'` for `league_cat`. Tuning was correct; only the log noise is wrong. Future cleanup: filter the warning in tune_model.py or pop `enable_categorical` out of `best_params` before passing to `xgb.cv`. **Deployment**: model must be retrained for new params to take effect; run `./bin/retrain_pipeline.sh` to also refit calibrators against the new model (otherwise calibrators are stale by construction).
- [ ] **D2 — Feature engineering pass.** Three tested-and-rolled-back so far, all flat (see `FEATURE_ENGINEERING_IDEAS.md` for full results). **Emerging finding: cheap features the betting market already prices give XGBoost no lift** — the model leans on the B365 implied-probability features (`IP_H/D/A`), which already encode form, rest, congestion, etc.
  - ~~**§1.2 Recency-weighted form**~~ — tested 2026-05-25, no lift (weighted-mean Δ ≈ −0.0000 across 11 685 matches). Collinear with ELO + PPG. Rolled back.
  - ~~**§1.3 Opponent-adjusted form**~~ — rolled back 2026-05-20.
  - ~~**§1.5 Rest days / fixture congestion**~~ — tested 2026-05-26, no lift globally (+0.0001) **or on the congested subsets it was supposed to help** (|rest_diff|≥3: +0.0002; congested teams: −0.0002 — all noise). Market already prices the fixture calendar. Rolled back.
  - **Reconsider the remaining Tier-1 candidates before spending more days**: §1.4 promoted-team, §1.6 H2H, §1.7 goal-difference, manager-change are ALL things the market prices → likely similarly flat. The real headroom is (a) signals priced *inefficiently* (key-player injuries, lineup leaks — overlaps D4) or (b) reducing odds-dependence so the model learns fundamentals (overlaps D3). Don't grind more date/form features expecting Brier wins.

### Architectural pivots — only if cheap wins are exhausted

These are the two real headroom directions after the D2 finding (cheap, market-priced tabular features don't move Brier — the model already rides the B365 implied probs). They attack the problem from opposite ends: **D3 changes the model on the data we already have; D4 adds data the market prices inefficiently and keeps the model.** Detailed below so we can pick deliberately.

#### D3 — Bivariate Poisson / Dixon-Coles (new model, existing data)

**What**: model goals directly instead of classifying outcomes. Per team: an attack strength α and defence strength β; a global home advantage γ. Expected goals λ_home = exp(α_home − β_away + γ), λ_away = exp(α_away − β_home); scoreline ~ Poisson(λ_home) × Poisson(λ_away) with the **Dixon-Coles low-score correction** (τ for the 0-0/1-0/0-1/1-1 dependence) and **exponential time-decay** (ξ) down-weighting old matches. Per-league MLE fit via `scipy.optimize`.

**What it produces**: a full joint scoreline distribution → 1X2, O/U *any* line, BTTS, correct-score, all **mutually consistent** by construction. Directly fixes the "1X2 says one thing, O/U another" inconsistency that's the #3 trigger in the decision rule below.

**Fit with current stack (why this is the lower-friction pivot)**:
- **Data**: needs only `FTHG`, `FTAG`, `date`, team names, `league` — every one already provided by `data_loader.load_historical_data()` from the `MatchHistory/` corpus. **Zero new scraping or data-layer work.** This is the basis for preferring D3 with the current scraper/loader.
- **Calibration**: per-league fitting is auto-calibrated by construction → likely makes the C4 Platt layer redundant for the DC model (keep it only as a thin safety / drop it).
- **predict_matches**: add a DC predictor path that emits the same `Home/Draw/Away/Over/Under %` columns from the scoreline matrix, so `auto_wager` + the betting flow consume it unchanged.
- **standings / ELO**: ELO becomes optional — DC's α/β strengths replace it as the team-rating mechanism (and they're interpretable: per-league attack/defence numbers that mean something).

**Catch / the real work**: native DC is purely parametric on goals — it **cannot use ELO / xG / shots / the form features** the XGBoost model leans on. A pure DC v1 therefore *trades* those signals for goal-distribution consistency; it might not beat the current model on raw 1X2 Brier. To keep the rich features you need the **Karlis-Ntzoufras hybrid**: XGBoost (or a GLM) predicts the Poisson *rates* λ from the full feature set, then the DC correction + scoreline matrix sits on top. That's the version that could genuinely beat the current stack, and it's the bulk of the effort.

**Effort**: ~3–4 weeks for a pure-DC v1 (fit + per-league params + predictor + validation harness). Hybrid adds ~1–2 weeks. **Decision sub-question to settle first**: is the goal pure-DC (consistency, interpretability, drop calibration) or the hybrid (consistency *and* keep xG/form signal)? They're different scopes.

**Risks**: thin leagues (N<~300) give unstable α/β — same low-data problem as the Platt calibrators; needs a pooled/parent-tier fallback. Time-decay ξ is a hyperparam to tune. A pure-DC model could regress 1X2 Brier vs the current feature-rich XGBoost — validate on the OOF harness before deploying, same gate as the D2 features.

#### D4 — More data, not a different model (new data, existing model)

**What**: ingest signals the betting market prices *inefficiently* (the D2 lesson: market-priced signals are dead, so chase the ones the market is slow/bad at). The XGBoost architecture stays; these become new features.
- **Player availability / confirmed lineups** — highest value. A key player out is huge and the market reacts, but lineup confirmations (~1 h pre-kickoff) and leaks create windows. Flashscore *has* lineups but the current spider doesn't scrape them.
- **Injury / suspension feeds** — separate source, drives availability.
- **News sentiment** (manager statements, team news) — needs an NLP pipeline + a news source.
- **Bookmaker line movement** as a derived feature — drift before kickoff carries sharp-money signal (partially self-defeating since it's market-derived, but the *movement* is information the static odds snapshot loses).

**Fit with current stack (why it's the higher-friction pivot)**:
- **Data**: NONE of this is in the `MatchHistory/` corpus or the current Flashscore spider. Each signal is a **new scraper + new data-layer columns + ongoing maintenance** (feeds break, sources change ToS). `data_loader` and `feature_engineering` both need real extension.
- **Latency mismatch**: predictions run the night before for next-day fixtures (per the operational cadence), but lineups confirm ~1 h pre-kickoff — so lineup features only help a *re-run-near-kickoff* prediction mode we don't have yet. Injuries/suspensions are known earlier and fit the night-before cadence better.

**Effort**: each signal is its own data-acquisition project — weeks each, plus permanent maintenance. Lineups are the highest-value, but the latency mismatch means they need a new "late refresh" prediction path. Injury/suspension data is the most cadence-compatible starting point.

**Risks**: data reliability + maintenance burden (the expensive part is keeping feeds alive, not the modelling); legal/ToS for new scrape sources; latency (above).

### Things explicitly NOT worth doing pre-emptively

- **Ensemble stacking with XGBoost + LightGBM + CatBoost.** All three are gradient boosting variants — too correlated to give diversity gains. ~2 weeks of work for sub-1% Brier improvement.
- **Tabular neural networks** (TabNet, FT-Transformer). Decade of evidence: GBT > NN on tabular unless you're combining with non-tabular features.
- **Generic ensembling / model stacking** without genuine model-family diversity. Diminishing returns.

### Decision rule: D3 vs D4

D2 is effectively exhausted for cheap tabular features (3 rolled back; market-priced signals don't move Brier). So the next model investment is D3 or D4. Lean **D3** with the current stack — it reuses `data_loader` + the `MatchHistory/` corpus with **no new data acquisition**, whereas D4 is a data-engineering project (new scrapers, new pipelines, ongoing feed maintenance) before any modelling. (Operator note 2026-05-26: D3 preferred given current scraper/loader coverage.)

Pull **D3** off the shelf when:
1. D1 + D2 done (✅ — D2 cheap features exhausted), AND
2. 1X2 Brier stable across retrains with no easy headroom (✅ — confirmed by the D2 nulls), AND
3. Per-market inconsistency (1X2 vs O/U disagreeing on the same match) is an observed problem — **CHECKED 2026-05-26: NOT met.** Ran the consistency check (`scripts/run_dc_consistency_check.py`, `ml_project/dixon_coles/`) over 204 matches: the rigorous metric — can a *single* bivariate-Poisson reproduce BOTH the 1X2 and O/U heads? — gives a joint-fit residual of **mean 0.011 / median 0.009 RMS, only 2.5% of matches >3pp, 0% >5pp**. The heads are already consistent; the O/U head supplies the total-goals info that 1X2 underdetermines. (A naive 1X2-only fit showed a scary 9pp implied-Over gap, but that's an artifact of 1X2 weakly identifying the total — not real inconsistency.) **So D3's headline justification does not hold on current data.**

**Current verdict (2026-05-26): D3 deferred — weak on all three axes.**
- *Consistency*: refuted by the joint-fit check (above).
- *Odds-independence*: a real property of pure DC (fit on goals, no B365), and the better motivation — BUT the odds-dependence ablation (below) shows the current XGBoost is **already mostly odds-independent** (removing all `B365*` + `IP_*` features costs only +1.6% Brier / −1.3pp acc). So DC's independence buys little vs the current model, AND an odds-free estimate is obtainable for ~0 effort by just dropping those 5 features from the existing XGBoost — no need for a 3–4 wk DC build.
- *Brier*: pure DC discards the fundamental features (ELO/xG/form) the XGBoost uses, so it would likely be *worse* on raw accuracy.

Remaining DC-only motivations (interpretable α/β strengths, BTTS/correct-score markets we don't bet) don't justify the cost. The DC scoreline + check tooling is kept under `ml_project/dixon_coles/` for a future re-check.

**Odds-dependence ablation (2026-05-26, informs both D2 and D3):** OOF 1X2 with vs without the 5 odds-derived features — Brier 0.6013 → 0.6108 (+1.6%), acc 50.5% → 49.2%, logloss 1.0050 → 1.0192. **The model is fundamentals-driven, not an odds-echo.** Corrects the earlier D2 framing: the recency-form / rest-days nulls are collinearity with ELO + season-PPG + form (fundamentals), not "the market already prices it." The odds add only a thin polish.

**Odds-free variant + model-vs-market backtest — DONE 2026-05-26, REFUTED.** Ran it (`scripts/run_odds_free_backtest.py`, output `output/odds_free/`): OOF 1X2 for with-odds vs odds-free models, flat-stake EV backtest on B365 odds over 14 027 matches. Results:
- Both models LOSE: with-odds ROI −9.5%→−13.6%, odds-free −9.6%→−14.6% across EV thresholds 0.00→0.20. Market is efficient.
- **Odds-free is *worse*, not better** — independence didn't surface value; its line-disagreements are noise. Model-vs-market hypothesis refuted.
- **Higher EV threshold → worse ROI for both** — the model's "high EV" marks its own overconfidence, not value. The EV ranking is *anti-predictive* at the top end.

**This closes the D3 / model-edge line entirely** (consistency refuted, odds-independence cheaply replicable AND valueless, raw model has no market edge). No more model-accuracy or model-independence work is worth doing for betting edge against this market with this data.

**Spillover finding for the betting side (more important than D3):** the EV-anti-predictivity is a candidate mechanism for the **Value lane's negative ROI**. Followed up with a calibrated, lane-replicating backtest (`scripts/run_value_lane_backtest.py`, output `output/odds_free/value_lane_backtest.json`, 14 027 matches OOF, 1X2):
- **Flat EV sweep stays anti-predictive after Platt calibration** — higher EV threshold → worse ROI (cal: −9.6% @ EV≥0 → −14.2% @ EV≥0.20). High model-EV = overconfidence, not value, calibrated or not.
- **Value-lane replica (deployed gate + Option-B sizing): raw −10.4% → calibrated −3.9% ROI.** Calibration cuts bets 5,092→1,392 (shrinks the overconfident probs that clear the gate) and roughly halves the loss. Production uses calibrated probs, so the deployed lane's true expectation is **~−4%, not −10%**. Live value lane is at −11.0% (63 settled; see "Lane performance" snapshot above) — tracking the raw end, likely because the post-Platt heuristic adjuster (not replicated in the backtest) re-introduces some overconfidence, plus O/U bets and small-sample variance.

**Verdict (2026-05-26): the value lane needs NO surgery and CANNOT be fixed into profit.** −3.9% ≈ the bookmaker margin — the lane is paying the vig, not capturing edge. Its negativity is the direct consequence of the model having no edge against an efficient market (established across the D2→D3→odds-free arc), not a lane misconfiguration. No tweak (harder cap, drop EV sizing, invert) makes an edgeless model profitable; calibration is the one thing that materially helps and it's already deployed.

**Go/no-go consequence for real betting:** with no demonstrated market edge, real-money wagering on these picks would lose ~the margin (3–5%). This argues against taking the real-betting integration live on *value* grounds — independent of how complete the plumbing is. The only paths to a real edge remain D4 (inefficiently-priced new data) or accepting the lanes as a paper/simulation exercise. Caveats on this backtest: 1X2 only (O/U markets can be softer — untested); calibration mildly optimistic (fit on OOF of same data); heuristic adjuster not replicated.

First scoping decision once D3 is greenlit: **pure-DC** (consistency + interpretability, drops xG/form signal, may not beat current 1X2 Brier) vs **Karlis-Ntzoufras hybrid** (keeps the feature signal, more work). Validate either on the existing OOF Brier harness before deploying.

Reach for **D4** instead only if a specific inefficiently-priced signal (esp. injuries/suspensions, which fit the night-before cadence) looks high-value enough to justify standing up a new scraper + data pipeline. Lineups are higher-value but need a new near-kickoff prediction path (latency mismatch).

Until one is picked, keep XGBoost + Platt calibration + ev_cap_value as the deployed stack.

## Future analysis ideas (not yet scoped)

- **"Place bet now?" shortcut on live rows** — when a live match has no open bet, show a one-click action that takes you to `/football/auto_wager` (or a future bet-placement modal) pre-filtered to that match. Useful for value-discovery on in-progress games where the score state has shifted the EV. Caveat: couples live analytical view with virtual betting action; needs design before building. Revisit when /auto_wager UI is generalised enough to accept a per-match filter.

## Open / deferred work (smaller items)

- **Calibration spot-check on real data** — once we have ~3 days of `live_history`, write a quick script that runs `LiveAdjuster` on real snapshots from games we know the outcome of, to see if "aggressive prob swings near full-time" survives real-game noise or was a synthetic-trajectory artifact.
- **Backtest report polish** — `bets_by_type` breakdown (1X2 vs O/U), sortable JSON output.
- **OS-level integration** — `live_data.json` currently overwritten each refresh; consider keeping last N snapshots in memory for the UI to show "trend" arrows.

### Real-betting batch input ergonomics

**UI front-end already shipped (2026-05-25)**: the Generate Slip preview on `/football/betting` has a per-bet **"Live" checkbox**; ticked bets persist `mark_for_real: true` onto `bets_<date>.json` when placed and show a ⚡ live badge in the slip history. A **dormant `/football/place_real_bets` route** receives the marked bets and reports them but places nothing real — it's the designated dispatch point for the future real-betting flow (would call into `real_betting/` behind a confirmation modal + stake caps + `EXECUTE_*` gating). So the "which bets go live" selection mechanism + the route scaffold exist now; only the actual placement backend is unwired.

The batch placement script (`real_betting/dryrun_batch_placement.py`) currently reads `BETS` as a Python literal — to place a different set of bets the operator edits the source file. URL discovery is dynamic (see scenario #5 + step 6b), but the bet *list* isn't. Two queued improvements, ordered by readiness:

- **Near-term — interactive prompt input**. Take team-name pairs (home/away/market/selection/odds/stake) interactively at the command line (or via a small JSON file passed with `--bets <file>`). Friendlier than editing source, doesn't require trusting the model to drive placement, keeps the supervised one-shot character of the current script. Bridge step until the slip-driven path below is appropriate. ~30-45 min of work.
- **Final form — `--from-slip YYYY-MM-DD`** (gated). Reads OPEN bets directly from `output/bets_<date>.json` (the virtual-betting slip that `/football/place_bets` already writes). Auto-wager → place-bets → real placement becomes a single pipeline; per-bet lane / EV / Conf metadata flows through unchanged. **Not appropriate yet** — needs the model's track record to be stable enough to trust each bet's recommendation as a real-money commit. Revisit after the conviction lane has accumulated meaningful settled-bet history at the 0.58 gate (target ~2026-06-08 checkpoint), and the broader value/model lanes are in profit on multi-week ROI. Will also need an opt-in filter (e.g., `--lane=conviction`, `--max-bets=N`) because a typical day's auto_wager output is 5–15 bets across three lanes — placing them all unfiltered would be a much larger commit than current single-bet experiments.

## Bugs / fixes queue

_(empty — last cleared 2026-05-25)_

## Known limitations of the current backtest

- **Synthetic trajectories** are crude: linear xG accumulation, no in-match drama (red cards, momentum swings). Use harness for *directional* signals only, not for tuning rule thresholds to the third decimal.
- **No counterfactual stake redeployment** — when a rule cashes out at min 60, the freed bankroll could in principle be reused. The harness reports a lower bound; actual edge is somewhat higher.
- **Adjuster bias near full-time** — late goals produce very large prob swings (home 0.32 → 0.84 at min 73). Rules built on this may over-fire after late equalisers. Calibrate against real data before trusting.

## How to update this doc

When a phase completes:
1. Flip its row to ✅ done with a one-line summary.
2. Add any newly-discovered work to "Open / deferred work."
3. Don't grow the doc into a changelog — git history is the changelog. This file is the *forward-looking* roadmap.
