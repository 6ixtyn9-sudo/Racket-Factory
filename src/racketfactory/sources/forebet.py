"""
Forebet Predictor Adapter
Extracts mathematical predictions and probabilities from Forebet pages.
Supports both tournament-specific pages and daily overview pages (yesterday/today/tomorrow).
"""
from __future__ import annotations
import logging
import re
from typing import Any, Optional
from datetime import datetime, timedelta
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
    """
    words = re.findall(r"[a-zA-Z]+", str(name or ""))
    if not words:
        return ""
    long_words = [w for w in words if len(w) > 1]
    surname = long_words[-1].lower() if long_words else "".join(sorted(w.lower() for w in words))
    given = ""
    if len(words[0]) == 1:
        given = words[0].lower()
    elif len(words[-1]) == 1:
        given = words[-1].lower()
    elif long_words:
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


class ForebetPredictor:
    """
    Handles extraction of pre-match predictions from Forebet.
    Supports two page types:
      1. Tournament pages      — /tennis/{tour}/{tournament}  (all matches for one tournament)
      2. Daily overview pages  — /predictions-{today|tomorrow|yesterday} (all matches across all tournaments)
    """
    BASE_URL = "https://www.forebet.com/en/tennis"

    def __init__(self, impersonate: str = "chrome133a"):
        self.impersonate = impersonate
        self._session = requests.Session()
        self._session.impersonate = impersonate

    # ------------------------------------------------------------------
    # Low-level fetch (curl_cffi + Playwright fallback)
    # ------------------------------------------------------------------

    def _fetch_via_jina(self, url: str) -> Optional[str]:
        """Lightweight fallback via Jina AI Reader (https://r.jina.ai/) which bypasses Cloudflare.
        Jina's servers fetch the page, not the GitHub runner IP, so 403 is avoided.
        Returns markdown text if successful, else None.
        """
        jina_url = f"https://r.jina.ai/{url}"
        # Try curl_cffi first (Jina is not CF protected, but use impersonation for safety)
        try:
            from curl_cffi import requests as curl_requests
            for imp in ["chrome133a", "chrome124", "safari18"]:
                try:
                    r = curl_requests.get(jina_url, impersonate=imp, timeout=30)
                    if r.status_code == 200 and len(r.text) > 2000 and "Tennis predictions" in r.text:
                        logger.info(f"Forebet recovered via Jina {imp} for {url} (len={len(r.text)})")
                        return r.text
                except Exception:
                    continue
        except Exception:
            pass
        # Fallback to standard requests if available
        try:
            import requests as std_requests
            r = std_requests.get(jina_url, timeout=30, headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"})
            if r.status_code == 200 and len(r.text) > 2000 and "Tennis predictions" in r.text:
                logger.info(f"Forebet recovered via Jina std for {url} (len={len(r.text)})")
                return r.text
        except Exception as e:
            logger.warning(f"Forebet Jina std failed for {url}: {e}")
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
                    tokens = players_part.split()
                    init_positions = [idx for idx, tok in enumerate(tokens) if re.match(r"^[A-Z]\.$", tok)]
                    home = None
                    away = None
                    if len(init_positions) >= 2:
                        split_idx = init_positions[1]
                        home = " ".join(tokens[:split_idx]).strip()
                        away = " ".join(tokens[split_idx:]).strip()
                    else:
                        if len(tokens) >= 2:
                            mid = len(tokens)//2
                            home = " ".join(tokens[:mid])
                            away = " ".join(tokens[mid:])
                        else:
                            home = players_part
                            away = ""
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
                    # Extract odds from lookahead (Jina markdown contains American odds like -303 +240 or decimal like 1.57 2.35)
                    # Search up to 15 lines ahead for price tokens, prefer a valid two-way pair
                    all_prices = []
                    for k in range(i+1, min(i+15, len(lines))):
                        line_k = lines[k]
                        if not line_k or line_k.startswith("[") or line_k == "FT":
                            continue
                        # Skip pure prob lines (e.g. "41 59") and predicted winner lines
                        if re.match(r"^\d{1,3}\s+\d{1,3}$", line_k):
                            continue
                        if re.match(r"^[12]\s+\d+\-\d+", line_k):
                            continue
                        prices = _forebet_prices_from_text(line_k)
                        if prices:
                            all_prices.extend(prices)
                        if len(all_prices) >= 2:
                            # Try to find a valid two-way pair among collected prices
                            # Prefer pair that forms valid implied probability sum 0.98-1.35
                            found = False
                            for a_idx in range(len(all_prices)):
                                for b_idx in range(a_idx+1, len(all_prices)):
                                    oh = all_prices[a_idx]
                                    oa = all_prices[b_idx]
                                    # Use same validation as warehouse
                                    try:
                                        from racketfactory.warehouse import valid_two_way_decimal_pair
                                        if valid_two_way_decimal_pair(oh, oa):
                                            odds_home, odds_away = oh, oa
                                            found = True
                                            break
                                    except Exception:
                                        # Fallback: simple range check
                                        if 1.01 <= oh <= 51 and 1.01 <= oa <= 51:
                                            # Check implied sum
                                            try:
                                                s = 1.0/oh + 1.0/oa
                                                if 0.98 <= s <= 1.35:
                                                    odds_home, odds_away = oh, oa
                                                    found = True
                                                    break
                                            except:
                                                pass
                                if found:
                                    break
                            if found:
                                break
                            # If no valid pair yet but we have at least 2, keep first two as fallback (will be validated later)
                            if len(all_prices) >= 2 and odds_home is None:
                                odds_home, odds_away = all_prices[0], all_prices[1]
                                # Don't break yet, keep searching for better valid pair
                    # If we collected prices but didn't find valid pair, use first two
                    if odds_home is None and len(all_prices) >= 2:
                        odds_home, odds_away = all_prices[0], all_prices[1]
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
                    if "Just a moment" in html or "challenge-error" in html:
                        logger.warning("Forebet Playwright still got CF challenge for %s", url)
                        return None
                    return html
                return None
        except Exception as e:
            logger.warning("Forebet Playwright error for %s: %s", url, e)
            return None

    def _fetch(self, url: str) -> Optional[str]:
        """Fetch with curl_cffi primary, Playwright fallback, robust UA rotation."""
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
                        if "Just a moment" in txt or "challenge-error" in txt or "Attention Required" in txt:
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
            if r.status_code == 200 and "Just a moment" not in r.text and len(r.text) > 2000:
                logger.info("Forebet fetched via cloudscraper for %s", url)
                return r.text
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Forebet cloudscraper failed for %s: %s", url, e)

        # 2.5 Jina AI Reader fallback — lightweight, no browser, bypasses runner IP block
        logger.info("Forebet trying Jina AI Reader for %s", url)
        jina = self._fetch_via_jina(url)
        if jina:
            # Jina returns markdown; return it so fetch_daily can parse via parse_jina_markdown
            # For _fetch we return markdown and let caller handle; but to keep HTML path working, return markdown if it looks valid
            if "Tennis predictions" in jina:
                logger.info("Forebet fetched via Jina for %s (%d bytes)", url, len(jina))
                return jina

        # 3. Playwright fallback — real browser can clear CF
        logger.info("Forebet falling back to Playwright for %s", url)
        html = self._fetch_via_playwright(url)
        if html:
            logger.info("Forebet fetched via Playwright for %s (%d bytes)", url, len(html))
            return html

        logger.error("Forebet failed to fetch %s via all methods", url)
        return None


    # ------------------------------------------------------------------
    # Tournament page fetch
    # ------------------------------------------------------------------
    def _fetch_tournament_page(self, tour_slug: str, tournament_slug: str) -> Optional[str]:
        url = f"{self.BASE_URL}/{tour_slug}/{tournament_slug}"
        return self._fetch(url)

    # ------------------------------------------------------------------
    # Daily overview page fetch
    # ------------------------------------------------------------------
    def _fetch_daily_page(self, day: str = "today") -> Optional[str]:
        """Fetch a daily predictions page.

        ``day`` is ``yesterday``/``today``/``tomorrow`` or an explicit
        ``YYYY-MM-DD`` calendar date (Forebet serves
        ``/tennis/predictions/YYYY-MM-DD``).
        """
        import re as _re
        if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day or "")):
            url = f"{self.BASE_URL}/predictions-{day}"
        elif day in ("yesterday", "today", "tomorrow"):
            url = f"{self.BASE_URL}/predictions-{day}"
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
        """
        tour_slug = forebet_tour_slug(tour)
        tourn_slug = forebet_tournament_slug(tournament)
        html = self._fetch_tournament_page(tour_slug, tourn_slug)
        if not html:
            return []
        preds = self.parse_page(html)
        for p in preds:
            p["tournament"] = tournament
        return preds

    # ------------------------------------------------------------------
    # Public API: daily overview predictions
    # ------------------------------------------------------------------
    def fetch_daily_predictions(self, day: str = "today") -> list[dict[str, Any]]:
        """
        Fetch all predictions for a given day across ALL tournaments.
        day: 'yesterday', 'today', or 'tomorrow'
        Returns list of raw prediction dicts.
        Tries HTML first, then Jina markdown fallback, then Playwright.
        Handles empty Jina (9310 bytes, 0 parsed) by falling back to Playwright.
        """
        html = self._fetch_daily_page(day)
        expected_day = _expected_iso_for_day(day)
        if not html:
            url = f"{self.BASE_URL}/predictions-{day}"
            jina_md = self._fetch_via_jina(url)
            if jina_md:
                try:
                    parsed = self.parse_jina_markdown(jina_md, expected_day)
                    if parsed:
                        return _apply_page_day(parsed, expected_day, day)
                    else:
                        logger.warning("Forebet %s Jina returned %d bytes but parsed 0, trying Playwright; head=%r",
                                       day, len(jina_md), jina_md[:300])
                        # Fallback to Playwright if Jina empty
                        pw_html = self._fetch_via_playwright(url)
                        if pw_html:
                            pw_parsed = self.parse_page(pw_html)
                            if pw_parsed:
                                logger.info(f"Forebet {day} recovered via Playwright with {len(pw_parsed)} rows after Jina empty")
                                return _apply_page_day(pw_parsed, expected_day, day)
                except Exception as e:
                    logger.warning(f"Jina markdown parse failed for {day}: {e}")
            # Try Playwright directly if Jina failed
            pw_html = self._fetch_via_playwright(f"{self.BASE_URL}/predictions-{day}")
            if pw_html:
                try:
                    pw_parsed = self.parse_page(pw_html)
                    if pw_parsed:
                        return _apply_page_day(pw_parsed, expected_day, day)
                except Exception as e:
                    logger.warning(f"Playwright parse failed for {day}: {e}")
            return []
        if "Markdown Content:" in html or "URL Source:" in html or "Tennis predictions for" in html and "[" in html and "/tennis/matches/" in html:
            try:
                parsed = self.parse_jina_markdown(html, expected_day)
                if parsed:
                    return _apply_page_day(parsed, expected_day, day)
                else:
                    # Jina from _fetch returned markdown but parsed 0 - try Playwright
                    logger.warning("Forebet %s Jina from _fetch %d bytes parsed 0, trying Playwright; head=%r",
                                   day, len(html), html[:300])
                    pw_html = self._fetch_via_playwright(f"{self.BASE_URL}/predictions-{day}")
                    if pw_html:
                        pw_parsed = self.parse_page(pw_html)
                        if pw_parsed:
                            return _apply_page_day(pw_parsed, expected_day, day)
            except Exception as e:
                logger.warning(f"Jina markdown parse failed for {day} (from _fetch): {e}")
        parsed = self.parse_page(html)
        if not parsed:
            url = f"{self.BASE_URL}/predictions-{day}"
            jina_md = self._fetch_via_jina(url)
            if jina_md:
                try:
                    jparsed = self.parse_jina_markdown(jina_md, expected_day)
                    if jparsed:
                        logger.info(f"Forebet {day} recovered via Jina markdown with {len(jparsed)} rows")
                        return _apply_page_day(jparsed, expected_day, day)
                    else:
                        logger.warning("Forebet %s Jina fallback %d bytes parsed 0, trying Playwright; head=%r",
                                       day, len(jina_md), jina_md[:300])
                        pw_html = self._fetch_via_playwright(url)
                        if pw_html:
                            pw_parsed = self.parse_page(pw_html)
                            if pw_parsed:
                                logger.info(f"Forebet {day} recovered via Playwright after Jina empty with {len(pw_parsed)} rows")
                                return _apply_page_day(pw_parsed, expected_day, day)
                except Exception as e:
                    logger.warning(f"Jina fallback parse failed for {day}: {e}")
            # Final Playwright fallback
            pw_html = self._fetch_via_playwright(url)
            if pw_html:
                try:
                    pw_parsed = self.parse_page(pw_html)
                    if pw_parsed:
                        logger.info(f"Forebet {day} recovered via Playwright final with {len(pw_parsed)} rows")
                        return _apply_page_day(pw_parsed, expected_day, day)
                except Exception as e:
                    logger.warning(f"Playwright final parse failed for {day}: {e}")
        return _apply_page_day(parsed, expected_day, day)

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