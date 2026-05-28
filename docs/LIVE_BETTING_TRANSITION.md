# Live Betting Transition — Design Doc

**Status**: forward-looking design, ratified 2026-05-20.
**Lifecycle**: this file exists to bridge the gap between Phase 7
(manual cashout, virtual mode) and the eventual live-betting integration.
**Delete it** once the migration is complete and `/football/live/` is
shipping in production.

## Premise

Today we have **one** betting surface — virtual, internal bankroll,
predictions feed our `output/bets_<date>.json`. When the real-betting
integration matures (Pamestoixima Step 9 in `FOOTBALL_NEXT_STEPS.md`), we want
**two** parallel surfaces:

- `/football/` — virtual betting (today, unchanged)
- `/football/live/` — live betting against the bookmaker

Both surfaces render the **same templates**. They differ in which
backend they're talking to. The interface that hides that difference
is `BettingBackend`. This document is the contract.

## Why URL separation, not a mode toggle

Physical URL separation is a deliberate safety design, not a style
choice. Two reasons:

1. **Mistake-cost asymmetry.** Clicking "Place Bet" while you think
   you're in virtual mode but actually in live mode is a real-money
   mistake. URL separation makes the mode unambiguous.
2. **Different data sources entirely.** A mode toggle on one page would
   mean every component does `if mode == 'live' else virtual`. That
   pattern ages badly. Different backends keep call sites clean.

## v1 Contract — `BettingBackend`

