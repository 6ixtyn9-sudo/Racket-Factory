# Autobets deep dive — measured, not asserted

> **Status:** Parts 1–6 are the pre-fix review (2026-09-26 morning). **Part 7 is the implementation record** — the Tier 0 defects, the Tier 1 stake reduction and the Tier 2 instruments have all shipped, and a sixth defect was found while red-teaming the fix. Read Part 7 before treating anything in Parts 1–6 as live behaviour.

**Date:** 2026-09-26
**Scope:** `scripts/auto_tickets.py` (1,054 lines) + `scripts/auto_tickets_grade.py` (516 lines), measured against `localdata/auto_tickets_state.json`, and compared with Edge Factory's `scripts/auto_tickets.py` (2,639 lines) @ `27d8211`.
**Re-runnable tool:** `PYTHONPATH=src python3 scripts/autobets_forensics.py`

Everything numeric below comes from Racket's own settled slip ledger: **9 bet-days, 25 real accas, 50 staked legs**. Every cell under n=30 is flagged as noise, because it is.

---

## PART 1 — WHAT THE ENGINE ACTUALLY DID

### 1.1 The pool is losing at the prices it records

| | value |
|---|---|
| staked legs | 50 |
| W–L | 33–17 (66.0%) |
| mean price | 1.460 |
| **breakeven needed** | **70.1%** |
| **realised** | **66.0%** |
| **gap** | **−4.1pp** |
| **flat-stake leg ROI** | **−3.88%** (80% CI −13.0%…+5.4%) |

The engine wins two bets in three and still loses money, because it is systematically paying 70c for a 66c outcome.

### 1.2 The bank is variance, and the bootstrap says so out loud

| | value |
|---|---|
| actual parlay P&L | **+30.17 pts** on 305.15 staked (ROI +9.89%) |
| expected P&L if legs are −3.88% and independent | (1−0.0388)² − 1 = −7.61% → **−23.22 pts** |
| **luck component** | **+53.4 pts (+17.5pp of ROI)** |

Bootstrap, resampling legs into the same card shape 20,000 times:

```
median  −4.2 pts      p10  −118.3 pts      p90  +116.5 pts
P(P&L ≥ the actual +30.2) = 0.362
```

**On a 100-point starting bank the 10th percentile is −118 points.** The same engine, same edge, different draw, is bankrupt one run in ten. The realised +30 is a 36th-percentile-or-better outcome from a distribution centred slightly below zero. `bank 130.2%` is not evidence of anything.

### 1.3 Where the money is lost: one price band

| band | n | hit% | breakeven | gap | flat ROI |
|---|---|---|---|---|---|
| 1.00–1.29 | 12 | 91.7% | 80.7% | +10.9pp | **+13.58%** |
| **1.30–1.44** | **18** | 61.1% | 73.3% | −12.2pp | **−16.39%** |
| **1.45–1.59** | **14** | 42.9% | 66.0% | −23.2pp | **−34.21%** |
| 1.60–1.99 | 3 | 100.0% | 52.5% | +47.5pp | +90.33% |
| 2.00+ | 3 | 66.7% | 45.4% | +21.3pp | +48.67% |

**32 of 50 legs (64%) sit in 1.30–1.59, and that zone runs −16% to −34%.** `MIN_ODDS_PER_LEG` is dynamically pinned at 1.35 and `dynamic_max_odds` caps most picks at 1.8 — the engine is *aiming* at its worst band by construction, because the constants were fitted to remembered winning tickets:

```python
# dynamic_max_odds, line 90
- Base 1.8 = user's winning range 1.28-1.88 (stray dogs blocked here)
# line 151
MIN_ACCA_ODDS = 1.5  # Lowered from 2.0 to 1.5 to allow user's winning accas
```

### 1.4 The ML layer's flagship label is anti-predictive

| ml_verdict | n | hit% | breakeven | flat ROI |
|---|---|---|---|---|
| **BOOST** | **30** | 63.3% | 73.0% | **−13.83%** |
| ALLOW | 20 | 70.0% | 65.9% | **+11.05%** |

BOOST is the strongest positive signal the ML layer emits. It unlocks a lower odds floor (`MIN_ODDS_BOOST = 1.15` vs 1.35), overrides VETO buckets, overrides the EV gate, and gets a bonus in `sort_key`. It is the **worst-performing** label in the ledger, by 25 points of ROI.

