#!/usr/bin/env python3
"""Normalize OddsPortal tennis CSV/HTML/URL into localdata CSVs.

Single-route mode:
    PYTHONPATH=src python3 scripts/capture_oddsportal.py --route-key ATP_WIMBLEDON --year 2026 --pages 3

Bulk mode (all routes, all configured years):
    PYTHONPATH=src python3 scripts/capture_oddsportal.py --all

Bulk mode (specific routes, specific years):
    PYTHONPATH=src python3 scripts/capture_oddsportal.py --route ATP_WIMBLEDON WTA_WIMBLEDON --year 2024 2025 2026

Options:
    --pages N          Override page count per route
    --delay SECONDS    Wait between page fetches (default: 5.0, use 0 to disable)
    --dry-run          Print what would be captured without fetching
    --skip-exists      Skip routes whose CSVs already exist in data_dir
    --no-checkpoint    Ignore existing progress checkpoint (start fresh)
    --save-html DIR    Save rendered HTML snapshots
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.config import load_routes, ROUTES_PATH
from racketfactory.oddsportal import parse_embedded_json, parse_rendered_html, read_export_csv, write_monthly_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("capture_oddsportal")

# Script to remove navigator.webdriver flag
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.navigator.chrome = { runtime: {}, };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5], });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], });
"""

def infer_year_from_url(url: str) -> int | None:
    match = re.search(r"-(20\d{2})/results/?", url)
    return int(match.group(1)) if match else None


def page_url(url: str, page_no: int) -> str:
    if page_no <= 1:
        return url
    parts = urlsplit(url)
    base_path = parts.path.rstrip("/") + "/"
    return urlunsplit((parts.scheme, parts.netloc, base_path, parts.query, f"/page/{page_no}/"))


def resolve_url(template: str, year: int) -> str:
    return template.replace("{year}", str(year))


def fetch_rendered_html(url: str, *, timeout_ms: int = 60000, max_retries: int = 2) -> str:
    """Render a URL with Playwright Chromium and return the HTML."""
    from playwright.sync_api import sync_playwright
    
    last_html = ""
    for attempt in range(1, max_retries + 1):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 1200},
                locale="en-GB",
                timezone_id="UTC",
                java_script_enabled=True,
            )
            page = context.new_page()
            
            # Inject stealth scripts before navigating
            page.add_init_script(script=STEALTH_JS)
            
            # Block heavy resources
            page.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}", lambda r: r.abort())
            
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Wait for network idle (gives time for CF challenge)
                page.wait_for_timeout(3000)
                
                html = page.content()
                
                # Check for Cloudflare challenge
                if len(html) < 2000 or "cloudflare" in html.lower() or "challenge" in html.lower():
                    logger.warning(f"[Attempt {attempt}] Possible Cloudflare block (length={len(html)}). Waiting longer...")
                    page.wait_for_timeout(8000)
                    html = page.content()
                
                last_html = html
                
                # If we got meaningful content, break
                if len(html) > 3000 and "cloudflare" not in html.lower():
                    break
                elif attempt < max_retries:
                    logger.info(f"  Retrying page fetch...")
                    
            except Exception as exc:
                logger.error(f"  Fetch error: {exc}")
                last_html = ""
            finally:
                browser.close()
            
            # Small delay between retries
            if attempt < max_retries and len(last_html) < 3000:
                time.sleep(2)

    return last_html


# ---- Bulk helpers -----------------------------------------------------------

