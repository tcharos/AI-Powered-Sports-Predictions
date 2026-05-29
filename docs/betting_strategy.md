# Betting Strategy

How the system turns predictions into wagers, tracks them, and settles them.
This is the user-facing reference — for the developer-level architecture
notes (function names, file paths, refactor history), see `CLAUDE.md`.

## Sports & markets

The same three-lane paper-trading engine runs **per sport**, with isolated
bankrolls and tunables. What differs is the market each sport bets:

| Sport | Markets (v1) | Odds source | Notes |
|---|---|---|---|
| **Football** | 1X2 **and** Over/Under 2.5 | football-data.co.uk / Flashscore | Full feature set: per-league Platt calibration, live in-play cashout + auto-cashout, void. |
| **NBA** | Moneyline | ESPN (live) | Predictions + paper betting on the NBA dashboard; totals (Over/Under) is a follow-up. |
| **Euroleague + EuroCup** | Moneyline | Flashscore (**season-gated** — arrives ~Oct) | Combined model with per-competition calibration. Slips stay empty until the odds probe lands; predictions show regardless. |

Each sport's lanes, bankrolls, statuses, and the Strategy Comparison table work
identically — only the market column and odds source change. All three are
viewable together on the consolidated **`/betting`** tabbed dashboard, or per
sport on `/football/`, `/nba/`, `/euroleague/`.

> Live in-play **cashout / auto-cashout / void** are **football-only** today
> (basketball has no live in-play feed yet). The rest of this doc's cashout
> sections describe the football flow.

## The three lanes

Each prediction can drive **three parallel paper-trading strategies** at
once. Each lane has its own bankroll, its own entry filter, its own
staking formula, and its own daily exposure cap. They never share funds.

| Lane | Entry filter | Stake formula | Per-bet cap | Min stake | Daily exposure cap | What it captures |
|---|---|---|---|---|---|---|
| **Value** | EV > 0 AND Conf ≥ 0.45 | `bankroll × EV × Conf × 0.4` | 3% of bankroll | €2 | 10% of bankroll | Market mispricing (the "edge" play) |
| **Conviction** | Conf ≥ 0.65 AND odds ≥ 1.40 (EV ignored) | Flat `bankroll × 0.5%` | 3% of bankroll | €2 | 10% of bankroll | High-confidence model opinions regardless of vig |
| **Model** | Every prediction | `bankroll × 0.5% × Conf × ev_factor` where `ev_factor = clamp(Conf × odds, 0.5, 1.5)` | 1.5% of bankroll | €1 | 15% of bankroll | Broad coverage of every model output |

**Why three lanes?** They answer different questions:

- *Is the model finding bookmaker mispricings?* → Value lane ROI.
- *Are the model's high-confidence calls profitable?* → Conviction lane ROI.
- *Does the model add information even when no clean "edge" exists?* → Model lane ROI.

Comparing their ROIs side-by-side over time tells you which signal is
actually paying off, independently from the others.

## Daily exposure caps

Each lane has a daily exposure cap that prevents a single day's bets
from putting too much of that lane's bankroll at risk. Defaults:

- Value: 10% of value bankroll
- Conviction: 10% of conviction bankroll
- Model: 15% of model bankroll (looser because the lane is broad-coverage by design)

**Cap-handling per lane**: if a lane's stakes for the day exceed its
cap, that lane's stakes are scaled down pro-rata. Other lanes are
unaffected. The cap action is logged in the slip's summary.

## Bankrolls

Each lane in each sport has its own bankroll bucket — value / conviction /
model — and never mixes with another lane's funds or another sport's
funds. Default starting bankroll: €1000 per lane per sport.

You can see the current per-lane bankrolls in the navbar (when you're
on a sport page) and in the **Portfolio Summary** table on the landing
page (which aggregates across sports).

**Bankroll tunables** live in `data_sets/betting_config.json`. Each
sport has its own block:

```json
"football": {
  "bankrolls": {
    "value":      {"current": 1000.0, "initial": 1000.0},
    "conviction": {"current": 1000.0, "initial": 1000.0},
    "model":      {"current": 1000.0, "initial": 1000.0}
  },
  "min_confidence": 0.45, "stake_multiplier": 0.4,
  "min_stake_eur": 2.0, "max_stake_pct": 0.03,
  "conviction_min_confidence": 0.65, "conviction_min_odds": 1.4, "conviction_stake_pct": 0.005,
  "model_base_pct": 0.005, "model_max_stake_pct": 0.015, "model_min_stake_eur": 1.0,
  "value_max_daily_exposure_pct": 0.10,
  "conviction_max_daily_exposure_pct": 0.10,
  "model_max_daily_exposure_pct": 0.15
}
```

Editing these doesn't require code changes — change the JSON and restart.

## End-to-end workflow

1. **Predict**: 🚀 click "Run Prediction" (or `./bin/run_predictions.sh`).
   Generates `output/predictions_<date>.csv` with one row per match
   containing 1X2 probs, O/U probs, odds, EV, Kelly, confidence,
   heuristic logs, and league-relative strength.

2. **Generate slip**: 🎰 click "Generate Slip" on the betting page.
   Builds three parallel slips (one per lane) from the predictions
   CSV using each lane's entry filter and stake formula. Renders them
   side-by-side. Optional "session bankroll" overrides let you
   preview a slip with a hypothetical smaller bankroll.

3. **Place bets**: click "Place All Bets" to write the combined slip
   to `output/bets_<date>.json`. Each bet is tagged with its `lane`
   and stamped with a canonical `bet_id`. Stake is deducted from
   each lane's bankroll immediately.

4. **Wait for matches** — kickoff to final whistle.

5. **(Optional) Cash out early**: while a match is live and we have
   live data for it, the dashboard's live row shows a "💰 Cash Out"
   button next to each open bet. Click → confirm → bet flips to
   `CASHED_OUT`, cashout amount credited to the lane bankroll, P/L
   set to `cashout_amount − stake`. **Cashout cascades across lanes**:
   if multiple lanes hold the same wager (e.g., value + model both
   on Over 2.5), one click cashes out every lane's bet with its own
   per-bet payout.

6. **Verify & settle**: ✅ click "Run Verification" (or
   `./bin/run_verification.sh`). Scrapes the match results, settles
   every open bet against the actual outcome, credits winning bets'
   payouts to their respective lane bankrolls.

7. **(Optional) Void postponed matches**: on the betting page, OPEN
   bets get a "⊘ Void" button. Use it for matches that won't ever
   settle (postponed, cancelled, abandoned). Stake refunded to the
   lane. Same cross-lane cascade as cashout.

## Bet statuses

Every bet has a `status` field that progresses through the lifecycle:

- **OPEN** — placed but match hasn't finished yet. Eligible for cashout.
  If the verification CSV doesn't have the match's result yet (still
  in-play), the bet **stays OPEN** — settlement is idempotent and you
  can re-run verification later.
- **WON** / **LOST** — set by verification once the match result is
  in. `pnl` is set to `payout − stake` or `−stake`. Re-running
  verification on an already-settled bet leaves it alone (no
  double-credit).
- **VOID** — match canceled / postponed / abandoned. Set via the ⊘
  Void button. Stake is refunded to the lane bankroll, `pnl = 0`.
- **CASHED_OUT** — bet manually cashed out via the 💰 Cash Out
  button. Carries `cashout_amount`, `cashout_profit`,
  `cashout_timestamp`. Bankroll is credited at cashout time, not at
  settlement.

## Strategy Comparison Table

The header of `/football/betting` shows the **Strategy Comparison**
table — one row per lane, aggregated across every slip (active + archived):

