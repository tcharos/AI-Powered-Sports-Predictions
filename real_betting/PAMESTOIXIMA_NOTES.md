# Pamestoixima (OPAP) — Phase 9 Field Notes

Concrete DOM structures, working selectors, and anti-patterns discovered
while driving the bet-placement flow end-to-end on 2026-05-20 (one real
€10 bet successfully placed on SC Freiburg vs Aston Villa, O/U 2.5 Over).

Captured here so future Phase 9 / generalisation work doesn't have to
re-discover any of it. The throwaway dry-run script
(`real_betting/dryrun_freiburg_villa.py`) was the vehicle; its in-line
comments are also a useful reference but it's a one-shot artifact.

## Stack

- **Frontend**: React + Material-UI (MUI). Class names are auto-generated
  (`css-1jga1zu`, `css-1iqrail`, etc.) and *not stable* — never select on
  them. The Pamestoixima-specific class names (`event-box-root`,
  `outcome-box-root`, `slip-button-root`, `market-box-root`) ARE stable.
- **Localisation**: `/en` URLs serve English UI, but several elements
  still ship Greek text (PriceBoost descriptions, currency symbols,
  some button labels). Always include Greek-fallback selectors.

## End-to-end placement checklist

Confirmed working sequence (each step's selector lives in the section
below). **Do not skip a step or rearrange** — virtualisation /
collapsable sections / click-bubbling all assume this order.

1. **Login** (reuse session if `*.session_state.json` is valid).
2. **Pre-clear slip** — previous failed runs might have left selections.
3. **Direct navigation to match URL** — `/en/football/<league-slug>/<teams-slug>/<match-id>`.
   Do NOT try to use the coupon-page sport tabs; they're session-pinned
   (fresh login defaults to Basketball, etc.) and the page virtualises
   rows aggressively.
4. **Verify match page** via Playwright locator (`get_by_text`), NOT
   `page.content()` — React content often doesn't serialise reliably.
5. **Scroll match page** to render all market sections (MUI lazy-loads).
   Use *both* `page.mouse.wheel`, `keyboard.press('PageDown')`, and
   `window.scrollBy` — different containers respond to different events.
6. **Detect collapsed accordion** by reading the `class` attribute of
   `.MuiCollapse-root` parent; `MuiCollapse-hidden` = closed,
   `MuiCollapse-entered` = open.
7. **Click the section toggle** — `button.event-page-market-box-collapseBtn`
   scoped by the visible label (e.g., `:has-text("Total Goals Over/Under")`).
8. **Wait for `MuiCollapse-entered`** before interacting with anything
   inside.
9. **Click the outcome button** scoped to the right `market-box-root`
   (each O/U line — 0.5, 1.5, 2.5, 3.5… — is a separate sibling box).
10. **Verify the outcome button has class `selected`** post-click. If
    not, the click missed.
11. **Read `.slip-button-root` counter** — should show `(N)` where N≥1.
    Do NOT click anything to "open the slip" — it's already visible
    in a sidebar, and broad `[class*="betslip" i]` selectors will bubble
    clicks back to your outcome button and deselect it.
12. **Fill stake input** via `input_value()`. Strip the currency symbol
    on read-back (Pamestoixima renders `10€`).
13. **Read balance pre-click** for the audit record.
14. **Click Place Bet** — dedicated method bypassing the
    `FORBIDDEN_CLICK_LABELS` safety net (the whole point of having that
    safety net is that the place-bet click is the one place that
    *explicitly* opts out).
15. **Wait for placement-receipt indicator** — the **actual** success
    signal on the current site is the receipt overlay
    (`[class*="placementNotification" i]` /
    `.slip-receipt-header-placementNotification`), NOT an empty slip.
    Pamestoixima keeps the betslip surface alive and renders the
    receipt **over** it, so `.empty-message-betslipEmpty` and the
    `(0)` counter never appear post-success. See the 2026-05-25
    correction note at the bottom of this section.
16. **Read balance post-click** — should be `pre - stake`.
17. **Write audit record** to disk (JSON with timestamp, match, stake,
    balance pre/post, dryrun screenshot dir).

## Working selectors (copy-paste-ready)

