"""
PredixSport Predictor Adapter
Forward-capture of daily AI predictions.
"""
from __future__ import annotations
import logging
import re
from bs4 import BeautifulSoup
from typing import Any, Optional
from datetime import date
from curl_cffi import requests

from racketfactory.entities import normalize_player

logger = logging.getLogger(__name__)

class PredixSportPredictor:
    def __init__(self):
        self.base_url = "https://www.predixsport.com"

    def fetch_daily(self) -> list[dict[str, Any]]:
        url = f"{self.base_url}/tennis_predictions"
        try:
            resp = requests.get(url, impersonate="chrome133a", timeout=20)
            if resp.status_code != 200:
                logger.error("Failed to fetch PredixSport index")
                return []
        except Exception as e:
            logger.error(f"PredixSport request failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        links = [a['href'] for a in soup.find_all('a', href=True) if '/tennis/' in a['href'] and '.html' in a['href']]
        
        results = []
        for link in set(links):
            match_url = self.base_url + link
            try:
                r = requests.get(match_url, impersonate="chrome133a", timeout=20)
                if r.status_code != 200:
                    continue
                s = BeautifulSoup(r.text, 'html.parser')
                
                players = [p.text.strip() for p in s.find_all('h2', class_='player-name')]
                probs = [p.text.strip().replace('%', '') for p in s.find_all('div', class_='win-probability')]
                
                if len(players) == 2 and len(probs) == 2 and probs[0].isdigit() and probs[1].isdigit():
                    p1, p2 = players[0], players[1]
                    prob1, prob2 = int(probs[0]), int(probs[1])

                    main = s.find('div', class_='main-content') or s.find('div', class_='container')
                    main_text = " ".join(main.get_text(" ", strip=True).split()) if main else ""
                    title_text = s.title.get_text(" ", strip=True) if s.title else ""

                    tournament = ""
                    country = ""
                    surface = ""
                    series = ""
                    m = re.search(
                        r"Today's Tennis Game Predictions\s+(.+?)\s+(Spain|Great Britain|United Kingdom|England|Germany|France|Italy|USA|United States|Australia)\s+(Grass|Clay|Hard)\s+(Atp\s*\d+|Wta\s*\d+|Challenger|Grand Slam)\s+([A-Za-z]+)",
                        main_text,
                        re.IGNORECASE,
                    )
                    if m:
                        tournament = m.group(1).strip()
                        country = m.group(2).strip()
                        surface = m.group(3).strip()
                        series = m.group(4).strip()
                    else:
                        m2 = re.search(r"AI predictions for .*? at ([A-Za-z][A-Za-z\s\-']+?)\.", title_text, re.IGNORECASE)
                        if m2:
                            tournament = m2.group(1).strip()

                    # Try to extract bookmaker odds from PredixSport page
                    odds_home, odds_away = None, None
                    for tag in s.find_all(["div", "span"]):
                        text = tag.get_text(strip=True)
                        if any(w in str(tag.get("class", [])).lower() for w in ("odds", "odd", "bet", "price")):
                            matches = re.findall(r"\b([1-9]\.\d{2})\b", text)
                            if len(matches) >= 2:
                                odds_home, odds_away = float(matches[0]), float(matches[1])
                                break
                            elif len(matches) == 1:
                                if odds_home is None: odds_home = float(matches[0])
                                elif odds_away is None: odds_away = float(matches[0])

                    winner = p1 if prob1 >= prob2 else p2
                    if not any(r["player_home"] == p1 for r in results):
                        results.append({
                        "match_date": match_url.split("_")[-2][:4] + date.today().strftime("-%m-%d"),
                        "match_time": "",
                        "player_home": p1,
                        "player_away": p2,
                        "prob_home": prob1,
                        "prob_away": prob2,
                        "odds_home": odds_home,
                        "odds_away": odds_away,
                        "predicted_winner": "1" if prob1 >= prob2 else "2",
                        "predicted_winner_name": winner,
                        "tournament": tournament,
                        "country": country,
                        "surface": surface,
                        "event_level": series,
                        "event_text": main_text[:500],
                        "source": "PredixSport"
                    })
            except Exception as e:
                logger.warning(f"Error parsing match {match_url}: {e}")

        return results