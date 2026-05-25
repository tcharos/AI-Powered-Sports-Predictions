# Pamestoixima — End-to-End Test Case Scenarios

Curated list of behaviours we want validated against a real (logged-in)
Pamestoixima session *before* the read-only policy is lifted and the
real-betting integration is allowed to drive bets autonomously.

Each scenario is anchored to a one-shot script under `real_betting/`,
the exact match used during validation, the stable selectors that worked,
and the safety gate that controls when real money moves. New scenarios
get appended below as they are designed.

Conventions used throughout:
- **Status** — `PASSED <date>` once executed end-to-end against a real
  account, `OPEN` otherwise.
- **Safety gate** — the module-level constant (or flag) the operator
  must explicitly flip to `True` to commit real money. Default is always
  the safe value. Re-set to safe after the test.
- **Selectors** — Pamestoixima-namespaced stable classes only. Never the
  auto-generated `MuiButton-*` / `css-<hash>` classes.

---

## 1. Place a new bet (single, pre-match)

**Goal**: drive the full pre-match betting flow — login → navigate to
fixture → expand market → select outcome → enter stake → click
*Place Bet* — and verify the bet appears in the slip-cleared / balance-
debited terminal state.

**Match used**: SC Freiburg vs Aston Villa, UEFA Europa League,
2026-05-20.
- Market: Total Goals Over/Under, **Over 2.5** @ 1.92.
- Stake: **€10.00** (hard-capped at module load).
- Outcome: placed successfully. Balance debited by €10. Slip cleared.

**Script**: `real_betting/dryrun_freiburg_villa.py`
**CLI**: `python -m real_betting dry-run-freiburg-villa`

**Safety gate**: `EXECUTE_PLACE_BET` (module-level constant). Defaults
to safe; the script also asserts `STAKE == MAX_STAKE == 10.0` at load
time and refuses to run if anyone changes either constant.

**Critical selectors & DOM landmarks**:
- Direct match URL pattern: `/en/football/<league-slug>/<teams-slug>/<match-id>`
  (the `<match-id>` is the canonical Pamestoixima fixture ID, stable
  for the lifetime of the fixture). Example used:
  `/en/football/uefa-europa-league/sc-freiburg-v-aston-villa/10889590`.
- Market section toggle (collapsible accordion):
  `button.event-page-market-box-collapseBtn:has-text("Total Goals Over/Under")`
- Collapse-state probe: read the `class` of
  `.MuiCollapse-root:has(.market-box-root[class*="TOTAL_GOALS_OVER"])` —
  `MuiCollapse-hidden` = closed, `MuiCollapse-entered` = open.
- Over 2.5 outcome button (must scope at market-box level to disambiguate
  from Over 0.5 / 1.5 / 3.5 — each line is a sibling `.market-box-root`):
  ```
  .market-box-root[class*="TOTAL_GOALS_OVER"]:has(.oddLine:has-text("2.5"))
   button[name="Over"]
  ```
- Slip counter (already visible in sidebar — do NOT click):
  `.slip-button-root:has(span:has-text("Betslip")) span:has-text("(")`
- Stake input: `[class*="stake" i] input`. `input_value()` returns the
  rendered string like `"10€"` — strip the currency symbol before parsing.
- Place-bet button: `button:has-text("Place Bet"):not([disabled])`,
  with Greek `Στοιχημάτισε` as fallback and a `[class*="placeBet" i]`
  class-based fallback. Also check `aria-disabled="true"` separately —
  MUI uses both attributes.
- Success signal: the slip counter falls back to `(0)` and
  `.empty-message-betslipEmpty` becomes visible. Balance reads as
  `pre - stake`.

**Audit artefact**: `placement_record.json` written into
`output/real_betting/dryrun_freiburg_villa_<ts>/` (timestamp, match,
stake, balance pre/post, slip-cleared boolean, dump dir).

**Key gotchas discovered**:
- Do NOT click the match-row anchor on the coupon page — Pamestoixima
  nests the Match-Result odds buttons *inside* the navigation anchor;
  the inner button captures the click and adds an unintended bet.
  Extract the `href` and `page.goto()` instead.
