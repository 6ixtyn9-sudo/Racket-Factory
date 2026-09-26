# What Racket Factory should take from Edge Factory

**Date:** 2026-09-26
**Subject repo:** `6ixtyn9-sudo/Edge-Factory` @ `27d8211` (football sibling, ~32k LOC Python, 47 test modules, 493KB HANDOVER)
**Compared against:** `6ixtyn9-sudo/Racket-Factory` @ `2289065` (~11k LOC in `src/`, 33 test modules)

---

## 0. The one-sentence version

Edge Factory and Racket Factory are the same machine, but Edge has an **evidence layer** bolted underneath the betting layer — a replay harness, a decay circuit-breaker, a pre-registered evidence report, and tripwires that close doors that stop paying. Racket has the betting layer and a self-monitoring ML module, but **no instrument that can tell it whether the thing it is betting is real**. That is why Racket's ledger currently reads `bank 130.2%` while its own audit file says the picks it makes have an ROI of **−14.4%**.

---

## 1. Facts first: what Racket's own ledgers say today

Straight out of `localdata/picks_audit_rolling.json` and `auto_tickets_performance.txt`, not from anything I computed:

| measure | value | source |
|---|---|---|
| settled priced picks | 61 | `overall.priced_picks` |
| hit rate | **70.9%** | `overall.hit_rate` |
| **flat ROI on those picks** | **−14.4%** | `overall.roi_real` |
| bank | 130.2% (from 100%) | `auto_tickets_performance.txt` |
| REAL accas settled | 12W / 13L over **10 bet-days** | same |

ROI by bucket — every single door is negative:

| bucket | hit rate | ROI |
|---|---|---|
| CERTIFIED_CLEAN | 66.7% | **−13.2%** |
| WATCHLIST | 45.5% | **−22.0%** |
| SKIPPED_DEAD_EDGE | 59.1% | −22.2% |
| SKIPPED_VETO *(refused bets)* | 78.9% | **−1.7%** |

ROI by tour: ATP −25.6%, CHALLENGER −20.3%, WTA −7.4%. By surface: Hard −12.6%, Grass −23.7%.

And by price band (`auto_tickets_performance.txt`, staked legs only):

| band | n | hit% | breakeven% | spread |
|---|---|---|---|---|
| <1.30 | 12 | 91.7% | 80.5% | +11.2pp |
| **1.30–1.59** | **32** | **53.1%** | **69.9%** | **−16.8pp** |
| 1.60+ | 6 | 83.3% | 48.7% | +34.6pp |

**Read this carefully.** The bucket labels have no discriminative power — the bucket the engine *refuses to bet* (SKIPPED_VETO) is the best-performing one. The band the engine lives in (1.30–1.59, n=32, the majority of staked legs) is 16.8 points below breakeven. The bank at 130% is 25 parlays of variance, not edge. Edge Factory ran exactly this review on itself on 2026-09-23 and wrote the same conclusion about its own numbers (HANDOVER line 8787): *"selection quality is real but thin … the parlay structure doubles the bleed vs singles; staking turned variance into the −31% drawdown."*

Racket has no equivalent moment on record because it has no instrument that produces one.

---

## 2. The structural difference that causes all the others

### Edge: one selection function, every knob a parameter

```python
# Edge scripts/auto_tickets.py:1908
def select_accas(pool, *, floor=None, rank="prob", rank_caps=None, pairing=None,
                 max_accas=None, legs_per_acca=None, volume_pool=None,
                 volume_min=None, gate_mode=None, fallback=True,
                 saturated_accas=None, min_accas=None, fixture_report=None):
    """THE selection recipe — one code path for live and for the replay harness."""
    floor = MIN_LEG_ODDS if floor is None else floor
    ...
```

Every constant defaults to the live value; the harness passes overrides. That single design choice is what makes `scripts/replay_harness.py` (2,859 lines) possible, and the harness docstring says why it had to be that way:

