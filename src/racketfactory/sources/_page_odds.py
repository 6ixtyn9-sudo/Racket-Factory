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
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
# Edge-Factory parity: plain simple UA works for BetExplorer (no impersonation).
EDGE_SIMPLE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
)

# Edge parity: minimum seconds between consecutive requests per host.
_MIN_INTERVAL = 3.0
_last_request_time: dict[str, float] = {}


def _throttle(host: str) -> None:
    now = time.monotonic()
    elapsed = now - _last_request_time.get(host, 0.0)
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time[host] = time.monotonic()

_DECIMAL_RE = re.compile(r"\b(\d{1,2}\.\d{1,2})\b")
_DATE_DMY_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_NAME_SEP_RE = re.compile(r"\s+[–—-]\s+")
_FINISHED_RE = re.compile(r"\b(FIN\b|FINISHED|RET\.?|RETIRED|W\.?\s?O\.?|WALKOVER|AWARDED|CANCELLED)", re.IGNORECASE)

MIN_DECIMAL_ODDS = 1.01
MAX_DECIMAL_ODDS = 51.0


def fetch_page_html(url: str, source_label: str, *, timeout: int = 30,
                    prefer_plain: bool = False) -> str:
    """GET a listing page; "" on any failure (fail-soft by contract).

    Transport order per host: Cloudflare-hard hosts try curl impersonation
    (repo-standard chrome133a) first; BetExplorer-class hosts try plain
    requests with a simple UA first (Edge-Factory parity — proven working).
    Throttled to one request per 3s per host; on HTTP 429, honors
    Retry-After (capped 60s) with one delayed second pass.
    """
    order = (_plain_fetch, _curl_fetch) if prefer_plain else (_curl_fetch, _plain_fetch)
    html, status, retry_after = _run_order(order, url, source_label, timeout=timeout)
    if html or status != 429:
        return html
    wait = max(5, min(retry_after or 10, 60))
    logger.warning("%s rate-limited (429) for %s; retrying once after %ds",
                   source_label, url, wait)
    time.sleep(wait)
    html, _, _ = _run_order(order, url, source_label, timeout=timeout)
    return html


def _run_order(order, url: str, source_label: str, *,
               timeout: int) -> tuple[str, int | None, int]:
    html, status, retry_after = order[0](url, source_label, timeout=timeout)
    if html or status not in (None, 429):
        return html, status, retry_after
    # First transport failed or was limited: try the other once.
    html2, status2, retry_after2 = order[1](url, source_label, timeout=timeout)
    return html2, status2, max(retry_after, retry_after2)


def _curl_fetch(url: str, source_label: str, *, timeout: int) -> tuple[str, int | None, int]:
    _throttle(urlsplit(url).netloc)
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(
            url, timeout=timeout, impersonate="chrome133a",
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
        )
    except Exception as exc:
        logger.warning("%s curl fetch failed for %s: %s", source_label, url, exc)
        return "", None, 0
    return _response_text(resp, url, source_label)


def _plain_fetch(url: str, source_label: str, *, timeout: int) -> tuple[str, int | None, int]:
    _throttle(urlsplit(url).netloc)
    try:
        import requests as std_requests
        resp = std_requests.get(
            url, timeout=timeout,
            headers={"User-Agent": EDGE_SIMPLE_UA, "Accept-Language": "en-US,en;q=0.9"},
        )
    except Exception as exc:
        logger.warning("%s plain fetch failed for %s: %s", source_label, url, exc)
        return "", None, 0
    return _response_text(resp, url, source_label)


def _response_text(resp: object, url: str, source_label: str) -> tuple[str, int | None, int]:
    try:
        status = resp.status_code  # type: ignore[union-attr]
    except Exception:
        return "", None, 0
    if status != 200:
        logger.warning("%s fetch %s: HTTP %s", source_label, url, status)
        return "", status if isinstance(status, int) else None, _retry_after(resp)
    try:
        return resp.text or "", status, 0  # type: ignore[union-attr]
    except Exception:
        return "", status if isinstance(status, int) else None, 0


def _retry_after(resp: object) -> int:
    try:
        headers = resp.headers  # type: ignore[union-attr]
        return int(str(headers.get("Retry-After", "") or "0").strip() or "0")
    except Exception:
        return 0


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


_CLIMB_MAX_LEVELS = 6
_CLIMB_MAX_CHARS = 8000


def _is_player_anchor(anchor: Tag, is_match_link: Callable[[str], dict[str, Any] | None]) -> tuple[str, str, dict[str, Any]] | None:
    """Return (home, away, ctx) if anchor looks like a player-vs-player link."""
    href = str(anchor.get("href") or "")
    ctx = is_match_link(href)
    if ctx is None:
        return None
    txt = anchor.get_text(" ", strip=True)
    # Skip pure score / time anchors that match href pattern but carry no names
    # e.g. BetExplorer score links like "1:3" or "CAN." etc.
    if not txt or len(txt) < 5:
        return None
    home, away = split_match_names(txt)
    if not home or not away:
        return None
    return home, away, ctx


