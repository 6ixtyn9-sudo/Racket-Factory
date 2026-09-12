#!/usr/bin/env python3
"""Download [tennis-data.co.uk](http://tennis-data.co.uk) yearly Excel files and normalize into Racket Factory CSVs.

Usage:
    PYTHONPATH=src python3 scripts/download_tennisdata.py --year 2020 2021 2022 2023 2024 2025
    PYTHONPATH=src python3 scripts/download_tennisdata.py --year 2025 --tour ATP
    PYTHONPATH=src python3 scripts/download_tennisdata.py --all

Output:
    Writes localdata/tennisdata_tennis_YYYY-MM.csv.gz files compatible with
    build_warehouse.py. Pinnacle odds used as primary; Bet365 as fallback.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure src is in path for imports if run as script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.tennisdata import (
    fetch_and_normalize_years,
    repair_monthly_scores,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("download_tennisdata")

# [tennis-data.co.uk](http://tennis-data.co.uk) ATP starts 2000, WTA starts 2007
ATP_YEARS = list(range(2000, datetime.now().year + 1))
WTA_YEARS = list(range(2007, datetime.now().year + 1))

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Download [tennis-data.co.uk](http://tennis-data.co.uk) data and normalize to Racket Factory CSVs"
    )
    ap.add_argument("--year", type=int, nargs="+", help="Year(s) to download")
    ap.add_argument("--tour", nargs="+", default=["ATP", "WTA"],
                    choices=["ATP", "WTA"], help="Tour(s) to download")
    ap.add_argument("--all", action="store_true",
                    help="Download all available years (ATP 2000+, WTA 2007+)")
    ap.add_argument("--data-dir", default=str(ROOT / "localdata" / "tennisdata"),
                    help="Cache directory for downloaded Excel files")
    ap.add_argument("--output-dir", default=str(ROOT / "localdata"),
                    help="Output directory for normalized CSV files")
    ap.add_argument("--force", action="store_true",
                    help="Re-download yearly Excel files even when cached")
    ap.add_argument("--max-age-hours", type=float, default=24.0,
                    help="Refetch the current-year file when the cache is older than this (default 24h)")
    ap.add_argument("--repair-local", action="store_true",
                    help="Flip winner-first scores in existing local monthly files to player_a-first and exit")
    args = ap.parse_args()

    if args.repair_local:
        out_dir = Path(args.output_dir)
        total_rows = total_flipped = total_skipped = n_files = 0
        for path in sorted(out_dir.glob("tennisdata_tennis_*.csv.gz")):
            stats = repair_monthly_scores(path)
            n_files += 1
            total_rows += stats["rows"]
            total_flipped += stats["flipped"]
            total_skipped += stats["skipped_tokens"]
            logger.info("%s: %d rows, %d flipped, %d stripped 0-0, %d skipped tokens",
                        path.name, stats["rows"], stats["flipped"],
                        stats.get("stripped_00", 0), stats["skipped_tokens"])
        logger.info("Repair done: %d files, %d rows, %d flipped, %d skipped tokens",
                    n_files, total_rows, total_flipped, total_skipped)
        return 0

    if args.all:
        years = []
        if "ATP" in args.tour:
            years.extend(ATP_YEARS)
        if "WTA" in args.tour:
            years.extend(WTA_YEARS)
        years = sorted(set(years))
    elif args.year:
        years = args.year
    else:
        ap.error("Provide --year or --all")

    total = fetch_and_normalize_years(
        years=years,
        tours=args.tour,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        force=args.force,
        max_age_hours=args.max_age_hours,
    )
    logger.info("Done. Total normalized rows: %d", total)
    if total == 0:
        logger.warning(
            "No rows downloaded. Check network connectivity to [tennis-data.co.uk](http://tennis-data.co.uk)."
        )
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
