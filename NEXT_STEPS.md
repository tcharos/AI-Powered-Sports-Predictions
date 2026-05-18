# Next Steps — Cashout Roadmap

Living document. Keep it short. Update statuses inline as phases complete.

## Phase status

| #   | Phase                              | Status         | Notes |
| --- | ---------------------------------- | -------------- | ----- |
| 1   | Live-history persistence + on-demand | ✅ done        | `output/live_history_<date>.jsonl` append on every Refresh Live Snapshot. Daemon loop removed. |
| 2   | Backtest harness (engine + CLI)    | ✅ done        | `ml_project/backtest/` + `scripts/run_backtest.py`. Self-validation against stored slip P/L passes. |
| 5   | O/U adjuster                       | ✅ done (pulled forward) | `LiveAdjuster.adjust_ou_probabilities()` — Poisson goal model blended with pre-match. Harness now evaluates O/U bets. |
| 3   | Bet status migration (CASHED_OUT)  | ⏸ deferred     | Cheap (~30 min). Defer until just before Phase 7 — nothing uses it yet. |
| 6   | Bets↔live UI linkage (display only) | ⚙ partial      | Dashboard live rows show a per-match bet column: lane badge, type/selection/odds, stake, fair-value cashout (1X2 only — O/U adj probs not yet persisted to snapshots), state badge (🟢 lock-in / 🔴 stop-loss / 🟡 hold) from the same rule thresholds the backtest harness uses. No button, no auto-action. Same template fragment isn't shared with the `/football/live_analysis` standalone page yet. |
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

### Eventually (placeholder — DO NOT START)

When/if we revisit the end-goal decision:

- [ ] **9. Bet placement** — design phase. New plan required. Conviction-lane only as starting point per earlier recommendation.
- [ ] **10. Settlement reconciliation** — match Pamestoixima's settled-bet history against our `bets_*.json`.
- [ ] **11. Withdrawal flow** — **never automated**. Manual only, by design.

## Future analysis ideas (not yet scoped)

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
