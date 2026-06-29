#!/usr/bin/env python3
"""
Resolve Pending AI Predictions
Matches archived predictions against the built warehouse of completed matches.
"""
import pandas as pd
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.entities import fuzzy_match_players

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
logger = logging.getLogger("resolve_pending")

def resolve_source(archive_file: Path, output_file: Path, wh_df: pd.DataFrame, source_name: str):
    if not archive_file.exists():
        logger.info(f"Archive {archive_file} does not exist, skipping.")
        return
        
    logger.info(f"Resolving {source_name} archive: {archive_file}")
    df_archive = pd.read_csv(archive_file)
    
    # Pre-index warehouse by date for fast lookup
    pred_index = {}
    for idx, row in wh_df.iterrows():
        date_str = row["match_date"]
        if pd.isna(row["player_a"]) or pd.isna(row["player_b"]): continue # type: ignore
        if date_str not in pred_index: pred_index[date_str] = []
        pred_index[date_str].append((idx, str(row["player_a"]), str(row["player_b"]), str(row["tour"]), str(row["tournament"])))
        
    matched_predictions = []
    
    for _, p in df_archive.iterrows():
        date_str = p.get("match_date")
        if not pd.notna(date_str) or date_str not in pred_index: continue # type: ignore
        
        home = str(p.get("player_home"))
        away = str(p.get("player_away"))
        
        for idx, player_a, player_b, tour, tournament in pred_index[date_str]:
            if fuzzy_match_players(home, player_a) and fuzzy_match_players(away, player_b):
                matched_predictions.append({
                    "match_id": idx, "match_date": date_str, "tour": tour, "tournament": tournament, "player_a": player_a, "player_b": player_b,
                    "predicted_winner": "player_a" if str(p.get("predicted_winner")) == "1" else "player_b",
                    "prob_home": p.get("prob_home"), "prob_away": p.get("prob_away"), "source": source_name
                })
                break
            elif fuzzy_match_players(home, player_b) and fuzzy_match_players(away, player_a):
                matched_predictions.append({
                    "match_id": idx, "match_date": date_str, "tour": tour, "tournament": tournament, "player_a": player_a, "player_b": player_b,
                    "predicted_winner": "player_b" if str(p.get("predicted_winner")) == "1" else "player_a",
                    "prob_home": p.get("prob_away"), "prob_away": p.get("prob_home"), "source": source_name
                })
                break
                
    if matched_predictions:
        df_resolved = pd.DataFrame(matched_predictions).drop_duplicates(subset=["match_id"])
        df_resolved.to_csv(output_file, index=False, compression="gzip")
        logger.info(f"Resolved {len(df_resolved)} matches for {source_name}. Saved to {output_file}")
    else:
        logger.warning(f"No matches resolved for {source_name}.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse CSV")
    parser.add_argument("--data-dir", default="localdata", help="Output directory")
    args = parser.parse_args()

    wh_path = Path(args.warehouse)
    if not wh_path.exists():
        logger.error(f"Warehouse not found at {wh_path}")
        return

    logger.info(f"Loading warehouse from {wh_path}")
    wh = pd.read_csv(wh_path, low_memory=False)
    wh["match_date"] = pd.to_datetime(wh["match_date"]).dt.strftime("%Y-%m-%d")
    
    data_dir = Path(args.data_dir)
    resolve_source(data_dir / "archive_predixsport.csv", data_dir / "predictions_predixsport_resolved.csv.gz", wh, "PredixSport")
    resolve_source(data_dir / "archive_betclan.csv", data_dir / "predictions_betclan_resolved.csv.gz", wh, "BetClan")

if __name__ == "__main__":
    main()
