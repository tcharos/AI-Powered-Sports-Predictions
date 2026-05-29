# Sports Predictor UI Manual

The web dashboard is the command center for running predictions, watching
live matches, placing virtual bets, settling them, and tracking the
three-lane betting strategy over time. This manual covers what every page,
button, and column does.

For the **strategy** side (how lanes work, what each formula does, when
to cash out / void), see [`docs/betting_strategy.md`](betting_strategy.md).

## 0. Multi-sport URL structure

The UI is sport-aware. Each sport mounts under its own URL prefix:

| URL | What lives there |
|---|---|
| `/` | **Sport-picker landing page.** Shows a card per sport (active or dormant) with current bankroll, plus a **Portfolio Summary table** at the bottom aggregating bets, stake, P/L, ROI, and bankroll across every sport. |
| `/football/*` | All football routes — dashboard, betting, predict, verify, retrain, data updates, view files, cashout, void, archive, etc. Full 1X2 + Over/Under markets. |
| `/nba/*` | **NBA** routes (active) — predictions dashboard + moneyline paper-betting flow. |
| `/euroleague/*` | **Euroleague + EuroCup** routes (active) — predictions dashboard + moneyline paper-betting flow. |
| `/betting` | **Consolidated betting dashboard** — one page with tabs (All sports · Football · 🏆 Euroleague · 🏀 NBA). The navbar's "Betting Dashboard" points here; `/football/betting` still works as a football-only deep link. |
| `/status`, `/stop/*`, `/server/*` | Sport-agnostic infrastructure. |

The navbar has a **🏟️ Sport ▾** dropdown (Football · Euroleague · NBA, all
active) to switch between sports and a brand link (🏆 Sports Predictor) that
returns to the landing page. The menu is sport-generic — sport-specific data /
retrain actions live on each sport's own dashboard, not in the top menu.

**Bankroll display is sport-aware.** On a football page it shows the
sum across football's three lane bankrolls; on the landing/agnostic
pages it shows the portfolio total across every sport.

**Each sport has its own three-lane bankroll** (value / conviction /
model) and its own betting strategy tunables — fully isolated. A
losing week on NBA never drains football's funds. Tunables live in
`data_sets/betting_config.json` under `sports.<slug>`.

## 1. Football Dashboard (`/football/`)

The main landing for the football sport. Top-to-bottom:

### Row 1 — Actions card

A two-row Actions card (the data/retrain controls moved here off the old
top-menu "Data ▾" dropdown so the navbar stays sport-generic):

- **Row 1 left — 🚀 Prediction** (`/football/predict`) — runs the prediction
  pipeline for the date in the date-picker (defaults to tomorrow). Optional
  "Force scrape (overwrite data)" toggle.
- **Row 1 right — 💸 Generate Bet Slip** — a link that jumps to the betting
  page's slip generator (`/football/betting#generate-slip`).
- **Row 2 left — ✅ Verification** (`/football/verify`) — runs the verification
  pipeline for the date in the date-picker (defaults to yesterday). Scrapes
  results, settles bets. Works even when the predictions CSV is missing
  (falls back to bet-derived match IDs).
- **Row 2 right — 💾 Data** — three buttons: **Update Results**
  (`/football/update_data`, latest match results from football-data.co.uk),
  **Update Standings & Form** (`/football/update_leagues`, Flashscore tables/
  form), and **🔄 Retrain Model (Full Pipeline)** (`/football/retrain_model`:
  data → standings → train → fit + validate per-league Platt calibrators).

### Row 2 — Live Matches (when present)

If `output/live_data.json` has matches, this section renders one card
per live match. Each card has four columns:

1. **Match info** — home/away teams + score + match minute. Cards on
   matches with at least one OPEN bet on them get a **yellow border
   + 💰 N badge** so you can see at a glance which live games have
   your money on them. Matches whose pre-match probabilities were
   synthesized from bet odds (no prediction row available) get a
   `🛈 priors from odds` badge.
2. **Live stats** — xG, xGOT, possession, touches in opposition box
   per side.
3. **Pre / Live probability adjustment table** — pre-match vs live-
   adjusted probabilities for both 1X2 and O/U markets. Live cells
   are green when up vs pre-match, red when down.
4. **Open bets on this match** — one card per OPEN bet, showing lane
   badge, type/selection/odds/stake, the state badge (🟢 Lock-in /
   🔴 Stop-loss / 🟡 Hold), the fair-value cashout estimate, and a
   **💰 Cash Out** button. Cashed-out, won, lost, and void bets do
   NOT appear here — only OPEN ones.

