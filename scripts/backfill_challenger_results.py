#!/usr/bin/env python3
"""
Backfill daily TennisExplorer results, including Challenger events.

Writes warehouse-compatible CSVs only for recognised completed matches.

Usage:
    PYTHONPATH=src python3 scripts/backfill_challenger_results.py --days 3 --output-dir localdata
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import date, timedelta
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("backfill_challenger")

from racketfactory.sources.tennisexplorer import parse_daily, fetch_daily


def parse_tennisexplorer_results(html, target_date):
    return parse_daily(html, target_date, completed=True)


def fetch_tennisexplorer_results(target_date):
    try:
        return fetch_daily(target_date, completed=True)
    except Exception as exc:
        logger.warning("TennisExplorer fetch failed for %s: %s", target_date, type(exc).__name__)
        return []


def write_result_rows(rows: list[dict], output_dir: Path):
    if not rows:
        return []
    df=pd.DataFrame(rows)
    # Add warehouse-compatible columns
    from datetime import datetime
    df["round"]=""
    # Keep reference prices for coverage/CLV, not retroactive pick pricing.
    if "odds_a" not in df: df["odds_a"] = pd.NA
    if "odds_b" not in df: df["odds_b"] = pd.NA
    df["bookmaker"]=""
    df["source"]="TennisExplorer_results"
    if "captured_at" not in df: df["captured_at"] = datetime.now().isoformat(timespec="seconds")
    df["oddsportal_url"]=""
    df["_surface"]=""
    df["_court"]=""
    df["_series"]=""
    df["_comment"]="result_from_challenger_backfill"
    df["_location"]=""
    df["_winner_rank"]=pd.NA
    df["_loser_rank"]=pd.NA
    df["_odds_source"]=""
    df["_is_live"]=False
    df["_score_perspective"]="player_a_sets-player_b_sets"

    df["match_date"]=df["match_date"].astype(str).str[:10]
    written=[]
    for month, group in df.groupby(df["match_date"].str[:7]):
        path=output_dir / f"challenger_results_tennis_{month}.csv.gz"
        if path.exists():
            old=pd.read_csv(path, low_memory=False)
            combined=pd.concat([old, group], ignore_index=True, sort=False)
        else:
            combined=group.copy()
        combined=combined.drop_duplicates(subset=["match_date","tour","tournament","player_a","player_b"], keep="last")
        combined.to_csv(path, index=False, compression="gzip")
        written.append(path)
        logger.info(f"Wrote {len(group)} challenger results to {path}")
    return written

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="Days back from today to fetch")
    ap.add_argument("--output-dir", default="localdata")
    ap.add_argument("--date", help="Specific date YYYY-MM-DD")
    ap.add_argument("--include-today", action="store_true")
    ap.add_argument("--fixtures-date", help="Also capture scheduled match odds for this date")
    args=ap.parse_args()

    out_dir=Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dates=[]
    if args.date:
        dates=[args.date]
    else:
        today=date.today()
        if args.include_today:
            dates.append(today.isoformat())
        for i in range(args.days):
            dates.append((today - timedelta(days=i+1)).isoformat())  # yesterday and before

    if args.fixtures_date:
        import json
        try:
            fixtures = fetch_daily(args.fixtures_date, completed=False)
            (out_dir / f"tennisexplorer_odds_{args.fixtures_date}.json").write_text(json.dumps(fixtures, indent=2))
        except Exception as exc:
            logger.warning("TennisExplorer odds capture failed: %s", type(exc).__name__)

    import json
    all_rows=[]
    diagnostics=[]
    for d in dates:
        try:
            rows=fetch_daily(d, completed=True)
            diagnostics.append(dict(date=d, rows=len(rows), status="ok" if rows else "no_parsed_results"))
            all_rows.extend(rows)
        except Exception as exc:
            diagnostics.append(dict(date=d, rows=0, status=type(exc).__name__))
            logger.warning("TennisExplorer %s failed: %s", d, type(exc).__name__)
    (out_dir / "source_capture_tennisexplorer.json").write_text(json.dumps(diagnostics, indent=2))

    if all_rows:
        write_result_rows(all_rows, out_dir)
        logger.info(f"Total {len(all_rows)} challenger results written")
    else:
        logger.warning("No challenger results found")

if __name__=="__main__":
    main()
