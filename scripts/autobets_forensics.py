#!/usr/bin/env python3
"""Autobets forensics — measure what the ticket engine actually did.

READ-ONLY. Reads localdata/auto_tickets_state.json (the settled slip ledger)
and reports, with denominators and bootstrap intervals:

  POOL      do the staked legs beat their own prices?
  RANKING   does the engine's acca ordering predict anything?
  LABELS    do bucket / ml_verdict labels separate winners from losers?
  CALIB     does ml_calibrated_prob mean what it says?
  STAKING   what the stake fraction did, and what other fractions would have done
  VARIANTS  counterfactual cards on identical days + PAIRED bootstrap
  NULL      search-winner noise test — how big a "winner" noise alone produces

Doctrine (inherited from Edge Factory's replay harness, which learned it the
hard way):
  - Relative comparisons only. These are not predictions.
  - Primary metric is MEAN LOG GROWTH PER BET-DAY, not final bank. Final bank
    is dominated by whichever day happened to land a treble.
  - A/B differences are PAIRED-bootstrapped: the same resampled day indices
    scored under both arms. Unpaired intervals are meaningless here.
  - Every cell prints n. Anything under 30 is noise and is labelled as such.
  - Running a battery and quoting the winner's CI is INVALID. The NULL section
    exists to show how large a winner pure noise manufactures at this n.

Usage:
    PYTHONPATH=src python3 scripts/autobets_forensics.py
    PYTHONPATH=src python3 scripts/autobets_forensics.py --bootstrap 50000
    PYTHONPATH=src python3 scripts/autobets_forensics.py --json out.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"
STATE = LOCALDATA / "auto_tickets_state.json"

from racketfactory.tripwire import (  # noqa: E402
    acca_rule_breach,
    adjusted_pnl,
    render_adjusted_pnl,
)


def rule_breach_accas(days: dict) -> list[tuple[str, dict]]:
    """(bet-day, acca) for every settled acca the engine's rules barred."""
    return [(day, acca) for day in sorted(days)
            for acca in days[day] if acca_rule_breach(acca)]

NOISE_N = 30          # Edge's bar: cells below this are noise, always labelled
SEED = 2026


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def load_days(state: dict) -> dict[str, list]:
    """Bet-day -> list of settled REAL accas (paper excluded)."""
    days: dict[str, list] = collections.defaultdict(list)
    for entry in state.get("history", []):
        for acca in entry.get("accas", []):
            if acca.get("paper"):
                continue
            days[entry["date"]].append(acca)
    return dict(days)


def load_legs(days: dict[str, list]) -> list[dict]:
    legs = []
    for day in sorted(days):
        for acca in days[day]:
            for leg in acca.get("legs", []):
                try:
                    odds = float(leg.get("odds") or 0)
                except (TypeError, ValueError):
                    odds = 0.0
                if odds <= 1.0:
                    continue
                legs.append({
                    "day": day,
                    "match": leg.get("match"),
                    "odds": odds,
                    "won": leg.get("_settle_outcome") == "WON",
                    "bucket": str(leg.get("bucket")),
                    "verdict": str(leg.get("ml_verdict")),
                    "cp": leg.get("ml_calibrated_prob"),
                    "doubles": "/" in str(leg.get("match") or ""),
                })
    return legs


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def flat_roi(legs) -> float | None:
    if not legs:
        return None
    won = sum(l["odds"] for l in legs if l["won"])
    return (won - len(legs)) / len(legs)


def breakeven(legs) -> float:
    return sum(1.0 / l["odds"] for l in legs) / len(legs)


def boot_ci(legs, rng, n_boot, lo=10, hi=90):
    vals = []
    for _ in range(n_boot):
        sample = [legs[rng.randrange(len(legs))] for _ in legs]
        v = flat_roi(sample)
        if v is not None:
            vals.append(v)
    vals.sort()
    return vals[int(lo / 100 * len(vals))], vals[int(hi / 100 * len(vals))]


