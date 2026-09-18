"""
Forebet Predictor Adapter
Extracts mathematical predictions and probabilities from Forebet pages.
Supports both tournament-specific pages and daily overview pages (yesterday/today/tomorrow).

Transport follows the Slumdog/Edge-Factory pattern: the Jina reader relay
(with explicit X-No-Cache / X-Return-Format headers) is the only cloud
transport; direct fetching is local-only; on a GitHub runner a relay failure
fails fast instead of burning the run on transports the provider blocks.
"""
from __future__ import annotations
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timedelta, timezone
from curl_cffi import requests
from bs4 import BeautifulSoup
from racketfactory.entities import normalize_player, player_key

logger = logging.getLogger(__name__)


def _forebet_price_to_decimal(value: object) -> float | None:
    """Parse Forebet decimal or American price text into decimal odds."""
    text = str(value or "").strip().replace("−", "-")
    if not text:
        return None

    try:
        # American odds, e.g. +160 or -227. Unsigned 1-2 digit integers are
        # far more likely probabilities ("79") than prices, so they are
        # rejected: a real unsigned American price has 3+ digits ("+100").
        if re.fullmatch(r"[+-]?\d+", text):
            if text[0] not in "+-" and len(text) < 3:
                return None
            american = int(text)
            if american > 0:
                return round(1.0 + american / 100.0, 6)
            if american < 0:
                return round(1.0 + 100.0 / abs(american), 6)
            return None

        # Decimal odds, e.g. 1.36 or 2.88.
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            val = float(text)
            return val if val > 1.0 else None

    except Exception:
        return None

    return None


def _forebet_prices_from_text(text: object) -> list[float]:
    """Extract decimal/American odds from a small odds-only text fragment."""
    out: list[float] = []
    for token in re.findall(r"(?<!\w)([+-]?\d+(?:\.\d+)?)(?!\w)", str(text or "")):
        price = _forebet_price_to_decimal(token)
        if price is not None:
            out.append(price)
    return out

# ---------------------------------------------------------------------------
# Tournament name -> Forebet slug mapping
# ---------------------------------------------------------------------------
TOURNAMENT_SLUGS: dict[str, str] = {
    "Australian Open": "australian-open",
    "French Open": "french-open",
    "Wimbledon": "wimbledon",
    "US Open": "us-open",
    "BNP Paribas Open": "indian-wells",
    "Miami Open": "miami",
    "Mutua Madrid Open": "madrid",
    "Internazionali BNL d'Italia": "rome",
    "Western & Southern Financial Group Masters": "cincinnati",
    "Western & Southern Financial Group Women's Open": "cincinnati",
    "Shanghai Masters": "shanghai",
    "China Open": "china-open",
    "Canadian Open": "toronto",
    "Monte Carlo Masters": "monte-carlo",
    "BNP Paribas Masters": "paris",
    "Dubai Duty Free Tennis Championships": "dubai",
    "Dubai Tennis Championships": "dubai",
    "Qatar Open": "doha",
    "Qatar Exxon Mobil Open": "doha",
    "Wuhan Open": "wuhan",
    "Charleston Open": "charleston",
    "Citi Open": "washington",
    "Winston-Salem Open at Wake Forest University": "winston-salem",
    "Eastbourne": "eastbourne",
    "Eastbourne International": "eastbourne",
    "Eastbourne Open": "eastbourne",
    "Halle": "halle",
    "Halle Open": "halle",
    "Queens": "queens",
    "Queen's Club Championships": "queens",
    "Stuttgart": "stuttgart",
    "Stuttgart Open": "stuttgart",
    "Porsche Tennis Grand Prix": "stuttgart",
    "Barcelona": "barcelona",
    "Barcelona Open": "barcelona",
    "Basel": "basel",
    "Swiss Indoors": "basel",
    "Vienna": "vienna",
    "Vienna Open": "vienna",
    "Paris": "paris",
    "Tokyo": "tokyo",
    "Japan Open": "tokyo",
    "Japan Open Tennis Championships": "tokyo",
    "Toray Pan Pacific Open Tennis Tournament": "tokyo",
    "Beijing": "beijing",
    "Doha": "doha",
    "Dubai": "dubai",
    "Marseille": "marseille",
    "Open 13": "marseille",
    "Metz": "metz",
    "Open de Moselle": "metz",
    "Rotterdam": "rotterdam",
    "ABN AMRO World Tennis Tournament": "rotterdam",
    "Umag": "umag",
    "Croatia Open": "umag",
    "Zurich": "zurich",
    "Delray Beach": "delray-beach",
    "Delray Beach Open": "delray-beach",
    "Acapulco": "acapulco",
    "Abierto Mexicano": "acapulco",
    "Adelaide": "adelaide",
    "Adelaide International": "adelaide",
    "Almaty": "almaty",
    "Almaty Open": "almaty",
    "Antwerp": "antwerp",
    "European Open": "antwerp",
    "Atlanta": "atlanta",
    "Atlanta Open": "atlanta",
    "Auckland": "auckland",
    "ASB Classic": "auckland",
    "Bastad": "bastad",
    "Nordea Open": "bastad",
    "Belgrade 2": "belgrade-2",
    "Belgrade Open": "belgrade",
    "Brisbane": "brisbane",
    "Brisbane International": "brisbane",
    "Brussels": "brussels",
    "Bucharest": "bucharest",
    "Buenos Aires": "buenos-aires",
    "Argentina Open": "buenos-aires",
    "Chengdu": "chengdu",
    "Chengdu Open": "chengdu",
    "Dallas": "dallas",
    "Dallas Open": "dallas",
    "Geneva": "geneva",
    "Geneva Open": "geneva",
    "Gstaad": "gstaad",
    "Suisse Open Gstaad": "gstaad",
    "Hamburg": "hamburg",
    "Hamburg Open": "hamburg",
    "German Open": "hamburg",
    "Hertogenbosch": "hertogenbosch",
    "Rosmalen Grass Court Championships": "hertogenbosch",
    "Hong Kong": "hong-kong",
    "Hong Kong Tennis Open": "hong-kong",
    "Houston": "houston",
    "U.S. Men's Clay Court Championships": "houston",
    "U.S.Men's Clay Court Championships": "houston",
    "Kitzbuhel": "kitzbuhel",
    "Generali Open": "kitzbuhel",
    "Laver Cup": "laver-cup",
    "London": "london",
    "Los Cabos": "los-cabos",
    "Los Cabos Open": "los-cabos",
    "Mallorca": "mallorca",
    "Mallorca Championships": "mallorca",
    "Newport": "newport",
    "Hall of Fame Championships": "newport",
    "Rio de Janeiro": "rio-de-janeiro",
    "Rio Open": "rio-de-janeiro",
    "Sydney": "sydney",
    "Chennai": "chennai",
    "Chennai Open": "chennai",
    "Chile Open": "santiago",
    "Copa Colsanitas": "bogota",
    "Cordoba": "cordoba",
    "Cordoba Open": "cordoba",
    "Estoril": "estoril",
    "Estoril Open": "estoril",
    "Grand Prix Hassan II": "marrakech",
    "Morocco Open": "rabat",
    "Guadalajara": "guadalajara",
    "Guadalajara Open": "guadalajara",
    "Guangzhou": "guangzhou",
    "Guangzhou Open": "guangzhou",
    "Hangzhou": "hangzhou",
    "Hangzhou Open": "hangzhou",
    "Hobart": "hobart",
    "Hobart International": "hobart",
    "Iasi": "iasi",
    "Iasi Open": "iasi",
    "Indian Wells": "indian-wells",
    "Lyon": "lyon",
    "Lyon Open": "lyon",
    "Madrid": "madrid",
    "Madrid Open": "madrid",
    "Merida": "merida",
    "Merida Open": "merida",
    "Monterrey": "monterrey",
    "Monterrey Open": "monterrey",
    "Montpellier": "montpellier",
    "Open Sud de France": "montpellier",
    "Munich": "munich",
    "BMW Open": "munich",
    "Ningbo": "ningbo",
    "Ningbo Open": "ningbo",
    "Nottingham": "nottingham",
    "Nottingham Open": "nottingham",
    "Palermo": "palermo",
    "Internazionali Femminili di Palermo": "palermo",
    "Prague": "prague",
    "Prague Open": "prague",
    "Rabat": "rabat",
    "San Diego": "san-diego",
    "San Diego Open": "san-diego",
    "Santiago": "santiago",
    "Seoul": "seoul",
    "Korea Open": "seoul",
    "Singapore": "singapore",
    "Singapore Open": "singapore",
    "Stockholm": "stockholm",
    "Nordic Open": "stockholm",
    "Strasbourg": "strasbourg",
    "Internationaux de Strasbourg": "strasbourg",
    "Thailand": "bangkok",
    "Thailand Open": "bangkok",
    "Thailand Open 2": "bangkok-2",
    "Transylvania": "cluj-napoca",
    "Transylvania Open": "cluj-napoca",
    "WTA Finals": "wta-finals",
    "Masters Cup": "atp-finals",
    "Bad Homburg": "bad-homburg",
    "Bad Homburg Open": "bad-homburg",
    "Budapest": "budapest",
    "Budapest Open": "budapest",
    "Jasmin": "monastir",
    "Jasmin Open": "monastir",
    "Jiangxi": "jiujiang",
    "Jiangxi Open": "jiujiang",
    "Ladies Linz": "linz",
    "Ladies Linz Open": "linz",
    "Linz": "linz",
    "Rouen": "rouen",
    "Open de Rouen": "rouen",
    "Sao Paulo": "sao-paulo",
    "SP Open": "sao-paulo",
    "Tennis in the Land": "cleveland",
    "Tiriac": "brasov",
    "Tiriac Open": "brasov",
    "Birmingham": "birmingham",
    "Rothesay Classic": "birmingham",
    "WTA Finals": "wta-finals",
    "ATX Open": "austin",
    "Abu Dhabi WTA Women's Tennis Open": "abu-dhabi",
    "Hellenic Championship": "athens",
    "Rothesay International": "eastbourne",
    "Nottingham Open": "nottingham",
    "Rothesay Classic": "birmingham",
}


