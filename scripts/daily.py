#!/usr/bin/env python3
"""Hoop Factory single daily trigger.

This mirrors Edge Factory's operating style while keeping basketball as a
separate regime: capture -> warehouse -> mine -> emit watchlist/picks.

Operational safety
------------------
At start-up the pipeline pins ``HOOP_FACTORY_RUN_AS_OF`` to the local-time
"now".  picks_today.py uses that timestamp as the cutoff for same-day pick
eligibility, so the morning archive cannot be retroactively regenerated with
fresh (drifted) live sources once games have tipped off.  The env var is
inherited by every child subprocess in this run.

Outputs (Edge-Factory parity)
-----------------------------
For each run we materialise the full day ledger under ``localdata/``:

  picks_YYYY-MM-DD.json              full archive (every pick)
  picks_YYYY-MM-DD.txt               human-readable report
  picks_morning_YYYY-MM-DD.json      locked morning baseline (first write of the day)
  picks_audit_YYYY-MM-DD.md          per-day graded markdown
  picks_audit_rolling.json           rolling 30-day graded summary
  picks_next_2days.json              forward planner aggregate
  picks_next_2days_manifest.json     forward planner manifest
  picks_today.json                   filtered live snapshot (default hides started)
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_TZ = "Africa/Johannesburg"
LOCALDATA = ROOT / "localdata"

# Current-window sources used by the operator-facing consensus booster.
# Deep archive sources (progsport/dunksandthrees) are still handled by
# capture_daily.py; this block is the quick multi-source fixer for a target
# slate when consensus volume is thin.
BOOST_TIPSTER_SOURCES = (
    "feedinco",
    "sportus",
    "vitibet",
    "sportytrader",
    "basketballbets365",
    "forebet",
)
DEFAULT_BACKFILL_DAYS = 9
DEFAULT_BACKFILL_FORWARD_DAYS = 1



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
    """Capture combined stdout/stderr (used for the inline future planner so
    its per-day output stays concise)."""
    print(f"\n>>> {label}")
    result = subprocess.run(
        cmd, shell=True, cwd=ROOT, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        print(f"\nFAILED: {label}")
        sys.exit(result.returncode)
    return result.stdout


# ------------------------------------------------------------ archive helpers --
def archived_picks_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_{target_date}.json"


def morning_baseline_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_morning_{target_date}.json"


def save_morning_baseline(target_date: str, picks_text: str | None, *, overwrite: bool = False) -> None:
    """Lock the FIRST operational picks of the day so late runs cannot drift them.

    Mirrors Edge-Factory's behaviour: if a morning file already exists, do not
    overwrite unless ``overwrite`` is true (used by --force-repick).
    """
    if picks_text is None:
        return
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    path = morning_baseline_file(target_date)
    if path.exists() and not overwrite:
        return
    path.write_text(picks_text)


# ---------------------------------------------------- tipster boost backfill --
def _date_window(target_date: str, lookback_days: int, forward_days: int) -> tuple[str, str]:
    """Return inclusive local_backfill start/end dates around the target."""
    target_d = datetime.strptime(target_date, "%Y-%m-%d").date()
    start_d = target_d - timedelta(days=max(0, lookback_days - 1))
    end_d = target_d + timedelta(days=max(0, forward_days))
    return start_d.isoformat(), end_d.isoformat()


def _parse_sources(raw: str | None) -> list[str]:
    if not raw:
        return list(BOOST_TIPSTER_SOURCES)
    return [s.strip() for s in raw.split(",") if s.strip()]


def run_tipster_backfill_block(
    target_date: str,
    *,
    lookback_days: int,
    forward_days: int,
    max_seconds: int,
    sources: list[str],
    env_prefix: str,
    env: dict,
) -> None:
    """Run the six-line current-window tipster backfill bootstrap.

    This is intentionally separate from capture_daily.py. capture_daily performs
    the normal deep/current capture. This block is the operator's explicit
    "no consensus / thin slate" fixer: get multiple current-window voices, then
    the caller MUST rebuild the warehouse and re-mine consensus before picks.
    """
    start_s, end_s = _date_window(target_date, lookback_days, forward_days)
    print(
        f"\n>>> tipster boost backfill {start_s}..{end_s} "
        f"({len(sources)} sources, max_seconds={max_seconds})"
    )
    for source in sources:
        cmd = (
            f"{env_prefix} PYTHONPATH=src python3 scripts/local_backfill.py "
            f"{shlex.quote(source)} {start_s} {end_s} --max-seconds {int(max_seconds)}"
        )
        # Individual current-window sources can be empty/off-season or temporarily
        # unavailable. Continue, then rebuild/mine from whatever data exists.
        run_soft(cmd, f"boost_backfill {source} {start_s}..{end_s}", env=env)


# ----------------------------------------------------------- future planner --
def load_archived(day: str) -> list[dict]:
    """Read picks_{day}.json back as a list of dicts (empty if missing)."""
    path = archived_picks_file(day)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def write_future_outputs(all_picks: list[dict], days: int, snapshot_as_of: str) -> None:
    """Write the aggregate machine-readable future-picks file + forecast manifest."""
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    json_file = LOCALDATA / f"picks_next_{days}days.json"
    manifest_file = LOCALDATA / f"picks_next_{days}days_manifest.json"

    json_file.write_text(json.dumps(all_picks, indent=2, sort_keys=True))
    manifest = {
        "ledger_kind": "forecast",
        "snapshot_as_of": snapshot_as_of,
        "days": days,
        "row_count": len(all_picks),
        "as_of_tz": DEFAULT_LOCAL_TZ,
    }
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"Future planner wrote: {json_file}")
    print(f"Future planner manifest: {manifest_file}")


def run_future_planner(start_date: str, days: int, env_prefix: str, env: dict) -> None:
    """Inline N-day planner using picks_today.py as the only pick engine.

    For each future day (start+1 .. start+days-1) we re-invoke picks_today.py
    with the same run-as-of timestamp.  Each call writes the full day archive
    automatically; we aggregate them into a single machine-readable forward
    ledger.
    """
    if days <= 0:
        print("future_days <= 0 — skipping future planner")
        return

    print(f"\n>>> future planner ({days}-day reports)")

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    all_picks: list[dict] = []

    # Day 0 picks live in picks_today.json / picks_{start}.json
    day_zero = load_archived(start_date)
    for p in day_zero:
        p.setdefault("picked_for", start_date)
    all_picks.extend(day_zero)
    print(f"  {start_date}: reused target-day archive ({len(day_zero)} rows)")

    for offset in range(1, days):
        target = (start + timedelta(days=offset)).isoformat()
        # Future planner uses --show-started so the audit can see late slate
        # picks that are still pre-match in the future (i.e. today + N).
        cmd = (
            f"{env_prefix} PYTHONPATH=src python3 scripts/picks_today.py {target}"
            f" --archive-only --show-started"
        )
        output = run_capture(cmd, f"future planner: picks_today {target}", env=env)
        # Echo a compact summary line for the user
        for line in output.splitlines():
            if line.startswith("Archive:") or line.startswith("pre-match filter"):
                print(f"  {line.strip()}")

        day_picks = load_archived(target)
        for p in day_picks:
            p.setdefault("picked_for", target)
        all_picks.extend(day_picks)
        print(f"  {target}: added {len(day_picks)} rows from archive")

    # Deterministic sort: date → bucket → confidence desc → match
    def _sort_key(p: dict) -> tuple:
        return (
            str(p.get("date") or "")[:10],
            str(p.get("bucket") or ""),
            -float(p.get("confidence") or 0.0),
            str(p.get("match") or ""),
        )
    all_picks.sort(key=_sort_key)
    write_future_outputs(all_picks, days, env.get("HOOP_FACTORY_RUN_AS_OF", ""))


# ------------------------------------------------- autonomous smart schedule --
def _now_local() -> datetime:
    return datetime.now(local_tz())


def run_smart_auto(args: argparse.Namespace) -> None:
    """One autonomous iteration of the accumulating ledger (Edge-Factory parity).

    Decides the operational regime for *today* and dispatches a single pipeline
    run with the right flags, then exits.  Designed to be invoked once every few
    hours by CI (GitHub Actions) or a cron/`--auto-run` loop:

      * Case 1 — no official archive yet for today (typically the first wake-up
        of the day): run the FULL heavy pipeline (capture -> warehouse -> mine
        -> certify -> picks), lock the morning baseline, audit, plan forward,
        and sync.  This is the "morning" run.

      * Case 2 — today's archive already exists (every later wake-up): run a
        LIGHT intraday discovery pass (`--picks-only`) that re-mines the late
        slate, accumulates any brand-new picks into the SAME day archive, and
        re-syncs — WITHOUT touching the locked morning baseline.  Heavy warehouse
        rebuilds are skipped for speed.

    Either way exactly one pipeline executes and the process returns.
    """
    now = _now_local()
    target = args.date or now.strftime("%Y-%m-%d")
    archive = archived_picks_file(target)
    archive_exists = archive.exists() and not args.force_repick

    print(f"\n=== Hoop Factory Smart Autonomous Schedule — {now.strftime('%Y-%m-%d %H:%M:%S %Z')} ===")
    print(f"    target date : {target}")
    print(f"    archive     : {'EXISTS → intraday accumulating discovery' if archive_exists else 'MISSING/FORCED → full morning heavy run'}")

    args.date = target
    if archive_exists:
        # Intraday: keep the locked morning baseline, accumulate late slate only.
        # picks-only skips the heavy warehouse rebuild; backfill the current
        # window so genuinely new fixtures/odds are discovered, then re-mine.
        args.picks_only = True
        args.backfill_tipsters = True
        args.force_repick = False  # NEVER overwrite the morning baseline intraday
    else:
        # Morning: full heavy pipeline. force_repick already honoured below.
        args.picks_only = False
    run_once(args)


# --------------------------------------------------------------- main --
def run_once(args: argparse.Namespace) -> None:
    """Execute exactly one full pipeline pass for ``args`` (no scheduling)."""
    target = args.date or date.today().isoformat()
    start = (datetime.strptime(target, "%Y-%m-%d").date() - timedelta(days=args.capture_days)).isoformat()

    # Pin the operational "now" for this entire pipeline run.  picks_today.py
    # will see HOOP_FACTORY_RUN_AS_OF in its env and treat it as the cutoff
    # for same-day pick eligibility.  Inheriting through subprocess env means
    # capture, mine, and emit all share the same frozen timestamp.
    run_as_of = make_run_as_of()
    child_env = os.environ.copy()
    child_env["HOOP_FACTORY_RUN_AS_OF"] = run_as_of
    child_env.setdefault("HOOP_FACTORY_TZ", DEFAULT_LOCAL_TZ)
    child_env.setdefault("HOOP_FACTORY_MIN_LEAD_MINUTES", "30")
    env_prefix = f"HOOP_FACTORY_RUN_AS_OF={shlex.quote(run_as_of)}"

    print("=== Hoop Factory Daily Pipeline (Consensus-First) ===")
    print(f"    target date : {target}")
    print(f"    capture     : {start}..{target}")
    print(f"    mode        : {'auto-run' if args.auto_run else ('picks-only' if args.picks_only else 'full')}")
    print(f"    run as-of   : {run_as_of} (tz={DEFAULT_LOCAL_TZ})")
    print(f"    min lead    : {child_env.get('HOOP_FACTORY_MIN_LEAD_MINUTES')}m")
    if args.backfill_tipsters:
        print(f"    backfill    : tipster boost ({args.backfill_days}d back, +{args.backfill_forward_days}d)")

    if not args.picks_only:
        # 1. Official Source Captures
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/capture_nba.py --start {start} --end {target} --season-type '{args.season_type}'",
            f"capture_nba {start}..{target}",
            env=child_env,
        )
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/capture_espn.py --start {start} --end {target}",
            f"capture_espn fallback {start}..{target}",
            env=child_env,
        )
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/capture_schedule.py {target}",
            f"capture_schedule nba_api {target}",
            env=child_env,
        )

        # 2. Tipster Consensus Captures
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_daily.py --skip-build", "capture_tipsters", env=child_env)

        if args.backfill_tipsters:
            run_tipster_backfill_block(
                target,
                lookback_days=args.backfill_days,
                forward_days=args.backfill_forward_days,
                max_seconds=args.backfill_max_seconds,
                sources=_parse_sources(args.backfill_sources),
                env_prefix=env_prefix,
                env=child_env,
            )

        if not args.skip_odds and os.getenv("ODDS_API_KEY"):
            run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_oddsapi.py", "capture_oddsapi", env=child_env)
        elif not args.skip_odds:
            print("\n>>> capture_oddsapi")
            print("ODDS_API_KEY unset — skipping optional live odds capture")

        # OddsPortal historical backfill — closes the post-Jan-2023 odds gap.
        # Only runs when explicitly opted in via ODDS_PORTAL_BACKFILL=1 to
        # avoid surprise 30-second sleep costs on every daily run.  Run with
        # `ODDS_PORTAL_BACKFILL=1 ODDS_PORTAL_FROM=2023 ODDS_PORTAL_TO=2024
        #  python3 scripts/daily.py --auto-run` to backfill one season.
        if not args.skip_odds and os.getenv("ODDS_PORTAL_BACKFILL") == "1":
            from_year = os.getenv("ODDS_PORTAL_FROM", "2023")
            to_year = os.getenv("ODDS_PORTAL_TO", str(date.today().year))
            run_soft(
                f"{env_prefix} PYTHONPATH=src python3 scripts/capture_oddsportal.py "
                f"--from {from_year} --to {to_year}",
                f"capture_oddsportal {from_year}-{to_year}",
                env=child_env,
            )

        # 3. Build the Warehouse (Tipsters + Official Data)
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py", "build_warehouse", env=child_env)

        # 4. Track per-source accuracy against historical results — produces
        #    localdata/source_accuracy.json with per-(source × confidence_bucket)
        #    verdicts.  MUST run before mine_consensus so consensus can load
        #    the bucket calibration table.
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/source_accuracy.py", "source_accuracy", env=child_env)

        # 4b. Mine the Consensus layer with per-bucket calibration (creates
        #     edges_consensus.json/csv).  Reads the bucket table written above.
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/mine_consensus.py", "mine_consensus", env=child_env)

        # 4c. Certify consensus ROI against Kaggle SBR odds (HARD: picks_today
        #     certification gating and confidence baseline depend on this report)
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/certify_roi.py", "certify_roi", env=child_env)

        # 5. Build traditional Edges / Ratings on top of consensus
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/mine_edges.py", "mine_edges", env=child_env)
        run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/assay_purity.py", "assay_purity", env=child_env)
        run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/decay_monitor.py", "decay_monitor", env=child_env)

    elif args.backfill_tipsters:
        # picks-only normally skips capture/build/mine. If the operator requests
        # a tipster boost, never emit picks from stale consensus files: rebuild
        # and re-mine first.
        run_tipster_backfill_block(
            target,
            lookback_days=args.backfill_days,
            forward_days=args.backfill_forward_days,
            max_seconds=args.backfill_max_seconds,
            sources=_parse_sources(args.backfill_sources),
            env_prefix=env_prefix,
            env=child_env,
        )
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py", "build_warehouse_after_backfill", env=child_env)
        run(f"{env_prefix} PYTHONPATH=src python3 scripts/mine_consensus.py", "mine_consensus_after_backfill", env=child_env)

    # 6. Emit target-date picks (writes both filtered live + full day archive).
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/picks_today.py {target}", f"picks_today {target}", env=child_env)

    # 7. Lock the morning baseline (first write of the day wins; subsequent
    #    runs preserve the original operational picks).
    archive = archived_picks_file(target)
    if archive.exists():
        save_morning_baseline(target, archive.read_text(), overwrite=args.force_repick)

    # 8. Rolling grader (audit_recent_picks.py) — settles archived picks
    #    against completed_games in warehouse.duckdb.
    if not args.skip_audit:
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/audit_recent_picks.py --end {target} --days {args.audit_days}",
            f"audit_recent_picks {target} [{args.audit_days}d]",
            env=child_env,
        )

    # 9. Forward planner — picks_today.py for the next (future_days - 1) days.
    if not args.skip_future:
        run_future_planner(target, args.future_days, env_prefix, child_env)

    # 10. Sync to Supabase
    if not args.skip_sync and os.getenv("SUPABASE_URL"):
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/sync_supabase.py --target-date {target} --replace-date",
            "sync_supabase",
            env=child_env,
        )
    elif not args.skip_sync:
        print("\n>>> sync_supabase")
        print("SUPABASE_URL unset — skipping Supabase sync")

    print(f"\n=== Pipeline Complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # 11. Optional WhatsApp heads-up (Edge-Factory parity). Off unless the
    #     CallMeBot creds are present; never blocks or fails the pipeline.
    if not args.skip_notify and os.getenv("CALLMEBOT_APIKEY") and os.getenv("CALLMEBOT_PHONE"):
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/notify_whatsapp.py --date {target}",
            "notify_whatsapp",
            env=child_env,
        )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    ap.add_argument("--picks-only", action="store_true", help="Skip capture/build/mine; run picks only")
    ap.add_argument("--capture-days", type=int, default=14, help="Lookback days for NBA capture")
    ap.add_argument("--season-type", default="Regular Season")
    ap.add_argument("--skip-odds", action="store_true", help="Do not call optional live odds API")
    ap.add_argument("--skip-sync", action="store_true", help="Do not push to Supabase")
    ap.add_argument("--skip-audit", action="store_true", help="Do not run the rolling grader")
    ap.add_argument("--skip-future", action="store_true", help="Do not run the forward planner")
    ap.add_argument("--skip-notify", action="store_true", help="Do not send the WhatsApp heads-up")
    ap.add_argument("--future-days", type=int, default=2,
                    help="Number of forward days to plan (default: 2 → picks_next_2days.json)")
    ap.add_argument("--audit-days", type=int, default=30,
                    help="Rolling window length for the audit grader (default: 30)")
    ap.add_argument("--force-repick", action="store_true",
                    help="Overwrite the morning baseline lock (dangerous — only use for replays)")
    ap.add_argument("--backfill-tipsters", action="store_true",
                    help="Run the current-window multi-source tipster backfill booster "
                         "before picks. Always followed by build_warehouse + mine_consensus.")
    ap.add_argument("--backfill-days", type=int, default=DEFAULT_BACKFILL_DAYS,
                    help=f"Lookback days for --backfill-tipsters (default: {DEFAULT_BACKFILL_DAYS}).")
    ap.add_argument("--backfill-forward-days", type=int, default=DEFAULT_BACKFILL_FORWARD_DAYS,
                    help=f"Forward days for --backfill-tipsters (default: {DEFAULT_BACKFILL_FORWARD_DAYS}).")
    ap.add_argument("--backfill-max-seconds", type=int, default=300,
                    help="Per-source local_backfill time budget for --backfill-tipsters (default: 300).")
    ap.add_argument("--backfill-sources", default=None,
                    help="Comma-separated source override for --backfill-tipsters. "
                         "Default: feedinco,sportus,vitibet,sportytrader,basketballbets365")
    # forebet now in DEEP_SOURCES (capture_daily.py)
    ap.add_argument("--auto-run", action="store_true",
                    help="Run the autonomous smart-schedule loop forever (sleeps between iterations).")
    ap.add_argument("--auto-once", action="store_true",
                    help="Run exactly ONE smart-schedule iteration and exit (use this in CI).")
    ap.add_argument("--auto-interval-hours", type=float, default=3.0,
                    help="Sleep between --auto-run iterations (default: 3h, matching the CI cadence).")
    return ap


def main() -> None:
    import time

    args = _build_parser().parse_args()

    # Autonomous accumulating-ledger schedule (Edge-Factory parity).
    if args.auto_once:
        run_smart_auto(args)
        return

    if args.auto_run:
        print(f"=== Starting Hoop Factory Autonomous Service ({DEFAULT_LOCAL_TZ}) ===")
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

    # Default: one explicit pipeline pass with whatever flags were given.
    run_once(args)


if __name__ == "__main__":
    main()