> *"There is no re-implementation of the recipe in this file — the 2026-09-04 audit found two parity bugs and one no-op A/B caused by exactly that duplication. One code path, or the harness lies."*

### Racket: constants baked in, gates scattered, no seam

```python
# Racket scripts/auto_tickets.py:430
def build_accas(pool):   # no parameters at all
```

The gates live in three places with hardcoded numbers: `is_playable()` (lines 283–387), the `eligible_pool()` closure inside `build_accas` (lines 563–645), and module constants (53–155). There is **no way to ask "what would yesterday's card have looked like at MIN_ACCA_ODDS=2.0?"** without editing the file and re-running the pipeline.

Which is why Racket's constants are justified like this:

```python
MIN_ACCA_ODDS = 1.5   # Lowered from 2.0 to 1.5 to allow user's winning accas: 1.34*1.22=1.63 ...
MAX_ACCA_ODDS = 4.0   # CAP acca odds: user complained 8.06/7.13 high, winners were 1.28-1.88 ...
# USER FIX: those odds are high (7.45) – prioritize high prob favorites like user's winning tickets
```

Eight such anecdote-anchored constants in one file. Edge's are justified like this:

```python
STAKE_FRAC = 1.0 / 4.0
#  (2026-09-04 sizing audit: on the 52-day replay the growth-optimal fraction is ~40%,
#   and the growth curve is FLAT from 30-50% while max drawdown climbs 62% -> 87%.
#   50% was past the peak: LOWER growth AND higher risk. 1/3 keeps 96% of peak growth
#   at 67% DD. Raising the fraction stays rejected — 75% and 100% bust.)
```

…and Edge still later published an addendum titled *"STAKE_FRAC's own justification no longer reproduces"* (HANDOVER 8465). That is the culture difference: Edge audits its own receipts.

---

## 3. Capability gap table

| capability | Edge Factory | Racket Factory | impact |
|---|---|---|---|
| Walk-forward train/valid split on edge certification | `config.py GATES` (min_n_train=340, min_n_valid=120, min_roi_valid≥0, split 2025-06-01), enforced in `mine_consensus.evaluate` | **none** — `grep min_n_train` returns nothing; `mine_edges.py` assays full history and emits | **critical** |
| Counterfactual replay of the live engine | `replay_harness.py`, paired bootstrap, NO-OP detection, log-growth-per-bet-day metric | none | **critical** |
| Decay circuit-breaker / auto-bench | `decay_monitor.py` + `assay.decay_verdict/should_bench` (HEALTHY/WATCH/DECAYING/DEAD) | none — `ml.py` vetoes contexts but nothing benches a rule | **critical** |
| Pre-registered evidence report (pool vs ranking vs buckets, bootstrap CIs, required-n) | `scripts/evidence.py` | none | high |
| Bucket P&L tripwire (demote ×0.5 at 2-day bleed, bench at 4) | `auto_tickets.compute_bucket_pnl` + `auto_tickets_bucket_pnl.json` | none | high |
| Slice tripwire w/ hysteresis + rank caps | `compute_bucket_slice`, `auto_tickets_slice_ledger.jsonl` | Racket writes `slice_table_*.json` but **nothing consumes it as policy** | high |
| Price quarantine / execution-safety bit | `price_push_eligible`, `WATCHLIST_UNCORROBORATED_PRICE`, `WATCHLIST_SUSPECT_PRICE`, `suspect_price`, `BAD_QUARANTINE={alias_fuzzy,suspect}` | `_TRUSTED_MARKET_SOURCES` is a flat allow-list; BetExplorer consensus is staked as a real price | high |
| Identity fold at the identity seam | `identity.py` (deterministic folds + evidence-seeded aliases, "FOLDS ONLY, NO FUZZ") | `entities.py` has `fuzzy_match_players()` at ratio **> 0.75** — pure fuzz | high |
| Shadow notification slate | second WhatsApp with all non-pushed streams, each labelled with its own 30d audit record | none | medium |
| Firing tripwire (rule silent / source stale) | `edge_firing_tripwire.py` | partial — `doctor.py` covers freshness, not rule firing | medium |
| `localdata` retention policy | `clean_localdata.py --keep-days 30`, runs first in `daily.py` | none; 129 tracked files and growing ~2/day | medium |
| Regime tagging of picks | none | **`regime.py` — Racket is ahead here** | — |
| API quota guard / fetch cache | ad-hoc | **`quota_guard.py`, `fetch_cache.py` — Racket is ahead** | — |
| Pipeline health doctor | scattered | **`doctor.py` + `pipeline_health.json` — Racket is ahead** | — |