def forebet_tour_slug(tour: str) -> str:
    t = tour.upper().strip()
    if t == "ATP":
        return "atp-singles"
    if t == "WTA":
        return "wta-singles"
    if t == "CHALLENGER":
        return "challenger-men"
    if t == "ITF":
        return "itf-men"
    return t.lower().replace(" ", "-")


def forebet_tournament_slug(tournament: str) -> str:
    if tournament in TOURNAMENT_SLUGS:
        return TOURNAMENT_SLUGS[tournament]
    # Best-effort slugify
    s = tournament.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s


def name_signature(name: str) -> str:
    """
    Canonical merge key for a player name, used by warehouse.py to align
    the same player across sources.

    Cross-site player names come in two layouts:
      - "Firstname Lastname"  (BetClan, PredixSport, ForeTennis)
      - "Lastname F."         (Forebet, some OddsPortal rows)

    The anagram-of-all-letters approach fails across these because a trailing
    initial contributes one letter that does not sort to the same place as a
    full given name (e.g. 'Zizou Bergs' -> 'begiorsuzz' vs 'Bergs Z.' ->
    'begorssz'). Use the last long word as the surname instead — both
    layouts agree on the surname. Single-character trailing tokens are
    treated as initials and dropped.

    Edge case: pure-initial names (rare, e.g. doubles seedings) fall back to
    a sorted signature so they still produce a stable key.
    """
    words = re.findall(r"[a-zA-Z]+", name)
    long_words = [w for w in words if len(w) > 1]
    if long_words:
        return long_words[-1].lower()
    return "".join(sorted(w.lower() for w in words))


def name_signature_strict(name: str) -> str:
    """Collision-resistant merge key: surname + given initial.

    :func:`name_signature` returns the bare surname, so ``Alexander Zverev``
    and ``Mischa Zverev`` share the key ``zverev`` and their predictions
    cross-attach in the warehouse join. The strict key appends the given
    initial from either layout (``Zverev A.`` -> ``zverev|a``,
    ``Alexander Zverev`` -> ``zverev|a``), keeping brothers apart while
    still joining ``Bergs Z.`` to ``Zizou Bergs``.

    Improvement (v5-matching): for multi-initial names like
    ``D. E. Galan`` vs ``Galan D. E.`` we now consistently pick the *first*
    single-letter token as the given initial, so both map to ``galan|d``
    instead of ``galan|d`` vs ``galan|e``.
    """
    words = re.findall(r"[a-zA-Z]+", str(name or ""))
    if not words:
        return ""
    long_words = [w for w in words if len(w) > 1]
    surname = long_words[-1].lower() if long_words else "".join(sorted(w.lower() for w in words))
    given = ""
    # First single-letter token anywhere is the most stable given initial
    for w in words:
        if len(w) == 1:
            given = w.lower()
            break
    if not given and long_words:
        given = long_words[0][0].lower()
    return f"{surname}|{given}" if given else surname





def _span_direct_text(span) -> str:
    """Return main score text from a Forebet score span.

    Tie-break points are nested in .tntbrk spans, e.g. 7<span>7</span>.
    We remove those child spans before reading the game score so 7(7)
    does not become 77.
    """
    if span is None:
        return ""
    clone = BeautifulSoup(str(span), "html.parser")
    for tb in clone.find_all(class_="tntbrk"):
        tb.extract()
    return clone.get_text("", strip=True)


