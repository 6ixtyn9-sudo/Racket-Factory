#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.config import load_routes
from racketfactory.oddsportal import parse_embedded_json, parse_rendered_html, read_export_csv, write_monthly_csv


def fetch_rendered_html(url: str, *, timeout_ms: int = 60000) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        html = page.content()
        browser.close()
        return html


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize OddsPortal tennis CSV/HTML/URL into localdata CSVs.")
    ap.add_argument("--input-csv", help="Exported CSV with date/player/odds columns")
    ap.add_argument("--input-html", help="Saved/rendered OddsPortal HTML page")
    ap.add_argument("--url", help="OddsPortal URL to render with Playwright")
    ap.add_argument("--route-key", help="Optional key in config/routes.json, e.g. ATP or WTA")
    ap.add_argument("--date", default="", help="Default match date for HTML rows")
    ap.add_argument("--tour", default="UNKNOWN", help="Tour segment, e.g. ATP/WTA/CHALLENGER")
    ap.add_argument("--tournament", default="", help="Tournament name for HTML rows")
    ap.add_argument("--data-dir", default=str(ROOT / "localdata"))
    ap.add_argument("--save-html", default="", help="Optional path to save rendered HTML from --url")
    args = ap.parse_args()

    if not args.input_csv and not args.input_html and not args.url:
        ap.error("provide --input-csv, --input-html, or --url")

    routes = load_routes()
    if args.route_key and args.route_key in routes:
        route = routes[args.route_key]
        if args.tour == "UNKNOWN":
            args.tour = route.get("tour", args.tour)

    rows = []
    if args.input_csv:
        rows.extend(read_export_csv(args.input_csv))

    html = ""
    url_for_rows = ""
    if args.url:
        html = fetch_rendered_html(args.url)
        url_for_rows = args.url
        if args.save_html:
            Path(args.save_html).write_text(html)
    if args.input_html:
        html = Path(args.input_html).read_text(errors="replace")

    if html:
        rows.extend(parse_rendered_html(html, default_date=args.date, tour=args.tour, tournament=args.tournament, oddsportal_url=url_for_rows))
        rows.extend(parse_embedded_json(html, tour=args.tour, tournament=args.tournament, oddsportal_url=url_for_rows))

    written = write_monthly_csv(rows, args.data_dir)
    print(f"normalized rows: {len(rows)}")
    for path in written:
        print(f"wrote {path}")
    if not rows:
        print("WARNING: no rows parsed — save the HTML and add a fixture-specific parser/test before scaling capture", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
