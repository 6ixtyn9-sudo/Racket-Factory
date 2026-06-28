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
import sys
import time
from pathlib import Path
from typing import Optional
from collections import defaultdict

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.forebet import ForebetPredictor, name_signature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_forebet")


def _write_predictions(predictions: list[dict], output_dir: Path) -> None:
    if not predictions:
        logger.warning("No predictions to write.")
        return
    out_df = pd.DataFrame(predictions)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_month = out_df.groupby(out_df["match_date"].astype(str).str[:7])
    for month, group in by_month:
        path = out_dir / f"predictions_forebet_{month}.csv.gz"
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            group = pd.concat([existing, group], ignore_index=True)
            group = group.drop_duplicates(
                subset=["match_date", "tour", "tournament", "player_a", "player_b"],
                keep="last",
            )
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

    for i, (tour, tournament) in enumerate(tournament_list):
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
                continue
            h = name_signature(p["player_home"])
            a = name_signature(p["player_away"])
            key = tuple(sorted([h, a]))
            pred_index[(p["match_date"], key)] = p

        for _, row in group_df.iterrows():
            match_date = str(row["match_date"])
            sig_a = name_signature(row["player_a"])
            sig_b = name_signature(row["player_b"])
            key = tuple(sorted([sig_a, sig_b]))

            pred = pred_index.get((match_date, key))
            if not pred:
                continue

            mapped = predictor.map_prediction_to_player(pred, row["player_a"], row["player_b"])
            if not mapped:
                continue

            predictions.append({
                "match_date": match_date,
                "tour": row["tour"],
                "tournament": row["tournament"],
                "player_a": row["player_a"],
                "player_b": row["player_b"],
                "predicted_winner": mapped["predicted_winner"],
                "prediction_prob": mapped["prediction_prob"],
                "odds_a": pred.get("odds_home") if mapped["predicted_winner"] == "player_a" else pred.get("odds_away"),
                "odds_b": pred.get("odds_away") if mapped["predicted_winner"] == "player_a" else pred.get("odds_home"),
                "source": "Forebet",
            })
            matched_count += 1

        if (i + 1) % 10 == 0:
            logger.info(
                "Progress: %d/%d tournaments, %d matched so far.",
                i + 1, len(tournament_list), matched_count,
            )
        time.sleep(args.delay)

    logger.info("Tournament mode complete: %d matched predictions.", matched_count)
    _write_predictions(predictions, args.output_dir)
    return 0


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
        preds = predictor.fetch_daily_predictions(day)
        if not preds:
            logger.warning("No predictions returned for %s.", day)
            continue

        logger.info("predictions-%s: %d raw matches parsed.", day, len(preds))

        if warehouse_df is not None and not warehouse_df.empty:
            matched = 0
            unmatched = 0
            for p in preds:
                match_date = p.get("match_date")
                if not match_date:
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
                            predictions.append({
                                "match_date": match_date,
                                "tour": row["tour"],
                                "tournament": row["tournament"],
                                "player_a": row["player_a"],
                                "player_b": row["player_b"],
                                "predicted_winner": mapped["predicted_winner"],
                                "prediction_prob": mapped["prediction_prob"],
                                "odds_a": p.get("odds_home") if mapped["predicted_winner"] == "player_a" else p.get("odds_away"),
                                "odds_b": p.get("odds_away") if mapped["predicted_winner"] == "player_a" else p.get("odds_home"),
                                "source": "Forebet",
                            })
                            matched += 1
                            found = True
                            break
                if not found:
                    predictions.append({
                        "match_date": match_date,
                        "tour": "",
                        "tournament": p.get("tournament", "Unknown"),
                        "player_a": p["player_home"],
                        "player_b": p["player_away"],
                        "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                        "prediction_prob": (p["prob_home"] if p.get("predicted_winner") == "1" else p.get("prob_away")) / 100 if p.get("prob_home") is not None else None,
                        "odds_a": p.get("odds_home"),
                        "odds_b": p.get("odds_away"),
                        "source": "Forebet",
                    })
                    unmatched += 1

            logger.info("predictions-%s: %d matched to warehouse, %d stored as new upcoming matches.", day, matched, unmatched)
        else:
            for p in preds:
                predictions.append({
                    "match_date": p["match_date"],
                    "tour": "",
                    "tournament": p.get("tournament", "Unknown"),
                    "player_a": p["player_home"],
                    "player_b": p["player_away"],
                    "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                    "prediction_prob": (p["prob_home"] if p.get("predicted_winner") == "1" else p.get("prob_away")) / 100 if p.get("prob_home") is not None else None,
                    "odds_a": p.get("odds_home"),
                    "odds_b": p.get("odds_away"),
                    "source": "Forebet",
                })
            logger.info("predictions-%s: %d raw predictions stored (no warehouse match).", day, len(preds))

        time.sleep(args.delay)

    _write_predictions(predictions, args.output_dir)
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