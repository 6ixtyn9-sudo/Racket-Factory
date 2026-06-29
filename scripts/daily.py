#!/usr/bin/env python3
"""Racket Factory single daily trigger.

This mirrors Edge Factory's operating style while keeping tennis as a
separate regime: capture -> warehouse -> mine -> emit watchlist/picks.

Operational safety
------------------
At start-up the pipeline pins ``RACKET_FACTORY_RUN_AS_OF`` to the local-time
"now".

Outputs (Edge-Factory parity)
-----------------------------
For each run we materialise the full day ledger under ``localdata/``:

  picks_YYYY-MM-DD.json              full archive (every pick)
  picks_morning_YYYY-MM-DD.json      locked morning baseline (first write of the day)
  picks_YYYY-MM-DD.txt               human friendly report (Edge-Factory parity)

The daily trigger also runs an inline next-day planner by default (matching
Edge Factory's operating style): after today's official ledger is created, it
re-mines tomorrow's slate and writes forecast reports/JSON under ``localdata/``
without replacing today's live ``picks_today.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_TZ = "Africa/Johannesburg"
LOCALDATA = ROOT / "localdata"


def load_daily_env() -> None:
    """Load repo-local environment secrets for every daily run.

    Existing process env wins. Secrets are never printed.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    for env_path in (ROOT / ".env", LOCALDATA / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)


def local_tz() -> ZoneInfo:
    return ZoneInfo(DEFAULT_LOCAL_TZ)


def make_run_as_of() -> str:
    """ISO timestamp pinned to local TZ at pipeline start."""
    return datetime.now(local_tz()).isoformat(timespec="seconds")


def run(cmd: str, label: str, *, env: dict | None = None) -> None:
    print(f"\n>>> {label}")
    result = subprocess.run(cmd, shell=True, cwd=ROOT, env=env)
    if result.returncode != 0:
        print(f"FAILED: {label}")
        sys.exit(result.returncode)


def run_soft(cmd: str, label: str, *, env: dict | None = None) -> None:
    print(f"\n>>> {label}")
    result = subprocess.run(cmd, shell=True, cwd=ROOT, env=env)
    if result.returncode != 0:
        print(f"WARNING: non-critical step failed: {label}")


def run_capture(cmd: str, label: str, *, env: dict | None = None) -> str:
    """Run a command and return captured combined stdout/stderr.

    Used by the inline future planner so tomorrow/next-day scans can print a
    compact summary while still surfacing the full output if the miner fails.
    """
    print(f"\n>>> {label}")
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        print(f"FAILED: {label}")
        sys.exit(result.returncode)
    return result.stdout


def print_mine_run_summary(output: str) -> None:
    """Print concise future-planner status from mine_edges.py output."""
    interesting = ("Exported ", "Wrote ", "Today candidate rows", "Upcoming-card fallback rows")
    printed = False
    for line in output.splitlines():
        if any(token in line for token in interesting):
            print(f"  {line}")
            printed = True
    if not printed:
        print("  mine_edges completed")


# ------------------------------------------------------------ archive helpers --
def archived_picks_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_{target_date}.json"


def morning_baseline_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_morning_{target_date}.json"


def save_morning_baseline(target_date: str, picks_text: str | None, *, overwrite: bool = False) -> None:
    """Lock the FIRST operational picks of the day so late runs cannot drift them."""
    if picks_text is None:
        return
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    path = morning_baseline_file(target_date)
    if path.exists() and not overwrite:
        return
    path.write_text(picks_text)


def get_actual_kickoff_date(pick: dict[str, Any], fallback: str) -> str:
    """Extract the real match date from match_date/date/kickoff time, fallback to provided date."""
    for key in ("match_date", "date", "kickoff", "time", "start_time", "ko"):
        val = pick.get(key)
        if val and isinstance(val, str) and len(val) >= 10:
            match = re.search(r"(\d{4}-\d{2}-\d{2})", val)
            if match:
                return match.group(1)
    return fallback