### Login + overlays
- Cookie banner: `#onetrust-accept-btn-handler` (stable OneTrust ID).
- Login button: `#quick_login_login` (stable Vue ID on Pamestoixima).
- Post-login indicators (any one = logged in):
  `#logged-in-menu`, `.pli-logged-in`, `.pli-profile__avatar`.
  Do NOT use `.pli-deposit-button` — only renders when balance == 0.
- **Promo / ad modal dismissal**: ESC key first (kills most Vue-mounted
  modals for free), then iterate close-button selectors. See
  `Pamestoixima.PROMO_DISMISS_SELECTORS` in `bookmakers/pamestoixima.py`.

### Direct match navigation
- URL pattern: `/en/<sport>/<league-slug>/<teams-slug>/<match-id>`
- Example: `/en/football/uefa-europa-league/sc-freiburg-v-aston-villa/10889590`
- The `<match-id>` is the canonical Pamestoixima identifier; stable for the lifetime of the fixture.

### Match-page market accordion
Structure (key parts only, whitespace collapsed):
```html
<section class="event-page-market-box-root">
  <div class="event-page-market-box-marketHeader">
    <button class="event-page-market-box-collapseBtn">   <!-- TOGGLE -->
      <div class="marketNameWrapper">
        <h6 class="event-page-market-box-headerLabel">
          <span>Total Goals Over/Under</span>
        </h6>
      </div>
      <button class="event-page-market-box-favoriteBtn">...</button>
      <span class="MuiButton-icon MuiButton-endIcon">
        <svg data-testid="ExpandMoreIcon">...</svg>
      </span>
    </button>
  </div>
  <div class="MuiCollapse-root MuiCollapse-hidden">    <!-- COLLAPSED -->
    ...
    <div class="market-box-root TOTAL_GOALS_OVER/UNDER">
      <button name="Over" col="1" row="1">             <!-- ONE PER LINE -->
        <span class="oddName">
          <span class="name">Over</span>
          <span class="oddLine"> 2.5</span>            <!-- leading space -->
        </span>
        <span class="price">1.92</span>
      </button>
      <button name="Under" col="2" row="1">...</button>
    </div>
    <!-- repeated per line: 0.5, 1.5, 2.5, 3.5, ... -->
  </div>
</section>
```

Selectors:
- Toggle: `button.event-page-market-box-collapseBtn:has-text("Total Goals Over/Under")`
- Collapsed check: read `.MuiCollapse-root:has(.market-box-root[class*="TOTAL_GOALS_OVER"])`'s `class` attribute; substring `MuiCollapse-hidden` means collapsed.
- Expansion confirm: `.MuiCollapse-entered:has(.market-box-root[class*="TOTAL_GOALS_OVER"])` becomes visible.
- **Over 2.5 button** (critical — must filter at parent box level to disambiguate from Over 0.5 / 1.5 / 3.5):
  ```
  .market-box-root[class*="TOTAL_GOALS_OVER"]:has(.oddLine:has-text("2.5"))
   button[name="Over"]
  ```
- Selected-state verification: `.market-box-root[class*="TOTAL_GOALS_OVER"] button.outcome-box-root.selected`

### Bet slip (right sidebar — already visible, no opening required)
- Counter button: `.slip-button-root:has(span:has-text("Betslip")) span:has-text("(")`
  - Reads as `"(0)"` / `"(1)"` etc.
- Slip-empty indicator (pre-placement only): `.empty-message-betslipEmpty` or `body2:has-text("Your betslip is empty")`. Use this for verifying a fresh / cleared slip, NOT as the post-placement success signal — see "Placement success signal" below for the corrected post-Place-Bet check.
- **Placement success signal** (verified 2026-05-25): `[class*="placementNotification" i]` — see corrections note at the bottom of this file.

### Stake input
- Best selector observed: `[class*="stake" i] input`.
- Read-back via `input_value()` returns the rendered string like `"10€"` — strip non-numeric characters before parsing.

### Place Bet button
EN labels first, Greek as fallback. Always `:not([disabled])`:
```
button:has-text("Place Bet"):not([disabled])
button:has-text("PLACE BET"):not([disabled])
button:has-text("Στοιχημάτισε"):not([disabled])
button[class*="placeBet" i]:not([disabled])
```
Also check `aria-disabled="true"` separately (MUI uses both attribute and aria).