Permutation test (20,000 shuffles): observed ALLOW−BOOST gap = +24.9%, **p = 0.126**. Not resolvable at n=50 — but the point is not that BOOST is *proven* bad. The point is that after ~10 weeks of live betting there is **zero evidence it is good**, and it is wired into five separate gates as if it were.

### 1.5 Calibration: one band is broken, and it is the band the engine loves

| promised band | n | promised | realised | gap | z | Edge verdict |
|---|---|---|---|---|---|---|
| 0.60–0.65 | 6 | 62.5% | 33.3% | −29.2pp | −1.48 | COLD |
| 0.65–0.70 | 5 | 67.0% | 100.0% | +33.0pp | +1.57 | PAYING |
| **0.70–0.75** | **13** | **73.6%** | **30.8%** | **−42.8pp** | **−3.51** | **BLEEDING** |
| 0.75–1.01 | 24 | 82.0% | 83.3% | +1.4pp | +0.18 | PAYING |
| **ALL** | **50** | **75.0%** | **66.0%** | **−9.0pp** | **−1.50** | **COLD** |

The 0.70–0.75 cell promises 73.6% and delivers 30.8% — **z = −3.51**. Under Edge's bucket tripwire (`z ≤ −2.0` → BLEEDING) that door would be demoted to half stake after two days and **closed after four**. Racket has no such mechanism, so it has been betting this band for weeks.

What's in it? 11 of 13 are `ml_verdict = BOOST`, all priced 1.36–1.57:

```
Laura Samson vs Noemi Basiletti          1.47  cp 0.74  BOOST  LOST
Martyna Kubka vs Berfu Cengiz            1.51  cp 0.74  BOOST  LOST
Valentina Steiner vs Caroline Werner     1.47  cp 0.73  BOOST  LOST
David Jorda Sanchis vs Harry Wendelken   1.36  cp 0.75  BOOST  LOST
Grigor Dimitrov vs Edas Butvilas         1.39  cp 0.75  BOOST  LOST
Matteo Martineau vs Tristan Schoolkate   1.39  cp 0.75  BOOST  LOST
Elena Pridankina vs Ksenia Zaytseva      1.48  cp 0.74  BOOST  LOST
...                                                     4 WON / 9 LOST
```

**This is the machine's core competency, and it is its worst cell.** The 1.30–1.59 band, the BOOST label, and the 0.70–0.75 calibration bucket are largely the same 30 legs viewed three ways.

### 1.6 A live gate is falsified by the data it was built from

```python
min_ev = 0.05 if is_doubles else 0.01   # doubles need 5%
# comment: "doubles 0W/5L -40% need EV>=5%"
```

Measured across the whole ledger:

| | n | hit% | breakeven | flat ROI |
|---|---|---|---|---|
| singles matches | 39 | 64.1% | 70.3% | **−6.90%** |
| **doubles matches** | **11** | **72.7%** | 69.6% | **+6.82%** |

Doubles went 8W–3L and are the only positive match type. The 5× EV penalty was fitted to a 5-bet sample and is now pointing the wrong way. It is still in the code, still suppressing the better cohort.

### 1.7 The ranking works at the top and burns money at the bottom

| slot | n | hit% | flat ROI | P&L |
|---|---|---|---|---|
| 1 | 9 | 77.8% | +79.78% | **+111.83 pts** |
| 2 | 8 | 37.5% | −28.62% | −50.85 pts |
| 3 | 4 | 50.0% | +16.50% | −0.84 pts |
| 4 | 4 | 0.0% | −100.00% | **−29.97 pts** |

Slots 3–4 contributed **−30.8 pts**. `MAX_ACCAS = 4` funds them. Edge moved from 3 to 2 on 2026-09-09 after the equivalent measurement.

### 1.8 Staking: 25% is past the growth peak and doubles the drawdown

Same cards, same results, different fraction:

| frac | final bank | log/day | maxDD |
|---|---|---|---|
| 5% | 111.1% | +0.0117 | 7.8% |
| 10% | 119.7% | +0.0200 | 15.3% |
| 15% | 125.6% | +0.0253 | 22.5% |
| **20%** | **128.5%** | **+0.0279** | **29.4%** |
| **25% (live)** | 128.5% | +0.0279 | **36.1%** |
| 33% | 122.6% | +0.0226 | 46.0% |
| 50% | 90.4% | −0.0112 | 67.6% |

