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
MAX_MATCH_PAGES = 120
# Additional category pages to broaden coverage (especially for qualification/Challenger)
TODAY_EXTRA_PATHS = [
    "/tennis/next/",
    "/tennis/atp/",
    "/tennis/wta/",
    "/tennis/challenger/",
    "/tennis/itf-men/",
    "/tennis/itf-women/",
]
TOMORROW_EXTRA_PATHS = [
    "/tennis/tomorrow/next/",
    "/tennis/atp/tomorrow/",
    "/tennis/wta/tomorrow/",
    "/tennis/challenger/tomorrow/",
]

_CHALLENGE_MARKERS = ("Just a moment", "Attention Required", "cf_chl", "cf_clearance")
# Clickable odds on a match page always route through a betslip URL and
# nothing else on the page does, so these links self-identify the Home/Away
# prices without depending on table/div markup.
_BETSLIP_HREF = "/betslip/"


def _is_match_link(href: str) -> dict[str, Any] | None:
    """Match links: tolerant to markup changes (h2h, match, or player-slug pages).

    Historically /tennis/h2h/<a>/<b>/, but live listings have been observed
    with /tennis/match/<id>/, /tennis/<tournament>/<slug>/<id>/ and similar
    shapes. Category pages like /tennis/usa/atp-us-open/ (3 segs, all lowercase)
    must be excluded — they caused bad_names=16 parsed=0 in run 6ffa8c3a.

    Heuristic:
    - /tennis/h2h/... or /tennis/match/... always allowed
    - 3-seg /tennis/<x>/<y>/ where x is atp/wta/challenger/etc or y is tournament slug (all lowercase, no digit) -> reject (category)
    - 4+ segs under /tennis/ -> allow (match pages like /tennis/usa/atp-us-open/<player-slug>/ or h2h)
    - Otherwise reject, letting split_match_names filter non-player anchors.
    """
    try:
        path = urlsplit(href).path
        # Keep original case for digit/upper heuristic, but lower for comparisons
        path_lower = path.lower()
    except Exception:
        return None
    segs = [s for s in path.split("/") if s]
    segs_lower = [s for s in path_lower.split("/") if s]
    if not segs or segs_lower[0] != "tennis":
        return None
    if len(segs_lower) == 1:
        return None  # /tennis/

    def _id_like(seg: str) -> bool:
        # Event/player IDs carry digits or uppercase; category slugs are
        # pure lowercase alpha+hyphen. (SPA shape observed 2026-09-18:
        # /tennis/<event-id>/ 2-seg and /tennis/<cat>/<event-id>/ 3-seg.)
        return any(c.isdigit() or c.isupper() for c in seg)

    if len(segs_lower) == 2:
        second = segs_lower[1]
        if second in {"tomorrow", "next", "atp", "wta", "challenger", "itf-men", "itf-women", "results", "standings", "live", "my-matches", "rankings", "news", "stats", "calendar"}:
            return None
        if _id_like(segs[1]):
            return {"tour_hint": "", "tournament": ""}  # /tennis/<event-id>/
        return None
    if len(segs_lower) == 3:
        # /tennis/atp/tomorrow/, /tennis/wta/tomorrow/, etc
        if segs_lower[1] in {"atp", "wta", "challenger", "itf-men", "itf-women", "tomorrow", "next"}:
            return None
        # Category like /tennis/usa/atp-us-open/ -> 3 segs, all lowercase, no digit, no uppercase -> reject
        # Match like /tennis/match/<id> -> segs[1]=match, allow
        if segs_lower[1] in {"match", "h2h"}:
            return {"tour_hint": "", "tournament": ""}
        # Event-id shaped last seg (digit/uppercase) -> match, allow
        if _id_like(segs[2]):
            return {"tour_hint": segs[1].replace("-", " "), "tournament": ""}
        # FIX: Allow all-lowercase player slugs like "shelton-ben" or "alcaraz-carlos-sinner-jannik"
        # These have hyphens and look like 2 names (e.g. surname-firstname or surname-surname)
        # Tournament slugs like "atp-us-open" have 3+ parts and contain tour keywords
        last = segs[2]
        last_lower = segs_lower[2]
        # If last seg is all lowercase alpha+hyphen, check if it looks like player vs player
        if last.islower() and "-" in last and len(last) >= 5:
            parts = last.split("-")
            # Player slug: 2-4 parts, each >=2 chars, no tour keywords
            tour_keywords = {"atp", "wta", "challenger", "itf", "men", "women", "open", "masters", "championships", "cup", "trophy"}
            has_tour_kw = any(p in tour_keywords for p in parts)
            # If contains tour keyword and 3+ parts, likely tournament category -> reject
            if has_tour_kw and len(parts) >= 3:
                return None
            # If 2 parts and both look like names (e.g. shelton-ben), allow
            if len(parts) >= 2 and all(len(p) >= 2 and p.isalpha() for p in parts[:4]):
                return {"tour_hint": segs[1].replace("-", " "), "tournament": ""}
            # If 4+ parts and looks like player1 vs player2 (e.g. alcaraz-carlos-sinner-jannik -> 4 parts)
            if len(parts) >= 4 and all(len(p) >= 2 for p in parts):
                # Check if first 2 and last 2 could be names (surname-firstname pattern)
                return {"tour_hint": segs[1].replace("-", " "), "tournament": ""}
        if last.islower() and last.replace("-", "").replace("_", "").isalpha() and len(last) >= 3:
            # Pure lowercase alpha hyphen, no digit, no uppercase
            # Could be tournament slug like "atp-us-open" -> reject
            # But if it has exactly 2 hyphen parts and looks like names, allow (handled above)
            # Otherwise reject to avoid bad_names
            return None
        # Otherwise, be conservative and reject 3-seg unknown to avoid bad_names
        return None
    # len >=4
    if segs_lower[1] == "h2h" or segs_lower[1] == "match":
        return {"tour_hint": "", "tournament": ""}
    # Generic 4+ segs: /tennis/usa/atp-us-open/<player> or /tennis/<cat>/<tour>/<player>/<id>
    # Allow, but still need to avoid obvious non-match like /tennis/rankings/...
    if segs_lower[1] in {"rankings", "news", "stats", "calendar"}:
        return None
    # If last seg is pure tournament (all lowercase alpha hyphen) and no player-like second last, might still be category with 4 segs like /tennis/usa/atp-us-open/results/ ?
    # Check if last two segs are both lowercase alpha hyphen -> likely not match
    # But allow if any seg contains digit or uppercase (player ID)
    # FIX: Also allow all-lowercase player slugs without ID (new OddsPortal markup)
    has_id_like = any(any(c.isupper() or c.isdigit() for c in seg) for seg in segs[2:])
    if not has_id_like:
        last = segs[-1]
        # Allow if last seg contains hyphen and looks like player names (2+ parts, each >=2 chars)
        if "-" in last and len(last) >= 5:
            parts = last.split("-")
            if len(parts) >= 2 and all(len(p) >= 2 for p in parts[:4]):
                # Exclude obvious tournament categories with tour keywords + 3+ parts
                tour_keywords = {"atp", "wta", "challenger", "itf", "men", "women", "open", "masters", "championships", "cup", "trophy", "results", "standings", "live", "rankings"}
                # If last seg itself is a known category word, reject
                if last.lower() in tour_keywords:
                    return None
                # If last seg contains tour keyword and 3+ parts, likely tournament -> reject
                # But if it looks like 4 parts names (e.g. alcaraz-carlos-sinner-jannik), allow
                if len(parts) >= 3:
                    has_tour_kw = any(p in tour_keywords for p in parts)
                    if has_tour_kw and len(parts) >= 3 and not (len(parts) >= 4 and all(p.isalpha() for p in parts)):
                        # Check if it's tournament slug like atp-us-open (has atp + open)
                        if "open" in parts or "masters" in parts or "championships" in parts:
                            return None
                return {"tour_hint": segs[1].replace("-", " ") if len(segs) >= 2 else "", "tournament": segs[2].replace("-", " ") if len(segs) >= 3 else ""}
        return None
    return {"tour_hint": segs[1].replace("-", " ") if len(segs) >= 2 else "", "tournament": segs[2].replace("-", " ") if len(segs) >= 3 else ""}


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


