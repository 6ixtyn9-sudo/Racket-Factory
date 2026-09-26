#!/usr/bin/env python3
"""Start a clean bank cycle at an execution-regime boundary.

WHY A RESET IS JUSTIFIED HERE
-----------------------------
The bank is not a neutral scoreboard, it is an *input*: the day cap is
``stake_frac * bank``. On 2026-09-26 the live bank was 130.17%, of which
+88.26 points came from a single acca the engine's own rules barred (a 4.08
double against a 4.00 ceiling, placed by an ungated fallback branch now
closed). On bets it was entitled to place the record is -58.09.

So the engine is currently sizing every stake against a bank inflated 3.1x
relative to its entitled record. That is a live risk, not a bookkeeping
nicety, and it is why this script exists.

WHAT IT DOES NOT DO
-------------------
It does NOT bump ``REGIME_ID`` and it does NOT delete pick history.

Pick generation did not change at the 2026-09-26 boundary — only acca
assembly, staking and settlement did. The 141 settled picks and 50 staked
legs remain valid evidence about pick quality, and they are the scarce
resource every pre-registered hypothesis in ``autobets_forensics.py
--preregistration`` is waiting on. Throwing them away would reset every n to
zero, blind the ML context registry, and push the earliest possible answer
out by months. Calibration accrues across the boundary; only acca-level P&L
restarts.

SAFETY
------
- Prints a plan and changes nothing unless ``--apply`` is passed.
- Refuses to run with open slips (capital is in flight) unless ``--force``.
- Archives the current state next to it before writing.
- Keeps ``history`` intact and stamps the boundary into ``events``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"
STATE_FILE = LOCALDATA / "auto_tickets_state.json"
TZ = ZoneInfo("Africa/Johannesburg")

from racketfactory.regime import (  # noqa: E402
    EXECUTION_REGIME_ID,
    REGIME_ID,
)
from racketfactory.execution import committed_stake  # noqa: E402
from racketfactory.tripwire import adjusted_pnl  # noqa: E402

BASELINE = 100.0


def load_state(path: Path) -> dict:
    return json.loads(path.read_text())


def plan(state: dict) -> dict:
    """What a reset would change, computed without touching anything."""
    history = state.get("history", []) or []
    adj = adjusted_pnl(history)
    bank = float(state.get("bank") or state.get("bank_pct") or BASELINE)
    return {
        "bank_before": round(bank, 6),
        "bank_after": BASELINE,
        "paper_bank_before": round(float(state.get("paper_bank") or BASELINE), 6),
        "cycle_base_before": round(float(state.get("cycle_base") or BASELINE), 6),
        "open_slips": len(state.get("open_slips", []) or []),
        # Paper slips hold no capital, so they must not block a reset. Only
        # REAL committed stake is capital in flight. Both slips open on
        # 2026-09-26 were paper: blocking on the count alone would have made
        # the safety rail fire on nothing.
        "committed_pct": round(committed_stake(state.get("open_slips", []) or []), 4),
        "history_entries": len(history),
        "history_preserved": True,
        "booked_pnl_pct": adj["booked_pnl_pct"],
        "adjusted_pnl_pct": adj["adjusted_pnl_pct"],
        "breaches": adj["breaches"],
        "pick_regime": REGIME_ID,
        "pick_regime_action": "UNCHANGED — pick logic did not change",
        "execution_regime": EXECUTION_REGIME_ID,
    }


def apply_reset(state: dict, *, note: str, now: datetime | None = None) -> dict:
    """Return a new state with the bank cycle restarted. History is kept."""
    when = (now or datetime.now(TZ)).isoformat()
    before = plan(state)
    new = dict(state)
    new["bank"] = BASELINE
    new["paper_bank"] = BASELINE
    new["cycle_base"] = BASELINE
    new["base_pct"] = BASELINE
    events = list(new.get("events", []) or [])
    events.append({
        "at": when,
        "event": "execution_cycle_reset",
        "execution_regime": EXECUTION_REGIME_ID,
        "pick_regime": REGIME_ID,
        "bank_before": before["bank_before"],
        "bank_after": BASELINE,
        "booked_pnl_pct": before["booked_pnl_pct"],
        "adjusted_pnl_pct": before["adjusted_pnl_pct"],
        "history_entries_preserved": before["history_entries"],
        "note": note,
    })
    new["events"] = events
    new[f"_reset_{when[:10].replace('-', '')}_{EXECUTION_REGIME_ID}"] = True
    return new


def render_plan(p: dict, *, applying: bool) -> list[str]:
    head = "APPLYING RESET" if applying else "DRY RUN — nothing will be written"
    lines = ["=" * 74, f"EXECUTION CYCLE RESET — {head}", "=" * 74]
    if p["breaches"]:
        lines.append("  why:")
        for b in p["breaches"]:
            lines.append(f"    {b['date']} {b['type']} @ {b['odds']:.2f} "
                         f"{b['pnl_pct']:+.2f} pts — {b['reason']}")
        lines.append(f"    booked P&L {p['booked_pnl_pct']:+.2f} pts, "
                     f"ADJUSTED {p['adjusted_pnl_pct']:+.2f} pts")
        infl = p["bank_before"] / max(BASELINE + p["adjusted_pnl_pct"], 1e-9)
        lines.append(f"    the bank is sizing stakes {infl:.2f}x the entitled record")
    lines += [
        "",
        f"  bank          {p['bank_before']:.4f}%  ->  {p['bank_after']:.1f}%",
        f"  paper bank    {p['paper_bank_before']:.4f}%  ->  {p['bank_after']:.1f}%",
        f"  cycle base    {p['cycle_base_before']:.4f}%  ->  {p['bank_after']:.1f}%",
        f"  open slips    {p['open_slips']} "
        f"(real committed capital {p['committed_pct']:.2f}%)"
        + ("  <-- IN FLIGHT, use --force to override"
           if p["committed_pct"] > 0 else "  — paper only, safe to reset"),
        "",
        f"  history       {p['history_entries']} entries — PRESERVED, not deleted",
        f"  pick regime   {p['pick_regime']}  ({p['pick_regime_action']})",
        f"  exec regime   {p['execution_regime']}",
        "",
        "  Calibration and every pre-registered hypothesis keep their sample",
        "  size. Only acca-level P&L and the bank restart.",
    ]
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write the new state (default: dry run)")
    ap.add_argument("--force", action="store_true",
                    help="reset even with open slips (capital in flight)")
    ap.add_argument("--note", default="execution regime boundary 2026-09-26",
                    help="reason recorded in the state event log")
    ap.add_argument("--state", default=None, help="state file (testing)")
    args = ap.parse_args(argv)

    state_path = Path(args.state) if args.state else STATE_FILE
    if not state_path.exists():
        print(f"no state file at {state_path} — nothing to reset")
        return 0

    state = load_state(state_path)
    p = plan(state)
    for line in render_plan(p, applying=args.apply):
        print(line)

    if not args.apply:
        print("\n  re-run with --apply to perform the reset.")
        return 0

    if p["committed_pct"] > 0 and not args.force:
        print(f"\n  REFUSED: {p['open_slips']} open slip(s) hold "
              f"{p['committed_pct']:.2f}% of real capital in flight. Settle "
              f"them first (auto_tickets_grade.py, or --close-stale-days N), "
              f"or pass --force.")
        return 1

    stamp = datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
    archive = state_path.with_name(f"{state_path.stem}.pre-reset-{stamp}.json")
    shutil.copy(state_path, archive)
    new_state = apply_reset(state, note=args.note)
    state_path.write_text(json.dumps(new_state, indent=2))
    print(f"\n  archived previous state -> {archive.name}")
    print(f"  wrote {state_path.name}: bank {BASELINE:.1f}%, "
          f"{p['history_entries']} history entries preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
