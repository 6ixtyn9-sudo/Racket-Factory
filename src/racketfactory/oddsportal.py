"""OddsPortal scraper for tennis — curl_cffi primary, Playwright fallback.

OddsPortal is protected by Cloudflare WAF. curl_cffi impersonates Chrome TLS
fingerprints to bypass the challenge. The script extracts page IDs from the
tournament page, then fetches paginated AJAX data.

URL pattern note (2026 SPA migration):
    Old:  /tennis/<country>/<tournament>/results-<year>/
    New:  /tennis/<country>/<tournament>/results/   (year moved to AJAX param)
The year is now passed to the AJAX archive endpoint as a path segment, not in
the page URL. See `_ajax_archive_url` for the new format.

This module intentionally does NOT require Playwright at import time.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


def _load_op_cookies() -> dict:
    """Load OddsPortal cookies from ODDSPORTAL_COOKIES env var.

    Supports Netscape cookies.txt format (as exported by the
    "Get cookies.txt LOCALLY" Chrome extension) and a simple
    name=value-per-line fallback.
    """
    path = os.getenv("ODDSPORTAL_COOKIES", "").strip()
    if not path:
        return {}
    try:
        cookies: dict[str, str] = {}
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    # Netscape format: domain, flag, path, secure, expiry, name, value
                    name, value = parts[5], parts[6]
                    cookies[name] = value
                elif "=" in line and ";" not in line[:40]:
                    # simple name=value per line fallback
                    k, v = line.split("=", 1)
                    cookies[k.strip()] = v.strip()
        return cookies
    except Exception:
        return {}


_OP_COOKIES = _load_op_cookies()

# URLs that returned a genuine HTTP 404 from OddsPortal's server (not CF).
# Used to skip the Playwright fallback — a 404 means the page doesn't exist,
# no amount of browser waiting will change that.
_KNOWN_404_URLS: set[str] = set()


def is_known_404(url: str) -> bool:
    """Return True if curl_cffi already got a 404 for this URL."""
    return url in _KNOWN_404_URLS

logger = logging.getLogger(__name__)

TENNIS_BASE = "https://www.oddsportal.com/tennis"

COLUMNS = [
    "match_date", "tour", "tournament", "round", "player_a", "player_b",
    "winner", "score", "odds_a", "odds_b", "bookmaker", "source",
    "captured_at", "oddsportal_url",
]

# Cloudflare challenge indicators — used to detect that a fetched page is
# actually a bot challenge rather than real content.
_CF_CHALLENGE_MARKERS = (
    "Just a moment",
    "cf-challenge",
    "cf_chl_opt",
    "cf_chl_jschl",
    "cf_clearance",
    "Attention Required! | Cloudflare",
    "Verifying you are human",
    "_cf_chl_opt",
    "challenge-platform",
    "challenge-running",
    "Checking your browser",
)

# 404 / error page detection — these pages are tiny and should not be parsed.
_ERROR_PAGE_MARKERS = (
    "<title>404",
    "<title>403",
    "<title>500",
    "<title>502",
    "<title>503",
    "<title>504",
    "Page Not Found",
    "Gateway Time-out",
)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Tennis sport code on OddsPortal (basketball=3, tennis=2)
_TENNIS_SPORT_CODE = 2

# Page ID patterns — covers legacy AJAX hydration AND post-2026 SPA hydration.
# Order matters: try the most-specific legacy patterns first, then fall through
# to generic id-blob regexes that match newer hydration formats.
_PAGE_ID_PATTERNS = [
    re.compile(r"pageOutrightsVar\s*=\s*'?\{\"id\":\"([A-Za-z0-9]+)\""),
    re.compile(r"new\s+PageTournament\(\{\"id\":\"([A-Za-z0-9]+)\""),
    re.compile(r"PageTournament\(\s*\{\s*\"id\"\s*:\s*\"([A-Za-z0-9]+)\""),
    re.compile(r"new\s+PageTournament\s*\(\s*\{[^}]{0,400}?\"id\"\s*:\s*\"([A-Za-z0-9]+)\"", re.S),
    # data attributes on the tournament container
    re.compile(r'data-page-id="([A-Za-z0-9]+)"'),
    re.compile(r'data-tournament-id="([A-Za-z0-9]+)"'),
    re.compile(r'data-event-id="([A-Za-z0-9]+)"'),
    # post-2026 hydration blobs (NEXT_DATA, pageRepo, etc.)
    re.compile(r'"tournamentId"\s*:\s*"([A-Za-z0-9]{6,12})"'),
    re.compile(r'"_tournamentId"\s*:\s*"([A-Za-z0-9]{6,12})"'),
    re.compile(r'"_tournamentUrl"\s*:\s*"[^"]*?/(\d{4,8})/?["\\]', re.S),
    re.compile(r'"_tournamentTemplateId"\s*:\s*"([A-Za-z0-9]{6,12})"'),
    # generic 6-12 char alphanumeric id (less specific, used as last resort)
    re.compile(r'"id":"([A-Za-z0-9]{6,12})"'),
    re.compile(r"'id':\s*'([A-Za-z0-9]{6,12})'"),
]


def _is_cloudflare_challenge(html: str) -> bool:
    """Return True if the HTML body looks like a CF bot-management challenge page."""
    if not html:
        return False
    snippet = html[:8000]
    return any(marker in snippet for marker in _CF_CHALLENGE_MARKERS)


def _is_error_page(html: str) -> bool:
    """Return True if the HTML body looks like a 404/5xx error page."""
    if not html:
        return True
    snippet = html[:4000]
    return any(marker in snippet for marker in _ERROR_PAGE_MARKERS)


def _extract_page_id(html: str) -> str | None:
    """Extract OddsPortal tournament page ID from a chunk of HTML."""
    if not html:
        return None
    for i, pat in enumerate(_PAGE_ID_PATTERNS):
        m = pat.search(html)
        if m:
            candidate = m.group(1)
            # The generic 6-12 char pattern can match unrelated IDs; require
            # at least 8 chars for that fallback so we don't return noise.
            if i >= len(_PAGE_ID_PATTERNS) - 2 and len(candidate) < 8:
                continue
            logger.info("oddsportal: page id matched pattern #%d: %s", i + 1, candidate)
            return candidate
    return None


def _curl_fetch(url: str, *, retries: int = 3, impersonate: str = "chrome133a") -> str:
    """Fetch URL with curl_cffi, warming CF cookies on 403.

    impersonate defaults to chrome133a — the only confirmed bypass for the
    current Cloudflare JA3 check on OddsPortal and Forebet.  chrome133a and
    earlier get HTTP 403.  Override with the ODDSPORTAL_IMPERSONATE env var
    if a newer Chrome version is needed later.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi not installed — oddsportal curl fetch disabled")
        return ""

    impersonate = os.getenv("ODDSPORTAL_IMPERSONATE", impersonate)

    cookies = dict(_OP_COOKIES) if _OP_COOKIES else None
    last_status: int | None = None
    for attempt in range(retries):
        try:
            kwargs: dict[str, Any] = {"impersonate": impersonate, "timeout": 30}
            if cookies:
                kwargs["cookies"] = cookies
            resp = cffi_requests.get(url, **kwargs)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 403:
                logger.info(
                    "oddsportal 403 on attempt %d for %s, warming cookies",
                    attempt + 1, url,
                )
                try:
                    warm = cffi_requests.get(
                        "https://www.oddsportal.com/",
                        impersonate=impersonate,
                        timeout=20,
                        cookies=_OP_COOKIES or None,
                    )
                    if warm.status_code == 200:
                        merged = dict(_OP_COOKIES)
                        merged.update(dict(warm.cookies))
                        cookies = merged
                except Exception:
                    pass
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 404:
                _KNOWN_404_URLS.add(url)
                logger.info("oddsportal 404 for %s", url)
                return ""
            logger.warning("oddsportal %s returned %s", url, resp.status_code)
        except Exception as e:
            logger.warning("oddsportal fetch error (attempt %d): %s", attempt + 1, e)
        time.sleep(2 ** attempt)
    if last_status is not None:
        logger.warning(
            "oddsportal: gave up on %s after %d retries (last status %s)",
            url, retries, last_status,
        )
    return ""