class Checkpoint:
    """Persist per-route progress so interrupted runs resume safely."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict[str, int]] = self._load()

    def _load(self) -> dict[str, dict[str, int]]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                return {}
        return {}

    def is_done(self, route_key: str, year: int, pages: int) -> bool:
        return self.data.get(route_key, {}).get(str(year), 0) >= pages

    def mark_done(self, route_key: str, year: int, pages: int):
        self.data.setdefault(route_key, {})[str(year)] = max(
            self.data.get(route_key, {}).get(str(year), 0), pages
        )
        self.path.write_text(json.dumps(self.data, indent=2))

    def summary(self) -> str:
        lines = ["Progress checkpoint:"]
        for rk, pages in sorted(self.data.items()):
            total_pages = max(pages.values()) if pages else 0
            years = sorted(pages.keys())
            lines.append(f"  {rk}: {total_pages} pages across years " + ", ".join(years))
        return "\n".join(lines) if lines[1:] else "  No checkpoints yet."


def _row_dedup_key(row: dict) -> str:
    return (
        f"{row.get('match_date')}|{row.get('tour')}|{row.get('tournament')}"
        f"|{row.get('player_a')}|{row.get('player_b')}|{row.get('bookmaker')}"
    )


def merge_dedup(all_rows: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for row in all_rows:
        seen[_row_dedup_key(row)] = row
    return list(seen.values())


def build_tasks(
    routes: dict,
    route_keys: list[str],
    years: list[int] | None,
    pages: int | None,
) -> list[dict]:
    """Expand route config into (url, year, pages, tour, tournament) tasks."""
    tasks: list[dict] = []
    for rk in route_keys:
        if rk not in routes:
            logger.warning("Route key %s not in %s — skipping", rk, ROUTES_PATH)
            continue
        route = routes[rk]
        url_template = route.get("url_template") or route.get("base_url") or route.get("url")
        if not url_template:
            logger.warning("Route %s has no url_template/base_url — skipping", rk)
            continue
        route_years = route.get("years", [])
        effective_years = [y for y in years if y in route_years] if years else route_years
        if not effective_years:
            logger.warning("Route %s has no matching years — skipping", rk)
            continue
        for y in effective_years:
            n_pages = pages if pages is not None else route.get("pages_default", 5)
            tasks.append({
                "route_key": rk,
                "url": resolve_url(url_template, y),
                "tour": route.get("tour", "UNKNOWN"),
                "tournament": route.get("tournament", ""),
                "year": y,
                "pages": n_pages,
            })
    return tasks


# ---- Single-route capture (original API) ------------------------------------

def capture_single(
    *,
    input_csv: str | None,
    input_html: str | None,
    url: str,
    tour: str,
    tournament: str,
    default_year: int | None,
    pages: int,
    data_dir: Path,
    save_html: str,
) -> int:
    """Original single-route/HTML/CSV capture. Returns row count."""
    rows: list[dict] = []
    if input_csv:
        rows.extend(read_export_csv(input_csv))

    html_pages: list[tuple[str, str]] = []
    if url:
        for page_no in range(1, pages + 1):
            p_url = page_url(url, page_no)
            logger.info("Fetching page %d/%d ...", page_no, pages)
            html = fetch_rendered_html(p_url)
            html_pages.append((html, p_url))
            if save_html:
                save_path = Path(save_html)
                if pages > 1:
                    save_path = save_path.with_name(
                        f"{save_path.stem}_p{page_no}{save_path.suffix or '.html'}"
                    )
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(html)
    if input_html:
        html_pages.append((Path(input_html).read_text(errors="replace"), ""))

    year = default_year or (infer_year_from_url(url) if url else None)
    for html, source_url in html_pages:
        rows.extend(parse_rendered_html(
            html,
            default_year=year,
            tour=tour,
            tournament=tournament,
            oddsportal_url=source_url,
        ))
        rows.extend(parse_embedded_json(
            html, tour=tour, tournament=tournament, oddsportal_url=source_url,
        ))

    written = write_monthly_csv(rows, str(data_dir))
    print(f"normalized rows: {len(rows)}")
    for path in written:
        print(f"wrote {path}")
    if not rows:
        print(
            "WARNING: no rows parsed — save the HTML and add a fixture-specific "
            "parser/test before scaling capture",
            file=sys.stderr,
        )
    return len(rows)


# ---- Bulk capture -----------------------------------------------------------

def capture_bulk(
    *,
    tasks: list[dict],
    data_dir: Path,
    delay: float,
    dry_run: bool,
    save_html_dir: Path | None,
    checkpoint: Checkpoint | None,
    skip_exists: bool,
) -> int:
    """Execute all tasks with dedup, checkpointing, and rate-limiting."""
    if skip_exists:
        existing = sorted(data_dir.glob("oddsportal_tennis_*.csv.gz"))
        if existing:
            logger.info("CSVs already exist in %s — skipping all routes (--skip-exists)", data_dir)
            return 0

    all_rows: list[dict] = []
    for idx, task in enumerate(tasks):
        rk = task["route_key"]
        url = task["url"]
        year = task["year"]
        n_pages = task["pages"]
        tour = task["tour"]
        tournament = task["tournament"]
        logger.info(
            "[%d/%d] %s (year=%d, %d pages) — %s",
            idx + 1, len(tasks), rk, year, n_pages, url,
        )

        if checkpoint and not dry_run and checkpoint.is_done(rk, year, n_pages):
            logger.info("  -> Already captured (checkpoint). Skipping.")
            continue

        page_rows: list[dict] = []
        for pg in range(1, n_pages + 1):
            p_url = page_url(url, pg)
            logger.info("  -> Fetching page %d/%d ...", pg, n_pages)

            if dry_run:
                logger.info("    [DRY RUN] Would fetch %s", p_url)
                continue

            html = fetch_rendered_html(p_url)
            
            if not html or len(html) < 2000:
                logger.error(f"  X Page {pg} returned empty/blocked HTML. Skipping.")
                continue

            page_rows.extend(
                parse_rendered_html(
                    html, tour=tour, tournament=tournament,
                    default_year=year, oddsportal_url=p_url,
                )
            )
            page_rows.extend(
                parse_embedded_json(
                    html, tour=tour, tournament=tournament,
                    oddsportal_url=p_url,
                )
            )
            logger.info("    Parsed %d rows from page %d.", len(page_rows), pg)

            if save_html_dir:
                save_html_dir.mkdir(parents=True, exist_ok=True)
                safe_rk = rk.replace(" ", "_").replace("/", "_")
                (save_html_dir / f"{safe_rk}_{year}_p{pg}.html").write_text(html)

            if delay > 0 and pg < n_pages:
                time.sleep(delay)

        all_rows.extend(page_rows)

        if checkpoint and not dry_run and page_rows:
            checkpoint.mark_done(rk, year, n_pages)
            logger.info("  [tick] %s year %d done. %d rows.", rk, year, len(page_rows))

        if delay > 0 and not dry_run:
            time.sleep(delay)

    if not all_rows:
        logger.info("No rows captured.")
        return 0

    deduped = merge_dedup(all_rows)
    logger.info("Total raw rows: %d | After dedup: %d", len(all_rows), len(deduped))
    written = write_monthly_csv(deduped, str(data_dir))
    for p in written:
        logger.info("Wrote %s", p)
    return len(deduped)


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Normalize OddsPortal tennis CSV/HTML/URL into localdata CSVs.",
    )
    # Original single-route args
    ap.add_argument("--input-csv", help="Exported CSV with date/player/odds columns")
    ap.add_argument("--input-html", help="Saved/rendered OddsPortal HTML page")
    ap.add_argument("--url", help="OddsPortal URL to render with Playwright")
    ap.add_argument("--route-key", help="Key in config/routes.json, e.g. ATP_WIMBLEDON")
    ap.add_argument("--date", default="", help="Default match date for HTML rows")
    ap.add_argument("--year", type=int, default=None, help="Tournament year for date headers")
    ap.add_argument("--tour", default="UNKNOWN", help="Tour segment, e.g. ATP/WTA/CHALLENGER")
    ap.add_argument("--tournament", default="", help="Tournament name for HTML rows")
    ap.add_argument("--data-dir", default=str(ROOT / "localdata"), help="Output CSV directory")
    ap.add_argument("--save-html", default="", help="Path/prefix to save rendered HTML")
    ap.add_argument("--pages", type=int, default=1, help="Number of pages to capture")
    # Bulk mode args
    ap.add_argument("--all", action="store_true", help="Capture all routes for all configured years")
    ap.add_argument("--route", nargs="+", default=[], help="Specific route key(s) to capture")
    ap.add_argument("--years", nargs="+", type=int, default=[], help="Year(s) to capture")
    ap.add_argument("--delay", type=float, default=5.0, help="Seconds between page fetches (default 5.0)")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without fetching")
    ap.add_argument("--skip-exists", action="store_true", help="Skip if CSVs already exist")
    ap.add_argument("--no-checkpoint", action="store_true", help="Ignore existing progress checkpoint")
    args = ap.parse_args()

    if args.pages < 1:
        ap.error("--pages must be >= 1")

    routes = load_routes()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # ---- Bulk mode ----
    if args.all or args.route:
        route_keys = [k for k in routes if not k.startswith("_")] if args.all else args.route
        tasks = build_tasks(routes, route_keys, args.years if args.years else None, None if args.pages == 1 else args.pages)
        if not tasks:
            logger.info("No tasks to run.")
            return 0

        ckpt_path = ROOT / "localdata" / ".bulk_checkpoint.json"
        checkpoint = None if args.no_checkpoint else Checkpoint(ckpt_path)
        save_html_dir = Path(args.save_html) if args.save_html else None

        total = capture_bulk(
            tasks=tasks,
            data_dir=data_dir,
            delay=args.delay,
            dry_run=args.dry_run,
            save_html_dir=save_html_dir,
            checkpoint=checkpoint,
            skip_exists=args.skip_exists,
        )
        if checkpoint:
            logger.info(checkpoint.summary())
        logger.info("Done. Total deduped rows: %d", total)
        return 0

    # ---- Single-route mode (original) ----
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

    count = capture_single(
        input_csv=args.input_csv,
        input_html=args.input_html,
        url=args.url,
        tour=args.tour,
        tournament=args.tournament,
        default_year=args.year,
        pages=args.pages,
        data_dir=data_dir,
        save_html=args.save_html,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())