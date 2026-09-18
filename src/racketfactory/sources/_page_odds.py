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
# Dash plus vs variants observed on BetExplorer/OddsPortal: " - ", " vs ", " vs. "
# Note: single "v" not included because it collides with initial "V." in "Lepchenko V. Melichar..."
_NAME_SEP_RE = re.compile(r"\s+(?:[–—-]|vs\.?)\s+", re.IGNORECASE)
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


def _resolve_link_names(link: Tag, is_match_link: Callable[[str], dict[str, Any] | None]) -> tuple[str, str, str]:
    """Resolve (home, away, names_text) for a match link.

    Primary: the anchor's own text ('A - B', fused 'A B'). Fallback (observed
    2026-09-18, BetExplorer): one match rendered as TWO sibling anchors on the
    same match href ('<a>Borisiouk M.</a> vs <a>Kim D. J.</a>') — join their
    texts in DOM order and split the union. The fallback fires only when the
    parent holds exactly those two anchors, both are match links to the same
    href, and the joined text splits cleanly, so it cannot invent matches.
    """
    txt = link.get_text(" ", strip=True)
    home, away = split_match_names(txt)
    if home and away:
        return home, away, txt
    parent = link.parent
    if isinstance(parent, Tag):
        anchors = [c for c in parent.children
                   if isinstance(c, Tag) and c.name == "a" and c.get("href")]
        if len(anchors) == 2 and link in anchors:
            hrefs = [str(a.get("href") or "") for a in anchors]
            if hrefs[0] == hrefs[1] and is_match_link(hrefs[0]) is not None:
                joined = " vs ".join(a.get_text(" ", strip=True) for a in anchors)
                h2, a2 = split_match_names(joined)
                if h2 and a2:
                    return h2, a2, joined
    return "", "", txt


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
    home, away, _ = _resolve_link_names(anchor, is_match_link)
    if not home or not away:
        return None
    return home, away, ctx


def _match_link_count(container: Tag, is_match_link: Callable[[str], dict[str, Any] | None]) -> int:
    n = 0
    seen: set[tuple[str, str]] = set()
    for anchor in container.find_all("a", href=True):
        resolved = _is_player_anchor(anchor, is_match_link)
        if resolved is None:
            continue
        # Two sibling anchors of the SAME match (BetExplorer two-anchor
        # rendering) resolve to one identity, not two.
        ident = (resolved[0], resolved[1])
        if ident in seen:
            continue
        seen.add(ident)
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
    """'Surname I.' shape check (multi-word surnames / multi-initials allowed).

    Now tolerant to lowercase particles (von, der, van, de, etc.) which appear
    in compound surnames like 'Von der Schulenburg J.' – observed 2026-09-13
    in BetExplorer results. Middle tokens may be initials or surname parts
    regardless of case as long as they are alphabetic length>=2.

    Extended 2026-09-17 to also accept full names without initials
    (e.g. 'Alcaraz Carlos', 'Sinner Jannik') and names where last token is
    a capitalized word, not just initial – this fixes bad=28 where BetExplorer
    started rendering full first names in some Challenger rows.
    """
    toks = text.split()
    if not 2 <= len(toks) <= _FUSED_SPLIT_MAX_TOKENS:
        return False
    # Last token: either initial "X." or capitalized word len>=2 (full first name)
    last = toks[-1]
    if not (re.fullmatch(r"[A-Z]\.", last) or (len(re.sub(r"[^A-Za-z]", "", last)) >= 2 and last[:1].isupper())):
        return False
    if len(re.sub(r"[^A-Za-z]", "", toks[0])) < 2 or not toks[0][:1].isupper():
        return False
    for tok in toks[1:-1]:
        if re.fullmatch(r"[A-Z]\.", tok):
            continue
        cleaned = re.sub(r"[^A-Za-z]", "", tok)
        if len(cleaned) >= 2:
            continue
        return False
    # Additional sanity: must contain at least one dot OR two capitalized words (to avoid random phrases)
    # Accept if any token is initial OR at least 2 tokens start with uppercase
    has_initial = any(re.fullmatch(r"[A-Z]\.", t) for t in toks)
    caps = sum(1 for t in toks if t[:1].isupper() and len(re.sub(r"[^A-Za-z]", "", t)) >= 2)
    if not (has_initial or caps >= 2):
        # Allow single full name like "Alcaraz Carlos" has caps=2, so passes
        # Reject random like "Winner" single token already filtered by len>=2 check
        return False
    return True


