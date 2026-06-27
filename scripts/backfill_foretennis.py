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
    parser.add_argument("--output-dir", default="localdata", help="Output directory")
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
                        "match_date": date_str,
                        "tour": wh.at[idx, "tour"],
                        "tournament": wh.at[idx, "tournament"],
                        "player_a": player_a,
                        "player_b": player_b,
                        "predicted_winner": mapped["predicted_winner"],
                        "prediction_prob": mapped.get("prediction_prob"),
                        "source": "ForeTennis",
                        "match_id": idx,  # Keep for deduplication
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
                            "match_date": date_str,
                            "tour": wh.at[idx, "tour"],
                            "tournament": wh.at[idx, "tournament"],
                            "player_a": player_a,
                            "player_b": player_b,
                            "predicted_winner": mapped["predicted_winner"],
                            "prediction_prob": mapped.get("prediction_prob"),
                            "source": "ForeTennis",
                            "match_id": idx,
                        })
                        break

    if not matched_predictions:
        logger.warning("No predictions matched to warehouse.")
        return

    df_preds = pd.DataFrame(matched_predictions)
    
    # Drop duplicates in case of overlaps
    df_preds = df_preds.drop_duplicates(subset=["match_id"])
    
    # Save the raw extractions to the output dir for the warehouse builder to pick up
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    if args.mode == "daily":
        filename = "predictions_foretennis_daily.csv.gz"
    else:
        filename = f"predictions_foretennis_{args.tour}_{args.year}.csv.gz"
        
    out_file = out_dir / filename
    df_preds.to_csv(out_file, index=False, compression="gzip")
    logger.info(f"Saved {len(df_preds)} matched predictions to {out_file}")

if __name__ == "__main__":
    main()
