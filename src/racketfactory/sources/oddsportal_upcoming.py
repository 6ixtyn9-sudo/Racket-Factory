"""OddsPortal upcoming-tennis odds adapter (match pages, not the archive).

The existing capture_oddsportal.py mines the historical results archive per
tournament/year. This module is the complementary live leg: it reads the
server-rendered day listings — /tennis/ for today, /tennis/tomorrow/ for
tomorrow — for match links, then prices each unfinished match from its
detail page. The listings themselves carry no odds (every upcoming row
renders "- -" cells, verified 2026-09-13 from two independent fetches),
while match pages render the full per-bookmaker Home/Away table
server-side — so the listing is link discovery only.

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
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources._page_odds import (
    MAX_DECIMAL_ODDS,
    MIN_DECIMAL_ODDS,
    fetch_page_html,
    parse_listing_page,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.oddsportal.com"
TODAY_PATH = "/tennis/"
TOMORROW_PATH = "/tennis/tomorrow/"
SOURCE_NAME = "OddsPortal"
BOOK_LABEL = "OddsPortal best odds"
DISABLE_ENV = "RACKET_FACTORY_DISABLE_ODDSPORTAL_UPCOMING"
# A day's unfinished matches (~30-100 links); each costs one throttled
# match-page fetch, so bound the worst case per run.
MAX_MATCH_PAGES = 60

_CHALLENGE_MARKERS = ("Just a moment", "Attention Required", "cf_chl", "cf_clearance")
# Clickable odds on a match page always route through a betslip URL and
# nothing else on the page does, so these links self-identify the Home/Away
# prices without depending on table/div markup.
_BETSLIP_HREF = "/betslip/"


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


def _betslip_link_count(node: Tag) -> int:
    n = 0
    for anchor in node.find_all("a", href=True):
        if _BETSLIP_HREF in str(anchor.get("href") or ""):
            n += 1
    return n


def _betslip_decimals(node: Tag) -> list[float]:
    vals: list[float] = []
    for anchor in node.find_all("a", href=True):
        if _BETSLIP_HREF not in str(anchor.get("href") or ""):
            continue
        try:
            val = float(anchor.get_text(" ", strip=True))
        except ValueError:
            continue
        if MIN_DECIMAL_ODDS <= val <= MAX_DECIMAL_ODDS:
            vals.append(val)
    return vals


def _row_decimals_generic(text: str) -> list[float]:
    import re
    vals: list[float] = []
    for tok in re.findall(r"\b(\d{1,2}\.\d{1,2})\b", text):
        try:
            v = float(tok)
        except ValueError:
            continue
        if MIN_DECIMAL_ODDS <= v <= MAX_DECIMAL_ODDS:
            vals.append(v)
    return vals


def _betslip_pairs(box: Tag) -> list[tuple[float, float]]:
    """(1, 2) price pairs, grouped by row so sides can never shift.

    Pairing consecutive page-wide links would misattribute sides whenever a
    book suspends one side, so pairs only form inside a single row: a ``tr``
    with exactly two betslip prices, or (div-grid markup) the smallest box
    around the link holding exactly two. Rows with any other count are
    ignored rather than guessed. Falls back to generic decimal-row scan when
    betslip links are absent (OddsPortal markup change observed 2026-09-13:
    Payout marker present but betslip count 0).
    """
    pairs: list[tuple[float, float]] = []
    seen: set[int] = set()
    has_betslip = False
    for anchor in box.find_all("a", href=True):
        if _BETSLIP_HREF in str(anchor.get("href") or ""):
            has_betslip = True
            break
    if has_betslip:
        for anchor in box.find_all("a", href=True):
            if _BETSLIP_HREF not in str(anchor.get("href") or ""):
                continue
            tr = anchor.find_parent("tr")
            if tr is not None:
                if id(tr) in seen:
                    continue
                seen.add(id(tr))
                vals = _betslip_decimals(tr)
                if len(vals) == 2:
                    pairs.append((vals[0], vals[1]))
                continue
            node = anchor.parent
            for _ in range(4):
                if node is None or getattr(node, "name", None) in ("html", "body", "[document]"):
                    break
                if isinstance(node, Tag) and _betslip_link_count(node) == 2:
                    if id(node) not in seen:
                        seen.add(id(node))
                        vals = _betslip_decimals(node)
                        if len(vals) == 2:
                            pairs.append((vals[0], vals[1]))
                    break
                node = getattr(node, "parent", None)
        if pairs:
            return pairs
    # Fallback: generic row scan for exactly 2 decimals per row inside the payout box
    for tr in box.find_all("tr"):
        if id(tr) in seen:
            continue
        txt = tr.get_text(" ", strip=True)
        if "Payout" in txt or "Bookmaker" in txt:
            continue
        vals = _row_decimals_generic(txt)
        if len(vals) == 2:
            pairs.append((vals[0], vals[1]))
    if not pairs:
        for div in box.find_all("div"):
            txt = div.get_text(" ", strip=True)
            if len(txt) > 200:
                continue
            vals = _row_decimals_generic(txt)
            if len(vals) == 2:
                if id(div) in seen:
                    continue
                seen.add(id(div))
                pairs.append((vals[0], vals[1]))
    return pairs


def parse_match_page_odds(html: str) -> tuple[float | None, float | None]:
    """Best Home/Away prices from a match page's bookmaker table.

    The odds grid is scoped via its ``Payout`` header cell so previous-match
    sections elsewhere on the page can never leak in; without the marker the
    whole page is scanned (row grouping still applies). Best-across-books
    per side. (None, None) when no complete pair is found.
    """
    if not html:
        return None, None
    soup = BeautifulSoup(html, "html.parser")
    box: Tag = soup
    for marker in soup.find_all(string=lambda s: isinstance(s, str) and s.strip() == "Payout"):
        node = marker.parent
        for _ in range(6):
            if node is None or getattr(node, "name", None) in ("html", "body", "[document]"):
                break
            if isinstance(node, Tag) and _betslip_link_count(node) >= 2:
                box = node
                break
            node = getattr(node, "parent", None)
        if box is not soup:
            break
    pairs = _betslip_pairs(box)
    if not pairs:
        return None, None
    return max(p[0] for p in pairs), max(p[1] for p in pairs)


def parse_tennis_page(html: str, page_date: str) -> list[dict[str, Any]]:
    """Listing stage: unfinished match links with names; odds filled later."""
    if _is_challenge_page(html):
        logger.warning("OddsPortal served a challenge page; no upcoming rows.")
        return []
    return parse_listing_page(
        html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
        page_date=page_date, require_odds=False,
    )


def _page_for_target(target: str) -> tuple[str, str] | None:
    """(page path, cache key) or None when the target is out of range."""
    today = date.today()
    if target == today.isoformat():
        return TODAY_PATH, "oddsportal_upcoming_today"
    if target == (today + timedelta(days=1)).isoformat():
        return TOMORROW_PATH, "oddsportal_upcoming_tomorrow"
    return None


def _strip_fragment(href: str) -> str:
    return href.split("#", 1)[0]


def _fetch_live(path: str, page_date: str) -> list[dict[str, Any]]:
    html = fetch_page_html(BASE_URL + path, SOURCE_NAME)
    if not html:
        return []
    links = parse_tennis_page(html, page_date)
    rows: list[dict[str, Any]] = []
    n_live = n_failed = 0
    for link in links[:MAX_MATCH_PAGES]:
        href = str(link.get("match_url") or "")
        if "inplay-odds" in href:
            # Already live: the page shows live odds, not pre-match prices.
            n_live += 1
            continue
        mhtml = fetch_page_html(urljoin(BASE_URL, _strip_fragment(href)), SOURCE_NAME)
        logger.info("OddsPortal match %s - %s: %d bytes payout=%s betslip=%d challenged=%s",
                    link.get("player_home"), link.get("player_away"), len(mhtml),
                    "Payout" in mhtml, mhtml.count(_BETSLIP_HREF),
                    _is_challenge_page(mhtml))
        best = parse_match_page_odds(mhtml) if mhtml else (None, None)
        if best[0] is None or best[1] is None:
            n_failed += 1
            continue
        link["odds_home"] = best[0]
        link["odds_away"] = best[1]
        link["bookmaker"] = BOOK_LABEL
        link["source"] = SOURCE_NAME
        match_url = str(link.pop("match_url", "") or "")
        link["event_id"] = match_url.rstrip("/").rsplit("/", 1)[-1] if match_url else ""
        rows.append(link)
    logger.info("OddsPortal match pages %s: candidates=%d priced=%d live_skipped=%d failed=%d",
                page_date, min(len(links), MAX_MATCH_PAGES), len(rows), n_live, n_failed)
    return rows


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
