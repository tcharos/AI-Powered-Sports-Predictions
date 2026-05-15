# Sports Predictor UI Manual

The Web Dashboard serves as the command center for running predictions, verifications, and managing data.

## 0. Multi-sport URL structure

The UI is sport-aware. Each sport mounts under its own URL prefix:

| URL | What lives there |
| :--- | :--- |
| `/` | **Sport-picker landing page**. Shows a card per sport (active or dormant) with current bankroll, plus a **Portfolio Summary table** at the bottom aggregating bets, stake, P/L, ROI, and bankroll across every sport. |
| `/football/*` | All football routes — dashboard, betting, predict, verify, retrain, etc. |
| `/nba/*` | NBA routes (currently dormant — returns 404 until reactivated next NBA season). |
| `/status`, `/stop/*`, `/server/*` | Sport-agnostic infrastructure. |

The navbar has a **🏟️ Sport ▾** dropdown to switch between sports, and a brand link (🏆 Sports Predictor) that returns to the landing page. The bankroll display in the navbar is **sport-aware**: on a football page it shows football's bankroll; on the landing or any sport-agnostic page it shows the portfolio total across all sports.

**Each sport has its own bankroll and its own betting strategy tunables.** They're isolated — a losing week on NBA won't drain football's funds, and you can A/B compare each sport's Value-vs-Conviction lanes independently. Tunables live in `data_sets/betting_config.json` under `sports.<slug>`. Adding a new sport is a one-PR operation — see `CLAUDE.md` for the recipe.

## 1. Football Dashboard (`/football/`)
The main page lists generated reports sorted by date (most recent first).
*   **Prediction Reports / Verification Reports / Available Scraped Data**: each card shows the **last 3** entries to keep the dashboard tidy. Older entries remain on disk under `output/` and `output/history/`; access them by direct URL or by archiving newer entries to bring older ones into view.
*   **Action**: Click "View" to see the detailed table of predictions for that day.
*   **Global Stats**: Displays overall accuracy for the loaded leagues at the top.
*   **📁 Archive button**: All "delete" buttons across the dashboard (predictions, verifications, scraped data) are **soft-delete** — they move the file to `output/history/` rather than removing it. Files in `output/history/` are hidden from the dashboard lists but remain on disk for reference and (for bet slips) keep counting toward the Strategy Comparison totals.

## 2. Navigation Bar (Top Menu)

### ⚽ Flashscore Predictor
Links back to the Home Dashboard.

### 💾 Data (Dropdown)
Contains administrative tasks for data management:
*   **Update Current Season (Results)**: Downloads the latest match results (CSV) from *Football-Data.co.uk*. This now updates **both** Main 22 Leagues and all configured **Extra Leagues** (USA, BRA, JPN, etc.). Use this daily.
*   **Update Standings & Form**: Scrapes Flashscore for the latest league tables and form guides. This updates the JSON files used by the *Heuristic Adjuster*.
*   **🔄 Retrain Model**: Runs the **Full Pipeline**:
    1.  Updates Results.
    2.  Updates Standings.
    3.  Retrains the XGBoost Model.
    *Use this once a week or when significant new data is available.*

### 💸 Betting Strategy & Simulator
Opens the **Betting Simulator Dashboard** at `/betting`. Two parallel paper-trading strategies run on the same predictions and are tracked separately for long-run comparison.

#### **1. Two-Lane Strategy**

| | Value lane | Conviction lane |
| :--- | :--- | :--- |
| **Entry filter** | EV > 0 AND Conf ≥ `min_confidence` (default 0.45) | Conf ≥ `conviction_min_confidence` (0.65) AND odds ≥ `conviction_min_odds` (1.40). EV ignored. |
| **Stake formula** | `bankroll × EV × Conf × stake_multiplier` (default multiplier 0.4) | Flat `bankroll × conviction_stake_pct` (0.5%) |
| **Per-bet cap** | `bankroll × max_stake_pct` (3%) | Same |
| **Min-stake floor** | €2 (drop sub-floor picks entirely) | Same |
| **Goal** | Capture market mispricing | Bet the model's strongest opinions, regardless of vig |

