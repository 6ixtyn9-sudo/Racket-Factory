"""BetExplorer tennis odds adapter (Edge-Factory parity).

Transport (proven by Edge-Factory on the same runners): plain requests with
a simple UA first, 3s per-host throttle, dated results URLs for any date::

    /tennis/results/?year=YYYY&month=MM&day=DD

Listing parse is Edge-style primary (``tr.js-tournament`` headers +
``tr[data-dt]`` match rows + ``td.table-main__tt`` names) with the generic
row scanner as fallback. Listing prices are indicative consensus only;
they do not establish an executable single-bookmaker ticket quote.

Finished/retired/walkover rows are skipped: only actionable pre-match
prices are wanted. Disable with RACKET_FACTORY_DISABLE_BETEXPLORER=1.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources import _page_odds as po
from racketfactory.sources._page_odds import fetch_page_html, parse_listing_page, PageFetchError

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


def parse_results_page(html: str, page_date: str, *, metrics: dict | None = None, include_unpriced: bool = False) -> list[dict[str, Any]]:
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
        # Collect all match-link anchors in this row
        candidates: list[tuple] = []
        for a in tr.find_all("a", href=True):
            href = str(a.get("href") or "")
            ctx = _is_match_link(href)
            if ctx is None:
                continue
            txt = a.get_text(" ", strip=True)
            if not txt or len(txt) < 2:
                parent_td = a.find_parent("td")
                if parent_td is not None:
                    txt = parent_td.get_text(" ", strip=True)
            if txt:
                candidates.append((a, txt, ctx, href))

        chosen = None
        chosen_home = chosen_away = ""
        chosen_ctx = None
        chosen_href = ""

        # 1) Try single anchor that contains both players (dash or fused)
        for a, txt, ctx, href in candidates:
            h, aw = po.split_match_names(txt)
            if h and aw:
                chosen = a
                chosen_home, chosen_away = h, aw
                chosen_ctx = ctx
                chosen_href = href
                break
            row_tmp = tr.get_text(" ", strip=True)
            h2, aw2 = po.split_match_names(row_tmp)
            if h2 and aw2:
                chosen = a
                chosen_home, chosen_away = h2, aw2
                chosen_ctx = ctx
                chosen_href = href
                break

        # 2) Fallback: two separate anchors, each a single player / doubles team
        # Handles <a>Borisiouk M.</a> vs <a>Kim D. J.</a> or <a>Collins K.</a> vs <a>Yashina E.</a>
        # 2026-09-17: extended to full names without initials (e.g. Alcaraz Carlos) and loose check
        if chosen is None and len(candidates) >= 2:
            def _looks_like_player_or_team(t: str) -> bool:
                tt = t.strip()
                if not tt or len(tt) < 2:
                    return False
                if re.fullmatch(r"\d+:\d+", tt):
                    return False
                if tt.lower() in {"winner", "odds", "result", "finished", "live"}:
                    return False
                try:
                    if po._looks_like_player(tt):
                        return True
                except Exception:
                    pass
                try:
                    if po._looks_like_player_loose(tt):
                        return True
                except Exception:
                    pass
                if "/" in tt:
                    try:
                        if po._looks_like_doubles_team(tt):
                            return True
                    except Exception:
                        pass
                    # Fallback: slash with dot or space
                    if "." in tt or " " in tt:
                        return len(tt) >= 5
                # Single player heuristic: allow full names (two caps words) or initial
                if len(tt) >= 3 and tt[0].isupper():
                    if "." in tt or " " in tt:
                        return True
                    # Single capitalized surname with len>=3 (rare but possible)
                    if len(tt) >= 3 and tt.isalpha():
                        return True
                return False

            player_cands = [(a, txt, ctx, href) for a, txt, ctx, href in candidates if _looks_like_player_or_team(txt)]
            # Deduplicate by text to avoid same anchor twice
            seen_txt = set()
            uniq_cands = []
            for cand in player_cands:
                txt = cand[1].strip()
                if txt.lower() not in seen_txt:
                    seen_txt.add(txt.lower())
                    uniq_cands.append(cand)
            player_cands = uniq_cands
            if len(player_cands) >= 2:
                a1, txt1, ctx1, href1 = player_cands[0]
                a2, txt2, ctx2, href2 = player_cands[1]
                if txt1 != txt2 and len(txt1) >= 2 and len(txt2) >= 2 and href1 == href2:
                    # Clean trailing odds if present
                    txt1_clean = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", txt1).strip()
                    txt2_clean = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", txt2).strip()
                    chosen = a1
                    chosen_home = txt1_clean or txt1
                    chosen_away = txt2_clean or txt2
                    chosen_ctx = ctx1 or ctx2
                    chosen_href = href1 or href2

        if chosen is None:
            n_bad += 1
            # Capture only a bounded redacted description of the actual row;
            # unknown markup remains incomplete, never a verified non-fixture.
            try:
                from racketfactory.rebuild_diagnostics import capture_unrecognized_listing_row
                capture_unrecognized_listing_row(tr, page_date)
            except (OSError, ValueError, TypeError):
                logger.warning("Could not retain unrecognized BetExplorer row evidence")
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
            if not include_unpriced:
                continue
            decimals = [None, None]
        key = chosen_href
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
    if metrics is not None:
        metrics.update({"data_dt_rows": n_dt, "parsed_priced_rows": sum(r.get("odds_home") is not None for r in rows), "discovered_rows": len(rows), "finished_rows": n_finished,
                        "no_odds_rows": n_no_odds, "unrecognized_rows": n_bad})
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
        url = str(row.get("match_url") or "")
        row["event_id"] = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return rows


def _results_url(target: str) -> str:
    y, m, d = target.split("-")
    return RESULTS_URL.format(y=y, m=m, d=d)


MAX_MATCH_PAGES = 60
# A detail plus comparison consumes two requests within the old 60-page budget.
MAX_COMPARISON_FIXTURES = MAX_MATCH_PAGES // 2


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


def _fetch_live(target: str, *, with_status: bool = False, metrics: dict | None = None):
    def done(rows, status):
        return (rows, status) if with_status else rows

    # 1) Try dated results page with odds (historical shape)
    try:
        html = fetch_page_html(_results_url(target), SOURCE_NAME, prefer_plain=True)
        observed_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        if isinstance(exc, PageFetchError):
            raise
        logger.warning("%s dated fetch failed for %s: %s", SOURCE_NAME, target, exc)
        html = ""
    if html:
        from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
        capture_betexplorer_fragment(html, "listing")
    parse_metrics: dict[str, int] = {}
    rows = parse_results_page(html, target, metrics=parse_metrics, include_unpriced=with_status) if html else []
    if metrics is not None:
        metrics.update(parse_metrics)
    if rows:
        for row in rows:
            row["observed_at"] = observed_at
        logger.info("%s: dated results shape hit for %s (%d rows)", SOURCE_NAME, target, len(rows))
        partial = bool(parse_metrics.get("unrecognized_rows") or (not with_status and parse_metrics.get("no_odds_rows")))
        for row in rows:
            row["_collection_partial"] = partial
        return done(_finalize(rows), "partial" if partial else "complete")

    # An unrecognized dated row remains unresolved even when no other rows
    # parsed. Neither a generic fallback nor an empty-board marker can prove
    # that this row is non-actionable or make the dated listing complete.
    if parse_metrics.get("unrecognized_rows"):
        if with_status:
            return done([], "partial")
        raise PageFetchError("parser", "BetExplorer dated listing contains unrecognized rows")

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

    from racketfactory.kickoff import SAST
    if target == datetime.now(SAST).date().isoformat():
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
                    html = day_html  # preserve verified empty-board evidence


    if not link_rows:
        if not html:
            raise PageFetchError("transport", "BetExplorer listings unavailable")
        if not any(marker in html.lower() for marker in ("no matches", "no events")):
            raise PageFetchError("parser", "BetExplorer listing has no recognised fixtures or empty-board marker")
        return done([], "complete")

    if with_status:
        # Guarded rebuild uses this listing as discovery, never an indiscriminate
        # match-page crawl. Detail fetches are selected from the prediction pool.
        for row in link_rows:
            row["observed_at"] = observed_at
        return done(_finalize(link_rows), "complete")

    # 3) Price each link via its match page (bounded)
    priced: list[dict[str, Any]] = []
    n_live = n_failed = 0
    for link in link_rows[:MAX_MATCH_PAGES]:
        href = str(link.get("match_url") or "")
        if not href:
            continue
        full_url = href if href.startswith("http") else (BASE_URL + href if href.startswith("/") else href)
        mhtml = fetch_page_html(full_url, SOURCE_NAME, prefer_plain=True)
        observed_at = datetime.now(timezone.utc).isoformat()
        if mhtml:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
            capture_betexplorer_fragment(mhtml, "detail")
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
        link["observed_at"] = observed_at
        priced.append(link)

    logger.info("%s match pages %s: candidates=%d priced=%d live_skipped=%d failed=%d",
                SOURCE_NAME, target, min(len(link_rows), MAX_MATCH_PAGES), len(priced), n_live, n_failed)
    if priced:
        partial = bool(n_failed or len(link_rows) > MAX_MATCH_PAGES)
        for row in priced:
            row["_collection_partial"] = partial
        return done(_finalize(priced), "partial" if partial else "complete")

    if n_failed:
        raise PageFetchError("parser", "BetExplorer fixture pages contained no usable paired prices")
    return done([], "unknown")


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
    rows = cached_fetch(f"betexplorer_v6_{target}", lambda: _fetch_live(target))
    return [dict(r) for r in rows] if rows else []


def _comparison_endpoint_candidates(html: str, event_id: str, *, metrics: dict | None = None) -> list[dict]:
    """Discover *literal* tennis comparison URLs and explicit tab metadata.

    No football endpoint or missing market/scope is inferred. Only same-host,
    HTTPS URLs for this event, with an innocuous language parameter, are used.
    """
    from urllib.parse import urljoin, urlparse, parse_qsl
    soup = BeautifulSoup(html, "html.parser")
    found = []
    contexts = {}
    def record(key):
        if metrics is not None:
            metrics[key] = metrics.get(key, 0) + 1
    for node in soup.select('[data-url], [data-endpoint], [data-ajax-url], a[href]'):
        record("endpoint_nodes_scanned")
        raw = next((str(node.get(k) or "") for k in ("data-url", "data-endpoint", "data-ajax-url", "href") if node.get(k)), "")
        url = urljoin(BASE_URL, raw)
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "www.betexplorer.com" or parsed.username or parsed.password
                or parsed.fragment or not parsed.path.startswith("/match-odds/")
                or event_id not in parsed.path.split("/")
                or any(k != "lang" or v != "en" for k, v in parse_qsl(parsed.query, keep_blank_values=True))):
            record("endpoint_url_rejected")
            continue
        market = str(node.get("data-market") or node.get("data-market-name") or "").strip().lower()
        period = str(node.get("data-period") or node.get("data-scope") or "").strip().lower()
        market_id = str(node.get("data-market-id") or "")
        # Compare explicit contexts for the *same validated endpoint* before
        # rejecting unsupported markets/periods. Plain links with no context
        # are not assertions about scope. Only the aliases already accepted by
        # this parser are normalized; unsupported labels remain distinct.
        market_key = "match winner" if market in {"match winner", "match_winner", "match-winner", "h2h"} else market
        period_key = "match" if period in {"match", "full match", "full_match", "full-match"} else period
        context = contexts.setdefault(url, {"market": set(), "period": set(), "market_id": set()})
        for key, value in (("market", market_key), ("period", period_key), ("market_id", market_id)):
            if value:
                context[key].add(value)
        if market not in {"match winner", "match_winner", "match-winner", "h2h"}:
            record("endpoint_market_rejected")
            continue
        if period not in {"match", "full match", "full_match", "full-match"}:
            record("endpoint_period_rejected")
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{0,32}", market_id):
            record("endpoint_market_id_rejected")
            continue
        found.append({"url": url, "raw_market": market, "raw_period": period, "market_id": market_id})
    # A page with competing explicit contexts is ambiguous, not a first hit.
    if any(any(len(values) > 1 for values in contexts[item["url"]].values()) for item in found):
        record("endpoint_conflicting_context")
        return []
    unique = {}
    for item in found:
        previous = unique.get(item["url"])
        if previous is None:
            unique[item["url"]] = item
    if len(unique) > 1:
        record("endpoint_ambiguous_urls")
    return list(unique.values()) if len(unique) == 1 else []


def _comparison_response_rows(payload: str, fixture: dict, context: dict, observed_at: str) -> tuple[list[dict], dict]:
    """Parse two explicitly labelled tennis columns and individual book rows.

    The format covered here is synthetic until actual tennis fragments validate
    the provider contract. A comparison average is never a bookmaker offer.
    """
    import hashlib
    import json
    import math
    from racketfactory.warehouse import names_match, strict_quote_team_match
    if (context.get("raw_market") not in {"match winner", "match_winner", "match-winner", "h2h"}
            or context.get("raw_period") not in {"match", "full match", "full_match", "full-match"}):
        raise PageFetchError("parser", "comparison market or scope not established")
    metrics = {"bookmaker_rows": 0, "offers": 0, "average_rows": 0,
               "missing_bookmaker": 0, "invalid_price": 0, "duplicate_rows": 0,
               "ambiguous_columns": 0, "ambiguous_bookmaker_rows": 0, "average_prices": []}
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        raise PageFetchError("parser", "comparison response is not JSON")
    html = data.get("odds") if isinstance(data, dict) else None
    if not isinstance(html, str):
        raise PageFetchError("parser", "comparison response has no odds HTML")
    soup = BeautifulSoup(html, "html.parser")
    home, away = str(fixture.get("player_home") or ""), str(fixture.get("player_away") or "")
    if not home or not away or names_match(home, away):
        raise PageFetchError("parser", "ambiguous fixture participants")
    digest = hashlib.sha256(payload.encode()).hexdigest()
    offers, seen, conflicts = [], {}, set()
    def unique_book(book_id, book, prices):
        key = book_id or book.casefold()
        value = (book.casefold(), tuple(prices))
        if key in seen:
            if seen[key] == value:
                metrics["duplicate_rows"] += 1
            else:
                conflicts.add(key)
                metrics["ambiguous_bookmaker_rows"] += 1
            return False
        seen[key] = value
        return True
    for table in soup.find_all("table"):
        thead = table.find("thead")
        headers = thead.find_all("th") if thead else []
        labels = [x.get_text(" ", strip=True) for x in headers]
        hidx = [i for i, text in enumerate(labels) if strict_quote_team_match(home, text)]
        aidx = [i for i, text in enumerate(labels) if strict_quote_team_match(away, text)]
        extra_outcome = any(i not in hidx + aidx and (n.get("data-pos") or labels[i].strip().lower() in {"1", "2", "x", "draw", "tie"})
                            for i, n in enumerate(headers))
        if len(hidx) != 1 or len(aidx) != 1 or hidx[0] == aidx[0] or extra_outcome:
            metrics["ambiguous_columns"] += 1
            continue
        for row in table.select("tbody tr"):
            classes = " ".join(row.get("class") or []).lower()
            if "average" in classes or (row.find("td") and row.find("td").get_text(" ", strip=True).lower() in {"average", "avg"}):
                metrics["average_rows"] += 1
                avg_cells = row.find_all("td", recursive=False)
                if max(hidx[0], aidx[0]) < len(avg_cells):
                    try:
                        avg_nodes = [avg_cells[i].find(attrs={"data-odd": True}) or avg_cells[i] for i in (hidx[0], aidx[0])]
                        avg = [float(n.get("data-odd") or n.get_text(" ", strip=True)) for n in avg_nodes]
                        if all(math.isfinite(v) and v > 1 for v in avg) and len(metrics["average_prices"]) < 2:
                            metrics["average_prices"].append(avg)
                    except (TypeError, ValueError, OverflowError):
                        pass
                continue
            cells = row.find_all("td", recursive=False)
            if max(hidx[0], aidx[0]) >= len(cells):
                continue
            if any((c.has_attr("data-odd") or c.find(attrs={"data-odd": True}))
                   for i, c in enumerate(cells) if i not in (hidx[0], aidx[0])):
                metrics["ambiguous_columns"] += 1
                continue
            metrics["bookmaker_rows"] += 1
            logo = row.select_one('img[title], a[title]')
            bookmaker = str(row.get("data-bookmaker-name") or (logo.get("title") if logo else "") or "").strip()
            book_id = str(row.get("data-bookie") or "").strip()
            if not bookmaker or bookmaker.lower() in {"betexplorer_best", "betexplorer best"}:
                metrics["missing_bookmaker"] += 1
                continue
            try:
                price_nodes = [cells[i].find(attrs={"data-odd": True}) or cells[i] for i in (hidx[0], aidx[0])]
                for i, node in zip((hidx[0], aidx[0]), price_nodes):
                    if headers[i].get("data-pos") and node.get("data-pos") and str(headers[i].get("data-pos")) != str(node.get("data-pos")):
                        raise ValueError("conflicting position")
                prices = [float(n.get("data-odd")) for n in price_nodes]
                if not all(math.isfinite(v) and v > 1 for v in prices):
                    raise ValueError("invalid decimal")
            except (TypeError, ValueError, OverflowError):
                metrics["invalid_price"] += 1
                continue
            if not unique_book(book_id, bookmaker, prices):
                continue
            offers.append({**{k: fixture.get(k) for k in ("event_id", "match_url", "match_date", "match_time", "commence_time", "player_home", "player_away", "tournament", "tour_hint")},
                "odds_home": prices[0], "odds_away": prices[1], "bookmaker": bookmaker, "bookmaker_id": book_id, "odds_home_bookmaker": bookmaker, "odds_away_bookmaker": bookmaker,
                "source": SOURCE_NAME, "market": "match_winner", "period": "match", "line": None,
                "raw_market": context["raw_market"], "raw_period": context["raw_period"], "market_id": context.get("market_id"),
                "observed_at": observed_at, "retrieved_at": observed_at,
                "provider_price_updated_at": [n.get("data-created") for n in price_nodes],
                "provider_positions": [n.get("data-pos") for n in price_nodes],
                "response_sha256": digest, "price_type": "bookmaker", "settlement_rules": None})
    # Hidden all-bookmaker rows are accepted only when one table explicitly
    # binds participant headings to data-pos tokens used by the hidden cells.
    header_maps = []
    for table in soup.find_all("table"):
        head = table.find("thead")
        heads = head.find_all("th") if head else []
        hm = [str(n.get("data-pos") or "") for n in heads if strict_quote_team_match(home, n.get_text(" ", strip=True))]
        am = [str(n.get("data-pos") or "") for n in heads if strict_quote_team_match(away, n.get_text(" ", strip=True))]
        if len(hm) == len(am) == 1 and hm[0] and am[0] and hm[0] != am[0] and sum(bool(n.get("data-pos")) for n in heads) == 2:
            header_maps.append((hm[0], am[0]))
    if len(header_maps) == 1:
        hpos, apos = header_maps[0]
        for row in soup.find_all("div", class_=lambda c: c and "oddsComparisonAll__rowBookie" in str(c)):
            if "average" in " ".join(row.get("class") or []).lower():
                metrics["average_rows"] += 1
                continue
            metrics["bookmaker_rows"] += 1
            logo = row.select_one('img[title], a[title]')
            book = str(row.get("data-bookmaker-name") or (logo.get("title") if logo else "") or "").strip()
            book_id = str(row.get("data-bookie") or "").strip()
            if not book or book.lower() in {"betexplorer_best", "betexplorer best"}:
                metrics["missing_bookmaker"] += 1
                continue
            nodes = row.find_all(attrs={"data-odd": True})
            h = [n for n in nodes if str(n.get("data-pos") or "") == hpos]
            a = [n for n in nodes if str(n.get("data-pos") or "") == apos]
            try:
                if len(h) != 1 or len(a) != 1 or len(nodes) != 2: raise ValueError("ambiguous position")
                prices = [float(h[0].get("data-odd")), float(a[0].get("data-odd"))]
                if not all(math.isfinite(v) and v > 1 for v in prices): raise ValueError("invalid price")
            except (TypeError, ValueError, OverflowError):
                metrics["invalid_price"] += 1
                continue
            if not unique_book(book_id, book, prices):
                continue
            offers.append({**{k: fixture.get(k) for k in ("event_id", "match_url", "match_date", "match_time", "commence_time", "player_home", "player_away", "tournament", "tour_hint")},
                "odds_home": prices[0], "odds_away": prices[1], "bookmaker": book, "bookmaker_id": book_id, "odds_home_bookmaker": book, "odds_away_bookmaker": book,
                "source": SOURCE_NAME, "market": "match_winner", "period": "match", "line": None,
                "raw_market": context["raw_market"], "raw_period": context["raw_period"], "market_id": context.get("market_id"),
                "observed_at": observed_at, "retrieved_at": observed_at,
                "provider_price_updated_at": [h[0].get("data-created"), a[0].get("data-created")],
                "provider_positions": [h[0].get("data-pos"), a[0].get("data-pos")],
                "response_sha256": digest, "price_type": "bookmaker", "settlement_rules": None})
    offers = [row for row in offers if (row.get("bookmaker_id") or row["bookmaker"].casefold()) not in conflicts]
    metrics["offers"] = len(offers)
    return offers, metrics


def _fetch_comparison_document(url: str, referer: str | None = None) -> tuple[str, str]:
    """Single throttled request, no alternate route on a restriction."""
    import requests
    from racketfactory.quota_guard import cooldown_status, record_cooldown
    host = urlsplit(url).netloc.lower()
    if cooldown_status("betexplorer", "host:" + host):
        raise PageFetchError("rate_limit", "BetExplorer cooldown active")
    po._throttle(host)
    headers = {"User-Agent": po.EDGE_SIMPLE_UA, "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    try:
        response = requests.get(url, timeout=30, headers=headers, allow_redirects=False)
    except requests.RequestException as exc:
        raise PageFetchError("transport", type(exc).__name__) from exc
    when = datetime.now(timezone.utc).isoformat()
    if response.status_code == 429:
        from email.utils import parsedate_to_datetime
        raw = str(response.headers.get("Retry-After") or "")
        try:
            wait = float(raw)
        except ValueError:
            try:
                wait = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                wait = 900
        try:
            record_cooldown("betexplorer", "host:" + host, "rate_limit", max(1, wait))
        except OSError:
            logger.warning("BetExplorer cooldown could not be persisted")
    body = response.text or ""
    def refusal(kind: str, detail: str):
        import hashlib
        exc = PageFetchError(kind, detail)
        exc.http_status, exc.observed_at = response.status_code, when
        exc.response_sha256 = hashlib.sha256(body.encode()).hexdigest()
        raise exc
    if response.status_code in (401, 403, 429):
        refusal("challenge" if response.status_code in (401, 403) else "rate_limit", f"HTTP {response.status_code}")
    if response.status_code != 200:
        refusal("http", f"HTTP {response.status_code}")
    if any(x in body[:6000].lower() for x in ("cf-chl", "just a moment", "attention required", "verify you are human")):
        refusal("challenge", "verification page")
    return body, when


def fetch_fixture_comparison(fixture: dict, *, refresh_evidence: bool = False, stage_counts: dict | None = None,
                             capture_replay: bool = False) -> list[dict]:
    """Fetch and cache one prediction-matched fixture, retaining timestamps."""
    from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
    event_id = str(fixture.get("event_id") or "")
    href = str(fixture.get("match_url") or "")
    if (not re.fullmatch(r"[A-Za-z0-9]{1,32}", event_id) or not href.startswith("/tennis/")
            or "\\" in href or urlsplit(href).query or urlsplit(href).fragment
            or not _is_match_link(href) or href.rstrip('/').split('/')[-1] != event_id):
        raise PageFetchError("parser", "fixture URL or event identity invalid")
    fetched = False
    def count(key, amount=1):
        if stage_counts is not None:
            stage_counts[key] = stage_counts.get(key, 0) + amount
    def fetch():
        nonlocal fetched
        fetched = True
        count("match_page_attempts")
        # Per-fixture variables are reset on every invocation, including errors.
        match_url = BASE_URL + href
        try:
            page, page_at = _fetch_comparison_document(match_url)
        except PageFetchError as exc:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "detail", exc.kind, status=getattr(exc, "http_status", None), observed_at=getattr(exc, "observed_at", ""), response_sha256=getattr(exc, "response_sha256", ""))
            exc.stage = "match_page"
            raise
        count("match_page_success")
        capture_betexplorer_fragment(page, "detail", event_id=event_id, observed_at=page_at, status=200)
        if capture_replay:
            from racketfactory.betexplorer_evidence import capture_replay as save_replay
            capture_result = save_replay(page, "detail", fixture, observed_at=page_at, request_url=match_url)
            if capture_result["file_written"]:
                count("replay_detail_file_written")
            if capture_result["discovery_material_retained"]:
                count("replay_detail_material_retained")
            if capture_result["material_sufficiency"] == "insufficient":
                count("replay_detail_insufficient")
        discovery_metrics = {}
        endpoints = _comparison_endpoint_candidates(page, event_id, metrics=discovery_metrics)
        if capture_replay and stage_counts is not None and stage_counts.get("replay_detail_file_written"):
            # Compare against the captured DOM, without implying that an empty
            # result proves the omitted JavaScript is sufficient for a new parser.
            import json
            from racketfactory.rebuild_diagnostics import directory
            replay_file = directory() / ("betexplorer_replay_" + event_id + ".json")
            try:
                evidence = json.loads(replay_file.read_text())
                replay = _comparison_endpoint_candidates(evidence["detail"]["replay_html"], event_id)
                agrees = replay == endpoints
                replay_status = ("match_nonempty" if agrees and endpoints else
                                 "match_empty_inconclusive" if agrees else "mismatch")
                evidence["detail"]["current_parser_replay_agreement"] = replay_status
                evidence["detail"]["same_current_discovery_result"] = agrees
                evidence["detail"]["empty_discovery_does_not_prove_future_parser_fidelity"] = not bool(endpoints)
                if not agrees:
                    evidence["detail"]["material_sufficiency"] = "insufficient"
                    categories = evidence["detail"].setdefault("omission_categories", [])
                    if "current_parser_replay_mismatch" not in categories:
                        categories.append("current_parser_replay_mismatch")
                    count("replay_detail_insufficient")
                replay_file.write_text(json.dumps(evidence, indent=2, ensure_ascii=True), encoding="utf-8")
                if agrees and endpoints:
                    count("replay_discovery_agrees_nonempty")
                elif agrees:
                    count("replay_discovery_empty_inconclusive")
                else:
                    count("replay_discovery_mismatch")
            except (OSError, ValueError, KeyError, TypeError):
                count("replay_comparison_failed")
                count("replay_detail_insufficient")
        for key, amount in discovery_metrics.items():
            count(key, amount)
        if not endpoints:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "detail", "parser")
            error = PageFetchError("parser", "tennis match-winner endpoint or scope not explicitly established")
            error.stage = "endpoint_discovery"
            raise error
        count("comparison_endpoint_discovered")
        count("comparison_attempts")
        try:
            payload, observed_at = _fetch_comparison_document(endpoints[0]["url"], referer=match_url)
        except PageFetchError as exc:
            if getattr(exc, "http_status", None) is not None:
                count("comparison_http_response_observed")
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "comparison", exc.kind, status=getattr(exc, "http_status", None), observed_at=getattr(exc, "observed_at", ""), response_sha256=getattr(exc, "response_sha256", ""))
            exc.stage = "comparison_request"
            raise
        count("comparison_response_success")
        count("comparison_http_response_observed")
        try:
            import json
            html = json.loads(payload).get("odds", "")
        except (ValueError, AttributeError):
            html = ""
        if not isinstance(html, str):
            html = ""
        capture_betexplorer_fragment(html or "<div></div>", "comparison", event_id=event_id, observed_at=observed_at, status=200, response_body=payload)
        if capture_replay:
            from racketfactory.betexplorer_evidence import capture_replay as save_replay
            result = save_replay(html, "comparison", fixture, observed_at=observed_at,
                                 response_body=payload, request_url=endpoints[0]["url"])
            if result["file_written"]:
                count("replay_comparison_file_written")
            if result["discovery_material_retained"]:
                count("replay_comparison_material_retained")
            if result["material_sufficiency"] == "insufficient":
                count("replay_comparison_insufficient")
        try:
            rows, metrics = _comparison_response_rows(payload, fixture, endpoints[0], observed_at)
        except PageFetchError as exc:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "comparison", exc.kind, status=200, observed_at=observed_at)
            exc.stage = "comparison_parse"
            raise
        count("comparison_parsed")
        count("concrete_bookmaker_rows", metrics.get("bookmaker_rows", 0))
        count("identified_offers", len(rows))
        if not capture_replay:
            from racketfactory.execution import rebuild_quote_execution_problem
            count("settlement_compatible_offers", sum(not rebuild_quote_execution_problem(r) for r in rows))
        from racketfactory.rebuild_diagnostics import capture_comparison_offers
        capture_comparison_offers(event_id, rows, metrics)
        logger.info("BetExplorer comparison parse counts: %s", metrics)
        if not rows:
            error = PageFetchError("parser", "no uniquely oriented named-bookmaker quotes")
            error.stage = "bookmaker_rows"
            raise error
        return rows
    rows = cached_fetch("betexplorer_detail_" + event_id, fetch, fetch_day=str(fixture.get("match_date") or "")[:10], ttl_minutes=0 if refresh_evidence else 30)
    if not fetched:
        count("detail_cache_hits")
    from racketfactory.warehouse import strict_quote_team_match
    if any(str(row.get("event_id") or "") != event_id or row.get("match_url") != href
           or not strict_quote_team_match(str(row.get("player_home") or ""), str(fixture.get("player_home") or ""))
           or not strict_quote_team_match(str(row.get("player_away") or ""), str(fixture.get("player_away") or "")) for row in rows):
        raise PageFetchError("parser", "cached fixture identity mismatch")
    return rows
