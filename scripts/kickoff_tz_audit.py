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


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def audit(dates: list[str]) -> dict:
    """Measure the BetClan-vs-reference kickoff offset over ``dates``."""
    samples: list[dict] = []
    for target_date in dates:
        betclan = betclan_kickoffs(target_date)
        reference = theoddsapi_kickoffs(target_date)
        for key, when in betclan.items():
            if key not in reference:
                continue
            delta = minutes_between_wall_times(when, reference[key])
            if delta is None:
                continue
            samples.append({"date": target_date, "fixture": key,
                            "betclan": when, "reference": reference[key],
                            "delta_minutes": delta})
    offset = _median([s["delta_minutes"] for s in samples]) if samples else None
    status = betclan_tz_status(measured_offset_minutes=offset,
                               sample_size=len(samples))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dates_checked": dates,
        "matched_fixtures": len(samples),
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