### Time-range filter (coupon page, not used in match-direct flow)
MUI Tab elements with this pattern:
```html
<h1 class="MuiButtonBase-root MuiTab-root common-button-filters-buttonFilter [selected]" role="tab">
  <span class="text">All</span>
</h1>
```
Click strategies in order: `get_by_role('tab', name='All', exact=True)`,
`.common-button-filters-buttonFilter:has(span.text:text-is("All"))`,
`[role="tab"]:has(span.text:text-is("All"))`, `span.text:text-is("All")`.

## Anti-patterns (do NOT do these)

1. **`page.content()` substring scans for content existence.** React +
   MUI doesn't serialise reliably. Use Playwright `locator` /
   `get_by_text` — they traverse shadow DOM and auto-wait.

2. **Clicking the match-row anchor on the coupon page.** Pamestoixima
   nests the Match-Result odds buttons *inside* the navigation anchor.
   The button child captures the click and adds an unintended bet.
   Extract `href` from the anchor and `page.goto()` instead.

3. **`window.scrollBy` alone on virtualised pages.** The actual scrollable
   container is usually an inner `overflow:auto` div, not the window.
   Combine `mouse.wheel`, `keyboard.press('PageDown')`, and
   `window.scrollBy` per pass — whichever moves the right container wins.

4. **Broad `[class*="betslip" i]` selectors for "open the slip".** The
   slip is already visible. Clicking a betslip-class element can bubble
   to your just-selected outcome button and deselect it. Step 5 must be
   read-only.

5. **Assuming `name="Over 2.5"` on the outcome button.** It's just
   `name="Over"`; the line value is in a child `.oddLine` span with a
   leading space (`" 2.5"`). Filter at parent `.market-box-root` level
   with `:has(.oddLine:has-text("2.5"))`.

6. **Selecting on auto-generated MUI hash classes** (`css-1jga1zu`,
   `css-1iqrail`, etc.). These can change on any deploy. Use the
   Pamestoixima-namespaced classes (`outcome-box-root`,
   `event-box-root`, etc.) which are stable.

7. **`:text-is("2.5")` against `<span class="oddLine"> 2.5</span>`.**
   Playwright's text normalisation handles leading/trailing whitespace
   inconsistently across versions. Prefer `:has-text("2.5")` (substring,
   robust to whitespace).

8. **Retrying a failed login.** "Humans don't brute-force." One attempt
   per session; on failure, dump artifacts and surface to a human.

## Anti-bot mitigations confirmed working (2026-05-20)

- **playwright-stealth** (`Stealth().apply_stealth_sync(context)`) —
  applied per context. Verified at bot.sannysoft.com (4 passed / 0 failed
  in headless mode during step 3 work).
- **Headed mode** — default through Step 8 of the Pamestoixima checklist.
- **Greek locale + Europe/Athens timezone** — matches a real OPAP user.
- **Randomised 800-2500ms `human_pause()`** between any two browser actions.
- **Single-session lockfile** — prevents concurrent runs from the same machine.
- **No login retry** — one attempt only.
- **Saved storage state** — reuses cookies across runs, falls back to fresh login on rejection.
- **No automation flag** — `--disable-blink-features=AutomationControlled` in launch args.

## Open questions / future work

- **In-play / live URLs**: do pre-match URLs (`/en/football/<league>/<teams>/<id>`)
  redirect to a live-betting view once the match starts? Not tested.
- **Two-step confirmation**: does Pamestoixima show a confirmation modal
  when odds drift between selection and Place-Bet? Not observed in the
  2026-05-20 placement but worth checking under load.
- **Account verification / KYC gates**: untested. The test account
  already had KYC complete; a fresh account might hit a verification
  block at Place Bet.
- **Stake-input minimum**: €10 worked; the minimum stake on Pamestoixima
  for sports is documented as €0.50 but a live test would confirm.
- **Fixture discovery from team names**: separate problem (NEXT_STEPS
  step 6c). The coupon page is the entry point but has the basketball-
  default problem on fresh sessions. Likely path: scrape
  `/en/sport/football/<league-id>` pages directly.
