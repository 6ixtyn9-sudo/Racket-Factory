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


def _winner_from_actual_result(actual_result: object) -> str | None:
    """Infer winner side from ForeTennis actual_result like '20', '02', '21', '12', '30', '23'."""
    text = str(actual_result or "").strip()
    digits = [int(ch) for ch in text if ch.isdigit()]
    if len(digits) < 2:
        return None
    home_sets, away_sets = digits[0], digits[1]
    if home_sets == away_sets:
        return None
    return "player_a" if home_sets > away_sets else "player_b"


def _result_rows_from_foretennis(df):
    """Build warehouse-compatible settled result rows from ForeTennis daily output."""
    import pandas as pd
    from datetime import datetime

    if df.empty or "actual_result" not in df.columns:
        return pd.DataFrame()

    rows = []
    for _, r in df.iterrows():
        side = _winner_from_actual_result(r.get("actual_result"))
        if side not in {"player_a", "player_b"}:
            continue

        player_a = str(r.get("player_a") or "").strip()
        player_b = str(r.get("player_b") or "").strip()
        if not player_a or not player_b:
            continue

        tournament = str(r.get("tournament") or "")
        tour = str(r.get("tour") or "UNKNOWN").strip() or "UNKNOWN"
        winner = player_a if side == "player_a" else player_b

        rows.append({
            "match_date": str(r.get("match_date") or "")[:10],
            "tour": tour,
            "tournament": tournament,
            "round": "",
            "player_a": player_a,
            "player_b": player_b,
            "winner": winner,
            "score": str(r.get("actual_result") or "").strip(),
            "odds_a": pd.NA,
            "odds_b": pd.NA,
            "bookmaker": "",
            "source": "ForeTennis_results",
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "oddsportal_url": "",
            "_surface": "",
            "_court": "",
            "_series": "",
            "_comment": "result_from_foretennis_actual_result",
            "_location": "",
            "_winner_rank": pd.NA,
            "_loser_rank": pd.NA,
            "_odds_source": "",
            "_is_live": False,
            "_foretennis_match_id": r.get("match_id"),
            "_score_perspective": "player_a_sets-player_b_sets",
        })

    return pd.DataFrame(rows)


def _write_result_rows(result_df, output_dir):
    """Append/dedupe ForeTennis result rows into monthly warehouse-compatible files."""
    import pandas as pd
    from pathlib import Path

    if result_df is None or result_df.empty:
        return []

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result_df = result_df.copy()
    result_df["match_date"] = result_df["match_date"].astype(str).str[:10]

    written = []
    for month, group in result_df.groupby(result_df["match_date"].str[:7]):
        path = out_dir / f"foretennis_results_tennis_{month}.csv.gz"
        if path.exists():
            old = pd.read_csv(path, low_memory=False)
            combined = pd.concat([old, group], ignore_index=True, sort=False)
        else:
            combined = group.copy()

        combined = combined.drop_duplicates(
            subset=["match_date", "tour", "tournament", "player_a", "player_b"],
            keep="last",
        )
        combined.to_csv(path, index=False, compression="gzip")
        written.append(path)

    return written

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
                        "actual_result": p.get("actual_result"),
                        "prediction_correct": p.get("prediction_correct"),
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
                            "actual_result": p.get("actual_result"),
                            "prediction_correct": p.get("prediction_correct"),
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

    # ForeTennis daily/lastpredictions carries actual_result values such as
    # "20", "02", "21", "12", "30", "23". Convert those into settled,
    # warehouse-compatible result rows so audit can settle from source results.
    result_df = _result_rows_from_foretennis(df_preds)
    written_results = _write_result_rows(result_df, out_dir)
    for result_path in written_results:
        logger.info("Saved %d ForeTennis result rows to %s", len(result_df), result_path)

if __name__ == "__main__":
    main()