def _year_seg(year: int | None) -> str:
    """Format the year path segment for the AJAX archive URL."""
    return str(year) if year else "X0"


def _ajax_archive_url(page_id: str, page_num: int, *, year: int | None = None) -> str:
    """Primary OddsPortal archive AJAX URL for tennis.

    After the 2026 SPA migration, the year is an explicit path segment
    (replacing the legacy "X0" placeholder). If year is None we leave it as
    "X0" which historically meant "any year"; the API in practice returns
    the most recent season for that tournament, so pass a year whenever
    possible.
    """
    return (
        f"https://www.oddsportal.com/ajax-sport-country-tournament-archive_/"
        f"{_TENNIS_SPORT_CODE}/{page_id}/{_year_seg(year)}/1/0/{page_num}/"
    )


def _ajax_archive_url_candidates(
    page_id: str, page_num: int, *, year: int | None = None,
) -> list[str]:
    """Candidate AJAX URL formats, primary first."""
    return [
        _ajax_archive_url(page_id, page_num, year=year),
        # Old "X0" placeholder variant (legacy endpoints that ignore year)
        f"https://www.oddsportal.com/ajax-sport-country-tournament-archive_/"
        f"{_TENNIS_SPORT_CODE}/{page_id}/X0/1/0/{page_num}/",
        # Query-param variant some endpoints accept
        f"https://www.oddsportal.com/ajax-sport-country-tournament-archive_/"
        f"{_TENNIS_SPORT_CODE}/{page_id}/?_=&page={page_num}",
    ]


def _html_text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or " ")