- `name="Over 2.5"` does NOT exist on the outcome button; it's
  `name="Over"` with the line value in a child `.oddLine` span carrying
  a leading space (`" 2.5"`). Filter at the parent `.market-box-root`
  level with `:has(.oddLine:has-text("2.5"))`.
- Broad `[class*="betslip" i]` "open the slip" selectors are dangerous:
  the slip is already visible in a sidebar, and clicking into a betslip-
  class element can bubble back to the just-selected outcome button and
  deselect it. The slip-verification step must be read-only.

**Status**: **PASSED 2026-05-20.**

---

## 2. Cash out an open bet

**Goal**: locate an open bet on the *My Bets* page, read the current
cashout offer, and drive the bookmaker's two-click commit flow to
actually cash out the bet. Verify the bet leaves the Open list (button
removed from DOM) and the balance is credited.

**Match used**: FC Machida Zelvia vs Urawa Red Diamonds, J League,
2026-05-22.
- Open bet on Pamestoixima: O/U-style single (placed manually via the
  website, not via the scripted place-bet flow).
- Cashout offer: **€1.47** at commit time.
- Outcome: cashed out successfully. Button disappeared from the bet
  row within the success-wait window.

**Script**: `real_betting/dryrun_cashout_discovery.py`
**CLI**: `python -m real_betting dry-run-cashout-discovery`

**Safety gate**: `EXECUTE_CASHOUT` (module-level constant; default
`False`). When `False`, the script runs in *discovery* mode — clicks
the cashout button once to surface the "Confirm cash out" state, reads
the post-click text, then **destroys every `.full-cashout-root` from
the DOM** via `page.evaluate(... b.remove() ...)` and navigates to the
homepage. A second click is structurally impossible after defuse.

Additional gate: `MAX_CASHOUT_EUR` (default `2.0`). The script parses
the cashout amount out of the button text and refuses to commit if the
amount exceeds the cap. Belt-and-braces against a runaway cashout if
the offer drifts unexpectedly between discovery and commit.

**Critical selectors & DOM landmarks**:
- My Bets entry: `span:text-is("My Bets")` (EN), `span:has-text("Δελτία μου")`
  (Greek fallback). Clicking it renders the Open bets list directly
  below — no separate sub-navigation.
- Open / Active tab (best-effort): `button:has-text("Open"):not([disabled])`
  with Greek `Ενεργά` fallback.
- Open-bets list wrapper: `<ul class="my-bets-container-root">`.
- Per-bet row: `<li id="my-bets-O-<uuid>">`. The `-O-` segment marks
  the bet as **Open**; settled bets use a different prefix. The UUID
  is the canonical per-bet identifier and is worth recording for
  future settlement-reconciliation work.
- **Cashout button** — Pamestoixima-namespaced stable class:
  `button.full-cashout-root` (the surrounding `MuiButton-*` / `css-1mz3gsl`
  classes are auto-generated noise, must NOT be selected on).
- Container heuristic that worked: smallest DOM ancestor whose
  `innerText` contains both team-name tokens **and** which contains a
  `.full-cashout-root` descendant. Walking from each text match toward
  the page root and picking the tightest valid wrapper lands on the
  correct `<li>` row. The earlier heuristic — "smallest with both
  tokens" — picked the inline `.eventName` div, which sits as a sibling
  to (not parent of) the cashout button.
- **Confirm-state class**: after the first click, the same button
  gains the `confirmation` class. Full post-click selector:
  `button.full-cashout-root.confirmation`. Text changes from
  `"Cash Out\n€X.XX"` → `"Confirm Cash Out\n€X.XX"` (value unchanged).
- Success signal after the commit click: the `.full-cashout-root`
  button is removed from the bet row entirely. Tossed in as fallbacks
  are regex toast matchers (`(?i)cash.?out.*(successful|complete|paid|placed)`,
  Greek `εξαργ.*επιτυχ|ολοκλ`), but the button disappearance was the
  signal that actually fired in this validation.

