#!/usr/bin/env python3
"""Merge a freshly built warehouse onto the previous run's committed baseline.

Why this exists
---------------
The assembled warehouse is NOT reproducible from committed localdata alone:
the CI build merges runtime caches (comparison odds, official result
fetches, prediction re-merges) that drift run to run. On 2026-09-20 the
`_surface:Hard | fav_odds_band:1.1-1.3 | pred_confidence:High` family
derived as FADE THIS SIGNAL in the mining snapshot but EDGE CONFIRMED in
the post-run rebuild (14 rows drifted on cross_source_agree alone), and
every one of the day's 20 candidates fell through to no_slice_export.

How it works
------------
The previous run's committed warehouse is preserved as the baseline before
the rebuild (daily.py moves it to localdata/warehouse_baseline.csv.gz).
After the final build, this script re-anchors the fresh warehouse onto it:

* historical (non-live) rows present in the baseline keep their dimension
  columns (odds + predictions -> bands / confidence / agree);
* settlement fields (winner, score, ranks, ref odds, result sets) always
  flow in from the fresh build, and a baseline row that is now settled
  also adopts the fresh settled odds;
* matches new to the fresh build, and all live rows, pass through as-is.

The merged file (committed at end of run) is the next run's baseline, so
day-to-day slice tables become reproducible and every match/no-match
decision is auditable against the exact snapshot that was mined.

The drift between baseline and fresh is still MEASURED and reported
(dim_drift_rows in the JSON report) so input drift stays visible even
though it can no longer move verdicts silently.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.entities import player_key  # noqa: E402

SETTLEMENT_COLS = [
    "winner",
    "score",
    "_score_perspective",
    "_sets_a",
    "_sets_b",
    "_winner_rank",
    "_loser_rank",
    "_result_status",
    "_result_sets_home",
    "_result_sets_away",
    "_ref_odds_a",
    "_ref_odds_b",
]
SETTLED_ODDS_COLS = ["odds_a", "odds_b", "bookmaker", "odds_source", "_odds_source"]
_EMPTY = {"", "nan", "<na>", "none"}


def _norm(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _is_live(row: pd.Series) -> bool:
    return _norm(row.get("_is_live")).lower() in {"true", "1", "yes"}


def _settled(row: pd.Series) -> bool:
    return bool(_norm(row.get("winner")))


def match_key(row: pd.Series) -> str:
    """Same identity rule as build_warehouse dedupe: date+context+players."""
    a = _norm(row.get("player_a"))
    b = _norm(row.get("player_b"))
    return "|".join(
        [
            _norm(row.get("match_date")),
            _norm(row.get("tour")),
            _norm(row.get("tournament")),
            "||".join(sorted([player_key(a), player_key(b)])),
        ]
    )


def _drift_cols(baseline: pd.DataFrame, fresh: pd.DataFrame) -> list[str]:
    cols = [c for c in ("odds_a", "odds_b") if c in baseline.columns and c in fresh.columns]
    cols += [
        c
        for c in baseline.columns
        if c in fresh.columns and c.startswith(("predicted_winner", "prediction_prob"))
    ]
    return cols


def merge_baselines(baseline: pd.DataFrame, fresh: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Row-level merge of ``fresh`` onto ``baseline`` (see module docstring)."""
    if baseline is None or baseline.empty:
        merged = fresh.reset_index(drop=True)
        return merged, {
            "status": "passthrough",
            "reason": "no baseline",
            "baseline_rows": 0,
            "fresh_rows": int(len(fresh)),
            "merged_rows": int(len(fresh)),
        }

    b = baseline.copy()
    f = fresh.copy()
    b["__key"] = b.apply(match_key, axis=1)
    f["__key"] = f.apply(match_key, axis=1)
    b = b.drop_duplicates(subset=["__key"], keep="last").set_index("__key")
    f = f.drop_duplicates(subset=["__key"], keep="last").set_index("__key")

    drift_cols = _drift_cols(b, f)
    b_index = list(b.index)
    f_index = list(f.index)
    merged_index = [k for k in b_index if k not in set(f_index)] + f_index

    rows = []
    report = {
        "status": "merged",
        "baseline_rows": int(len(b)),
        "fresh_rows": int(len(f)),
        "shared_keys": 0,
        "new_from_fresh": 0,
        "baseline_only": 0,
        "settlement_updates": 0,
        "live_rows_from_fresh": 0,
        "dim_drift_rows": 0,
        "dim_drift_examples": [],
    }

    for key in merged_index:
        in_b, in_f = key in set(b_index), key in set(f_index)
        if in_b and not in_f:
            report["baseline_only"] += 1
            rows.append(b.loc[key])
            continue
        if in_f and not in_b:
            report["new_from_fresh"] += 1
            rows.append(f.loc[key])
            continue

        report["shared_keys"] += 1
        br, fr = b.loc[key], f.loc[key]
        if _is_live(fr):
            # Fresh live rows always win — live prices move, and today's
            # candidates must carry today's odds (including matches that
            # were still live in the previous snapshot).
            report["live_rows_from_fresh"] += 1
            rows.append(fr)
            continue

        row = br.copy()
        changed = False
        for col in SETTLEMENT_COLS:
            if (
                col in f.columns
                and _norm(fr.get(col))
                and _norm(fr.get(col)) != _norm(br.get(col))
            ):
                row[col] = fr[col]
                changed = True
        if not _settled(br) and _settled(fr):
            # Newly settled: adopt the fresh build's settled odds too.
            for col in SETTLED_ODDS_COLS:
                if col in f.columns and _norm(fr.get(col)):
                    row[col] = fr[col]
            changed = True
        if changed:
            report["settlement_updates"] += 1

        for col in drift_cols:
            if _norm(br.get(col)) != _norm(fr.get(col)):
                report["dim_drift_rows"] += 1
                if len(report["dim_drift_examples"]) < 10:
                    report["dim_drift_examples"].append(
                        {
                            "key": key,
                            "col": col,
                            "baseline": _norm(br.get(col)),
                            "fresh": _norm(fr.get(col)),
                        }
                    )
                break
        rows.append(row)

    merged = pd.DataFrame(rows).reset_index(drop=True)
    if "__key" in merged.columns:
        merged = merged.drop(columns=["__key"])
    report["merged_rows"] = int(len(merged))
    return merged, report