def match_market_key(pick: dict[str, Any]) -> tuple[str, str, str]:
    match_str = str(pick.get("match") or "").lower().strip()
    selected = str(pick.get("selected_player") or pick.get("selected_side") or "").lower().strip()
    return ("EVENT_ID", match_str, selected)


def merge_picks(existing_ledger: list[dict[str, Any]], fresh_run: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_keys: set[tuple[str, str, str]] = set()
    merged: list[dict[str, Any]] = []

    for pick in existing_ledger:
        key = match_market_key(pick)
        seen_keys.add(key)
        merged.append(pick)

    for pick in fresh_run:
        key = match_market_key(pick)
        if key not in seen_keys:
            seen_keys.add(key)
            merged.append(pick)

    merged.sort(
        key=lambda p: (
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "CAUTION": 2,
             "SKIPPED_DEAD_EDGE": 3, "WATCHLIST_NO_ODDS": 4, "WATCHLIST_UNKNOWN_CTX": 5,
             "SKIPPED_VETO": 6}.get(str(p.get("bucket")), 9),
            -float(p.get("expected_value") or 0.0),
            -int(p.get("source_count") or 0),
            str(p.get("match", "")),
        )
    )
    return merged


def archive_picks_by_kickoff(picks: list[dict[str, Any]], fallback_date: str) -> list[str]:
    """Distribute picks to archives based on their actual kickoff date."""
    if not picks:
        return []
    LOCALDATA.mkdir(parents=True, exist_ok=True)

    by_date: dict[str, list[dict[str, Any]]] = {}
    for p in picks:
        d = get_actual_kickoff_date(p, fallback_date)
        by_date.setdefault(d, []).append(p)

    for d, date_picks in by_date.items():
        archive_path = archived_picks_file(d)
        existing: list[dict[str, Any]] = []
        if archive_path.exists():
            try:
                existing = json.loads(archive_path.read_text())
                if not isinstance(existing, list): existing = []
            except Exception:
                existing = []

        merged = merge_picks(existing, date_picks)
        archive_path.write_text(json.dumps(merged, indent=2, sort_keys=True))
    return list(by_date.keys())


def format_kickoff(pick: dict[str, Any]) -> str:
    for key in ("kickoff", "match_time", "time", "start_time", "ko"):
        value = pick.get(key)
        if value not in (None, "", "nan", "<NA>"):
            return str(value)
    return "n/a"


def load_picks_file(path: Path | None = None) -> list[dict[str, Any]]:
    picks_file = path or (LOCALDATA / "picks_today.json")
    if not picks_file.exists():
        return []
    try:
        data = json.loads(picks_file.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)]


def pick_date(pick: dict[str, Any], fallback: str) -> str:
    for key in ("match_date", "date", "picked_for", "target_date"):
        value = pick.get(key)
        if value:
            return str(value)[:10]
    return fallback