```python
# web_ui/betting_backend.py (lands with Phase 7, Option C)
from abc import ABC, abstractmethod
from typing import Optional

class BettingBackend(ABC):
    """Provides the operations a betting UI needs: balance read,
    bet placement, cashout, settlement. Selected by URL prefix:
    `/football/*` → VirtualBettingBackend
    `/football/live/*` → PamestoiximaBackend (Phase 9)

    All methods may raise on hard failure. Soft-failure indicators
    (e.g. "no cashout available") use Optional[T] returning None.
    Atomicity guarantees are per-method below.
    """

    # ---- balance ----------------------------------------------------

    @abstractmethod
    def get_balance(self, lane: Optional[str] = None) -> float:
        """Return current EUR balance. With `lane`, return that lane's
        bankroll; without, the sport-wide total.

        Virtual: read from data_sets/betting_config.json via sports_config.
        Live: read from Pamestoixima.get_balance(). The `lane` argument
              is IGNORED for live mode and always returns the total
              account balance — bookmakers don't have separate
              per-lane bankrolls. Decided 2026-05-20.
        """

    # ---- place ------------------------------------------------------

    @abstractmethod
    def place_bet(
        self,
        date: str,            # 'YYYY-MM-DD' the match is played on
        lane: str,            # 'value' | 'conviction' | 'model'
        match: str,           # 'Home vs Away'
        match_id: str,        # source identifier (Flashscore / bookmaker)
        type: str,            # '1X2' | 'O/U'
        selection: str,       # 'Home' | 'Draw' | 'Away' | 'Over' | 'Under'
        odds: float,
        stake_eur: float,
        meta: Optional[dict] = None,   # e.g. conf, ev, kelly — pass-through
    ) -> str:
        """Place a bet. Returns a bet identifier (slip path for virtual,
        bookmaker bet ID for live). Persists the bet record locally for
        later reconciliation. Debits the lane bankroll where applicable.

        ATOMICITY: must be all-or-nothing. If the local record write
        fails after a live bookmaker placement, the implementation must
        either retry the local write until success or undo the
        placement on the bookmaker side. Partial state is a bug.

        Virtual: appends to output/bets_<date>.json, debits lane via
                 sports_config.update_bankroll(-stake_eur).
        Live: drives the bookmaker browser session to place the bet
              (see PAMESTOIXIMA_NOTES.md for selectors), then writes
              the local mirror record. Bookmaker auto-debits balance.
        """

    # ---- cashout ----------------------------------------------------

    @abstractmethod
    def get_cashout_amount(
        self,
        bet: dict,            # one entry from bets_<date>.json['bets']
        live_match: Optional[dict],  # the matching live_data.json row, or None
    ) -> Optional[float]:
        """Return the EUR amount we'd receive if we cashed out this bet
        right now. None if cashout is unavailable (e.g. match not live,
        bookmaker offer suspended).

        Virtual: fair-value estimate = stake × odds × adj_prob × 0.95.
                 Requires `live_match` with adjusted probs.
        Live: scrape the bookmaker's actual cashout offer for this bet.
              The `live_match` is informational, not used to compute the
              value; the bookmaker owns that number.
        """

    @abstractmethod
    def execute_cashout(
        self,
        bet: dict,
        live_match: Optional[dict],
    ) -> bool:
        """Commit the cashout. Returns True on success. On success, the
        bet's `status` becomes 'CASHED_OUT' and `cashout_amount`,
        `cashout_profit`, `cashout_timestamp` fields are populated
        (Phase 3 schema — already wired through settlement, aggregation,
        and UI rendering).

        ATOMICITY: same as place_bet. Live: if the bookmaker confirms
        the cashout but our local write fails, we must reconcile —
        currently we'd surface to the user and require manual fixup.

        Virtual: re-reads cashout amount, credits lane bankroll, writes
                 the slip JSON.
        Live: drives the bookmaker browser session to accept the cashout
              offer, captures confirmation, writes the local mirror.
        """

    # ---- settle -----------------------------------------------------

    @abstractmethod
    def settle_bets(self, date: str, verification_data: object) -> dict:
        """Settle all open bets for `date` against the match result.
        Returns a summary dict (total_pnl, total_return, lane breakdowns).

        SAME behaviour for both backends. Decided 2026-05-20: rather
        than reconciling live bets against the bookmaker's bet history
        (which is brittle, Pamestoixima-specific scraping), both modes
        just compute P/L from the verification CSV's match result.
        CASHED_OUT bets are skipped in both modes (their P/L was set
        at cashout time). The only mode-specific behaviour is bankroll
        credit:
          Virtual: credits lane bankrolls in betting_config.json.
          Live: no-op for bankroll (bookmaker auto-settled it on their
                side; our local mirror just records the outcome).

        For live mode, this means our 'settled P/L' is what WOULD have
        happened on the bookmaker side — accurate for non-cashed-out
        bets, since the bookmaker uses the same match result.
        """
```

## Call sites to refactor at Phase 9 time

When `PamestoiximaBackend` is ready and we wire up `/football/live/`,
the existing `/football/` routes need to switch from direct
`sports_config` calls to going through `backend.method()`. Below is
the explicit table — line numbers were accurate at 2026-05-20; re-grep
if drift is suspected.

| Today's code (file:line) | Phase 9 replacement |
|---|---|
| `web_ui/app.py:14-19` — direct `sports_config` imports | Keep — VirtualBettingBackend wraps these |
| `web_ui/app.py:347` `process_bet_verification(...)` | → `backend.settle_bets(date, df_verify)` |
| `web_ui/app.py:450,452` `update_bankroll/get_bankroll` inside settlement | Moves inside `VirtualBettingBackend.settle_bets` |
| `web_ui/app.py:845` `def place_bets():` | Body delegates to `backend.place_bet(...)` per bet |
| `web_ui/app.py:872,883` `lane_bankrolls`/`update_bankroll` inside `/place_bets` | Moves inside `VirtualBettingBackend.place_bet` |
| `web_ui/app.py:1024` `lane_br = lane_bankrolls('football')` | → `backend.get_balance(lane=...)` per lane (or `backend.lane_balances()` if we extend the interface) |
| `web_ui/app.py:1148` `def auto_wager():` | Calls `backend.get_balance(lane)` to size stakes |
| `web_ui/app.py:1173` `lane_br = lane_bankrolls('football')` | → `backend.get_balance(...)` |
| Live row cashout display (Phase 6 partial) | Uses `backend.get_cashout_amount(bet, live_match)` |
| Phase 7 cashout endpoint | Uses `backend.execute_cashout(bet, live_match)` |

**Sites that stay unchanged:**
- `web_ui/sports_config.py` — storage layer for virtual mode, called only by `VirtualBettingBackend`
- `data_sets/betting_config.json` — schema unchanged
- All scrapers, ML pipeline, calibration code, live_data.json pipeline

## URL routing pattern

```python
# web_ui/app.py
from .betting_backend import VirtualBettingBackend, PamestoiximaBackend

football_bp.before_request(lambda: setattr(g, 'backend', VirtualBettingBackend()))

# At Phase 9 time, register the parallel blueprint:
football_live_bp = Blueprint('football_live', __name__)
football_live_bp.before_request(lambda: setattr(g, 'backend', PamestoiximaBackend()))
# Reuse the same route bodies — register both blueprints against the same
# view functions if possible, or replicate routes with `backend = g.backend`.
app.register_blueprint(football_live_bp, url_prefix='/football/live')
```

`before_request` injection keeps view functions clean — they read
`g.backend` and don't care which mode they're in. The `/football/live/`
blueprint is **disabled by default** until policy explicitly flips
(via a config flag in `data_sets/betting_config.json`):

```json
"live_betting_enabled": false
```

When false, the blueprint isn't registered → 404 at `/football/live/`.

## Schema change — `mode` field

Every bet gets a `mode` field at creation time:

```python
{
  "lane": "value",
  "mode": "virtual",         # NEW. 'virtual' | 'live'
  "bet_id": "...",           # NEW. See "Bet ID format" below.
  "match": "...",
  "selection": "...",
  ...
}
```

**Backward compatibility**: missing `mode` defaults to `'virtual'` in
every read path. Existing `bets_*.json` files don't need migration —
they'll be treated as virtual and won't carry a `bet_id` until they're
re-saved by a Phase 7+ code path.

**Filtering**: `compute_sport_summary`, `process_bet_verification`,
the dashboard "Recent Bets" table all filter by mode where they read.
The portfolio summary on the landing page **does NOT cross-aggregate**
virtual + live (decided 2026-05-20). Each mode has its own
bet-analysis table; if you want to see both, you switch surfaces.

## Bet ID format

A stable, mode-agnostic identifier for any bet — same string across
virtual and live for the same conceptual wager. Decided 2026-05-20.

```
bet_id = "<date>:<home>:<away>:<type>:<selection>"
```

- `<date>`: ISO `YYYY-MM-DD`
- `<home>`, `<away>`: canonical team names from `entity_resolver`,
  lowercased, non-alphanumerics replaced with `_`
- `<type>`: `1x2` or `ou`
- `<selection>`: lowercased, non-alphanumerics replaced with `_`

Example:
```
2026-05-20:sc_freiburg:aston_villa:o_u:over_2_5
```
(The `O/U` type slug becomes `o_u` because `/` is non-alphanumeric and
slugifies to `_`. Same uniform rule for every component — no special-
casing.)

**Properties:**
- **Stable across modes**: the same wager has the same ID whether
  placed virtually or on the bookmaker.
- **Stable across runs**: `entity_resolver` canonicalises team names,
  so source-of-name differences (Flashscore vs football-data vs
  Pamestoixima) don't break ID equality.
- **Slug-safe**: all characters are URL-safe → can appear in
  `/football/cashout/<bet_id>` directly.
- **Self-documenting**: a human can read the ID and know what bet it
  refers to without looking it up.

**Uniqueness within a slip**: the bet_id identifies a *conceptual
wager*, NOT a unique storage record. Multiple bet records can share
the same bet_id when the same wager exists in different lanes (e.g.
value AND model lane both betting Over 2.5 on the same match). This
is by design — **cashout and void cascade across all OPEN sibling
bets with the same bet_id**, so a single user click settles every
lane's stake on that wager. The lane is stored as a separate field
on each bet record. Decided 2026-05-21 after observing that lane-
scoped cashouts were confusing (user thinks "I cashed out Freiburg
Over 2.5" but only one lane's share was actually settled).

**Pamestoixima reconciliation**: at Phase 9 time, Pamestoixima bet
records will carry their own internal IDs. We don't store those as
the primary ID; we store them in a `bookmaker_bet_id` field for
cashout / settlement reconciliation. Our `bet_id` stays the canonical
internal handle.

**Generation site**: a single helper in `web_ui/betting_backend.py`:

```python
def make_bet_id(date: str, home: str, away: str,
                bet_type: str, selection: str) -> str:
    """Canonical bet ID. Stable across virtual and live modes."""
    def slug(s):
        return ''.join(c if c.isalnum() else '_' for c in s.lower()).strip('_')
    return f"{date}:{slug(home)}:{slug(away)}:{bet_type.lower()}:{slug(selection)}"
```

Used by:
- `BettingBackend.place_bet` — stamps the ID on the record.
- The cashout endpoint URL — `/football/cashout/<bet_id>` (Phase 7).
- Audit records in `placement_record.json` (already in use by the
  dry-run script; will become structured at Phase 9).

## Template impact

`dashboard.html`, `betting.html`, `live_analysis.html` stay shared
between the two surfaces. Small additions:

1. **Mode label** in the navbar/header — colored chip showing
   `Virtual` (blue) or `Live` (red/orange) so the user always knows
   which surface they're on. Sourced from `g.mode` (set alongside
   `g.backend`).
2. **Cashout confirmation dialog** — wording diverges by backend:
   - Virtual: "Cash out at €X.XX? Bet becomes CASHED_OUT, €X.XX credited to value bankroll."
   - Live: "Cash out at €X.XX (bookmaker offer)? This will execute the cashout on Pamestoixima."
3. **Place Bet confirmation** (live only) — extra dialog step quoting
   the stake and warning that real money will move.

The template fragment for the cashout button + state badge is shared
between `dashboard.html` (compact, inline on live row) and
`live_analysis.html` (full-width, dedicated cashout panel). One Jinja
include, two render contexts — landed as part of Phase 7's UI work.

## Open questions to revisit at Phase 9 design time

Two genuine unknowns remain. Four other questions were resolved on
2026-05-20 and folded into the contract above (Bet ID format,
settlement reconciliation, cross-mode portfolio, `get_balance(lane)`
for live mode).

1. **`place_bet` recovery semantics.** What if the bookmaker confirms
   placement but our local write fails? Options:
   - Retry local write until success (with a bounded timeout).
   - Treat as "placed but unrecorded" — fetch from bookmaker bet history
     and reconcile on next settlement (but per the settlement decision
     above, we don't reconcile against bookmaker history — so this
     option requires extra work just for this case).
   - Refuse and surface to user (most conservative).
   The v1 contract says "all-or-nothing" but doesn't pick a mechanism.
   Pick one when implementing `PamestoiximaBackend.place_bet`.

2. **Browser session lifetime for live.** Today's dry-run script
   creates a session per run. A live-betting UI needs the browser
   alive for the duration of the user's interaction — open in
   background, ready to fire cashouts on click. How that maps to
   Flask's request lifecycle is genuinely tricky (Flask is
   request-scoped; Playwright sessions are long-lived). Plan to
   revisit when the live-betting page is actually built. Likely
   answer: a small process manager that keeps a single browser
   session warm and serializes requests through it.

### Resolved (decided 2026-05-20)

- ✅ **Bet ID format** — see "Bet ID format" section. Common mapping
  across virtual + live.
- ✅ **Settlement reconciliation** — both modes compute P/L from
  match result. No bookmaker history sync. See `settle_bets` contract.
- ✅ **Cross-mode portfolio view** — per-mode tables only, no
  aggregation on landing page.
- ✅ **`get_balance(lane)` for live mode** — ignore lane, return total.
- ✅ **`live_betting_enabled` flag** — defaults `false`. Flipped only
  by explicit policy decision, documented in FOOTBALL_NEXT_STEPS at flip time.

## Migration order

The work falls in phases. **Do not start until Pamestoixima Step 9
re-evaluation per `FOOTBALL_NEXT_STEPS.md` is approved.**

1. **Implement `PamestoiximaBackend` methods** one by one, smoke-tested
   against the live site. Probably 2-4 weeks. Each method needs its own
   selector exploration; `PAMESTOIXIMA_NOTES.md` is the starting point
   for selectors. Expect anti-bot iteration.

2. **Refactor virtual-side call sites** to go through `g.backend.method()`.
   The table above is the explicit checklist. Each refactor commit
   should change ONE call site and be smoke-tested via the manual
   prediction→verification cycle before moving to the next. No single
   commit should touch more than one or two routes — keep diff hygiene
   tight because there are no automated tests.

3. **Register `/football/live/` blueprint** with `PamestoiximaBackend`
   injection. Behind the `live_betting_enabled` config flag.

4. **Add `mode` field** to new bets. Templates start surfacing the mode
   label.

5. **Cross-mode portfolio polish** — landing page surfaces virtual vs
   live as separate rows. Test with both populated.

6. **Headed-mode validation** for a week, then optionally flip to
   headless (Pamestoixima Step 8, currently dormant — likely stays
   dormant for live betting).

7. **Delete this doc.** When everything above ships, this file has
   served its purpose. The contract lives in `web_ui/betting_backend.py`
   docstrings; the call sites are the implementation. This bridge
   doc gets removed.

## Cross-references

- `FOOTBALL_NEXT_STEPS.md` "Real betting integration — Pamestoixima (DORMANT)"
  section. Step 9 (bet placement) is the trigger for starting Phase 9.
- `real_betting/PAMESTOIXIMA_NOTES.md` — DOM selectors, anti-patterns,
  end-to-end placement flow checklist from the 2026-05-20 validation run.
- `real_betting/dryrun_freiburg_villa.py` — concrete reference code for
  the placement flow. Throwaway script kept for selector reference.
- `CLAUDE.md` §4 "Real-betting integration" — repo-level pointer.
- `CLAUDE.md` Web UI section — "Bet-status taxonomy" subsection covers
  the CASHED_OUT schema this design relies on.

## Status notes

- **Phase 3 (CASHED_OUT schema)** — ✅ done 2026-05-20. Settlement,
  aggregation, and UI rendering all handle CASHED_OUT correctly.
  This is the foundation Phase 7 + Phase 9 cashout work builds on.
- **Phase 7 (manual cashout endpoint)** — ⏸ pending. Will land Option C:
  `BettingBackend` ABC + `VirtualBettingBackend` with `get_cashout_amount` /
  `execute_cashout` implemented properly; other methods are thin wrappers
  around existing call sites. `PamestoiximaBackend` ships as a stub.
- **Pamestoixima Step 9** — DORMANT. Triggers the work in this doc.
