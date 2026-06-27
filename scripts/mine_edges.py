#!/usr/bin/env python3
"""
Racket Factory Edge Miner (Ma Golide Enhanced)
Automated combinatorial discovery of Bankers and Robbers, including prediction signals.
"""
import pandas as pd
import argparse
import logging
import sys
from pathlib import Path
from itertools import product

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.assay import assay_segment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("edge_miner")

def get_rank_band(rank: float) -> str:
    if pd.isna(rank): return "Unknown"
    if rank <= 10: return "Top 10"
    if rank <= 50: return "11-50"
    if rank <= 100: return "51-100"
    return "100+"

def get_odds_band(odds: float) -> str:
    if pd.isna(odds): return "Unknown"
    if odds < 1.3: return "1.1-1.3"
    if odds < 1.6: return "1.3-1.6"
    if odds < 2.0: return "1.6-2.0"
    return "2.0+"

def get_confidence_band(prob: float) -> str:
    """Bucket prediction probability into confidence tiers."""
    if pd.isna(prob): return "Unknown"
    if prob >= 0.70: return "High"    # ≥70% confident
    if prob >= 0.60: return "Medium"  # 60–70%
    return "Low"                       # <60%


def get_cross_source_agree(row: pd.Series) -> str:
    """
    Compare Forebet (primary) vs ForeTennis (secondary) predictions.
    Returns one of: Both | Disagree | ForebetOnly | ForeTennisOnly
    """
    fb = row.get("predicted_winner")          # Forebet canonical
    ft = row.get("predicted_winner_foretennis")  # ForeTennis suffixed
    has_fb = pd.notna(fb) and fb != ""
    has_ft = pd.notna(ft) and ft != ""
    if has_fb and has_ft:
        return "Both" if fb == ft else "Disagree"
    if has_fb:
        return "ForebetOnly"
    if has_ft:
        return "ForeTennisOnly"
    return "Unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine the warehouse for automated edges")
    ap.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse")
    ap.add_argument("--min-n", type=int, default=15,
                    help="Minimum matches per slice (default 15)")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.warehouse, low_memory=False)
    except Exception as e:
        logger.error("Could not load warehouse: %s", e)
        return 1

    # 1. Pre-calculate "Bands"
    df['winner_rank_band'] = df['_winner_rank'].apply(get_rank_band)
    df['fav_odds'] = df.apply(
        lambda r: r['odds_a'] if pd.notna(r.get('odds_a')) and pd.notna(r.get('odds_b'))
                  and r['odds_a'] < r['odds_b'] else r.get('odds_b'), axis=1
    )
    df['fav_odds_band'] = df['fav_odds'].apply(get_odds_band)

    # pred_confidence — based on Forebet primary probability
    if 'prediction_prob' in df.columns:
        df['pred_confidence'] = df['prediction_prob'].apply(get_confidence_band)

    # cross_source_agree — Forebet vs ForeTennis agreement
    if 'predicted_winner_foretennis' in df.columns:
        df['cross_source_agree'] = df.apply(get_cross_source_agree, axis=1)
        logger.info("Cross-source agree distribution: %s",
                    df['cross_source_agree'].value_counts().to_dict())
    
    # Define the dimensions we want to "Self-Slice" across
    dimensions = {
        "tour": df['tour'].unique(),
        "_surface": df['_surface'].unique(),
        "fav_odds_band": df['fav_odds_band'].unique(),
        "winner_rank_band": df['winner_rank_band'].unique(),
        "_series": df['_series'].unique(),
    }
    
    # Add prediction dimensions only when that data is present
    if 'predicted_winner' in df.columns:
        dimensions['predicted_winner'] = df['predicted_winner'].unique()
    if 'pred_confidence' in df.columns:
        dimensions['pred_confidence'] = df['pred_confidence'].unique()
    if 'cross_source_agree' in df.columns:
        dimensions['cross_source_agree'] = df['cross_source_agree'].unique()
    
    for k, v in dimensions.items():
        dimensions[k] = [x for x in v if pd.notna(x) and x != "Unknown" and x != ""]

    logger.info("Mining for Bankers and Robbers across %d dimensions...", len(dimensions))
    
    results = []
    all_combinations = list(product(*dimensions.values()))
    dim_names = list(dimensions.keys())

    for combo in all_combinations:
        query = " and ".join([f"{name} == '{val}'" for name, val in zip(dim_names, combo)])
        slice_df = df.query(query)
        
        if len(slice_df) < args.min_n:
            continue
            
        res = assay_segment(slice_df)
        
        if res.grade in ["GOLD", "PLATINUM", "SILVER"] or res.tier == "ROBBER":
            results.append({
                "Slice": " | ".join([f"{n}:{v}" for n, v in zip(dim_names, combo)]),
                "N": res.n,
                "WinRate": f"{res.win_rate:.2%}",
                "Shrunk": f"{res.shrunk_rate:.2%}",
                "ROI": f"{res.roi:.2%}",
                "Grade": res.grade,
                "Tier": res.tier,
                "Verdict": res.verdict
            })

    if not results:
        logger.info("No high-conviction edges found.")
        return 0

    report = pd.DataFrame(results).sort_values("ROI", ascending=False)
    
    print("\n" + "="*120)
    print("🚀 RACKET FACTORY EDGE MINER: SIGNAL INTELLIGENCE MODE")
    print("="*120)
    print(report.to_string(index=False))
    print("="*120 + "\n")
    
    return 0

if __name__ == "__main__":
    main()
