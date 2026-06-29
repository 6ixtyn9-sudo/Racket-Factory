#!/usr/bin/env python3
"""
Backfill Forebet Predictions

Two modes of operation:

1.  --mode tournament (default)
    Reads the warehouse, groups by tournament, and fetches each tournament page once.
    Best for historical backfill where you have years of match data.

2.  --mode daily
    Fetches predictions-yesterday / predictions-today / predictions-tomorrow.
    One page = all matches across all tournaments.  Best for ongoing daily capture.

Usage examples:
    # Historical backfill (slow, thorough)
    PYTHONPATH=src python3 scripts/backfill_forebet.py --mode tournament

    # Daily capture (fast, 3 pages total)
    PYTHONPATH=src python3 scripts/backfill_forebet.py --mode daily --days yesterday today tomorrow

    # Test on a small subset
    PYTHONPATH=src python3 scripts/backfill_forebet.py --mode tournament --limit 5
"""
import pandas as pd
import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional
from collections import defaultdict

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.forebet import ForebetPredictor, name_signature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_forebet")


def _write_predictions(predictions: list[dict], output_dir: Path) -> None:
    if not predictions:
        logger.warning("No predictions to write.")
        return
    out_df = pd.DataFrame(predictions)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_month = out_df.groupby(out_df["match_date"].astype(str).str[:7])
    for month, group in by_month:
        path = out_dir / f"predictions_forebet_{month}.csv.gz"
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            group = pd.concat([existing, group], ignore_index=True)
            group = group.drop_duplicates(
                subset=["match_date", "tour", "tournament", "player_a", "player_b"],
                keep="last",
            )
        group.to_csv(path, index=False, compression="gzip")
        logger.info("Wrote %d predictions to %s", len(group), path)


