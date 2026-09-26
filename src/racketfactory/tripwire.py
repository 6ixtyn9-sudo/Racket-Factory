"""Report-only tripwires over the settled auto-tickets history.

Ported from Edge Factory's bucket-P&L / decay monitors, deliberately
**observation only**: nothing here changes a stake, a floor or a gate. It
prints what the ledger says and names the statistical bar each slice would
have to clear before anyone is allowed to act on it.

That restraint is the point. A variant battery run over this same 9-bet-day
ledger produced a "winner" worth +0.12 log-growth/day with a paired
bootstrap P(better)=100% — and a null test (arms demeaned, days resampled
jointly) then showed pure noise clears that bar 14.4% of the time. So the
tripwires report, tag ``NOISE`` below the minimum n, and wait.

Two independent families:

* **Calibration** — does ``ml_calibrated_prob`` mean what it says? Promised
  vs realised hit rate per probability band. This is where the live ledger
  has a genuine z = -3.5 hole (0.70-0.75 promised 73.6%, delivered 30.8%).
* **Slice P&L** — flat-stake return per bucket / ML verdict / price band
  over a trailing window, with the consecutive-loss streak Edge uses to
  decide demote-then-bench.

Pure stdlib: this is imported by the grader on every run and must never be
the reason a settlement pass fails.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

# --- pass bars -------------------------------------------------------------
# Below MIN_N nothing gets a verdict, ever. Edge's equivalent gate is 30
# settled; the tennis ledger is far younger, so the tag is loud instead.
MIN_N = 30
Z_WATCH = -1.2816   # one-sided 90%
Z_BLEED = -2.0      # Edge's demote bar
PNL_WINDOW_DAYS = 21
DEMOTE_STREAK = 2   # consecutive losing windows -> halve (when enforced)
BENCH_STREAK = 4    # consecutive losing windows -> close (when enforced)

STATUS_NOISE = "NOISE"            # n below the bar: no verdict is possible
STATUS_INCONCLUSIVE = "INCONCL."  # enough n, negative, not yet distinguishable
STATUS_OK = "OK"                  # enough n, at or above expectation
STATUS_WATCH = "WATCH"
STATUS_BLEEDING = "BLEEDING"

# Probability bands for the calibration table (lower bound inclusive).
PROB_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<0.60", 0.0, 0.60),
    ("0.60-0.65", 0.60, 0.65),
    ("0.65-0.70", 0.65, 0.70),
    ("0.70-0.75", 0.70, 0.75),
    ("0.75-0.80", 0.75, 0.80),
    ("0.80+", 0.80, 1.01),
)

PRICE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("1.00-1.29", 1.0, 1.30),
    ("1.30-1.44", 1.30, 1.45),
    ("1.45-1.59", 1.45, 1.60),
    ("1.60-1.99", 1.60, 2.00),
    ("2.00+", 2.00, float("inf")),
)


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def leg_records(history: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Flatten settled history into one record per decided, priced leg.

    Only WON/LOST legs on non-paper accas with a usable price count: VOID /
    PENDING / CONFLICT carry no verdict, and paper legs never moved money.
    """
    out: list[dict] = []
    for entry in history or []:
        day = str(entry.get("date") or "")
        for acca in entry.get("accas", []) or []:
            if acca.get("paper"):
                continue
            for leg in acca.get("legs", []) or []:
                outcome = leg.get("_settle_outcome")
                if outcome not in ("WON", "LOST"):
                    continue
                odds = _as_float(leg.get("odds"))
                if odds is None or odds <= 1.0:
                    continue
                out.append({
                    "date": day,
                    "odds": odds,
                    "won": outcome == "WON",
                    "profit": (odds - 1.0) if outcome == "WON" else -1.0,
                    "prob": _as_float(leg.get("ml_calibrated_prob")),
                    "bucket": str(leg.get("bucket") or "UNKNOWN").upper(),
                    "verdict": str(leg.get("ml_verdict") or "UNKNOWN").upper(),
                    "tour": str(leg.get("tour") or "UNKNOWN").upper(),
                    "surface": str(leg.get("surface") or "UNKNOWN").title(),
                    "match": str(leg.get("match") or ""),
                })
    return out


def _status(z: float | None, n: int) -> str:
    """Verdict for a slice, erring towards "we do not know".

    OK is reserved for slices performing AT OR ABOVE expectation. A slice
    that is losing money but has not yet cleared the significance bar is
    INCONCLUSIVE, not OK — "BOOST -13.83% OK" is the kind of line that gets
    a broken gate left switched on for another month.
    """
    if n < MIN_N or z is None:
        return STATUS_NOISE
    if z <= Z_BLEED:
        return STATUS_BLEEDING
    if z <= Z_WATCH:
        return STATUS_WATCH
    if z < 0:
        return STATUS_INCONCLUSIVE
    return STATUS_OK