def tag_picks(picks: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for pick in picks:
        p = dict(pick)
        p.setdefault("date", target)
        p.setdefault("picked_for", target)
        tagged.append(p)
    return tagged


def restore_picks_today(picks_text: str | None) -> None:
    """Restore the live ledger after a future-planner scan."""
    if picks_text is None:
        return
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    (LOCALDATA / "picks_today.json").write_text(picks_text)


def generate_daily_report(
    target_date: str,
    output_path: Path | None = None,
    source_picks: list[dict[str, Any]] | None = None,
    header_title: str | None = None,
    metadata_lines: list[str] | None = None,
) -> Path | None:
    """Generate a human-readable .txt summary matching Edge-Factory parity."""
    report_file = output_path or (LOCALDATA / f"picks_{target_date}.txt")
    picks_file = LOCALDATA / "picks_today.json"
    if source_picks is None and not picks_file.exists():
        return None
    try:
        picks = source_picks if source_picks is not None else load_picks_file(picks_file)
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            header_title or f"Racket Factory Picks — {target_date}",
            "=" * 60,
            f"Generated at: {now_ts}",
        ]
        if metadata_lines:
            lines.extend(metadata_lines)
        lines.append("")

        buckets: dict[str, list[dict[str, Any]]] = {}
        for p in picks:
            b = p.get("bucket", "UNKNOWN")
            buckets.setdefault(str(b), []).append(p)

        bucket_order = [
            "CERTIFIED_CLEAN",
            "CAUTION",
            "WATCHLIST",
            "WATCHLIST_NO_ODDS",
            "WATCHLIST_UNKNOWN_CTX",
            "SKIPPED_VETO",
            "SKIPPED_DEAD_EDGE",
        ]
        bucket_labels = {
            "CERTIFIED_CLEAN": "CERTIFIED CLEAN PICKS",
            "CAUTION": "CAUTION PICKS",
            "WATCHLIST": "WATCHLIST PICKS",
            "WATCHLIST_NO_ODDS": "WATCHLIST — NO MATCHED ODDS",
            "WATCHLIST_UNKNOWN_CTX": "WATCHLIST — UNKNOWN CONTEXT",
            "SKIPPED_VETO": "SKIPPED — VETO CONTEXT",
            "SKIPPED_DEAD_EDGE": "SKIPPED — DEAD EDGE (ROBBER slices / negative-EV — DO NOT BET)",
        }

        for b in bucket_order:
            bpicks = buckets.get(b, [])
            lines.append(f"\n{bucket_labels.get(b, b)}")
            lines.append("=" * 60)
            if not bpicks:
                lines.append("  (none)")
                continue

            for p in sorted(bpicks, key=lambda x: -float(x.get("confidence") or 0)):
                odds_val = p.get("odds")
                if odds_val is not None and str(odds_val).strip() not in {"nan", "<NA>", "None", ""}:
                    try: odds = f"@{float(odds_val):.2f}"
                    except: odds = f"@{odds_val}"
                else:
                    odds = "@n/a"

                ev_val = p.get("expected_value")
                ev_str = ""
                if ev_val is not None and str(ev_val).strip() not in {"nan", "<NA>", "None", ""}:
                    try:
                        ev_str = f"  EV {float(ev_val):+.2f}u"
                    except (TypeError, ValueError):
                        ev_str = ""

                label = p.get("slice_matched") or p.get("rule", "?")
                match = str(p.get("match", ""))[:42]
                kickoff = format_kickoff(p)
                pick_str = str(p.get("selected_player") or p.get("selected_side") or "?").upper()
                conf = float(p.get("confidence") or 0)
                if conf <= 1.0 and conf > 0:
                    conf *= 100.0

                lines.append(
                    f"  [{label}] {match:42s} KO {kickoff:5s} -> "
                    f"{pick_str:5s}  conf {conf:.0f}% {odds}{ev_str}"
                )
                lines.append(
                    f"     bucket={b}  "
                    f"tour={p.get('tour', 'UNKNOWN')}  series={p.get('series', 'UNKNOWN')}  surface={p.get('surface', 'UNKNOWN')}  source={p.get('source', 'UNKNOWN')}"
                )

        lines.append("")
        lines.append("⚠️  Flat stakes only. Best odds inflate ROI (~halve it).")
        lines.append("⚠️  Bet only what you can afford to lose.")

        LOCALDATA.mkdir(parents=True, exist_ok=True)
        report_file.write_text("\n".join(lines))
        print(f"\n>>> generate_daily_report")
        print(f"Report written: {report_file}")
        return report_file
    except Exception as exc:
        print(f"Could not generate report: {exc}")
        return None


def write_future_outputs(all_picks: list[dict[str, Any]], days: int, snapshot_as_of: str) -> None:
    """Write aggregate machine-readable next-day/future planner outputs."""
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    json_file = LOCALDATA / f"picks_next_{days}days.json"
    json_file.write_text(json.dumps(all_picks, indent=2, sort_keys=True))

    manifest_file = LOCALDATA / f"picks_next_{days}days_manifest.json"
    manifest = {
        "ledger_kind": "forecast",
        "snapshot_as_of": snapshot_as_of,
        "days": days,
        "row_count": len(all_picks),
    }
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"Future planner wrote: {json_file}")
    print(f"Future planner manifest: {manifest_file}")