**Audit artefact**: `cashout_placement_record.json` written into
`output/real_betting/cashout_discovery_<ts>/` (timestamp, match,
cashout amount, pre/post balance, success-signal boolean, dump dir).
Also produced: `cashout_discovery_report.json` with the full per-step
selector / classes / matched-text trail useful for post-mortem.

**Key gotchas discovered**:
- **No confirmation modal**. The same `.full-cashout-root` button
  morphs in-place — colour changes (yellow background), text becomes
  "Confirm Cash Out", and the `confirmation` class is added. A second
  click on that same button **commits the cashout**. Any abstraction
  that assumes a dialog/modal step will silently mis-handle this.
- The auto-generated `css-1mz3gsl` class on the cashout button is
  unstable across deploys. Always select on `.full-cashout-root`.
- DOM-defuse via `document.querySelectorAll('.full-cashout-root').forEach(b => b.remove())`
  is the cheapest way to make a second click structurally impossible
  during discovery — Playwright can't click a node that isn't in the
  DOM. Belt-and-braces: also navigate to a safe URL afterward.

**Status**: **PASSED 2026-05-22.**

---

## 3. Bulk-read cashout offers across every open bet, wired into live analysis

**Goal**: enumerate **every** OPEN bet on the *My Bets* page, pull each
one's current cashout value in a single pass, and **replace the
synthetic `fair_cashout` shown on the dashboard with the real
bookmaker offer**. The "Est. cashout €X.XX" pill rendered next to
every OPEN bet today is computed inline as
`stake × odds × adj_prob × 0.95` — once this scenario lands, that
estimate gives way to the actual Pamestoixima offer for any bet we
managed to match on the *My Bets* page. If we can't match (bet not on
Pamestoixima, or market paused in-play), the synthetic estimate stays
as a fallback so the UI never goes blank.

This is the prerequisite for scenario #4 — without a real-offer feed
into `live_data.json` we can't drive an automated cash/hold decision.

**Match used**: TBD. Validate against any account state with ≥2
concurrent open bets. The Machida vs Urawa bet from scenario #2 is
gone (cashed out 2026-05-22) — re-validate with a fresh batch.

**Script**: TBD — `real_betting/read_open_bets.py` (new). CLI
subcommand `read-open-bets` on `bookmaker_cli.py`.

**Safety gate**: none required. This is a **pure-read** scenario — no
clicks on `.full-cashout-root` at all (so no first-click confirm-state
mutation, no risk of accidentally committing a cashout). The
`FORBIDDEN_CLICK_LABELS` net is still wired but won't fire because the
script never clicks a cashout-related button.

**Critical selectors & DOM landmarks** (reuse scenario #2 vocabulary):
- Iterate `ul.my-bets-container-root > li[id^="my-bets-O-"]` — each is
  one OPEN bet row.
- Per-row extraction:
  - Bet UUID: parse from `id="my-bets-O-<uuid>"`.
  - Match: text under `.selectionName` (away team) + the row's
    fixture link `href` (`/en/live/football/<league>/<teams>/<match-id>`)
    — pull both team slugs and the `<match-id>` out of the URL.
  - Market + selection: `.bet-details-container-row1`'s text segments;
    exact subselectors to be discovered during validation (same
    `bet-details-container-*` namespaced classes as scenario #2 used
    for ancestor-finding).
  - Stake / odds: `.bet-details-container-*` numerics — TBD.
  - Cashout offer: `li[id^="my-bets-O-"] button.full-cashout-root`
    `inner_text()`, parsed via the same `r'[€]?\s*([\d.,]+)'` pattern
    as scenario #2's `_execute_confirm_click` (Greek decimal: `,` →
    `.`). If the button is absent / disabled, mark the offer as
    `paused` (event in-play, offer temporarily withdrawn).
- Settled bets are under a different `<li>` prefix (likely `my-bets-S-`
  or similar) — confirm during validation and EXCLUDE them so we only
  read still-actionable bets.