Both lanes share a **combined daily exposure cap** of `bankroll × max_daily_exposure_pct` (10%). When the combined total exceeds the cap, the value lane gets priority and the conviction lane is scaled down (or dropped entirely if value alone hits the cap). Look for the `cap_action` line in the slip summary to see which behavior fired.

All tunables live in `data_sets/betting_config.json` and can be edited without touching code.

#### **2. Workflow**

1.  **Generate Slip** (`/auto_wager`):
    *   Click 🎰 **Generate Slip**.
    *   Two cards render side-by-side — Value lane (blue) and Conviction lane (yellow). Each shows match, type, selection, odds, conf, EV, and stake.
    *   **Session Bankroll override**: optional input. Lets you preview a slip sized as if your bankroll were smaller. Cannot exceed your real saved bankroll. Empty = use real bankroll.
2.  **Place All Bets** (`/place_bets`):
    *   Combined slip (both lanes) is written to `output/bets_<date>.json`. Each bet is tagged with its `lane`.
    *   Total stake is **immediately deducted** from your saved bankroll.
3.  **Wait for results** — kickoff and finals.
4.  **Verification & Settlement**:
    *   Click ✅ **Run Verification** (or run `./bin/run_verification.sh` from CLI). Triggers the scraper to pull final scores.
    *   Two settlement paths run on the same `bets_<date>.json` file: `resolve_daily_bets.py` (CLI, fires first) and `process_bet_verification` (web UI, fires after subprocess returns and skips if already CLOSED).
    *   Winning bets credit `stake × odds` back to bankroll. Losing bets credit nothing. VOID bets refund stake.

#### **3. Strategy Comparison Table**
At the top of `/<sport>/betting` (e.g. `/football/betting`). The header reads "**Strategy Comparison · &lt;Sport&gt;**" so the scope is unambiguous when more than one sport is active. One row per lane, aggregated across **all** of that sport's historical slips (active + archived):

| Column | What it means |
| :--- | :--- |
| **Bets** | Total bets placed in this lane |
| **Settled / Won / Lost / Void** | Resolution counts |
| **Win %** | `won / (won + lost)` — voids don't count |
| **Stake** | Total currency staked |
| **Returned** | Total currency returned (stake + winnings + voided refunds) |
| **Net P/L** | Currency delta. Bankroll-era dependent — use ROI for comparison |
| **ROI %** | `Net P/L / Stake` — the apples-to-apples lane comparison metric |

Use **ROI %** as the long-run lane comparator; absolute P/L is bankroll-era dependent.

For a cross-sport view of the same metrics, see the **Portfolio Summary** table on the landing page (`/`) — one row per sport plus a Total row. Same aggregator under the hood (`compute_sport_summary()` in `web_ui/app.py`).

#### **4. History & Soft Delete**
*   Each placed slip shows below the comparison table.
*   **OPEN slips**: badge shows `🔒 Archive available after settlement`. The Archive button is intentionally hidden — settlement only looks in `output/`, so archiving an OPEN slip would orphan it.
*   **CLOSED slips**: 📁 **Archive** button moves the file to `output/history/`. The slip disappears from the visible list but its bets are still counted in the Strategy Comparison table totals.
*   **Result badges**: WON (green), LOST (red), VOID (yellow), OPEN (grey).
*   To restore an archived slip: move the file back manually via `mv output/history/bets_<date>.json output/`.

### ⚙️ Server (Dropdown)
*   **Restart Server**: Reloads the Flask application (useful after code changes).
*   **Stop Server**: Shuts down the web interface.

## 3. Prediction View
Clicking a prediction file opens the **Prediction Report**.
*   **Filters**: Filter by League, Confidence Level, or Prediction Type (1/X/2 or O/U).
*   **Table Columns**:
    *   **1X2 Prediction**: The predicted outcome (1 = Home, X = Draw, 2 = Away).
    *   **Conf**: Model confidence score (0.00 - 1.00).
    *   **Heuristic Logs**: Explains *why* a confidence was boosted (e.g., "Form Boost Home").

## 4. Verification View
Clicking "Run Verification" (or viewing a verification file) compares predictions against reality.
*   **Green Rows**: Correct Predictions.
*   **Red Rows**: Incorrect Predictions.
*   **Stats**: Shows accuracy (%) for that specific day for both 1X2 and Over/Under 2.5 markets.
