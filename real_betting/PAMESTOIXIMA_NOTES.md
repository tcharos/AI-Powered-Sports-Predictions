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

### 2026-05-25 — Fixture-listing page selectors confirmed

Discovered while live-testing `real_betting/discover_fixtures.py`:

- **Football landing URL with sport ID**: `/en/sport/football/11` (the
  `/11` is Pamestoixima's internal football sport ID — visible in the
  site nav alongside `/5` for basketball, `/12` for tennis, etc.).
  **HOWEVER**: as of 2026-05-25 this URL renders the sports-nav skeleton
  without any fixture rows (event-box-root count = 0 even after scroll).
  Either it requires a date / category filter click, or fixtures are
  hydrated via a websocket subscription that arrives after our
  `wait_for_load_state('networkidle')`. Treat as "not currently viable".

- **24-hour coupon entry — works**: `/en/next24hCoupon`. Lists every
  football fixture kicking off within the next 24h. 9 rows on the
  sample run (Norwegian + Danish + English + Faroese leagues); listing
  is virtualised but scroll-until-stable settles in 3 passes. The
  trade-off is the rolling 24h window — fixtures further out won't
  appear here. For broader discovery, the calendar page
  (`/en/calendar`) or per-league pages
  (`/en/football/<league-slug>`) are the next places to try.

- **Per-fixture container**: `[class*="event-box-root" i]` (confirmed
  stable; same class used on the match-detail page per earlier notes).

- **Working per-fixture selectors** (verified from dumped DOM):
  - Home team: `.homeTeam` (or `.team.homeTeam`). Yields the
    bookmaker's display name with correct caps — e.g. `IK Start`,
    `Valerenga IF`, `FK Arendal`. Better than slug-derived names
    which can't reproduce genuine acronyms.
  - Away team: `.awayTeam` (or `.team.awayTeam`).
  - Wrapper: `.teams`.
  - Kickoff: `.event-box-eventDate time`. Renders as `Today 15:30`,
    `Tomorrow 18:00`, or absolute `DD/MM HH:MM`. Preserve as-is —
    relative vs absolute format is meaningful downstream.
  - League header: `.event-box-sportCompetitionName`. Multi-span:
    `<span>Norway</span><span>-</span><span>Eliteserien</span>` →
    innerText `Norway-Eliteserien`.

- **Fixture URL pattern (canonical)**:
  `/en/football/<league-slug>/<home-slug>-v-<away-slug>/<match-id>`
  — the `-v-` separator is always present. Splitting `teams_slug`
  on the *first* `-v-` (not any `-`) preserves multi-word names
  containing dashes like `rb-leipzig`.

- **Lookup helper validated**: `find_fixture_url('IK Start', 'Valerenga')`
  returns the right record with score 100; `find_fixture_url(
  'Notts County', 'Salford')` returns Notts County vs Salford City
  with score 100 even with "City" omitted (fuzz.token_set_ratio is
  the right comparator). Reversed home/away correctly returns None
  — the helper requires home-to-home and away-to-away alignment.

### 2026-05-25 — Successful scenario #5 batch placement

Both bets from scenario #5 in `test_case_scenarios.md` placed end-to-end
via `real_betting/dryrun_batch_placement.py`:

- Paderborn vs Wolfsburg, O/U Over 2.5, €2 @ 1.94 — run `batch_placement_20260525-141135`.
- Sandefjord vs Fredrikstad, O/U Over 2.5, €2 @ 1.65 — run `batch_placement_20260525-142011`.

The first run halted on the false slip-empty failure (described above);
the second run used the corrected placement-receipt selector and
succeeded cleanly. Balance trail: €19.67 → €17.67 → €15.67, both
Δ exactly €2.00.

Used **hardcoded `match_url` constants** in the BETS list. The
discoverer wiring landed later the same day — future batch runs can
omit `match_url` on a BETS entry to trigger `find_fixture_url(home,
away)` against `output/real_betting/fixtures_<today>.json`.

### 2026-05-25 — Open-bets scraper working (scenario #3B)

`real_betting/read_open_bets.py` driven against the user's two real
OPEN bets. Confirmed selectors / patterns:

- Row container: `li[id^="my-bets-O-"]`. The id payload is the
  Pamestoixima UUID — stable identifier per bet.
- Anchor inside the row carries the canonical match URL with
  Pamestoixima's own 8-digit numeric `match_id`. Pattern:
  `/en/football/<league>/<home>-v-<away>/<match-id>` or the
  `/en/live/football/...` variant for in-play matches.
- Cashout offer button: `button.full-cashout-root`. Text format:
  `"Cash Out\n€X.XX"` pre-click. Parse `€\s*([\d.,]+)` for the value.
- Disabled state markers: `disabled` attribute, `aria-disabled="true"`,
  or class containing `disabled|paused`. Treat any of those as
  `paused: true` and emit `cashout_offer: null`.
