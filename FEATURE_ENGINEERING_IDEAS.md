# D2 — Feature Engineering Brainstorm

Living scratchpad for the D2 feature-engineering pass referenced in `NEXT_STEPS.md`.
Goal: get a candidate list down on paper, then pick 2–3 to implement and measure against the current model's OOF Brier per league.

Status: brainstorm. Nothing here is committed work.

## Ground rules

- Every candidate must be derivable from data we already have, OR have a concrete plan for where the new data comes from. No "we'd need a Premier League injury feed" hand-waves without an answer.
- Every candidate has to respect the no-leakage rule (use only data strictly before the match's kickoff). The existing `_add_ppg_strength_features` is the template — copy that shape.
- Acceptance per feature: ≥1% Brier improvement on TimeSeriesSplit OOF (the same harness `train_model.py` uses), measured per market (1X2 + O/U). Anything under 1% is noise on our sample sizes.
- Order of attack: cheapest data + biggest expected mechanism first.

## Current feature inventory (for reference)

So we don't re-invent what's already there. From `ml_project/feature_engineering.py`:

- **Market**: implied probs `IP_H/D/A` from Bet365 (falls back to AvgC/MaxC).
- **Strength**: `H_elo`, `A_elo`, `elo_diff`, `abs_elo_diff`.
- **Rolling form (last-5, all venues)**: pts, gf, ga, O/U rate, form-string, shots-for/against, corners-for/against — both sides.
- **Venue-specific form (last-5)**: home team's last-5 *at home*, away team's last-5 *away*.
- **Season-to-date**: `H_ppg`, `A_ppg`, `ppg_diff`, `abs_ppg_diff`, `H_att`, `A_att`, `H_def`, `A_def`, `att_def_diff` (all normalised vs league running average).
- **League**: `league_cat` categorical.

Everything below is additive to that.

## Tier 1 — Cheap (data already in `data_sets/MatchHistory/` CSVs)

Pure compute work on data we already ingest. Each is 1–2 days end-to-end including the OOF eval.

### 1.1 Variable rolling windows per league
**What**: replace the fixed `window=5` with a per-league window (e.g. EPL=5, Brazilian Serie A=8, Veikkausliiga=3).
**Why**: leagues with shorter seasons and higher variance need narrower windows to stay reactive; stable leagues benefit from more history. Currently we use the same window everywhere, which is a compromise that's optimal nowhere.
**Cost**: small. Add a league→window map (start by tuning empirically — fit Brier on a held-out split for window ∈ {3,5,7,10} per league, pick the argmin).
**Expected lift**: 1–3% Brier on small/volatile leagues; rounding error on EPL/Bundesliga.
**Risk**: if we tune the window on the same data we evaluate on, we'll over-fit. Need an outer CV layer.

### 1.2 Recency-weighted form (exponential decay) — ❌ TESTED & ROLLED BACK (2026-05-25)
**What**: replace `np.mean(last_5)` with an exponentially weighted mean — most recent game weighted heaviest. Half-life as a hyperparam (e.g. 3 games).
**Why**: a 5-0 win last week tells you more than a 5-0 win two months ago. The flat mean treats them identically.
**Cost**: trivial — one-line change in `_get_stats_from_history`.
**Expected lift (doc, pre-test)**: 1–2% Brier broadly; bigger on leagues where form fluctuates fast.
**Empirical result (post-test)**: **no measurable lift, globally or per-league**. Rolled back.

**Test setup (2026-05-25)**: built `_weighted_mean(values, half_life=3.0)` helper + 8 new training features (`H_form_{pts,gf,ga,ou}_w`, `A_form_*_w`). Half-life 3 games — most recent match weight 1.0, 3 matches back ½, 6 matches back ¼. Inference path re-used the same helper so train/serve produced identical values. Retrained the full pipeline; calibrators refit; calibration validate passed at 85.7% (full) / 89.2% (minimal) — same band as the pre-D2.1.2 baseline.

**Empirical findings**:
- **Global CV Brier (1X2)**: 5-fold mean 0.6011 (pre) → 0.6012 (post). Δ = +0.00008. Well within fold-to-fold noise (±0.003).
- **Per-league ablation** (`scripts/sweep_d212_per_league.py`, 21 leagues with n≥100, 11 685 matches):

| | count |
|---|---:|
| Leagues helped (Δ < −0.001) | **0** |
| Leagues hurt (Δ > +0.001) | **0** |
| Best per-league Δ | D1: −0.0006 (−0.10%) |
| Worst per-league Δ | F2: +0.0005 (+0.07%) |
| Weighted-mean Δ | **−0.0000 (−0.01%)** |

Every per-league delta is within ±0.0006 — noise. Even Serie B (I2, supposedly volatile) only showed Δ=−0.0003.

**Why D2.1.2 didn't help (best hypothesis)**: the model already has plenty of recency signal — ELO updates by goal margin (naturally recency-weighted); season-to-date PPG / attack / defense strength features carry recent trend; the flat L5 mean is already "recent" by construction. The weighted variants were collinear enough that XGBoost found no orthogonal signal to split on.

**Rollback (2026-05-25)**: removed `_weighted_mean`, removed `form_*_w` keys from `_get_stats_from_history`, removed 8 `_w` features from `train_model.common_features`, stripped `_w` wiring from `predict_matches.py`. The currently-deployed model (trained earlier the same day) still has the 8 `_w` columns in its `features_*.json`; predict_matches' existing defensive `if c not in input_df.columns: input_df[c] = 0` block handles them as zero-defaults until the next retrain produces a model trained without them. The ablation script `scripts/sweep_d212_per_league.py` is kept — it's a reusable tool for future feature-engineering decisions.

### 1.3 Opponent-adjusted form — ❌ TESTED & ROLLED BACK (2026-05-20)

**Verdict**: Implemented Flavours A (weighted form) + B (xPts residual) as 6 new features (3 per flavour incl. diffs). Trained + ablated with TimeSeriesSplit 5-fold CV against the production hyperparams. **Global Brier deltas were all within ±0.03%** for both 1X2 and O/U markets. **Per-league breakdown across 21 leagues** (n ≥ 100 OOF samples) found **zero leagues improving by ≥1% in any of the 3 variants × 2 markets = 6 column combinations**. Worst per-league delta was D2 OU at +0.26%; best was T1 1X2 at -0.15%. No consistent pattern across variants — clear noise.

**Why the null**: XGBoost already has `H_elo`, `A_elo`, `elo_diff`, `H_att`, `A_def`, and the form features as direct inputs. The "weight points by opponent strength" and "actual minus expected points" transformations are exactly the kind of interaction XGBoost discovers natively through tree splits. We hand-crafted features the model was already computing internally.

**Rolled back**: features removed from `feature_engineering.py`, `train_model.py`, `predict_matches.py`. Model retrained on the 42-feature baseline. **Kept**: the HFA fix in `elo_engine.py` (foundational correctness, not part of §1.3).

**Don't re-attempt** without a different mechanism. If we want to revisit opponent-adjustment, the next angle is **Flavour C (xGD-like)** which uses `H_att × A_def × league_avg_gf` — the goal-magnitude residual rather than the points residual — but only if there's a reason to believe XGBoost can't already discover this interaction either.

Original brainstorm preserved below for context.

**What**: each "form point" gets weighted by opponent strength. A win vs a top-3 team contributes more than a win vs a relegation candidate.
**Why**: "5 in a row vs bottom half" and "5 in a row vs top half" look identical to the model today. The H_att / A_def features capture *who's playing now* but not *who they beat to look this good*.
**Cost**: medium. Need to look up each opponent's running ELO (or PPG) as of the match date, then weight `form_pts` by `opp_elo / league_avg_elo`. Be careful with the no-leakage requirement — use the opponent's ELO *as of that historical match*, not their current ELO.
**Expected lift**: 2–4% Brier on 1X2. This is the most likely big winner of Tier 1.
**Risk**: complexity in the rolling computation. Easy to introduce subtle leakage.

#### 1.3.x Flavour breakdown
"Opponent-adjusted form" is a family of approaches, not one. Worth picking the flavour before writing code. All four use the same fact: `H_elo` and `A_elo` are already snapshotted on every historical row at pre-match time by `elo_engine.process_history`, so opponent strength at the time of each historical match is a free lookup with no leakage.

**Flavour A — Weighted rolling mean**
For each of the last-N matches, weight the contribution by opponent strength:
```
form_pts_w = sum(pts_i × w_i) / sum(w_i)
where w_i = opp_elo_i / league_avg_elo   (clipped to e.g. [0.5, 2.0])
```
- Simplest. Same input data, different aggregation.
- Pro: trivially understandable, easy to ablate.
- Con: a draw vs a top side still gets only 1 point of credit × a weight — doesn't capture "you held a much stronger team to a draw" properly.

**Flavour B — Expected-points residual (xPts)**
For each historical match, compute what was *expected* from the ELO diff at that point (with home-field bump), then store the residual:
```
exp_win = 1 / (1 + 10^(-(own_elo - opp_elo + HFA)/400))
exp_pts ≈ 3·exp_win + 1·(1 − exp_win)·draw_rate
form_xpts = mean(actual_pts − exp_pts)
```
A team with `form_xpts = +0.4` is consistently over-performing; much stronger signal than raw form because it controls for schedule.
- Pro: cleaner signal, matches how analysts already think ("xPts above expectation").
- Con: more moving parts (draw-rate assumption, HFA constant), one extra failure mode if ELO is wrong at that snapshot — see ELO audit below.

**Flavour C — Goal-based version (xGD-like)**
Same idea as B but for goals:
```
exp_gf = H_att × A_def × league_avg_gf   (we already compute these!)
form_gf_above_expectation = mean(actual_gf − exp_gf)
```
Targets the O/U model specifically. Pairs naturally with existing `H_att` / `A_def` features.
- Pro: feeds the O/U market, which is the weaker of our two markets.
- Con: noisier than xPts because goal counts have more variance per match.

**Flavour D — All three, as additive features**
Keep existing unweighted features unchanged, add A + B + C as *new* columns. Let XGBoost decide which signal to lean on.
- Pro: zero regression risk, full attribution at ablation time.
- Con: ~6–8 new features, mild collinearity, more retrain time.

#### 1.3.y Watch-outs that apply to all flavours
1. **Use opponent ELO at the time of that match** — `H_elo`/`A_elo` snapshotted on every row by the ELO engine. No leakage, no extra lookup.
2. **Cross-league matches** (e.g. Champions League games mixed into Premier League form). Decide whether to filter to same-league only or trust absolute ELO scale. Current `_calculate_rolling` doesn't filter, so we inherit this either way.
3. **Cold-start at season-open**: first 2–3 matches have unstable opponent-ELO snapshots. Cap or fall back to unweighted when N < 3.
4. **Normalisation midpoint**: 1500 is arbitrary. Better to use a per-league running mean ELO — but we don't compute one yet. Easy to add (same shape as `league_avg_gf`).
5. **ELO quality** — these features inherit any biases in the ELO computation itself. Audit findings below.

#### 1.3.z Recommended path
**Flavour D first, ablate after** — marginal cost of computing all three at once is small (one extra pass over the same rows in `_get_stats_from_history`), and we get clean attribution at ablation time. Drop the dead-weight features afterward. Alternative if we want maximum simplicity: **Flavour B alone** — xPts residual is the highest-signal single feature in the family.

### 1.4 Promoted-team indicator
**What**: binary flag `is_promoted` for teams in their first season of a league.
**Why**: promoted teams are systematically mispriced — both by bookmakers and by ELO (which carries no league-rank prior). The model usually treats them as "weak team in current league" once ELO catches up, but the first ~10 matches of the season are noisy.
**Cost**: small. Derive from history: a team is promoted into league X in season Y if its previous season was in a different league. Add the inference alongside data loading.
**Expected lift**: 2–4% Brier on the affected matches (a fraction of the dataset), so the global lift is more modest — maybe 0.5–1%.
**Risk**: same-name teams across divisions / promotions across confederations need careful handling. `entity_resolver.py` covers most.

### 1.5 Rest days / fixture congestion — ❌ TESTED & ROLLED BACK (2026-05-26)
**What**: `H_rest_days`, `A_rest_days` = days since each team's previous match. Also `rest_diff = H - A`.
**Why**: well-documented effect — three games in a week vs a fully-rested opponent matters. Currently invisible to the model.
**Cost**: small. Pure date arithmetic on existing match history.
**Expected lift (doc, pre-test)**: 1–2% Brier, concentrated on midweek fixtures and post-cup matchdays.
**Empirical result (post-test)**: **no measurable lift, anywhere** — including on the congested subsets the doc predicted it would help. Rolled back.

**Test setup**: `H_rest_days`/`A_rest_days`/`rest_diff` built in `_add_rest_days` (cap/sentinel 14), train + serve, added to `common_features`. Same per-league + subset OOF 1X2 Brier ablation used to kill §1.2.

**Findings** (5-fold OOF, 11 685 matches):
- Global weighted-mean Brier delta (with rest − without): **+0.0001 (+0.01%)** — flat, within noise. 1 league helped, 1 hurt, 19 neutral.
- **Subset test (the concentrated-effect hypothesis)** — even where rest *should* matter most:
  - `|rest_diff| ≥ 3` (n=1049): +0.0002
  - `|rest_diff| ≥ 4` (n=522): +0.0001
  - either team rest ≤ 3 / congested (n=1482): −0.0002
  - both teams rested (n=8180): +0.0001
  All within noise. The concentrated effect didn't show.

**Why it didn't help (strategic insight — applies to §1.2 too)**: the model's strongest features are the **bookmaker implied probabilities** (`IP_H/D/A` from B365 odds). The betting market already prices rest / congestion (it knows the fixture calendar), so feeding the model a raw version of a signal the odds already encode gives XGBoost nothing orthogonal. Two cheap features now rolled back (§1.2 recency form, §1.5 rest) both plausibly for this reason.
**Implication for the rest of D2**: candidates the market *also* efficiently prices (promoted-team, H2H, manager-change) are likely to come up similarly flat. The real headroom is either (a) signals the market prices *inefficiently* (rare/hard-to-quantify — injuries to specific key players, lineup leaks), or (b) dropping the odds-dependence so the model must learn fundamentals (a bigger architectural choice, overlaps with D3). Worth weighing before spending more days on Tier-1 date/form features.
**Risk**: low. Just be sure to skip the first matchday of each season cleanly (no prior match → use a sentinel like 14 days).

### 1.6 Head-to-head specifics (last 2–3 H2H)
**What**: features from the last 2–3 meetings between these specific teams: H2H goals avg, H2H result, H2H total goals.
**Why**: rivalries / stylistic matchups produce repeatable patterns the global model misses ("these two always draw," "this fixture is always 3+ goals").
**Cost**: medium. Need to build a lookup of past meetings per (home_team, away_team) ordered pair; respect the date filter.
**Expected lift**: 1–2% Brier. Smaller than opponent-adjusted form, but a free additive on top.
**Risk**: only ~1–2 H2H matches per team-pair per season, so the feature is sparse and noisy. Consider falling back to a league-wide draw rate when there's < 2 H2H samples.

### 1.7 Goal-difference / scoring-trend features
**What**: rate of change of `form_gf` and `form_ga` — i.e. is the team trending up or down? Compute as `recent_3_avg − previous_5_avg`.
**Why**: captures a "heating up" / "cooling off" signal that the current heuristic adjuster has, but in feature space where the model can use it natively rather than as a post-hoc nudge.
**Cost**: small. Two extra rolling computations.
**Expected lift**: probably 0.5–1%. The heuristic already does some of this in `heuristic_adjuster.py`.
**Risk**: overlaps with the heuristic. If this works, the H4/H5 heuristic deltas should be reduced or removed (cleaner factoring).

## Tier 2 — Medium (we scrape it but don't store / use it for training)

Each is 2–5 days because we have to add a data column to the historical pipeline first.

### 2.1 Matchday number / season progress
**What**: `matchday` (1..38 or whatever the league uses) and `season_progress` (matchday / total_matches_in_season). Optionally `is_endgame` flag for the last ~5 matches.
**Why**: late-season dynamics differ — relegation battles play differently than mid-table dead rubbers. Currently invisible.
**Cost**: medium. Derive from standings spider output or match ordering within season. The standings spider already has matchday info.
**Expected lift**: 1–2% Brier on end-of-season matches; modest globally.

### 2.2 Standings-derived features (table position, points gap)
**What**: `H_position`, `A_position`, `position_diff`, `points_gap_to_top`, `points_gap_to_safety`.
**Why**: ELO and PPG capture strength but not *standings context*. A 7th-vs-8th match has different stakes than 1st-vs-2nd even with similar PPG diffs.
**Cost**: medium. Standings JSON is current-only — we'd need to either reconstruct historical standings from results (doable but tedious) or limit this feature to inference-time only (asymmetric — train without it, predict with it: doesn't work).
**Expected lift**: 1–3% Brier. The "stakes" signal is real but hard to extract without genuine motivational state (which we don't have).
**Risk**: data plumbing is the bottleneck. The asymmetry above means we'd need to compute *historical* standings from `MatchHistory/` CSVs at training time — non-trivial, but a one-off script.

### 2.3 Cup-involvement / fixture-density indicator
**What**: flag for "team played a cup match midweek" — proxy for tired legs, rotated lineup.
**Why**: explains a lot of upset results that look random to a league-only model.
**Cost**: medium-high. football-data.co.uk CSVs are league-only; we'd need an extra data source (or cross-reference Flashscore for cup matches). May not be worth it pre-D3.
**Expected lift**: 1–2% Brier on the ~20% of matches with a midweek cup tie nearby.

## Tier 3 — Expensive (new data acquisition required)

These are 1–4 week projects on the *data* side before any modelling starts. Listed for completeness; not recommended until Tier 1 is exhausted.

- **Manager-change indicator**: well-documented signal but we have no manager-tenure feed. Would need to either scrape Wikipedia/Transfermarkt or hand-curate.
- **Lineup / availability**: key player out is highly predictive; lineup data is the single biggest "easy" feature we don't have. Sources exist (e.g. Sofascore, official club sites) but scraping is brittle.
- **Bookmaker line movement**: line drift in the 24h before kickoff carries signal. Requires us to start logging odds snapshots over time — a 6-month data wait before useful.
- **Public expected lineup sentiment**: media / news features. Adds noise as often as signal. Skip.

## Cross-cutting considerations

- **Interactions**: once we have 5+ new base features, consider explicit interaction terms (e.g. `elo_diff × season_progress`, `rest_diff × is_endgame`). XGBoost discovers interactions natively but engineered ones can help on small-data leagues.
- **Re-tune after**: any meaningful feature addition invalidates the D1 hyperparameter set somewhat. Plan to re-run `tune_model.py` after Tier 1 completes, not after every individual feature.
- **Re-calibrate after**: same applies to the per-league Platt calibrators. The C5 pipeline already chains fit + validate after retrain, so this is automatic — but the *first* run with new features should be eyeballed.
- **Backward compatibility**: existing models on disk use the current feature list (`models/features_*.json`). Adding features is a hard break — old models will fail to predict. Bump model file naming or accept that retrain is required.

## Proposed first cut

**Updated 2026-05-20** after §1.3 ablation came back null. Key lesson from that exercise: XGBoost is good at discovering interactions between features it already has. Hand-engineered transformations of existing inputs (weighting one feature by another, computing residuals against another) are unlikely to add signal. Future candidates should add information the model **cannot derive from current features**, not just re-express it.

Updated ranking:

1. **1.5 Rest days** — adds information the model genuinely doesn't have (fixture timing). Trivial cost.
2. **1.4 Promoted-team indicator** — also adds new information (squad-discontinuity signal). Cheap to derive.
3. **1.2 Recency-weighted form** — replaces an aggregation, doesn't add information. Lower expected value but trivial cost. Test second.
4. **1.1 Per-league window size** — same caveat as 1.2, plus a tuning loop. Lower priority.

**Skip permanently**:
- 1.3 (opponent-adjusted form) — tested, null. See section above.
- 1.6 (H2H) and 1.7 (trends) — overlap with existing features and heuristics. Same XGBoost-already-knows risk as 1.3.

## ELO audit (prerequisite for Flavours B / C of §1.3)

Triggered by the opponent-adjusted-form work: every flavour above leans on `H_elo`/`A_elo` snapshots being accurate. Auditing `ml_project/elo_engine.py` + the `train_model.py` integration turned up a mix of confirmed-correct and questionable-correct behaviour.

### Verified correct

- **Per-match snapshot is pre-match.** `process_history` records `H_elo`/`A_elo` *before* calling `update_ratings`. No leakage — when we look up opponent ELO in `_get_stats_from_history`, we get their rating as of that match. ✅
- **Global date sort.** Matches are processed in chronological order across all leagues. ✅
- **Predict-time ELO matches train-time semantics.** `data_sets/elo_ratings.json` holds the final state and is loaded at predict time, which is the correct "current pre-match" rating for tomorrow's fixtures. ✅
- **Goal-difference K-multiplier follows World Football Elo Ratings conventions** (G=1 / 1.5 / (11+N)/8). ✅
- **Buffer dating mitigates cold-start.** ELO is computed on full history from ~2010; training filter starts 2020-01-01. By then teams have ~30–50 matches of history, so initial-1500-noise has washed out for most teams. ✅

### Fixed (2026-05-20)

- **✅ Home-field advantage in `expected_result`.** `EloTracker.__init__` now takes `hfa=100`; `update_ratings` applies HFA to the home team's *effective* rating for the expected-result calculation (`expected_result(r_home + hfa, r_away)`). Stored `H_elo`/`A_elo` remain the intrinsic team ratings — no HFA baked in — so `elo_diff` semantics are unchanged downstream. Verified: home expected vs equal opponent now = 0.6401 (matches World Football Elo Ratings reference). Deployment requires a retrain (`./bin/retrain_pipeline.sh`) — current `H_elo`/`A_elo` snapshots in the trained model were computed without HFA.
- **✅ NaN match handling.** `process_history` now skips rows where `FTHG` or `FTAG` is NaN instead of silently treating them as home losses.

### Still open

- **No seasonal regression to the mean.** ELO carries forward unchanged across season boundaries. Standard practice is to pull ratings ~1/3 toward 1500 (or toward the league mean) between seasons because roster changes invalidate prior signal — promoted teams especially. Impact: end-of-season ELO is over-confident going into a new season; ~10–20 matches into the new season the system catches up.

- **Cross-league mixing without baseline.** All leagues share one pool. A 1500 Premier League team is rated identically to a 1500 Greek SL team. Mostly self-corrects via European competition results, but the lower-tier baseline is biased upward (their cross-league losses haven't propagated far). Probably not worth fixing — adding per-league offsets is a real project.

- **K-factor not validated.** K=20 is defensible (World Football Elo uses 20 for non-tournament league play) but never tuned for our data mix.

### Recommendation

HFA + NaN guard are in. Before §1.3 starts, run `./bin/retrain_pipeline.sh` so the model and calibrators are re-fit against the corrected ELO. Seasonal regression-to-mean and per-league offsets stay deferred — neither blocks D2.

## Open questions before starting

- Do we want each feature merged independently (so we can attribute Brier delta to each), or all three at once (faster, less attribution)? Independent is more rigorous but ~3× the dev time.
- Where does the OOF eval live? `train_model.py` has CV but no per-feature ablation harness. Might be worth writing a tiny `scripts/ablate_features.py` that loops a list of feature sets and prints Brier per league per market.
- What's the bar for "this feature stays"? Suggest: ≥1% global Brier improvement AND ≥0% on every major league (no league regresses meaningfully). Stricter than just "global wins" — avoids globally-useful-but-locally-harmful features.
