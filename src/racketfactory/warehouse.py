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
    Merge all tennis data sources into a unified warehouse.
    
    Args:
        data_dir: Directory containing source CSVs.
        output_file: The filename to write the final warehouse to.
        
    Returns:
        Path to the created warehouse file, or None if failed.
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error("Data directory not found: %s", data_path)
        return None

    all_files = list(data_path.glob("*.csv.gz"))
    
    dfs = []
    for f in all_files:
        # We only take files intended for the warehouse (containing 'tennis')
        if "tennis" in f.name:
            try:
                logger.info("Loading %s into warehouse...", f.name)
                temp_df = pd.read_csv(f, low_memory=False)
                if not temp_df.empty:
                    dfs.append(temp_df)
            except Exception as e:
                logger.error("Failed to load %s: %s", f.name, e)
    
    if not dfs:
        logger.error("No valid data files found to build warehouse.")
        return None
    
    try:
        warehouse = pd.concat(dfs, ignore_index=True)
        
        # Explicit Column Cleanup
        # Ensure essential columns are present; fill NaNs for critical keys
        critical_cols = ["match_date", "tour", "tournament", "player_a", "player_b"]
        for col in critical_cols:
            if col not in warehouse.columns:
                logger.warning("Warehouse missing critical column: %s. Creating empty column.", col)
                warehouse[col] = ""

        # Deduplication: The 'Holy Grail' key for tennis match identification.
        # We use player keys (alphanumeric, case-insensitive) instead of display names
        # to ensure 'Roger Federer' and 'ROGER FEDERER' are treated as the same person.
        warehouse['p_a_key'] = warehouse['player_a'].apply(player_key)
        warehouse['p_b_key'] = warehouse['player_b'].apply(player_key)
        
        initial_len = len(warehouse)
        warehouse = warehouse.drop_duplicates(
            subset=["match_date", "tour", "tournament", "p_a_key", "p_b_key"], 
            keep="last"
        ).drop(columns=['p_a_key', 'p_b_key'])
        
        # Final safety: ensure numeric types for odds
        if "odds_a" in warehouse.columns:
            warehouse["odds_a"] = pd.to_numeric(warehouse["odds_a"], errors='coerce')
        if "odds_b" in warehouse.columns:
            warehouse["odds_b"] = pd.to_numeric(warehouse["odds_b"], errors='coerce')

        dest_path = data_path / output_file
        warehouse.to_csv(dest_path, index=False, compression="gzip")
        
        logger.info("Warehouse build successful. %d -> %d rows.", initial_len, len(warehouse))
        return dest_path

    except Exception as e:
        logger.exception("Unexpected error during warehouse build: %s", e)
        return None
