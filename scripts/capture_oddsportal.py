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
    --delay SECONDS    Wait between page fetches (default: 2.0)
    --dry-run          Print what would be captured without fetching
    --skip-exists      Skip routes whose CSVs already exist in data_dir
    --no-checkpoint    Ignore existing progress checkpoint (start fresh)
    --save-html DIR    Save rendered HTML snapshots
    --force-render     Skip curl_cffi, go straight to Playwright render
    --env ODDSPORTAL_USE_PLAYWRIGHT=1 also forces Playwright path

Fetcher strategy (v0.3+):
    1. curl_cffi with current Chrome impersonation hits the tournament SPA
       page (/results/, no year in URL) and pulls the page ID.
    2. If page ID found, hit the AJAX archive endpoint with year as a path
       segment to get all pages.
    3. If curl_cffi can't clear Cloudflare, fall through to Playwright
       which uses the browser's cf_clearance cookies to do AJAX calls in
       page context (most reliable).
    4. If AJAX is blocked entirely, render the page DOM and click through
       pagination as a last resort.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.config import load_routes, ROUTES_PATH
from racketfactory.oddsportal import (
    _fetch_ajax_via_playwright,
    _is_cloudflare_challenge,
    _is_error_page,
    _resolve_page_id,
    fetch_rendered_html,
    fetch_tournament_pages,
    fetch_via_rendered_dom,
    parse_embedded_json,
    parse_rendered_html,
    read_export_csv,
    write_monthly_csv,
)
from datetime import date as _date_today

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("capture_oddsportal")


def infer_year_from_url(url: str) -> int | None:
    match = re.search(r"-(20\d{2})/results/?", url)
    if match:
        return int(match.group(1))
    # After 2026 SPA migration, year is no longer in URL — try a few
    # alternative patterns in case the user passes a legacy URL.
    match = re.search(r"/(20\d{2})/", url)
    return int(match.group(1)) if match else None


def page_url(url: str, page_no: int) -> str:
    """Build a paginated URL.

    After the 2026 SPA migration, OddsPortal uses client-side pagination
    (clicking Next), so this URL is mostly cosmetic / diagnostic now. We
    keep it for backwards compatibility with the legacy /page/N/ fragment.
    """
    if page_no <= 1:
        return url
    parts = urlsplit(url)
    base_path = parts.path.rstrip("/") + "/"
    return urlunsplit((parts.scheme, parts.netloc, base_path, parts.query, f"/page/{page_no}/"))


def resolve_url(template: str, year: int) -> str:
    """Substitute {year} into the URL template.

    Note: after the 2026 SPA migration the URL no longer contains the year.
    This helper still substitutes if the template includes {year} (legacy
    routes), otherwise returns the template unchanged.
    """
    return template.replace("{year}", str(year))


def _force_playwright() -> bool:
    return os.getenv("ODDSPORTAL_USE_PLAYWRIGHT", "").strip().lower() in {"1", "true", "yes"}


def _current_year() -> int:
    return _date_today.today().year


def _no_year_url_fallback(url: str, year: int) -> str | None:
    """If the URL contains '-{year}' for the current year, return the no-year variant.

    The current season uses `/results/` without a year suffix; the year-in-slug
    pattern (`/atp-wimbledon-2026/results/`) returns 404 for the in-progress season.
    """
    if year != _current_year():
        return None
    needle = f"-{year}"
    if needle not in url:
        return None
    return url.replace(needle, "")


