#!/usr/bin/env python3
"""
Backfill Forebet Predictions
Reads the warehouse and fetches predictions for each match.
"""
import pandas as pd
import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.predictors.forebet import ForebetPredictor
from racketfactory.entities import normalize_player

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_forebet")

def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill predictions from Forebet")
    ap.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse")
    ap.add_argument("--output-dir", default="localdata", help="Output directory")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.warehouse, low_memory=False)
    except Exception as e:
        logger.error("Could not load warehouse: %s", e)
        return 1

    predictor = ForebetPredictor()
    predictions = []

    logger.info("Starting prediction backfill for %d matches...", len(df))

    for i, row in df.iterrows():
        # Construct a slug for the URL (Example: "atp-australian-open-2024")
        # In a real scenario, we'd have a mapping for tournament slugs
        tour = row['tour'].lower()
        tournament = row['tournament'].lower().replace(" ", "-")
        year = str(row['match_date'])[:4]
        match_slug = f"{row['player_a']}_{row['player_b']}".lower().replace(" ", "-")
        
        slug = f"{tour}-{tournament}-{year}"
        match_slug = match_slug
        
        # Fetch prediction
        res = predictor.fetch_prediction(slug, match_slug)
        
        if res:
            # Map the predicted name to a/b
            winner_id = predictor.map_prediction_to_player(
                res['predicted_winner'], row['player_a'], row['player_b']
            )
            
            predictions.append({
                "match_date": row['match_date'],
                "tour": row['tour'],
                "tournament": row['tournament'],
                "player_a": row['player_a'],
                "player_b": row['player_b'],
                "predicted_winner": winner_id,
                "prediction_prob": res['probability'],
                "source": "Forebet"
            })
        
        if (i + 1) % 100 == 0:
            logger.info("Processed %d/%d matches...", i + 1, len(df))
            time.sleep(1) # Respectful rate limit

    if not predictions:
        logger.warning("No predictions were successfully fetched.")
        return 1

    # Write to monthly CSVs (same pattern as tennisdata)
    out_df = pd.DataFrame(predictions)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Group by month
    by_month = out_df.groupby(out_df['match_date'].str[:7])
    for month, group in by_month:
        path = out_dir / f"predictions_forebet_{month}.csv.gz"
        group.to_csv(path, index=False, compression="gzip")
        logger.info("Wrote predictions to %s", path)

    logger.info("Backfill complete. Fetched %d predictions.", len(predictions))
    return 0

if __name__ == "__main__":
    main()
