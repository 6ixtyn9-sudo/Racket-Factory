#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.config import load_routes
from racketfactory.oddsportal import parse_embedded_json, parse_rendered_html, read_export_csv, write_monthly_csv


def infer_year_from_url(url: str) -> int | None:
    match = re.search(r"-(20\d{2})/results/?", url)
    return int(match.group(1)) if match else None


def page_url(url: str, page_no: int) -> str:
    if page_no <= 1:
        return url
    parts = urlsplit(url)
    base_path = parts.path.rstrip("/") + "/"
    return urlunsplit((parts.scheme, parts.netloc, base_path, parts.query, f"/page/{page_no}/"))


def fetch_rendered_html(url: str, *, timeout_ms: int = 60000) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1200},
        )
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        try:
            page.wait_for_selector(".eventRow, [data-testid='game-row']", timeout=15000)
        except Exception:
            pass
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
    ap.add_argument("--year", type=int, default=None, help="Tournament year for rendered date headers without a year")
    ap.add_argument("--tour", default="UNKNOWN", help="Tour segment, e.g. ATP/WTA/CHALLENGER")
    ap.add_argument("--tournament", default="", help="Tournament name for HTML rows")
    ap.add_argument("--data-dir", default=str(ROOT / "localdata"))
    ap.add_argument("--save-html", default="", help="Optional path/prefix to save rendered HTML from --url")
    ap.add_argument("--pages", type=int, default=1, help="Number of rendered OddsPortal result pages to capture")
    args = ap.parse_args()

    if args.pages < 1:
        ap.error("--pages must be >= 1")

    routes = load_routes()
    if args.route_key and args.route_key in routes:
        route = routes[args.route_key]
        if args.tour == "UNKNOWN":
            args.tour = route.get("tour", args.tour)
        if not args.tournament:
            args.tournament = route.get("tournament", args.tournament)
        if not args.url:
            args.url = route.get("url") or route.get("base_url") or args.url

    if not args.input_csv and not args.input_html and not args.url:
        ap.error("provide --input-csv, --input-html, --url, or a --route-key with a URL")

    rows = []
    if args.input_csv:
        rows.extend(read_export_csv(args.input_csv))

    html_pages: list[tuple[str, str]] = []
    if args.url:
        for page_no in range(1, args.pages + 1):
            url = page_url(args.url, page_no)
            html = fetch_rendered_html(url)
            html_pages.append((html, url))
            if args.save_html:
                save_path = Path(args.save_html)
                if args.pages > 1:
                    save_path = save_path.with_name(f"{save_path.stem}_p{page_no}{save_path.suffix or '.html'}")
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(html)
    if args.input_html:
        html_pages.append((Path(args.input_html).read_text(errors="replace"), ""))

    default_year = args.year or (infer_year_from_url(args.url) if args.url else None)
    for html, source_url in html_pages:
        rows.extend(parse_rendered_html(
            html,
            default_date=args.date,
            default_year=default_year,
            tour=args.tour,
            tournament=args.tournament,
            oddsportal_url=source_url,
        ))
        rows.extend(parse_embedded_json(html, tour=args.tour, tournament=args.tournament, oddsportal_url=source_url))

    written = write_monthly_csv(rows, args.data_dir)
    print(f"normalized rows: {len(rows)}")
    for path in written:
        print(f"wrote {path}")
    if not rows:
        print("WARNING: no rows parsed — save the HTML and add a fixture-specific parser/test before scaling capture", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