**⚡ Refresh Live Snapshot** button at the bottom of the section
triggers a fresh scrape of all currently-live matches (predictions +
open bets union). Writes `output/live_data.json` and appends to
`output/live_history_<date>.jsonl`.

**Auto 10m** checkbox auto-triggers a browser-side refresh every 10 minutes
while the tab is visible (UI convenience only; state persists via localStorage).

**Auto-cashout** checkbox arms **server-side autonomous auto-cashout** — it does
NOT depend on a browser tab. While armed, a server daemon refreshes Flashscore
and automatically cashes out (at the estimated fair value) any OPEN bet whose
live decision is 🟢 lock-in or 🔴 stop-loss, every 10 minutes. Virtual money
only — no real bet is placed. The decision rule is in §`betting_strategy.md`.

### Row 3 — Recent Reports (collapsible)

Two columns side-by-side, each showing the **last 3 entries**:

- **Prediction Reports** (`predictions_*.csv`) — most recent first.
  Each entry: date + match count + View button + 📁 archive (soft
  delete). At the top of the column: **📁 Archive all** button to
  archive every prediction file in one click (with confirmation
  dialog). Predictions and their matching `report_*.txt` summaries
  archive together.
- **Verification Reports** (`verification_*.csv`) — same pattern.
  Independent "📁 Archive all" button.

### Row 4 — Cumulative League Performance (collapsible)

A table showing prediction accuracy per league (1X2 and O/U) across
every verification run. Sourced from `data_sets/league_analytics.json`,
updated each time verification runs.

### Row 5 — Available Scraped Data (collapsible)

Shows the last 3 `matches_*.json` files (scraper output before
predictions). Independent **📁 Archive all** button.

### Soft delete (Archive button)

Every per-row **📁** button and every column-level **Archive all**
button does a **soft delete**: the file moves to `output/history/`
rather than being removed. Files there are hidden from the dashboard
lists but remain on disk; for bet slips, they still count in the
Strategy Comparison totals.

To restore an archived file: move it back manually
(`mv output/history/<file> output/`).

## 1b. NBA & Euroleague dashboards (`/nba/`, `/euroleague/`)

Both basketball sports share the same shape — simpler than football (moneyline
v1, no live/cashout feed yet):

- **Header** — sport title + current 3-lane bankroll badges (value / conviction
  / model).