20% and 25% deliver **identical growth**; 25% buys **7 extra points of drawdown for nothing**. This is precisely the shape Edge documented for its own STAKE_FRAC ("the growth curve is FLAT from 30-50% while max drawdown climbs 62% → 87%") and then honoured by cutting to 1/4.

One real day: **2026-09-25 staked 43.4 points and returned 0** — bank 173.6% → 130.2%, −25% in a day.

---

## PART 2 — FIVE DEFECTS IN THE AUTOBETS CODE

### DEFECT 1 — the no-clobber guard is disarmed during the entire build window

`should_write_ticket_files()` exists to enforce: *"An empty regeneration must never blank a ledger that has booked accas (observed 2026-09-12 evening: 1 acca → 0 accas after picks starved)."*

But `main()` does this:

```python
is_frozen = now.hour >= FREEZE_HOUR or now.hour < GENERATE_HOUR_START
...
if not is_frozen and existing_ledger and existing_ledger.get("date") == target_date:
    effective_force = True      # <-- true for EVERY run between 06:00 and 09:00
```

Verified:

```
existing ledger: 1 booked acca
new build produces 0 accas
  should_write_ticket_files(force=False) -> False   guard says DO NOT overwrite
  should_write_ticket_files(force=True)  -> True    ledger gets BLANKED
```

Any second run inside the build window — a retry, a manual kick, the intraday pass — can erase a booked card. The guard that was written after the incident is bypassed on exactly the days it matters.

### DEFECT 2 — builder and grader disagree about what "paper" means

Two definitions, one case apart:

| | rule |
|---|---|
| **build** (`eligible_pool`) | paper if `odds is None` **OR** `_late_start` |
| **settle** (`_leg_odds_trusted`) | paper if `odds_source` missing |

The divergent case is **late + priced**. Verified against today's actual skipped row:

```
leg: _late_start=True, _paper_reason='already started (stale price)', odds_source='BetExplorer'
  auto_tickets       -> routed to PAPER track
  _leg_odds_trusted  -> True   (grader says REAL)
```

`_leg_odds_trusted()` never looks at `_late_start`, `_is_paper`, or `_paper_reason`.

**This is live right now.** The open slip for 2026-09-20 is a paper acca whose two legs are both late-start and both BetExplorer-priced:

```
slip 2026-09-20   acca paper=True  stake_pct=25.0  odds=None
  Angelina Voloshchuk vs Jana Otzipka   1.22  BetExplorer  late=True -> grader_trusts=True
  Semra Aksu vs Ekaterina Yashina       1.18  BetExplorer  late=True -> grader_trusts=True
  => builder says paper=True   grader will say paper=False   *** MISMATCH ***
```

Traced through `settle_open_slips()` branch by branch:

| outcome | branch | effect |
|---|---|---|
| both legs win | `else:` | *"won legs but acca has no valid odds — held open, never pays on fiction"* → **hangs in open_slips forever** |
| a leg loses | `if any LOST` → `acca_return=None` | `paper_unpriced += 1`, `paper_wins += 0` → **booked as a paper LOSS** |

**A mismatched acca can be recorded as a loss but can never be recorded as a win.** The paper track's hit rate is biased downward by construction, and the slip never closes. The real bank survives only because of the unrelated "never pays on fiction" guard — *not* because the paper flag was respected, since the grader discards it.

### DEFECT 3 — paper accas are reconstructed into `open_slips` carrying real stakes

The reconstruction loop copies the ticket file wholesale:

```python
slip = {"date": d, "accas": data.get("accas", []), "staked_pct": data.get("staked_pct", 0), ...}
```

No `paper` filter. Those entries already carry a non-zero `stake_pct` computed from `paper_bank`:

```
auto_tickets_2026-09-13.json   paper accas=4  stake_pcts=[6.25, 6.25, 6.25, 6.25]
auto_tickets_2026-09-20.json   paper accas=1  stake_pcts=[25.0]
```

Real/paper is then re-derived by the broken predicate in Defect 2. The only thing standing between a 25-point paper stake and the real bank is that `build_track` happens to set `odds: None` on paper accas. That is a coincidence of implementation, not a safety property.

### DEFECT 4 — stake is sized against total bank, ignoring committed capital

