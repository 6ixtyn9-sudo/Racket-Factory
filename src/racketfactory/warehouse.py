"""
Racket Factory Warehouse
Handles the merging and deduplication of various tennis data sources.
"""
import pandas as pd
from pathlib import Path
import logging
from typing import Optional
from racketfactory.entities import player_key

logger = logging.getLogger(__name__)

def build_warehouse(data_dir: str = "localdata", output_file: str = "warehouse.csv.gz") -> Optional[Path]:
    """
    Merge all tennis data sources into a unified warehouse, 
    including external prediction sources.
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error("Data directory not found: %s", data_path)
        return None

    # 1. Load Match Data
    all_files = list(data_path.glob("*.csv.gz"))
    dfs = []
    for f in all_files:
        if "tennis" in f.name and "predictions" not in f.name:
            try:
                logger.info("Loading match data from %s...", f.name)
                temp_df = pd.read_csv(f, low_memory=False)
                if not temp_df.empty:
                    dfs.append(temp_df)
            except Exception as e:
                logger.error("Failed to load %s: %s", f.name, e)
    
    if not dfs:
        logger.error("No valid match data files found.")
        return None
    
    warehouse = pd.concat(dfs, ignore_index=True)
    
    # Standard Deduplication
    critical_cols = ["match_date", "tour", "tournament", "player_a", "player_b"]
    for col in critical_cols:
        if col not in warehouse.columns:
            warehouse[col] = ""

    warehouse['p_a_key'] = warehouse['player_a'].apply(player_key)
    warehouse['p_b_key'] = warehouse['player_b'].apply(player_key)
    warehouse['_sorted_players'] = warehouse.apply(
        lambda r: tuple(sorted([r['p_a_key'], r['p_b_key']])), axis=1
    )
    
    warehouse = warehouse.drop_duplicates(
        subset=["match_date", "tour", "tournament", "_sorted_players"], 
        keep="last"
    ).drop(columns=['p_a_key', 'p_b_key', '_sorted_players'])
    
    # 2. Join Predictions (The "Signal" Layer)
    # We look for files matching predictions_*.csv.gz
    pred_files = list(data_path.glob("predictions_*.csv.gz"))
    if pred_files:
        logger.info("Merging %d prediction files into warehouse...", len(pred_files))
        pred_dfs = []
        for pf in pred_files:
            try:
                pred_dfs.append(pd.read_csv(pf, low_memory=False))
            except Exception as e:
                logger.warning("Failed to load prediction file %s: %s", pf, e)
        
        if pred_dfs:
            preds_all = pd.concat(pred_dfs, ignore_index=True)
            
            # deduplicate predictions to keep the latest one
            preds_all = preds_all.drop_duplicates(
                subset=["match_date", "tour", "tournament", "player_a", "player_b"],
                keep="last"
            )
            
            # Join predictions to warehouse
            # We use a left join to keep all matches, adding predictions where available
            warehouse = warehouse.merge(
                preds_all[['match_date', 'tour', 'tournament', 'player_a', 'player_b', 'predicted_winner', 'prediction_prob', 'source']],
                on=["match_date", "tour", "tournament", "player_a", "player_b"],
                how="left",
                suffixes=('', '_pred')
            )
            # Rename source to clarify it's the predictor source
            if 'source_pred' in warehouse.columns:
                warehouse = warehouse.rename(columns={'source_pred': 'predicted_source'})

    # Final safety: ensure numeric types for odds
    if "odds_a" in warehouse.columns:
        warehouse["odds_a"] = pd.to_numeric(warehouse["odds_a"], errors='coerce')
    if "odds_b" in warehouse.columns:
        warehouse["odds_b"] = pd.to_numeric(warehouse["odds_b"], errors='coerce')

    dest_path = data_path / output_file
    warehouse.to_csv(dest_path, index=False, compression="gzip")
    
    logger.info("Warehouse build successful. Total rows: %d", len(warehouse))
    return dest_path