**Behavioural notes to confirm during validation**:
- Whether the My Bets list is virtualised. Scenario #2's bet was the
  only Open bet on the account, so the list never scrolled. A populated
  account may lazy-render rows; if so, the script needs a scroll loop
  (same `mouse.wheel` + `keyboard.press('PageDown')` + `window.scrollBy`
  triple-poke that the Freiburg dryrun's Step 3 uses for the match-page
  markets list).
- Whether the cashout value updates without a page refresh. Likely
  driven by the same WebSocket feed as the odds — scenario #2's value
  stayed constant across the ~3s discovery window, but a longer hold
  would tell us. Mode of refresh decides the polling cadence in
  scenario #4.

**Output** (three concentric layers; everything downstream chains off
the first one):
- Source of truth: `output/real_betting/open_bets_snapshot.json` —
  one record per OPEN bet with the fields above plus `ts` (ISO UTC).
  Overwritten on every read (latest-wins). Last-known-good cached
  here so the dashboard can read it without firing a fresh scrape.
- Append-only JSONL log `output/real_betting/open_bets_history.jsonl`
  — one line per (read × bet) pair. Lets us track cashout drift over
  time and feeds scenario #4 backtests.
- **Live-analysis wiring** (the actual outcome): `scripts/run_live_analysis.py`
  reads the snapshot at the same point it reads the standings /
  prediction inputs, and `_attach_open_bets` in `web_ui/app.py:300`
  is taught a new code path:
    - Look up the bet's match in the snapshot (join key: scenario #4's
      `match_id`, secondary `my-bets-O-<uuid>` if we've persisted it).
    - If found and the offer is fresh (within N minutes, TBD ~5 min)
      and not `paused`, set `fair_cashout = <real offer>` and tag the
      enriched record with `cashout_source: 'bookmaker'`.
    - Else, keep the existing `stake × odds × adj_prob × 0.95`
      formula and tag `cashout_source: 'synthetic'`.
  The fragment template (`_open_bets_fragment.html`) is updated to
  show the source ("Cashout €X.XX · bookmaker" vs "Est. cashout €X.XX")
  so the user can tell at a glance whether the displayed value is a
  real offer or an estimate.

**Key gotchas anticipated**:
- The `data-cashout-target="1"` attribute injection from scenario #2
  must NOT be used here — that was a "find one bet" trick. The bulk
  path iterates `li` siblings directly.
- Greek decimal separator. `'1,47'` parses to `1.47`, not `147`.
- Time zone on the snapshot timestamp — record as UTC ISO; conversion
  to Athens local time is a display concern.

**Status**: **PASSED 2026-05-25** — shipped end-to-end across 5
sub-pieces (3A → 3E):

- **3A — Consumer**: `cashout_source` flag in
  `data_sets/betting_config.json` (flipped to `'bookmaker'` for
  football 2026-05-25; `cashout_snapshot_max_age_s = 600`),
  `_load_bookmaker_offers` helper, `_attach_open_bets` rewired with
  synthetic fallback, `real`/`est` source badge in
  `_open_bets_fragment.html`. Verified synthetic across 4 cases.
- **3B — Scraper**: `real_betting/read_open_bets.py` writes
  `output/real_betting/open_bets_snapshot.json` + appends
  `open_bets_history.jsonl`. CLI `python -m real_betting
  read-open-bets`. Validated against two real OPEN bets — extracted
  UUIDs, `match_id`, home/away, cashout offer text + parsed value,
  paused flag.
- **3C — Join via fuzzy team-name match**: discovered Pamestoixima
  (8-digit numeric) and Flashscore (8-char alphanumeric) use
  entirely different `match_id` schemes — the original
  `match_id`-keyed join could never fire on real data. Added
  `_match_offer_by_teams()` (rapidfuzz `token_set_ratio`, min-score-80
  on the worse of the two team names). Synthetic test verified
  matching against the real snapshot.
- **3D — Link UI surface**: green `🔗 linked` badge on every
  enriched bet whose join hits, independent of `cashout_source`
  preference and parseable-offer state. Standalone
  `/football/live_analysis` page filters to linked-bet matches only
  ("skin in the game" view); dashboard keeps the full listing.