def calibration_row(label: str, legs: Sequence[Mapping[str, Any]]) -> dict:
    """Promised vs realised hit rate for one probability band.

    z is the normal approximation to the binomial under the model's own
    claim: (realised - promised) / sqrt(p(1-p)/n). Negative = the model is
    over-confident, which is the only direction that costs money.
    """
    scored = [leg for leg in legs if leg.get("prob") is not None]
    n = len(scored)
    row = {"band": label, "n": n, "wins": 0, "promised_pct": None,
           "realised_pct": None, "gap_pp": None, "z": None,
           "status": STATUS_NOISE, "min_n": MIN_N}
    if not n:
        return row
    wins = sum(1 for leg in scored if leg["won"])
    promised = sum(float(leg["prob"]) for leg in scored) / n
    realised = wins / n
    var = promised * (1.0 - promised) / n
    z = (realised - promised) / math.sqrt(var) if var > 0 else None
    row.update(wins=wins,
               promised_pct=round(100.0 * promised, 1),
               realised_pct=round(100.0 * realised, 1),
               gap_pp=round(100.0 * (realised - promised), 1),
               z=None if z is None else round(z, 2),
               status=_status(z, n))
    return row


def calibration_table(legs: Sequence[Mapping[str, Any]],
                      bands: Sequence[tuple[str, float, float]] = PROB_BANDS) -> dict:
    scored = [leg for leg in legs if leg.get("prob") is not None]
    return {
        "bands": [calibration_row(label, [leg for leg in scored
                                          if lo <= float(leg["prob"]) < hi])
                  for label, lo, hi in bands],
        "all": calibration_row("all", scored),
    }


def slice_row(label: str, legs: Sequence[Mapping[str, Any]]) -> dict:
    """Flat-stake ROI for one slice, with the t-like z on mean profit.

    Unit stake per leg, so profit is (odds-1) on a win and -1 on a loss and
    ROI is just the mean. The z uses the sample sd, which is the honest
    denominator here: leg outcomes are not iid Bernoulli at a shared price.
    """
    n = len(legs)
    row = {"slice": label, "n": n, "wins": 0, "hit_pct": None, "avg_odds": None,
           "breakeven_pct": None, "roi_pct": None, "z": None,
           "status": STATUS_NOISE, "min_n": MIN_N}
    if not n:
        return row
    wins = sum(1 for leg in legs if leg["won"])
    profits = [float(leg["profit"]) for leg in legs]
    mean = sum(profits) / n
    avg_odds = sum(float(leg["odds"]) for leg in legs) / n
    z = None
    if n > 1:
        var = sum((p - mean) ** 2 for p in profits) / (n - 1)
        if var > 0:
            z = mean / math.sqrt(var / n)
    row.update(wins=wins,
               hit_pct=round(100.0 * wins / n, 1),
               avg_odds=round(avg_odds, 3),
               breakeven_pct=round(100.0 / avg_odds, 1),
               roi_pct=round(100.0 * mean, 2),
               z=None if z is None else round(z, 2),
               status=_status(z, n))
    return row


def _parse_day(value: str) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def window_legs(legs: Sequence[Mapping[str, Any]], *, days: int = PNL_WINDOW_DAYS,
                as_of: date | None = None) -> list[dict]:
    """Legs settled within the trailing window ending at ``as_of``."""
    if days <= 0:
        return list(legs)
    dated = [(_parse_day(leg.get("date", "")), leg) for leg in legs]
    known = [d for d, _ in dated if d is not None]
    if not known:
        return list(legs)
    end = as_of or max(known)
    start = end - timedelta(days=days)
    return [leg for day, leg in dated if day is not None and start <= day <= end]


def losing_streak(legs: Sequence[Mapping[str, Any]], key: str, value: str, *,
                  window: int = PNL_WINDOW_DAYS, windows: int = BENCH_STREAK,
                  as_of: date | None = None) -> int:
    """How many consecutive trailing windows this slice has lost money in.

    Edge demotes at ``DEMOTE_STREAK`` and benches at ``BENCH_STREAK``. Here
    it is reported, not enforced — a window with n < MIN_N breaks the streak
    rather than extending it, so silence never looks like failure.
    """
    dated = [(_parse_day(leg.get("date", "")), leg) for leg in legs]
    known = [d for d, _ in dated if d is not None]
    if not known:
        return 0
    end = as_of or max(known)
    streak = 0
    for index in range(windows):
        hi = end - timedelta(days=window * index)
        lo = hi - timedelta(days=window)
        chunk = [leg for day, leg in dated
                 if day is not None and lo < day <= hi and str(leg.get(key)) == value]
        if len(chunk) < 2:
            break
        if sum(float(leg["profit"]) for leg in chunk) >= 0:
            break
        streak += 1
    return streak


