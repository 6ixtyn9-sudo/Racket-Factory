#!/usr/bin/env python3
"""
Backfill recent results from TennisExplorer (all tours, not just Challenger).

TennisExplorer ``/results/`` pages repeat adjacent-day content, so every row
carries the page's visible date and the match-detail id; merging keeps the
earliest dated row per id.

Usage:
    PYTHONPATH=src python3 scripts/backfill_challenger_results.py --days 4 --output-dir localdata
    PYTHONPATH=src python3 scripts/backfill_challenger_results.py --date 2026-09-11 --output-dir localdata
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.tennis_explorer import (  # noqa: E402
    fetch_day,
    parse_results_page,
    write_result_rows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("backfill_challenger")


def parse_tennisexplorer_html(html: str, target_date: str) -> list[dict]:
    """Compatibility wrapper: parse HTML into normalized row dicts."""
    return parse_results_page(html, target_date).rows


def fetch_flashscore_challenger_results(target_date: str) -> list[dict]:
    """Fetch one day (name kept for CLI compatibility)."""
    page = fetch_day(target_date)
    if page.visible_date and page.visible_date != target_date:
        logger.warning("Visible page date %s != requested %s: rows kept "
                       "with _page_date for audit, earliest-date merge wins",
                       page.visible_date, target_date)
    return page.rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4,
                    help="Days back from today to fetch (default 4)")
    ap.add_argument("--output-dir", default="localdata")
    ap.add_argument("--date", help="Specific date YYYY-MM-DD")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.date:
        dates = [args.date]
    else:
        today = date.today()
        dates = [(today - timedelta(days=i)).isoformat()
                 for i in range(args.days)]

    all_rows: list[dict] = []
    for day in dates:
        rows = fetch_flashscore_challenger_results(day)
        logger.info("%s: %d rows", day, len(rows))
        all_rows.extend(rows)

    if all_rows:
        written = write_result_rows(all_rows, out_dir)
        logger.info("Total %d rows merged into %s",
                    len(all_rows), ", ".join(str(w) for w in written))
    else:
        logger.warning("No challenger results found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