def _extract_data_odd_pairs(node: Tag) -> list[tuple[float, float]]:
    """Extract (1,2) pairs from data-odd attributes grouped by row."""
    pairs: list[tuple[float, float]] = []
    seen: set[int] = set()
    for tr in node.find_all("tr"):
        if id(tr) in seen:
            continue
        vals: list[float] = []
        for attr in ("data-odd", "data-opening-odd", "data-closing-odd", "data-odd-value"):
            for el in tr.find_all(attrs={attr: True}):
                try:
                    v = float(str(el.get(attr) or "").strip())
                except Exception:
                    continue
                if MIN_DECIMAL_ODDS <= v <= MAX_DECIMAL_ODDS:
                    vals.append(v)
        # Also check direct td text if data-odd gave <2
        if len(vals) < 2:
            txt = tr.get_text(" ", strip=True)
            if "Payout" in txt or "Bookmaker" in txt:
                continue
            if "Previous Matches" in txt or "Head to Head" in txt:
                continue
            generic = _row_decimals_generic(txt)
            for g in generic:
                if g not in vals:
                    vals.append(g)
        if len(vals) == 2:
            seen.add(id(tr))
            pairs.append((vals[0], vals[1]))
    return pairs


def _betslip_pairs(box: Tag) -> list[tuple[float, float]]:
    """(1, 2) price pairs, grouped by row so sides can never shift.

    Pairing consecutive page-wide links would misattribute sides whenever a
    book suspends one side, so pairs only form inside a single row: a ``tr``
    with exactly two betslip prices, or (div-grid markup) the smallest box
    around the link holding exactly two. Rows with any other count are
    ignored rather than guessed. Falls back to generic decimal-row scan when
    betslip links are absent (OddsPortal markup change observed 2026-09-13:
    Payout marker present but betslip count 0). Now also scans data-odd
    attributes and div-based odds grids.
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

    # Fallback 1: data-odd attribute pairs (new markup without betslip href)
    data_pairs = _extract_data_odd_pairs(box)
    if data_pairs:
        return data_pairs

    # Fallback 2: generic row scan for exactly 2 decimals per row inside the payout box
    for tr in box.find_all("tr"):
        if id(tr) in seen:
            continue
        txt = tr.get_text(" ", strip=True)
        if "Payout" in txt or "Bookmaker" in txt:
            continue
        if "Previous Matches" in txt or "Head to Head" in txt:
            continue
        vals = _row_decimals_generic(txt)
        if len(vals) == 2:
            pairs.append((vals[0], vals[1]))
    if pairs:
        return pairs

    # Fallback 3: div-based odds (OddsPortal div-grid markup)
    for div in box.find_all("div"):
        txt = div.get_text(" ", strip=True)
        if len(txt) > 500:
            continue
        if "Payout" in txt or "Bookmaker" in txt or "Previous Matches" in txt:
            continue
        if txt.count(".") > 10:
            continue
        vals = _row_decimals_generic(txt)
        if len(vals) == 2:
            if id(div) in seen:
                continue
            seen.add(id(div))
            pairs.append((vals[0], vals[1]))
    if pairs:
        return pairs

    # Fallback 4: scan any element with class containing 'odd' for 2 decimals
    for el in box.find_all(class_=lambda c: c and "odd" in str(c).lower()):
        txt = el.get_text(" ", strip=True)
        vals = _row_decimals_generic(txt)
        if len(vals) == 2:
            parent_tr = el.find_parent("tr")
            if parent_tr is not None and id(parent_tr) in seen:
                continue
            pairs.append((vals[0], vals[1]))

    return pairs


def parse_match_page_odds(html: str) -> tuple[float | None, float | None]:
    """Best Home/Away prices from a match page's bookmaker table.

    The odds grid is scoped via its ``Payout`` header cell so previous-match
    sections elsewhere on the page can never leak in; without the marker the
    whole page is scanned (row grouping still applies). Best-across-books
    per side. (None, None) when no complete pair is found. Now robust to
    betslip=0 markup changes: if Payout exists but betslip links are gone,
    we still locate the odds table via row-decimal heuristics.
    """
    if not html:
        return None, None
    soup = BeautifulSoup(html, "html.parser")
    box: Tag = soup
    found_payout = False
    for marker in soup.find_all(string=lambda s: isinstance(s, str) and s.strip() == "Payout"):
        found_payout = True
        node = marker.parent
        for _ in range(8):
            if node is None or getattr(node, "name", None) in ("html", "body", "[document]"):
                break
            if isinstance(node, Tag):
                if _betslip_link_count(node) >= 2:
                    box = node
                    break
                # Fallback: node contains at least 2 rows with 2 decimals each
                tr_with_odds = 0
                for tr in node.find_all("tr"):
                    txt = tr.get_text(" ", strip=True)
                    if "Payout" in txt or "Bookmaker" in txt:
                        continue
                    if len(_row_decimals_generic(txt)) == 2:
                        tr_with_odds += 1
                    if tr_with_odds >= 2:
                        box = node
                        break
                if box is not soup:
                    break
                if node.name == "table":
                    cnt = 0
                    for tr in node.find_all("tr"):
                        if len(_row_decimals_generic(tr.get_text(" ", strip=True))) == 2:
                            cnt += 1
                    if cnt >= 1:
                        box = node
                        break
            node = getattr(node, "parent", None)
        if box is not soup:
            break

    # If no Payout marker, try to find any table with many odds rows
    if box is soup and not found_payout:
        best_table = None
        best_count = 0
        for table in soup.find_all("table"):
            cnt = 0
            for tr in table.find_all("tr"):
                txt = tr.get_text(" ", strip=True)
                if len(_row_decimals_generic(txt)) == 2:
                    cnt += 1
            if cnt > best_count and cnt >= 2:
                best_count = cnt
                best_table = table
        if best_table is not None:
            box = best_table

    pairs = _betslip_pairs(box)
    if not pairs:
        # Last resort: scan whole page if Payout box gave nothing but page has odds
        if box is not soup:
            pairs = _betslip_pairs(soup)
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
        return TODAY_PATH, "oddsportal_upcoming_today_v6"
    if target == (today + timedelta(days=1)).isoformat():
        return TOMORROW_PATH, "oddsportal_upcoming_tomorrow_v6"
    return None


def _strip_fragment(href: str) -> str:
    return href.split("#", 1)[0]


def _fetch_via_jina(url: str) -> str:
    """Fetch via Jina reader relay when JINA_API_KEY is available (Cloudflare bypass)."""
    import os, urllib.request, urllib.error
    key = (os.getenv("JINA_API_KEY") or os.getenv("JINA_READER_API_KEY") or "").strip()
    if not key:
        return ""
    try:
        from racketfactory.quota_guard import check_and_spend as _q_spend
        if not _q_spend("jina"):
            logger.warning("quota jina exhausted — skipping OddsPortal relay fetch for %s", url)
            return ""
    except Exception:
        pass
    try:
        relay_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(relay_url, headers={
            "Authorization": f"Bearer {key}",
            "X-Return-Format": "html",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:
        logger.debug("OddsPortal Jina relay failed for %s: %s", url, exc)
        return ""

def _fetch_live(path: str, page_date: str, *, extra_paths: list[str] | None = None) -> list[dict[str, Any]]:
    # Primary page - try Jina relay first when available (GitHub Actions is blocked by Cloudflare)
    url = BASE_URL + path
    html = _fetch_via_jina(url)
    if not html:
        html = fetch_page_html(url, SOURCE_NAME)
    all_links: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    if html:
        links = parse_tennis_page(html, page_date)
        for lk in links:
            key = f"{lk.get('player_home')}\x00{lk.get('player_away')}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_links.append(lk)

    # Extra category pages (broadens coverage for Challenger/ITF) - Jina first
    for extra in (extra_paths or []):
        try:
            eurl = BASE_URL + extra
            ehtml = _fetch_via_jina(eurl)
            if not ehtml:
                ehtml = fetch_page_html(eurl, SOURCE_NAME)
            if not ehtml:
                continue
            elinks = parse_tennis_page(ehtml, page_date)
            for lk in elinks:
                key = f"{lk.get('player_home')}\x00{lk.get('player_away')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_links.append(lk)
        except Exception as exc:
            logger.debug("OddsPortal extra path %s failed: %s", extra, exc)
            continue

    rows: list[dict[str, Any]] = []
    n_live = n_failed = 0
    for link in all_links[:MAX_MATCH_PAGES]:
        href = str(link.get("match_url") or "")
        if "inplay-odds" in href:
            n_live += 1
            continue
        murl = urljoin(BASE_URL, _strip_fragment(href))
        mhtml = _fetch_via_jina(murl)
        if not mhtml:
            mhtml = fetch_page_html(murl, SOURCE_NAME)
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
    logger.info("OddsPortal match pages %s: candidates=%d priced=%d live_skipped=%d failed=%d (extra_paths=%d)",
                page_date, min(len(all_links), MAX_MATCH_PAGES), len(rows), n_live, n_failed, len(extra_paths or []))
    return rows


def _get_oddsportal_ttl_minutes() -> float:
    try:
        hours = float(os.getenv("RACKET_FACTORY_ODDSPORTAL_TTL_HOURS", "2"))
        return hours * 60.0
    except Exception:
        return 120.0


def fetch_oddsportal_upcoming_rows(target_date: str) -> list[dict[str, Any]]:
    """Priced upcoming rows for target_date (today/tomorrow), TheOddsAPI-shaped."""
    if os.getenv(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    target = str(target_date)[:10]
    page = _page_for_target(target)
    if page is None:
        logger.info("OddsPortal upcoming serves today/tomorrow only; no rows for %s.", target)
        return []
    p_path, cache_key = page
    today = date.today().isoformat()
    extra = TODAY_EXTRA_PATHS if target == today else TOMORROW_EXTRA_PATHS if target == (date.today() + timedelta(days=1)).isoformat() else []
    ttl = _get_oddsportal_ttl_minutes()
    try:
        rows = cached_fetch(cache_key, lambda: _fetch_live(p_path, target, extra_paths=extra), ttl_minutes=ttl)
    except Exception as exc:
        logger.warning("OddsPortal upcoming fetch failed: %s", exc)
        return []
    return [dict(r) for r in rows] if rows else []