def _parse_date_text(text: str, *, default_year: int | None = None) -> str | None:
    """Parse OddsPortal date labels into YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    # Try "13 Jul 2024" format
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", text)
    if not m:
        # Try "13 Jul" (no year)
        m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\b", text)
        if not m:
            return None
        day = int(m.group(1))
        mon = _MONTHS.get(m.group(2).lower())
        if not mon:
            return None
        year = default_year or date_type.today().year
    else:
        day = int(m.group(1))
        mon = _MONTHS.get(m.group(2).lower())
        year = int(m.group(3))
        if not mon:
            return None
    try:
        return date_type(year, mon, day).isoformat()
    except ValueError:
        return None


def _row_chunks(fragment: str) -> Iterable[tuple[str, str]]:
    """Yield (prefix_since_previous_row, row_html) for table-participant rows."""
    row_re = re.compile(
        r'(<tr[^>]*class="[^"]*table-participant[^"]*"[^>]*>.*?</tr>)',
        re.I | re.S,
    )
    pos = 0
    for m in row_re.finditer(fragment):
        yield fragment[pos:m.start()], m.group(1)
        pos = m.end()


def _extract_player_names(row_html: str) -> list[str]:
    """Extract two player names from a row."""
    # Method 1: <a class="name"> tags (legacy AJAX)
    names = re.findall(r'<a[^>]+class="[^"]*name[^"]*"[^>]*>([^<]+)</a>', row_html)
    if len(names) >= 2:
        return [n.strip() for n in names[:2]]
    # Method 2: data-name attributes
    names = re.findall(r'data-name="([^"]+)"', row_html)
    if len(names) >= 2:
        return [n.strip() for n in names[:2]]
    return []


def _extract_bookmakers(row_html: str) -> list[str]:
    """Extract bookmaker names from a row."""
    bms = re.findall(r'<a[^>]+class="[^"]*bookmaker[^"]*"[^>]+title="([^"]+)"', row_html)
    if bms:
        return [b.strip() for b in bms]
    bms = re.findall(r'data-bookmaker="([^"]+)"', row_html)
    return [b.strip() for b in bms]


def _extract_decimal_odds(row_html: str) -> list[float]:
    """Extract decimal odds from a row."""
    vals: list[float] = []
    # Method 1: data-oid attributes (legacy AJAX)
    for raw in re.findall(r'data-oid="([\d.]+)"', row_html):
        try:
            v = float(raw)
        except ValueError:
            continue
        if 1.01 <= v <= 1000:
            vals.append(v)
    if vals:
        return vals
    # Method 2: visible decimal odds in text (rendered pages)
    for raw in re.findall(r">\s*(\d+\.\d{1,3})\s*<", row_html):
        try:
            v = float(raw)
        except ValueError:
            continue
        if 1.01 <= v <= 1000:
            vals.append(v)
    return vals


def _extract_round(row_html: str) -> str:
    """Extract round information from a row."""
    rounds = re.findall(r'class="[^"]*round[^"]*"[^>]*>([^<]+)', row_html)
    if rounds:
        return rounds[0].strip()
    rounds = re.findall(r'data-round="([^"]+)"', row_html)
    if rounds:
        return rounds[0].strip()
    return ""


def _extract_score(row_html: str) -> str:
    """Extract final score from a row."""
    scores = re.findall(r'(\d{1,2}\s*-\s*\d{1,2}(?:\s+\d{1,2}\s*-\s*\d{1,2})*)', row_html)
    if scores:
        return scores[0].strip()
    score_els = re.findall(r'class="[^"]*score[^"]*"[^>]*>([^<]+)', row_html)
    if score_els:
        return score_els[0].strip()
    return ""


def _parse_odds_html(
    html_fragment: str,
    *,
    tour: str = "UNKNOWN",
    tournament: str = "",
    default_year: int | None = None,
    oddsportal_url: str = "",
) -> list[dict]:
    """Parse OddsPortal HTML/AJAX fragment into raw rows."""
    if not html_fragment:
        return []
    fragment = html_fragment.replace("\\/", "/").replace('\\"', '"')
    out: list[dict] = []
    current_date: str | None = None
    current_round: str = ""

    for prefix, row_html in _row_chunks(fragment):
        prefix_date = _parse_date_text(_html_text(prefix), default_year=default_year)
        if prefix_date:
            current_date = prefix_date

        round_match = re.search(r'([A-Z][a-z]+(?:\s*-\s*[A-Z][a-z]+)*)', _html_text(prefix))
        if round_match:
            current_round = round_match.group(1).strip()

        row_date = _parse_date_text(_html_text(row_html), default_year=default_year) or current_date
        if not row_date:
            continue

        players = _extract_player_names(row_html)
        if len(players) < 2:
            continue
        player_a, player_b = players[0], players[1]

        bookmaker_links = _extract_bookmakers(row_html)
        decimal_odds_list = _extract_decimal_odds(row_html)
        if not bookmaker_links or not decimal_odds_list:
            continue

        two_sided = len(decimal_odds_list) >= 2 * len(bookmaker_links)
        round_info = _extract_round(row_html) or current_round
        score_info = _extract_score(row_html)

        winner = ""
        if score_info and not re.search(r'retired|walkover|w/o|RET', score_info, re.I):
            nums = [int(x) for x in re.findall(r'\d+', score_info)]
            if len(nums) >= 2 and len(nums) % 2 == 0:
                sets_a = sum(1 for i in range(0, len(nums), 2) if nums[i] > nums[i+1])
                sets_b = sum(1 for i in range(0, len(nums), 2) if nums[i+1] > nums[i])
                if sets_a > sets_b:
                    winner = player_a
                elif sets_b > sets_a:
                    winner = player_b

        captured_at = datetime.now(timezone.utc).isoformat()

        for i, bm in enumerate(bookmaker_links):
            bm_display = bm.strip() or "OddsPortal Rendered"
            if two_sided:
                odds_a = decimal_odds_list[2 * i]
                odds_b = decimal_odds_list[2 * i + 1]
            elif i < len(decimal_odds_list):
                odds_a = decimal_odds_list[i]
                odds_b = None
            else:
                continue

            row = {
                "match_date": row_date,
                "tour": tour,
                "tournament": tournament,
                "round": round_info,
                "player_a": player_a,
                "player_b": player_b,
                "winner": winner,
                "score": score_info,
                "odds_a": odds_a,
                "odds_b": odds_b if odds_b is not None else 0.0,
                "bookmaker": bm_display,
                "source": "OddsPortal AJAX",
                "captured_at": captured_at,
                "oddsportal_url": oddsportal_url,
            }
            out.append(row)
    return out


def _parse_ajax_payload(
    payload_text: str,
    *,
    tour: str = "UNKNOWN",
    tournament: str = "",
    default_year: int | None = None,
    oddsportal_url: str = "",
) -> list[dict]:
    """Parse OddsPortal AJAX response text into raw rows."""
    if not payload_text:
        return []
    html_frag = ""
    try:
        payload = json.loads(payload_text)
        d = payload.get("d") or payload
        if isinstance(d, dict):
            html_frag = d.get("html") or d.get("result") or ""
        if not html_frag:
            html_frag = str(payload)
    except json.JSONDecodeError:
        html_frag = payload_text

    return _parse_odds_html(
        html_frag,
        tour=tour,
        tournament=tournament,
        default_year=default_year,
        oddsportal_url=oddsportal_url,
    )


def _resolve_page_id(tournament_url: str) -> str | None:
    """Fetch the tournament page and extract its page ID via curl_cffi.

    Returns None if no ID can be resolved. The caller should then try the
    Playwright-based path which can read the page ID from the rendered DOM
    after Cloudflare's challenge has been cleared.
    """
    html = _curl_fetch(tournament_url)
    if not html:
        return None
    if _is_error_page(html):
        logger.warning("oddsportal: %s returned an error page", tournament_url)
        return None
    if _is_cloudflare_challenge(html):
        logger.warning(
            "oddsportal: CF challenge page returned for %s; "
            "fall back to Playwright which can clear the challenge",
            tournament_url,
        )
        return None
    page_id = _extract_page_id(html)
    if page_id:
        return page_id
    snippet_idx = html.find("PageTournament")
    if snippet_idx > 0:
        logger.warning(
            "oddsportal: no PageTournament regex matched. context=%r",
            html[snippet_idx:snippet_idx + 200],
        )
    else:
        logger.warning(
            "oddsportal: no PageTournament marker (HTML len=%d)",
            len(html),
        )
    return None


def fetch_tournament_pages(
    tournament_url: str,
    *,
    tour: str = "UNKNOWN",
    tournament: str = "",
    default_year: int | None = None,
    max_pages: int = 50,
    sleep: float = 1.0,
    page_id: str | None = None,
) -> list[dict]:
    """Fetch all pages for a tennis tournament using curl_cffi + AJAX.

    If page_id is supplied, the resolver is skipped. If curl_cffi fails
    (Cloudflare challenge or empty response), the caller is expected to
    retry via Playwright using `_fetch_ajax_via_playwright`.
    """
    if page_id is None:
        page_id = _resolve_page_id(tournament_url)
    if not page_id:
        logger.warning("oddsportal: no page id for %s", tournament_url)
        return []

    out: list[dict] = []
    for page_num in range(1, max_pages + 1):
        page_rows: list[dict] = []
        for url in _ajax_archive_url_candidates(page_id, page_num, year=default_year):
            resp = _curl_fetch(url)
            if _is_cloudflare_challenge(resp) or _is_error_page(resp):
                logger.info(
                    "oddsportal curl: skipping %s (challenge/error page)", url,
                )
                continue
            page_rows = _parse_ajax_payload(
                resp,
                tour=tour,
                tournament=tournament,
                default_year=default_year,
                oddsportal_url=tournament_url,
            )
            if page_rows:
                break
        if not page_rows:
            break
        out.extend(page_rows)
        if len(page_rows) < 5:
            break
        if sleep:
            time.sleep(sleep)

    logger.info(
        "oddsportal curl: fetched %d rows for %s (year=%s)",
        len(out), tournament_url, default_year,
    )
    return out


# ---- Playwright fallback (for when curl_cffi fails) -------------------------

def _playwright_launch_kwargs() -> list[str]:
    """Chromium launch flags that reduce bot fingerprinting."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-features=IsolateOrigins,site-per-process",
    ]


