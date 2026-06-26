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

from racketfactory.sources.tennisdata import fetch_and_normalize_years

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
    args = ap.parse_args()

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