def slice_table(legs: Sequence[Mapping[str, Any]], key: str, *,
                window_days: int = PNL_WINDOW_DAYS,
                as_of: date | None = None) -> dict:
    """Trailing-window slice P&L keyed by ``bucket`` / ``verdict`` / ``tour``."""
    scoped = window_legs(legs, days=window_days, as_of=as_of)
    values = sorted({str(leg.get(key)) for leg in scoped})
    rows = []
    for value in values:
        subset = [leg for leg in scoped if str(leg.get(key)) == value]
        row = slice_row(value, subset)
        row["key"] = key
        row["losing_windows"] = losing_streak(legs, key, value,
                                              window=window_days, as_of=as_of)
        row["would_demote"] = (row["status"] == STATUS_BLEEDING
                               and row["losing_windows"] >= DEMOTE_STREAK)
        row["would_bench"] = (row["status"] == STATUS_BLEEDING
                              and row["losing_windows"] >= BENCH_STREAK)
        rows.append(row)
    rows.sort(key=lambda r: (r["roi_pct"] if r["roi_pct"] is not None else 0.0))
    return {"key": key, "window_days": window_days, "rows": rows,
            "all": slice_row("all", scoped)}


def price_band_table(legs: Sequence[Mapping[str, Any]]) -> dict:
    return {"rows": [slice_row(label, [leg for leg in legs
                                       if lo <= float(leg["odds"]) < hi])
                     for label, lo, hi in PRICE_BANDS],
            "all": slice_row("all", legs)}


def build_report(history: Iterable[Mapping[str, Any]], *,
                 as_of: date | None = None) -> dict:
    legs = leg_records(history)
    return {
        "n_legs": len(legs),
        "min_n": MIN_N,
        "window_days": PNL_WINDOW_DAYS,
        "enforced": False,
        "calibration": calibration_table(legs),
        "price_bands": price_band_table(legs),
        "by_verdict": slice_table(legs, "verdict", as_of=as_of),
        "by_bucket": slice_table(legs, "bucket", as_of=as_of),
    }


def _fmt(value: Any, spec: str = "", dash: str = "—") -> str:
    if value is None:
        return f"{dash:>{len(format(0, spec)) if spec else 1}}" if spec else dash
    return format(value, spec)


def render_report(report: Mapping[str, Any]) -> list[str]:
    """Plain-text block for auto_tickets_performance.txt. Report only."""
    lines: list[str] = []
    n_legs = report.get("n_legs", 0)
    min_n = report.get("min_n", MIN_N)
    lines.append("")
    lines.append(f"--- TRIPWIRES (report-only, no gate is enforced) — {n_legs} decided staked legs ---")
    lines.append(f"  bar: a slice needs n>={min_n} before any verdict counts; "
                 f"below that it is tagged {STATUS_NOISE} and ignored.")

    cal = report.get("calibration", {})
    lines.append("  calibration of ml_calibrated_prob (promised vs realised):")
    lines.append(f"    {'band':<11}{'n':>4}{'promised':>10}{'realised':>10}{'gap':>9}{'z':>8}  status")
    for row in list(cal.get("bands", [])) + [cal.get("all", {})]:
        if not row or not row.get("n"):
            continue
        lines.append(
            f"    {row['band']:<11}{row['n']:>4}"
            f"{row['promised_pct']:>9.1f}%{row['realised_pct']:>9.1f}%"
            f"{row['gap_pp']:>+8.1f}p{_fmt(row['z'], '>8.2f')}  {row['status']}"
        )

    for section, title in (("by_verdict", "ML verdict"), ("by_bucket", "bucket")):
        table = report.get(section, {})
        rows = [r for r in table.get("rows", []) if r.get("n")]
        if not rows:
            continue
        lines.append(f"  flat-stake ROI by {title} "
                     f"(trailing {table.get('window_days')}d, worst first):")
        lines.append(f"    {'slice':<22}{'n':>4}{'hit%':>8}{'ROI':>9}{'z':>8}  status")
        for row in rows:
            lines.append(
                f"    {row['slice'][:22]:<22}{row['n']:>4}{row['hit_pct']:>7.1f}%"
                f"{row['roi_pct']:>+8.2f}%{_fmt(row['z'], '>8.2f')}  {row['status']}"
                + (f"  [would demote after {row['losing_windows']} losing windows]"
                   if row.get("would_demote") else "")
            )

    bleeding = [row["band"] for row in cal.get("bands", [])
                if row.get("status") == STATUS_BLEEDING]
    if bleeding:
        lines.append(f"  ALARM calibration BLEEDING in band(s): {', '.join(bleeding)} "
                     f"— the model's stated probability is not the realised one there.")

    # Always name the worst cell, even when it is under the bar. A -3.5 z on
    # n=13 is not actionable, but it must not be invisible either: burying it
    # in a table row tagged NOISE is how a known hole survives a review.
    worst = min((row for row in cal.get("bands", [])
                 if row.get("z") is not None and row.get("n")),
                key=lambda row: row["z"], default=None)
    if worst and worst["z"] < Z_WATCH:
        provisional = " (PROVISIONAL — under the n bar, do not act on it yet)" \
            if worst["n"] < MIN_N else ""
        lines.append(
            f"  worst calibration cell: {worst['band']} n={worst['n']} "
            f"promised {worst['promised_pct']}% delivered {worst['realised_pct']}% "
            f"z={worst['z']}{provisional}")
    return lines