def run_future_planner(
    start_date: str,
    days: int,
    target_picks: list[dict[str, Any]],
    run_as_of: str,
    env_prefix: str,
    child_env: dict[str, str],
) -> None:
    """Inline N-day planner using mine_edges.py as the Racket pick engine.

    This mirrors Edge Factory's future planner: today's picks are reused, then
    each future day is mined and written to its own forecast report/archive. The
    planner restores today's official picks_today.json before the pipeline syncs
    or notifies, so future picks never replace today's live ledger by accident.
    """
    if days <= 0:
        print("future_days <= 0 — skipping future planner")
        return

    print(f"\n>>> future planner ({days}-day reports)")
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    all_picks: list[dict[str, Any]] = tag_picks(target_picks, start_date)
    print(f"  {start_date}: reused target picks ({len(target_picks)} rows)")

    for offset in range(1, days):
        target = (start + timedelta(days=offset)).isoformat()
        output = run_capture(
            f"{env_prefix} PYTHONPATH=src python3 scripts/mine_edges.py --warehouse localdata/warehouse.csv.gz --date {target}",
            f"future planner: mine_edges {target}",
            env=child_env,
        )
        print_mine_run_summary(output)

        forecast_file = archived_picks_file(target)
        day_picks = load_picks_file(forecast_file)
        if not day_picks:
            print(f"  {target}: no future picks found")

        # Ensure the forecast ledger is tagged to the day it was mined for.
        day_picks = tag_picks(day_picks, target)
        forecast_file.write_text(json.dumps(day_picks, indent=2, sort_keys=True))

        all_picks = merge_picks(all_picks, day_picks)
        day_specific = [p for p in all_picks if pick_date(p, "9999-99-99") == target]
        generate_daily_report(
            target,
            source_picks=day_specific,
            metadata_lines=[f"Snapshot as of: {run_as_of}", "Ledger kind: forecast"],
        )
        print(f"  {target}: forecast rows {len(day_specific)}")

    all_picks.sort(
        key=lambda p: (
            pick_date(p, "9999-99-99"),
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "CAUTION": 2,
             "SKIPPED_DEAD_EDGE": 3, "WATCHLIST_NO_ODDS": 4, "WATCHLIST_UNKNOWN_CTX": 5,
             "SKIPPED_VETO": 6}.get(str(p.get("bucket")), 9),
            -float(p.get("expected_value") or 0.0),
            -int(p.get("source_count") or 0),
            str(p.get("match", "")),
        )
    )
    write_future_outputs(all_picks, days, run_as_of)


# ------------------------------------------------- autonomous smart schedule --
def _now_local() -> datetime:
    return datetime.now(local_tz())


def run_smart_auto(args: argparse.Namespace) -> None:
    """One autonomous iteration of the accumulating ledger (Edge-Factory parity).

    Decides the operational regime for *today* and dispatches a single pipeline
    run with the right flags, then exits.  Designed to be invoked once every few
    hours by CI (GitHub Actions) or a cron/`--auto-run` loop:

      * Case 1 — no official archive yet for today (typically the first wake-up
        of the day): run the FULL heavy pipeline (OddsPortal + TennisData + Daily).

      * Case 2 — today's archive already exists (every later wake-up): run a
        LIGHT intraday discovery pass that just fetches daily predictions and
        re-mines, WITHOUT touching the locked morning baseline.
    """
    now = _now_local()
    target = args.date or now.strftime("%Y-%m-%d")
    archive = archived_picks_file(target)
    archive_exists = archive.exists() and not args.force_repick

    print(f"\n=== Racket Factory Smart Autonomous Schedule — {now.strftime('%Y-%m-%d %H:%M:%S %Z')} ===")
    print(f"    target date : {target}")
    print(f"    archive     : {'EXISTS → intraday accumulating discovery' if archive_exists else 'MISSING/FORCED → full morning heavy run'}")

    args.date = target
    if archive_exists:
        args.intraday_only = True
        args.force_repick = False  # NEVER overwrite the morning baseline intraday
    else:
        args.intraday_only = False
    run_once(args)


