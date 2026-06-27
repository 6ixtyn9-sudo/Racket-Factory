#!/usr/bin/env python3
"""
Backfill Market Probabilities
Calculates vig-free implied probabilities from warehouse odds and saves them as a "Market" prediction source.
"""
import pandas as pd
import logging
from pathlib import Path
import sys

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
logger = logging.getLogger("backfill_market")

def main():
    wh_path = ROOT / "localdata/warehouse.csv.gz"
    if not wh_path.exists():
        logger.error(f"Warehouse not found at {wh_path}")
        return

    logger.info(f"Loading warehouse from {wh_path}")
    df = pd.read_csv(wh_path, low_memory=False)

    if "odds_a" not in df.columns or "odds_b" not in df.columns:
        logger.error("odds_a or odds_b missing from warehouse.")
        return

    # Filter where odds exist
    df_valid = df.dropna(subset=["odds_a", "odds_b", "match_date", "tour", "tournament", "player_a", "player_b"]).copy()
    
    # Ensure numeric
    df_valid["odds_a"] = pd.to_numeric(df_valid["odds_a"], errors="coerce")
    df_valid["odds_b"] = pd.to_numeric(df_valid["odds_b"], errors="coerce")
    df_valid = df_valid.dropna(subset=["odds_a", "odds_b"])
    df_valid = df_valid[(df_valid["odds_a"] > 1) & (df_valid["odds_b"] > 1)]

    # Calculate vig-free probabilities
    implied_a = 1.0 / df_valid["odds_a"]
    implied_b = 1.0 / df_valid["odds_b"]
    margin = implied_a + implied_b

    df_valid["prob_home"] = (implied_a / margin * 100).round().astype(int)
    df_valid["prob_away"] = (implied_b / margin * 100).round().astype(int)

    # Determine predicted winner (the favorite)
    def get_winner(row):
        return "player_a" if row["prob_home"] >= row["prob_away"] else "player_b"
        
    df_valid["predicted_winner"] = df_valid.apply(get_winner, axis=1)
    df_valid["prediction_prob"] = df_valid[["prob_home", "prob_away"]].max(axis=1)
    df_valid["source"] = "Market"

    out_cols = ["match_date", "tour", "tournament", "player_a", "player_b", 
                "predicted_winner", "prediction_prob", "prob_home", "prob_away", "source"]

    out_df = df_valid[out_cols].copy()
    
    # Drop duplicates just in case
    out_df = out_df.drop_duplicates(subset=["match_date", "tour", "tournament", "player_a", "player_b"])

    out_file = ROOT / "localdata/predictions_market_historical.csv.gz"
    out_df.to_csv(out_file, index=False, compression="gzip")
    logger.info(f"Saved {len(out_df)} market predictions to {out_file}")

if __name__ == "__main__":
    main()