def _playwright_stealth_script() -> str:
    """JS injected at context creation to mask Playwright fingerprints."""
    return """
        // Hide webdriver
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        // Fake plugins / mimeTypes
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        // Chrome runtime shim
        window.navigator.chrome = {runtime: {}, csi: function(){}, loadTimes: function(){}};
        // Permissions API shape
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) =>
                parameters && parameters.name === 'notifications'
                    ? Promise.resolve({state: Notification.permission})
                    : originalQuery(parameters);
        }
    """


def _wait_for_real_content(page, *, max_seconds: float = 90.0) -> bool:
    """Wait until the page is past any Cloudflare challenge.

    Returns True if real content loaded, False if we hit the max wait.
    """
    import random
    start = time.time()
    # OddsPortal 2025/2026 SPA selectors – be generous
    selectors = (
        ".eventRow",
        "[class*='eventRow']",
        "[data-testid*='event']",
        "table.odds",
        "div.next-matches-tournament",
        "#tournamentTable",
        ".tournament-page",
        "div[flex*=event]",
    )
    while time.time() - start < max_seconds:
        try:
            html = page.content()
        except Exception:
            time.sleep(1.0)
            continue
        if _is_cloudflare_challenge(html):
            time.sleep(1.0)
            continue
        if _is_error_page(html):
            return False
        # Look for actual event content
        try:
            count = page.evaluate(
                """(sels) => sels.some(s => document.querySelectorAll(s).length > 0)""",
                list(selectors),
            )
        except Exception:
            count = False
        if count:
            return True
        time.sleep(1.0)
    return False