---

## 4. The five things I'd actually do, in order

### #1 — Stop mining ROI on closing odds. This is a live contamination.

`scripts/mine_edges.py` line 5 carries this warning, written by whoever built it, and never resolved:

> *"WARNING: ROI is currently calculated using Market Closing Odds. AI predictions captured early in the day must be evaluated against Opening Odds before live capital is deployed."*

Today's card shows the consequence directly. Every WATCHLIST pick in `picks_2026-09-26.json` carries `roi_estimate: "21.85%"` and `slice_winrate: "82.86%"`. The realised ROI of that machinery is **−14.4%**. A +21.85% slice estimate computed on closing prices is not a forecast for a bet struck at 08:00 on a consensus quote.

Edge hit the identical wall and refused to cross it (HANDOVER 6842):

> *"The engine bets ~30+ minutes before kickoff at `forebet_best`. BetExplorer is a closing price. Substituting one for the other is not an approximation, it is a different bet, and the harness refuses to mix them."*

**Action:** tag every warehouse odds row with a price-time class (`open` / `pre_match_Xmin` / `closing`) and make `assay_segment` refuse to mix classes. A slice ROI must state which price class produced it. If only closing odds exist for the history, the honest output is "not measurable at bet-time prices" — which is a complete answer, not a failure.

### #2 — Add the price-execution safety bit; demote BetExplorer consensus

Racket currently stakes on BetExplorer *consensus* and says so in a comment (`auto_tickets.py:249`): *"BetExplorer legs quote the bookmaker consensus (indicative, not the ticket price at any single book) — staked because EV gating runs on the same leg."* 18 of today's 26 picks were priced this way; it is effectively the only price source Racket has.

That reasoning is circular: gating on the same fictional price does not make the price executable. Edge's answer is a *separate* bit from bucket quality — `price_push_eligible` — with quarantine buckets so a doubtful price stays visible and audited but can never be printed as a leg.

**Action:**
- add `price_push_eligible: bool` + `price_evidence: {source, method, corroborated_by, divergence}` to every pick row;
- consensus-only quotes → `WATCHLIST_UNCORROBORATED_PRICE`, archived and scored, never staked;
- The Odds API H2H (a real book price) becomes the only push-eligible primary;
- record the *executed* price you actually get on the phone, so within ~30 days you have a measured consensus-vs-executable haircut instead of a guess. Edge's rule of thumb is `BEST_ODDS_HAIRCUT = 0.5` — best odds roughly halve when you bet them for real.

This will cut Racket's playable pool hard in the short term. That is the correct outcome: the pool it currently has is −14.4%.

### #3 — Parameterize `build_accas`, then port the replay harness

This is the enabling refactor. Without it, nothing below can be measured.

```python
# target signature — every current constant becomes a defaulted kwarg
def select_accas(pool, *, min_leg=None, max_leg_fn=None, min_acca=None, max_acca=None,
                 max_accas=None, legs_per_acca=None, rank="prob", pairing="consecutive",
                 min_accas=None, bucket_weights=None, fixture_report=None): ...
```

Move the gates out of `is_playable`/`eligible_pool` into a single declarative `GATES`-style dataclass (mirror `edgefactory/config.py`) so a variant is a dict, not a diff. Then port `replay_harness.py`. The parts worth copying verbatim:

- **mean log growth per bet-day** as the primary metric, not final bank (bank is dominated by one lucky treble);
- **paired bootstrap** — the same resampled day indices scored under both variants. Edge's unpaired version printed a ±22,000% interval for two *identical* variants;
- **NO-OP detection** — diff the two cards day by day first and refuse to bootstrap if the legs are the same;
- **flag every cell with n < 30 as noise.**

Then re-derive Racket's eight anecdotal constants. My prior, given the band table: `MIN_ACCA_ODDS=1.5`, `MAX_ACCA_ODDS=4.0` and the 1.30 leg floor are all fitted to a handful of remembered winning tickets, and the 1.30–1.59 band (−16.8pp, n=32) is where the money goes.

**Also run the singles arm.** Edge measured its parlay structure at −0.0337 log/day vs singles at −0.0171 — *the parlay doubled the bleed*. Racket's `NO SINGLES` rule has never been tested against its alternative, and it is the direct cause of today's NO BET.

### #4 — Port `evidence.py` and pre-register the bar before October

Edge's `evidence.py` separates three things Racket currently confounds into one bank number:

1. **THE POOL** — do the admitted legs beat their own prices? *(Racket: no. −14.4%.)*
2. **THE RANKING** — does the engine's ordering of that pool predict anything? *(Racket: unknown, never measured.)*
3. **THE BUCKETS** — do bucket labels separate winners from losers? *(Racket: no — SKIPPED_VETO is the best bucket.)*

Every section reports the denominator, an 80% bootstrap interval, **and the sample size that would be needed to resolve the effect it just measured**. That last figure is the whole point: with 61 settled priced picks and 25 accas, almost nothing about Racket is currently resolvable, and the report should say so in the same breath as the estimate.

Pre-register the adoption bar now, while nothing is riding on it (Edge uses p10 > 0 on a bootstrap, plus a minimum new-bet-day count). Otherwise the bar gets negotiated after the fact.

### #5 — Tripwires: bucket P&L and slice, with hysteresis

Port `compute_bucket_pnl` more or less as-is — the design is well tested and the parameters are already tuned for a daily-cadence system:

- trailing 21 calendar days of settled **playable legs per bucket** (door-wide, not the shipped-slip slice — the shipped slice never reaches n≥20);
- `gap = realized_hit − stated_prob`, `z = gap / sqrt(Σp(1−p)/n²)`;
- verdicts: `INSUFFICIENT` (n<20, **fail open** — benches are earned by evidence, never by silence), `BLEEDING` (z≤−2.0), `COLD` (gap<0), `PAYING`;
- streak counts **calendar days**, advances at most once per day so intraday reruns can't compound;
- ladder: 2 days bleeding → ×0.5 stake, 4 days → 0.0 (door closed, card rebuilds from survivors); any non-bleeding day resets.

Racket already writes `slice_table_*.json` every day and *nothing reads it as policy*. Wire the same ladder to slices with Edge's hysteresis (`SLICE_DEMOTE_STREAK=2`, `SLICE_BENCH_STREAK=4`, `SLICE_DEMOTE_CAP={"CAUTION":0.70}`).

Critically, keep the replay-purity contract: `plan_day(bucket_weights=None)` must produce byte-identical plans so the harness and backfill are unaffected.

---

## 5. Smaller, cheap wins

