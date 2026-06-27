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
                    
                    winner = p1 if prob1 >= prob2 else p2
                    results.append({
                        "match_date": date.today().strftime("%Y-%m-%d"),
                        "player_home": p1,
                        "player_away": p2,
                        "prob_home": prob1,
                        "prob_away": prob2,
                        "predicted_winner": "1" if prob1 >= prob2 else "2",
                        "predicted_winner_name": winner,
                        "source": "PredixSport"
                    })
            except Exception as e:
                logger.warning(f"Error parsing match {match_url}: {e}")

        return results
