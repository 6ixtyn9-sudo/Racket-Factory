"""BetExplorer tennis day-page odds adapter.

BetExplorer renders https://www.betexplorer.com/tennis/ server-side with one
row per upcoming match: ``time | Home - Away | 1 | 2`` across ATP, WTA,
Challenger (men/women incl. WTA-125Ks) and ITF — exactly the tours The Odds
API does not cover. The listing prices track the bookmaker consensus (spot
checked 2026-09-13 against Betway SA: within ~2-4%), so they are usable as
indicative market prices for EV gating and acca building. Display them as
indicative, never as the ticket price at the user's book.

Scope: the day page only (no verified date-URL pattern for other days), so
this source serves the fetch day and returns [] for any other target date.
Disable with RACKET_FACTORY_DISABLE_BETEXPLORER=1.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources._page_odds import fetch_page_html, parse_listing_page

logger = logging.getLogger(__name__)

BASE_URL = "https://www.betexplorer.com"
TENNIS_URL = BASE_URL + "/tennis/"
SOURCE_NAME = "BetExplorer"
BOOK_LABEL = "BetExplorer consensus"
DISABLE_ENV = "RACKET_FACTORY_DISABLE_BETEXPLORER"


def _is_match_link(href: str) -> dict[str, Any] | None:
    """Match links are /tennis/<cat>/<tournament>/<slug>/<id>/ (5 segments).

    Tournament links (/tennis/<cat>/<tournament>/) and player links fall out
    by segment count / prefix.
    """
    try:
        path = urlsplit(href).path
    except Exception:
        return None
    segs = [s for s in path.split("/") if s]
    if len(segs) != 5 or segs[0] != "tennis":
        return None
    return {"tour_hint": segs[1].replace("-", " "), "tournament": segs[2].replace("-", " ")}


def parse_tennis_page(html: str, page_date: str) -> list[dict[str, Any]]:
    rows = parse_listing_page(
        html, source_label=SOURCE_NAME, is_match_link=_is_match_link, page_date=page_date,
    )
    for row in rows:
        row["bookmaker"] = BOOK_LABEL
        row["source"] = SOURCE_NAME
        url = str(row.pop("match_url", "") or "")
        row["event_id"] = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return rows


def _fetch_live(fetch_day: str) -> list[dict[str, Any]]:
    html = fetch_page_html(TENNIS_URL, SOURCE_NAME)
    if not html:
        return []
    return parse_tennis_page(html, fetch_day)


def fetch_betexplorer_rows(target_date: str) -> list[dict[str, Any]]:
    """Priced upcoming rows for target_date (fetch day only), TheOddsAPI-shaped."""
    if os.getenv(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    fetch_day = date.today().isoformat()
    if str(target_date)[:10] != fetch_day:
        logger.info("BetExplorer serves the day page only; no rows for %s.", target_date)
        return []
    try:
        rows = cached_fetch("betexplorer_tennis", lambda: _fetch_live(fetch_day))
    except Exception as exc:
        logger.warning("BetExplorer fetch failed: %s", exc)
        return []
    return [dict(r) for r in rows] if rows else []