- **3E — Refresh chaining**: manual "Refresh Live Snapshot" button
  now POSTs to `/football/refresh_live?with_bookmaker=1`, spawning a
  parallel `read-open-bets` Popen alongside the Flashscore scrape.
  Auto-5m stays Flashscore-only (headless Pamestoixima blocked at
  login — step 6d gate still in effect per
  `PAMESTOIXIMA_NOTES.md` corrections).

**Deferred minor**: `market` extraction returns `"Single\n2.00€"`
(bet-type cell) instead of `"Total Goals Over/Under"`; `odds`
parsed as `null`. Both fields are informational, not consumed by
the join — fixable from the dumped HTML when convenient.

---

## 4. Wire cashout offers into the live-match decision engine

**Goal**: at every Refresh Live Snapshot tick (or on a fixed cadence),
for every OPEN bet the user holds, join the **scraped cashout offer**
(scenario #3) with the **live model output** (`LiveAdjuster.adjust_probabilities`
/ `adjust_ou_probabilities` from `ml_project/live_adjuster.py`) and the
**cashout rules** (`ml_project/backtest/rules.py`) to produce a
recommended action: `HOLD`, `CASH_NOW`, or `WARN` (offer differs from
fair value by > threshold). Surface the recommendation on the
`/football/betting` page next to each OPEN bet.

