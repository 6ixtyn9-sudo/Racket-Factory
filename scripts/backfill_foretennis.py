#!/usr/bin/env python3
"""
Backfill ForeTennis Predictions

Fetches the lastpredictions page (finished matches with results) and
matches them to the warehouse. This is a genuine backtesting source —
ForeTennis predictions include actual results, so we can validate accuracy.

Usage:
    PYTHONPATH=src python3 scripts/backfill_foretennis.py
    PYTHONPATH=src python3 scripts/backfill_foretennis.py --warehouse localdata/warehouse.csv.gz
"""
import pandas as pd
import argparse
import logging
import sys
from pathlib import Path

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.foretennis import ForeTennisPredictor, name_signature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_foretennis")


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill predictions from ForeTennis")
    ap.add_argument("--warehouse", default=str(ROOT / "localdata" / "warehouse.csv.gz"),
                    help="Path to warehouse")
    ap.add_argument("--output-dir", default=str(ROOT / "localdata"),
                    help="Output directory")
    args = ap.parse_args()

    predictor = ForeTennisPredictor()
    logger.info("Fetching ForeTennis lastpredictions...")
    preds = predictor.fetch_lastpredictions()
    if not preds:
        logger.warning("No predictions returned from ForeTennis.")
        return 1

    logger.info("Fetched %d raw predictions from ForeTennis.", len(preds))

    # Build lookup index
    pred_index: dict[tuple[str, tuple[str, str]], dict] = {}
    for p in preds:
        if not p.get("match_date"):
            continue
        h = name_signature(p["player_home"])
        a = name_signature(p["player_away"])
        key = tuple(sorted([h, a]))
        pred_index[(p["match_date"], key)] = p

    # Try to match against warehouse
    predictions = []
    matched = 0
    if args.warehouse and Path(args.warehouse).exists():
        try:
            warehouse_df = pd.read_csv(args.warehouse, low_memory=False)
        except Exception as e:
            logger.warning("Could not read warehouse: %s", e)
            warehouse_df = None

        if warehouse_df is not None and not warehouse_df.empty:
            for _, row in warehouse_df.iterrows():
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
                    "source": "ForeTennis",
                })
                matched += 1

            logger.info("Matched %d predictions to warehouse.", matched)
    else:
        logger.info("No warehouse found. Dumping raw predictions.")
        for p in preds:
            predictions.append({
                "match_date": p["match_date"],
                "tour": "",  # Unknown without warehouse
                "tournament": p.get("tournament", "Unknown"),
                "player_a": p["player_home"],
                "player_b": p["player_away"],
                "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                "prediction_prob": (p["prob_home"] / 100 if p.get("predicted_winner") == "1" else p.get("prob_away") / 100) if p.get("prob_home") is not None else None,
                "source": "ForeTennis",
            })

    if not predictions:
        logger.warning("No predictions matched or generated.")
        return 1

    # Write to monthly CSVs
    out_df = pd.DataFrame(predictions)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_month = out_df.groupby(out_df["match_date"].str[:7])
    for month, group in by_month:
        path = out_dir / f"predictions_foretennis_{month}.csv.gz"
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            group = pd.concat([existing, group], ignore_index=True)
            group = group.drop_duplicates(
                subset=["match_date", "tour", "tournament", "player_a", "player_b"],
                keep="last",
            )
        group.to_csv(path, index=False, compression="gzip")
        logger.info("Wrote %d predictions to %s", len(group), path)

    logger.info("Backfill complete. Total predictions: %d", len(predictions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