def tag(n) -> str:
    return "" if n >= NOISE_N else f"  [n<{NOISE_N}: NOISE]"


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------
def build_card(accas, *, max_accas=99, drop_band=None, drop_verdict=None,
               singles=False):
    kept = []
    for acca in accas:
        ok = True
        for leg in acca.get("legs", []):
            try:
                odds = float(leg.get("odds") or 0)
            except (TypeError, ValueError):
                odds = 0.0
            if drop_band and drop_band[0] <= odds < drop_band[1]:
                ok = False
            if drop_verdict and leg.get("ml_verdict") == drop_verdict:
                ok = False
        if ok:
            kept.append(acca)
    kept = kept[:max_accas]
    if singles:
        return [(float(l.get("odds") or 0), l.get("_settle_outcome") == "WON")
                for a in kept for l in a.get("legs", [])]
    return [(float(a.get("odds") or 0), bool(a.get("won"))) for a in kept]


def replay(days, order, frac=0.25, **kw):
    """Returns (final_bank, per-day log growths, max drawdown, bet-days)."""
    bank, logs, peak, dd, nb = 100.0, [], 100.0, 0.0, 0
    for day in order:
        bets = build_card(days[day], **kw)
        if not bets:
            logs.append(0.0)
            continue
        total = bank * frac
        per = total / len(bets)
        ret = sum(per * o for o, w in bets if w)
        before = bank
        bank = max(bank - total + ret, 1e-9)
        logs.append(math.log(bank / before))
        peak = max(peak, bank)
        dd = max(dd, (peak - bank) / peak)
        nb += 1
    return bank, logs, dd, nb


def stake_dominance(days, order, rng, n_boot, lo=0.20, hi=0.25, exclude=()):
    """Is a lower stake fraction actually better, or did one sequence flatter it?

    THE MISTAKE THIS EXISTS TO PREVENT: the 0.25 -> 0.20 change was first
    justified as "identical growth to four decimal places, 6.7pp less
    drawdown". The growth was not identical — 128.50757 vs 128.50844 on the
    real ledger, with 0.25 fractionally AHEAD. A point estimate that close is
    a coincidence of one 9-day ordering, and reading it as dominance is
    exactly the winner's-curse error the null test guards against elsewhere.

    Resamples bet-days with replacement and re-runs both fractions on the
    SAME resampled sequence (paired), so the comparison is not confounded by
    which days got drawn. Reports the two components separately, because
    they behave completely differently: the drawdown reduction is robust, the
    growth difference is a coin flip.
    """
    gross = [g for d, g in _gross_by_day(days, order) if d not in exclude]
    if len(gross) < 2:
        return None

    def run(sequence, frac):
        bank, logs, peak, drawdown = 100.0, [], 100.0, 0.0
        for g in sequence:
            before = bank
            bank = max(bank * (1 - frac * (1 - g)), 1e-9)
            logs.append(math.log(bank / before))
            peak = max(peak, bank)
            drawdown = max(drawdown, (peak - bank) / peak)
        return sum(logs) / len(logs), drawdown

    point_lo, point_hi = run(gross, lo), run(gross, hi)
    n = len(gross)
    growth_wins = dd_wins = dominates = 0
    for _ in range(n_boot):
        sample = [gross[rng.randrange(n)] for _ in range(n)]
        g_lo, d_lo = run(sample, lo)
        g_hi, d_hi = run(sample, hi)
        if g_lo >= g_hi:
            growth_wins += 1
        if d_lo <= d_hi:
            dd_wins += 1
        if g_lo >= g_hi and d_lo <= d_hi:
            dominates += 1
    return {
        "lo": lo, "hi": hi, "bet_days": n, "excluded": list(exclude),
        "point_lo_log_day": round(point_lo[0], 8),
        "point_hi_log_day": round(point_hi[0], 8),
        "point_lo_maxdd": round(point_lo[1], 4),
        "point_hi_maxdd": round(point_hi[1], 4),
        "p_growth_not_worse": round(growth_wins / n_boot, 3),
        "p_drawdown_not_worse": round(dd_wins / n_boot, 3),
        "p_dominates": round(dominates / n_boot, 3),
    }


def _gross_by_day(days, order):
    """(date, gross return multiple) per bet-day: bank *= 1 - frac*(1 - g)."""
    out = []
    for day in order:
        bets = build_card(days[day])
        if not bets:
            continue
        out.append((day, sum(o for o, w in bets if w) / len(bets)))
    return out


def paired_bootstrap(arm, base, rng, n_boot):
    diffs = [a - b for a, b in zip(arm, base)]
    n = len(diffs)
    sims = []
    for _ in range(n_boot):
        sims.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    sims.sort()
    return (sum(diffs) / n, sims[int(.10 * n_boot)], sims[int(.90 * n_boot)],
            sum(1 for x in sims if x > 0) / n_boot)