Scenario #3 wires the real *cashout value* into `live_data.json`;
scenario #4 wires a real *decision* off the back of that — same
backtest rules (`lock_in_profit` / `stop_loss` / `late_drift`) that
today consume the synthetic `fair_cashout`, but now operating on the
real bookmaker offer rather than the `stake × odds × adj_prob × 0.95`
proxy. The output is a recommendation field on each enriched bet, not
a change to the displayed cashout value (#3 owns that).

**Match used**: TBD. Validate against any open bet during a live match.
Best signal: bet placed pre-match, then a Refresh Live Snapshot tick
during the live window where the model's adjusted probability has
drifted vs the pre-match probability — the decision should differ
between "ignore offer" and "cash now".

**Script(s)**: TBD. Probable layout:
- `real_betting/read_open_bets.py` from scenario #3 — bulk reader.
- `ml_project/backtest/rules.py` — already has the rule API; reuse
  the `Rule.evaluate(...)` signature. Add a new live-driven entry
  point that takes a real offer + a fresh live adjuster output and
  returns a `RuleDecision`. The existing `null` / `lock_in_profit` /
  `stop_loss` / `late_drift` rules become the same logic operating
  on real-instead-of-synthetic offers.
- `scripts/run_live_analysis.py` — extend so that after writing
  `output/live_data.json` and the `live_history_<date>.jsonl` line,
  the script also calls the open-bets reader and writes a per-bet
  recommendation file `output/cashout_recommendations_<date>.json`.
  Gated by a config flag — default off until scenario #3 is solid.
- `web_ui/templates/_open_bets_fragment.html` — render the
  recommendation alongside the existing `fair_cashout` estimate.

**Safety gate**: read-only at the bookmaker. The recommendation is
*displayed*, never acted on automatically. A separate scenario (TBD)
will introduce the auto-commit path; this scenario stops at "produce
the recommendation". The scraped offer feeds the UI; nothing the script
does can move money.

**Critical inputs & joins**:
- **Identity** between a scraped bet and a stored slip:
  - Primary key: `match_id` extracted from the Pamestoixima row's
    fixture link (`/en/live/football/<league>/<teams>/<match-id>`).
    Maps to the Flashscore `match_id` already stored on each bet —
    they should match after a one-time alias check (TBD: confirm the
    two providers use the same canonical ID; if not, fall back to
    fuzzy team-name match via `rapidfuzz`, same approach as
    `ml_project/resolve_daily_bets.py`).
  - Secondary key: `my-bets-O-<uuid>` from Pamestoixima. Store this
    onto the bet record at scenario #3 commit so settled-bet
    reconciliation in the future can use it.
- **Live model output**: each Refresh Live Snapshot tick already
  produces `adj_probs` (1X2) and `adj_ou_probs` (O/U) per live match,
  written to `output/live_data.json`. The decision engine reads
  these directly.
- **Rule evaluation**: feed `{stake, odds, sel_prob_now (from
  adj_probs), cashout_offer (from scenario #3), pre_match_prob,
  minute}` into each of the four backtest rules. Pick the strongest
  signal (precedence TBD — `stop_loss` typically dominates, then
  `lock_in_profit`, then `late_drift`).

**Recommendation surface**:
- New column on the `/football/betting` page's OPEN bets table:
  `Cashout offer (€X.XX) · Recommend: HOLD` / `CASH NOW` / `WARN`.
- `_open_bets_fragment.html` already has a `fair_cashout` column —
  this scenario adds the **real** offer and the recommendation
  alongside it; the fair-value estimate becomes a sanity check
  ("real offer is N% above/below internal fair value").

**Behavioural notes to confirm during validation**:
- Refresh cadence. The dashboard's "Auto 5m" tick is 5 minutes
  (`scripts/run_live_analysis.py` invoked from the UI); the cashout
  read should piggyback on that to avoid bookmaker rate-limiting.
  A separate manual scrape is fine ad-hoc.
- Behaviour when a market is paused live. Scenario #3 marks the offer
  as `paused`; the decision engine must short-circuit to `HOLD` in
  that case — no offer means no decision to make.
- Behaviour when no live data exists (match finished, late kickoff,
  CSV not refreshed). Same `HOLD` fallback. Never default to
  `CASH NOW` when inputs are incomplete.

**Output**:
- `output/cashout_recommendations_<date>.json` — one record per OPEN
  bet on the latest tick: `{bet_id, match_id, lane, stake, odds,
  cashout_offer, fair_cashout, sel_prob_now, recommendation, rule_fired,
  ts}`. Idempotent across ticks (last writer wins).
- Rendered in the UI on the `/football/betting` page.

**Key gotchas anticipated**:
- The bookmaker's offer is a *commercial* price — it includes the
  bookmaker's margin. The model's fair-value estimate is margin-free.
  Direct comparison without correcting for that margin will systematically
  recommend `CASH NOW` (the offer always looks low). The recommendation
  threshold must be tuned on real data — scenario #4 validation must
  collect ≥1 week of paired (offer, fair-value) data before any
  recommendation is trusted.
- `match_id` parity between Pamestoixima and Flashscore is unverified.
  If the two providers use different identifiers, fall back to fuzzy
  team-name match — this is the same problem `team_mapping.py` already
  solves between Flashscore and football-data.co.uk, and the same
  `rapidfuzz`-based approach should work.
- Multi-lane bets. A single conceptual wager held in `value` + `model`
  lanes (the cascading-cashout behaviour documented in CLAUDE.md) shares
  a `bet_id` but has separate `stake_units` per lane. The
  recommendation must be computed per lane — the offer is shared, but
  the EV calc isn't.

**Status**: **OPEN.**

---

## 5. Place multiple bets, each in its own slip

**Goal**: drive the place-bet flow once per bet across a batch of N
independent pre-match bets, with **each bet committed as its own
single-selection slip** (no multi / parlay accumulator). The output
is N separate slip IDs on Pamestoixima, N balance debits, N audit
records — same shape as scenario #1, just iterated.

This is the natural extension of scenario #1 to the `/football/auto_wager`
output, which today produces 3–15 candidate bets per day across the
value / conviction / model lanes. Placing them one at a time manually
is a non-starter; the batch flow is the path to actually using the
model output.

**Match(es) used**: TBD. Validation batch should be:
- 2–3 fixtures we're prepared to actually back (smallest-stake bets
  from a real `output/predictions_*.csv` → `auto_wager` output).
- Mixed markets — at least one 1X2 bet and one O/U bet — so the
  per-market market-box selector paths from scenario #1 are both
  exercised.
- Mixed leagues, so the per-league market accordion ordering can't
  hide a regression where the second iteration only works because the
  scroll position from the first iteration left the right section in
  view.

