#!/usr/bin/env python3
"""
Racket Factory Edge Miner (Ma Golide Enhanced)
Automated combinatorial discovery of Bankers and Robbers, including prediction signals.

WARNING: ROI is currently calculated using Market Closing Odds. AI predictions captured early in the day must be evaluated against Opening Odds before live capital is deployed.
"""
import pandas as pd
import argparse
import logging
import sys
import json
from datetime import datetime
from pathlib import Path
from itertools import combinations

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
    Compare Market baseline vs ForeTennis AI predictions.
    Returns one of: Both | Disagree | MarketOnly | ForeTennisOnly
    """
    mkt = row.get("predicted_winner_market") 
    ft = row.get("predicted_winner_foretennis")
    has_mkt = pd.notna(mkt) and mkt != ""
    has_ft = pd.notna(ft) and ft != ""
    if has_mkt and has_ft:
        return "Both" if mkt == ft else "Disagree"
    if has_mkt:
        return "MarketOnly"
    if has_ft:
        return "ForeTennisOnly"
    return "Unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine the warehouse for automated edges")
    ap.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse")
    ap.add_argument("--min-n", type=int, default=15,
                    help="Minimum matches per slice (default 15)")
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD to extract specific picks (default: today)")
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
    if 'predicted_winner_foretennis' in df.columns:
        dimensions['predicted_winner_foretennis'] = df['predicted_winner_foretennis'].unique()
    if 'pred_confidence' in df.columns:
        dimensions['pred_confidence'] = df['pred_confidence'].unique()
    if 'cross_source_agree' in df.columns:
        dimensions['cross_source_agree'] = df['cross_source_agree'].unique()
    
    for k, v in dimensions.items():
        dimensions[k] = [x for x in v if pd.notna(x) and x != "Unknown" and x != ""]

    logger.info("Mining for Bankers and Robbers across %d dimensions...", len(dimensions))

    results = []
    dim_names = list(dimensions.keys())

    # Mine across lower-dimensional combinations first so live rows have a much
    # better chance of matching a historically profitable slice.
    min_dims = 3
    max_dims = min(5, len(dim_names))
    logger.info("Evaluating dimension combinations from %dD to %dD...", min_dims, max_dims)

    seen_signatures = set()
    for r in range(min_dims, max_dims + 1):
        for subset in combinations(dim_names, r):
            subset = list(subset)
            subset_df = df.copy()
            for d in subset:
                subset_df = subset_df[~subset_df[d].isin(["Unknown", ""])]
                subset_df = subset_df.dropna(subset=[d])

            if subset_df.empty:
                continue

            for combo, slice_df in subset_df.groupby(subset):
                if not isinstance(combo, tuple):
                    combo = (combo,)
                if len(slice_df) < args.min_n:
                    continue

                res = assay_segment(slice_df)
                if res.grade not in ["GOLD", "PLATINUM", "SILVER"] and res.tier != "ROBBER":
                    continue

                combo_dict = dict(zip(subset, combo))
                signature = tuple(sorted(combo_dict.items()))
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)

                results.append({
                    "Slice": " | ".join([f"{n}:{v}" for n, v in combo_dict.items()]),
                    "Combo_Dict": combo_dict,
                    "Dims": len(combo_dict),
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

    report = pd.DataFrame(results)
    report["ROI_num"] = report["ROI"].str.rstrip('%').astype(float)
    report = report.sort_values(["Verdict", "ROI_num", "N", "Dims"], ascending=[True, False, False, True])
    
    print("\n" + "="*120)
    print("🚀 RACKET FACTORY EDGE MINER: SIGNAL INTELLIGENCE MODE")
    print("="*120)
    print(report.drop(columns=["Combo_Dict", "ROI_num"]).to_string(index=False))
    print("="*120 + "\n")
    
    # 2. Extract Specific Picks for the Target Date
    target_date = args.date or datetime.now().strftime("%Y-%m-%d")
    today_df = df[df["match_date"] == target_date]
    
    picks_to_export = []
    
    if not today_df.empty:
        for _, row in today_df.iterrows():
            best_pick = None
            best_roi = -999.0
            
            for res in results:
                combo = res["Combo_Dict"]
                # Check if the row matches all dimensions of the slice
                match_all = True
                for dim_name, dim_val in combo.items():
                    if row.get(dim_name) != dim_val:
                        match_all = False
                        break

                if match_all:
                    # Prefer stronger verdicts, then higher dimensional specificity,
                    # then better ROI.
                    slice_roi = float(res["ROI"].strip('%')) / 100.0
                    verdict_rank = {"EDGE CONFIRMED": 3, "WATCHLIST": 2, "FADE THIS SIGNAL": 1}.get(res["Verdict"], 0)
                    best_verdict_rank = -1 if best_pick is None else {"EDGE CONFIRMED": 3, "WATCHLIST": 2, "FADE THIS SIGNAL": 1}.get(best_pick["Verdict"], 0)
                    best_dims = -1 if best_pick is None else int(best_pick.get("Dims", 0))
                    cur_dims = int(res.get("Dims", 0))
                    if (
                        best_pick is None
                        or verdict_rank > best_verdict_rank
                        or (verdict_rank == best_verdict_rank and cur_dims > best_dims)
                        or (verdict_rank == best_verdict_rank and cur_dims == best_dims and slice_roi > best_roi)
                    ):
                        best_roi = slice_roi
                        best_pick = res
            
            if best_pick:
                # Format for the WhatsApp Notifier
                is_robber = best_pick["Tier"] == "ROBBER"
                bucket = "CERTIFIED_CLEAN" if best_pick["Verdict"] == "EDGE CONFIRMED" else ("WATCHLIST" if best_pick["Verdict"] == "WATCHLIST" else "CAUTION")
                prob = row.get("prediction_prob")
                if pd.isna(prob):
                    prob = None
                
                picks_to_export.append({
                    "match": f"{row.get('player_a', 'A')} vs {row.get('player_b', 'B')}",
                    "date": str(row.get("match_date", target_date)),
                    "bucket": bucket,
                    "pick": best_pick["Verdict"],
                    "odds": row.get("fav_odds"),
                    "confidence": prob,
                    "slice_matched": best_pick["Slice"]
                })
                
    picks_file = Path(f"localdata/picks_{target_date}.json")
    picks_file.parent.mkdir(parents=True, exist_ok=True)
    picks_file.write_text(json.dumps(picks_to_export, indent=2))
    logger.info("Exported %d actionable picks to %s", len(picks_to_export), picks_file)
    
    return 0

if __name__ == "__main__":
    main()