def _capture_with_retry(
    *,
    url: str,
    tour: str,
    tournament: str,
    year: int,
    n_pages: int,
    delay: float,
    save_html_dir: Path | None,
    page_id: str | None = None,
) -> tuple[list[dict], str]:
    """Capture rows for one URL with a single retry on transient failures.

    Returns (rows, url_used). Retries with a longer Playwright timeout when
    the first attempt returns 0 rows AND the page_id resolved cleanly (i.e.
    we know CF is cleared but rendering was flaky).
    """
    curl_rows = _try_curl_fetch(url, tour, tournament, year, n_pages, delay)
    if curl_rows:
        logger.info("  [curl_cffi] Fetched %d rows", len(curl_rows))
        return curl_rows, url

    logger.info("  [curl_cffi] Failed/empty, falling back to Playwright...")
    resolved_id = page_id if page_id else _resolve_page_id(url)
    pw_rows = _try_playwright_fetch(
        url, tour, tournament, year, n_pages, delay, save_html_dir,
        page_id=resolved_id,
    )
    if pw_rows:
        return pw_rows, url

    if not resolved_id:
        # Couldn't even resolve the page id — no point retrying.
        return [], url

    logger.info(
        "  -> First Playwright attempt returned 0 rows with page_id=%s; retrying "
        "with a longer 150 s timeout in case the render-DOM path just needed "
        "more time for events to paint.",
        resolved_id,
    )
    time.sleep(5)
    # Second Playwright attempt with longer per-page timeout by temporarily
    # monkey-patching the env, then a clean retry of the same fallback chain.
    prev = os.environ.get("ODDSPORTAL_RENDER_TIMEOUT_MS")
    os.environ["ODDSPORTAL_RENDER_TIMEOUT_MS"] = "150000"
    try:
        pw_rows2 = _try_playwright_fetch(
            url, tour, tournament, year, n_pages, delay, save_html_dir,
            page_id=resolved_id,
        )
    finally:
        if prev is None:
            os.environ.pop("ODDSPORTAL_RENDER_TIMEOUT_MS", None)
        else:
            os.environ["ODDSPORTAL_RENDER_TIMEOUT_MS"] = prev
    return pw_rows2, url


def _capture_url_with_current_year_fallback(
    *,
    url: str,
    tour: str,
    tournament: str,
    year: int,
    n_pages: int,
    delay: float,
    save_html_dir: Path | None,
) -> tuple[list[dict], str]:
    """Try a URL; if 0 rows and the year matches current year, try no-year fallback."""
    rows, url_used = _capture_with_retry(
        url=url, tour=tour, tournament=tournament, year=year,
        n_pages=n_pages, delay=delay, save_html_dir=save_html_dir,
    )
    if rows:
        return rows, url_used
    fallback_url = _no_year_url_fallback(url, year)
    if not fallback_url or fallback_url == url:
        return rows, url_used
    logger.info(
        "  -> Year-in-slug URL returned 0 rows; trying current-season no-year URL: %s",
        fallback_url,
    )
    fb_rows, fb_url_used = _capture_with_retry(
        url=fallback_url, tour=tour, tournament=tournament, year=year,
        n_pages=n_pages, delay=delay, save_html_dir=save_html_dir,
    )
    if fb_rows:
        return fb_rows, fb_url_used
    return rows, url_used


def _try_curl_fetch(
    tournament_url: str,
    tour: str,
    tournament: str,
    default_year: int | None,
    pages: int,
    delay: float,
) -> list[dict]:
    """Try the curl_cffi + AJAX approach first."""
    if _force_playwright():
        return []
    return fetch_tournament_pages(
        tournament_url,
        tour=tour,
        tournament=tournament,
        default_year=default_year,
        max_pages=pages,
        sleep=delay,
    )


def _try_playwright_fetch(
    tournament_url: str,
    tour: str,
    tournament: str,
    default_year: int | None,
    pages: int,
    delay: float,
    save_html_dir: Path | None,
    page_id: str | None = None,
) -> list[dict]:
    """Playwright fallback.

    Strategy:
      1. If we already have a page_id (from a successful curl_cffi resolver),
         use _fetch_ajax_via_playwright to do AJAX calls in the browser
         context (most reliable — uses browser's cf_clearance cookie).
      2. Otherwise, resolve the page_id via Playwright, then do AJAX calls.
      3. If AJAX still doesn't work, fall through to rendered-DOM pagination.
    """
    rows: list[dict] = []

    if page_id is None:
        # Load the page first; this both clears CF and gives us the page_id.
        logger.info("  -> Playwright: opening %s to resolve page_id + clear CF", tournament_url)
        from racketfactory.oddsportal import fetch_rendered_html
        html = fetch_rendered_html(tournament_url)
        if save_html_dir:
            save_html_dir.mkdir(parents=True, exist_ok=True)
            safe_tour = tournament_url.split("/")[-3].replace(" ", "_")
            (save_html_dir / f"{safe_tour}_{default_year or 'any'}_p1.html").write_text(html)
        if html and not _is_cloudflare_challenge(html) and not _is_error_page(html):
            page_id = _resolve_page_id_from_rendered(html)

    if page_id:
        logger.info("  -> Playwright AJAX path with page_id=%s", page_id)
        ajax_rows = _fetch_ajax_via_playwright(
            page_id,
            tour=tour,
            tournament=tournament,
            default_year=default_year,
            oddsportal_url=tournament_url,
            max_pages=pages,
            sleep=delay,
        )
        if ajax_rows:
            rows.extend(ajax_rows)
            return rows

    # Last-resort: click through the rendered DOM
    logger.info("  -> Playwright render-DOM fallback (page_id=%s)", page_id or "<unresolved>")
    render_rows = fetch_via_rendered_dom(
        tournament_url,
        tour=tour,
        tournament=tournament,
        default_year=default_year,
        oddsportal_url=tournament_url,
        max_pages=pages,
        sleep=delay,
    )
    rows.extend(render_rows)
    return rows


