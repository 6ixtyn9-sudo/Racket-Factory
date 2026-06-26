#!/usr/bin/env python3
"""
Racket Factory Daily Orchestrator
Standardizes the daily pipeline: Data Capture -> Warehouse -> Assay -> Archive.
"""
import subprocess
import logging
from pathlib import Path
from datetime import datetime
import sys
import os

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("daily")

def run_cmd(cmd: list[str], step_name: str) -> bool:
    logger.info("Starting step: %s", step_name)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode == 0:
            logger.info("Step %s completed successfully.", step_name)
            if result.stdout:
                print(result.stdout)
            return True
        else:
            logger.error("Step %s failed with exit code %d.", step_name, result.returncode)
            logger.error("Error Output: %s", result.stderr)
            return False
    except Exception as e:
        logger.exception("Exception occurred during step %s: %s", step_name, e)
        return False

def main():
    logger.info("--- RACKET FACTORY DAILY PIPELINE START ---")
    
    # 1. Data Capture (Update current year)
    year = datetime.now().year
    if not run_cmd(["python3", "scripts/backfill_tennisdata.py", "--year", str(year)], "Data Capture"):
        logger.error("Pipeline aborted at Data Capture.")
        sys.exit(1)

    # 2. Build Warehouse
    if not run_cmd(["python3", "scripts/build_warehouse.py"], "Warehouse Build"):
        logger.error("Pipeline aborted at Warehouse Build.")
        sys.exit(1)

    # 3. Statistical Assay
    if not run_cmd(["python3", "scripts/assay_market.py"], "Market Assay"):
        logger.error("Pipeline aborted at Market Assay.")
        sys.exit(1)

    # 4. Snapshot Archive
    try:
        archive_dir = ROOT / "localdata" / "daily"
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        
        source = ROOT / "localdata" / "warehouse.csv.gz"
        if source.exists():
            dest = archive_dir / f"warehouse_{timestamp}.csv.gz"
            source.replace(dest)
            logger.info("Daily snapshot archived to %s", dest)
        else:
            logger.warning("Warehouse file not found for archiving: %s", source)
    except Exception as e:
        logger.error("Failed to archive daily snapshot: %s", e)

    logger.info("--- RACKET FACTORY DAILY PIPELINE COMPLETE ---")

if __name__ == "__main__":
    main()
