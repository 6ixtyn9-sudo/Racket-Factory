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
    
    # 2. Multi-Source Prediction Join
    # Primary source writes to canonical columns (predicted_winner, prediction_prob,
    # predicted_source). Each additional source gets suffixed columns so sources
    # never silently overwrite each other based on glob order.
    #
    # Source priority: Forebet is primary (most historical coverage). Any source
    # not in PRIMARY_SOURCES is merged as a secondary with a suffix derived from
    # its 'source' column value (lowercased, spaces→underscore).
    PRIMARY_SOURCES = {"Forebet"}
    JOIN_KEYS = ["match_date", "tour", "tournament", "player_a", "player_b"]

    pred_files = list(data_path.glob("predictions_*.csv.gz"))
    if pred_files:
        logger.info("Loading %d prediction files for multi-source merge...", len(pred_files))

        # Load and bucket by source name
        source_dfs: dict[str, list[pd.DataFrame]] = {}
        for pf in pred_files:
            try:
                pdf = pd.read_csv(pf, low_memory=False)
                if pdf.empty or "source" not in pdf.columns:
                    continue
                src = pdf["source"].iloc[0]
                source_dfs.setdefault(src, []).append(pdf)
            except Exception as e:
                logger.warning("Failed to load prediction file %s: %s", pf, e)

        for src, frames in source_dfs.items():
            merged = pd.concat(frames, ignore_index=True)
            # Deduplicate within this source: keep most recent scrape
            merged = merged.drop_duplicates(
                subset=JOIN_KEYS, keep="last"
            )
            pred_cols = [c for c in ["predicted_winner", "prediction_prob", "source"]
                         if c in merged.columns]

            if src in PRIMARY_SOURCES:
                # Primary source → canonical column names
                logger.info("Merging primary source '%s': %d predictions", src, len(merged))
                warehouse = warehouse.merge(
                    merged[JOIN_KEYS + pred_cols],
                    on=JOIN_KEYS,
                    how="left",
                    suffixes=('', '_pred'),
                )
                if 'source_pred' in warehouse.columns:
                    warehouse = warehouse.rename(columns={'source_pred': 'predicted_source'})
            else:
                # Secondary source → suffixed column names
                suffix = src.lower().replace(" ", "_").replace("-", "_")
                logger.info("Merging secondary source '%s' (suffix: _%s): %d predictions",
                            src, suffix, len(merged))
                rename_map = {c: f"{c}_{suffix}" for c in pred_cols if c != "source"}
                merged_renamed = merged[JOIN_KEYS + pred_cols].rename(columns=rename_map)
                # Drop 'source' col — it's redundant given the suffix encodes it
                if "source" in merged_renamed.columns:
                    merged_renamed = merged_renamed.drop(columns=["source"])
                warehouse = warehouse.merge(
                    merged_renamed,
                    on=JOIN_KEYS,
                    how="left",
                )

        # Report final prediction column coverage
        pred_winner_cols = [c for c in warehouse.columns if c.startswith("predicted_winner")]
        logger.info("Prediction columns in warehouse: %s", pred_winner_cols)
        primary_cov = warehouse["predicted_winner"].notna().sum() if "predicted_winner" in warehouse.columns else 0
        logger.info("Primary (Forebet) prediction coverage: %d/%d rows (%.1f%%)",
                    primary_cov, len(warehouse), 100 * primary_cov / max(len(warehouse), 1))

    # Final safety: ensure numeric types for odds
    if "odds_a" in warehouse.columns:
        warehouse["odds_a"] = pd.to_numeric(warehouse["odds_a"], errors='coerce')
    if "odds_b" in warehouse.columns:
        warehouse["odds_b"] = pd.to_numeric(warehouse["odds_b"], errors='coerce')

    dest_path = data_path / output_file
    warehouse.to_csv(dest_path, index=False, compression="gzip")
    
    logger.info("Warehouse build successful. Total rows: %d", len(warehouse))
    return dest_path