def _match_link_count(container: Tag, is_match_link: Callable[[str], dict[str, Any] | None]) -> int:
    n = 0
    for anchor in container.find_all("a", href=True):
        if _is_player_anchor(anchor, is_match_link) is None:
            continue
        n += 1
        if n > 1:
            break
    return n


def _row_container(link: Tag, names_text: str,
                   is_match_link: Callable[[str], dict[str, Any] | None]) -> Tag | None:
    """Smallest box holding this match alone with (hopefully) its prices.

    Tables/lists give the row directly. For Next.js div soup, climb from the
    link's parent while the box holds <=1 match and no prices yet. Never
    return a box holding 2+ matches (sibling prices would misattribute).
    """
    for tag in ("tr", "li"):
        parent = link.find_parent(tag)
        if parent is not None:
            return parent if _match_link_count(parent, is_match_link) <= 1 else None
    node = link.parent if isinstance(link.parent, Tag) else None
    best: Tag | None = None
    for _ in range(_CLIMB_MAX_LEVELS + 1):
        if node is None or node.name in ("html", "body", "[document]"):
            break
        if len(node.get_text(" ", strip=True)) > _CLIMB_MAX_CHARS:
            break
        if _match_link_count(node, is_match_link) > 1:
            break
        best = node
        scan = node.get_text(" ", strip=True).replace(names_text, " ")
        if len(_row_decimals(scan)) >= 2:
            break
        node = node.parent if isinstance(node.parent, Tag) else None
    return best


def _extract_odds_from_tr(tr: Tag, names_text: str = "") -> list[float]:
    """Extract 1/2 decimals from a <tr> via visible text and data-odd attrs."""
    # Visible text scan (names removed to avoid leaking into odds)
    txt = tr.get_text(" ", strip=True)
    if names_text:
        txt = txt.replace(names_text, " ")
    vals = _row_decimals(txt)
    if len(vals) >= 2:
        return vals[:2]

    # Data-attribute scan (BetExplorer renders odds in data-odd / data-opening-odd)
    attr_vals: list[float] = []
    for attr in ("data-odd", "data-opening-odd", "data-closing-odd", "data-odd-value"):
        for el in tr.find_all(attrs={attr: True}):
            try:
                v = float(str(el.get(attr) or "").strip())
            except Exception:
                continue
            if MIN_DECIMAL_ODDS <= v <= MAX_DECIMAL_ODDS:
                attr_vals.append(v)
    if len(attr_vals) >= 2:
        return attr_vals[:2]

    # Class-based odds cells (td.table-main__odds, etc.)
    cell_vals: list[float] = []
    for td in tr.find_all("td"):
        cls = " ".join(td.get("class") or []).lower()
        if "odd" not in cls:
            continue
        try:
            v = float(td.get_text(strip=True))
        except Exception:
            continue
        if MIN_DECIMAL_ODDS <= v <= MAX_DECIMAL_ODDS:
            cell_vals.append(v)
    if len(cell_vals) >= 2:
        return cell_vals[:2]

    # Merge any partials found
    merged = vals + attr_vals + cell_vals
    # Deduplicate preserving order, keep first two plausible
    seen: set[float] = set()
    uniq: list[float] = []
    for v in merged:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
        if len(uniq) >= 2:
            break
    if len(uniq) >= 2:
        return uniq[:2]
    return []


def _find_odds_near(link: Tag, names_text: str, is_match_link) -> list[float]:
    """Search for 2 decimals near the link within same tr only (conservative).

    Used as fallback when the immediate container has no odds. Searches the
    parent tr only, not siblings, to avoid misattributing odds from neighboring
    matches (which broke test_climb_never_enters_multi_match_box). Now also
    checks data-odd attributes inside the tr.
    """
    tr = link.find_parent("tr")
    if tr is None:
        return []
    return _extract_odds_from_tr(tr, names_text)


_FUSED_SPLIT_MAX_TOKENS = 4
_TRAIL_SCORE_RE = re.compile(r"\s+\d+\s*:\s*\d+(?:\s*[,\s]\s*\d+\s*:\s*\d+)*\s*$")


def _looks_like_player(text: str) -> bool:
    """'Surname I.' shape check (multi-word surnames / multi-initials allowed)."""
    toks = text.split()
    if not 2 <= len(toks) <= _FUSED_SPLIT_MAX_TOKENS:
        return False
    if not re.fullmatch(r"[A-Z]\.", toks[-1]):
        return False
    if len(re.sub(r"[^A-Za-z]", "", toks[0])) < 2 or not toks[0][:1].isupper():
        return False
    for tok in toks[1:-1]:
        if re.fullmatch(r"[A-Z]\.", tok):
            continue
        if tok[:1].isupper() and len(re.sub(r"[^A-Za-z]", "", tok)) >= 2:
            continue
        return False
    return True


