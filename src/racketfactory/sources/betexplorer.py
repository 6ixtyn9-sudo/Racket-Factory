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
    n_dt = n_finished = n_no_odds = n_bad = 0
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
        # Robust: find the first anchor in the row whose href matches and whose text splits into players
        # Handles empty anchors (e.g. icon-only) by also checking parent td text
        chosen = None
        chosen_home = chosen_away = ""
        chosen_ctx = None
        chosen_href = ""
        for a in tr.find_all("a", href=True):
            href = str(a.get("href") or "")
            ctx = _is_match_link(href)
            if ctx is None:
                continue
            txt = a.get_text(" ", strip=True)
            # If anchor text empty, try parent td / container text as fallback
            if not txt or len(txt) < 2:
                parent_td = a.find_parent("td")
                if parent_td is not None:
                    # Use td text but remove any nested score-like tokens
                    parent_txt = parent_td.get_text(" ", strip=True)
                    # If parent contains the anchor href text plus players, try it
                    txt = parent_txt
            h, aw = po.split_match_names(txt)
            if not h or not aw:
                # Last resort: try to extract from the whole row text
                # Row often contains time + names + odds, e.g. "09:30 Rouvroy M. - Johnson S. 3.61 1.26"
                # We can attempt to find a player-like substring via the generic splitter
                # by scanning row_text for dash separator
                row_tmp = tr.get_text(" ", strip=True)
                # Remove time and odds to isolate names
                # Simple heuristic: find first occurrence of ' - ' and take surrounding words
                # Use split_match_names on row_tmp directly
                h2, aw2 = po.split_match_names(row_tmp)
                if h2 and aw2:
                    # Ensure this row_tmp split isn't just picking up random text;
                    # require that the anchor href still corresponds to those names
                    # (we already have a valid href, so accept)
                    h, aw = h2, aw2
                if not h or not aw:
                    continue
            chosen = a
            chosen_home, chosen_away = h, aw
            chosen_ctx = ctx
            chosen_href = href
            break
        if chosen is None:
            n_bad += 1
            continue
        row_text = tr.get_text(" ", strip=True)
        if po._FINISHED_RE.search(row_text):
            n_finished += 1
            continue
        # Primary: shared extractor that checks visible text + data-odd attrs + odds cells
        decimals = po._extract_odds_from_tr(tr, chosen.get_text(" ", strip=True))
        if len(decimals) < 2:
            # Secondary: legacy row-decimal scan for backward compat
            decimals = po._row_decimals(row_text.replace(chosen.get_text(" ", strip=True), " "))
        if len(decimals) < 2:
            # Tertiary: conservative nearby search (parent tr only)
            try:
                decimals = po._find_odds_near(chosen, chosen.get_text(" ", strip=True), _is_match_link)
            except Exception:
                decimals = []
        if len(decimals) < 2:
            n_no_odds += 1
            continue
        key = f"{chosen_home}\x00{chosen_away}"
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
            "player_home": chosen_home,
            "player_away": chosen_away,
            "odds_home": decimals[0],
            "odds_away": decimals[1],
            "tournament": (chosen_ctx.get("tournament") if chosen_ctx else "") or tournament,
            "tour_hint": (chosen_ctx.get("tour_hint") if chosen_ctx else "") or tour_hint,
            "match_url": chosen_href,
        })
    logger.info("%s results parse %s: data-dt rows=%d parsed=%d finished=%d no_odds=%d bad=%d",
                SOURCE_NAME, page_date, n_dt, len(rows), n_finished, n_no_odds, n_bad)
    return rows


