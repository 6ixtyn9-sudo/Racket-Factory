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
sys.path.insert(0, str(ROOT))
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


def run_critical_grade(cmd: str, *, env: dict | None, run_as_of: str) -> None:
    """Record interruption/failure durably and stop financial downstream work."""
    grade_status = LOCALDATA / "auto_tickets_grade_status.json"
    marker = {"run_as_of": run_as_of, "github_run_id": os.getenv("GITHUB_RUN_ID"),
              "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT")}
    grade_status.write_text(json.dumps({**marker, "status": "running"}))
    print("\n>>> auto_tickets_grade (settle past slips)", flush=True)
    grade = subprocess.run(cmd, shell=True, cwd=ROOT, env=env)
    grade_status.write_text(json.dumps({**marker, "status": "ok" if grade.returncode == 0 else "failed",
                                        "returncode": grade.returncode}))
    if grade.returncode:
        print("FAILED: grading; ledger forensics and downstream sync skipped. Existing reports are not current.", file=sys.stderr)
        raise SystemExit(grade.returncode)


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


def forecast_picks_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_forecast_{target_date}.json"


def forecast_report_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_forecast_{target_date}.txt"


def save_forecast_outputs(
    target_date: str,
    picks: list[dict[str, Any]],
    run_as_of: str,
) -> None:
    """Save the forecast ledger for a target date.

    Low-bloat policy:
      - one forecast JSON per target date
      - one forecast TXT per target date
      - last forecast run wins

    Official same-day ledgers remain separate:
      localdata/picks_YYYY-MM-DD.json/txt
    """
    LOCALDATA.mkdir(parents=True, exist_ok=True)

    tagged: list[dict[str, Any]] = []
    for row in picks:
        p = dict(row)
        p["ledger_kind"] = "forecast"
        p["forecast_for"] = target_date
        p["forecast_as_of"] = run_as_of
        tagged.append(p)

    forecast_picks_file(target_date).write_text(json.dumps(tagged, indent=2, sort_keys=True))
    print(f"Forecast ledger written: {forecast_picks_file(target_date)}")

def morning_baseline_file(target_date: str) -> Path:
    # Deprecated: morning baseline removed per user request (focus on autobets)
    return LOCALDATA / f"picks_morning_{target_date}.json"