# --------------------------------------------------------------- main --
def run_once(args: argparse.Namespace) -> None:
    """Execute exactly one full pipeline pass for ``args`` (no scheduling)."""
    target = args.date or date.today().isoformat()
    year = target[:4]

    run_as_of = make_run_as_of()
    load_daily_env()
    child_env = os.environ.copy()
    child_env["RACKET_FACTORY_RUN_AS_OF"] = run_as_of
    child_env.setdefault("RACKET_FACTORY_TZ", DEFAULT_LOCAL_TZ)
    env_prefix = f"RACKET_FACTORY_RUN_AS_OF={shlex.quote(run_as_of)}"
    oddsportal_delay = float(os.getenv("RACKET_FACTORY_ODDSPORTAL_DELAY", "30"))
    refresh_oddsportal = os.getenv("RACKET_FACTORY_REFRESH_ODDSPORTAL", "").strip().lower() in {"1", "true", "yes", "on"}

    print("=== Racket Factory Daily Pipeline (Tennis) ===")
    print(f"    target date : {target}")
    print(f"    mode        : {'auto-run' if args.auto_run else ('intraday-only' if args.intraday_only else 'full')}")
    print(f"    future_days : {args.future_days}")
    print(f"    run as-of   : {run_as_of} (tz={DEFAULT_LOCAL_TZ})")

    if not args.intraday_only:
        # 1. Official Source Captures (Heavy History)
        if refresh_oddsportal:
            run_soft(
                f"{env_prefix} PYTHONPATH=src python3 scripts/capture_"
                f"oddsportal.py --all --years {year} --no-checkpoint --delay {oddsportal_delay:g}",
                f"capture_oddsportal {year}",
                env=child_env,
            )
        else:
            print("\n>>> capture_oddsportal skipped")
            print("RACKET_FACTORY_REFRESH_ODDSPORTAL not set; using source/TennisData result refresh for daily run.")
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/backfill_tennisdata.py --year {year}",
            f"backfill_tennisdata {year}",
            env=child_env,
        )
    else:
        # REDTEAM Finding #6: in intraday mode we still need yesterday's
        # results to flow into the warehouse so the audit can measure
        # settled picks. We re-capture only the current year with the
        # checkpoint reset, which is one bulk pass per run and tolerates
        # CF re-challenge gracefully. Failures are non-fatal so an outage
        # in the results pass does not block picks.
        if refresh_oddsportal:
            run_soft(
                f"{env_prefix} PYTHONPATH=src python3 scripts/capture_"
                f"oddsportal.py --all --years {year} --no-checkpoint --delay {oddsportal_delay:g}",
                f"settle_yesterday_results {year}",
                env=child_env,
            )
        else:
            print("\n>>> settle_yesterday_results skipped")
            print("RACKET_FACTORY_REFRESH_ODDSPORTAL not set; skipping heavy OddsPortal intraday refresh.")

    # 2. Daily Prediction Sources
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/backfill_forebet.py --mode daily --days yesterday today tomorrow --warehouse localdata/warehouse.csv.gz --output-dir localdata", "backfill_forebet", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/backfill_foretennis.py --warehouse localdata/warehouse.csv.gz --output-dir localdata", "backfill_foretennis", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_predixsport.py --output-dir localdata", "capture_predixsport", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_betclan.py --output-dir localdata", "capture_betclan", env=child_env)

    # 3. Warehouse Resolution & Assembly
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py --data-dir localdata --output warehouse.csv.gz", "build_warehouse_initial", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/resolve_pending.py --warehouse localdata/warehouse.csv.gz --data-dir localdata", "resolve_pending", env=child_env)
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py --data-dir localdata --output warehouse.csv.gz", "build_warehouse_final", env=child_env)

    # 4. Mine Edges
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/mine_edges.py --warehouse localdata/warehouse.csv.gz --date {target}", "mine_edges", env=child_env)

    # 5. Archive by Kickoff & Lock the morning baseline
    picks_today = LOCALDATA / "picks_today.json"
    if picks_today.exists():
        try:
            current_picks = json.loads(picks_today.read_text())
            if not isinstance(current_picks, list):
                current_picks = []
        except Exception:
            current_picks = []

        distinct_dates = archive_picks_by_kickoff(current_picks, target)
        if target not in distinct_dates:
            distinct_dates.append(target)

        for d in distinct_dates:
            arch = archived_picks_file(d)
            if arch.exists():
                save_morning_baseline(d, arch.read_text(), overwrite=args.force_repick)
    else:
        archive = archived_picks_file(target)
        if archive.exists():
            save_morning_baseline(target, archive.read_text(), overwrite=args.force_repick)

    # 6. Generate human friendly TXT report + inline next-day planner (Edge-Factory parity)
    target_archive = archived_picks_file(target)
    if target_archive.exists():
        target_picks_text = target_archive.read_text()
        target_picks = load_picks_file(target_archive)
    elif picks_today.exists():
        target_picks_text = picks_today.read_text()
        target_picks = load_picks_file(picks_today)
    else:
        target_picks_text = None
        target_picks = []
    generate_daily_report(target, source_picks=target_picks)
    run_future_planner(target, args.future_days, target_picks, run_as_of, env_prefix, child_env)
    restore_picks_today(target_picks_text)

    # 7. Run Audit
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/audit_recent_picks.py --end {target} --days 30 --warehouse localdata/warehouse.csv.gz", "audit_recent_picks", env=child_env)

    # 8. Supabase Live Dashboard Sync (Optional)
    sync_script = ROOT / "scripts" / "sync_supabase.py"
    if sync_script.exists() and (os.getenv("SUPABASE_URL") or args.force_sync):
        run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/sync_supabase.py --picks {archived_picks_file(target)} --target-date {target} --replace-date", "sync_supabase", env=child_env)

    print(f"\n=== Pipeline Complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # 9. Optional WhatsApp heads-up
    archive = archived_picks_file(target)
    if not args.skip_notify and os.getenv("CALLMEBOT_APIKEY") and os.getenv("CALLMEBOT_PHONE"):
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/notify_whatsapp.py --date {target} --picks {archive}",
            "notify_whatsapp",
            env=child_env,
        )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    ap.add_argument("--intraday-only", action="store_true", help="Skip heavy captures; run live prediction fetch only")
    ap.add_argument("--skip-notify", action="store_true", help="Do not send the WhatsApp heads-up")
    ap.add_argument("--force-repick", action="store_true",
                    help="Overwrite the morning baseline lock (dangerous — only use for replays)")
    ap.add_argument("--force-sync", action="store_true",
                    help="Force execution of Supabase sync script even if SUPABASE_URL is not in local env.")
    ap.add_argument("--auto-run", action="store_true",
                    help="Run the autonomous smart-schedule loop forever (sleeps between iterations).")
    ap.add_argument("--auto-once", action="store_true",
                    help="Run exactly ONE smart-schedule iteration and exit (use this in CI).")
    ap.add_argument("--auto-interval-hours", type=float, default=3.0,
                    help="Sleep between --auto-run iterations (default: 3h, matching the CI cadence).")
    ap.add_argument("--future-days", type=int, default=2,
                    help="Days ahead for inline future planner (default: 2 = today + tomorrow). Use 0 to disable.")
    return ap


def main() -> None:
    import time

    args = _build_parser().parse_args()

    # Autonomous accumulating-ledger schedule
    if args.auto_once:
        run_smart_auto(args)
        return

    if args.auto_run:
        print(f"=== Starting Racket Factory Autonomous Service ({DEFAULT_LOCAL_TZ}) ===")
        while True:
            try:
                run_smart_auto(args)
            except (Exception, SystemExit) as exc:
                print(
                    f"\n⚠️ [Auto-Run] Iteration failed: {exc}. "
                    "Retrying on next scheduled window...",
                    file=sys.stderr,
                )
            next_run = _now_local() + timedelta(hours=args.auto_interval_hours)
            print(f"\n💤 Resting. Next iteration ~{next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}.")
            time.sleep(int(args.auto_interval_hours * 3600))

    # Default: one explicit pipeline pass
    run_once(args)


if __name__ == "__main__":
    main()
