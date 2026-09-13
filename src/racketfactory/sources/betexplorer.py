"""BetExplorer tennis odds adapter (Edge-Factory parity).

Transport (proven by Edge-Factory on the same runners): plain requests with
a simple UA first, 3s per-host throttle, dated results URLs for any date::

    /tennis/results/?year=YYYY&month=MM&day=DD

Listing parse is Edge-style primary (``tr.js-tournament`` headers +
``tr[data-dt]`` match rows + ``td.table-main__tt`` names) with the generic
row scanner as fallback. The listing 1/2 prices track the bookmaker
consensus (spot checked 2026-09-13 against Betway SA: within ~2-4%), so
they are usable as indicative market prices for EV gating and acca
building. Display them as indicative, never as the ticket price.

Finished/retired/walkover rows are skipped: only actionable pre-match
prices are wanted. Disable with RACKET_FACTORY_DISABLE_BETEXPLORER=1.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources import _page_odds as po
from racketfactory.sources._page_odds import fetch_page_html, parse_listing_page

logger = logging.getLogger(__name__)

BASE_URL = "https://www.betexplorer.com"
RESULTS_URL = BASE_URL + "/tennis/results/?year={y}&month={m}&day={d}"
TENNIS_URL = BASE_URL + "/tennis/"
SOURCE_NAME = "BetExplorer"
BOOK_LABEL = "BetExplorer consensus"
DISABLE_ENV = "RACKET_FACTORY_DISABLE_BETEXPLORER"

_DT_RE = re.compile(r"(\d+),(\d+),(\d+),(\d+),(\d+)")


def _is_match_link(href: str) -> dict[str, Any] | None:
    """Match links are /tennis/<cat>/<tournament>/<slug>/<id>/ (5 segments)."""
    try:
        path = urlsplit(href).path
    except Exception:
        return None
    segs = [s for s in path.split("/") if s]
    if len(segs) != 5 or segs[0] != "tennis":
        return None
    return {"tour_hint": segs[1].replace("-", " "), "tournament": segs[2].replace("-", " ")}


def parse_results_page(html: str, page_date: str) -> list[dict[str, Any]]:
    """Edge-style primary parse: js-tournament headers + data-dt rows."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    tournament = tour_hint = ""
    n_dt = n_finished = 0
    for tr in soup.find_all("tr"):
        classes = tr.get("class") or []
        if "js-tournament" in classes:
            text = tr.get_text(" ", strip=True)
            label = text.split(":")[-1].strip() if ":" in text else text.strip()
            tournament = label or tournament
            tour_hint = ""
            continue
        dt_raw = tr.get("data-dt")
        if not dt_raw:
            continue
        n_dt += 1
        name_cell = tr.find("td", class_="table-main__tt")
        link = name_cell.find("a", href=True) if name_cell else tr.find("a", href=True)
        if link is None:
            continue
        href = str(link.get("href") or "")
        ctx = _is_match_link(href)
        names_text = link.get_text(" ", strip=True)
        home, away = po.split_match_names(names_text)
        if not home or not away:
            continue
        row_text = tr.get_text(" ", strip=True)
        if po._FINISHED_RE.search(row_text):
            n_finished += 1
            continue
        decimals = po._row_decimals(row_text.replace(names_text, " "))
        if len(decimals) < 2:
            continue
        key = f"{home}\x00{away}"
        if key in seen:
            continue
        seen.add(key)
        ko = ""
        m = _DT_RE.search(str(dt_raw))
        if m:
            ko = f"{int(m.group(4)):02d}:{int(m.group(5)):02d}"
        rows.append({
            "match_date": page_date,
            "match_time": ko,
            "player_home": home,
            "player_away": away,
            "odds_home": decimals[0],
            "odds_away": decimals[1],
            "tournament": (ctx.get("tournament") if ctx else "") or tournament,
            "tour_hint": (ctx.get("tour_hint") if ctx else "") or tour_hint,
            "match_url": href,
        })
    logger.info("%s results parse %s: data-dt rows=%d parsed=%d finished=%d",
                SOURCE_NAME, page_date, n_dt, len(rows), n_finished)
    return rows


def parse_tennis_page(html: str, page_date: str) -> list[dict[str, Any]]:
    """Day-page parse (generic scanner; fallback when results shape misses)."""
    rows = parse_listing_page(
        html, source_label=SOURCE_NAME, is_match_link=_is_match_link, page_date=page_date,
    )
    return _finalize(rows)


def _finalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["bookmaker"] = BOOK_LABEL
        row["source"] = SOURCE_NAME
        url = str(row.pop("match_url", "") or "")
        row["event_id"] = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return rows


def _results_url(target: str) -> str:
    y, m, d = target.split("-")
    return RESULTS_URL.format(y=y, m=m, d=d)


def _fetch_live(target: str) -> list[dict[str, Any]]:
    try:
        html = fetch_page_html(_results_url(target), SOURCE_NAME, prefer_plain=True)
    except Exception as exc:
        logger.warning("%s dated fetch failed for %s: %s", SOURCE_NAME, target, exc)
        html = ""
    rows = parse_results_page(html, target) if html else []
    if rows:
        logger.info("%s: dated results shape hit for %s (%d rows)", SOURCE_NAME, target, len(rows))
        return _finalize(rows)
    if target != date.today().isoformat():
        return []
    # Last resort for today: the day page with the generic scanner.
    logger.info("%s: dated shape missed for %s; trying day page", SOURCE_NAME, target)
    html = fetch_page_html(TENNIS_URL, SOURCE_NAME, prefer_plain=True)
    if not html:
        return []
    return parse_tennis_page(html, target)


def fetch_betexplorer_rows(target_date: str) -> list[dict[str, Any]]:
    """Priced upcoming rows for target_date (any date), TheOddsAPI-shaped."""
    if os.getenv(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    target = str(target_date)[:10]
    try:
        parts = target.split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return []
    except Exception:
        return []
    try:
        rows = cached_fetch(f"betexplorer_{target}", lambda: _fetch_live(target))
    except Exception as exc:
        logger.warning("BetExplorer fetch failed: %s", exc)
        return []
    return [dict(r) for r in rows] if rows else []
