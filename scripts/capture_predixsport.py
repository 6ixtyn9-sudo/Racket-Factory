#!/usr/bin/env python3
"""
Capture PredixSport Predictions
Fetches daily predictions from PredixSport and matches them to the warehouse.
"""
import pandas as pd
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.foretennis import name_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
logger = logging.getLogger("capture_predixsport")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse CSV")
    parser.add_argument("--output-dir", default="localdata", help="Output directory")
    args = parser.parse_args()

    wh_path = Path(args.warehouse)
    if not wh_path.exists():
        logger.error(f"Warehouse not found at {wh_path}")
        return

    logger.info(f"Loading warehouse from {wh_path}")
    wh = pd.read_csv(wh_path, low_memory=False)
    wh["match_date"] = pd.to_datetime(wh["match_date"]).dt.strftime("%Y-%m-%d")

    pred_index = {}
    for idx, row in wh.iterrows():
        date_str = row["match_date"]
        if pd.isna(row["player_a"]) or pd.isna(row["player_b"]): continue
        pa_sig = name_signature(str(row["player_a"]))
        pb_sig = name_signature(str(row["player_b"]))
        if date_str not in pred_index: pred_index[date_str] = []
        pred_index[date_str].append((idx, pa_sig, pb_sig, str(row["player_a"]), str(row["player_b"])))

    px = PredixSportPredictor()
    logger.info("Fetching PredixSport daily predictions...")
    preds = px.fetch_daily()
    
    matched_predictions = []
    for p in preds:
        date_str = p.get("match_date")
        if not date_str or date_str not in pred_index: continue
        sig_home = name_signature(p["player_home"])
        sig_away = name_signature(p["player_away"])
        
        for idx, pa_sig, pb_sig, player_a, player_b in pred_index[date_str]:
            if (sig_home == pa_sig and sig_away == pb_sig):
                matched_predictions.append({
                    "match_id": idx, "match_date": date_str, "tour": wh.at[idx, "tour"], "tournament": wh.at[idx, "tournament"], "player_a": player_a, "player_b": player_b,
                    "predicted_winner": "player_a" if p["predicted_winner"] == "1" else "player_b",
                    "prob_home": p.get("prob_home"), "prob_away": p.get("prob_away"), "source": "PredixSport"
                })
                break
            elif (sig_home == pb_sig and sig_away == pa_sig):
                matched_predictions.append({
                    "match_id": idx, "match_date": date_str, "tour": wh.at[idx, "tour"], "tournament": wh.at[idx, "tournament"], "player_a": player_a, "player_b": player_b,
                    "predicted_winner": "player_b" if p["predicted_winner"] == "1" else "player_a",
                    "prob_home": p.get("prob_away"), "prob_away": p.get("prob_home"), "source": "PredixSport"
                })
                break

    if not matched_predictions:
        logger.warning("No PredixSport predictions matched to warehouse.")
        return

    df_preds = pd.DataFrame(matched_predictions).drop_duplicates(subset=["match_id"])
    out_file = Path(args.output_dir) / "predictions_predixsport_daily.csv.gz"
    df_preds.to_csv(out_file, index=False, compression="gzip")
    logger.info(f"Saved {len(df_preds)} matched predictions to {out_file}")

if __name__ == "__main__":
    main()