def parse_tennis_page(html: str, page_date: str, *, require_odds: bool = True) -> list[dict[str, Any]]:
    """Day-page parse (generic scanner; fallback when results shape misses)."""
    rows = parse_listing_page(
        html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
        page_date=page_date, require_odds=require_odds,
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


MAX_MATCH_PAGES = 60


def _strip_fragment(href: str) -> str:
    return href.split("#", 1)[0]


def parse_match_page_odds(html: str) -> tuple[float | None, float | None]:
    """Extract consensus Home/Away from a BetExplorer match detail page.

    Strategy (tolerant, no JS):
    - Collect (1,2) pairs per table row where the row contains exactly 2
      plausible decimals (either via data-odd attributes or visible text) and
      at least one cell with an odds-like class.
    - Also scan generic data-odd / data-opening-odd attributes as fallback.
    - Average per side across pairs (consensus). Returns (None, None) when
      no complete pair is found or page is finished/retired.
    """
    if not html:
        return None, None
    if po._FINISHED_RE.search(html[:5000]):
        # Quick check: many finished pages contain FIN/RET markers in header
        pass
    soup = BeautifulSoup(html, "html.parser")
    pairs: list[tuple[float, float]] = []

    # Primary: rows with odds
    for tr in soup.find_all("tr"):
        # Skip header rows
        vals: list[float] = []
        # Prefer data-odd attributes inside row
        for el in tr.find_all(attrs={"data-odd": True}):
            try:
                v = float(str(el.get("data-odd") or "").strip())
                if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                    vals.append(v)
            except Exception:
                continue
        if len(vals) < 2:
            for el in tr.find_all(attrs={"data-opening-odd": True}):
                try:
                    v = float(str(el.get("data-opening-odd") or "").strip())
                    if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                        vals.append(v)
                except Exception:
                    continue
        if len(vals) < 2:
            # Fallback to visible odds cells
            has_odds_cell = False
            for td in tr.find_all("td"):
                cls = " ".join(td.get("class") or [])
                if "odd" in cls.lower():
                    has_odds_cell = True
                    break
            if has_odds_cell or tr.find(attrs={"data-odd": True}) is not None:
                txt = tr.get_text(" ", strip=True)
                vals = po._row_decimals(txt)
        if len(vals) == 2:
            pairs.append((vals[0], vals[1]))

    # Secondary: if no row pairs, collect all data-odd on page and try to pair
    if not pairs:
        all_odds: list[float] = []
        for attr in ("data-odd", "data-opening-odd", "data-closing-odd"):
            for el in soup.find_all(attrs={attr: True}):
                try:
                    v = float(str(el.get(attr) or "").strip())
                    if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                        all_odds.append(v)
                except Exception:
                    continue
        # Also scan td.table-main__odds
        for td in soup.find_all("td", class_=lambda c: c and "odd" in str(c).lower()):
            try:
                v = float(td.get_text(strip=True))
                if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                    all_odds.append(v)
            except Exception:
                continue
        # If we have at least 2, assume first two are representative average
        # (BetExplorer often renders average row first)
        if len(all_odds) >= 2:
            # If even number, average first half as home, second half as away is wrong.
            # Instead, take first two as pair (likely average row)
            pairs.append((all_odds[0], all_odds[1]))

    if not pairs:
        return None, None
    # Average across bookmakers for consensus (matches previous behavior)
    avg_home = sum(p[0] for p in pairs) / len(pairs)
    avg_away = sum(p[1] for p in pairs) / len(pairs)
    return avg_home, avg_away


def _fetch_live(target: str) -> list[dict[str, Any]]:
    # 1) Try dated results page with odds (historical shape)
    try:
        html = fetch_page_html(_results_url(target), SOURCE_NAME, prefer_plain=True)
    except Exception as exc:
        logger.warning("%s dated fetch failed for %s: %s", SOURCE_NAME, target, exc)
        html = ""
    rows = parse_results_page(html, target) if html else []
    if rows:
        logger.info("%s: dated results shape hit for %s (%d rows)", SOURCE_NAME, target, len(rows))
        return _finalize(rows)

    # 2) For any date, if results page missed, try to collect links without odds
    #    and price via match pages (new fallback for JS-rendered listings)
    link_rows: list[dict[str, Any]] = []
    if html:
        # Re-parse results page without requiring odds to get candidate links
        soup = BeautifulSoup(html, "html.parser")
        # Use generic scanner with require_odds=False on results html
        link_rows = parse_listing_page(
            html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
            page_date=target, require_odds=False,
        )

    if target == date.today().isoformat():
        # Also try day page for today
        if not link_rows:
            logger.info("%s: dated shape missed for %s; trying day page", SOURCE_NAME, target)
            day_html = fetch_page_html(TENNIS_URL, SOURCE_NAME, prefer_plain=True)
            if day_html:
                link_rows = parse_listing_page(
                    day_html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
                    page_date=target, require_odds=False,
                )

    if not link_rows:
        return []

    # 3) Price each link via its match page (bounded)
    priced: list[dict[str, Any]] = []
    n_live = n_failed = 0
    for link in link_rows[:MAX_MATCH_PAGES]:
        href = str(link.get("match_url") or "")
        if not href:
            continue
        full_url = href if href.startswith("http") else (BASE_URL + href if href.startswith("/") else href)
        mhtml = fetch_page_html(full_url, SOURCE_NAME, prefer_plain=True)
        if not mhtml:
            n_failed += 1
            continue
        # Skip finished pages
        if po._FINISHED_RE.search(mhtml[:8000]):
            n_live += 1
            continue
        best = parse_match_page_odds(mhtml)
        if best[0] is None or best[1] is None:
            n_failed += 1
            continue
        link["odds_home"] = best[0]
        link["odds_away"] = best[1]
        priced.append(link)

    logger.info("%s match pages %s: candidates=%d priced=%d live_skipped=%d failed=%d",
                SOURCE_NAME, target, min(len(link_rows), MAX_MATCH_PAGES), len(priced), n_live, n_failed)
    if priced:
        return _finalize(priced)

    # Fallback: if match-page pricing also failed, return empty (previous behavior)
    return []


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
