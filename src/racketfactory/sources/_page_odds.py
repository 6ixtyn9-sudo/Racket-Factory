"""Shared helpers for scraping odds-comparison listing pages.

BetExplorer and OddsPortal both render their tennis day pages server-side:
one row per match with a match-detail link plus the two-way (1/2) prices as
plain decimals. This module holds the tolerant row scanner both site adapters
use; site-specific URL shapes and gating live in the adapter modules.

Deliberately conservative: rows without exactly-two plausible decimals are
skipped, finished/retired/walkover rows are skipped (only actionable
pre-match prices are wanted), and anything unexpected returns [] (fail-soft)
instead of raising into the pipeline.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_DECIMAL_RE = re.compile(r"\b(\d{1,2}\.\d{1,2})\b")
_DATE_DMY_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_NAME_SEP_RE = re.compile(r"\s+[–—-]\s+")
_FINISHED_RE = re.compile(r"\b(FIN\b|FINISHED|RET\.?|RETIRED|W\.?\s?O\.?|WALKOVER|AWARDED|CANCELLED)", re.IGNORECASE)

MIN_DECIMAL_ODDS = 1.01
MAX_DECIMAL_ODDS = 51.0


def fetch_page_html(url: str, source_label: str, *, timeout: int = 30) -> str:
    """GET a listing page; "" on any failure (fail-soft by contract)."""
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(
            url, timeout=timeout, impersonate="chrome",
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
        )
    except Exception as exc:
        logger.warning("%s fetch failed for %s: %s", source_label, url, exc)
        return ""
    try:
        status = resp.status_code
    except Exception:
        return ""
    if status != 200:
        logger.warning("%s fetch %s: HTTP %s", source_label, url, status)
        return ""
    try:
        return resp.text or ""
    except Exception:
        return ""


def _sanitize_row_text(text: str) -> str:
    text = _DATE_DMY_RE.sub(" ", text)  # "13.09.2026" would fake a 13.09 odd
    text = _TIME_RE.sub(" ", text)
    return text


def _row_decimals(row_text: str) -> list[float]:
    vals: list[float] = []
    for tok in _DECIMAL_RE.findall(_sanitize_row_text(row_text)):
        try:
            v = float(tok)
        except ValueError:
            continue
        if MIN_DECIMAL_ODDS <= v <= MAX_DECIMAL_ODDS:
            vals.append(v)
    return vals


def _row_container(link: Tag) -> Tag | None:
    for tag in ("tr", "li"):
        parent = link.find_parent(tag)
        if parent is not None:
            return parent
    return link.parent if isinstance(link.parent, Tag) else None


def split_match_names(link_text: str) -> tuple[str, str]:
    """Split 'Home Player - Away Player' on the score-safe separator."""
    parts = _NAME_SEP_RE.split(link_text.strip(), maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip(), parts[1].strip()


def parse_listing_page(
    html: str,
    *,
    source_label: str,
    is_match_link: Callable[[str], dict[str, Any] | None],
    page_date: str,
) -> list[dict[str, Any]]:
    """Scan a day-listing page for (names, 1, 2) match rows.

    ``is_match_link(href)`` returns a context dict (tournament/tour/...) for
    match-detail links, else None. Odds are the first two plausible decimals
    in the match's row once the names/date/time text is removed.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_finished = 0
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        ctx = is_match_link(href)
        if ctx is None:
            continue
        names_text = link.get_text(" ", strip=True)
        home, away = split_match_names(names_text)
        if not home or not away:
            continue
        container = _row_container(link)
        row_text = container.get_text(" ", strip=True) if container else names_text
        if _FINISHED_RE.search(row_text):
            skipped_finished += 1
            continue
        # Remove the names themselves so hyphenated/second-decimal name parts
        # can never leak into the odds scan; then take the first two decimals.
        scan_text = row_text.replace(names_text, " ")
        decimals = _row_decimals(scan_text)
        if len(decimals) < 2:
            continue
        key = f"{home}\x00{away}"
        if key in seen:
            continue
        seen.add(key)
        ko = _TIME_RE.search(row_text)
        rows.append({
            "match_date": page_date,
            "match_time": ko.group(0) if ko else "",
            "player_home": home,
            "player_away": away,
            "odds_home": decimals[0],
            "odds_away": decimals[1],
            "tournament": str(ctx.get("tournament") or ""),
            "tour_hint": str(ctx.get("tour_hint") or ""),
            "match_url": href,
        })
    if skipped_finished:
        logger.info("%s: skipped %d finished rows on %s", source_label, skipped_finished, page_date)
    logger.info("%s: parsed %d upcoming priced rows for %s", source_label, len(rows), page_date)
    return rows