def _looks_like_player_loose(text: str) -> bool:
    """Very loose check for BetExplorer fallback: any 2+ word capitalized phrase, or initial present."""
    t = text.strip()
    if len(t) < 3:
        return False
    if re.fullmatch(r"\d+:\d+", t):
        return False
    if t.lower() in {"winner", "odds", "result", "finished"}:
        return False
    # Must have at least one uppercase letter
    if not any(c.isupper() for c in t):
        return False
    # Must have at least 2 chars and either dot or space
    if "." in t or " " in t:
        # Avoid pure scores
        if re.search(r"\d", t) and not re.search(r"[A-Za-z]", t):
            return False
        return len(t) >= 3
    return False


def _looks_like_doubles_team(text: str) -> bool:
    """Check if text looks like a doubles team 'A / B' with player-ish parts."""
    if "/" not in text:
        return False
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if len(parts) != 2:
        return False
    for p in parts:
        if len(p) < 2:
            return False
        if "." not in p and " " not in p.strip():
            return False
    return True


def _split_fused_names(text: str) -> tuple[str, str]:
    """Split 'Surname I. Surname I.' when no dash separator is present.

    BetExplorer's runner-served day/results pages join both names with a bare
    space ('Tiafoe F. Shelton B.', run #222) and sometimes without any space
    after the dot ('Zverev A.Shelton B.', 'Baris O.Claverie L.', observed
    2026-09-13). A trailing scoreline is stripped first so finished rows still
    reach the finished check; doubles without dash ('Britto L. / Remondy
    Pagotto V. H.Tosetto R. / Zanellato N.') are also handled when two slashes
    are present. Score-only texts are rejected rather than guessed.

    The no-space variant is normalized by inserting a space after any dot
    that is immediately followed by an uppercase letter (e.g. 'A.Shelton' ->
    'A. Shelton'), then the existing '. ' scan applies.

    2026-09-17: added loose fallback that accepts full names without initials
    (e.g. 'Alcaraz Carlos Sinner Jannik' split on cap boundary) to fix bad=28.
    """
    clean = _TRAIL_SCORE_RE.sub("", text.strip()).strip()
    # Strip trailing decimal odds that may leak from row text (e.g. " 1.69 2.21")
    clean = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", clean).strip()
    # Normalize fused 'A.Shelton' -> 'A. Shelton' so the '. ' scanner can find it.
    clean = re.sub(r"\.(?=[A-Z])", ". ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()

    # Doubles handling: if slash present
    if "/" in clean:
        if clean.count("/") >= 2:
            best: tuple[str, str] = ("", "")
            for m in re.finditer(r"\. ", clean):
                left = clean[: m.end()].strip()
                right = clean[m.end():].strip()
                if not left or not right:
                    continue
                if "/" not in left or "/" not in right:
                    continue
                if "." not in left or "." not in right:
                    continue
                # For doubles, allow right starting with initial (e.g. "H. Tosetto R. / ...")
                # Only skip pure-initial fragments that are not a team.
                if "/" not in right and re.match(r"^[A-Z]\.\s*$", right):
                    continue
                if _looks_like_doubles_team(left) and _looks_like_doubles_team(right):
                    best = (left, right)
                    if left.count("/") == 1 and right.count("/") == 1 and len(left) > 5 and len(right) > 5:
                        if right and right[0].isupper():
                            break
            if best[0]:
                return best
            # Fallback: try any whitespace split that yields 1 slash per side
            for m in re.finditer(r"\s+", clean):
                left = clean[: m.start()].strip()
                right = clean[m.end():].strip()
                if left.count("/") == 1 and right.count("/") == 1 and len(left) > 5 and len(right) > 5:
                    if "/" not in right and re.match(r"^[A-Z]\.\s*$", right):
                        continue
                    if _looks_like_doubles_team(left) and _looks_like_doubles_team(right):
                        return (left, right)
        # Single slash = single team, not a match
        return "", ""

    best: tuple[str, str] = ("", "")
    # Primary: split on ". " where both sides look like player (strict)
    for m in re.finditer(r"\. ", clean):
        left, right = clean[: m.end()].strip(), clean[m.end():].strip()
        if _looks_like_player(left) and _looks_like_player(right):
            best = (left, right)
    if not best[0]:
        for m in re.finditer(r"(?<=\.)\s+", clean):
            left, right = clean[: m.end()].strip(), clean[m.end():].strip()
            if _looks_like_player(left) and _looks_like_player(right):
                best = (left, right)
    if best[0]:
        return best
    # Loose fallback: try splitting on whitespace where both sides look loosely like players
    # This handles full names without initials: "Alcaraz Carlos Sinner Jannik" -> "Alcaraz Carlos" + "Sinner Jannik"
    # Find all possible split points and pick the most balanced where both sides have >=2 tokens and caps
    tokens = clean.split()
    if len(tokens) >= 4:
        # Try middle splits
        for split_idx in range(2, len(tokens)-1):
            left = " ".join(tokens[:split_idx])
            right = " ".join(tokens[split_idx:])
            if _looks_like_player_loose(left) and _looks_like_player_loose(right):
                # Prefer splits where both sides have at least 2 tokens and start with uppercase
                if left[:1].isupper() and right[:1].isupper():
                    # If both contain at least 2 words, accept
                    if len(left.split()) >= 2 and len(right.split()) >= 2:
                        return (left, right)
    return ("", "")


def split_match_names(link_text: str) -> tuple[str, str]:
    """Split 'Home Player - Away Player' on the score-safe separator.

    Falls back to fused-name splitting ('Surname I. Surname I.') when no
    separator is present. Leading time and trailing scoreline are stripped
    first so '19:30 Parks A. - Lepchenko V.' and 'Tiafoe F. - Shelton B. 1:3'
    both parse as player names.

    BetExplorer results pages (2026-09-13) also render fused without space
    after the dot ('Zverev A.Shelton B.', 'Baris O.Claverie L.'). We normalize
    '.<Upper>' -> '. <Upper>' before any split so both dash and fused paths
    see the same shape.

    2026-09-17: strip trailing odds (e.g. ' 1.69 2.21') in dash path too, and
    allow full names without initials to fix bad=28.
    """
    txt = link_text.strip()
    # Normalize fused dot+Upper (no space) early – helps both dash and fused paths
    txt = re.sub(r"\.(?=[A-Z])", ". ", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    # Strip leading time like "19:30 " that BetExplorer prefixes on day pages
    txt = _TIME_RE.sub("", txt, count=1).strip()
    # Strip trailing scoreline like " 1:3" or " 1:3, 6:2" etc.
    txt = _TRAIL_SCORE_RE.sub("", txt).strip()
    # Strip trailing odds like " 1.69 2.21" that leak from row text
    txt = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", txt).strip()
    parts = _NAME_SEP_RE.split(txt, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        # Right side may still carry trailing score/odds after dash split
        right = _TRAIL_SCORE_RE.sub("", right).strip()
        right = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", right).strip()
        left = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", left).strip()
        # Basic sanity: both sides must look at least vaguely like players
        if left and right:
            # Accept if either strict or loose check passes
            if _looks_like_player(left) or _looks_like_player_loose(left):
                if _looks_like_player(right) or _looks_like_player_loose(right):
                    return left, right
            # Even if loose fails, if both have >=3 chars and uppercase, accept (dash is strong signal)
            if len(left) >= 3 and len(right) >= 3 and left[:1].isupper() and right[:1].isupper():
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
    rejected_href_samples: list[str] = []
    for link in soup.find_all("a", href=True):
        n_links += 1
        href = str(link.get("href") or "")
        ctx = is_match_link(href)
        if ctx is None:
            if len(rejected_href_samples) < 5:
                rejected_href_samples.append(href)
            continue
        n_match += 1
        # Two-anchor matches resolve via the parent (names_text = joined text)
        home, away, names_text = _resolve_link_names(link, is_match_link)
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
    if n_match == 0 and n_links >= 20:
        # Zero match links on a populated listing = URL-shape drift. Log the
        # rejected hrefs so the next Actions run is debuggable without a
        # live re-fetch (2026-09-18: OddsPortal scan links=183 match_links=0).
        for sample in rejected_href_samples:
            logger.info("%s rejected-href sample: %r", source_label, sample[:160])
    return rows
