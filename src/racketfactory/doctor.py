"""
Racket Factory — Health & Sanity Checks.
Single source of truth for repo/pipeline health.

Run with: python3 -m racketfactory.doctor [--expect-warehouse] [--as-of YYYY-MM-DD]
Writes localdata/pipeline_health.json and exits 1 on any CRITICAL check.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Allow running without PYTHONPATH=src
ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

LOCALDATA = ROOT / "localdata"

RESULT_GROUPS = {
    "foretennis_results": ("foretennis_results_*.csv.gz", 3),
    "forebet_results": ("forebet_results_*.csv.gz", 3),
    "challenger_results": ("challenger_results_*.csv.gz", 3),
    "theoddsapi_scores": ("theoddsapi_scores_*.csv.gz", 3),
    "tennisdata": ("tennisdata_tennis_*.csv.gz", 45),
}
PREDICTION_PATTERNS = (
    "predictions_*.csv.gz",
    "predictions_*.csv",
    "archive_bzzoiro.csv",
)


def _today(as_of: str | None) -> date:
    raw = as_of or os.getenv("RACKET_FACTORY_RUN_AS_OF") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date() if raw else date.today()
    except ValueError:
        return date.today()


def _read_match_dates(path: Path) -> tuple[int, str]:
    """(row_count, max match_date) reading only the date column when possible."""
    rows = 0
    latest = ""
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return 0, ""
            date_col = next((c for c in ("match_date", "date", "Date")
                             if c in reader.fieldnames), None)
            for row in reader:
                rows += 1
                if date_col:
                    val = (row.get(date_col) or "")[:10]
                    if val > latest:
                        latest = val
    except Exception:
        return rows, latest
    return rows, latest


def check_localdata_exists() -> bool:
    """Ensure localdata directory exists."""
    return LOCALDATA.exists()


def check_localdata_writable() -> bool:
    """Check that we can write to localdata."""
    if not LOCALDATA.exists():
        return False
    try:
        test_file = LOCALDATA / ".write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except Exception:
        return False


def group_stats(pattern: str) -> dict:
    files = sorted(LOCALDATA.glob(pattern))
    rows = 0
    latest = ""
    newest_mtime: float | None = None
    for f in files:
        n, mx = _read_match_dates(f)
        rows += n
        latest = max(latest, mx)
        try:
            mt = f.stat().st_mtime
            newest_mtime = mt if newest_mtime is None else max(newest_mtime, mt)
        except OSError:
            pass
    return {"files": len(files), "rows": rows, "max_date": latest,
            "newest_mtime": newest_mtime}


def _age_days(mtime: float | None, today: date) -> float | None:
    if mtime is None:
        return None
    return (datetime.now().timestamp() - mtime) / 86400.0


def run_health_checks(*, expect_warehouse: bool = False,
                      as_of: str | None = None) -> dict:
    """Run all health checks. Returns {name: {status, detail}}.

    Status is OK / WARN / CRITICAL.
    """
    today = _today(as_of)
    today_s = today.isoformat()
    checks: dict[str, dict] = {}

    def add(name: str, status: str, detail: str = "") -> None:
        checks[name] = {"status": status, "detail": detail}

    add("localdata_exists", "OK" if check_localdata_exists() else "CRITICAL")
    add("localdata_writable", "OK" if check_localdata_writable() else "CRITICAL")
    if not check_localdata_exists():
        return checks

    total_result_rows = 0
    for name, (pattern, max_lag_days) in RESULT_GROUPS.items():
        st = group_stats(pattern)
        total_result_rows += st["rows"]
        if st["files"] == 0:
            add(f"results:{name}", "WARN", "no files match " + pattern)
        elif not st["max_date"]:
            add(f"results:{name}", "WARN",
                f"{st['files']} files, {st['rows']} rows, no date column")
        else:
            try:
                lag = (today - date.fromisoformat(st["max_date"])).days
            except ValueError:
                lag = 999
            status = "OK" if lag <= max_lag_days else "WARN"
            add(f"results:{name}", status,
                f"{st['files']} files, {st['rows']} rows, max_date {st['max_date']} "
                f"(lag {lag}d, budget {max_lag_days}d)")
    add("results:any_rows",
        "OK" if total_result_rows > 0 else "CRITICAL",
        f"{total_result_rows} result rows across all sources")

    pred_rows = pred_files = 0
    pred_latest = ""
    for pattern in PREDICTION_PATTERNS:
        st = group_stats(pattern)
        pred_files += st["files"]
        pred_rows += st["rows"]
        pred_latest = max(pred_latest, st["max_date"])
    add("predictions:coverage",
        "OK" if pred_rows > 0 else "WARN",
        f"{pred_files} files, {pred_rows} rows, max_date {pred_latest or '?'}")

    wh = LOCALDATA / "warehouse.csv.gz"
    if wh.exists():
        n, mx = _read_match_dates(wh)
        add("warehouse", "OK" if n > 0 else "WARN",
            f"{n} rows, max_date {mx or '?'}")
    else:
        add("warehouse", "CRITICAL" if expect_warehouse else "WARN",
            "warehouse.csv.gz missing")

    # Picks pricing: 100% unpriced means the odds pipeline is down.
    picks = None
    for cand in (LOCALDATA / f"picks_{today_s}.json", LOCALDATA / "picks_today.json"):
        if cand.exists():
            try:
                data = json.loads(cand.read_text())
                if isinstance(data, list) and data:
                    picks = (cand.name, data)
                    break
            except Exception:
                pass
    if picks is None:
        add("picks:priced_share", "WARN", "no picks file for " + today_s)
    else:
        name, data = picks
        rows = [r for r in data if isinstance(r, dict)
                and not str(r.get("bucket", "")).startswith("SKIPPED")]

        def _priced(r: dict) -> bool:
            if r.get("odds_reject_reason"):
                return False
            if str(r.get("odds_source") or "").strip().lower() in (
                    "", "nan", "none", "ml_estimated", "<na>"):
                return False
            try:
                return float(r.get("odds")) > 1.0
            except (TypeError, ValueError):
                return False

        priced = sum(1 for r in rows if _priced(r))
        status = "OK" if priced > 0 else "WARN"
        add("picks:priced_share", status,
            f"{name}: {priced}/{len(rows)} priced")

    audit_file = LOCALDATA / "picks_audit_rolling.json"
    if audit_file.exists():
        try:
            overall = json.loads(audit_file.read_text()).get("overall", {})
            add("audit:rolling",
                "WARN" if overall.get("conflict_picks") else "OK",
                f"settled {overall.get('settled_picks')} "
                f"conflicts {overall.get('conflict_picks')} "
                f"pending {overall.get('pending_picks')}")
        except Exception as exc:
            add("audit:rolling", "WARN", f"unreadable: {exc}")
    else:
        add("audit:rolling", "WARN", "picks_audit_rolling.json missing")

    state_file = LOCALDATA / "auto_tickets_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            add("tickets:bank", "OK",
                f"bank {state.get('bank')}% paper {state.get('paper_bank')}% "
                f"open {len(state.get('open_slips', []))} "
                f"history {len(state.get('history', []))}")
        except Exception as exc:
            add("tickets:bank", "WARN", f"unreadable: {exc}")
    else:
        add("tickets:bank", "WARN", "auto_tickets_state.json missing")

    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Racket Factory health checks")
    ap.add_argument("--expect-warehouse", action="store_true",
                    help="missing warehouse.csv.gz is CRITICAL (post-pipeline)")
    ap.add_argument("--as-of", default=None,
                    help="reference date YYYY-MM-DD (default: today / RUN_AS_OF)")
    ap.add_argument("--write-report", action="store_true",
                    help="write localdata/pipeline_health.json")
    args = ap.parse_args(argv)

    print("Racket Factory Doctor")
    print("=" * 70)
    results = run_health_checks(expect_warehouse=args.expect_warehouse,
                                as_of=args.as_of)
    worst_rank = {"OK": 0, "WARN": 1, "CRITICAL": 2}
    worst = 0
    for name, res in results.items():
        mark = {"OK": "PASS", "WARN": "WARN", "CRITICAL": "FAIL"}[res["status"]]
        print(f"{name:28} {mark:4}  {res.get('detail', '')}")
        worst = max(worst, worst_rank[res["status"]])
    print("=" * 70)
    if worst >= 2:
        print("CRITICAL checks failed. See above.")
    elif worst == 1:
        print("Healthy with warnings.")
    else:
        print("All checks passed. Repo is healthy.")

    if args.write_report:
        try:
            LOCALDATA.mkdir(parents=True, exist_ok=True)
            (LOCALDATA / "pipeline_health.json").write_text(json.dumps(
                {"as_of": (_today(args.as_of)).isoformat(), "checks": results},
                indent=2))
            print("wrote localdata/pipeline_health.json")
        except Exception as exc:
            print(f"report write failed: {exc}")
    return 1 if worst >= 2 else 0


if __name__ == "__main__":
    sys.exit(main())