- **Actions** — 🔮 **Predict**, ✅ **Verify** (both with a date input —
  Euroleague has these from day one; NBA's date inputs are a tracked follow-up),
  🔄 **Retrain**, and 💸 **Generate Slip (Moneyline · v1)**.
- **Predictions table** — one row per game: matchup, pre-game ELO, calibrated
  `P(home)` + raw `P(home)`, pick, predicted total, calibration source.
  Euroleague adds a **Comp** badge (EuroLg / EuroCup).
- **Recent slips** + an inline **slip preview** (generate → review lanes →
  Place Bets), writing to `output_basketball/` (NBA) / `output_euroleague/`.

**Odds dependency:** the basketball slip generators join an odds file
(`espn_odds_<date>.json` for NBA; `euroleague_odds_<date>.json` for Euroleague).
NBA has live ESPN odds; **Euroleague odds arrive with the season-start Flashscore
probe** — until then Euroleague slips come back empty (predictions still show).

Storage is fully sport-separated: an NBA/Euroleague bet can never touch
football's bankroll or slips.

## 2. Navigation Bar (top menu)

### 🏆 Sports Predictor (brand link)
Returns to the landing page.

### 🏟️ Sport ▾
Switch between active sports: Football, 🏆 Euroleague, 🏀 NBA.

> The old **💾 Data ▾** menu was **removed** — those football data/retrain
> actions now live on the football dashboard's Actions card (§1, Row 2), so the
> top menu is sport-generic. Each sport owns its own data/retrain controls.

### 📊 Betting Dashboard ▾
Opens the **consolidated** betting dashboard at `/betting` (tabbed across all
sports — see §4b). A "Football-only view" item links to the legacy
`/football/betting`. Also holds **⚙️ Strategy Tunables**.

### ⚙️ Server ▾
- **Restart Server** — restarts Flask (useful after `.md` doc edits
  or config changes that need a fresh server).
- **Stop Server** — shuts down the web UI.

## 3. Prediction View (`/football/view/predictions_<date>.csv`)

Clicking "View" on a prediction file opens the detailed report:

- **Filters** — by League, Confidence Level, Prediction Type (1/X/2 or O/U).
- **Columns** —
  - **1X2 Prediction** — Home / Draw / Away.
  - **Conf** — model confidence (0.00–1.00) for the picked outcome.
  - **EV** — expected value (`prob × odds − 1`).
  - **Heuristic Logs** — why a confidence was boosted/cooled (e.g.,
    "Form Boost Home", "Rank-gap Draw fade").
  - **Cal 1X2 Source** / **Cal O/U Source** — whether the per-league
    Platt calibrator came from full-features OOF, minimal-features
    OOF, or no calibrator was applied (empty).
  - **Home Win % (raw) / Draw % (raw) / etc.** — the model's
    pre-calibration probabilities (audit-trail columns).

## 4. Betting Page (`/football/betting`)

The strategy comparison + bet-history view. Top-to-bottom:

### Header — Strategy Comparison table

Three rows (value / conviction / model lanes) aggregated across every
slip in `output/` + `output/history/`:

| Column | Meaning |
|---|---|
| **Bets** | Total bets placed in this lane |
| **Settled** | Bets that reached a terminal state (WON / LOST / VOID / CASHED_OUT) |
| **Won / Lost / Void / Cashed Out** | Per-status counts |
| **Win %** | `won / (won + lost)` — voids and cashouts not counted |
| **Stake** | Total currency staked |
| **Returned** | Total currency returned (winnings + voided refunds + cashout amounts) |
| **Net P/L** | Currency delta |
| **ROI %** | `Net P/L / Stake` — the **canonical lane comparator** (use this, not absolute P/L) |

The header label is "**Strategy Comparison · Football (cumulative)**"
so the scope is unambiguous when multiple sports are active.

### Auto Wager (Generate Slip)

🎰 **Generate Slip** (`/football/auto_wager`) builds three parallel
slips — one per lane — from the latest `predictions_*.csv`:

- **Value lane** (sky-blue card) — EV-gated entries with Option B sizing.
- **Conviction lane** (lavender card) — high-confidence entries, flat 0.5%.
- **Model lane** (info-blue card) — broad coverage with adaptive sizing.

Each card lists its bets: match, type, selection, odds, conf, EV, stake.
Per-row **✕ Remove** button to drop an individual pick before placement.

Each lane card auto-hides when its slip is empty after removals.

**Session bankroll overrides** — three optional query-param inputs
(`bankroll_value`, `bankroll_conviction`, `bankroll_model`) let you
preview a slip sized as if a lane's bankroll were smaller. Cannot
exceed real saved bankroll. Empty = use real bankroll. Same with
`cap_value` / `cap_conviction` / `cap_model` (0–1 fraction) to
preview different daily-exposure caps.

### Place Bets

**Place All Bets** (`/football/place_bets`) writes the combined slip
to `output/bets_<date>.json`. Each bet is tagged with its `lane` and
stamped with a canonical `bet_id`. Each lane's stake is **immediately
deducted from its own bankroll**.

### Bet History (below the Strategy Comparison)

Every placed slip, ordered **chronologically (earliest slip first)**. Each slip
card is **collapsible** — click the header to expand/collapse; all slips start
collapsed so the view stays clean. The header alone gives you the date, status,
played stake, and outcome at a glance.

- **Header** — date + slip status badge (OPEN / CLOSED) + total stake
  + cumulative P/L + a ▾/▸ caret showing collapse state. Clicking the
  header toggles the bets table; the action buttons (Cancel / Archive)
  stay clickable without toggling.
- **Archive button** — 📁 visible only on CLOSED slips. Soft-deletes
  to `output/history/`. OPEN slips don't get the button (archiving
  before settlement orphans the slip).
- **Bet rows** — ordered **by lane (value → conviction → model), then by
  match start time**. One row per bet:
  - Lane badge (value / conviction / model — color-coded).
  - **Time** (match kickoff, local) + Match + type + selection + odds + stake.
    (Time shows `—` for legacy bets placed before kickoff time was recorded.)
  - **Result** column:
    - 🟢 **WON** (green stripe)
    - 🔴 **LOST** (red stripe)
    - 🟡 **VOID** (yellow stripe) with tooltip showing voided timestamp
    - 🔵 **CASHED OUT** (info-blue stripe) with tooltip showing cashout timestamp
    - 🔘 **OPEN** badge + **⊘ Void** button (for postponed/canceled matches)
  - **P/L** — per-bet currency delta. WON: payout − stake. LOST: −stake.
    VOID: 0. CASHED OUT: cashout_amount − stake.