def _parse_forebet_result_from_row(row) -> dict:
    """Extract finished result and set scores from a Forebet tennis row.

    Forebet's yesterday page embeds actual score in a .predQ set grid. Each
    set column has two spans: home score and away score. Bold marks the set
    winner, but we infer winner from numeric set scores to avoid relying on
    CSS/markup.
    """
    out = {
        "result_status": None,
        "result_score": None,
        "result_winner": None,       # "1" or "2" in Forebet home/away orientation
        "result_winner_name": None,
        "result_sets_home": None,
        "result_sets_away": None,
    }
    if row is None:
        return out

    text = row.get_text(" ", strip=True)
    if re.search(r"\bFT\b", text):
        out["result_status"] = "FT"

    pred_q = row.find("div", class_="predQ")
    if not pred_q:
        return out

    set_scores = []
    home_sets = 0
    away_sets = 0

    for col in pred_q.find_all("div", class_="fj_column"):
        spans = col.find_all("span", recursive=False)
        if len(spans) < 2:
            continue

        h_txt = _span_direct_text(spans[0])
        a_txt = _span_direct_text(spans[1])
        if not h_txt or not a_txt:
            continue

        try:
            h_val = int(re.sub(r"\D+", "", h_txt))
            a_val = int(re.sub(r"\D+", "", a_txt))
        except ValueError:
            continue

        set_scores.append(f"{h_val}-{a_val}")
        if h_val > a_val:
            home_sets += 1
        elif a_val > h_val:
            away_sets += 1

    if not set_scores:
        return out

    out["result_score"] = " ".join(set_scores)
    out["result_sets_home"] = home_sets
    out["result_sets_away"] = away_sets

    if home_sets > away_sets:
        out["result_winner"] = "1"
    elif away_sets > home_sets:
        out["result_winner"] = "2"

    return out


def _expected_iso_for_day(day: str) -> str | None:
    """Calendar date (YYYY-MM-DD) a Forebet daily page is supposed to list.

    ``day`` is yesterday/today/tomorrow or an explicit YYYY-MM-DD (Forebet
    serves /tennis/predictions/YYYY-MM-DD). Returns None for anything else.
    """
    label = str(day or "").strip()
    today = datetime.now().date()
    if label == "today":
        return today.strftime("%Y-%m-%d")
    if label == "yesterday":
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    if label == "tomorrow":
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
        return label
    return None


# Bump when the Jina/HTML row parser changes shape or semantics: fetch_cache
# stores PARSED rows, so without a key change a parser upgrade keeps serving
# stale parses until TTL expiry (run #208 served #207's pre-rewrite rows).
FOREBET_CACHE_VERSION = "j2"


def forebet_cache_key(day: str) -> str:
    """Cross-stage fetch_cache key for one Forebet daily page."""
    return f"forebet_{day}_{FOREBET_CACHE_VERSION}"


def _apply_page_day(rows: list[dict[str, Any]], expected_day: str | None, day_label: str) -> list[dict[str, Any]]:
    """Fill missing match dates with the daily page's own calendar day.

    Every row on predictions-yesterday/today/tomorrow belongs to that day by
    construction; when neither the link text (Jina) nor the date span (HTML)
    yields a date, assigning the page day beats dropping the row (run #206:
    all 43 predictions-tomorrow rows parsed dateless and were silently
    skipped downstream). Rows with unusable names are left dateless so the
    writer still skips them instead of storing garbage.
    """
    if not expected_day:
        return rows
    filled = 0
    unfillable = 0
    for r in rows:
        if r.get("match_date"):
            continue
        if r.get("player_home") and r.get("player_away"):
            r["match_date"] = expected_day
            filled += 1
        else:
            unfillable += 1
    if filled or unfillable:
        logger.info("Forebet %s: assigned page day %s to %d dateless rows (%d unfillable)",
                    day_label, expected_day, filled, unfillable)
    return rows


def _split_jina_players(players_part: str) -> tuple[str, str]:
    """Split a Jina link-text player field into (home, away).

    Jina fuses names without spaces ("A. ZverevB. Shelton") and players
    carry multiple initials ("J. D. Hara FriendM. Basing"), so whitespace
    token splitting misfires (it produced home="J." / home="A."). Split
    before the first initial (after the start) that leaves a surname token
    on both sides; fall back to the whitespace midpoint for initial-less
    full-name links.
    """
    text = str(players_part or "").strip()
    if not text:
        return "", ""

    def _has_surname(fragment: str) -> bool:
        for tok in re.split(r"\s+", fragment.strip()):
            if len(re.sub(r"[^A-Za-z]", "", tok)) > 1:
                return True
        return False

    for m in re.finditer(r"[A-Z]\.", text):
        if m.start() == 0:
            continue
        home, away = text[:m.start()].strip(), text[m.start():].strip()
        if _has_surname(home) and _has_surname(away):
            return home, away

    tokens = text.split()
    if len(tokens) >= 2:
        mid = len(tokens) // 2
        return " ".join(tokens[:mid]), " ".join(tokens[mid:])
    return text, ""


# ---------------------------------------------------------------------------
# Slumdog-style transport: relay first, direct local-only, fail fast on runners
# ---------------------------------------------------------------------------
RELAY_BASE = "https://r.jina.ai/"
# Header set Slumdog validated for tennis HTML via the relay (their 2026-08-23
# capture froze a 224,013-byte full tennis page with these; our headerless
# calls were served ~11KB stubs, almost certainly a poisoned reader cache).
# X-No-Cache defeats stale snapshots; X-Return-Format selects raw HTML so the
# body goes through parse_page rather than parse_jina_markdown.
# The User-Agent strings are Slumdog's exact validated UAs: its committed
# capture receipts show the relay serving fresh forebet boards from GitHub
# Actions daily with zero Cloudflare challenges, and only under those UAs.
# 2026-09-18: our "RacketFactory/1.0" UA was CF-challenged on every forebet
# fetch while Slumdog's 04:25Z run fetched the same tennis page fine.
def _auth_headers() -> dict[str, str]:
    """Jina Reader API key support — paid key bypasses anonymous shared IP CF challenges."""
    import os
    key = (os.getenv("JINA_API_KEY") or os.getenv("JINA_READER_API_KEY") or "").strip()
    if key:
        # Jina docs: Authorization: Bearer <key>
        return {"Authorization": f"Bearer {key}"}
    return {}

RELAY_HEADERS = {
    "User-Agent": "Slumdog",
    "Accept": "text/plain",
    "X-No-Cache": "true",
    "X-Return-Format": "html",
    **_auth_headers(),
}
# Reader-mode header set (no X-Return-Format): the single fallback flavor when
# html-mode returns a stub. Morning runs historically parsed full boards out
# of reader-mode markdown, so a page that stubs in one flavor may serve in
# the other. Reader-mode UA is Slumdog's EdgeFactory-validated string.
RELAY_HEADERS_MARKDOWN = {
    "User-Agent": "EdgeFactory/1.0",
    "Accept": "text/plain",
    "X-No-Cache": "true",
    **_auth_headers(),
}
# Relay statuses worth retrying (Slumdog _RETRY_STATUS). Any other 4xx
# (401/403/404) is deterministic per context and is never retried.
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
_RETRYABLE_ERRORS = (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError)


def on_github_runner() -> bool:
    """True on GitHub-hosted runners, where direct Forebet access is blocked.

    Edge-Factory's 2026-08-20 probe showed the provider refuses runner IPs
    even with browser TLS, so from that network the relay is the only route
    and a relay failure must fail fast instead of stalling the run.
    """
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"


