#!/usr/bin/env python3
"""
Backfill Forebet Predictions

Two modes of operation:

1.  --mode tournament (default)
    Reads the warehouse, groups by tournament, and fetches each tournament page once.
    Best for historical backfill where you have years of match data.

2.  --mode daily
    Fetches predictions-yesterday / predictions-today / predictions-tomorrow.
    One page = all matches across all tournaments.  Best for ongoing daily capture.

Usage examples:
    # Historical backfill (slow, thorough)
    PYTHONPATH=src python3 scripts/backfill_forebet.py --mode tournament

    # Daily capture (fast, 3 pages total)
    PYTHONPATH=src python3 scripts/backfill_forebet.py --mode daily --days yesterday today tomorrow

    # Test on a small subset
    PYTHONPATH=src python3 scripts/backfill_forebet.py --mode tournament --limit 5
"""
import pandas as pd
import argparse
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional
from collections import defaultdict

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources.forebet import (
    ForebetPredictor,
    forebet_cache_key,
    name_signature,
    name_signature_strict,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_forebet")


def _strip_key_columns(frame, key_cols: list[str]):
    """Normalize dedup-key columns: NaN -> "" plus whitespace strip.

    A re-read CSV yields NaN for empty cells while fresh parser rows carry
    ""; without unifying both, cross-run dedup never matches. Empty keys
    downstream behave identically (all joins normalize first).
    """
    for col in key_cols:
        if col in frame.columns:
            frame[col] = frame[col].fillna("").astype(str).str.strip()
    return frame


def _collapse_identity_twins(frame):
    """Collapse same-match rows that differ only in attribution.

    Pass 1 (no warehouse yet) stores tour-less Jina-spelled rows; pass 2
    stores warehouse-matched twins (filled tour, warehouse name spelling).
    Both share (date, name-signature pair) but differ on the 5-key, so plain
    dedup keeps both (run #208: 4 twin pairs). Keep the best-attributed twin
    (filled tour, then filled tournament), newest on ties.
    """
    if frame.empty or not {"match_date", "player_a", "player_b"}.issubset(frame.columns):
        return frame
    sig_a = frame["player_a"].astype(str).map(name_signature_strict)
    sig_b = frame["player_b"].astype(str).map(name_signature_strict)
    twin_key = (
        frame["match_date"].astype(str)
        + "|" + pd.Series(
            [f"{min(x, y)}|{max(x, y)}" for x, y in zip(sig_a, sig_b)],
            index=frame.index,
        )
    )
    if "tour" in frame.columns:
        tour_empty = frame["tour"].fillna("").astype(str).str.strip().eq("")
    else:
        tour_empty = pd.Series(True, index=frame.index)
    if "tournament" in frame.columns:
        tourn = frame["tournament"].fillna("").astype(str).str.strip()
        tourn_empty = tourn.eq("") | tourn.eq("Unknown")
    else:
        tourn_empty = pd.Series(True, index=frame.index)
    ranked = frame.assign(_twin_key=twin_key, _s1=tour_empty, _s2=tourn_empty)
    # Stable sort: emptiest first, best-attributed last; keep=last wins ties by recency.
    ranked = ranked.sort_values(["_twin_key", "_s1", "_s2"], ascending=[True, False, False], kind="mergesort")
    ranked = ranked.drop_duplicates(subset=["_twin_key"], keep="last")
    return ranked.drop(columns=["_twin_key", "_s1", "_s2"])


def _write_predictions(predictions: list[dict], output_dir: Path) -> None:
    if not predictions:
        logger.warning("No predictions to write.")
        return
    out_df = pd.DataFrame(predictions)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key_cols = ["match_date", "tour", "tournament", "player_a", "player_b"]
    out_df = _strip_key_columns(out_df, key_cols)
    # Always dedup (fresh files too): the parser can emit the same match
    # twice (HTML + Jina paths) and key columns can carry stray whitespace.
    out_df = out_df.drop_duplicates(
        subset=[c for c in key_cols if c in out_df.columns], keep="last"
    )
    by_month = out_df.groupby(out_df["match_date"].astype(str).str[:7])
    for month, group in by_month:
        path = out_dir / f"predictions_forebet_{month}.csv.gz"
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            existing = _strip_key_columns(existing, key_cols)
            group = pd.concat([existing, group], ignore_index=True)
        group = group.drop_duplicates(
            subset=[c for c in key_cols if c in group.columns],
            keep="last",
        )
        group = _collapse_identity_twins(group)
        group.to_csv(path, index=False, compression="gzip")
        logger.info("Wrote %d predictions to %s", len(group), path)


def mode_tournament(args) -> int:
    """Historical backfill using tournament pages."""
    try:
        df = pd.read_csv(args.warehouse, low_memory=False)
    except Exception as e:
        logger.error("Could not load warehouse: %s", e)
        return 1

    if df.empty:
        logger.error("Warehouse is empty.")
        return 1

    required = ["match_date", "tour", "tournament", "player_a", "player_b"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error("Warehouse missing required columns: %s", missing)
        return 1

    groups = df.groupby(["tour", "tournament"])
    tournament_list = list(groups.groups.keys())
    logger.info(
        "Tournament mode: %d tournaments, %d matches in warehouse.",
        len(tournament_list), len(df),
    )

    if args.limit:
        tournament_list = tournament_list[: args.limit]
        logger.info("Limiting to %d tournaments.", args.limit)

    predictor = ForebetPredictor()
    predictions: list[dict] = []
    matched_count = 0
    dateless_skipped = 0

    for i, (tour, tournament) in enumerate(tournament_list): # type: ignore
        group_df = groups.get_group((tour, tournament))
        logger.info(
            "[%3d/%d] %s / %s — %d warehouse matches",
            i + 1, len(tournament_list), tour, tournament, len(group_df),
        )

        preds = predictor.fetch_tournament_predictions(tour, tournament)
        if not preds:
            time.sleep(args.delay)
            continue

        # Index by (date, sorted name-signature pair)
        pred_index: dict[tuple[str, tuple[str, str]], dict] = {}
        for p in preds:
            if not p.get("match_date"):
                dateless_skipped += 1
                continue
            h = name_signature(p["player_home"])
            a = name_signature(p["player_away"])
            key = (min(h, a), max(h, a))
            pred_index[(p["match_date"], key)] = p

        for _, row in group_df.iterrows():
            match_date = str(row["match_date"])
            sig_a = name_signature(row["player_a"])
            sig_b = name_signature(row["player_b"])
            key = (min(sig_a, sig_b), max(sig_a, sig_b))

            pred = pred_index.get((match_date, key))
            if not pred:
                continue

            mapped = predictor.map_prediction_to_player(pred, row["player_a"], row["player_b"])
            if not mapped:
                continue

            # FIX: Orient prices by identity (player_home vs player_a), not by predicted winner
            # Previously used predicted_winner to map odds, which is wrong per investigation
            pred_home = str(pred.get("player_home") or "")
            # Determine if pred home matches warehouse player_a
            from racketfactory.sources.forebet import name_signature as _ns
            home_is_a = _ns(pred_home) == sig_a
            # If home is A, then odds_a = odds_home, else odds_a = odds_away
            if home_is_a:
                odds_a = pred.get("odds_home")
                odds_b = pred.get("odds_away")
            else:
                odds_a = pred.get("odds_away")
                odds_b = pred.get("odds_home")
            predictions.append({
                "match_date": match_date,
                "tour": row["tour"],
                "tournament": row["tournament"],
                "player_a": row["player_a"],
                "player_b": row["player_b"],
                "predicted_winner": mapped["predicted_winner"],
                "prediction_prob": mapped["prediction_prob"],
                "odds_a": odds_a,
                "odds_b": odds_b,
                "source": "Forebet",
            })
            matched_count += 1

        if (i + 1) % 10 == 0:
            logger.info(
                "Progress: %d/%d tournaments, %d matched so far.",
                i + 1, len(tournament_list), matched_count,
            )
        time.sleep(args.delay)

    blocked_note = (" [relay blocked by Cloudflare challenge — failing fast]"
                    if predictor.relay_blocked else "")
    logger.info("Tournament mode complete: %d matched predictions, %d skipped (no date)%s.",
                matched_count, dateless_skipped, blocked_note)
    _write_predictions(predictions, args.output_dir)
    return 0



def _map_forebet_result_side(p: dict, player_a: str, player_b: str) -> str | None:
    """Map Forebet result_winner ('1'/'2' home/away) to warehouse player_a/player_b."""
    side = str(p.get("result_winner") or "").strip()
    if side not in {"1", "2"}:
        return None

    home_sig = name_signature(str(p.get("player_home") or ""))
    away_sig = name_signature(str(p.get("player_away") or ""))
    a_sig = name_signature(str(player_a or ""))
    b_sig = name_signature(str(player_b or ""))

    if side == "1":
        if home_sig == a_sig:
            return "player_a"
        if home_sig == b_sig:
            return "player_b"
    if side == "2":
        if away_sig == a_sig:
            return "player_a"
        if away_sig == b_sig:
            return "player_b"

    # Most unmatched rows are stored player_a=home, player_b=away.
    return "player_a" if side == "1" else "player_b"


def _copy_forebet_result_fields(out: dict, p: dict, player_a: str, player_b: str) -> dict:
    """Attach Forebet result fields to a prediction row."""
    result_side = _map_forebet_result_side(p, player_a, player_b)
    out["result_status"] = p.get("result_status")
    out["result_score"] = p.get("result_score")
    out["result_winner"] = result_side
    out["result_winner_name"] = (
        player_a if result_side == "player_a"
        else player_b if result_side == "player_b"
        else p.get("result_winner_name")
    )
    out["result_sets_home"] = p.get("result_sets_home")
    out["result_sets_away"] = p.get("result_sets_away")
    return out


def _forebet_result_rows_from_predictions(predictions: list[dict]) -> pd.DataFrame:
    """Build warehouse-compatible settled result rows from Forebet parsed results."""
    from datetime import datetime

    rows = []
    for p in predictions:
        side = str(p.get("result_winner") or "").strip()
        if side not in {"player_a", "player_b"}:
            continue

        player_a = str(p.get("player_a") or "").strip()
        player_b = str(p.get("player_b") or "").strip()
        if not player_a or not player_b:
            continue

        winner = player_a if side == "player_a" else player_b
        match_date = str(p.get("match_date") or "")[:10]
        if not match_date:
            continue

        rows.append({
            "match_date": match_date,
            "tour": p.get("tour") or "",
            "tournament": p.get("tournament") or "",
            "round": "",
            "player_a": player_a,
            "player_b": player_b,
            "winner": winner,
            "score": p.get("result_score") or "",
            "odds_a": p.get("odds_a"),
            "odds_b": p.get("odds_b"),
            "bookmaker": "Forebet",
            "source": "Forebet_results",
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "oddsportal_url": "",
            "_surface": "",
            "_court": "",
            "_series": "",
            "_comment": "result_from_forebet_yesterday",
            "_location": "",
            "_winner_rank": pd.NA,
            "_loser_rank": pd.NA,
            "_odds_source": "Forebet",
            "_is_live": False,
            "_score_perspective": "player_a_games-player_b_games",
            "_result_sets_home": p.get("result_sets_home"),
            "_result_sets_away": p.get("result_sets_away"),
        })

    return pd.DataFrame(rows)


def _write_forebet_result_rows(predictions: list[dict], output_dir: Path) -> list[Path]:
    result_df = _forebet_result_rows_from_predictions(predictions)
    if result_df.empty:
        return []

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    result_df["match_date"] = result_df["match_date"].astype(str).str[:10]
    key_cols = ["match_date", "tour", "tournament", "player_a", "player_b"]
    result_df = _strip_key_columns(result_df, key_cols)
    for month, group in result_df.groupby(result_df["match_date"].str[:7]):
        path = out_dir / f"forebet_results_tennis_{month}.csv.gz"
        if path.exists():
            old = pd.read_csv(path, low_memory=False)
            old = _strip_key_columns(old, key_cols)
            combined = pd.concat([old, group], ignore_index=True, sort=False)
        else:
            combined = group.copy()

        combined = combined.drop_duplicates(
            subset=[c for c in key_cols if c in combined.columns],
            keep="last",
        )
        combined = _collapse_identity_twins(combined)
        combined.to_csv(path, index=False, compression="gzip")
        written.append(path)

    return written


def _drop_future_garbage_month_files(output_dir: Path, today: date | None = None) -> list[str]:
    """Delete Forebet month files that can only be misdate garbage.

    Two rules (either triggers deletion):
      1. The filename month is more than one month ahead of ``today`` — daily
         mode only ever writes ±3 days around the run date, and tournament
         mode only writes past months, so e.g. a December file in September
         is misdated rows (run #206: Sept-12 rows filed as Dec-9).
      2. Every row in the file is dated more than 14 days in the FUTURE — no
         legitimate fetch produces such rows (upcoming fixtures are at most
         ~1 day out), so the whole month is garbage.
    Legitimate history is untouched: past/current months keep their rows, and
    mixed files (any in-window row) are kept. This also kills
    cache-resurrected garbage: the actions cache can restore month files
    deleted from git, so the sweep must run on every daily capture.
    Returns the deleted filenames.
    """
    if today is None:
        today = date.today()
    out_dir = Path(output_dir)
    dropped: list[str] = []
    if not out_dir.is_dir():
        return dropped
    file_re = re.compile(r"(?:predictions_forebet_|forebet_results_tennis_)(\d{4})-(\d{2})\.csv\.gz")
    this_idx = today.year * 12 + today.month
    cutoff = pd.Timestamp(today) + pd.Timedelta(days=14)
    for path in sorted(out_dir.glob("*.csv.gz")):
        m = file_re.fullmatch(path.name)
        if not m:
            continue
        reason = ""
        if int(m.group(1)) * 12 + int(m.group(2)) > this_idx + 1:
            reason = f"month {m.group(1)}-{m.group(2)} is >1 month ahead of {today.isoformat()}"
        else:
            try:
                dates = pd.to_datetime(
                    pd.read_csv(path, usecols=["match_date"])["match_date"],
                    errors="coerce",
                ).dropna()
            except Exception as e:
                logger.warning("Garbage-month sweep: cannot read %s (%s) — keeping", path.name, e)
                continue
            if len(dates) and bool((dates > cutoff).all()):
                reason = f"all {len(dates)} rows dated >14 days after {today.isoformat()}"
        if reason:
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Garbage-month sweep: cannot delete %s (%s)", path.name, e)
                continue
            logger.warning("Garbage-month sweep: deleted %s (%s)", path.name, reason)
            dropped.append(path.name)
    return dropped


def mode_daily(args) -> int:
    """Daily capture using predictions-yesterday / today / tomorrow pages."""
    predictor = ForebetPredictor()
    predictions: list[dict] = []

    warehouse_df = None
    if args.warehouse and Path(args.warehouse).exists():
        try:
            warehouse_df = pd.read_csv(args.warehouse, low_memory=False)
        except Exception as e:
            logger.warning("Could not read warehouse for matching: %s", e)

    for day in args.days:
        logger.info("Fetching predictions-%s ...", day)
        preds = cached_fetch(forebet_cache_key(day), lambda: predictor.fetch_daily_predictions(day))
        if not preds:
            logger.warning("No predictions returned for %s.", day)
            continue

        logger.info("predictions-%s: %d raw matches parsed.", day, len(preds))

        if warehouse_df is not None and not warehouse_df.empty:
            matched = 0
            unmatched = 0
            dateless = 0
            for p in preds:
                match_date = p.get("match_date")
                if not match_date:
                    dateless += 1
                    continue
                h = name_signature(p["player_home"])
                a = name_signature(p["player_away"])
                key = tuple(sorted([h, a]))

                w_rows = warehouse_df[warehouse_df["match_date"].astype(str) == str(match_date)]
                found = False
                for _, row in w_rows.iterrows():
                    sig_a = name_signature(row["player_a"])
                    sig_b = name_signature(row["player_b"])
                    w_key = tuple(sorted([sig_a, sig_b]))
                    if key == w_key:
                        mapped = predictor.map_prediction_to_player(p, row["player_a"], row["player_b"])
                        if mapped:
                            # FIX: Orient prices by identity, not predicted winner
                            pred_home_sig = name_signature(p["player_home"])
                            row_a_sig = name_signature(row["player_a"])
                            home_is_a = pred_home_sig == row_a_sig
                            if home_is_a:
                                odds_a = p.get("odds_home")
                                odds_b = p.get("odds_away")
                            else:
                                odds_a = p.get("odds_away")
                                odds_b = p.get("odds_home")
                            row_out = {
                                "match_date": match_date,
                                "tour": row["tour"],
                                "tournament": row["tournament"],
                                "player_a": row["player_a"],
                                "player_b": row["player_b"],
                                "predicted_winner": mapped["predicted_winner"],
                                "prediction_prob": mapped["prediction_prob"],
                                "odds_a": odds_a,
                                "odds_b": odds_b,
                                "source": "Forebet",
                            }
                            predictions.append(_copy_forebet_result_fields(row_out, p, row["player_a"], row["player_b"]))
                            matched += 1
                            found = True
                            break
                if not found:
                    row_out = {
                        "match_date": match_date,
                        "tour": "",
                        "tournament": p.get("tournament", "Unknown"),
                        "player_a": p["player_home"],
                        "player_b": p["player_away"],
                        "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                        "prediction_prob": (float(p["prob_home"]) if p.get("predicted_winner") == "1" else float(p["prob_away"])) / 100 if p.get("prob_home") is not None and p.get("prob_away") is not None else None,
                        "odds_a": p.get("odds_home"),
                        "odds_b": p.get("odds_away"),
                        "source": "Forebet",
                    }
                    predictions.append(_copy_forebet_result_fields(row_out, p, p["player_home"], p["player_away"]))
                    unmatched += 1

            logger.info("predictions-%s: %d matched to warehouse, %d stored as new upcoming matches, %d skipped (no date).",
                        day, matched, unmatched, dateless)
        else:
            for p in preds:
                row_out = {
                    "match_date": p["match_date"],
                    "tour": "",
                    "tournament": p.get("tournament", "Unknown"),
                    "player_a": p["player_home"],
                    "player_b": p["player_away"],
                    "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                    "prediction_prob": (float(p["prob_home"]) if p.get("predicted_winner") == "1" else float(p["prob_away"])) / 100 if p.get("prob_home") is not None and p.get("prob_away") is not None else None,
                    "odds_a": p.get("odds_home"),
                    "odds_b": p.get("odds_away"),
                    "source": "Forebet",
                }
                predictions.append(_copy_forebet_result_fields(row_out, p, p["player_home"], p["player_away"]))
            logger.info("predictions-%s: %d raw predictions stored (no warehouse match).", day, len(preds))

        time.sleep(args.delay)

    _write_predictions(predictions, args.output_dir)

    written_results = _write_forebet_result_rows(predictions, Path(args.output_dir))
    for result_path in written_results:
        logger.info("Wrote Forebet result rows to %s", result_path)
    _drop_future_garbage_month_files(args.output_dir)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill predictions from Forebet")
    ap.add_argument("--mode", choices=["tournament", "daily"], default="tournament",
                    help="tournament = historical backfill per tournament page; "
                         "daily = fast capture from predictions-yesterday/today/tomorrow pages")
    ap.add_argument("--warehouse", default=str(ROOT / "localdata" / "warehouse.csv.gz"),
                    help="Path to warehouse")
    ap.add_argument("--output-dir", default=str(ROOT / "localdata"),
                    help="Output directory for predictions CSVs")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit number of tournaments (tournament mode only)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds between requests")
    ap.add_argument("--days", nargs="+", default=["yesterday"],
                    choices=["yesterday", "today", "tomorrow"],
                    help="Which daily pages to fetch (daily mode only)")
    args = ap.parse_args()

    if args.mode == "daily":
        return mode_daily(args)
    else:
        return mode_tournament(args)


if __name__ == "__main__":
    raise SystemExit(main())