def save_morning_baseline(target_date: str, picks_text: str | None, *, overwrite: bool = False) -> None:
    """Deprecated: morning baseline disabled — user wants autobets only, no morning duplicate."""
    return


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
             "SKIPPED_DEAD_EDGE": 3, "SKIPPED_LOW_ODDS": 3, "WATCHLIST_NO_ODDS": 4, "WATCHLIST_UNKNOWN_CTX": 5,
             "SKIPPED_VETO": 6}.get(str(p.get("bucket")), 9),
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
            "FADE",
            "WATCHLIST_NO_ODDS",
            "WATCHLIST_UNKNOWN_CTX",
            "SKIPPED_VETO",
            "SKIPPED_DEAD_EDGE",
            "SKIPPED_LOW_ODDS",
        ]
        bucket_labels = {
            "CERTIFIED_CLEAN": "CERTIFIED CLEAN PICKS",
            "CAUTION": "CAUTION PICKS",
            "WATCHLIST": "WATCHLIST PICKS",
            "FADE": "FADE PICKS — validated contrarian (bet AGAINST the model's pick; slice ROI is the edge basis, conf = faded side)",
            "WATCHLIST_NO_ODDS": "WATCHLIST — NO MATCHED ODDS",
            "WATCHLIST_UNKNOWN_CTX": "WATCHLIST — UNKNOWN CONTEXT",
            "SKIPPED_VETO": "SKIPPED — VETO CONTEXT",
            "SKIPPED_DEAD_EDGE": "SKIPPED — DEAD EDGE (ROBBER or legacy skip — DO NOT BET)",
            "SKIPPED_LOW_ODDS": "SKIPPED — INVALID OR BELOW-FLOOR ODDS (1.30 MINIMUM)",
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
                edge_bits = []
                if p.get("edge_verdict"):
                    edge_bits.append(f"edge={p.get('edge_verdict')}")
                if p.get("edge_tier"):
                    edge_bits.append(f"tier={p.get('edge_tier')}")
                if p.get("edge_grade"):
                    edge_bits.append(f"grade={p.get('edge_grade')}")
                if p.get("edge_n"):
                    edge_bits.append(f"n={p.get('edge_n')}")
                if p.get("roi_estimate"):
                    edge_bits.append(f"roi={p.get('roi_estimate')}")
                edge_str = "  " + "  ".join(edge_bits) if edge_bits else ""

                lines.append(
                    f"     bucket={b}  "
                    f"tour={p.get('tour', 'UNKNOWN')}  series={p.get('series', 'UNKNOWN')}  surface={p.get('surface', 'UNKNOWN')}  source={p.get('source', 'UNKNOWN')}"
                    f"{edge_str}"
                )

        # No-slice diagnostics (run 35402614701: 81 candidates dropped with
        # no operator-visible trace). mine_edges writes picks_unmatched_<date>.json
        # on every day where candidates matched no exportable slice.
        unmatched_file = LOCALDATA / f"picks_unmatched_{target_date}.json"
        unmatched_rows: list[dict[str, Any]] = []
        if unmatched_file.exists():
            try:
                loaded = json.loads(unmatched_file.read_text())
                if isinstance(loaded, list):
                    unmatched_rows = loaded
            except Exception:
                unmatched_rows = []
        if unmatched_rows:
            lines.append("")
            lines.append(f"NO-SLICE CANDIDATES (diagnostic — matched no exportable slice; NOT bets)  [{len(unmatched_rows)} rows]")
            lines.append("=" * 60)
            for u in unmatched_rows[:30]:
                conf = u.get("confidence")
                if conf:
                    c = float(conf)
                    if 0 < c <= 1.0:  # dump stores decimal prob; pick json stores percent
                        c *= 100.0
                    conf_str = f"{c:.0f}%"
                else:
                    conf_str = "n/a"
                odds_val = u.get("odds")
                odds_str = f"@{float(odds_val):.2f}" if odds_val else "@n/a"
                lines.append(
                    f"  {str(u.get('match', '?'))[:42]:42s} KO {str(u.get('kickoff', 'n/a'))[:5]:5s}  "
                    f"{str(u.get('tour', '?'))}/{str(u.get('_surface', '?'))}  conf={u.get('pred_confidence', '?')}  "
                    f"agree={u.get('cross_source_agree', '?')}  band={u.get('fav_odds_band', '?')}  {odds_str} {conf_str}"
                )
                missing = u.get("missing_dims") or []
                closest = str(u.get("closest_slice") or "?")[:60]
                lines.append(f"     nearest: {closest}  missing: {', '.join(missing[:4]) or 'n/a'}")
            if len(unmatched_rows) > 30:
                lines.append(f"  ... and {len(unmatched_rows) - 30} more (full list in picks_unmatched_{target_date}.json)")

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
            f"{env_prefix} PYTHONPATH=src python3 scripts/mine_edges.py --warehouse localdata/warehouse.csv.gz --bet-side prediction --date {target}",
            f"future planner: mine_edges {target}",
            env=child_env,
        )
        print_mine_run_summary(output)

        forecast_file = archived_picks_file(target)
        day_picks = load_picks_file(forecast_file)
        if not day_picks:
            print(f"  {target}: no future picks found")

        # Ensure the forecast ledger is tagged to the day it was mined for.
        # Do NOT persist it back to picks_YYYY-MM-DD.json; that filename is
        # reserved for official same-day ledgers. Forecast persistence happens
        # below via picks_forecast_YYYY-MM-DD.json/txt.
        day_picks = tag_picks(day_picks, target)

        all_picks = merge_picks(all_picks, day_picks)
        day_specific = [p for p in all_picks if pick_date(p, "9999-99-99") == target]
        generate_daily_report(
            target,
            output_path=forecast_report_file(target),
            source_picks=day_specific,
            metadata_lines=[f"Snapshot as of: {run_as_of}", "Ledger kind: forecast"],
        )
        save_forecast_outputs(target, day_specific, run_as_of)

        # mine_edges.py writes picks_YYYY-MM-DD.json as its standard output.
        # During future planning that file is only an intermediate. Remove it
        # so picks_YYYY-MM-DD.* remains reserved for official same-day ledgers.
        for stale_path in (archived_picks_file(target), LOCALDATA / f"picks_{target}.txt"):
            try:
                if stale_path.exists():
                    stale_path.unlink()
                    print(f"Removed future official placeholder: {stale_path}")
            except Exception as exc:
                print(f"WARNING: could not remove future official placeholder {stale_path}: {exc}")

        print(f"  {target}: forecast rows {len(day_specific)}")

    all_picks.sort(
        key=lambda p: (
            pick_date(p, "9999-99-99"),
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "CAUTION": 2,
             "SKIPPED_DEAD_EDGE": 3, "SKIPPED_LOW_ODDS": 3, "WATCHLIST_NO_ODDS": 4, "WATCHLIST_UNKNOWN_CTX": 5,
             "SKIPPED_VETO": 6}.get(str(p.get("bucket")), 9),
            -int(p.get("source_count") or 0),
            str(p.get("match", "")),
        )
    )
    write_future_outputs(all_picks, days, run_as_of)


# ------------------------------------------------- autonomous smart schedule --
def _now_local() -> datetime:
    return datetime.now(local_tz())


# Explicitly approved for guarded rebuilds. Metered providers remain optional
# and are not contacted here without a verified durable quota authority.
REBUILD_REQUIRED_SOURCES = ("betclan", "betexplorer")
REBUILD_OPTIONAL_SOURCES = ("predixsport", "oddsportal", "theoddsapi", "bzzoiro")