**Script**: TBD — `real_betting/dryrun_batch_placement.py` (new). CLI
subcommand `dry-run-batch-placement` on `bookmaker_cli.py`. Initial
shape: hardcoded list of `{match_url, market, selection, odds, stake}`
tuples (same one-shot-artifact pattern as `dryrun_freiburg_villa.py`)
so the validation run is reproducible from source. Generalising to
read directly from `output/bets_<date>.json` is a later step.

**Safety gates** (defence in depth — every one must pass per bet, not
just at module load):
- `EXECUTE_PLACE_BETS` — module-level constant, default `False`. Mirrors
  `EXECUTE_PLACE_BET` from scenario #1.
- `MAX_BETS_PER_RUN` — hard cap on the batch size (default `3`). The
  script refuses to load if the hardcoded list exceeds the cap.
- `MAX_STAKE_PER_BET_EUR` — per-bet stake cap (default `10.0`, same as
  scenario #1's `MAX_STAKE`). Asserted **per iteration**, not just at
  module load — odds drift between iterations could shift a stake.
- `MAX_TOTAL_STAKE_EUR` — sum-of-stakes cap across the whole batch
  (default `20.0`). The script refuses to proceed past the bet that
  would push the cumulative total over the cap.
- **Pre-iteration slip check**: before selecting the next outcome, the
  Betslip counter must read `(0)`. If it doesn't, the previous bet's
  Place Bet didn't fully clear — abort the batch. Never add a
  selection to a slip that already has one (silent multi/parlay risk).
- **Pre-iteration balance check**: before committing the next bet,
  `pm.get_balance()` must be ≥ the next bet's stake. If unreadable,
  fall back to "remaining-budget tracked locally" (initial balance −
  cumulative stake placed so far) and refuse the next bet if that
  budget would go negative.

**Per-bet flow** (identical to scenario #1, iterated):
1. Pre-clear slip (defensive — leftover state from a previous batch
   run). `SLIP_CLEAR_SELECTORS` from `dryrun_freiburg_villa.py`.
2. Navigate to the bet's match URL directly. NEVER reuse the coupon
   page across iterations — the sport tab + scroll position are
   session-pinned in unpredictable ways across navigations.
3. Expand the right market accordion (`Total Goals Over/Under` for
   O/U, `Match Result` for 1X2). Reuse scenario #1's collapse-state
   probe + `event-page-market-box-collapseBtn` toggle pattern.
4. Click the outcome button (`button[name="Over"]` scoped at the
   right `.market-box-root` for O/U; the equivalent for 1X2).
5. Verify `outcome-box-root.selected` count is 1 in that market box.
6. Read the Betslip counter — must be `(1)`. If `(2)+`, abort: a
   previous iteration's selection survived and we're about to commit
   an accumulator.
7. Fill stake input, verify via `input_value()` read-back.
8. Click Place Bet via the dedicated `_execute_place_bet`-style
   method (bypasses `FORBIDDEN_CLICK_LABELS` — that's the point).
9. Wait for `SLIP_EMPTY_INDICATORS`. If they don't appear within
   15s, abort the batch (uncertain state — don't risk firing another
   placement on top).
10. Read balance, append a per-bet audit record, sleep
    `BETWEEN_BETS_PAUSE_S` (default 4–8s, randomised) before the
    next iteration. The sleep is anti-bot hygiene, not a UI signal.

**Behavioural notes to confirm during validation**:
- **Odds-drift confirmation modal**. Pamestoixima sometimes shows an
  "Odds have changed — accept new odds?" modal between Select and
  Place Bet. Single-bet scenario #1 never hit it (one click, no
  delay), but a batch run with `human_pause`s scattered through may.
  If the modal appears, the script must either accept the new odds
  (only if `new_odds >= original_odds × 0.95`) or reject and abort.
  Selectors TBD on first observed instance.
- **Slip carry-over between matches**. Hypothesis: navigating to a
  new match URL while a selection sits in the slip preserves the
  selection (the slip is session-state, not page-state). The
  pre-iteration slip check (step 6 above) is what catches this; the
  pre-iteration `SLIP_CLEAR_SELECTORS` click (step 1) is what
  prevents it.
- **Anti-bot tripwires under rapid placement**. Three consecutive
  Place Bet clicks within ~30s is more conspicuous than one. If the
  third bet hits a CAPTCHA / verification page, the script must
  abort the batch (no auto-solve, no retry). One-attempt rule from
  scenario #1 carries over.
- **`auto_wager` output as the natural input**. The eventual
  generalisation (after this scenario passes) reads the auto-wager
  CSV / bets JSON directly. Two integration points to confirm:
  (1) `bet_id`s assigned by `/football/place_bets` survive the round
  trip and end up on the audit record so settlement can join back
  to the slip later, and (2) the per-lane stakes from `auto_wager`
  are within `MAX_STAKE_PER_BET_EUR` — if the conviction lane's
  stake exceeds the cap, the script refuses without firing.

**Output**:
- `output/real_betting/batch_placement_<ts>/` — one dir per batch run.
  - `batch_placement_record.json` — array of per-bet records (match,
    market, selection, odds_at_selection, odds_at_place, stake,
    balance_before, balance_after, success boolean, pamestoixima_slip_id
    when scrape-able, dryrun_dir). Mirrors scenario #1's
    `placement_record.json` shape × N.
  - Per-bet screenshots (`{i}_01_on_match_page.png`, `{i}_04_selected.png`,
    etc.) so a post-mortem can isolate which iteration failed.
  - A top-level `batch_summary.txt` — count placed / count failed /
    total stake / final balance.

**Key gotchas anticipated**:
- **The slip is session-state, not page-state**. A selection survives
  page navigation. The pre-iteration clear is non-optional, not a
  defensive flourish.
- **Race between odds read and Place Bet click**. The stake is
  computed off the odds at selection time (scenario #1's stake input
  carried €10 flat; auto_wager stakes scale with the odds). If odds
  drift between Select and Place, the staked amount no longer matches
  the lane's intended exposure. The validation script must record
  both `odds_at_selection` and `odds_at_place` and abort the batch if
  the gap exceeds 5%.
- **Partial-batch failure semantics**. If bet 3 of 5 fails, bets 1–2
  remain placed (they're already committed at Pamestoixima); bets 4–5
  are skipped. The script must NOT attempt to "rollback" or void
  bets 1–2 — that would require another commit-click each. Reporting
  surfaces the partial outcome; the user decides whether to settle
  manually or wait for natural settlement via `bin/run_verification.sh`.
- **Stake-input residual value across iterations**. Pamestoixima may
  pre-fill the stake input with the previous iteration's value
  (€10 from bet 1 still showing when slip-2 opens). Always
  `fill()` with the iteration's stake before reading back, never
  trust the displayed value.
- **Anti-bot — randomised pauses, single login**. Re-login between
  iterations is conspicuous; one session covers the whole batch.
  `human_pause()` and `BETWEEN_BETS_PAUSE_S` exist for the same
  reason.

**Status**: **PASSED 2026-05-25** (script: `real_betting/dryrun_batch_placement.py`).
Validation batch:
- Paderborn vs Wolfsburg, O/U Over 2.5, €2 @ 1.94 — placed via run
  `batch_placement_20260525-141135`. That run halted on a false
  slip-empty-timeout failure (now fixed in PAMESTOIXIMA_NOTES.md
  "Corrections" — the actual success signal is the placement-receipt
  overlay, not an empty slip).
- Sandefjord vs Fredrikstad, O/U Over 2.5, €2 @ 1.65 — placed via run
  `batch_placement_20260525-142011` with the corrected
  `[class*="placementNotification" i]` indicator.

Balance trail: €19.67 → €17.67 → €15.67 (both Δ exactly €2.00).

Used **hardcoded `match_url` constants** in the BETS list — the new
`real_betting/discover_fixtures.py` (real-betting step 6b) is written
but its selectors haven't been live-validated yet, and the batch
script doesn't yet call `find_fixture_url()` from it. Wiring those
together is the natural follow-up so future batch runs are
team-name-driven rather than URL-pasted.

---

## Backlog / future scenarios to design

(Filled in after we discuss what other behaviours need coverage.)
