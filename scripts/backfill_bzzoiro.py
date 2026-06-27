import os
import argparse
import logging
from collections import defaultdict
import pandas as pd

from racketfactory.sources.bzzoiro import BzzoiroPredictor
from racketfactory.sources.forebet import name_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Backfill predictions from Bzzoiro API.")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", type=str, default="localdata", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    wh_path = os.path.join(args.output_dir, "warehouse.csv.gz")

    if not os.path.exists(wh_path):
        logger.error(f"Warehouse not found at {wh_path}. Cannot perform backfill.")
        return 1

    logger.info(f"Loading warehouse from {wh_path}")
    wh = pd.read_csv(wh_path, low_memory=False)
    
    # Build a fast index of matches by date
    # Format: dict[date] -> list of (index, sig_a, sig_b, player_a, player_b)
    pred_index = defaultdict(list)
    for idx, row in wh.iterrows():
        match_date = str(row["match_date"])
        if pd.isna(match_date) or match_date == "nan":
            continue
        sig_a = name_signature(str(row["player_a"]))
        sig_b = name_signature(str(row["player_b"]))
        pred_index[match_date].append((idx, sig_a, sig_b, row["player_a"], row["player_b"]))

    logger.info(f"Warehouse loaded. Indexed {len(pred_index)} distinct match dates.")
    
    bp = BzzoiroPredictor()
    
    logger.info(f"Running backfill from {args.start_date} to {args.end_date}")
    
    preds = bp.fetch_historical_predictions(date_from=args.start_date, date_to=args.end_date)
    
    matched_predictions = []
    
    for p in preds:
        date_str = p.get("match_date")
        if not date_str or date_str not in pred_index:
            continue
            
        for idx, pa_sig, pb_sig, player_a, player_b in pred_index[date_str]:
            mapped = bp.map_prediction_to_player(p, player_a, player_b)
            if mapped:
                matched_predictions.append({
                    "match_date": date_str,
                    "tour": wh.at[idx, "tour"],
                    "tournament": wh.at[idx, "tournament"],
                    "player_a": player_a,
                    "player_b": player_b,
                    "predicted_winner": mapped["predicted_winner"],
                    "prediction_prob": mapped.get("prediction_prob"),
                    "source": "Bzzoiro",
                    "match_id": idx,
                })
                break

    if not matched_predictions:
        logger.warning("No predictions matched to warehouse.")
        return 0

    out_df = pd.DataFrame(matched_predictions)
    # Deduplicate in case multiple Bzzoiro records hit the same match
    out_df = out_df.drop_duplicates(subset=["match_id"], keep="last")
    out_df = out_df.drop(columns=["match_id"])
    
    out_file = os.path.join(args.output_dir, f"predictions_bzzoiro_{args.start_date}_{args.end_date}.csv.gz")
    out_df.to_csv(out_file, index=False, compression="gzip")
    
    logger.info(f"Saved {len(out_df)} matched predictions to {out_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