def collect_rebuild_sources(target_date: str, *, progress=None) -> dict[str, Any]:
    """Collect once, directly into memory, without caches or publication.

    Exceptions, ambiguous empty responses and partial detail-page crawls are
    distinct from a verified empty board. No provider is retried via a relay.
    """
    from racketfactory.sources.betclan import BetClanPredictor
    from racketfactory.sources.predixsport import PredixSportPredictor
    from racketfactory.sources import betexplorer, oddsportal_upcoming
    from racketfactory.odds_compare import merge_comparison_rows

    result: dict[str, Any] = {"date": target_date, "observed_at": make_run_as_of(),
                              "outcomes": {}, "predictions": [], "comparisons": []}
    outcomes = result["outcomes"]
    def notify(provider, phase):
        if progress:
            progress(provider, phase, dict(outcomes))
    notify("betclan", "starting")
    betclan = BetClanPredictor()
    try:
        rows = betclan.fetch_daily()
        status = getattr(betclan, "last_collection_status", "unknown")
        outcomes["betclan"] = {"status": status, "rows": len(rows)}
        result["predictions"].extend(dict(row, source="BetClan") for row in rows)
    except Exception as exc:
        outcomes["betclan"] = {"status": "failed", "error_type": type(exc).__name__}
    notify("betexplorer", "starting")
    try:
        be_metrics = {}
        if os.getenv(betexplorer.DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
            be_rows, status = [], "disabled"
        else:
            be_rows, status = betexplorer._fetch_live(target_date, with_status=True, metrics=be_metrics)
        outcomes["betexplorer"] = {"status": status, "rows": len(be_rows), "parser_counts": be_metrics}
    except Exception as exc:
        be_rows = []
        outcomes["betexplorer"] = {"status": "failed", "error_type": type(exc).__name__, "failure_kind": getattr(exc, "kind", "unexpected")}
    notify("predixsport", "starting")
    try:
        px_rows = PredixSportPredictor().fetch_daily()
        outcomes["predixsport"] = {"status": "rows_observed" if px_rows else "unknown", "rows": len(px_rows)}
        result["predictions"].extend(dict(row, source="PredixSport") for row in px_rows)
    except Exception as exc:
        outcomes["predixsport"] = {"status": "failed", "error_type": type(exc).__name__}
    # Discover prices only for uniquely matched prediction fixtures; discovery
    # rows are not bookmaker quotes. A missing detail is not a verified empty.
    notify("betexplorer_details", "starting")
    from racketfactory.quote_enrichment import collect_prediction_comparisons
    detail_offers, detail_counts, partial = collect_prediction_comparisons(
        target_date, result["predictions"], be_rows, outcomes.get("betexplorer", {}).get("status", "unknown"))
    if partial:
        outcomes["betexplorer"]["status"] = "partial"
    outcomes["betexplorer"]["detail_counts"] = detail_counts
    notify("betexplorer_details", "finished")
    notify("oddsportal", "starting")
    try:
        if os.getenv(oddsportal_upcoming.DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
            op_rows = []
            outcomes["oddsportal"] = {"status": "disabled"}
        else:
            page = oddsportal_upcoming._page_for_target(target_date)
            if page is None:
                op_rows = []
                outcomes["oddsportal"] = {"status": "unknown"}
            else:
                path, _ = page
                extras = oddsportal_upcoming.TODAY_EXTRA_PATHS if target_date == datetime.now(local_tz()).date().isoformat() else oddsportal_upcoming.TOMORROW_EXTRA_PATHS
                op_rows = oddsportal_upcoming._fetch_live(path, target_date, extra_paths=extras)
                status = ("partial" if any(row.get("_collection_partial") for row in op_rows)
                          else "rows_observed" if op_rows else "unknown")
                outcomes["oddsportal"] = {"status": status, "rows": len(op_rows)}
    except Exception as exc:
        op_rows = []
        outcomes["oddsportal"] = {"status": "failed", "error_type": type(exc).__name__}
    notify("oddsportal", "finished")
    result["comparisons"] = merge_comparison_rows(be_rows, op_rows)
    # Keep both raw sources so an unverified primary quote never hides a
    # separately verifiable optional-source quote for the same fixture.
    # Keep discovery rows when no detail offers exist so unknown provenance
    # cannot be mistaken for a verified empty market.
    result["raw_quotes"] = {"betexplorer": detail_offers or be_rows, "oddsportal": op_rows}
    for name in ("theoddsapi", "bzzoiro"):
        outcomes[name] = {"status": "not_attempted", "reason": "durable quota authority not established for rebuild"}
    result["status"] = ("complete" if all(outcomes.get(name, {}).get("status") == "complete"
                                           for name in REBUILD_REQUIRED_SOURCES) else "insufficient")
    return result


def stage_fresh_rebuild_picks(target_date: str, collection: dict, workdir: Path) -> list[dict]:
    """Use the existing live normalizer and miner against an isolated snapshot."""
    import pandas as pd
    from racketfactory.warehouse import build_live_rows
    if collection.get("status") != "complete":
        raise RuntimeError("required fresh collection is incomplete")
    target_predictions = [row for row in collection.get("predictions", [])
                          if str(row.get("match_date") or "")[:10] == target_date]
    from datetime import timezone
    from racketfactory.odds_compare import merge_comparison_rows
    from collections import Counter
    from math import isfinite
    from racketfactory.execution import rebuild_quote_execution_problem

    # Count independent failures, not just the first; no prices or fixture
    # identities are written to the diagnostics artifact.
    counts: dict[str, Any] = {"sources": {}}
    def inspect(name, rows):
        reasons = Counter()
        accepted = []
        pairs = 0
        for row in rows:
            failures = []
            try:
                h, a = float(row.get("odds_home")), float(row.get("odds_away"))
                pair = isfinite(h) and isfinite(a) and h > 1 and a > 1
            except (TypeError, ValueError, OverflowError):
                pair = False
            pairs += int(pair)
            if not pair: failures.append("invalid_or_missing_pair")
            market = str(row.get("market") or "").strip().lower()
            period = str(row.get("period") or "").strip().lower()
            if not market: failures.append("market_missing")
            elif market not in {"h2h", "1x2", "match_winner", "match winner"}: failures.append("market_incompatible")
            if not period: failures.append("period_missing")
            elif period not in {"match", "full", "full_match", "full match", "0"}: failures.append("period_incompatible")
            if row.get("line") is not None and str(row.get("line")).strip(): failures.append("line_incompatible")
            try:
                observed = datetime.fromisoformat(str(row.get("observed_at") or "").replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - observed).total_seconds() if observed.tzinfo else -1
                if not 0 <= age <= 30 * 60: failures.append("observation_stale_or_future")
            except (TypeError, ValueError, OverflowError):
                failures.append("observation_missing_or_invalid")
            if str(row.get("match_date") or "")[:10] != target_date: failures.append("date_mismatch")
            if not all(row.get(k) for k in ("player_home", "player_away", "source", "bookmaker")):
                failures.append("fixture_source_or_bookmaker_missing")
            execution_problem = rebuild_quote_execution_problem(row)
            if execution_problem: failures.append("execution:" + execution_problem)
            if failures:
                reasons.update(failures)
            else:
                accepted.append(row)
        counts["sources"][name] = {"fixture_rows": len(rows), "quote_pairs": pairs,
                                   "eligible_rows": len(accepted), "rejection_reasons": dict(reasons)}
        return accepted

    raw = collection.get("raw_quotes")
    if isinstance(raw, dict):
        be_rows, op_rows = raw.get("betexplorer", []), raw.get("oddsportal", [])
        comparisons = list(be_rows) + list(op_rows)
        be_verified = inspect("betexplorer", be_rows)
        # Several bookmaker offers on one event are not multiple fixtures.
        # Pick deterministically by bookmaker identity, never by best price.
        chosen = {}
        for row in sorted(be_verified, key=lambda r: (str(r.get("event_id") or ""), str(r.get("bookmaker") or "").casefold(), str(r.get("bookmaker_id") or ""))):
            key = (str(row.get("event_id") or ""), str(row.get("match_date") or ""),
                   str(row.get("player_home") or ""), str(row.get("player_away") or ""))
            chosen.setdefault(key, row)
        verified = merge_comparison_rows(list(chosen.values()), inspect("oddsportal", op_rows))
    else:
        comparisons = collection.get("comparisons", [])
        verified = inspect("comparison", comparisons)
    counts["verified_merged_rows"] = len(verified)
    counts["warehouse_matched_rows"] = None
    counts["exported_picks"] = None
    counts["exported_selected_quotes"] = None
    collection["quote_diagnostics"] = counts
    print("Guarded rebuild quote stages:", json.dumps(counts, sort_keys=True))
    if comparisons and not verified:
        raise RuntimeError("collection has no market-identified fresh quotes")
    baseline = LOCALDATA / "warehouse.csv.gz"
    if not baseline.exists():
        raise RuntimeError("historical warehouse baseline unavailable")
    history = pd.read_csv(baseline, low_memory=False)
    if "_is_live" in history.columns:
        historical = ~history["_is_live"].astype(str).str.lower().isin({"1", "true", "yes"})
        history = history[historical].copy()
    # No old same-day fixture or archived price may be relabelled as fresh,
    # even if it already has a result. This only changes the staged snapshot.
    if "match_date" in history.columns:
        history = history[history["match_date"].astype(str).str[:10] != target_date].copy()
    live = build_live_rows(include_tomorrow=False, supplied_predictions=target_predictions,
                           target_date=target_date, supplied_odds=[], supplied_comparisons=verified)
    counts["warehouse_matched_rows"] = int(live["_odds_source"].fillna("").astype(str).ne("").sum()) if "_odds_source" in live else 0
    if not live.empty:
        live["predicted_winner_live"] = live["live_predicted_winner"]
        live["prediction_prob_live"] = live["live_prediction_prob"]
        history = pd.concat([history, live], ignore_index=True, sort=False)
    workdir.mkdir(parents=True, exist_ok=True)
    warehouse_path = workdir / "warehouse.csv.gz"
    history.to_csv(warehouse_path, index=False, compression="gzip")
    child_env = os.environ.copy()
    for key in ("BZZOIRO_TOKEN", "THE_ODDS_API_KEY", "THE_ODDS_API_KEYS", "JINA_API_KEY", "JINA_READER_API_KEY",
                "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "CALLMEBOT_APIKEY", "CALLMEBOT_PHONE"):
        child_env[key] = ""
    child_env["RACKET_FACTORY_DISABLE_FOREBET"] = "1"
    child_env["RACKET_FACTORY_FETCH_CACHE_TTL_MINUTES"] = "0"
    child_env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "mine_edges.py"), "--warehouse", str(warehouse_path),
                           "--output-dir", str(workdir), "--fresh-only", "--bet-side", "prediction", "--date", target_date],
                          cwd=ROOT, env=child_env, check=False)
    if proc.returncode:
        raise RuntimeError(f"staged miner failed with exit {proc.returncode}")
    path = workdir / f"picks_{target_date}.json"
    try:
        picks = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("staged miner did not produce valid picks") from exc
    if not isinstance(picks, list):
        raise RuntimeError("staged picks are not a list")
    counts["exported_picks"] = len(picks)
    counts["exported_selected_quotes"] = sum(isinstance(p, dict) and isinstance(p.get("selected_quote"), dict) for p in picks)
    from scripts.auto_tickets import selected_quote_problem
    export_rejections = Counter()
    for pick in picks:
        if not isinstance(pick, dict):
            export_rejections["malformed pick"] += 1
            continue
        try:
            price = float(pick.get("odds"))
            priced = isfinite(price) and price > 1
        except (TypeError, ValueError, OverflowError):
            priced = False
        if priced:
            problem = selected_quote_problem(pick, require_provenance=True)
            if problem:
                export_rejections[problem] += 1
    counts["export_rejections"] = dict(export_rejections)
    print("Guarded rebuild quote stages:", json.dumps(counts, sort_keys=True))
    if export_rejections:
        raise RuntimeError("staged priced picks lost or invalidated quote provenance")
    return picks