def _sleep_with_jitter(attempt: int, base: float = 4.0, cap: float = 40.0) -> None:
    delay = min(cap, base * (2 ** attempt)) * (0.7 + 0.6 * random.random())
    time.sleep(delay)


def relay_status_file(day: str, root: str | Path = "localdata") -> Path:
    """Per-day relay health marker (gitignored, Actions-cached dir)."""
    return Path(root) / "fetch_cache" / f"forebet_relay_status_{day}.json"


def record_relay_status(status: str, day: str, root: str | Path = "localdata") -> None:
    """Persist today's relay health so later steps can skip a proven-bad route.

    Slumdog discipline: a satellite that has already failed its route is not
    re-attempted in the same run. The daily forebet fetch (3 pages, runs
    first) is the canary for the 50-page tournament backfill (runs second).
    """
    try:
        path = relay_status_file(day, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "day": day, "status": status,
            "at": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True))
    except Exception as e:
        logger.debug("Could not write forebet relay status marker: %s", e)


def read_relay_status(day: str, root: str | Path = "localdata") -> Optional[str]:
    """'challenged' when today's daily forebet fetch hit the CF wall; else None."""
    try:
        payload = json.loads(relay_status_file(day, root).read_text())
        return payload.get("status")
    except Exception:
        return None


def relay_get(url: str, timeout: int = 45, max_retries: int = 3,
              headers: dict[str, str] | None = None) -> bytes:
    """GET a relay URL with bounded retry/backoff for transient failures only.

    Port of Slumdog's relay_get: plain urllib (the relay is not
    Cloudflare-protected), Slumdog-validated headers, hard client errors
    (401/403/404) raised immediately without retry. ``headers`` overrides the
    default html-mode set (used once for the reader-mode fallback).

    Jina API key: if JINA_API_KEY env is set, injects Authorization Bearer
    at call time (paid key bypasses anonymous shared IP CF challenges).
    """
    base = dict(RELAY_HEADERS) if headers is None else dict(headers)
    # Dynamic auth injection — env may be set after import
    auth = _auth_headers()
    for k, v in auth.items():
        base.setdefault(k, v)
    send = base
    last_error: Exception | None = None
    for attempt in range(max_retries):
        request = urllib.request.Request(url, headers=send)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in _RETRY_STATUS:
                raise  # 401/403/404 are deterministic; do not retry
        except _RETRYABLE_ERRORS as exc:
            last_error = exc
        if attempt + 1 < max_retries:
            _sleep_with_jitter(attempt)
    assert last_error is not None
    raise last_error