```python
total_stake = bank * STAKE_FRAC     # bank only drops when a slip SETTLES
```

Edge's model is explicit: *"'bank' alone means total bank, 'free bank' = total bank minus committed (open) stakes."* Racket has no free-bank concept. Two slips are open right now (09-13 and 09-20, both stuck per the grade log), and their stakes are invisible to every subsequent sizing decision. Tennis usually settles same-day so the overlap is normally small — but stuck slips are exactly the case where it isn't, and the engine is blind to it.

### DEFECT 5 — kickoff correctness rests on one unverified assumption with a dated expiry

*Correcting my earlier draft: kickoffs **are** normalised to SAST at ingestion — `src/racketfactory/kickoff.py` converts BetClan and The Odds API properly, and `tests/test_kickoff_tz.py` pins it. Today's Chengdu/Hangzhou times check out as sane.*

The real exposure is narrower and sharper. Every kickoff on today's card comes from BetClan (PredixSport emits `match_time: ""`), and the module's own docstring says:

> *"we assume the measured fixed UTC+1 via the IANA name Etc/GMT-1 … Whether BetClan runs fixed UTC+1 (e.g. WAT) or Europe/London is **unverified**: London leaves BST on **2026-10-25** which would widen the gap to 2h."*

**That is 29 days away.** If BetClan is actually Europe/London, on 2026-10-25 every kickoff in the system silently shifts by an hour, and the 08:00-ish build starts either dropping live picks or — worse — admitting already-started ones. Single source, single assumption, known trigger date, no alarm wired.

Separately, `auto_tickets.parse_kickoff()` **fails open**: a pick with an unparseable kickoff is kept in the pool. Edge deleted the equivalent fail-open path after its incident #6 (a Vancouver fixture staked ~4h45m after it started) and replaced it with a fail-closed guard plus a printed skip census.

---

## PART 3 — WHAT EDGE'S ENGINE DOES THAT RACKET'S DOESN'T

### 3.1 One selection function, every knob a parameter

```python
# Edge auto_tickets.py:1908
def select_accas(pool, *, floor=None, rank="prob", rank_caps=None, pairing=None,
                 max_accas=None, legs_per_acca=None, volume_pool=None,
                 volume_min=None, gate_mode=None, fallback=True,
                 saturated_accas=None, min_accas=None, fixture_report=None):
    """THE selection recipe — one code path for live and for the replay harness."""

# Edge auto_tickets.py:1985
def plan_day(pool, bank_pct, *, stake_frac=None, stake_mode=None,
             stake_per_acca=None, weights=None, bucket_weights=None, **overrides):
```

vs Racket:

```python
def build_accas(pool):    # no parameters; gates hardcoded across three sites
```

Racket's gates live in `is_playable()` (283–387), the `eligible_pool()` closure (563–645), and module constants (53–155). There is no seam to vary.

### 3.2 Mechanisms worth porting, in priority order

| Edge mechanism | what it does | Racket status |
|---|---|---|
| `compute_bucket_pnl` | 21d window, `gap = realised − stated`, `z = gap/√(Σp(1−p)/n²)`; BLEEDING at z≤−2.0; streak 2 → ×0.5 stake, 4 → door closed; INSUFFICIENT (n<20) **fails open** | none |
| `dedup_fixture_legs` | *"Two legs of one match on one card is correlated exposure masquerading as independent bets — a staking error, never an edge"* | `used_matches` covers this ✓ |
| stake cap arithmetic | rounds stakes to 4dp then **subtracts the excess** so a weighted card can't exceed the promised day cap by 0.0001 | no cap enforcement |
| `stake_mode` per_day/per_acca | `per_acca` total is `min(stake_frac, stake_per_acca × n_accas)` — fewer accas means less risk, not the same risk concentrated | always `stake_frac`, split n ways |
| `bucket_weights` in `plan_day` | benched bucket removed **before selection** so the card rebuilds from survivors, not post-hoc veto | none |
| replay purity contract | `plan_day(bucket_weights=None)` must produce byte-identical plans; pinned by tests | n/a |
| `MIN_ACCAS` card gate | a card with fewer than N accas is NO BET, explicitly | implicit |
| `rank_caps` / `_rank_capped_probability` | demote a bucket's *ordering* without touching its printed evidence | none |
| take-profit marker file | persisted, re-announced every run | in-memory only |

### 3.3 The constant-justification culture