def fetch_rendered_html(url: str, *, timeout_ms: int | None = None) -> str:
    """Render a URL with Playwright Chromium and return the HTML.

    Hardened against Cloudflare bot challenges:
      - Uses wait_until='load' (not 'networkidle') so a long-running CF
        challenge script does not abort the navigation.
      - Polls up to 90 s (overridable via ODDSPORTAL_RENDER_TIMEOUT_MS env var)
        for either real event content or a CF challenge to clear.
      - Disables image/font loads to keep network noise low.
      - Injects stealth JS to mask navigator.webdriver and friends.
    """
    import os as _os
    if timeout_ms is None:
        env_t = _os.getenv("ODDSPORTAL_RENDER_TIMEOUT_MS")
        timeout_ms = int(env_t) if env_t else 120000
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=_playwright_launch_kwargs(),
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1200},
            locale="en-GB",
            timezone_id="UTC",
        )
        context.add_init_script(_playwright_stealth_script())
        if _OP_COOKIES:
            try:
                context.add_cookies([
                    {"name": k, "value": v, "domain": ".oddsportal.com", "path": "/"}
                    for k, v in _OP_COOKIES.items()
                ])
            except Exception:
                pass
        # Don't waste bandwidth on static assets — they often hang on CF.
        context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot,ico}",
            lambda r: r.abort(),
        )
        page = context.new_page()
        try:
            # 'commit' just waits for the response; CF challenges usually do
            # return a response, just not the real page.
            page.goto(url, wait_until="commit", timeout=timeout_ms)
        except Exception as exc:
            logger.warning("goto commit failure for %s: %s", url, exc)
        # Now wait for content (or CF to clear)
        ok = _wait_for_real_content(page, max_seconds=timeout_ms / 1000.0)
        if not ok:
            logger.warning(
                "fetch_rendered_html: real content did not appear for %s "
                "within %d s; returning whatever is on the page",
                url, timeout_ms // 1000,
            )
        try:
            html = page.content()
        except Exception as exc:
            logger.warning("fetch_rendered_html: page.content() failed: %s", exc)
            html = ""
        context.close()
        browser.close()
    return html