class ForebetPredictor:
    """
    Handles extraction of pre-match predictions from Forebet.
    Supports two page types:
      1. Tournament pages      — /tennis/{tour}/{tournament}  (all matches for one tournament)
      2. Daily overview pages  — /predictions/YYYY-MM-DD (all matches across all tournaments;
         yesterday/today/tomorrow labels resolve to calendar dates up front)
    """
    BASE_URL = "https://www.forebet.com/en/tennis"

    def __init__(self, impersonate: str = "chrome133a"):
        self.impersonate = impersonate
        self._session = requests.Session()
        self._session.impersonate = impersonate
        # Cloudflare challenge streak on the relay (run 35311356382: 50/50
        # deep tournament pages were ~7.9KB "Just a moment..." shells that
        # passed the sport-label check and parsed to 0 silently).
        self._challenge_hits = 0
        self._relay_blocked = False

    @property
    def relay_blocked(self) -> bool:
        """True once the relay served CF challenge pages 3x consecutively."""
        return self._relay_blocked

    @staticmethod
    def _is_cf_challenge(text: str) -> bool:
        lower = text.lower()
        return ("just a moment" in lower
                or "challenge-error" in lower
                or "attention required" in lower)

    def _note_challenge(self, mode: str, url: str, text: str) -> None:
        self._challenge_hits += 1
        logger.warning(
            "Forebet relay (%s) returned a Cloudflare challenge page for %s "
            "(%d bytes) — discarded; head=%r", mode, url, len(text), text[:200],
        )
        if self._challenge_hits >= 3 and not self._relay_blocked:
            self._relay_blocked = True
            logger.warning(
                "Forebet relay blocked by Cloudflare for 3 consecutive pages "
                "— failing fast for this run (no further relay attempts)"
            )

    def _note_board_ok(self) -> None:
        self._challenge_hits = 0
        self._relay_blocked = False

    # ------------------------------------------------------------------
    # Low-level fetch (Slumdog-style: relay first, direct local-only)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_forebet_404(text: str) -> bool:
        lower = text.lower()
        return "not what you were looking for" in lower or "forebet 404 error" in lower

    def _relay_body(self, url: str, headers: dict[str, str], mode: str,
                    max_retries: int) -> Optional[str]:
        """One raw relay GET in the given header flavor; None on transport failure."""
        try:
            body = relay_get(RELAY_BASE + url, timeout=45, max_retries=max_retries,
                             headers=headers)
        except urllib.error.HTTPError as exc:
            logger.warning("Forebet relay (%s) HTTP %s for %s (not retried)", mode, exc.code, url)
            return None
        except Exception as exc:
            logger.warning("Forebet relay (%s) failed for %s: %s: %s",
                           mode, url, type(exc).__name__, exc)
            return None
        return body.decode("utf-8", "replace")

    def _looks_like_board(self, text: str, url: str, expect_matches: bool, mode: str) -> bool:
        """True when a relay snapshot looks like a real board.

        Discards log the snapshot head (~300 chars) so the next run identifies
        stub species from evidence instead of guessing (run #220: identical
        5919-byte stubs for two different dates).
        """
        if self._is_cf_challenge(text):
            # Challenge shells echo the requested URL (which contains
            # /tennis/), so they would pass the sport-label check below and
            # parse to 0 predictions silently.
            self._note_challenge(mode, url, text)
            return False
        if self._is_forebet_404(text):
            logger.warning("Forebet relay (%s) returned a 404 content page for %s", mode, url)
            return False
        if "tennis" not in text.lower():
            logger.warning("Forebet relay (%s) snapshot for %s lacks the sport label "
                           "(%d bytes) — discarded; head=%r",
                           mode, url, len(text), text[:300])
            return False
        if expect_matches and "/tennis/matches/" not in text:
            logger.warning("Forebet relay (%s) snapshot for %s lacks match links "
                           "(%d bytes) — discarded; head=%r",
                           mode, url, len(text), text[:300])
            return False
        return True

    def _fetch_via_relay(self, url: str, expect_matches: bool = True) -> Optional[str]:
        """Fetch a Forebet page through the Jina reader relay (Slumdog route).

        The relay's servers fetch the page, not the GitHub runner IP, so the
        runner 403 is avoided. Html-mode first; a stub (not a transport
        failure, not a Forebet 404) gets ONE reader-mode retry, since morning
        runs parsed full boards out of reader-mode markdown. Returns decoded
        body text, or None. ``expect_matches`` requires /tennis/matches/
        links (daily boards always have matches); tournament pages pass with
        the looser sport-label check since an empty board is legitimate there.
        """
        html = self._relay_body(url, RELAY_HEADERS, "html", max_retries=3)
        if html is None:
            return None
        if self._looks_like_board(html, url, expect_matches, "html"):
            self._note_board_ok()
            logger.info("Forebet fetched via relay (html) for %s (%d bytes)", url, len(html))
            return html
        if self._is_forebet_404(html):
            return None  # genuine Forebet 404: reader mode would 404 too
        logger.info("Forebet relay html-mode stub for %s — retrying once in reader mode", url)
        md = self._relay_body(url, RELAY_HEADERS_MARKDOWN, "markdown", max_retries=2)
        if md is None:
            return None
        if self._looks_like_board(md, url, expect_matches, "markdown"):
            self._note_board_ok()
            logger.info("Forebet fetched via relay (markdown) for %s (%d bytes)", url, len(md))
            return md
        return None

    def parse_jina_markdown(self, md_text: str, expected_day: str | None = None) -> list[dict[str, Any]]:
        """Parse Jina markdown output for Forebet predictions-today/yesterday/tomorrow.

        ``expected_day`` is the page's calendar day (YYYY-MM-DD); when given,
        ambiguous numeric dates prefer the reading that matches it.
        Markdown structure (from r.jina.ai):
          Tournament heading line (e.g. 'ATP US Open - Semi-finals')
          [F. Tiafoe B. Shelton 12/09/2026 01:45](https://www.forebet.com/en/tennis/matches/atp-singles/us-open/...)
          41 59   -> prob_home prob_away
          2 1-3   -> pred winner + predicted score
          ...
          FT
          1**3**  -> final result
        We extract at least player_home/away, date, prob, predicted_winner, tournament, tour/tournament slug, and result if present.
        """
        results = []
        lines = [l.strip() for l in md_text.splitlines()]
        current_tournament = None
        dated_rows = 0
        date_mismatches = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            if line and " - " in line and not line.startswith("[") and not line.startswith("![") and len(line) < 80 and not line.startswith("*") and not line.startswith("Tennis predictions"):
                j = i+1
                while j < len(lines) and not lines[j]:
                    j+=1
                if j < len(lines) and lines[j].startswith("[") and "/tennis/matches/" in lines[j]:
                    current_tournament = line
            if line.startswith("[") and "/tennis/matches/" in line:
                m = re.match(r"\[(.+?)\]\((https?://[^)]+)\)", line)
                if m:
                    link_text = m.group(1).strip()
                    url = m.group(2).strip()
                    date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})(?:\s+(\d{1,2}:\d{2})(?:\s*([AP]M))?)?", link_text)
                    match_date = None
                    match_time = ""
                    players_part = link_text
                    if date_match:
                        date_str = date_match.group(1)
                        time_str = date_match.group(2) or ""
                        ampm = (date_match.group(3) or "").upper()
                        players_part = link_text[:date_match.start()].strip()
                        if time_str and ampm in ("AM", "PM"):
                            try:
                                _tt = time_str.split(":")
                                _hh, _mm = int(_tt[0]), _tt[1]
                                if ampm == "PM" and _hh < 12:
                                    _hh += 12
                                elif ampm == "AM" and _hh == 12:
                                    _hh = 0
                                time_str = f"{_hh:02d}:{_mm}"
                            except (ValueError, IndexError):
                                pass
                        # Forebet's Jina date rendering is NOT format-stable:
                        # most pages print US MM/DD/YYYY ("09/11/2026" =
                        # Sep 11) but predictions-yesterday printed DD/MM
                        # ("12/09/2026" = Sep 12, observed run #206: 28 rows
                        # misfiled as 2026-12-09). Collect BOTH readings; when
                        # the caller passes the page's calendar day, prefer the
                        # reading that matches it. Without expected_day the
                        # historical MM/DD-first order is kept.
                        candidates = []
                        for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
                            try:
                                iso = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
                                if iso not in candidates:
                                    candidates.append(iso)
                            except ValueError:
                                continue
                        if candidates:
                            dated_rows += 1
                            match_time = time_str
                            if expected_day and expected_day in candidates:
                                match_date = expected_day
                            else:
                                match_date = candidates[0]
                            if expected_day and match_date != expected_day:
                                date_mismatches += 1
                                if date_mismatches <= 3:
                                    logger.debug("Jina row date %s != page day %s (token %s)",
                                                 match_date, expected_day, date_str)
                    else:
                        # No parseable date: strip trailing date/time words so
                        # they don't pollute the away name (which would break
                        # downstream name-signature matching).
                        players_part = re.sub(r"\s*\b(today|tomorrow|yesterday)\b\s*$", "", players_part, flags=re.IGNORECASE)
                        players_part = re.sub(r"\s*\b\d{1,2}:\d{2}\b\s*$", "", players_part)
                    home, away = _split_jina_players(players_part)
                    tour_slug = ""
                    tournament_slug = ""
                    tm = re.search(r"/tennis/matches/([^/]+)/([^/]+)/", url)
                    if tm:
                        tour_slug = tm.group(1)
                        tournament_slug = tm.group(2)
                    prob_home = None
                    prob_away = None
                    predicted_winner = None
                    lookahead = []
                    for k in range(i+1, min(i+10, len(lines))):
                        if lines[k]:
                            lookahead.append(lines[k])
                        if len(lookahead) >= 5:
                            break
                    for la in lookahead:
                        mprob = re.match(r"^(\d{1,3})\s+(\d{1,3})$", la)
                        if mprob:
                            try:
                                ph = int(mprob.group(1))
                                pa = int(mprob.group(2))
                                if 0 <= ph <= 100 and 0 <= pa <= 100 and ph+pa>=80:
                                    prob_home = ph
                                    prob_away = pa
                                    break
                            except:
                                pass
                    for la in lookahead:
                        if re.match(r"^[12]\s+\d+\-\d+", la):
                            predicted_winner = la.split()[0]
                            break
                        if la in ("1","2") and predicted_winner is None:
                            predicted_winner = la
                    result_status = None
                    result_score = None
                    result_winner = None
                    result_sets_home = None
                    result_sets_away = None
                    odds_home = None
                    odds_away = None
                    # Positional Coef parse: each Jina match block ends its stat
                    # lines with avg-games-per-set, then the single Coef (an
                    # American price like -152, a decimal like 1.66, or "-"
                    # when no market is shown). The old pair-hunt mistook
                    # avg-games for a price ([10.4, 1.66]) or duplicated one
                    # coef onto both sides ([1.66, 1.66], which even passes
                    # two-way validation) — both poison downstream pricing.
                    coef_val = None
                    for k in range(i + 1, min(i + 15, len(lines))):
                        lk = lines[k]
                        if not lk or lk.startswith("!["):
                            continue
                        if lk.startswith("[") or lk == "FT":
                            break
                        if re.fullmatch(r"\d+\.\d+", lk):
                            try:
                                first_decimal = float(lk)
                            except ValueError:
                                break
                            if 6.0 <= first_decimal <= 12.5:
                                # Avg-games line: the next significant line is
                                # the coef (or "-" for no market).
                                for kk in range(k + 1, min(i + 15, len(lines))):
                                    cand = lines[kk]
                                    if not cand or cand.startswith("!["):
                                        continue
                                    if cand.strip() in {"-", "–", "—"}:
                                        coef_val = None
                                    else:
                                        coef_val = _forebet_price_to_decimal(cand)
                                    break
                            else:
                                # No avg line (format drift): a lone decimal
                                # outside avg range is a coef candidate itself.
                                coef_val = _forebet_price_to_decimal(lk)
                            break
                    if coef_val is not None and prob_home is not None and prob_away is not None:
                        # Attribute the single coef to the side whose model
                        # probability it matches (tie: home). Beyond 15pts of
                        # gap the snapshot is stale/structural garbage (e.g.
                        # 1.02 on a 27/73 match) — discard, don't mislabel.
                        implied = 1.0 / coef_val
                        gap_home = abs(implied - prob_home / 100.0)
                        gap_away = abs(implied - prob_away / 100.0)
                        if min(gap_home, gap_away) <= 0.15:
                            if gap_home <= gap_away:
                                odds_home = coef_val
                            else:
                                odds_away = coef_val
                        else:
                            logger.debug("Jina coef %s (implied %.1f%%) matches neither side (%s/%s) — discarded",
                                         coef_val, implied * 100.0, prob_home, prob_away)
                    for k in range(i+1, min(i+20, len(lines))):
                        if lines[k] == "FT":
                            result_status = "FT"
                            for kk in range(k+1, min(k+5, len(lines))):
                                if lines[kk]:
                                    cleaned = lines[kk].replace("*","").strip()
                                    orig = lines[kk]
                                    nums = re.findall(r"\d+", cleaned)
                                    if len(nums) >= 2:
                                        try:
                                            h = int(nums[0])
                                            a = int(nums[1])
                                            result_sets_home = h
                                            result_sets_away = a
                                            result_score = f"{h}-{a}"
                                            if h > a:
                                                result_winner = "1"
                                            elif a > h:
                                                result_winner = "2"
                                            break
                                        except:
                                            pass
                            break
                    results.append({
                        "match_date": match_date,
                        "match_time": match_time,
                        "player_home": home,
                        "player_away": away,
                        "prob_home": prob_home,
                        "prob_away": prob_away,
                        "odds_home": odds_home,
                        "odds_away": odds_away,
                        "predicted_winner": predicted_winner,
                        "tournament": current_tournament,
                        "tour_slug": tour_slug,
                        "tournament_slug": tournament_slug,
                        "result_status": result_status,
                        "result_score": result_score,
                        "result_winner": result_winner,
                        "result_winner_name": None,
                        "result_sets_home": result_sets_home,
                        "result_sets_away": result_sets_away,
                        "source": "Forebet",
                    })
            i+=1
        logger.info("Parsed %d predictions from Jina markdown", len(results))
        if expected_day and dated_rows:
            logger.info("Jina row dates vs page day %s: %d/%d match",
                        expected_day, dated_rows - date_mismatches, dated_rows)
        dateless = len(results) - dated_rows
        if dateless:
            logger.info("Jina markdown: %d/%d rows had no parseable date token",
                        dateless, len(results))
        return results

    def _fetch_via_playwright(self, url: str, timeout_ms: int = 120000) -> Optional[str]:

        """Playwright fallback for Forebet — bypasses Cloudflare via real browser."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not installed — cannot fetch Forebet via browser")
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="UTC",
                )
                # Stealth
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                    window.navigator.chrome = {runtime: {}};
                """)
                page = context.new_page()
                try:
                    page.goto(url, wait_until="commit", timeout=timeout_ms)
                except Exception as e:
                    logger.warning("Forebet Playwright goto failed for %s: %s", url, e)
                # Wait for predictions to appear
                try:
                    page.wait_for_selector("a.tnmscn, .rcnt, .heading", timeout=30000)
                except Exception:
                    pass
                # Extra wait for Cloudflare
                try:
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
                html = ""
                try:
                    html = page.content()
                except Exception as e:
                    logger.warning("Forebet Playwright content failed: %s", e)
                context.close()
                browser.close()
                if html and len(html) > 1000:
                    if self._is_cf_challenge(html):
                        logger.warning("Forebet Playwright still got CF challenge for %s", url)
                        return None
                    return html
                return None
        except Exception as e:
            logger.warning("Forebet Playwright error for %s: %s", url, e)
            return None

    def _fetch_direct(self, url: str) -> Optional[str]:
        """Direct Forebet fetch: curl_cffi impersonations, cloudscraper, Playwright.

        LOCAL-ONLY transport: the provider blocks GitHub runner IPs even with
        browser TLS, so _fetch only calls this off-runner. On a runner a relay
        failure fails fast instead of stalling here.
        """
        # Common headers
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.forebet.com/",
            "Accept-Encoding": "gzip, deflate, br",
        }

        # 1. Try curl_cffi with rotating impersonations (most reliable for CF)
        try:
            from curl_cffi import requests as cffi_requests
            imps = [
                "chrome131",
                "chrome126",
                "chrome124",
                "chrome120",
                "chrome110",
                "safari17_2",
                "safari15_5",
                "firefox133",
                "chrome133a",
            ]
            for imp in imps:
                try:
                    # Use session-less get to avoid stale cookies
                    resp = cffi_requests.get(url, impersonate=imp, headers=headers, timeout=30)
                    if resp.status_code == 200:
                        txt = resp.text
                        if self._is_cf_challenge(txt):
                            logger.info("Forebet CF challenge with %s for %s", imp, url)
                            continue
                        # Must have some prediction marker or reasonable size
                        if len(txt) < 2000:
                            continue
                        # If page contains tnmscn it's definitely good
                        if "tnmscn" in txt or "homeTeam" in txt:
                            logger.info("Forebet fetched via curl_cffi %s for %s (%d bytes)", imp, url, len(txt))
                            return txt
                        # Otherwise still return if 200 and not challenge (tournament page may have 0 matches)
                        if len(txt) > 5000:
                            logger.info("Forebet fetched via curl_cffi %s (generic) for %s", imp, url)
                            return txt
                    elif resp.status_code == 403:
                        logger.warning("Forebet 403 with %s for %s", imp, url)
                        continue
                    else:
                        logger.warning("Forebet %d with %s for %s", resp.status_code, imp, url)
                except Exception as e:
                    logger.debug("Forebet curl_cffi %s failed for %s: %s", imp, url, e)
                    continue
        except ImportError:
            logger.warning("curl_cffi not installed for Forebet")
        except Exception as e:
            logger.warning("Forebet curl_cffi outer error: %s", e)

        # 2. Try cloudscraper if available (undetected)
        try:
            import cloudscraper
            scraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
            )
            r = scraper.get(url, headers=headers, timeout=30)
            if r.status_code == 200 and not self._is_cf_challenge(r.text) and len(r.text) > 2000:
                logger.info("Forebet fetched via cloudscraper for %s", url)
                return r.text
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Forebet cloudscraper failed for %s: %s", url, e)

        # 3. Playwright fallback — real browser can clear CF
        logger.info("Forebet falling back to Playwright for %s", url)
        html = self._fetch_via_playwright(url)
        if html:
            logger.info("Forebet fetched via Playwright for %s (%d bytes)", url, len(html))
            return html

        logger.error("Forebet failed to fetch %s via all direct methods", url)
        return None

    def _fetch(self, url: str, expect_matches: bool = True) -> Optional[str]:
        """Relay first; direct is local-only; fail fast on GitHub runners.

        Slumdog route policy: the relay is the only cloud transport. When it
        fails on a runner there is no second chance (direct is blocked from
        that network), so return None immediately and let the date stay
        retryable instead of stalling the run. Off-runner, fall through to
        the direct transport chain.
        """
        text = self._fetch_via_relay(url, expect_matches=expect_matches)
        if text is not None:
            return text
        if on_github_runner():
            logger.warning("Forebet relay failed on a GitHub runner for %s — "
                           "failing fast (direct is blocked from this network)", url)
            return None
        logger.info("Forebet relay failed locally for %s — trying direct transports", url)
        return self._fetch_direct(url)


    # ------------------------------------------------------------------
    # Tournament page fetch
    # ------------------------------------------------------------------
    def _fetch_tournament_page(self, tour_slug: str, tournament_slug: str) -> Optional[str]:
        url = f"{self.BASE_URL}/{tour_slug}/{tournament_slug}"
        return self._fetch(url, expect_matches=False)

    # ------------------------------------------------------------------
    # Daily overview page fetch
    # ------------------------------------------------------------------
    def _fetch_daily_page(self, day: str = "today") -> Optional[str]:
        """Fetch a daily predictions page via its date-addressable URL.

        Slumdog rule: always /tennis/predictions/YYYY-MM-DD, never wall-clock
        labels (the runner's "today" and Forebet's "today" can disagree across
        timezones). Labels are resolved to calendar dates up front, so the
        fetched board and ``expected_day`` agree by construction. Note the
        slash shape: the old dash-shaped explicit URL
        (/tennis/predictions-YYYY-MM-DD) 404s — verified 2026-09-13.
        """
        label = str(day or "").strip()
        if label in ("yesterday", "today", "tomorrow"):
            iso_day = _expected_iso_for_day(label)
            assert iso_day is not None
            url = f"{self.BASE_URL}/predictions/{iso_day}"
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
            url = f"{self.BASE_URL}/predictions/{label}"
        else:
            raise ValueError("day must be 'yesterday', 'today', 'tomorrow' or YYYY-MM-DD")
        return self._fetch(url)

    # ------------------------------------------------------------------
    # Unified parser — works on both tournament pages and daily pages
    # ------------------------------------------------------------------
    def parse_page(self, html: str, expected_day: str | None = None) -> list[dict[str, Any]]:
        """
        Parse any Forebet page containing tennis match predictions.
        """
        soup = BeautifulSoup(html, "html.parser")
        results = []

        match_rows = soup.find_all("a", class_="tnmscn")
        for anchor in match_rows:
            href = str(anchor.get("href", "") or "")
            if "/tennis/" not in href:
                continue

            tour_slug = ""
            tournament_slug = ""
            m = re.search(r"/tennis/matches/([^/]+)/([^/]+)/", href)
            if m:
                tour_slug = m.group(1).strip()
                tournament_slug = m.group(2).strip()

            # --- Players ---------------------------------------------------
            home_span = anchor.find("span", class_="homeTeam")
            away_span = anchor.find("span", class_="awayTeam")
            if not home_span or not away_span:
                continue
            home = home_span.get_text(strip=True)
            away = away_span.get_text(strip=True)
            if not home or not away:
                continue

            # --- Date & Time -----------------------------------------------
            # FIX: Accept both %d/%m/%Y %H:%M and MM/DD/YYYY h:MM AM/PM (Forebet displayed 09/11/2026 6:15 AM)
            date_span = anchor.find("span", class_="date_bah")
            match_date = None
            match_time = ""
            if date_span:
                date_text = date_span.get_text(strip=True)
                # Try multiple formats
                for fmt in ("%d/%m/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %H:%M"):
                    try:
                        dt = datetime.strptime(date_text, fmt)
                        match_date = dt.strftime("%Y-%m-%d")
                        match_time = dt.strftime("%H:%M")
                        break
                    except ValueError:
                        continue
                # Fallback: try to extract date with regex if strptime fails
                if not match_date:
                    import re as _re
                    # Look for MM/DD/YYYY or DD/MM/YYYY
                    m = _re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_text)
                    if m:
                        try:
                            # Try both interpretations, prefer MM/DD if first <=12 and second >12 or context
                            # Forebet uses MM/DD/YYYY in US format per note: 09/11/2026 6:15 AM = Sep 11
                            # So try MM/DD first
                            for fmt2 in ("%m/%d/%Y", "%d/%m/%Y"):
                                try:
                                    dt = datetime.strptime(f"{m.group(1)}/{m.group(2)}/{m.group(3)}", fmt2)
                                    match_date = dt.strftime("%Y-%m-%d")
                                    break
                                except ValueError:
                                    continue
                        except Exception:
                            pass

            # --- Tournament -----------------------------------------------
            tournament_name = None
            if tournament_slug:
                tournament_name = tournament_slug.replace("-", " ").title()
            row_container = anchor.find_parent("div", class_="rcnt")
            if not row_container:
                row_container = anchor.find_parent("div")
            if row_container:
                heading = row_container.find("div", class_="heading")
                if heading is None:
                    heading = row_container.find_previous("div", class_="heading")
                if heading:
                    heading_text = heading.get_text(" ", strip=True)
                    if heading_text:
                        tournament_name = heading_text

            # --- Probabilities, Odds & Prediction --------------------------
            prob_home = None
            prob_away = None
            predicted_winner = None
            odds_home = None
            odds_away = None
            result_info = {}

            row = row_container
            if row:
                fprc = row.find("div", class_="fprc")
                if fprc:
                    spans = fprc.find_all("span")
                    if len(spans) >= 2:
                        try:
                            prob_home = int(spans[0].get_text(strip=True))
                            prob_away = int(spans[1].get_text(strip=True))
                        except ValueError:
                            pass

                # Correctly match predict containers on today/tomorrow pages as well as yesterday
                pred_div = row.find("div", class_=re.compile(r"predict", re.I))
                if pred_div:
                    forepr = pred_div.find("span", class_="forepr")
                    if forepr:
                        inner = forepr.find("span")
                        if inner:
                            predicted_winner = inner.get_text(strip=True)
                        else:
                            predicted_winner = forepr.get_text(strip=True)
                    else:
                        txt = pred_div.get_text(strip=True)
                        if txt in ("1", "2"): predicted_winner = txt

                # Prefer Forebet's hidden two-way odds container. The visible
                # selected-price cell and nearby avg_score values can otherwise
                # pollute side mapping (e.g. 9.3 being read as home odds).
                haodd = row.find("div", class_="haodd")
                if haodd:
                    prices = _forebet_prices_from_text(haodd.get_text(" ", strip=True))
                    if len(prices) >= 2:
                        odds_home, odds_away = prices[0], prices[1]

                # Robustly check odds containers across possible tags/classes
                # only if the explicit two-way container was unavailable.
                if odds_home is None or odds_away is None:
                    odd_spans = row.find_all(["span", "div", "button", "a"], class_=re.compile(r"odd|pOdd|avg_odd|bot_odd|lrg_odd|price|val|bet", re.I))
                    for osp in odd_spans:
                        # Skip score/average-score widgets; they can contain
                        # decimal-looking values that are not two-way prices.
                        class_val = osp.get("class", []) if hasattr(osp, "get") else []
                        if isinstance(class_val, str): class_val = [class_val]
                        classes = " ".join(class_val or []).lower()
                        if "avg_sc" in classes or "ex_sc" in classes:
                            continue

                        txt = osp.get_text(" ", strip=True)
                        prices = _forebet_prices_from_text(txt)
                        if len(prices) >= 2:
                            odds_home, odds_away = prices[0], prices[1]
                            break

                if odds_home is None or odds_away is None:
                    # Final fallback: use only the explicit hidden odds text if
                    # present; do not scan the whole row because score widgets
                    # and ranking/probability values create false prices.
                    haodd = row.find("div", class_="haodd")
                    if haodd:
                        prices = _forebet_prices_from_text(haodd.get_text(" ", strip=True))
                        if len(prices) >= 2:
                            odds_home, odds_away = prices[0], prices[1]

                result_info = _parse_forebet_result_from_row(row)
                if result_info.get("result_winner") == "1":
                    result_info["result_winner_name"] = home
                elif result_info.get("result_winner") == "2":
                    result_info["result_winner_name"] = away

            results.append({
                "match_date": match_date,
                "match_time": match_time,
                "player_home": home,
                "player_away": away,
                "prob_home": prob_home,
                "prob_away": prob_away,
                "odds_home": odds_home,
                "odds_away": odds_away,
                "predicted_winner": predicted_winner,
                "tournament": tournament_name,
                "tour_slug": tour_slug,
                "tournament_slug": tournament_slug,
                "result_status": result_info.get("result_status"),
                "result_score": result_info.get("result_score"),
                "result_winner": result_info.get("result_winner"),
                "result_winner_name": result_info.get("result_winner_name"),
                "result_sets_home": result_info.get("result_sets_home"),
                "result_sets_away": result_info.get("result_sets_away"),
                "source": "Forebet",
            })

        logger.info("Parsed %d predictions from Forebet page", len(results))
        return results

    # ------------------------------------------------------------------
    # Public API: tournament predictions
    # ------------------------------------------------------------------
    def fetch_tournament_predictions(self, tour: str, tournament: str) -> list[dict[str, Any]]:
        """
        Fetch and parse predictions for a given tour + tournament.
        Returns list of raw prediction dicts (home/away orientation).
        Deep search fix: tournament pages via relay return HTML without tnmscn anchors,
        but Jina markdown contains match blocks — try both parsers.
        """
        if self._relay_blocked:
            # Relay is CF-challenged for this run: each page would be another
            # identical shell. Skip without burning a relay call.
            return []
        tour_slug = forebet_tour_slug(tour)
        tourn_slug = forebet_tournament_slug(tournament)
        body = self._fetch_tournament_page(tour_slug, tourn_slug)
        if not body:
            return []
        preds = []
        stripped = body.lstrip().lower()
        if stripped.startswith("<") or "<html" in stripped:
            preds = self.parse_page(body)
            if not preds:
                # Fallback: try Jina markdown parser on HTML that may be markdown-wrapped
                try:
                    preds = self.parse_jina_markdown(body)
                except Exception:
                    pass
        else:
            # Relay returned markdown wrapper
            try:
                preds = self.parse_jina_markdown(body)
            except Exception:
                preds = self.parse_page(body)
        for p in preds:
            p["tournament"] = tournament
        return preds

    # ------------------------------------------------------------------
    # Public API: daily overview predictions
    # ------------------------------------------------------------------
    def fetch_daily_predictions(self, day: str = "today") -> list[dict[str, Any]]:
        """
        Fetch all predictions for a given day across ALL tournaments.
        day: 'yesterday', 'today', 'tomorrow', or an explicit YYYY-MM-DD.
        Returns list of raw prediction dicts.

        Transport (_fetch) is relay-first with a local-only direct fallback,
        so by parse time there is nothing left to retry: a missing body means
        the date stays empty — and retryable next run, since the fetch cache
        never stores empty results.
        """
        expected_day = _expected_iso_for_day(day)
        if expected_day is None:
            raise ValueError("day must be 'yesterday', 'today', 'tomorrow' or YYYY-MM-DD")
        body = self._fetch_daily_page(day)
        if not body:
            logger.warning("Forebet %s: no body fetched (relay failed%s)", day,
                           " — failing fast on runner" if on_github_runner() else "")
            return []
        stripped = body.lstrip().lower()
        if stripped.startswith("<") or "<html" in stripped:
            return _apply_page_day(self.parse_page(body, expected_day), expected_day, day)
        if ("Markdown Content:" in body or "URL Source:" in body
                or ("](" in body and "/tennis/matches/" in body)):
            # Relay served the reader-mode markdown wrapper (X-Return-Format
            # ignored) rather than raw HTML.
            try:
                parsed = self.parse_jina_markdown(body, expected_day)
            except Exception as e:
                logger.warning("Jina markdown parse failed for %s: %s", day, e)
                return []
            return _apply_page_day(parsed, expected_day, day)
        return _apply_page_day(self.parse_page(body, expected_day), expected_day, day)

    # ------------------------------------------------------------------
    # Mapping: align Forebet prediction to warehouse orientation
    # ------------------------------------------------------------------
    def map_prediction_to_player(
        self, pred: dict[str, Any], player_a: str, player_b: str
    ) -> Optional[dict[str, Any]]:
        """
        Map a Forebet prediction (home/away orientation) to the warehouse
        player_a / player_b orientation using name-signature matching.
        """
        sig_a = name_signature(player_a)
        sig_b = name_signature(player_b)
        sig_home = name_signature(pred["player_home"])
        sig_away = name_signature(pred["player_away"])

        if sig_a == sig_home:
            home_is_a = True
        elif sig_b == sig_home:
            home_is_a = False
        elif sig_a == sig_away:
            home_is_a = False
        elif sig_b == sig_away:
            home_is_a = True
        else:
            logger.debug(
                "Cannot map %s/%s to %s/%s",
                pred["player_home"], pred["player_away"], player_a, player_b,
            )
            return None

        predicted_winner = pred.get("predicted_winner")
        prob = None
        if predicted_winner in ("1", "player_a"):
            winner = "player_a" if home_is_a else "player_b"
            prob = pred["prob_home"] / 100 if pred["prob_home"] is not None else None
        elif predicted_winner in ("2", "player_b"):
            winner = "player_b" if home_is_a else "player_a"
            prob = pred["prob_away"] / 100 if pred["prob_away"] is not None else None
        else:
            return None

        return {
            "predicted_winner": winner,
            "prediction_prob": prob,
            "source": "Forebet",
        }