def mode_tournament(args) -> int:
    """Historical backfill using tournament pages."""
    try:
        df = pd.read_csv(args.warehouse, low_memory=False)
    except Exception as e:
        logger.error("Could not load warehouse: %s", e)
        return 1

    if df.empty:
        logger.error("Warehouse is empty.")
        return 1

    required = ["match_date", "tour", "tournament", "player_a", "player_b"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error("Warehouse missing required columns: %s", missing)
        return 1

    groups = df.groupby(["tour", "tournament"])
    tournament_list = list(groups.groups.keys())
    logger.info(
        "Tournament mode: %d tournaments, %d matches in warehouse.",
        len(tournament_list), len(df),
    )

    if args.limit:
        tournament_list = tournament_list[: args.limit]
        logger.info("Limiting to %d tournaments.", args.limit)

    predictor = ForebetPredictor()
    predictions: list[dict] = []
    matched_count = 0

    for i, (tour, tournament) in enumerate(tournament_list): # type: ignore
        group_df = groups.get_group((tour, tournament))
        logger.info(
            "[%3d/%d] %s / %s — %d warehouse matches",
            i + 1, len(tournament_list), tour, tournament, len(group_df),
        )

        preds = predictor.fetch_tournament_predictions(tour, tournament)
        if not preds:
            time.sleep(args.delay)
            continue

        # Index by (date, sorted name-signature pair)
        pred_index: dict[tuple[str, tuple[str, str]], dict] = {}
        for p in preds:
            if not p.get("match_date"):
                continue
            h = name_signature(p["player_home"])
            a = name_signature(p["player_away"])
            key = (min(h, a), max(h, a))
            pred_index[(p["match_date"], key)] = p

        for _, row in group_df.iterrows():
            match_date = str(row["match_date"])
            sig_a = name_signature(row["player_a"])
            sig_b = name_signature(row["player_b"])
            key = (min(sig_a, sig_b), max(sig_a, sig_b))

            pred = pred_index.get((match_date, key))
            if not pred:
                continue

            mapped = predictor.map_prediction_to_player(pred, row["player_a"], row["player_b"])
            if not mapped:
                continue

            predictions.append({
                "match_date": match_date,
                "tour": row["tour"],
                "tournament": row["tournament"],
                "player_a": row["player_a"],
                "player_b": row["player_b"],
                "predicted_winner": mapped["predicted_winner"],
                "prediction_prob": mapped["prediction_prob"],
                "odds_a": pred.get("odds_home") if mapped["predicted_winner"] == "player_a" else pred.get("odds_away"),
                "odds_b": pred.get("odds_away") if mapped["predicted_winner"] == "player_a" else pred.get("odds_home"),
                "source": "Forebet",
            })
            matched_count += 1

        if (i + 1) % 10 == 0:
            logger.info(
                "Progress: %d/%d tournaments, %d matched so far.",
                i + 1, len(tournament_list), matched_count,
            )
        time.sleep(args.delay)

    logger.info("Tournament mode complete: %d matched predictions.", matched_count)
    _write_predictions(predictions, args.output_dir)
    return 0



def _map_forebet_result_side(p: dict, player_a: str, player_b: str) -> str | None:
    """Map Forebet result_winner ('1'/'2' home/away) to warehouse player_a/player_b."""
    side = str(p.get("result_winner") or "").strip()
    if side not in {"1", "2"}:
        return None

    home_sig = name_signature(str(p.get("player_home") or ""))
    away_sig = name_signature(str(p.get("player_away") or ""))
    a_sig = name_signature(str(player_a or ""))
    b_sig = name_signature(str(player_b or ""))

    if side == "1":
        if home_sig == a_sig:
            return "player_a"
        if home_sig == b_sig:
            return "player_b"
    if side == "2":
        if away_sig == a_sig:
            return "player_a"
        if away_sig == b_sig:
            return "player_b"

    # Most unmatched rows are stored player_a=home, player_b=away.
    return "player_a" if side == "1" else "player_b"


def _copy_forebet_result_fields(out: dict, p: dict, player_a: str, player_b: str) -> dict:
    """Attach Forebet result fields to a prediction row."""
    result_side = _map_forebet_result_side(p, player_a, player_b)
    out["result_status"] = p.get("result_status")
    out["result_score"] = p.get("result_score")
    out["result_winner"] = result_side
    out["result_winner_name"] = (
        player_a if result_side == "player_a"
        else player_b if result_side == "player_b"
        else p.get("result_winner_name")
    )
    out["result_sets_home"] = p.get("result_sets_home")
    out["result_sets_away"] = p.get("result_sets_away")
    return out


def _forebet_result_rows_from_predictions(predictions: list[dict]) -> pd.DataFrame:
    """Build warehouse-compatible settled result rows from Forebet parsed results."""
    from datetime import datetime

    rows = []
    for p in predictions:
        side = str(p.get("result_winner") or "").strip()
        if side not in {"player_a", "player_b"}:
            continue

        player_a = str(p.get("player_a") or "").strip()
        player_b = str(p.get("player_b") or "").strip()
        if not player_a or not player_b:
            continue

        winner = player_a if side == "player_a" else player_b
        match_date = str(p.get("match_date") or "")[:10]
        if not match_date:
            continue

        rows.append({
            "match_date": match_date,
            "tour": p.get("tour") or "",
            "tournament": p.get("tournament") or "",
            "round": "",
            "player_a": player_a,
            "player_b": player_b,
            "winner": winner,
            "score": p.get("result_score") or "",
            "odds_a": p.get("odds_a"),
            "odds_b": p.get("odds_b"),
            "bookmaker": "Forebet",
            "source": "Forebet_results",
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "oddsportal_url": "",
            "_surface": "",
            "_court": "",
            "_series": "",
            "_comment": "result_from_forebet_yesterday",
            "_location": "",
            "_winner_rank": pd.NA,
            "_loser_rank": pd.NA,
            "_odds_source": "Forebet",
            "_is_live": False,
            "_score_perspective": "player_a_games-player_b_games",
            "_result_sets_home": p.get("result_sets_home"),
            "_result_sets_away": p.get("result_sets_away"),
        })

    return pd.DataFrame(rows)


def _write_forebet_result_rows(predictions: list[dict], output_dir: Path) -> list[Path]:
    result_df = _forebet_result_rows_from_predictions(predictions)
    if result_df.empty:
        return []

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    result_df["match_date"] = result_df["match_date"].astype(str).str[:10]
    for month, group in result_df.groupby(result_df["match_date"].str[:7]):
        path = out_dir / f"forebet_results_tennis_{month}.csv.gz"
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

def mode_daily(args) -> int:
    """Daily capture using predictions-yesterday / today / tomorrow pages."""
    predictor = ForebetPredictor()
    predictions: list[dict] = []

    warehouse_df = None
    if args.warehouse and Path(args.warehouse).exists():
        try:
            warehouse_df = pd.read_csv(args.warehouse, low_memory=False)
        except Exception as e:
            logger.warning("Could not read warehouse for matching: %s", e)

    for day in args.days:
        logger.info("Fetching predictions-%s ...", day)
        preds = predictor.fetch_daily_predictions(day)
        if not preds:
            logger.warning("No predictions returned for %s.", day)
            continue

        logger.info("predictions-%s: %d raw matches parsed.", day, len(preds))

        if warehouse_df is not None and not warehouse_df.empty:
            matched = 0
            unmatched = 0
            for p in preds:
                match_date = p.get("match_date")
                if not match_date:
                    continue
                h = name_signature(p["player_home"])
                a = name_signature(p["player_away"])
                key = tuple(sorted([h, a]))

                w_rows = warehouse_df[warehouse_df["match_date"].astype(str) == str(match_date)]
                found = False
                for _, row in w_rows.iterrows():
                    sig_a = name_signature(row["player_a"])
                    sig_b = name_signature(row["player_b"])
                    w_key = tuple(sorted([sig_a, sig_b]))
                    if key == w_key:
                        mapped = predictor.map_prediction_to_player(p, row["player_a"], row["player_b"])
                        if mapped:
                            row_out = {
                                "match_date": match_date,
                                "tour": row["tour"],
                                "tournament": row["tournament"],
                                "player_a": row["player_a"],
                                "player_b": row["player_b"],
                                "predicted_winner": mapped["predicted_winner"],
                                "prediction_prob": mapped["prediction_prob"],
                                "odds_a": p.get("odds_home") if mapped["predicted_winner"] == "player_a" else p.get("odds_away"),
                                "odds_b": p.get("odds_away") if mapped["predicted_winner"] == "player_a" else p.get("odds_home"),
                                "source": "Forebet",
                            }
                            predictions.append(_copy_forebet_result_fields(row_out, p, row["player_a"], row["player_b"]))
                            matched += 1
                            found = True
                            break
                if not found:
                    row_out = {
                        "match_date": match_date,
                        "tour": "",
                        "tournament": p.get("tournament", "Unknown"),
                        "player_a": p["player_home"],
                        "player_b": p["player_away"],
                        "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                        "prediction_prob": (float(p["prob_home"]) if p.get("predicted_winner") == "1" else float(p["prob_away"])) / 100 if p.get("prob_home") is not None and p.get("prob_away") is not None else None,
                        "odds_a": p.get("odds_home"),
                        "odds_b": p.get("odds_away"),
                        "source": "Forebet",
                    }
                    predictions.append(_copy_forebet_result_fields(row_out, p, p["player_home"], p["player_away"]))
                    unmatched += 1

            logger.info("predictions-%s: %d matched to warehouse, %d stored as new upcoming matches.", day, matched, unmatched)
        else:
            for p in preds:
                row_out = {
                    "match_date": p["match_date"],
                    "tour": "",
                    "tournament": p.get("tournament", "Unknown"),
                    "player_a": p["player_home"],
                    "player_b": p["player_away"],
                    "predicted_winner": "player_a" if p.get("predicted_winner") == "1" else "player_b",
                    "prediction_prob": (float(p["prob_home"]) if p.get("predicted_winner") == "1" else float(p["prob_away"])) / 100 if p.get("prob_home") is not None and p.get("prob_away") is not None else None,
                    "odds_a": p.get("odds_home"),
                    "odds_b": p.get("odds_away"),
                    "source": "Forebet",
                }
                predictions.append(_copy_forebet_result_fields(row_out, p, p["player_home"], p["player_away"]))
            logger.info("predictions-%s: %d raw predictions stored (no warehouse match).", day, len(preds))

        time.sleep(args.delay)

    _write_predictions(predictions, args.output_dir)

    written_results = _write_forebet_result_rows(predictions, Path(args.output_dir))
    for result_path in written_results:
        logger.info("Wrote Forebet result rows to %s", result_path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill predictions from Forebet")
    ap.add_argument("--mode", choices=["tournament", "daily"], default="tournament",
                    help="tournament = historical backfill per tournament page; "
                         "daily = fast capture from predictions-yesterday/today/tomorrow pages")
    ap.add_argument("--warehouse", default=str(ROOT / "localdata" / "warehouse.csv.gz"),
                    help="Path to warehouse")
    ap.add_argument("--output-dir", default=str(ROOT / "localdata"),
                    help="Output directory for predictions CSVs")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit number of tournaments (tournament mode only)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds between requests")
    ap.add_argument("--days", nargs="+", default=["yesterday"],
                    choices=["yesterday", "today", "tomorrow"],
                    help="Which daily pages to fetch (daily mode only)")
    args = ap.parse_args()

    if args.mode == "daily":
        return mode_daily(args)
    else:
        return mode_tournament(args)


if __name__ == "__main__":
    raise SystemExit(main())