Racket, 8 occurrences:
```python
MAX_ACCA_ODDS = 4.0  # user complained 8.06/7.13 high, winners were 1.28-1.88
# USER FIX: those odds are high (7.45) – prioritize high prob favorites
```

Edge:
```python
STAKE_FRAC = 1.0 / 4.0
#  (2026-09-04 sizing audit: on the 52-day replay the growth-optimal fraction
#   is ~40%, and the growth curve is FLAT from 30-50% while max drawdown climbs
#   62% -> 87%. 50% was past the peak: LOWER growth AND higher risk...)
```

…and Edge still later published *"STAKE_FRAC's own justification no longer reproduces"*.

---

## PART 4 — THE METHODOLOGY LESSON (please read before acting on Part 1)

I built the variant battery. Here is what it says, at 25% of bank, on identical days:

| variant | bank | log/day | maxDD |
|---|---|---|---|
| LIVE (max_accas=4) | 128.5% | +0.0279 | 36.1% |
| max_accas=2 | 187.9% | +0.0701 | 28.4% |
| **max_accas=1** | **378.5%** | **+0.1479** | 25.0% |
| singles (all legs) | 109.2% | +0.0098 | 23.8% |
| drop ML BOOST legs | 119.4% | +0.0197 | 25.0% |

Paired bootstrap vs LIVE (same resampled day indices under both arms):

| variant | mean diff | 80% CI | P(better) |
|---|---|---|---|
| max_accas=2 | +0.0422 | +0.0221…+0.0643 | **99.6%** |
| max_accas=1 | +0.1200 | +0.0672…+0.1735 | **100.0%** |