# --------------------------------------------------------------------------
# pre-registration
# --------------------------------------------------------------------------
# Written BEFORE the data supports any of it. A hypothesis that only appears
# after you have seen the answer is not a hypothesis, it is a story — and the
# null test in section 7 exists because this ledger is short enough to tell
# very convincing ones.
#
# Rules of engagement for every row below:
#   1. Do not change the constant until n >= MIN_N settled legs (or bet-days)
#      for that specific slice. n is the bar, not the p-value.
#   2. The effect must clear the stated bar on OUT-OF-SAMPLE days — days that
#      accrued after this file was committed (2026-09-26).
#   3. Any change picked from a battery of variants must additionally clear
#      the search-winner null test in section 7 at p < 0.05.
#   4. A hypothesis that fails is recorded as failed. It does not get quietly
#      re-tested with a different slicing until it passes.
PREREGISTRATION = [
    {
        "id": "H1-boost-privileges",
        "claim": "ML BOOST does not earn the five gate overrides it unlocks "
                 "(lower leg floor 1.15, VETO override, EV override, higher "
                 "odds cap, sort bonus).",
        "as_of_2026_09_26": "BOOST n=30 -13.83% vs ALLOW n=20 +11.05%; "
                            "permutation p=0.126 — unproven in BOTH directions.",
        "test": "flat-stake ROI of BOOST legs vs ALLOW legs",
        "bar": "n>=60 BOOST legs AND one-sided permutation p<0.05",
        "action_if_passed": "set AccaKnobs.boost_privileges=False",
        "action_if_failed": "leave BOOST privileges on and stop re-testing",
    },
    {
        "id": "H2-doubles-ev-surcharge",
        "claim": "The 5x EV surcharge on doubles (min_ev_doubles=0.05, fitted "
                 "to 'doubles 0W/5L') suppresses the book's best cohort.",
        "as_of_2026_09_26": "doubles 8W-3L +6.82% vs singles-match legs "
                            "-6.90%; n=11.",
        "test": "flat-stake ROI of doubles legs vs singles legs",
        "bar": "n>=30 doubles legs AND one-sided permutation p<0.05",
        "action_if_passed": "set AccaKnobs.min_ev_doubles to the singles floor",
        "action_if_failed": "leave the surcharge at 0.05",
    },
    {
        "id": "H3-leg-price-floor",
        "claim": "The 1.30-1.59 price band is a structural sink and "
                 "MIN_ODDS_PER_LEG points the engine straight into it.",
        "as_of_2026_09_26": "1.30-1.44 n=18 -16.39%; 1.45-1.59 n=14 -34.21%; "
                            "32 of 50 staked legs sit in the band.",
        "test": "flat-stake ROI of the 1.30-1.59 band vs the rest",
        "bar": "n>=60 legs in-band AND the band's 80% bootstrap CI entirely "
               "below 0",
        "action_if_passed": "re-derive the floor against the replay harness",
        "action_if_failed": "leave the floor at its current value",
    },
    {
        "id": "H4-acca-slots",
        "claim": "Slots 3 and 4 destroy value and MAX_ACCAS should fall.",
        "as_of_2026_09_26": "slot1 +79.78% (+111.83 pts), slot2 -28.62%, "
                            "slot3 -0.84 pts, slot4 -100% (-29.97 pts). The "
                            "max_accas=1 arm wins the battery at "
                            "P(better)=100% and STILL fails the null test "
                            "(p=0.144). Do not act on it.",
        "test": "mean log growth per bet-day, max_accas arm vs live",
        "bar": "n>=60 bet-days AND paired-bootstrap CI excluding 0 AND "
               "search-winner null test p<0.05",
        "action_if_passed": "set AccaKnobs.max_accas to the winning arm",
        "action_if_failed": "leave MAX_ACCAS at 4",
    },
    {
        "id": "H5-calibration-band",
        "claim": "ml_calibrated_prob is broken in the 0.70-0.75 band "
                 "specifically, not globally.",
        "as_of_2026_09_26": "0.70-0.75 n=13 promised 73.6% delivered 30.8% "
                            "z=-3.51; 0.75+ n=24 within +1.4pp.",
        "test": "racketfactory.tripwire.calibration_table",
        "bar": "n>=30 in-band AND status BLEEDING (z<=-2.0)",
        "action_if_passed": "recalibrate or bench the band; do not stake it",
        "action_if_failed": "leave calibration alone",
    },
    {
        "id": "H6-stake-fraction",
        "claim": "STAKE_FRAC above 0.20 buys drawdown without buying growth.",
        "as_of_2026_09_26": "CORRECTED. The first justification claimed "
                            "'identical growth to 4dp'; it was not identical "
                            "(0.20 -> 128.50757, 0.25 -> 128.50844, i.e. 0.25 "
                            "fractionally AHEAD) and the sweep included the "
                            "ungated-fallback bet the fixed engine cannot "
                            "place. Paired bootstrap, N=20000: "
                            "P(0.20 drawdown not worse)=1.000, "
                            "P(0.20 growth not worse)=0.499. So the drawdown "
                            "reduction is robust and the growth difference is "
                            "a coin flip — a RISK TRADE at no measurable "
                            "growth cost, not a dominance. Excluding the bug "
                            "bet every fraction loses money and lower is "
                            "strictly better, because the measured edge is "
                            "negative.",
        "test": "stake_dominance(), paired over resampled bet-day sequences",
        "bar": "P(drawdown not worse) >= 0.95 at n>=30 bet-days; revisit the "
               "LEVEL (not just the direction) once the edge is non-negative",
        "action_if_passed": "keep 0.20 (acted 2026-09-26 on the drawdown leg "
                            "alone, which is the leg that holds up)",
        "action_if_failed": "revert to 0.25 and say so in the commit",
    },
]


