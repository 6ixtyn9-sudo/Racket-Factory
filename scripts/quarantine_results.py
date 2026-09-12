#!/usr/bin/env python3
"""Quarantine structurally invalid result rows (dry-run by default).

Moves rows that can never settle honestly — tournament names parsed as
players, winner/score incoherence, live markers, incomplete final sets —
out of the settlement result files into ``localdata/quarantine/``.

Legacy rows that are merely weak (no tournament, no match id, but a
coherent winner + score) are KEPT and marked ``_row_quality=legacy`` so
settlement deprioritizes them instead of trusting them blindly.

Usage:
    PYTHONPATH=src python3 scripts/quarantine_results.py --data-dir localdata
    PYTHONPATH=src python3 scripts/quarantine_results.py --data-dir localdata --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.settlement import check_score, row_finality  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("quarantine")

PREFIXES = (
    "challenger_results_tennis_",
    "forebet_results_tennis_",
    "foretennis_results_tennis_",
    "theoddsapi_scores_tennis_",
)

_SETS_DASH_RE = re.compile(r"^([0-3])\s*-\s*([0-3])$")
_SETS_CONCAT_RE = re.compile(r"^([0-3])([0-3])$")
_SETS_SINGLE_RE = re.compile(r"^([2-3])$")


def migrate_sets_only(row: dict) -> str:
    """Rewrite sets-only scores into ``_sets_a/_sets_b`` evidence in place.

    ForeTennis ``actual_result`` rows store concatenated sets (``20``) and old
    Odds-API score rows store dashed sets (``2-0``); neither is a per-set
    score string. Returns "" when migrated, else a quarantine reason.
    """
    from racketfactory.settlement import players_match
    source = str(row.get("source") or "")
    score = str(row.get("score") or "").strip()
    perspective = str(row.get("_score_perspective") or "")
    sets = None
    if source == "ForeTennis_results" and "sets" in perspective:
        m = _SETS_CONCAT_RE.match(score)
        if m:
            sets = (int(m.group(1)), int(m.group(2)))
        elif _SETS_SINGLE_RE.match(score):
            # Single digit = sets won by the recorded winner (positional
            # perspective does not apply: "Royer vs Zverev, W:Zverev, 3").
            n = int(score)
            winner_side = str(row.get("winner") or "")
            home, _ = players_match(winner_side, row.get("player_a"))
            away, _ = players_match(winner_side, row.get("player_b"))
            if home and not away:
                sets = (n, 0)
            elif away and not home:
                sets = (0, n)
            else:
                return f"winner {winner_side!r} does not resolve to a side"
    elif source == "TheOddsAPI_scores":
        m = _SETS_DASH_RE.match(score)
        if m:
            sets = (int(m.group(1)), int(m.group(2)))
    if sets is None:
        return ""
    if sets[0] == sets[1]:
        return f"sets-only draw {score!r} cannot settle"
    row["_sets_a"], row["_sets_b"] = sets
    row["score"] = ""
    winner = str(row.get("winner") or "")
    home_won = sets[0] > sets[1]
    if home_won:
        ok, _ = players_match(winner, row.get("player_a"))
        expect = "player_a"
    else:
        ok, _ = players_match(winner, row.get("player_b"))
        expect = "player_b"
    if not ok:
        return f"winner/sets mismatch: {winner!r} vs sets {sets[0]}-{sets[1]}"
    row["_result_status"] = str(row.get("_result_status") or "COMPLETED_FEED" or "").strip() or "COMPLETED_FEED"
    return ""


_TOURNAMENT_PLAYER_RE = re.compile(
    r"(?i)\b(wta|atp|challenger|itf|utr|masters|trophy|davis\s+cup|bjk\s+cup)\b"
    r"|\b(us|french|australian)\s+open\b|\bwimbledon\b"
)


def looks_like_tournament_as_player(name: object, tournament: object) -> str:
    """Return a reason when a player field is really a tournament name."""
    text = str(name or "").strip()
    if not text:
        return "empty player"
    if tournament and text.lower() == str(tournament).strip().lower():
        return "player equals tournament"
    if _TOURNAMENT_PLAYER_RE.search(text):
        # Real player names never contain tour/event keywords as tokens.
        return f"tournament-like player {text!r}"
    return ""


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    text = str(value).strip()
    return "" if text in ("nan", "None", "<NA>", "NaT", "N/A") else text


def classify_row(row: dict) -> tuple[str, str]:
    """Return (verdict, reason): keep / legacy / quarantine.

    Mutates ``row`` in place (NaN cleaning + sets-only migration) so callers
    can persist the migrated evidence.
    """
    for k, v in list(row.items()):
        if isinstance(v, str) or v is None:
            row[k] = _clean(v)
        elif isinstance(v, float) and v != v:
            row[k] = ""
    tournament = str(row.get("tournament") or "").strip()
    for side in ("player_a", "player_b"):
        reason = looks_like_tournament_as_player(row.get(side), tournament)
        if reason:
            return "quarantine", reason
    reason = migrate_sets_only(row)
    if reason:
        return "quarantine", reason
    winner = str(row.get("winner") or "").strip()
    if not winner:
        return "quarantine", "no winner recorded"
    fin = row_finality(row)
    if not fin.final:
        # Not-final rows are settlement POISON only when they also carry
        # incoherent evidence; incomplete/live rows are quarantined so they
        # can never settle, with the validator reason preserved.
        chk_reason = fin.reason
        if "live" in chk_reason or "incomplete" in chk_reason or "disagree" in chk_reason:
            return "quarantine", f"not final: {chk_reason}"
        if "unparseable" in chk_reason or "without set scores" in chk_reason:
            return "quarantine", f"not final: {chk_reason}"
        return "quarantine", f"not final: {chk_reason}"
    has_id = any(str(row.get(k) or "").strip()
                 for k in ("_te_id", "_result_id", "_api_event_id"))
    if not tournament and not has_id:
        return "legacy", "weak legacy row: no tournament and no match id"
    score = str(row.get("score") or "").strip()
    if score:
        chk = check_score(score)
        if not chk.valid:
            return "quarantine", f"bad score: {chk.reason}"
    return "keep", ""


def process_file(path: Path, quarantine_dir: Path, *, apply: bool) -> dict:
    df = pd.read_csv(path, low_memory=False)
    for col in ("_sets_a", "_sets_b", "_result_status", "_row_quality"):
        if col not in df.columns:
            df[col] = ""
    for col in ("_sets_a", "_sets_b", "score", "_result_status"):
        if col in df.columns:
            df[col] = df[col].astype(object)
    verdicts: list[str] = []
    reasons: list[str] = []
    migrated = 0
    for idx, series in df.iterrows():
        mutated = series.to_dict()
        verdict, reason = classify_row(mutated)
        verdicts.append(verdict)
        reasons.append(reason)
        if verdict in ("keep", "legacy"):
            # Persist sets-only migrations (_sets_a/_sets_b, emptied score).
            row_migrated = False
            for col in ("_sets_a", "_sets_b", "score", "_result_status"):
                if col in mutated and col in df.columns and mutated[col] != series.get(col):
                    if not (pd.isna(mutated[col]) and pd.isna(series.get(col))):
                        df.at[idx, col] = mutated[col]
                        if col in ("_sets_a", "_sets_b"):
                            row_migrated = True
            if row_migrated:
                migrated += 1
    df["_quarantine_verdict"] = verdicts
    df["_quarantine_reason"] = reasons

    bad = df[df["_quarantine_verdict"] == "quarantine"].copy()
    legacy = df[df["_quarantine_verdict"] == "legacy"].copy()
    good = df[df["_quarantine_verdict"] == "keep"].copy()

    summary = {
        "file": path.name,
        "total": len(df),
        "keep": len(good),
        "legacy": len(legacy),
        "quarantine": len(bad),
        "reasons": bad["_quarantine_reason"].value_counts().head(10).to_dict(),
        "examples": bad[["match_date", "tournament", "player_a", "player_b",
                          "winner", "score", "_quarantine_reason"]].head(8).to_dict(orient="records"),
    }
    summary["migrated_sets"] = migrated
    if not apply or (bad.empty and legacy.empty and not migrated):
        return summary

    keep_df = pd.concat([good, legacy], ignore_index=True)
    if "_row_quality" not in keep_df.columns:
        keep_df["_row_quality"] = ""
    mask_legacy = keep_df["_quarantine_verdict"] == "legacy"
    keep_df.loc[mask_legacy, "_row_quality"] = "legacy"
    keep_df = keep_df.drop(columns=["_quarantine_verdict", "_quarantine_reason"])
    keep_df.to_csv(path, index=False, compression="gzip")

    if not bad.empty:
        bad = bad.drop(columns=["_quarantine_verdict"])
        bad["_quarantined_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        qpath = quarantine_dir / path.name
        if qpath.exists():
            old = pd.read_csv(qpath, low_memory=False)
            bad = pd.concat([old, bad], ignore_index=True, sort=False)
        bad.to_csv(qpath, index=False, compression="gzip")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="localdata")
    ap.add_argument("--apply", action="store_true",
                    help="Rewrite files (default is dry-run report only)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    quarantine_dir = data_dir / "quarantine"
    if args.apply:
        quarantine_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in data_dir.glob("*_tennis_*.csv.gz")
                   if p.name.startswith(PREFIXES))
    if not files:
        logger.warning("No result files found in %s", data_dir)
        return 0

    report = {"applied": args.apply, "files": []}
    for path in files:
        summary = process_file(path, quarantine_dir, apply=args.apply)
        report["files"].append(summary)
        logger.info("%s: total=%d keep=%d legacy=%d quarantine=%d",
                    summary["file"], summary["total"], summary["keep"],
                    summary["legacy"], summary["quarantine"])

    total_q = sum(f["quarantine"] for f in report["files"])
    total_l = sum(f["legacy"] for f in report["files"])
    total_m = sum(f.get("migrated_sets", 0) for f in report["files"])
    logger.info("TOTAL quarantined=%d legacy-marked=%d sets-migrated=%d (applied=%s)",
                total_q, total_l, total_m, args.apply)
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rpath = quarantine_dir / f"report_{stamp}.json"
        rpath.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        logger.info("Wrote %s", rpath)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