**That looks conclusive. It is not.** Running the null test (Edge's `search_noise.py` method — demean every arm to exactly zero true edge, resample the 9 days jointly, keep the winner):

```
winner distribution under the null:  median +0.0320   p90 +0.1382   p99 +0.2311
observed winner-minus-LIVE gap:      +0.1200
P(noise alone produces a winner >= +0.1200) = 0.144
```

With 5–8 arms over 9 days, **noise alone routinely manufactures a "winner" at +0.032 log/day and reaches +0.138 one time in ten.** The paired CI did not account for the search. `max_accas=1` is not adoptable. Neither is anything else in the table.

**So: nothing in Part 1 should be shipped as a tuning change today.** What Part 1 legitimately supports is different and stronger — it identifies *defects* (Part 2) and *missing instruments*, neither of which require statistical power to justify fixing.

---

## PART 5 — WHAT TO ACTUALLY DO

### Tier 0 — correctness. No sample size required; these are bugs.

1. **Unify the paper/real definition.** Make `_leg_odds_trusted()` respect `_is_paper`, `_late_start`, and `_paper_reason`, or better: have the builder write an explicit `execution_safe: bool` onto each leg and have the grader read *only* that field. One definition, written once, read everywhere.
2. **Filter `paper` accas out of the reconstruction loop**, and stop assigning `stake_pct` to paper accas at all (write `stake_pct: 0.0` and a separate `paper_notional_pct` if the hit-rate report needs it).
3. **Remove the blanket `effective_force = True`.** In-window reruns should refresh a ledger only when the new card is non-empty; keep the empty-regen guard armed at all times.
4. **Close the stuck 2026-09-20 slip** and add a staleness alarm: any slip open more than N days is surfaced, not silently carried.
5. **Wire a BetClan-timezone alarm for 2026-10-25.** Cross-check a handful of BetClan kickoffs against The Odds API `commence_time` daily and alert on a systematic ≥30-minute drift. This is a dated, known, unmonitored risk.
6. **Make `parse_kickoff` fail closed** on unparseable kickoffs, with a printed skip census.

### Tier 1 — risk, justified by the drawdown curve rather than by edge

7. **Drop `STAKE_FRAC` to 0.20, or lower.** 20% and 25% produce identical growth on this ledger; 25% costs 7 extra points of drawdown. This is a free risk reduction, not a bet on a variant.
8. **Size against free bank** (total − committed open stakes), as Edge does.
9. **Enforce a hard day cap** with Edge's rounding-excess subtraction.

### Tier 2 — instruments. Build these before tuning anything.

10. **Refactor `build_accas` → `select_accas(pool, **knobs)` + `plan_day(pool, bank, **sizing)`**, every current constant a defaulted kwarg. Pin a byte-identical-output test so the refactor is provably behaviour-preserving.
11. **Port `compute_bucket_pnl`** with Edge's parameters (21d, z≤−2.0, streak 2→×0.5, 4→closed, n<20 fails open, streak advances at most once per calendar day). Run it in report-only mode first. On today's data the `0.70–0.75` calibration cell is already at **z = −3.51**.
12. **Promote `scripts/autobets_forensics.py`** (shipped with this review) into the daily run as a report, and make the null test mandatory in its output so no future battery gets quoted without it.

### Tier 3 — only after ~60 more bet-days

13. Re-derive `MIN_ODDS_PER_LEG`, `MAX_ACCAS`, `MIN_ACCA_ODDS`, `MAX_ACCA_ODDS`, the BOOST override ladder, and the doubles EV penalty against the harness — each with a pre-registered bar and the search-noise null test applied.
14. Revisit whether `BOOST` should confer *any* privilege. Today it confers five, on zero positive evidence.

---

## PART 6 — THE HONEST SUMMARY

The autobets engine currently:

- bets a pool that is **−3.88% flat** at the prices it records;
- concentrates **64% of its legs** in its worst price band (1.30–1.59, −16% to −34%);
- treats its **worst-performing label** (BOOST, −13.8%) as its strongest signal, unlocking five separate gate overrides;
- promises 73.6% and delivers 30.8% in its densest calibration cell (**z = −3.51**), with no tripwire to notice;
- penalises doubles 5× on EV while doubles are the **only positive cohort** (+6.8%);
- funds acca slots 3–4 which have contributed **−30.8 points**;
- stakes 25% where 20% gives identical growth for 7 fewer points of drawdown;
- carries a **paper/real definition mismatch** between builder and grader that can book losses but never wins;
- can have a **booked card silently blanked** by any rerun between 06:00 and 09:00;
- and shows `bank 130.2%`, which the bootstrap places at the **36th percentile of a distribution whose 10th percentile is −118 points.**

None of that is a reason to stop. It is a reason to fix the six correctness bugs, take the free risk reduction, build the instruments, and then — with 60 more bet-days and a pre-registered bar — find out whether there is an edge underneath. Edge Factory reached the same place with its own engine and wrote it down rather than tuning its way out of it. That discipline, more than any single mechanism, is the thing worth importing.

---

## PART 7 — IMPLEMENTATION RECORD (appended 2026-09-26, after the fixes landed)

Parts 1–6 above were written as an advisory review. They are preserved verbatim
as the pre-fix record. This part states what was actually changed, so the
document cannot be read as describing live behaviour that no longer exists.

**Verification contract for everything below:** `PYTHONPATH=src python3 -m pytest tests -q`
→ **497 passed** (baseline before this work: 354). The daily workflow runs the
suite as a blocking gate.

### Defects fixed (Tier 0)

| # | Defect | Fix | Proof |
|---|---|---|---|
| 1 | No-clobber guard disarmed for the whole build window | `should_write_ticket_files` checks the empty-regeneration case *before* the frozen/window branches; blanket `effective_force=True` removed from `main()` | `test_tickets_guards.py` |
| 2 | Builder and grader disagreed on "paper" | Single predicate in new `src/racketfactory/execution.py`; both sides import it. Grader honours `acca["paper"]` as a hard floor and logs any demotion | `test_execution_contract.py`, `test_grade_settlement_faults.py` |
| 3 | Reconstruction injected paper accas carrying real stakes | `sanitise_acca_for_replay` zeroes `stake_pct` **only** when the ledger had not already marked the acca paper (so a genuine real stake is not silently erased) | `test_tickets_guards.py` |
| 4 | Stake sized against total bank, ignoring committed capital | `free_bank(state)` + `allocate_stakes()`; ledger now emits `free_bank_pct`, `committed_pct`, `day_cap_pct`, `stake_frac` | `test_tickets_guards.py` |
| 5 | Kickoff correctness rested on an unverified assumption with a dated expiry | `scripts/kickoff_tz_audit.py` (fails **safe** to `UNKNOWN`, never green by default), `REVIEW_DUE` on 2026-10-25, env override, wired into `daily.py` before ticket generation | `test_kickoff_tz_drift.py` (26) |
| **6** | **`fallback_2leg_mutual` appended its pair with NO acca-level odds gate** | **Found during red-teaming, not in Parts 1–6.** The fallback now applies the primary path's admission rule (probability product, overshoot allowance, min/max acca odds) | `test_select_accas_golden.py::test_ungated_fallback_is_closed` |

Two further faults surfaced while writing the tests and were fixed:

- **A legless acca was a free win.** `any([])` is `False`, so an acca with an
  empty `legs` list matched no LOST and no VOID branch and fell through to
  `elif priceable:` — paying out `stake × odds` on nothing. Now voided with a
  full refund and a `FAULT` log line.
- **A bare `assert booked_real <= day_cap`** (a no-op under `python -O`) is now
  a loud **OVERSTAKE GUARD** that zeroes the book rather than shipping an
  over-staked card.

#### Defect 6 is the one that changes the record

The single live instance is **2026-09-17**: a `fallback_2leg_mutual` at odds
**4.08** against `MAX_ACCA_ODDS = 4.0`, legs 2.18 + 1.87. Both won. Stake 28.656
→ **+88.26 points**, i.e. the clear majority of the engine's entire +30.17 net
P&L came from a bet its own rules rejected. Part 1.2 said the bank is variance;
this sharpens it: the bank is variance *plus a bug*.

**The uncomfortable arithmetic.** Over the settled ledger the REAL track staked
305.15 and returned 335.32 — **+30.17 points**. Remove that single bet, which
the engine's own rules should never have placed, and the record is
**−58.09 points**. So the honest reading is not "we were +30 and gave some
back": it is *the engine has never been in profit on bets it was entitled to
make*. Every conclusion in Parts 1–6 that leaned on the bank being above 100%
should be re-read in that light, and the fix makes future days measure the
strategy rather than the bug.

Closing the gate has exactly one consequence on the seven captured pick-days:
`2026-09-20` no longer produces its 3.35 fallback (legs cp 0.69 × 0.66 = 0.46,
below the 0.65 floor; 3.35 above the 3.00 BOOST cap). **The golden fixture was
deliberately NOT re-baselined.** It stands as the pre-refactor record, and the
divergence is declared in `INTENTIONAL_DIVERGENCES` with its reason; the test
asserts the change is confined to the `priced` track and fails loudly if the
case ever silently re-matches.

### Risk reduction taken (Tier 1)

`STAKE_FRAC` 0.25 → **0.20**. Not a search result — a dominance argument:
identical log growth to four decimal places, 6.7 points less maximum drawdown.
Overridable via `RACKET_FACTORY_STAKE_FRAC` for replays.

### Instruments built (Tier 2)

- **`src/racketfactory/tripwire.py`** — calibration and slice-ROI monitor,
  appended to `auto_tickets_performance.txt` every grading run. Honest by
  construction: `MIN_N = 30` means the z = −3.50 cell is still tagged `NOISE`,
  a losing-but-inconclusive slice is `INCONCL.` rather than `OK`, and the worst
  cell is named on its own line even when it is under the bar.
- **`scripts/autobets_forensics.py`** — the measurement harness behind Part 1,
  now wired into `daily.py` and carrying `--preregistration`.
- **Stale-slip alarm** — `STALE_SLIP_ALARM_DAYS = 3`, written to
  `auto_tickets_stale_slips.json` and shouted in the performance file. Closing
  is `--close-stale-days N`: opt-in, human-invoked, never moves the bank.
- **`select_accas(pool, knobs)`** — `build_accas` renamed and parameterised via
  a frozen `AccaKnobs` dataclass (back-compat shim retained). Defaults are
  byte-identical to the old hardcoded constants, pinned against a pre-refactor
  capture on seven real pick-days.

### Deliberately NOT changed

Every finding in Part 1 that is a *tuning* claim was left alone and written
down instead: `scripts/autobets_forensics.py --preregistration` lists H1–H6
with their pass bars, registered **before** the data supports them. The two
stale justifications in the source (`doubles 0W/5L`, in `auto_tickets.py` and
`ml.py`) were corrected in place to state the current evidence *and* the reason
the constant is nonetheless unchanged: n = 11 is far below the bar, and acting
on an 11-sample reversal is precisely the error the null test exists to prevent.

`mine_edges.py`'s closing-odds ROI contamination is real but out of scope here;
it already carries a module-level warning and a proper fix needs opening-odds
capture, not a patch.
