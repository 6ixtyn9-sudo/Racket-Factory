#!/usr/bin/env python3
"""
Backfill ForeTennis Predictions

Fetches predictions from ForeTennis and matches them to the warehouse.
Supports daily mode (lastpredictions) and historical mode (by Tour + Year).

Usage:
    PYTHONPATH=src python3 scripts/backfill_foretennis.py --mode daily
    PYTHONPATH=src python3 scripts/backfill_foretennis.py --mode historical --tour atp --year 2024
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse CSV")
    parser.add_argument("--mode", choices=["daily", "historical"], default="daily", help="Run mode")
    parser.add_argument("--tour", choices=["atp", "wta"], help="Tour for historical mode")
    parser.add_argument("--year", type=int, help="Year for historical mode")
    args = parser.parse_args()

    if args.mode == "historical" and (not args.tour or not args.year):
        parser.error("--tour and --year are required for historical mode")

    wh_path = Path(args.warehouse)
    if not wh_path.exists():
        logger.error(f"Warehouse not found at {wh_path}")
        return

    logger.info(f"Loading warehouse from {wh_path}")
    wh = pd.read_csv(wh_path, compression="gzip", low_memory=False)
    wh["match_date"] = pd.to_datetime(wh["match_date"]).dt.strftime("%Y-%m-%d")

    # Build pred_index dictionary
    pred_index = {}
    for idx, row in wh.iterrows():
        date_str = row["match_date"]
        # Ensure we don't crash on nan names
        if pd.isna(row["player_a"]) or pd.isna(row["player_b"]):
            continue
            
        pa_sig = name_signature(str(row["player_a"]))
        pb_sig = name_signature(str(row["player_b"]))
        if date_str not in pred_index:
            pred_index[date_str] = []
        pred_index[date_str].append((idx, pa_sig, pb_sig, str(row["player_a"]), str(row["player_b"])))

    ft = ForeTennisPredictor()
    matched_predictions = []

    if args.mode == "daily":
        logger.info("Running in DAILY mode (lastpredictions)")
        preds = ft.fetch_lastpredictions()
        logger.info(f"Fetched {len(preds)} predictions from lastpredictions")
        
        for p in preds:
            date_str = p.get("match_date")
            if not date_str or date_str not in pred_index:
                continue
                
            for idx, pa_sig, pb_sig, player_a, player_b in pred_index[date_str]:
                mapped = ft.map_prediction_to_player(p, player_a, player_b)
                if mapped:
                    matched_predictions.append({
                        "match_id": idx,
                        "match_date": date_str,
                        "predicted_winner_foretennis": mapped["predicted_winner"],
                        "prob_home_foretennis": mapped.get("prob_home"),
                        "prob_away_foretennis": mapped.get("prob_away"),
                    })
                    break

    else:
        logger.info(f"Running in HISTORICAL mode for {args.tour.upper()} {args.year}")
        tournaments = ft.fetch_tournaments_for_year(args.tour, args.year)
        logger.info(f"Found {len(tournaments)} tournaments.")
        
        for t_url in tournaments:
            logger.info(f"Fetching predictions from {t_url}")
            preds = ft.fetch_tournament_predictions(t_url)
            logger.info(f"  -> {len(preds)} predictions found.")
            
            for p in preds:
                date_str = p.get("match_date")
                if not date_str or date_str not in pred_index:
                    continue
                    
                for idx, pa_sig, pb_sig, player_a, player_b in pred_index[date_str]:
                    mapped = ft.map_prediction_to_player(p, player_a, player_b)
                    if mapped:
                        matched_predictions.append({
                            "match_id": idx,
                            "match_date": date_str,
                            "predicted_winner_foretennis": mapped["predicted_winner"],
                            "prob_home_foretennis": mapped.get("prob_home"),
                            "prob_away_foretennis": mapped.get("prob_away"),
                        })
                        break

    if not matched_predictions:
        logger.warning("No predictions matched to warehouse.")
        return

    df_preds = pd.DataFrame(matched_predictions)
    
    # Drop duplicates in case of overlaps
    df_preds = df_preds.drop_duplicates(subset=["match_id"])
    
    # Save the raw extractions to localdata for the warehouse builder to pick up
    out_file = ROOT / f"localdata/foretennis_preds_{args.mode}.csv"
    df_preds.to_csv(out_file, index=False)
    logger.info(f"Saved {len(df_preds)} matched predictions to {out_file}")

if __name__ == "__main__":
    main()