def _fetch_ajax_via_playwright(
    page_id: str,
    *,
    tour: str,
    tournament: str,
    default_year: int | None,
    oddsportal_url: str,
    max_pages: int = 50,
    sleep: float = 1.0,
    headless: bool = True,
    timeout_ms: int = 90000,
) -> list[dict]:
    """Fetch AJAX archive pages from inside a Playwright browser context.

    This is the most reliable path when curl_cffi is blocked by Cloudflare,
    because the browser session has valid cf_clearance cookies after it
    passes the challenge.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — Playwright AJAX path disabled")
        return []

    out: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=_playwright_launch_kwargs())
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1200},
            locale="en-GB",
            timezone_id="UTC",
        )
        context.add_init_script(_playwright_stealth_script())
        if _OP_COOKIES:
            try:
                context.add_cookies([
                    {"name": k, "value": v, "domain": ".oddsportal.com", "path": "/"}
                    for k, v in _OP_COOKIES.items()
                ])
            except Exception:
                pass
        context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot,ico}",
            lambda r: r.abort(),
        )
        page = context.new_page()
        # Navigate to the tournament page first so cf_clearance is set.
        try:
            page.goto(oddsportal_url, wait_until="commit", timeout=timeout_ms)
        except Exception as exc:
            logger.warning("playwright ajax goto failure: %s", exc)
        _wait_for_real_content(page, max_seconds=timeout_ms / 1000.0)

        for page_num in range(1, max_pages + 1):
            rows: list[dict] = []
            for url in _ajax_archive_url_candidates(page_id, page_num, year=default_year):
                try:
                    text = page.evaluate(
                        """async (url) => {
                            const resp = await fetch(url, {
                                credentials: 'include',
                                headers: {'x-requested-with': 'XMLHttpRequest'}
                            });
                            return await resp.text();
                        }""",
                        url,
                    )
                except Exception as exc:
                    logger.warning(
                        "playwright AJAX fetch failed for %s: %s", url, exc,
                    )
                    continue
                if not text:
                    continue
                if _is_cloudflare_challenge(text):
                    logger.info("playwright AJAX returned CF challenge for %s", url)
                    continue
                rows = _parse_ajax_payload(
                    str(text),
                    tour=tour,
                    tournament=tournament,
                    default_year=default_year,
                    oddsportal_url=oddsportal_url,
                )
                if rows:
                    break
            if not rows:
                break
            out.extend(rows)
            if len(rows) < 5:
                break
            if sleep:
                time.sleep(sleep)

        browser.close()
    logger.info(
        "oddsportal Playwright AJAX: fetched %d rows for %s (year=%s)",
        len(out), oddsportal_url, default_year,
    )
    return out


def fetch_via_rendered_dom(
    url: str,
    *,
    tour: str = "UNKNOWN",
    tournament: str = "",
    default_year: int | None = None,
    oddsportal_url: str = "",
    max_pages: int = 50,
    sleep: float = 1.0,
    timeout_ms: int = 90000,
) -> list[dict]:
    """Fetch OddsPortal tournament pages by clicking through the rendered DOM.

    Last-resort path. Loads the tournament page in Playwright, scrapes the
    rows on screen, then clicks the "Next" pagination button and repeats
    until the button disappears or `max_pages` is reached. Useful when
    AJAX endpoints are blocked but the rendered page is reachable.
    """
    try:
        from racketfactory.entities import normalize_player
    except ImportError:
        normalize_player = lambda v: str(v or "").strip()  # noqa: E731

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — render-DOM path disabled")
        return []

    out: list[dict] = []
    seen: set[tuple] = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=_playwright_launch_kwargs())
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1200},
            locale="en-GB",
            timezone_id="UTC",
        )
        context.add_init_script(_playwright_stealth_script())
        if _OP_COOKIES:
            try:
                context.add_cookies([
                    {"name": k, "value": v, "domain": ".oddsportal.com", "path": "/"}
                    for k, v in _OP_COOKIES.items()
                ])
            except Exception:
                pass
        context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot,ico}",
            lambda r: r.abort(),
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="commit", timeout=timeout_ms)
        except Exception as exc:
            logger.warning("render-DOM goto failure: %s", exc)
        _wait_for_real_content(page, max_seconds=timeout_ms / 1000.0)

        for render_page in range(1, max_pages + 1):
            html = ""
            try:
                html = page.content()
            except Exception as exc:
                logger.warning("render-DOM page.content() failed: %s", exc)
                break
            page_rows = parse_rendered_html(
                html,
                tour=tour,
                tournament=tournament,
                default_year=default_year,
                oddsportal_url=oddsportal_url,
            )
            new_rows = []
            for row in page_rows:
                key = (
                    row.get("match_date"),
                    row.get("player_a"),
                    row.get("player_b"),
                    row.get("odds_a"),
                    row.get("odds_b"),
                )
                if key in seen:
                    continue
                seen.add(key)
                new_rows.append(row)
            if new_rows:
                logger.info(
                    "oddsportal render-DOM page %d: %d new rows",
                    render_page, len(new_rows),
                )
                out.extend(new_rows)
            elif render_page > 1:
                break
            if render_page >= max_pages:
                break
            # Try to click the next page button
            clicked = _click_next_pagination(page)
            if not clicked:
                break
            if sleep:
                time.sleep(sleep)

        browser.close()
    return out


def _click_next_pagination(page, *, timeout_ms: int = 15000) -> bool:
    """Click OddsPortal's pagination 'Next' button if present."""
    try:
        before = page.evaluate(
            """() => {
                const first = document.querySelector('.eventRow, [data-testid*=\"event\"], [class*=\"eventRow\"]');
                return first ? (first.id || (first.innerText || '').slice(0, 200)) : '';
            }"""
        )
        clicked = page.evaluate(
            """() => {
                const norm = (el) => ((el.innerText || el.textContent || '').trim());
                const cls = (el) => String(el.getAttribute('class') || '').toLowerCase();
                const els = Array.from(document.querySelectorAll('[class*=\"pagination-link\"], a, button, div, span'));
                let target = els.find((el) => cls(el).includes('pagination-link') && /next/i.test(norm(el)));
                if (!target) target = els.find((el) => /next/i.test(norm(el)) && /pag/i.test(cls(el)));
                if (!target) target = els.find((el) => /^next$/i.test(norm(el)));
                if (!target) return false;
                target.scrollIntoView({block: 'center'});
                target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                return true;
            }"""
        )
        if not clicked:
            return False
        page.wait_for_timeout(1500)
        try:
            page.wait_for_function(
                """(before) => {
                    const first = document.querySelector('.eventRow, [data-testid*=\"event\"], [class*=\"eventRow\"]');
                    const now = first ? (first.id || (first.innerText || '').slice(0, 200)) : '';
                    return now && now !== before;
                }""",
                before,
                timeout=timeout_ms,
            )
        except Exception:
            page.wait_for_timeout(2000)
        return True
    except Exception as exc:
        logger.warning("pagination click failed: %s", exc)
        return False