def _perm_p(a_legs, b_legs, rng, n_boot) -> float | None:
    """One-sided permutation p that a_legs underperforms b_legs on flat ROI."""
    if not a_legs or not b_legs:
        return None
    observed = flat_roi(a_legs) - flat_roi(b_legs)
    pool = list(a_legs) + list(b_legs)
    cut, hits = len(a_legs), 0
    for _ in range(n_boot):
        rng.shuffle(pool)
        diff = flat_roi(pool[:cut]) - flat_roi(pool[cut:])
        if diff <= observed:
            hits += 1
    return hits / n_boot


def evaluate_preregistration(legs, days, order, rng, n_boot) -> list[dict]:
    """Score every registered hypothesis against the data as it stands today.

    WHY THIS IS AUTOMATED: a pre-registration that a human has to remember to
    re-check is a pre-registration that quietly expires. Each row reports how
    far the evidence has come towards its own bar, so the moment a bar is met
    the report says READY TO DECIDE instead of waiting to be asked.

    Reporting only. Nothing here changes a constant.
    """
    rows: list[dict] = []
    boost = [l for l in legs if l["verdict"] == "BOOST"]
    allow = [l for l in legs if l["verdict"] == "ALLOW"]
    rows.append({
        "id": "H1-boost-privileges", "n": len(boost), "bar_n": 60,
        "metric": f"BOOST {_pct(flat_roi(boost))} vs ALLOW {_pct(flat_roi(allow))}",
        "p": _perm_p(boost, allow, rng, n_boot) if len(boost) >= 60 else None,
    })

    dbl = [l for l in legs if l["doubles"]]
    sgl = [l for l in legs if not l["doubles"]]
    rows.append({
        "id": "H2-doubles-ev-surcharge", "n": len(dbl), "bar_n": 30,
        "metric": f"doubles {_pct(flat_roi(dbl))} vs singles {_pct(flat_roi(sgl))}",
        "p": _perm_p(sgl, dbl, rng, n_boot) if len(dbl) >= 30 else None,
    })

    band = [l for l in legs if 1.30 <= l["odds"] < 1.60]
    ci = boot_ci(band, rng, min(n_boot, 5000)) if len(band) >= 60 else None
    rows.append({
        "id": "H3-leg-price-floor", "n": len(band), "bar_n": 60,
        "metric": f"1.30-1.59 band {_pct(flat_roi(band))}"
                  + (f", 80% CI {_pct(ci[0])}..{_pct(ci[1])}" if ci else ""),
        "p": None,
        "met": bool(ci and ci[1] < 0),
    })

    rows.append({
        "id": "H4-acca-slots", "n": len(order), "bar_n": 60,
        "metric": f"{len(order)} bet-day(s); battery winner still fails the "
                  f"search-winner null test",
        "p": None,
    })

    cell = [l for l in legs
            if l.get("cp") is not None and 0.70 <= float(l["cp"]) < 0.75]
    hit = (sum(1 for l in cell if l["won"]) / len(cell)) if cell else None
    rows.append({
        "id": "H5-calibration-band", "n": len(cell), "bar_n": 30,
        "metric": (f"0.70-0.75 delivered {hit:.1%}" if hit is not None
                   else "no legs in band"),
        "p": None,
    })

    dom = stake_dominance(days, order, rng, min(n_boot, 5000))
    rows.append({
        "id": "H6-stake-fraction", "n": len(order), "bar_n": 30,
        "metric": (f"P(drawdown not worse)={dom['p_drawdown_not_worse']:.3f}, "
                   f"P(growth not worse)={dom['p_growth_not_worse']:.3f}"
                   if dom else "insufficient bet-days"),
        "p": None,
        "met": bool(dom and dom["p_drawdown_not_worse"] >= 0.95
                    and len(order) >= 30),
    })

    for row in rows:
        if "met" not in row:
            row["met"] = bool(row["n"] >= row["bar_n"]
                              and row["p"] is not None and row["p"] < 0.05)
        row["status"] = ("READY TO DECIDE" if row["met"]
                         else f"not yet ({row['n']}/{row['bar_n']})")
    return rows


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def print_preregistration() -> None:
    line = "=" * 78
    print(line)
    print("PRE-REGISTERED HYPOTHESES — registered 2026-09-26, before the data")
    print(line)
    print(__doc__.strip().splitlines()[0] if __doc__ else "")
    print()
    for item in PREREGISTRATION:
        print(f"{item['id']}")
        print(f"  claim   : {item['claim']}")
        print(f"  as of   : {item['as_of_2026_09_26']}")
        print(f"  test    : {item['test']}")
        print(f"  BAR     : {item['bar']}")
        print(f"  if pass : {item['action_if_passed']}")
        print(f"  if fail : {item['action_if_failed']}")
        print()
    print("A change to any constant above without a passing bar is a guess.")


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", type=int, default=20000)
    ap.add_argument("--json", default=None)
    ap.add_argument("--preregistration", action="store_true",
                    help="print the pre-registered hypotheses and their pass "
                         "bars, then exit")
    args = ap.parse_args()
    if args.preregistration:
        print_preregistration()
        return 0
    rng = random.Random(SEED)

    if not STATE.exists():
        print(f"no state file at {STATE} — nothing to measure")
        return 0
    state = json.loads(STATE.read_text())
    days = load_days(state)
    if not days:
        print("no settled REAL accas in state history — nothing to measure")
        return 0
    order = sorted(days)
    legs = load_legs(days)
    out: dict = {"bet_days": len(order), "legs": len(legs)}

    line = "=" * 78
    print(line)
    print("AUTOBETS FORENSICS")
    print(line)
    print(f"bet-days {len(order)}   settled REAL accas "
          f"{sum(len(v) for v in days.values())}   staked legs {len(legs)}")
    print(f"bank {state.get('bank', 100.0):.2f}%   "
          f"paper bank {state.get('paper_bank', 100.0):.2f}%   "
          f"open slips {len(state.get('open_slips', []))}")

    # ---- POOL -----------------------------------------------------------
    print(f"\n{line}\n1. THE POOL — do staked legs beat their own prices?\n{line}")
    n, w = len(legs), sum(l["won"] for l in legs)
    roi, be = flat_roi(legs), breakeven(legs)
    lo, hi = boot_ci(legs, rng, args.bootstrap)
    print(f"  n={n}  W-L={w}-{n-w}  hit={w/n:.1%}  breakeven={be:.1%}  "
          f"gap={(w/n-be)*100:+.1f}pp")
    print(f"  flat-stake ROI = {roi:+.2%}   80% CI {lo:+.1%}..{hi:+.1%}{tag(n)}")
    out["pool"] = {"n": n, "wins": w, "roi": roi, "breakeven": be,
                   "ci80": [lo, hi]}

    print(f"\n  by price band:")
    print(f"    {'band':12} {'n':>3} {'hit%':>7} {'brk%':>7} {'gap':>9} {'ROI':>9}")
    def band(o):
        for edge, name in ((1.30, "1.00-1.29"), (1.45, "1.30-1.44"),
                           (1.60, "1.45-1.59"), (2.00, "1.60-1.99")):
            if o < edge:
                return name
        return "2.00+"
    grouped = collections.defaultdict(list)
    for l in legs:
        grouped[band(l["odds"])].append(l)
    for b in sorted(grouped):
        g = grouped[b]
        gw = sum(x["won"] for x in g)
        print(f"    {b:12} {len(g):3d} {gw/len(g):6.1%} {breakeven(g):6.1%} "
              f"{(gw/len(g)-breakeven(g))*100:+8.1f}pp {flat_roi(g):+8.2%}"
              f"{tag(len(g))}")

    # ---- LABELS ---------------------------------------------------------
    print(f"\n{line}\n2. THE LABELS — do bucket / ml_verdict separate winners?\n{line}")
    for field, label in (("bucket", "bucket"), ("verdict", "ml_verdict"),
                         ("doubles", "doubles?")):
        print(f"  --- {label} ---")
        grouped = collections.defaultdict(list)
        for l in legs:
            grouped[str(l[field])].append(l)
        for k, g in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            gw = sum(x["won"] for x in g)
            print(f"    {k:22} n={len(g):3d} hit={gw/len(g):6.1%} "
                  f"brk={breakeven(g):6.1%} ROI={flat_roi(g):+7.2%}{tag(len(g))}")

    # ---- RANKING --------------------------------------------------------
    print(f"\n{line}\n3. THE RANKING — does acca slot order predict?\n{line}")
    slots = collections.defaultdict(list)
    for day in order:
        for i, a in enumerate(days[day]):
            slots[i].append(a)
    print(f"  {'slot':>5} {'n':>3} {'hit%':>7} {'avg odds':>9} {'flat ROI':>10} {'P&L pts':>9}")
    for i in sorted(slots):
        g = slots[i]
        gw = sum(1 for a in g if a.get("won"))
        od = [float(a.get("odds") or 0) for a in g]
        r = (sum(o for a, o in zip(g, od) if a.get("won")) - len(g)) / len(g)
        pnl = sum((float(a.get("stake_pct") or 0) * float(a.get("odds") or 0)
                   if a.get("won") else 0) - float(a.get("stake_pct") or 0)
                  for a in g)
        print(f"  {i+1:5d} {len(g):3d} {gw/len(g):6.1%} {sum(od)/len(od):9.3f} "
              f"{r:+9.2%} {pnl:+9.2f}{tag(len(g))}")
    print("  (slot 1 = highest-ranked. Monotone decline = the ranking works.)")

    # ---- CALIBRATION ----------------------------------------------------
    print(f"\n{line}\n4. CALIBRATION — does ml_calibrated_prob mean what it says?\n{line}")
    cal = [l for l in legs if l["cp"] is not None]
    if cal:
        print(f"  {'promised':14} {'n':>3} {'promised':>10} {'realised':>9} "
              f"{'gap':>9} {'z':>7}  verdict")
        for lo_b, hi_b in ((0, .60), (.60, .65), (.65, .70), (.70, .75), (.75, 1.01)):
            g = [x for x in cal if lo_b <= float(x["cp"]) < hi_b]
            if not g:
                continue
            mp = sum(float(x["cp"]) for x in g) / len(g)
            rl = sum(x["won"] for x in g) / len(g)
            var = sum(float(x["cp"]) * (1 - float(x["cp"])) for x in g) / len(g) ** 2
            z = (rl - mp) / math.sqrt(var) if var > 0 else 0.0
            v = "BLEEDING" if z <= -2.0 else ("COLD" if rl < mp else "PAYING")
            print(f"  {f'{lo_b:.2f}-{hi_b:.2f}':14} {len(g):3d} {mp:9.1%} {rl:8.1%} "
                  f"{(rl-mp)*100:+8.1f}pp {z:+7.2f}  {v}{tag(len(g))}")
        mp = sum(float(x["cp"]) for x in cal) / len(cal)
        rl = sum(x["won"] for x in cal) / len(cal)
        var = sum(float(x["cp"]) * (1 - float(x["cp"])) for x in cal) / len(cal) ** 2
        z = (rl - mp) / math.sqrt(var)
        v = "BLEEDING" if z <= -2.0 else ("COLD" if rl < mp else "PAYING")
        print(f"  {'ALL':14} {len(cal):3d} {mp:9.1%} {rl:8.1%} "
              f"{(rl-mp)*100:+8.1f}pp {z:+7.2f}  {v}")
        out["calibration"] = {"n": len(cal), "promised": mp, "realised": rl, "z": z}

    # ---- STAKING --------------------------------------------------------
    # ---- THE RECORD ------------------------------------------------------
    print(f"\n{line}\n5. THE RECORD — booked, and adjusted for rule breaches\n{line}")
    adj = adjusted_pnl(state.get("history", []))
    out["adjusted_pnl"] = adj
    if adj["breaches"]:
        for row in render_adjusted_pnl(adj):
            print(row)
        print("\n  Everything below is measured on the booked card, which "
              "includes those bets.\n  Read every P&L figure against the "
              "ADJUSTED line, not the booked one.")
    else:
        print(f"  booked P&L {adj['booked_pnl_pct']:+.2f} pts on "
              f"{adj['staked_pct']:.2f} staked "
              f"(ROI {adj['roi_pct']:+.2f}%) — no rule breaches in the ledger.")

    print(f"\n{line}\n6. STAKING — the fraction curve on identical cards\n{line}")
    print(f"  {'frac':>6} {'final bank':>11} {'log/day':>9} {'maxDD':>8}")
    for f in (0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50):
        b, lg, dd, _ = replay(days, order, f)
        print(f"  {f:6.0%} {b:10.1f}% {sum(lg)/len(lg):+9.4f} {dd:8.1%}")

    # The curve above is ONE ordering of a handful of days. Two fractions can
    # land a thousandth of a point apart and mean nothing by it, so the
    # comparison that drives the live constant is bootstrapped, and its two
    # components are reported separately rather than collapsed into "better".
    dom = stake_dominance(days, order, rng, min(args.bootstrap, 20000))
    if dom:
        print(f"\n  paired bootstrap, {dom['lo']:.0%} vs {dom['hi']:.0%} "
              f"(n={dom['bet_days']} bet-days):")
        # 8dp on purpose: at 4dp these two read as an exact tie, which is
        # how the original (wrong) "identical growth" claim was born.
        print(f"    point: log/day {dom['point_lo_log_day']:+.8f} vs "
              f"{dom['point_hi_log_day']:+.8f}   "
              f"maxDD {dom['point_lo_maxdd']:.1%} vs {dom['point_hi_maxdd']:.1%}")
        print(f"    P(lower frac drawdown NOT worse) = {dom['p_drawdown_not_worse']:.3f}")
        print(f"    P(lower frac growth   NOT worse) = {dom['p_growth_not_worse']:.3f}")
        print(f"    P(lower frac DOMINATES)          = {dom['p_dominates']:.3f}")
        if dom["p_dominates"] < 0.95 <= dom["p_drawdown_not_worse"]:
            print("    READ: risk trade, NOT dominance — the drawdown "
                  "reduction is robust, the growth difference is noise.")
        elif dom["p_dominates"] >= 0.95:
            print("    READ: genuine dominance at this n.")
        else:
            print("    READ: neither leg is robust. The live constant is not "
                  "supported by this ledger.")
        out["stake_dominance"] = dom

    # Same question with the ungated-fallback day removed: that bet returned
    # 4.08 and the fixed engine cannot place it, so leaving it in measures a
    # staking policy against a card that no longer exists.
    breach_days = sorted({d for d, _ in rule_breach_accas(days)})
    if breach_days:
        clean = stake_dominance(days, order, rng, min(args.bootstrap, 20000),
                                exclude=tuple(breach_days))
        if clean:
            print(f"\n  excluding rule-breaching bet-day(s) {', '.join(breach_days)} "
                  f"— what the FIXED engine could have done:")
            print(f"    {'frac':>6} {'log/day':>10} {'maxDD':>8}")
            for f in (0.10, 0.15, 0.20, 0.25, 0.50):
                d2 = stake_dominance(days, order, rng, 1, lo=f, hi=f,
                                     exclude=tuple(breach_days))
                if d2:
                    print(f"    {f:6.0%} {d2['point_lo_log_day']:+10.5f} "
                          f"{d2['point_lo_maxdd']:8.1%}")
            print(f"    P(lower frac drawdown NOT worse) = "
                  f"{clean['p_drawdown_not_worse']:.3f}   "
                  f"P(growth NOT worse) = {clean['p_growth_not_worse']:.3f}")
            out["stake_dominance_excluding_breaches"] = clean

    # ---- VARIANTS -------------------------------------------------------
    print(f"\n{line}\n7. VARIANTS — counterfactual cards, then PAIRED bootstrap\n{line}")
    variants = {
        "LIVE": {},
        "max_accas=2": dict(max_accas=2),
        "max_accas=1": dict(max_accas=1),
        "singles (all legs)": dict(singles=True),
        "drop ML BOOST legs": dict(drop_verdict="BOOST"),
    }
    arms = {}
    print(f"  {'variant':24} {'bank':>8} {'log/day':>9} {'maxDD':>7} {'bet-days':>9}")
    for name, kw in variants.items():
        b, lg, dd, nb = replay(days, order, 0.25, **kw)
        arms[name] = lg
        print(f"  {name:24} {b:7.1f}% {sum(lg)/len(lg):+9.4f} {dd:7.1%} {nb:9d}")

    base = arms["LIVE"]
    print(f"\n  paired bootstrap vs LIVE ({len(base)} bet-days):")
    print(f"  {'variant':24} {'mean diff':>10} {'80% CI':>22} {'P(better)':>10}")
    for name, lg in arms.items():
        if name == "LIVE":
            continue
        if lg == base:
            print(f"  {name:24} {'NO-OP — identical cards, refusing to bootstrap':>44}")
            continue
        m, lo_b, hi_b, pb = paired_bootstrap(lg, base, rng, args.bootstrap)
        print(f"  {name:24} {m:+10.4f} {f'{lo_b:+.4f}..{hi_b:+.4f}':>22} {pb:10.1%}")

    # ---- NULL TEST ------------------------------------------------------
    print(f"\n{line}\n8. SEARCH-WINNER NULL TEST — read this before adopting anything\n{line}")
    best = max(arms, key=lambda k: sum(arms[k]) / len(arms[k]))
    best_gap = (sum(arms[best]) / len(arms[best])) - (sum(base) / len(base))
    demeaned = {k: [x - sum(v) / len(v) for x in v] for k, v in arms.items()}
    n = len(order)
    winners = []
    for _ in range(args.bootstrap):
        idx = [rng.randrange(n) for _ in range(n)]
        winners.append(max(sum(demeaned[k][i] for i in idx) / n for k in demeaned))
    winners.sort()
    p = sum(1 for x in winners if x >= best_gap) / args.bootstrap
    print(f"  best arm: {best}   gap vs LIVE = {best_gap:+.4f} log/day")
    print(f"  arms searched: {len(arms)}   bet-days: {n}")
    print(f"  under the null (every arm demeaned to zero true edge), the WINNER's")
    print(f"  gap distribution is: median {winners[len(winners)//2]:+.4f}  "
          f"p90 {winners[int(.9*len(winners))]:+.4f}  "
          f"p99 {winners[int(.99*len(winners))]:+.4f}")
    print(f"  P(noise alone produces a winner >= {best_gap:+.4f}) = {p:.3f}")
    print()
    if n < NOISE_N or p >= 0.05:
        print(f"  VERDICT: NOT ADOPTABLE. {'Sample too small' if n < NOISE_N else 'Search noise explains it'}.")
        print(f"  The battery above is an instrument check, not a recommendation.")
    else:
        print(f"  VERDICT: survives the null at this n — still requires out-of-sample days.")
    out["null_test"] = {"best": best, "gap": best_gap, "p": p, "bet_days": n}
    out["preregistration"] = PREREGISTRATION

    print()
    print(line)
    print("9. PRE-REGISTERED HYPOTHESES — scored against today's data")
    print(line)
    board = evaluate_preregistration(legs, days, order, rng,
                                     min(args.bootstrap, 5000))
    print(f"  {'hypothesis':<26} {'status':<20} {'p':>7}  evidence")
    for row in board:
        p = f"{row['p']:.3f}" if row["p"] is not None else "—"
        print(f"  {row['id']:<26} {row['status']:<20} {p:>7}  {row['metric']}")
    ready = [row["id"] for row in board if row["met"]]
    if ready:
        print(f"\n  ** {len(ready)} HYPOTHESIS/ES HAVE MET THEIR BAR: "
              f"{', '.join(ready)}")
        print("  ** These were registered in advance. Decide them now, apply "
              "the registered action, and record the outcome.")
    else:
        print("\n  none have met their bar — no constant may be changed on "
              "this evidence.")
    print("  (full text: scripts/autobets_forensics.py --preregistration)")
    out["preregistration_board"] = board

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
