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
from urllib.parse import urljoin
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
        updated = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", soup.get_text(" ", strip=True))
        published_date = updated.group(1) if updated else None
        links = [a['href'] for a in soup.find_all('a', href=True) if '/tennis/' in a['href'] and '.html' in a['href']]
        
        results = []
        for link in set(links):
            match_url = urljoin(self.base_url, link)
            try:
                r = requests.get(match_url, impersonate="chrome133a", timeout=20)
                if r.status_code != 200:
                    continue
                s = BeautifulSoup(r.text, 'html.parser')
                
                players = [p.text.strip() for p in s.find_all('h2', class_='player-name')]
                probs = [p.text.strip().replace('%', '') for p in s.find_all('div', class_='win-probability')]
                
                if len(players) == 2 and len(probs) == 2 and all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in probs):
                    p1, p2 = players[0], players[1]
                    prob1, prob2 = float(probs[0]), float(probs[1])
                    if not (0 <= prob1 <= 100 and 0 <= prob2 <= 100):
                        continue

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

                    # Probabilities, predicted games and aces are not market odds.
                    # Only explicit price elements may supply decimal quotes.
                    price_tags = s.select(".odds, .odd, .price")
                    prices = [float(t.get_text(strip=True)) for t in price_tags
                              if re.fullmatch(r"\d+\.\d+", t.get_text(strip=True))]
                    odds_home, odds_away = prices if len(prices) == 2 else (None, None)

                    winner = p1 if prob1 >= prob2 else p2
                    if not any(r["player_home"] == p1 for r in results):
                        results.append({
                        # PredixSport tennis dates are not reliable actual play dates.
                        # The public/API tennis date behaves like a generated/tournament
                        # slate date, not a confirmed scheduled match day.  Keep this
                        # generated date for compatibility, but mark it LOW confidence so
                        # downstream pick hygiene does not treat PredixSport-only rows as
                        # actionable same-day fixtures.
                        "match_date": published_date or "",
                        "predix_generated_date": published_date or "",
                        "date_confidence": "LOW",
                        "scheduled_date_source": "PredixSportGeneratedDate",
                        "date_warning": "PredixSport tennis date is not confirmed actual play date",
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
                        "source": "PredixSport",
                        "source_url": match_url
                    })
            except Exception as e:
                logger.warning(f"Error parsing match {match_url}: {e}")

        return results