def _resolve_page_id_from_rendered(html: str) -> str | None:
    """Same regex set as `_resolve_page_id` but works on rendered HTML too."""
    from racketfactory.oddsportal import _extract_page_id
    return _extract_page_id(html)


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
            lines.append(f"  {rk}: {total_pages} pages captured across years {', '.join(years)}")
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
    delay: float = 5.0,
    save_html_dir: Path | None = None,
) -> int:
    """Original single-route/HTML/CSV capture. Returns row count."""
    rows: list[dict] = []
    if input_csv:
        rows.extend(read_export_csv(input_csv))

    if input_html:
        html = Path(input_html).read_text(errors="replace")
        rows.extend(parse_rendered_html(
            html, default_year=default_year, tour=tour, tournament=tournament,
        ))
        rows.extend(parse_embedded_json(
            html, tour=tour, tournament=tournament,
        ))

    if url:
        # Capture with curl_cffi → Playwright → retry → current-year fallback.
        rows_out, url_used = _capture_url_with_current_year_fallback(
            url=url, tour=tour, tournament=tournament, year=default_year or _current_year(),
            n_pages=pages, delay=delay, save_html_dir=save_html_dir,
        )
        rows.extend(rows_out)
        if url_used != url:
            logger.info("  [fallback] Captured from %s instead of %s", url_used, url)

        if save_html and rows_out:
            save_path = Path(save_html)
            if pages > 1:
                save_path = save_path.with_name(
                    f"{save_path.stem}_p1{save_path.suffix or '.html'}"
                )
            save_path.parent.mkdir(parents=True, exist_ok=True)
            html = fetch_rendered_html(url_used)
            save_path.write_text(html)

    written = write_monthly_csv(rows, str(data_dir))
    print(f"normalized rows: {len(rows)}")
    for path in written:
        print(f"wrote {path}")
    if not rows:
        print(
            "WARNING: no rows parsed — check that curl_cffi is installed, "
            "that the URL matches a live OddsPortal tournament, and that "
            "your IP can clear Cloudflare. See HANDOVER.md for debugging.",
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

        if dry_run:
            logger.info("    [DRY RUN] Would fetch %s", url)
            for pg in range(2, n_pages + 1):
                p_url = page_url(url, pg)
                logger.info("    [DRY RUN] Would fetch %s", p_url)
            continue

        # Capture with curl_cffi → Playwright → retry → current-year fallback.
        rows, url_used = _capture_url_with_current_year_fallback(
            url=url, tour=tour, tournament=tournament, year=year,
            n_pages=n_pages, delay=delay, save_html_dir=save_html_dir,
        )
        page_rows.extend(rows)
        if url_used != url:
            logger.info("  [fallback] Captured from %s instead of %s", url_used, url)
        if not rows:
            logger.warning(
                "  X All capture paths returned 0 rows for %s year %d",
                rk, year,
            )

        all_rows.extend(page_rows)

        if checkpoint and page_rows:
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
    ap.add_argument("--delay", type=float, default=2.0, help="Seconds between page fetches (default 2.0)")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without fetching")
    ap.add_argument("--skip-exists", action="store_true", help="Skip if CSVs already exist")
    ap.add_argument("--no-checkpoint", action="store_true", help="Ignore existing progress checkpoint")
    ap.add_argument("--force-render", action="store_true",
                    help="Skip curl_cffi, go straight to Playwright render (same as ODDSPORTAL_USE_PLAYWRIGHT=1)")
    args = ap.parse_args()

    if args.force_render:
        os.environ["ODDSPORTAL_USE_PLAYWRIGHT"] = "1"

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

    save_html_dir = Path(args.save_html) if args.save_html else None
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
        delay=args.delay,
        save_html_dir=save_html_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
