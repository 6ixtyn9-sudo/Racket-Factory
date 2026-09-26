#!/usr/bin/env python3
"""Cross-source kickoff audit — is the BetClan timezone assumption still true?

WHY
---
Every staked leg's kickoff comes from BetClan, and BetClan publishes naive
timestamps. The pipeline reads them as a fixed UTC+1 (``Etc/GMT-1``) because
that is what they measured on 2026-09-24/25. If BetClan is actually running
Europe/London, Britain leaving BST on **2026-10-25** widens the gap to two
hours and every kickoff is read an hour early — which means ``kickoff_guard``
would wave through matches that had already started, and the engine would
stake settled results at pre-match prices.

That risk previously lived in a docstring. This script turns it into a
measurement plus an alarm file.

HOW
---
The Odds API's ``commence_time`` is unambiguous ISO UTC and is normalised to
SAST at ingestion, so it is an independent reference for the same fixture.
For every fixture that appears in both feeds on the same day, compare the two
SAST wall times. They should agree. The median disagreement is the measured
offset; anything at or past ``BETCLAN_DRIFT_ALARM_MINUTES`` means the
assumption is wrong and ``RACKET_FACTORY_BETCLAN_TZ`` should be set.

Fail-safe: with no overlap, no cache, or no picks, the verdict is UNKNOWN
(and REVIEW_DUE once the review date has passed) — never a green tick. Exit
code is 0 unless ``--strict`` is passed, because a monitoring step must not
take the daily pipeline down.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"

from racketfactory.kickoff import (  # noqa: E402
    BETCLAN_TZ_REVIEW_DATE,
    STATUS_DRIFT,
    STATUS_REVIEW_DUE,
    betclan_tz_status,
    minutes_between_wall_times,
)

OUT_FILE = LOCALDATA / "kickoff_tz_audit.json"

# Durable evidence ledger.
#
# THE PROBLEM THIS SOLVES: the only independent UTC reference is The Odds API
# `commence_time`, which lives in localdata/theoddsapi_odds_cache_{date}.json
# — a RUNTIME cache that is not committed and does not exist on a fresh
# checkout or a fresh CI runner. So the audit could only ever see the day it
# ran, usually saw nothing, and correctly-but-uselessly reported UNKNOWN
# forever. An alarm that can never leave UNKNOWN is not an alarm.
#
# Fix, borrowed from Edge's shadow-logging pattern: every time a run DOES see
# the cache, the matched (betclan, reference) pairs are appended here. The
# file is tiny, append-only, deduplicated, and committed, so the evidence
# accrues across runs from day one and the verdict can actually resolve.
EVIDENCE_NAME = "kickoff_tz_samples.jsonl"
EVIDENCE_MAX_AGE_DAYS = 120   # a stale offset is not evidence about today
EVIDENCE_MAX_ROWS = 5000      # hard bound: this file must never grow unbounded


def evidence_file() -> Path:
    """Resolved at CALL time from LOCALDATA, never frozen at import.

    A module-level ``LOCALDATA / name`` captures the real repo path the moment
    the module is imported, so a test that redirects ``LOCALDATA`` to a tmp
    dir still writes to the live localdata. That is the same defect the grader
    had, and it leaked straight past the first run of the new tests.
    """
    return LOCALDATA / EVIDENCE_NAME


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(sorted(part for part in text.split() if len(part) > 1))


def _fixture_key(a: str, b: str) -> str:
    return "|".join(sorted([_norm(a), _norm(b)]))


def _clean_time(value) -> str:
    text = str(value or "").strip()
    return text if re.match(r"^\d{1,2}:\d{2}", text) else ""


def betclan_kickoffs(target_date: str) -> dict[str, str]:
    """{fixture_key: HH:MM} for BetClan-sourced picks on ``target_date``."""
    out: dict[str, str] = {}
    path = LOCALDATA / f"picks_{target_date}.json"
    if not path.exists():
        return out
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return out
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if "betclan" not in str(row.get("source") or "").lower():
            continue
        when = _clean_time(row.get("kickoff") or row.get("match_time"))
        if not when:
            continue
        home = row.get("player_home") or row.get("player_a")
        away = row.get("player_away") or row.get("player_b")
        if not home or not away:
            match = str(row.get("match") or "")
            if " vs " not in match:
                continue
            home, away = match.split(" vs ", 1)
        out[_fixture_key(home, away)] = when
    return out


def theoddsapi_kickoffs(target_date: str) -> dict[str, str]:
    """{fixture_key: HH:MM SAST} from the cached The Odds API rows."""
    out: dict[str, str] = {}
    path = LOCALDATA / f"theoddsapi_odds_cache_{target_date}.json"
    if not path.exists():
        return out
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return out
    for row in (payload.get("rows") or []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        if str(row.get("match_date") or "")[:10] not in ("", target_date):
            continue
        when = _clean_time(row.get("match_time"))
        if not when:
            continue
        out[_fixture_key(row.get("player_home"), row.get("player_away"))] = when
    return out


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise — never raises.

    relative_to() throws when the path is outside ROOT, which happens the
    moment a test points the evidence file at a tmp dir. A monitoring script must
    not be able to die formatting its own log line.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_evidence(max_age_days: int = EVIDENCE_MAX_AGE_DAYS,
                  today: date | None = None) -> list[dict]:
    """Previously observed samples, still fresh enough to mean something."""
    path = evidence_file()
    if not path.exists():
        return []
    cutoff = (today or date.today()) - timedelta(days=max_age_days)
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue          # a corrupt line must never take the audit down
        if not isinstance(row, dict) or "delta_minutes" not in row:
            continue
        try:
            when = datetime.strptime(str(row.get("date")), "%Y-%m-%d").date()
        except Exception:
            continue
        if when >= cutoff:
            rows.append(row)
    return rows


def record_evidence(samples: list[dict], existing: list[dict] | None = None) -> int:
    """Append genuinely new samples. Returns how many were added.

    Never raises: this is monitoring, and monitoring must not be able to
    break the pipeline that feeds it.
    """
    if not samples:
        return 0
    try:
        seen = {(row.get("date"), row.get("fixture"))
                for row in (existing if existing is not None
                            else load_evidence(max_age_days=10**6))}
        fresh = [row for row in samples
                 if (row.get("date"), row.get("fixture")) not in seen]
        if not fresh:
            return 0
        path = evidence_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            for row in fresh:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        _trim_evidence()
        return len(fresh)
    except Exception:
        return 0


def _trim_evidence() -> None:
    """Keep the newest EVIDENCE_MAX_ROWS lines; the file is append-only."""
    try:
        path = evidence_file()
        lines = path.read_text().splitlines()
        if len(lines) > EVIDENCE_MAX_ROWS:
            path.write_text("\n".join(lines[-EVIDENCE_MAX_ROWS:]) + "\n")
    except Exception:
        pass


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def audit(dates: list[str], *, persist: bool = True,
          today: date | None = None) -> dict:
    """Measure the BetClan-vs-reference kickoff offset over ``dates``.

    Verdict is formed on live samples PLUS the durable evidence ledger, so a
    run that happens to see no cache today still benefits from every match
    any previous run observed.
    """
    fresh: list[dict] = []
    for target_date in dates:
        betclan = betclan_kickoffs(target_date)
        reference = theoddsapi_kickoffs(target_date)
        for key, when in betclan.items():
            if key not in reference:
                continue
            delta = minutes_between_wall_times(when, reference[key])
            if delta is None:
                continue
            fresh.append({"date": target_date, "fixture": key,
                          "betclan": when, "reference": reference[key],
                          "delta_minutes": delta})

    remembered = load_evidence(today=today)
    added = record_evidence(fresh, existing=remembered) if persist else 0

    live_keys = {(row["date"], row["fixture"]) for row in fresh}
    samples = fresh + [row for row in remembered
                       if (row.get("date"), row.get("fixture")) not in live_keys]

    offset = _median([s["delta_minutes"] for s in samples]) if samples else None
    status = betclan_tz_status(measured_offset_minutes=offset,
                               sample_size=len(samples))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dates_checked": dates,
        "matched_fixtures": len(samples),
        "matched_this_run": len(fresh),
        "remembered_samples": len(samples) - len(fresh),
        "new_samples_recorded": added,
        "evidence_file": _display_path(evidence_file()),
        "median_offset_minutes": offset,
        "samples": samples[:50],
        **status,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7,
                    help="how many days back to look for overlapping fixtures")
    ap.add_argument("--date", default=None, help="end date (default: today)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on DRIFT/REVIEW_DUE (default: report only)")
    args = ap.parse_args(argv)

    end = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else date.today())
    dates = [(end - timedelta(days=offset)).isoformat()
             for offset in range(max(1, args.days))]

    report = audit(dates)
    try:
        LOCALDATA.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(json.dumps(report, indent=2, sort_keys=True))
    except Exception as exc:
        print(f"could not write {OUT_FILE}: {exc}")

    print(f"BetClan timezone assumption: {report['status']}")
    print(f"  assumed {report['assumed_tz']} · review {BETCLAN_TZ_REVIEW_DATE.isoformat()} "
          f"(in {report['days_to_review']} day(s))")
    print(f"  evidence: {report['matched_this_run']} fixture(s) matched this run, "
          f"{report['remembered_samples']} carried from {report['evidence_file']}"
          + (f" (+{report['new_samples_recorded']} newly recorded)"
             if report["new_samples_recorded"] else ""))
    if report["matched_fixtures"] == 0:
        print("  NOTE: still UNKNOWN because no run has yet seen an odds cache "
              "alongside BetClan picks. Evidence accrues automatically — this "
              "resolves itself on the first live daily run, and cannot be "
              "green until it does.")
    print(f"  matched fixtures {report['matched_fixtures']} over {len(dates)} day(s)"
          + (f" · median offset {report['median_offset_minutes']:+.0f} min"
             if report["median_offset_minutes"] is not None else ""))
    print(f"  {report['detail']}")
    if report["status"] == STATUS_DRIFT:
        print("  ACTION: set RACKET_FACTORY_BETCLAN_TZ to the corrected zone "
              "(e.g. Europe/London) and re-run.")
    if args.strict and report["status"] in (STATUS_DRIFT, STATUS_REVIEW_DUE):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
