"""OddsPortal upcoming-tennis odds adapter (day pages, not the archive).

The existing capture_oddsportal.py mines the historical results archive per
tournament/year. This module is the complementary live leg: it reads the
server-rendered day listings — /tennis/ for today, /tennis/tomorrow/ for
tomorrow — whose 1/2 columns are the best price across bookmakers. Coverage
spans ATP/WTA/Challenger/ITF, including doubles.

Best-across-books prices skew optimistic versus any single book, so the
merger (odds_compare) treats them as the cross-check leg and prices from the
BetExplorer consensus leg when both agree. Disable with
RACKET_FACTORY_DISABLE_ODDSPORTAL_UPCOMING=1.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources._page_odds import fetch_page_html, parse_listing_page

logger = logging.getLogger(__name__)

BASE_URL = "https://www.oddsportal.com"
TODAY_PATH = "/tennis/"
TOMORROW_PATH = "/tennis/tomorrow/"
SOURCE_NAME = "OddsPortal"
BOOK_LABEL = "OddsPortal best odds"
DISABLE_ENV = "RACKET_FACTORY_DISABLE_ODDSPORTAL_UPCOMING"

_CHALLENGE_MARKERS = ("Just a moment", "Attention Required", "cf_chl", "cf_clearance")


def _is_match_link(href: str) -> dict[str, Any] | None:
    """Match links live under /tennis/h2h/<player-a>/<player-b>/."""
    try:
        path = urlsplit(href).path
    except Exception:
        return None
    segs = [s for s in path.split("/") if s]
    if len(segs) < 4 or segs[0] != "tennis" or segs[1] != "h2h":
        return None
    return {"tour_hint": "", "tournament": ""}


def _is_challenge_page(html: str) -> bool:
    head = html[:6000]
    return any(m in head for m in _CHALLENGE_MARKERS)


def parse_tennis_page(html: str, page_date: str) -> list[dict[str, Any]]:
    if _is_challenge_page(html):
        logger.warning("OddsPortal served a challenge page; no upcoming rows.")
        return []
    rows = parse_listing_page(
        html, source_label=SOURCE_NAME, is_match_link=_is_match_link, page_date=page_date,
    )
    for row in rows:
        row["bookmaker"] = BOOK_LABEL
        row["source"] = SOURCE_NAME
        url = str(row.pop("match_url", "") or "")
        row["event_id"] = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return rows


def _page_for_target(target: str) -> tuple[str, str] | None:
    """(page path, cache key) or None when the target is out of range."""
    today = date.today()
    if target == today.isoformat():
        return TODAY_PATH, "oddsportal_upcoming_today"
    if target == (today + timedelta(days=1)).isoformat():
        return TOMORROW_PATH, "oddsportal_upcoming_tomorrow"
    return None


def _fetch_live(path: str, page_date: str) -> list[dict[str, Any]]:
    html = fetch_page_html(BASE_URL + path, SOURCE_NAME)
    if not html:
        return []
    return parse_tennis_page(html, page_date)


def fetch_oddsportal_upcoming_rows(target_date: str) -> list[dict[str, Any]]:
    """Priced upcoming rows for target_date (today/tomorrow), TheOddsAPI-shaped."""
    if os.getenv(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    target = str(target_date)[:10]
    page = _page_for_target(target)
    if page is None:
        logger.info("OddsPortal upcoming serves today/tomorrow only; no rows for %s.", target)
        return []
    path, cache_key = page
    try:
        rows = cached_fetch(cache_key, lambda: _fetch_live(path, target))
    except Exception as exc:
        logger.warning("OddsPortal upcoming fetch failed: %s", exc)
        return []
    return [dict(r) for r in rows] if rows else []