- **Kill `fuzzy_match_players()` at ratio 0.75.** `entities.py:50` will happily fuse two different players. Edge deleted exactly this class of bug across three seams in 24h (HANDOVER 8744) and replaced it with deterministic folds plus an *evidence-seeded* alias table — every alias pair proven by a real corpus collision. Tennis is worse than football here: "A. Zverev" vs "M. Zverev", "Cerundolo J.M." vs "Cerundolo F.", doubles pairs like "Halys / Miedler".
- **Shadow slate notification.** Racket sends nothing on NO BET days — the user experience that prompted this review. Edge sends a second, separately-deduped message carrying every non-pushed stream, each labelled with that stream's rolling 30d record. On a day like today that message would have said: *"1 real leg (Cina @2.00, +14.7% EV), 1 late (Cerundolo, 08:00 KO), 13 dead-edge, 8 unpriced — no pair, no card."*
- **`clean_localdata.py --keep-days 30`** run first in `daily.py`. Racket tracks 129 localdata files and grows ~2/day with no pruning; Edge hit GitHub's 1,000-entry listing limit and wrote the retention policy in response. Copy the safety rule too: only prune *known dated telemetry*, never `picks_DATE.json` or printed slips (they seed the replay/audit).
- **Adopt `GATES` as a dataclass** even before certification exists. Having `min_n_train`, `min_recent_n`, `recent_window_days` in one frozen object is what lets `auto_tickets`, the audit and the tripwires share one definition of "recent" instead of three private constants.
- **Run the timing fix from this morning's NO BET.** 08:02 SAST is after the first Asian wave starts. Edge freezes at 09:00 but *builds from 06:00* and measured that 09:00 covers ~94% of leg kickoffs. Racket should start its build at 06:00–06:30.

---

## 6. What NOT to copy

Edge's HANDOVER is unusually honest about its own dead ends. Take these as free lessons:

1. **Do not build a warehouse-reconstruction backtest.** Edge measured the ceiling: of 536 playable legs, **1 (0.2%)** was faithfully reconstructable from its own stored data. `warehouse_replay.py` exists only to *refuse* the replay and print what's missing. Racket's data situation is no better. Replay the **archived pick ledgers** (which Racket has, daily, since 09-12), never a reconstructed warehouse.
2. **Do not trust a variant search winner.** Edge's `search_noise.py` showed that picking the best of 13 variants on ~33 days yields a median +0.037 log/day *from pure noise*, with P(noise ≥ the celebrated +0.041) ≈ 46%. Any tuning sweep Racket runs needs the same null test.
3. **Do not let post-kickoff information into features.** Edge's ml-meta model had `ht_diff`/`ht_total` in its feature set, making every replay of it contaminated by construction. Racket's `_result_sets_home`/`_sets_a` columns sit in the same warehouse as its prediction columns — audit that boundary before building any model on top.
4. **Edge's own bank is not a success story.** +1.3% ROI lifetime over 63 accas, then −31% from peak in four days, and its own gate refuses to adopt anything. Copy the instruments, not the results.

---

## 7. Suggested sequence

| phase | work | unlocks |
|---|---|---|
| **A** (days) | Price-time class on odds rows; `price_push_eligible` + quarantine buckets; shadow-slate notification; `clean_localdata.py`; drop `fuzzy_match_players` | Honest pool; you find out what's actually bettable |
| **B** (1–2 weeks) | Parameterize `select_accas`; `GATES` dataclass; port `replay_harness.py` (log-growth, paired bootstrap, NO-OP guard, n<30 flags) | Every constant becomes testable; singles-vs-parlay answerable |
| **C** | Port `evidence.py`; pre-register the adoption bar; run the null test on any sweep | Pool / ranking / buckets separated; "is the edge real" gets an answer with error bars |
| **D** | Bucket P&L tripwire; slice tripwire with hysteresis; decay monitor + auto-bench | Doors that stop paying close themselves |
| **E** | Walk-forward split in `mine_edges.py` with real train/valid gates | Slices stop being in-sample descriptions and start being certifications |

**Phase A is the one that matters most right now.** Phases B–E are instruments for measuring an edge; Phase A is what determines whether there is one to measure. A −14.4% pool priced off closing quotes will not be rescued by better acca construction.

---

## 8. One honest caveat about all of the above

Racket has **10 bet-days and 25 settled accas**. Nothing in this document can be validated on that sample — including the criticisms. The correct immediate action is not a big rewrite; it is to **stop the two contaminations (closing-odds ROI, unexecutable prices), start recording executed prices, and build the instruments while the sample accumulates.** Edge needed 60 new bet-days before its own gate would consider adopting anything. Racket is not close, and the honest engineering response is to build the measuring equipment now so that the sample, when it arrives, is worth something.