def run_guarded_empty_rebuild(target_date: str | None = None) -> None:
    """Track this attempt separately from historical files and financial state."""
    from racketfactory.rebuild_diagnostics import directory, write_status
    diagnostic_dir = directory() or LOCALDATA / ".rebuild_diagnostics"
    status = {"version": 1, "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
              "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", "1"),
              "target_date": target_date or _now_local().date().isoformat(),
              "started_at": _now_local().isoformat(), "stage": "starting", "status": "running",
              "ticket_generation": "not_completed", "authoritative_publication": "not_started"}
    def update(**fields):
        status.update(fields, updated_at=_now_local().isoformat())
        write_status(status, diagnostic_dir)
    update()
    try:
        _run_guarded_empty_rebuild(target_date, update)
    except BaseException as exc:
        update(status="failed", error_type=type(exc).__name__)
        raise


def _run_guarded_empty_rebuild(target_date: str | None, update) -> None:
    """Collect and stage without settlement, then update the isolated checkout.

    External publication must be a separate atomic Git ref update under the
    workflow's concurrency group; this function never pushes or notifies.
    """
    import tempfile
    from scripts import auto_tickets
    now = _now_local()
    target = target_date or now.date().isoformat()
    if target != now.date().isoformat():
        raise RuntimeError("rebuild only supports the configured current betting day")
    if os.getenv("RACKET_FACTORY_REBUILD_SERIALIZED") != "1":
        raise RuntimeError("guarded rebuild requires serialized workflow publication")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    if not run_id.isdigit() or not attempt.isdigit():
        raise RuntimeError("guarded rebuild requires a workflow run identity")
    # A Git-committed archive is a durable transaction record across runners.
    # The run ID is stable across Actions retries; an attempt must not rebook.
    archives = sorted((LOCALDATA / ".auto_tickets_rebuild").glob(f"archive_*_{run_id}.json"))
    if archives:
        if len(archives) != 1:
            raise RuntimeError("ambiguous rebuild transaction archive")
        import subprocess
        import base64
        archive = archives[0]
        relative = f"localdata/.auto_tickets_rebuild/{archive.name}"
        committed = subprocess.run(["git", "show", f"HEAD:{relative}"], cwd=ROOT, capture_output=True)
        if committed.returncode != 0 or committed.stdout != archive.read_bytes():
            raise RuntimeError("uncommitted rebuild archive exists; refusing retry")
        record = json.loads(committed.stdout)
        original = base64.b64decode(record.get("original_card_base64", ""), validate=True)
        if (record.get("transaction_id") != run_id or record.get("actionable") is not False
                or auto_tickets._rebuild_digest(original) != record.get("original_card_sha256")):
            raise RuntimeError("committed rebuild archive is invalid")
        if archive.name != f"archive_{target}_{run_id}.json":
            raise RuntimeError("this workflow run already published on a different betting day")
        print(f"Guarded rebuild {run_id} was already published; no changes")
        update(stage="already_published", status="already_published", ticket_generation="previously_completed", authoritative_publication="previously_published")
        return
    card_path = LOCALDATA / f"auto_tickets_{target}.json"
    state_path = LOCALDATA / "auto_tickets_state.json"
    try:
        card_raw, state_raw = card_path.read_bytes(), state_path.read_bytes()
        card, state = json.loads(card_raw), json.loads(state_raw)
    except (OSError, ValueError) as exc:
        raise RuntimeError("authoritative card or state is unavailable") from exc
    problem = auto_tickets.rebuild_replacement_problem(card, state, target)
    if problem:
        raise RuntimeError(problem)
    card_digest, state_digest = auto_tickets._rebuild_digest(card_raw), auto_tickets._rebuild_digest(state_raw)
    update(stage="collecting")
    collection = collect_rebuild_sources(target, progress=lambda provider, phase, outcomes:
        update(stage="collecting_" + provider, provider_phase=phase, source_outcomes=outcomes))
    update(stage="collection_complete", source_outcomes=collection["outcomes"], collection_status=collection["status"])
    print("Guarded rebuild source outcomes:", json.dumps(collection["outcomes"], sort_keys=True))
    if collection["status"] != "complete":
        raise RuntimeError("required source collection incomplete; old card remains unchanged")
    with tempfile.TemporaryDirectory(prefix="racket-rebuild-") as stage:
        update(stage="validating_and_staging_quotes")
        try:
            picks = stage_fresh_rebuild_picks(target, collection, Path(stage))
        finally:
            if collection.get("quote_diagnostics") is not None:
                update(quote_stages=collection["quote_diagnostics"])
        update(stage="ticket_generation")
        # The run's real start time is retained. Old cached quotes are never
        # stamped with that time; each candidate carries its own observation.
        proposal = auto_tickets.stage_rebuild_evaluation(picks, state, target, _now_local(), collection)
        proposal["collection_outcomes"] = collection["outcomes"]
        print("Guarded rebuild funnel:", json.dumps(proposal["diagnostics"], sort_keys=True))
        update(stage="local_publication", ticket_generation="completed", ticket_funnel=proposal["diagnostics"])
    result = auto_tickets.publish_staged_rebuild_local(LOCALDATA, target, proposal,
        expected_card_digest=card_digest, expected_state_digest=state_digest, run_id=run_id)
    update(stage="awaiting_git_publication", status="staged", ticket_generation="completed")
    print(f"Guarded rebuild staged locally: {result}; tickets={len(proposal['accas'])}. Awaiting atomic Git publication.")