### Cash Out (from the dashboard's live row)

The Cash Out button only appears on the dashboard live row when the
bet is OPEN, has a `bet_id`, and the match has current live data with
adjusted probabilities. Click → confirm → bet flips to CASHED_OUT,
cashout amount credited to the lane bankroll immediately.

**Cashout cascades across lanes.** If multiple lanes hold the same
conceptual wager (e.g. value + model both on Over 2.5 for the same
match), a single click cashes out every lane's bet with its own
per-bet payout. Each lane gets credited separately based on its own
stake × odds × adj_prob × 0.95.

### Void (from the betting page)

The ⊘ Void button only appears on OPEN bets. Use it for matches that
won't settle naturally — postponed, cancelled, abandoned. Click →
confirm → bet flips to VOID, stake refunded to the lane bankroll.
Same cross-lane cascade as cashout.

### Strategy Comparison vs Portfolio Summary

The Strategy Comparison table on `/football/betting` shows three rows
(one per lane) for **football only**. For a cross-sport view, the
**Portfolio Summary** table on the landing page (`/`) aggregates the
same columns per sport, plus a Total row. Same aggregator under the
hood (`compute_sport_summary()` in `web_ui/app.py`).

## 4b. Consolidated betting dashboard (`/betting`)

The navbar's **📊 Betting Dashboard** opens a single tabbed page:

- **🌐 All sports** — the cross-sport summary table (per-sport bankroll, bets,
  settled, stake, P/L, ROI + a Total row).
- **⚽ Football** — the full football betting panel (the same Strategy
  Comparison + Generate-Slip + slip-history described in §4, embedded verbatim).
- **🏆 Euroleague** / **🏀 NBA** — a card with the sport's bankroll badges and a
  **"Go to … dashboard"** link button (the rich per-bet panel for these is a
  tracked Phase-B follow-up; manage their slips on the sport's own dashboard).
  The Euroleague tab shows just the league logo (the mark already reads
  "Euroleague").

**The open tab survives a refresh:** switching tabs updates the URL's `?tab=`
param (and remembers your last tab), so reloading keeps you where you were
rather than snapping back to "All sports".

## 5. Verification View (`/football/view/verification_<date>.csv`)

Clicking "View" on a verification file shows predictions vs reality:

- **Green rows** — correct predictions.
- **Red rows** — incorrect predictions.
- **Stats** — accuracy (%) for that day for both 1X2 and Over/Under 2.5
  markets.

## 6. Live Analysis (`/football/live_analysis`)

Standalone live page with full-width cards per match. Same data as
the dashboard live row (uses the shared `_open_bets_fragment.html`
partial) but with bigger stats tables and the full pre-vs-live
probability comparison visible without scrolling.

Same matches-with-open-bets visual marking (yellow border + 💰 N).
Same Cash Out button per open bet.

**⚡ Refresh Live Snapshot** button at the bottom — identical to the
dashboard's.

## 7. Theme toggle

🌙/☀️ button in the navbar flips between light and dark mode.
Persisted via `localStorage`. Falls back to OS-level
`prefers-color-scheme` if no manual choice has been made.

The dark mode is Bootstrap 5.3's native `data-bs-theme` — set
inline in `<head>` before any CSS evaluates, so there's no flash
of mistitled theme on page load.

## 8. When predictions are missing

If today's predictions CSV is deleted but you still have open bets:

- **Live snapshot** still works — derives match IDs from the open
  bets and synthesizes minimal pre-match probabilities from each
  bet's odds. Matches with synthesized priors show the
  `🛈 priors from odds` badge.
- **Verification** still works — falls back to bet-derived match
  IDs, scrapes Flashscore, settles bets. Skips the prediction-vs-
  results accuracy report (nothing to compare). The UI flashes
  an info banner explaining the partial flow.

This is by design: the canonical `bet_id` per wager means the
betting system survives missing predictions because the bets
themselves carry enough info.

## See also

- [`docs/betting_strategy.md`](betting_strategy.md) — the lane
  system, staking formulas, daily caps, bet lifecycle, cashout
  formula. Read this for the "what does the system actually do
  with my money?" answer.
- [`docs/training_process.md`](training_process.md) — how the
  XGBoost model is trained and how the heuristic adjuster works.
- [`docs/walkthrough.md`](walkthrough.md) — end-to-end tour of a
  prediction → bet → settlement cycle.
- [`docs/codebase_overview.md`](codebase_overview.md) — folder-
  by-folder architecture map (for power users / devs).
