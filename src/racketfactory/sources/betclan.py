"""
BetClan Predictor Adapter
Forward-capture of daily AI predictions.
"""
from __future__ import annotations
import logging
import re
from bs4 import BeautifulSoup
from typing import Any, Optional
from datetime import date, timedelta
from curl_cffi import requests

from racketfactory.entities import normalize_player

logger = logging.getLogger(__name__)

class BetClanPredictor:
    def __init__(self):
        self.base_url = "https://www.betclan.com"

    def _extract_context(self, soup: BeautifulSoup) -> tuple[str, str, str]:
        """Best-effort extraction of BetClan page context using targeted selectors.

        Keep this lightweight: query only likely tournament/category nodes and
        avoid scanning the entire DOM.
        """
        candidates: list[str] = []
        selectors = [
            ("span", "titleh2page"),
            ("div", "caption"),
            ("div", "portlet-title"),
            ("div", "breadcrumb"),
            ("ul", "breadcrumb"),
        ]
        for tag_name, class_name in selectors:
            for tag in soup.find_all(tag_name, class_=class_name):
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text:
                    candidates.append(text)

        title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
        if title_text:
            candidates.append(title_text)

        deduped = list(dict.fromkeys(candidates))
        tournament = deduped[0] if deduped else ""
        event_text = " | ".join(deduped)
        category_text = " ".join(text for text in deduped if re.search(r"\b(?:ATP|WTA|ITF|Challenger|Men(?:'s)?|Women(?:'s)?|Boys|Girls|Doubles|Singles)\b", text, flags=re.IGNORECASE))
        return tournament, event_text, category_text

    def fetch_daily(self) -> list[dict[str, Any]]:
        results = []
        # Fetch today and tomorrow
        for day_offset, endpoint in [(0, "todays-tennis-predictions"), (1, "tomorrows-tennis-predictions")]:
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
                        if m: prob1 = int(m.group(1))
                    if x_container and 'width' in x_container.get('style', ''):
                        m = re.search(r"width:\s*(\d+)%", x_container.get('style'))
                        if m: prob2 = int(m.group(1))
                        
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
                        "predicted_winner": "1" if winner_name.lower() == p1.lower() else "2",
                        "predicted_winner_name": winner_name,
                        "tournament": tournament,
                        "surface": surface,
                        "event_text": event_text or tournament,
                        "category": category_text,
                        "source": "BetClan"
                    })
                except Exception as e:
                    logger.warning(f"Error parsing BetClan match {match_url}: {e}")

        return results