def _split_fused_names(text: str) -> tuple[str, str]:
    """Split 'Surname I. Surname I.' when no dash separator is present.

    BetExplorer's runner-served day/results pages join both names with a bare
    space ('Tiafoe F. Shelton B.', run #222). A trailing scoreline is
    stripped first so finished rows still reach the finished check; doubles
    ('/' present) and score-only texts are rejected rather than guessed.
    """
    if "/" in text:
        return "", ""
    clean = _TRAIL_SCORE_RE.sub("", text.strip()).strip()
    best: tuple[str, str] = ("", "")
    for m in re.finditer(r"\. ", clean):
        left, right = clean[: m.end()].strip(), clean[m.end():].strip()
        if _looks_like_player(left) and _looks_like_player(right):
            best = (left, right)
    return best


def split_match_names(link_text: str) -> tuple[str, str]:
    """Split 'Home Player - Away Player' on the score-safe separator.

    Falls back to fused-name splitting ('Surname I. Surname I.') when no
    separator is present. Leading time and trailing scoreline are stripped
    first so '19:30 Parks A. - Lepchenko V.' and 'Tiafoe F. - Shelton B. 1:3'
    both parse as player names.
    """
    txt = link_text.strip()
    # Strip leading time like "19:30 " that BetExplorer prefixes on day pages
    txt = _TIME_RE.sub("", txt, count=1).strip()
    # Strip trailing scoreline like " 1:3" or " 1:3, 6:2" etc.
    txt = _TRAIL_SCORE_RE.sub("", txt).strip()
    parts = _NAME_SEP_RE.split(txt, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        # Right side may still carry trailing score after dash split
        right = _TRAIL_SCORE_RE.sub("", right).strip()
        # Basic sanity: both sides must look at least vaguely like players
        # (contain a dot for initial, or at least two chars and a space)
        if left and right:
            return left, right
        return "", ""
    return _split_fused_names(txt)


def parse_listing_page(
    html: str,
    *,
    source_label: str,
    is_match_link: Callable[[str], dict[str, Any] | None],
    page_date: str,
    require_odds: bool = True,
) -> list[dict[str, Any]]:
    """Scan a day-listing page for (names, 1, 2) match rows.

    ``is_match_link(href)`` returns a context dict (tournament/tour/...) for
    match-detail links, else None. Odds are the first two plausible decimals
    in the match's row once the names/date/time text is removed. With
    ``require_odds=False`` the decimals gate is skipped and rows are emitted
    with ``odds_home``/``odds_away`` None (for legs that price each match
    from its detail page instead of the listing).
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    n_links = n_match = n_bad_names = n_no_box = 0
    n_finished = n_few_decimals = n_dupes = 0
    few_samples: list[str] = []
    bad_samples: list[str] = []
    for link in soup.find_all("a", href=True):
        n_links += 1
        href = str(link.get("href") or "")
        ctx = is_match_link(href)
        if ctx is None:
            continue
        n_match += 1
        names_text = link.get_text(" ", strip=True)
        home, away = split_match_names(names_text)
        if not home or not away:
            n_bad_names += 1
            if len(bad_samples) < 3:
                bad_samples.append(f"{names_text[:120]} | {href[:150]}")
            continue
        container = _row_container(link, names_text, is_match_link)
        if container is None:
            n_no_box += 1
            continue
        row_text = container.get_text(" ", strip=True)
        if _FINISHED_RE.search(row_text):
            n_finished += 1
            continue
        # Remove the names themselves so hyphenated/second-decimal name parts
        # can never leak into the odds scan; then take the first two decimals.
        decimals: list[float] = []
        if require_odds:
            scan_text = row_text.replace(names_text, " ")
            decimals = _row_decimals(scan_text)
            if len(decimals) < 2:
                # Fallback: search nearby (parent, siblings) for odds — BetExplorer day pages
                # sometimes render odds outside the immediate link container.
                decimals = _find_odds_near(link, names_text, is_match_link)
            if len(decimals) < 2:
                n_few_decimals += 1
                if len(few_samples) < 3:
                    few_samples.append(row_text)
                continue
        key = f"{home}\x00{away}"
        if key in seen:
            n_dupes += 1
            continue
        seen.add(key)
        ko = _TIME_RE.search(row_text)
        rows.append({
            "match_date": page_date,
            "match_time": ko.group(0) if ko else "",
            "player_home": home,
            "player_away": away,
            "odds_home": decimals[0] if decimals else None,
            "odds_away": decimals[1] if len(decimals) > 1 else None,
            "tournament": str(ctx.get("tournament") or ""),
            "tour_hint": str(ctx.get("tour_hint") or ""),
            "match_url": href,
        })
    logger.info(
        "%s scan %s: links=%d match_links=%d parsed=%d finished=%d "
        "few_decimals=%d bad_names=%d no_box=%d dupes=%d",
        source_label, page_date, n_links, n_match, len(rows), n_finished,
        n_few_decimals, n_bad_names, n_no_box, n_dupes,
    )
    for sample in few_samples:
        logger.info("%s few-decimals sample: %r", source_label, sample[:180])
    for sample in bad_samples:
        logger.info("%s bad-names sample: %r", source_label, sample[:180])
    return rows
