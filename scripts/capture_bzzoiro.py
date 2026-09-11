#!/usr/bin/env python3
"""Capture Bzzoiro Predictions - daily"""
import pandas as pd
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.bzzoiro import BzzoiroPredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
logger = logging.getLogger("capture_bzzoiro")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="localdata")
    parser.add_argument("--warehouse", default="localdata/warehouse.csv.gz")
    args = parser.parse_args()

    out_file = Path(args.output_dir) / "archive_bzzoiro.csv"
    bp = BzzoiroPredictor()
    logger.info("Fetching Bzzoiro daily predictions...")
    preds = bp.fetch_daily()
    if not preds:
        logger.warning("No Bzzoiro predictions fetched.")
        return
    df_new = pd.DataFrame(preds)
    if out_file.exists():
        df_old = pd.read_csv(out_file)
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_combined = df_new
    df_combined = df_combined.drop_duplicates(subset=["match_date", "player_home", "player_away"], keep="last")
    df_combined.to_csv(out_file, index=False)
    logger.info(f"Saved {len(df_new)} new predictions. Archive now has {len(df_combined)} records.")

if __name__ == "__main__":
    main()