def parse_rendered_html(
    html: str,
    *,
    default_date: str = "",
    tour: str = "UNKNOWN",
    tournament: str = "",
    oddsportal_url: str = "",
    default_year: int | None = None,
    today: date_type | None = None,
) -> list[dict[str, Any]]:
    """Parse saved/rendered OddsPortal tennis HTML."""
    from bs4 import BeautifulSoup
    from racketfactory.entities import normalize_player
    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select(".eventRow, [data-testid*='event'], .event-row, tr")
    rows: list[dict[str, Any]] = []
    current_date = default_date
    current_round = ""
    seen_row_ids: set[int] = set()

    for node in candidates:
        if id(node) in seen_row_ids:
            continue
        seen_row_ids.add(id(node))
        date_header = node.select_one('[data-testid="date-header"]')
        if date_header:
            header_text = date_header.get_text(" ", strip=True)
            current_date = parse_oddsportal_date_header(header_text, default_year=default_year, today=today, default=current_date)
            current_round = _round_from_date_header(header_text)

        text = node.get_text(" ", strip=True)
        if not text:
            continue
        odds = _odds_from_node(node)
        if len(odds) < 2:
            continue
        players = _players_from_node(node)
        if len(players) < 2:
            before_odds = text.split(odds[0], 1)[0]
            parts = [normalize_player(p) for p in re.split(r"\s+[-–—]\s+|\s+v(?:s\.)?\s+", before_odds, flags=re.I)]
            parts = [p for p in parts if p and not parse_date(p)]
            players = parts[:2]
        if len(players) < 2:
            continue

        status = ""
        status_node = node.select_one('[data-testid="time-item"]')
        if status_node:
            status = status_node.get_text(" ", strip=True)
        score, inferred_winner = _score_and_winner_from_event(node, players[0], players[1], status)
        if not score:
            score_match = re.search(r"\b\d{1,2}\s*[-:–—]\s*\d{1,2}(?:\s+\d{1,2}\s*[-:–—]\s*\d{1,2})*\b", text.replace(":", "-"))
            score = score_match.group(0) if score_match else ""
        rows.append({
            "match_date": current_date or parse_date(text, default=default_date),
            "tour": tour,
            "tournament": tournament,
            "round": current_round,
            "player_a": players[0],
            "player_b": players[1],
            "odds_a": odds[-2],
            "odds_b": odds[-1],
            "winner": inferred_winner,
            "score": score,
            "source": "OddsPortal Rendered",
            "bookmaker": "OddsPortal Rendered",
            "oddsportal_url": oddsportal_url,
        })
    return normalize_rows(rows)


