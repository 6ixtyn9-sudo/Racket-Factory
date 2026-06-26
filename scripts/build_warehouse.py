#!/usr/bin/env python3
"""Build the unified Racket Factory warehouse from local CSV data."""
import argparse
import logging
import sys
from pathlib import Path

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.warehouse import build_warehouse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("build_warehouse")

def main() -> int:
    ap = argparse.ArgumentParser(description="Build the unified Racket Factory warehouse")
    ap.add_argument("--data-dir", default=str(ROOT / "localdata"), help="Directory containing CSV data")
    ap.add_argument("--output", default="warehouse.csv.gz", help="Output warehouse filename")
    args = ap.parse_args()

    logger.info("Building warehouse from %s...", args.data_dir)
    build_warehouse(data_dir=args.data_dir, output_file=args.output)
    logger.info("Warehouse build complete.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