| Column | What it means |
|---|---|
| **Bets** | Total bets placed in this lane |
| **Settled / Won / Lost / Void / Cashed Out** | Resolution counts per status |
| **Win %** | `won / (won + lost)` — voids and cashouts don't count |
| **Stake** | Total currency staked |
| **Returned** | Total currency returned (winnings + voided refunds + cashout amounts) |
| **Net P/L** | Currency delta. Era-dependent — use ROI for comparison |
| **ROI %** | `Net P/L / Stake` — the apples-to-apples lane comparator |

**Use ROI %, not absolute P/L**, to compare lanes — absolute P/L
depends on how big the bankroll was when each lane started.

For a cross-sport view, the landing page (`/`) shows a Portfolio
Summary with the same columns aggregated per sport plus a Total row.

## The cashout fair-value formula

When a match is live and you have an open bet on it, the dashboard's
live row shows an "Est. cashout €X.XX". This is our internal estimate
of what the bet's worth right now, computed as:

```
fair_cashout = stake × odds × adj_prob × 0.95
```

- `stake` and `odds` are the bet's locked-in values.
- `adj_prob` is the model's *adjusted* probability of the bet winning
  given the current live state (score, minute, xG, possession, etc.).
- The `× 0.95` is a haircut accounting for bookmaker margin (an estimate
  — real-bookmaker cashout offers may differ).

The state badge next to the amount tells you the model's read. The decision is
driven by `adj_prob` (the live-adjusted win probability) and is suppressed
before **minute 30** (in-play stats aren't reliable earlier):

- 🟢 **Lock-in** — bet is in profit AND (`adj_prob ≥ 0.85` near-certain — odds-
  independent — **OR** `fair_cashout / stake ≥ 1.5` big unrealized profit).
- 🔴 **Stop-loss** — `adj_prob < 0.20` (model thinks the bet's likely lost).
- 🟡 **Hold** — neither — let the match play out.

> The probability branch (`adj_prob ≥ 0.85`) exists because `fair/stake` is
> capped by the odds — a ≥1.5× rule alone could never lock in a low-odds bet
> even at near-certain win (e.g. €2 @1.40 maxes at 1.33×).

### Manual vs auto-cashout

- **Manual:** the 💰 **Cash Out** button on a live row — you decide when to click.
- **Auto-cashout** (the dashboard **Auto-cashout** checkbox): arms a
  **server-side, autonomous** loop that fires the *same* decision rule above
  automatically every 10 minutes — locking in or stopping out OPEN bets without
  you watching. It runs even with **no browser tab open** (the arming state is
  persisted server-side), and is **Flashscore-only / virtual money only — no
  real bet is placed**. It prices at the estimated fair value above (not a real
  bookmaker offer), so it's testing cashout *timing*, not real economics. Every
  evaluation (fired or held) is logged for later threshold tuning.

## Soft delete and archived slips

The 📁 **Archive** button on each CLOSED slip moves it to
`output/history/`. The slip disappears from the visible list but its
bets **still count toward the Strategy Comparison table totals** (the
aggregator reads `output/` + `output/history/`).

OPEN slips can't be archived — the archive button is hidden because
settlement only looks in `output/`, so archiving early would orphan
the slip.

To un-archive: `mv output/history/bets_<date>.json output/`.

## When predictions are missing

If today's predictions CSV is deleted but you still have open bets
on today's matches:

- **Live snapshot** still works — it derives the matches to scrape
  from your open bets' `match_id` fields and synthesizes minimal
  pre-match probabilities from each bet's odds. Matches with
  synthesized priors get a `🛈 priors from odds` chip in the UI.
- **Verification** still works — `bin/run_verification.sh` falls
  back to bet-derived match IDs, scrapes Flashscore, settles bets.
  The prediction-vs-results accuracy report is skipped (nothing
  to compare against), but bets settle normally.

This is the point of having a canonical `bet_id` per wager: the
betting system survives missing predictions because the bets
themselves carry enough info.