def parse_embedded_json(html: str, *, tour: str = "UNKNOWN", tournament: str = "", oddsportal_url: str = "") -> list[dict[str, Any]]:
    """Best-effort extraction from JSON script blobs."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if "odds" not in text.lower() or "participant" not in text.lower():
            continue
        try:
            blobs = re.findall(r"\{[^{}]*(?:participant|home|away|odds)[^{}]*\}", text, flags=re.I)
        except Exception:
            blobs = []
        for blob in blobs:
            try:
                data = json.loads(blob)
            except Exception:
                continue
            rows.extend(normalize_rows([{**data, "tour": tour, "tournament": tournament, "oddsportal_url": oddsportal_url}]))
    return rows


# ---- Public API (legacy compatibility) --------------------------------------

def parse_date(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    m = re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2})\b", text)
    candidate = m.group(1) if m else text[:10]
    candidate = candidate.replace("/", "-").replace(".", "-")
    parts = candidate.split("-")
    try:
        if len(parts[0]) == 4:
            y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            d, mo, y = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return default


def parse_oddsportal_date_header(text: object, *, default_year: int | None = None, today: date_type | None = None, default: str = "") -> str:
    """Parse rendered OddsPortal date headers such as 'Today, 25 Jun - Singles'."""
    raw = " ".join(str(text or "").replace("\xa0", " ").split())
    if not raw:
        return default
    today = today or date_type.today()
    lowered = raw.lower()
    if lowered.startswith("today"):
        return today.isoformat()
    if lowered.startswith("yesterday"):
        return (today - timedelta(days=1)).isoformat()

    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?\b", raw)
    if not m:
        return parse_date(raw, default=default)
    day = int(m.group(1))
    month = _MONTHS.get(m.group(2).lower())
    year = int(m.group(3)) if m.group(3) else (default_year or today.year)
    if not month:
        return default
    try:
        return date_type(year, month, day).isoformat()
    except ValueError:
        return default


def to_decimal(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("−", "-")
    if not text:
        return None
    m = re.match(r"(?<!\w)([+-]\d{3,4})(?!\w)", text)
    if m:
        american = int(m.group(1))
        if american > 0:
            f = 1.0 + american / 100.0
        elif american < 0:
            f = 1.0 + 100.0 / abs(american)
        else:
            return None
    else:
        try:
            f = float(text)
        except ValueError:
            return None
    if f <= 1.0 or f > 1000:
        return None
    return round(f, 6)


def clean_winner(value: object, player_a: str, player_b: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in {"a", "1", "home", player_a.lower()}:
        return player_a
    if raw in {"b", "2", "away", player_b.lower()}:
        return player_b
    return str(value or "").strip() if str(value or "").strip() in {player_a, player_b} else ""


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from racketfactory.entities import normalize_player, normalize_tour
    out: list[dict[str, Any]] = []
    captured_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        player_a = normalize_player(row.get("player_a") or row.get("home") or row.get("player1"))
        player_b = normalize_player(row.get("player_b") or row.get("away") or row.get("player2"))
        odds_a = to_decimal(row.get("odds_a") or row.get("home_odds") or row.get("odds1"))
        odds_b = to_decimal(row.get("odds_b") or row.get("away_odds") or row.get("odds2"))
        match_date = parse_date(row.get("match_date") or row.get("date"), default="")
        if not match_date or not player_a or not player_b or odds_a is None or odds_b is None:
            continue
        winner = clean_winner(row.get("winner") or row.get("result") or "", player_a, player_b)
        out.append({
            "match_date": match_date,
            "tour": normalize_tour(row.get("tour") or row.get("league") or "UNKNOWN"),
            "tournament": str(row.get("tournament") or "").strip(),
            "round": str(row.get("round") or "").strip(),
            "player_a": player_a,
            "player_b": player_b,
            "winner": winner,
            "score": str(row.get("score") or "").strip(),
            "odds_a": odds_a,
            "odds_b": odds_b,
            "bookmaker": str(row.get("bookmaker") or "OddsPortal Rendered").strip(),
            "source": str(row.get("source") or "OddsPortal Rendered").strip(),
            "captured_at": str(row.get("captured_at") or captured_at),
            "oddsportal_url": str(row.get("oddsportal_url") or row.get("url") or "").strip(),
        })
    return out


def read_export_csv(path: str | Path) -> list[dict[str, Any]]:
    df = pd.read_csv(path, low_memory=False)
    return normalize_rows(df.to_dict("records"))


def write_monthly_csv(rows: list[dict[str, Any]], data_dir: str | Path = "localdata") -> list[Path]:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_month.setdefault(str(row["match_date"])[:7], []).append(row)
    written: list[Path] = []
    for month, month_rows in sorted(by_month.items()):
        path = data_dir / f"oddsportal_tennis_{month}.csv.gz"
        df = pd.DataFrame(month_rows, columns=COLUMNS)
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(
                subset=["match_date", "tour", "tournament", "player_a", "player_b", "bookmaker"],
                keep="last",
            )
        df.to_csv(path, index=False, compression="gzip")
        written.append(path)
    return written


# ---- Helpers for rendered HTML parsing ----

def _round_from_date_header(text: str) -> str:
    if " - " not in text:
        return ""
    parts = [p.strip() for p in text.split(" - ") if p.strip()]
    return " - ".join(parts[1:]) if len(parts) > 1 else ""


def _players_from_node(node) -> list[str]:
    selectors = [
        ".participant-name", "[class*='participant']", "[class*='team']", "[data-testid*='participant']",
        "p[class*='participant']", "a[href*='/tennis/']",
    ]
    from racketfactory.entities import normalize_player
    found: list[str] = []
    for sel in selectors:
        for p in node.select(sel):
            text = normalize_player(p.get("title") or p.get_text(" ", strip=True))
            if text and not re.match(r"\d+\.\d{2}$", text) and not re.match(r"[+-]\d{3,4}$", text) and text not in found:
                found.append(text)
        if len(found) >= 2:
            return found[:2]
    return found[:2]


def _odds_from_node(node) -> list[str]:
    odds: list[str] = []
    for p in node.select("p[data-testid*='odd-container'], [data-testid*='odd-container'] p"):
        text = p.get_text(" ", strip=True).replace("−", "-")
        if to_decimal(text) is not None and text not in odds:
            odds.append(text)
    if len(odds) >= 2:
        return odds[-2:]
    text = node.get_text(" ", strip=True).replace("−", "-")
    found = re.findall(r"(?<!\d)(\d+\.\d{2})(?!\d)", text) or re.findall(r"(?<!\w)([+-]\d{3,4})(?!\w)", text)
    return found[-2:]


def _score_and_winner_from_event(node, player_a: str, player_b: str, status_text: str) -> tuple[str, str]:
    if re.search(r"\b(retired|ret\.)\b", status_text, flags=re.I):
        return "RET", ""
    participant_box = node.select_one('[data-testid="event-participants"]') or node
    center = participant_box.select_one(".relative")
    score_text = center.get_text(" ", strip=True) if center else participant_box.get_text(" ", strip=True)
    score_match = re.search(r"\b\d{1,2}\s*[-:–—]\s*\d{1,2}(?:\s+\d{1,2}\s*[-:–—]\s*\d{1,2})*\b", score_text.replace(":", "-"))
    score = ""
    winner = ""
    if score_match:
        score = re.sub(r"\s*[-:–—]\s*", "-", score_match.group(0)).strip()
        nums = [int(x) for x in re.findall(r"\d+", score)]
        if len(nums) >= 2 and re.search(r"\b(finished|fin)\b", status_text, flags=re.I):
            if nums[0] > nums[1]:
                winner = player_a
            elif nums[1] > nums[0]:
                winner = player_b
    return score, winner