- **My Bets history scraping**: needed for settlement reconciliation
  (NEXT_STEPS step 10). Untouched so far.

## Corrections / lessons learned

### 2026-05-25 — Placement success signal is the receipt, not slip-empty

The original 2026-05-20 step 15 (and the matching selector list entry)
said the post-Place-Bet success indicator is `.empty-message-betslipEmpty`
or the betslip counter dropping back to `(0)`. **This is wrong on the
current Pamestoixima site.** Confirmed during the scenario #5 batch
placement (€2 Paderborn–Wolfsburg O/U Over):

- The first Place Bet click went through (My Bets counter went `(0) → (1)`,
  balance dropped €19.67 → €17.67, the page rendered a success notification),
  but the script timed out waiting for the slip-empty indicator.
- Inspection of the post-click HTML (in `iter_00_slip_did_not_clear.html`)
  showed a `slip-receipt-header-placementNotification` div, plus the strings
  `successfully`, `successful`, and `placed`. The betslip stays alive and
  the receipt is rendered **over** it; `.empty-message-betslipEmpty` never
  appears on the post-placement state.

**Corrected primary success selector**: `[class*="placementNotification" i]`
(or the more specific `.slip-receipt-header-placementNotification`). The
old `(0)` counter and `.empty-message-betslipEmpty` selectors are kept as
secondary fallbacks but rarely fire post-success in practice.

Why the 2026-05-20 Freiburg run didn't surface this: that run was a
single-bet placement followed by `pm.close()` — the timeout-on-empty
behaviour was likely papered over by the test ending immediately after,
or the empty state was caught by a refresh between snapshots. The
batch (multi-bet) flow exposed it because the script depends on the
indicator to progress to the next bet.

### 2026-05-25 — Successful scenario #5 batch placement

Both bets from scenario #5 in `test_case_scenarios.md` placed end-to-end
via `real_betting/dryrun_batch_placement.py`:

- Paderborn vs Wolfsburg, O/U Over 2.5, €2 @ 1.94 — run `batch_placement_20260525-141135`.
- Sandefjord vs Fredrikstad, O/U Over 2.5, €2 @ 1.65 — run `batch_placement_20260525-142011`.

The first run halted on the false slip-empty failure (described above);
the second run used the corrected placement-receipt selector and
succeeded cleanly. Balance trail: €19.67 → €17.67 → €15.67, both
Δ exactly €2.00.

Used **hardcoded `match_url` constants** in the BETS list, not the
new `discover_fixtures.py` discoverer (which is written but not yet
live-validated, and not yet wired into batch placement). Future
batch runs should call `find_fixture_url(home, away)` from
`real_betting/discover_fixtures.py` once the discoverer's selectors
are confirmed against the live Pamestoixima football landing page.

## File pointers

- Working bookmaker implementation: `real_betting/bookmakers/pamestoixima.py`
- Session manager + stealth setup: `real_betting/session.py`
- Single-bet placement (2026-05-20): `real_betting/dryrun_freiburg_villa.py`
- Cashout discovery + commit (2026-05-22): `real_betting/dryrun_cashout_discovery.py`
- Batch placement (2026-05-25, scenario #5): `real_betting/dryrun_batch_placement.py`
- Fixture discoverer + lookup helper (untested live): `real_betting/discover_fixtures.py`
- Audit records of the placements above:
  `output/real_betting/dryrun_freiburg_villa_<ts>/placement_record.json`,
  `output/real_betting/cashout_discovery_<ts>/cashout_placement_record.json`,
  `output/real_betting/batch_placement_<ts>/batch_placement_record.json`
  (all gitignored; live only on the local machine that ran the tests).

## Policy reminder

**`NEXT_STEPS.md`'s "read-only operations only" policy remains the
official stance until mass-production release.** The 2026-05-20 e2e
placement was a per-run override via `EXECUTE_PLACE_BET=True` in the
dryrun script, NOT a policy change. Phase 9 work in the proper
`real_betting/` module should keep `FORBIDDEN_CLICK_LABELS` safety nets
in place by default.
