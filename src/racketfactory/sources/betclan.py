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
import random

BROWSER_PROFILES = ["chrome133a", "safari17_0", "firefox133"]

def safe_prob(p):
    try:
        return int(float(str(p).replace('%', '').strip()))
    except:
        return None

logger = logging.getLogger(__name__)

class BetClanPredictor:
    def __init__(self):
        self.base_url = "https://www.betclan.com"

    def fetch_daily(self) -> list[dict[str, Any]]:
        results = []
        # Fetch today and tomorrow
        for day_offset, endpoint in [(0, "todays-tennis-predictions"), (1, "tomorrows-tennis-predictions")]:
            target_date = (date.today() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            url = f"{self.base_url}/{endpoint}/"
            try:
                resp = requests.get(url, impersonate=random.choice(BROWSER_PROFILES), timeout=20)
                if resp.status_code != 200:
                    continue
            except Exception as e:
                logger.error(f"BetClan request failed: {e}")
                continue

            links = re.findall(r"href=[\'\"](https://www\.betclan\.com/tennis/predictionsdetails/[^\'\"]+)[\'\"]", resp.text)
            
            for match_url in set(links):
                try:
                    r = requests.get(match_url, impersonate=random.choice(BROWSER_PROFILES), timeout=20)
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
                        m = re.search(r"width:\s*([^%]+)%", vote_container.get('style'))
                        if m: prob1 = safe_prob(m.group(1))
                    if x_container and 'width' in x_container.get('style', ''):
                        m = re.search(r"width:\s*([^%]+)%", x_container.get('style'))
                        if m: prob2 = safe_prob(m.group(1))
                        
                    results.append({
                        "match_date": target_date,
                        "player_home": p1,
                        "player_away": p2,
                        "prob_home": prob1,
                        "prob_away": prob2,
                        "predicted_winner": "1" if winner_name.lower() == p1.lower() else "2",
                        "predicted_winner_name": winner_name,
                        "source": "BetClan"
                    })
                except Exception as e:
                    logger.warning(f"Error parsing BetClan match {match_url}: {e}")

        return results
