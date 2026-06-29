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
        # American odds, e.g. +160 or -227.
        if re.fullmatch(r"[+-]?\d+", text):
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
    # Low-level fetch
    # ------------------------------------------------------------------
    def _fetch(self, url: str) -> Optional[str]:
        try:
            resp = self._session.get(url, timeout=20)
            if resp.status_code != 200:
                logger.warning("Forebet returned %d for %s", resp.status_code, url)
                return None
            if "Just a moment" in resp.text or "challenge-error" in resp.text:
                logger.warning("Forebet Cloudflare challenge for %s", url)
                return None
            return resp.text
        except Exception as e:
            logger.warning("Forebet fetch error for %s: %s", url, e)
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
        """Fetch predictions-yesterday, predictions-today, or predictions-tomorrow."""
        if day not in ("yesterday", "today", "tomorrow"):
            raise ValueError("day must be 'yesterday', 'today', or 'tomorrow'")
        url = f"{self.BASE_URL}/predictions-{day}"
        return self._fetch(url)

    # ------------------------------------------------------------------
    # Unified parser — works on both tournament pages and daily pages
    # ------------------------------------------------------------------
    def parse_page(self, html: str) -> list[dict[str, Any]]:
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
            date_span = anchor.find("span", class_="date_bah")
            match_date = None
            match_time = ""
            if date_span:
                date_text = date_span.get_text(strip=True)
                try:
                    dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")
                    match_date = dt.strftime("%Y-%m-%d")
                    match_time = dt.strftime("%H:%M")
                except ValueError:
                    match_date = None

            # --- Tournament -----------------------------------------------
            tournament_name = None
            if tournament_slug:
                tournament_name = tournament_slug.replace("-", " ").title()
            row_container = anchor.find_parent("div", class_="rcnt")
            if not row_container:
                row_container = anchor.find_parent("div")
            if row_container:
                prev_heading = row_container.find_previous("div", class_="heading")
                if prev_heading:
                    heading_text = prev_heading.get_text(" ", strip=True)
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
        """
        html = self._fetch_daily_page(day)
        if not html:
            return []
        return self.parse_page(html)

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