def run_smart_auto(args: argparse.Namespace) -> None:
    """One autonomous iteration of the accumulating ledger (Edge-Factory parity).

    Decides the operational regime for *today* and dispatches a single pipeline
    run with the right flags, then exits.  Designed to be invoked once every few
    hours by CI (GitHub Actions) or a cron/`--auto-run` loop:

      * Case 1 — no official archive yet for today (typically the first wake-up
        of the day): run the FULL heavy pipeline (OddsPortal + prediction captures + Daily).

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
    target = args.date or datetime.now(local_tz()).date().isoformat()
    year = target[:4]

    run_as_of = make_run_as_of()
    load_daily_env()
    child_env = os.environ.copy()
    # Stop trying Forebet access workarounds; retained history is unchanged.
    child_env["RACKET_FACTORY_DISABLE_FOREBET"] = "1"
    child_env["RACKET_FACTORY_RUN_AS_OF"] = run_as_of
    child_env.setdefault("RACKET_FACTORY_TZ", DEFAULT_LOCAL_TZ)
    env_prefix = f"RACKET_FACTORY_RUN_AS_OF={shlex.quote(run_as_of)}"
    oddsportal_delay = float(os.getenv("RACKET_FACTORY_ODDSPORTAL_DELAY", "5"))
    # FIX: Enable OddsPortal by default — recent months need OddsPortal for current odds (no odds-carrying history source remains)
    # Only disable if explicitly set RACKET_FACTORY_DISABLE_ODDSPORTAL=1
    disable_oddsportal = os.getenv("RACKET_FACTORY_DISABLE_ODDSPORTAL", "").strip().lower() in {"1", "true", "yes", "on"}
    refresh_oddsportal_env = os.getenv("RACKET_FACTORY_REFRESH_ODDSPORTAL", "").strip().lower()
    # Default enabled unless disabled, or if REFRESH explicitly set, respect it
    if refresh_oddsportal_env:
        refresh_oddsportal = refresh_oddsportal_env in {"1", "true", "yes", "on"}
    else:
        refresh_oddsportal = not disable_oddsportal

    print("=== Racket Factory Daily Pipeline (Tennis) ===")
    print(f"    target date : {target}")
    print(f"    mode        : {'auto-run' if args.auto_run else ('intraday-only' if args.intraday_only else 'full')}")
    print(f"    future_days : {args.future_days}")
    print(f"    run as-of   : {run_as_of} (tz={DEFAULT_LOCAL_TZ})")

    if not args.intraday_only:
        # 1. Official Source Captures (Heavy History)
        # FIX: Bulk historical OddsPortal --all for current year 2026 tries 36 tournaments with 5 pages each,
        # each failing after 120s Playwright wait (no PageTournament marker, 503) -> 45m+ run with 0 rows.
        # For current regime, we don't need 2026 bulk historical — we need recent results and live odds via TheOddsAPI/Bzzoiro.
        # So skip bulk --all for current year, only run if explicitly forced via REFRESH_ODDSPORTAL=1 and not current year,
        # or via manual mode.
        current_year = 2026
        if refresh_oddsportal and year != current_year:
            run_soft(
                f"{env_prefix} PYTHONPATH=src python3 scripts/capture_"
                f"oddsportal.py --all --years {year} --no-checkpoint --delay {oddsportal_delay:g}",
                f"capture_oddsportal {year}",
                env=child_env,
            )
        else:
            if year == current_year:
                print(f"\n>>> capture_oddsportal bulk {year} skipped — current year has no historical bulk, use live API/Bzzoiro")
            else:
                print("\n>>> capture_oddsportal skipped (disabled via RACKET_FACTORY_DISABLE_ODDSPORTAL)")
    else:
        # REDTEAM Finding #6: in intraday mode we still need yesterday's
        # results to flow into the warehouse so the audit can measure
        # settled picks. However heavy OddsPortal bulk capture (--all) can timeout CI (30 min).
        # FIX: intraday skips heavy bulk, only does lightweight live odds below. Full bulk only in full morning run.
        print("\n>>> intraday mode: skipping heavy OddsPortal bulk capture (lightweight live odds below)")

    # 2. Targeted live odds capture for known API coverage gaps + always-on current odds.
    # FIX: Recent months have NaN odds in history feeds; OddsPortal live capture fills current gaps.
    # Now we always try to fetch current odds for ATP/WTA/Challenger via OddsPortal (soft fail, uses curl_cffi to bypass CF)
    # The Odds API only covers Slams/1000/500, not Challenger/ITF, so OddsPortal is critical.
    refresh_live_doubles_odds = os.getenv("RACKET_FACTORY_REFRESH_LIVE_DOUBLES_ODDS", "").strip().lower() in {"1", "true", "yes", "on"}
    if refresh_live_doubles_odds:
        run_soft(
            f"{env_prefix} PYTHONPATH=src python3 scripts/capture_oddsportal.py "
            f"--url https://www.oddsportal.com/tennis/united-kingdom/atp-wimbledon-doubles/ "
            f"--tour ATP --tournament 'Wimbledon Doubles' --date {target} --pages 1",
            "capture_oddsportal live ATP Wimbledon doubles",
            env=child_env,
        )
    else:
        print("\n>>> capture_oddsportal live doubles skipped (optional)")

    # Always-on: try to fetch current ATP/WTA/Challenger odds for target date via OddsPortal live pages (soft fail)
    # FIX: Broad category pages /tennis/atp/, /tennis/wta/, /tennis/challenger/ have no PageTournament marker
    # and return 'no PageTournament marker' / 'no page id'. They are category pages, not tournament pages.
    # The collector expects tournament-page metadata. Installing Chromium fixes crash but not parser compatibility.
    # For now, skip broad category live capture and rely on TheOddsAPI (Slams/1000/500) + Bzzoiro fallback.
    # Future: validate tournament URLs or add category-page discovery step.
    if not disable_oddsportal:
        print("\n>>> capture_oddsportal live broad category capture disabled — category pages lack PageTournament marker")
        print("    Relying on TheOddsAPI + Bzzoiro for live odds; OddsPortal bulk historical still runs if REFRESH enabled")
        # Optionally try specific validated tournament URLs if needed
        # Example: US Open, French Open etc have known tournament pages that work with parser
    else:
        print("\n>>> capture_oddsportal live current odds skipped (disabled)")

    # 3. Daily Prediction Sources — DEEP SEARCH FIX: warehouse only 20% pred coverage, Both 0.2%
    # Daily quick: yesterday/today/tomorrow + last 7 days to deepen coverage 20%->40%
    print("\n>>> Forebet live capture skipped; retained archives remain available")
    # Extra deep: last 7 days rolling (fixes missing June-August if files deleted)
    try:
        from datetime import timedelta
        today = datetime.strptime(target, "%Y-%m-%d").date()
        last7 = [(today - timedelta(days=i)).isoformat() for i in range(2,8)]  # 2-7 days ago already have yesterday, so 2-7
        # Use daily mode with explicit dates via direct script call if supported, else skip
        # For now, run tournament mode limited to recent tournaments if deep needed
    except Exception:
        pass
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/backfill_foretennis.py --warehouse localdata/warehouse.csv.gz --output-dir localdata", "backfill_foretennis", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_predixsport.py --output-dir localdata", "capture_predixsport", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_betclan.py --output-dir localdata", "capture_betclan", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_bzzoiro.py --output-dir localdata", "capture_bzzoiro", env=child_env)

    # Historical repair is evaluated after grading, outside the candidate path.
    if os.getenv("RACKET_FACTORY_DISABLE_THEODDSAPI_SCORES", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("\n>>> capture_theoddsapi_scores skipped")
        print("RACKET_FACTORY_DISABLE_THEODDSAPI_SCORES is set; avoiding The Odds API score quota burn.")
    else:
        run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_theoddsapi_scores.py --days-from 3 --output-dir localdata", "capture_theoddsapi_scores", env=child_env)

    # 3b. Baseline snapshot — preserve the committed warehouse (previous
    # run's mined snapshot) before the rebuild overwrites it, so the
    # post-build merge can pin historical rows to it (deterministic slice
    # mining; see scripts/merge_warehouse_baseline.py).
    _wh_path = LOCALDATA / "warehouse.csv.gz"
    _baseline_path = LOCALDATA / "warehouse_baseline.csv.gz"
    try:
        _baseline_path.unlink(missing_ok=True)
        if _wh_path.exists():
            _wh_path.replace(_baseline_path)
    except OSError as e:
        print(f"warehouse baseline save failed (continuing without baseline): {e}")

    # 4. Warehouse Resolution & Assembly (initial — for predictions)
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py --data-dir localdata --output warehouse.csv.gz", "build_warehouse_initial", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/resolve_pending.py --warehouse localdata/warehouse.csv.gz --data-dir localdata", "resolve_pending", env=child_env)
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py --data-dir localdata --output warehouse.csv.gz", "build_warehouse_final", env=child_env)

    # 4b. Second pass of result backfills AFTER warehouse exists — ensures foretennis/forebet results
    # are generated from yesterday's actual_result even if initial warehouse was stale.
    # This is critical for settlement: 3 settled of 45 was because foretennis_results not generated.
    print("\n>>> Second pass: backfill result sources for settlement (yesterday)")
    print("Forebet live result backfill skipped; retained results remain available")
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/backfill_foretennis.py --warehouse localdata/warehouse.csv.gz --output-dir localdata", "backfill_foretennis second pass (results)", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/backfill_challenger_results.py --days 3 --output-dir localdata", "backfill_challenger_results (settlement)", env=child_env)
    if os.getenv("RACKET_FACTORY_DISABLE_THEODDSAPI_SCORES", "").strip().lower() not in {"1", "true", "yes", "on"}:
        run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/capture_theoddsapi_scores.py --days-from 3 --output-dir localdata", "capture_theoddsapi_scores second pass (results)", env=child_env)
    # Rebuild warehouse with new result rows so audit can settle
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/build_warehouse.py --data-dir localdata --output warehouse.csv.gz", "build_warehouse_with_results", env=child_env)

    # 4c. Re-anchor the fresh warehouse onto the committed baseline: pin
    # historical dimension columns, let settlements/new matches/live rows
    # flow in. Soft — the fresh build stands alone if the merge fails.
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/merge_warehouse_baseline.py --baseline localdata/warehouse_baseline.csv.gz --fresh localdata/warehouse.csv.gz --output localdata/warehouse.csv.gz --report localdata/warehouse_merge_report.json", "merge_warehouse_baseline", env=child_env)

    # 5. Mine Edges
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/mine_edges.py --warehouse localdata/warehouse.csv.gz --bet-side prediction --date {target}", "mine_edges", env=child_env)

    # 6. Archive by Kickoff (morning baseline disabled per user request)
    # Previously locked morning baseline, now skipped — focus on autobets only
    pass

    # 7. Generate human friendly TXT report + inline next-day planner (Edge-Factory parity)
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

    # 8. Run Audit + CLV/Calibration (feeds ML feedback loop for next run)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/audit_recent_picks.py --end {target} --days 0 --warehouse localdata/warehouse.csv.gz --include-same-day", "audit_recent_picks (full history, include same-day)", env=child_env)
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/audit_clv.py --days 30", "audit_clv", env=child_env)

    # 8b. Auto Tickets (Edge-Factory parity) — tennis accas from playable picks
    # ML self-monitor: BetExplorer consensus is REAL price (fixed bad=10), ML chooses winners
    run_soft(f"{env_prefix} PYTHONPATH=src python3 -c \"from racketfactory.ml import monitor_performance; import json; print(json.dumps(monitor_performance(), indent=2))\"", "ml_monitor (self-check)", env=child_env)
    # Kickoff timezone drift check BEFORE tickets are built: the BetClan
    # UTC+1 reading is an assumption with a known expiry (2026-10-25, when
    # Britain leaves BST). If it has moved, every kickoff is read an hour
    # early and kickoff_guard would stake matches already in progress.
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/kickoff_tz_audit.py --date {target} --days 7", "kickoff_tz_audit (BetClan offset)", env=child_env)
    run(f"{env_prefix} PYTHONPATH=src python3 scripts/auto_tickets.py --date {target}", "auto_tickets (generate/freeze)", env=child_env)
    run_critical_grade(f"{env_prefix} PYTHONPATH=src python3 scripts/auto_tickets_grade.py", env=child_env, run_as_of=run_as_of)
    # Read-only forensics over the settled ledger: pool/ranking/calibration/
    # staking plus the search-winner null test. Reports, never tunes.
    run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/autobets_forensics.py --json localdata/autobets_forensics.json", "autobets_forensics (ledger audit)", env=child_env)

    # Historical backfill is deliberately deferred from the daily ticket path.
    # Its source-specific scheduling needs separate evidence and review.

    # 9. Supabase Live Dashboard Sync (Optional)
    sync_script = ROOT / "scripts" / "sync_supabase.py"
    if sync_script.exists() and (os.getenv("SUPABASE_URL") or args.force_sync):
        run_soft(f"{env_prefix} PYTHONPATH=src python3 scripts/sync_supabase.py --picks {archived_picks_file(target)} --target-date {target} --replace-date", "sync_supabase", env=child_env)

    print(f"\n=== Pipeline Complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # 10. Optional WhatsApp heads-up
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
    ap.add_argument("--guarded-rebuild", action="store_true", help="Stage a serialized same-day empty-card rebuild")
    ap.add_argument("--intraday-only", action="store_true", help="Skip heavy captures; run live prediction fetch only")
    ap.add_argument("--skip-notify", action="store_true", help="Do not send the WhatsApp heads-up")
    ap.add_argument("--force-repick", action="store_true",
                    help="Force a full auto-schedule run; does not override frozen tickets")
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
    if args.guarded_rebuild:
        if args.intraday_only:
            raise SystemExit("guarded rebuild conflicts with intraday-only")
        try:
            run_guarded_empty_rebuild(args.date)
        except RuntimeError as exc:
            raise SystemExit(f"Guarded rebuild failed: {exc}") from exc
        return

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