- Selection text: `.selectionName` (e.g. `"Over 2.5"`).

**Important ID-scheme gotcha (verified live)**: Pamestoixima's
`match_id` is **completely different** from Flashscore's. Examples:

| Match | Flashscore match_id | Pamestoixima match_id |
| ----- | ------------------- | --------------------- |
| Paderborn vs Wolfsburg | `nFjvRRsQ` | `11012505` |
| Sandefjord vs Fredrikstad | `CCgR4LMj` | `10595954` |

The bookmaker-offer consumer (`_load_bookmaker_offers` in
`web_ui/app.py`) tries direct `match_id` lookup first (almost never
fires) and falls back to **fuzzy team-name match via
rapidfuzz** (`_match_offer_by_teams`) with a min-score-80 floor on
the worse of the two names. That's the path that actually surfaces
offers on the dashboard today.

**Deferred extraction issues** (informational fields only, not
consumed by the join — not blocking):

- `market` extracts as `"Single\n2.00€"` (the bet-type / return
  cell) rather than the actual market name like `"Total Goals
  Over/Under"`. Selector for `.marketName`/`betTypeName` doesn't
  match this page layout.
- `odds` parsed as `null` — the regex `(?:@|odds[:\s]+)([\d.,]+)`
  doesn't match how Pamestoixima renders odds on My Bets rows.

Both fix targets are in the dumped HTML at
`output/real_betting/open_bets_read_<ts>/02_my_bets_page.html` if/when
someone iterates.

### 2026-05-25 — Headless is blocked by Akamai Bot Manager (ROOT CAUSE)

Tested headless twice. First pass reported only "Could not find the
Login button" — symptom, not cause. Second pass dumped the actual
page headless Chromium receives, which settled it:

```
title: Access Denied
You don't have permission to access "http://www.pamestoixima.gr/en/"
on this server. Reference #18.4b173317...
https://errors.edgesuite.net/18.4b173317...
```

- **300-char "Access Denied" page** served by **Akamai** (the
  `errors.edgesuite.net` host + `#18.<hex>...` reference are Akamai
  Bot Manager's block signature). EVERY element counts 0 — including
  `#quick_login_login` — because **no real page is served at all**.
- This is a **network-edge block**, not a selector / hydration issue.
  Akamai refuses the request before any HTML renders. Headed Chromium
  passes Akamai's checks (TLS fingerprint, header order, behavioural
  signals); headless does not.
- **playwright-stealth does NOT defeat this.** It patches JS-level
  fingerprints (`navigator.webdriver`, plugins, WebGL, etc.) which
  is enough for `bot.sannysoft.com`, but Akamai also fingerprints the
  network/TLS layer and headless-specific runtime signals that stealth
  doesn't touch.

**Conclusion**: headless is NOT viable for Pamestoixima without
escalating to a C++-fingerprint-patched browser (CloakBrowser /
patchright — see "Optional escalation" in NEXT_STEPS). Per that note,
do NOT adopt preemptively: we don't *need* headless — headed mode
works for the on-demand ~25 s scrape. This is the concrete evidence
that real-betting step 6d ("headless validation") cannot pass on the
current stealth stack; revisit only if a headless requirement becomes
unavoidable.

Practical consequence: the "Refresh Live Snapshot" button chains the
bookmaker scrape **only on manual click** (`?with_bookmaker=1`).
Auto-5m stays Flashscore-only — both because popping a Chromium window
every 5 min is bad UX AND because a headless silent refresh is
impossible (Akamai block). Evidence dumps:
`output/real_betting/headless_test_<ts>/01_homepage.html` (gitignored).

### 2026-05-25 — `🔗 linked` badge surfaces the join

`_attach_open_bets` now always loads the bookmaker snapshot
(independent of `cashout_source` flag) so the link existence can be
surfaced even when the displayed value is synthetic. Each enriched
bet record carries `linked_to_bookmaker: bool` and
`pamestoixima_uuid: str|null`. The dashboard fragment shows a green
`🔗 linked` chip next to the lane badge when linked, with the UUID
in the tooltip. The standalone `/football/live_analysis` page filters
to **only** linked-bet matches — "skin in the game" view.

## File pointers

- Working bookmaker implementation: `real_betting/bookmakers/pamestoixima.py`
- Session manager + stealth setup: `real_betting/session.py`
- Single-bet placement (2026-05-20): `real_betting/dryrun_freiburg_villa.py`
- Cashout discovery + commit (2026-05-22): `real_betting/dryrun_cashout_discovery.py`
- Batch placement (2026-05-25, scenario #5): `real_betting/dryrun_batch_placement.py`
- Fixture discoverer + lookup helper (live-validated 2026-05-25): `real_betting/discover_fixtures.py`
- Open-bets scraper (live-validated 2026-05-25, scenario #3B): `real_betting/read_open_bets.py`
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