def _sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", default=str(ROOT / "localdata" / "warehouse_baseline.csv.gz"))
    ap.add_argument("--fresh", default=str(ROOT / "localdata" / "warehouse.csv.gz"))
    ap.add_argument("--output", default=None)
    ap.add_argument(
        "--report", default=str(ROOT / "localdata" / "warehouse_merge_report.json")
    )
    args = ap.parse_args()

    fresh_path = Path(args.fresh)
    out_path = Path(args.output) if args.output else fresh_path
    report_path = Path(args.report)
    baseline_path = Path(args.baseline)

    if not fresh_path.exists():
        print(f"merge_warehouse_baseline: fresh warehouse missing: {fresh_path}")
        return 1

    baseline = None
    if baseline_path.exists():
        try:
            baseline = pd.read_csv(baseline_path, low_memory=False)
        except Exception as exc:  # corrupt/stale baseline must not kill the run
            print(f"merge_warehouse_baseline: unreadable baseline ({exc}); passthrough")
            baseline = None
    fresh = pd.read_csv(fresh_path, low_memory=False)

    merged, report = merge_baselines(baseline, fresh)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["baseline_sha256"] = _sha256(baseline_path)
    report["fresh_sha256"] = _sha256(fresh_path)
    report["merged_sha256"] = None

    merged.to_csv(out_path, index=False, compression="gzip")
    report["merged_sha256"] = _sha256(out_path)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(
        "merge_warehouse_baseline: "
        f"status={report['status']} baseline={report.get('baseline_rows', 0)} "
        f"fresh={report.get('fresh_rows', 0)} merged={report.get('merged_rows', 0)} "
        f"settlement_updates={report.get('settlement_updates', 0)} "
        f"dim_drift_rows={report.get('dim_drift_rows', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
