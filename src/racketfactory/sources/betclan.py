"""
BetClan Predictor Adapter
Forward-capture of daily AI predictions.
"""
from __future__ import annotations
import logging
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import Any, Optional
from datetime import date, timedelta
from curl_cffi import requests

from racketfactory.entities import normalize_player

logger = logging.getLogger(__name__)

class BetClanPredictor:
    def __init__(self):
        self.base_url = "https://www.betclan.com"

    def _clean_text(self, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @staticmethod
    def _extract_odds_from_soup(soup: BeautifulSoup) -> tuple[Optional[float], Optional[float]]:
        """
        Best-effort decimal odds extraction from any BetClan page. Returns
        (odds_home, odds_away). Either may be None if not found.

        Strategies, in order:
          1. Iterate elements with class containing odds/odd/price/etc.
          2. Walk every <table> row and look for two decimals per row.
          3. Fall back to a global regex over the entire page text.
        The function stops at the first strategy that yields two numeric odds
        because once we have a pair, deeper guesses are more likely wrong than
        right. Single-odds matches are accumulated separately.
        """
        odds_home: Optional[float] = None
        odds_away: Optional[float] = None

        # Strategy 1: class-driven scanning — broader class set than the
        # historical inline scrape, including 'price', 'coef', 'decimal',
        # 'quote' (BetClan variant) and the Portuguese/Spanish 'cuota'.
        # Use a separator on get_text so adjacent spans/divs don't fuse their
        # numbers into one malformed token like "1.852.10".
        for tag in soup.find_all(["div", "span", "td", "button", "a", "b", "i", "strong", "em"]):
            text = tag.get_text(" ", strip=True)
            cls_str = " ".join(str(c) for c in (tag.get("class") or [])).lower()
            id_str = str(tag.get("id") or "").lower()
            haystack = cls_str + " " + id_str
            if not any(w in haystack for w in (
                "odds", "odd", "odd-val", "bet-odd", "box-odds", "price",
                "val", "bet", "book", "market", "quote", "coef", "decimal",
                "payout", "1x2", "moneyline",
            )):
                continue
            matches = re.findall(r"\b([1-9]\.\d{1,3})\b", text)
            if len(matches) >= 2:
                try:
                    return float(matches[0]), float(matches[1])
                except (TypeError, ValueError):
                    continue
            if len(matches) == 1:
                try:
                    v = float(matches[0])
                    if odds_home is None:
                        odds_home = v
                    elif odds_away is None and abs(v - odds_home) > 0.02:
                        odds_away = v
                except (TypeError, ValueError):
                    continue

        # Strategy 2: walk every <table> row for two-decimal patterns.
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                row_text = tr.get_text(" ", strip=True)
                row_matches = re.findall(r"\b([1-9]\.\d{1,3})\b", row_text)
                if len(row_matches) >= 2:
                    try:
                        return float(row_matches[0]), float(row_matches[1])
                    except (TypeError, ValueError):
                        continue

        # Strategy 3: global regex fallback. Only act if we found a clean pair.
        if odds_home is None or odds_away is None:
            full_text = soup.get_text(" ", strip=True)
            full_matches = re.findall(r"\b([1-9]\.\d{1,3})\b", full_text)
            # Heuristic: the first two decimal odds near the match header are
            # usually the home/away odds; subsequent ones are other markets.
            if len(full_matches) >= 2:
                try:
                    if odds_home is None:
                        odds_home = float(full_matches[0])
                    if odds_away is None:
                        odds_away = float(full_matches[1])
                except (TypeError, ValueError):
                    pass

        return odds_home, odds_away

    def _find_odds_subpage(self, prediction_soup: BeautifulSoup, prediction_url: str) -> list[str]:
        """
        Discover candidate per-match odds subpages from a BetClan prediction
        page. Returns a list of URLs to try, ordered by likelihood.
        """
        candidates: list[str] = []
        seen: set[str] = set()

        def _add(url: str) -> None:
            if not url:
                return
            if url in seen:
                return
            seen.add(url)
            candidates.append(url)

        # 1. Explicit odds links inside the prediction page DOM.
        odds_link_keywords = ("odds", "1x2", "bookmaker", "market", "prices", "picks")
        for a in prediction_soup.find_all("a", href=True):
            href = str(a.get("href", "") or "").strip()
            if not href or "/tennis/" not in href:
                continue
            lowered = href.lower()
            if any(k in lowered for k in odds_link_keywords):
                _add(urljoin(prediction_url, href))

        # 2. Parallel URL guess: /predictionsdetails/X/ -> /oddsdetails/X/ or /odds/X/.
        parsed = urlparse(prediction_url)
        path = parsed.path or ""
        # Extract the trailing id slug so we can rebuild paths.
        slug = ""
        m = re.search(r"predictionsdetails/([^/?#]+)", path)
        if m:
            slug = m.group(1)
        if slug:
            for prefix in ("/tennis/oddsdetails/", "/tennis/odds/", "/tennis/match-odds/", "/tennis/bookmaker-odds/"):
                _add(f"{parsed.scheme}://{parsed.netloc}{prefix}{slug}")
        return candidates

    def _fetch_odds_subpage(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch one candidate odds subpage. Returns None on any error or 404."""
        try:
            resp = requests.get(url, impersonate="chrome133a", timeout=15)
        except Exception as e:
            logger.debug(f"BetClan odds subpage fetch error for {url}: {e}")
            return None
        if resp.status_code != 200:
            return None
        try:
            return BeautifulSoup(resp.text, "html.parser")
        except Exception:
            return None

    def _extract_context(self, soup: BeautifulSoup) -> tuple[str, str, str]:
        """Extract focused BetClan context with minimal bleed from nearby widgets."""
        primary_selectors = [
            ("span", "titleh2page"),
            ("div", "portlet-title"),
            ("h1", None),
            ("h2", None),
        ]
        fallback_selectors = [
            ("div", "caption"),
            ("div", "breadcrumb"),
            ("ul", "breadcrumb"),
        ]

        candidates: list[str] = []
        for tag_name, class_name in primary_selectors + fallback_selectors:
            found = soup.find(tag_name, class_=class_name) if class_name else soup.find(tag_name)
            if found:
                text = self._clean_text(found.get_text(" ", strip=True))
                if text:
                    candidates.append(text)

        title_text = self._clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title_text:
            candidates.append(title_text)

        deduped = list(dict.fromkeys(candidates))
        tournament = deduped[0] if deduped else ""
        event_text = tournament
        category_text = " | ".join(
            text
            for text in deduped
            if re.search(
                r"\b(?:ATP|WTA|ITF|Challenger|Men(?:'s)?|Women(?:'s)?|Boys|Girls|Doubles|Singles|WD|MD)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        return tournament, event_text, category_text

    def fetch_daily(self) -> list[dict[str, Any]]:
        results = []
        # Fetch today and tomorrow
        for day_offset, endpoint in [(-1, "yesterdays-tennis-predictions"), (0, "todays-tennis-predictions"), (1, "tomorrows-tennis-predictions")]:
            target_date = (date.today() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            url = f"{self.base_url}/{endpoint}/"
            try:
                resp = requests.get(url, impersonate="chrome133a", timeout=20)
                if resp.status_code != 200:
                    continue
            except Exception as e:
                logger.error(f"BetClan request failed: {e}")
                continue

            links = re.findall(r'https://www\.betclan\.com/tennis/predictionsdetails/[^"\']+', resp.text)

            for match_url in set(links):
                try:
                    r = requests.get(match_url, impersonate="chrome133a", timeout=20)
                    if r.status_code != 200:
                        continue
                    s = BeautifulSoup(r.text, 'html.parser')

                    home_div = s.find('div', class_='teamtophome')
                    away_div = s.find('div', class_='teamtopaway')
                    if not home_div or not away_div:
                        continue

                    p1 = home_div.text.strip()
                    p2 = away_div.text.strip()

                    winner_tag = s.find(lambda tag: tag.name == "h4" and "winner" in tag.text.lower())
                    if not winner_tag:
                        continue
                    winner_name = winner_tag.find_next_sibling("h5").text.strip()

                    vote_container = s.find('div', class_='cell__section vote__team js-vote-stats-bar')
                    x_container = s.find('div', class_='cell__section vote__x js-vote-stats-bar')

                    prob1, prob2 = None, None
                    if vote_container and 'width' in vote_container.get('style', ''):
                        m = re.search(r"width:\s*(\d+)%", vote_container.get('style'))
                        if m:
                            prob1 = int(m.group(1))
                    if x_container and 'width' in x_container.get('style', ''):
                        m = re.search(r"width:\s*(\d+)%", x_container.get('style'))
                        if m:
                            prob2 = int(m.group(1))

                    # Robustly determine predicted winner using probabilities or clean name matching
                    if prob1 is not None and prob2 is not None:
                        pred_win = "1" if prob1 >= prob2 else "2"
                    else:
                        clean_w = re.sub(r"[^a-z]", "", winner_name.lower())
                        clean_p1 = re.sub(r"[^a-z]", "", p1.lower())
                        pred_win = "1" if clean_p1 in clean_w or clean_w in clean_p1 else "2"

                    # Robustly extract bookmaker odds from the prediction page itself.
                    # This is the historical behaviour; for many matches BetClan hides
                    # the odds behind a separate /odds/ subpage, handled below.
                    odds_home, odds_away = self._extract_odds_from_soup(s)

                    # REDTEAM coverage fix: if the prediction page didn't expose
                    # decimal odds, try to follow from the prediction page to a
                    # per-match odds subpage. Best-effort, capped at two attempts
                    # so we don't double the latency budget for every match.
                    if odds_home is None or odds_away is None:
                        for sub_url in self._find_odds_subpage(s, match_url)[:2]:
                            sub_soup = self._fetch_odds_subpage(sub_url)
                            if sub_soup is None:
                                continue
                            sub_home, sub_away = self._extract_odds_from_soup(sub_soup)
                            if sub_home is not None and sub_away is not None:
                                odds_home, odds_away = sub_home, sub_away
                                break

                    page_text = s.get_text(" ", strip=True)
                    tournament, event_text, category_text = self._extract_context(s)

                    surface = ""
                    surface_m = re.search(r'\b(Grass|Clay|Hard)\b', page_text, re.IGNORECASE)
                    if surface_m:
                        surface = surface_m.group(1).strip()

                    m_date = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", r.text)
                    match_time_str = m_date.group(1).replace("T", " ") if m_date else target_date + " 00:00"
                    match_date = match_time_str.split()[0]
                    match_time = match_time_str.split()[1] if " " in match_time_str else ""

                    if not any(
                        str(existing.get("match_date", "")) == match_date
                        and str(existing.get("player_home", "")).strip().lower() == p1.strip().lower()
                        and str(existing.get("player_away", "")).strip().lower() == p2.strip().lower()
                        for existing in results
                    ):
                        results.append({
                            "match_date": match_date,
                            "match_time": match_time,
                            "player_home": p1,
                            "player_away": p2,
                            "prob_home": prob1,
                            "prob_away": prob2,
                            "odds_home": odds_home,
                            "odds_away": odds_away,
                            "predicted_winner": pred_win,
                            "predicted_winner_name": winner_name,
                            "tournament": tournament,
                            "surface": surface,
                            "event_text": event_text or tournament,
                            "category": category_text,
                            "source": "BetClan",
                            "source_url": match_url,
                        })
                except Exception as e:
                    logger.warning(f"Error parsing BetClan match {match_url}: {e}")

        return results