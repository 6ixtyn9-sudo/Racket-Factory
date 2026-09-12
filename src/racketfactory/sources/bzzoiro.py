import os
import logging
from typing import Any, Optional
import requests
from datetime import date
from dotenv import load_dotenv

from racketfactory.sources.forebet import name_signature

logger = logging.getLogger(__name__)

load_dotenv()
BZZOIRO_TOKEN = os.getenv("BZZOIRO_TOKEN")

class BzzoiroPredictor:
    def __init__(self):
        if not BZZOIRO_TOKEN:
            logger.warning("BZZOIRO_TOKEN not found in .env. Bzzoiro Predictor will fail.")

    def fetch_historical_predictions(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        all_results = []
        url = "https://sports.bzzoiro.com/tennis/api/v2/predictions/"
        # Fallback to new domain if v2 fails: https://tennis.bzzoiro.com/api/predictions/
        fallback_urls = [
            "https://tennis.bzzoiro.com/api/predictions/",
            "https://sports.bzzoiro.com/api/v2/predictions/",
        ]
        params = {"date_from": date_from, "date_to": date_to, "upcoming_only": "false"}
        headers = {"Authorization": f"Token {BZZOIRO_TOKEN}"}
        tried_fallback = False
        while url:
            logger.info(f"Fetching Bzzoiro predictions from {url}")
            try:
                response = requests.get(url, headers=headers, params=params, timeout=20)
                params = None
                if response.status_code == 404 and not tried_fallback:
                    # Try fallback endpoints
                    logger.warning(f"Bzzoiro 404 on {url}, trying fallback endpoints")
                    for fb_url in fallback_urls:
                        try:
                            logger.info(f"Trying Bzzoiro fallback {fb_url}")
                            fb_resp = requests.get(fb_url, headers=headers, params={"date_from": params.get("date_from") if isinstance(params, dict) else None, "date_to": params.get("date_to") if isinstance(params, dict) else None, "upcoming_only": "false"} if params else {"upcoming_only": "false"}, timeout=20)
                            if fb_resp.status_code == 200:
                                logger.info(f"Bzzoiro fallback succeeded: {fb_url}")
                                response = fb_resp
                                url = fb_url
                                break
                        except Exception as fe:
                            logger.warning(f"Bzzoiro fallback {fb_url} failed: {fe}")
                            continue
                    tried_fallback = True
                if response.status_code != 200:
                    logger.error(f"Bzzoiro API failed: HTTP {response.status_code}")
                    if response.status_code == 404:
                        # Try next fallback if not yet
                        if not tried_fallback:
                            for fb_url in fallback_urls:
                                if fb_url != url:
                                    url = fb_url
                                    break
                            continue
                    break
                data = response.json()
                results = data.get("results", [])
                for item in results:
                    match = item.get("match", {})
                    player1 = match.get("player1", {})
                    player2 = match.get("player2", {})
                    match_date = match.get("match_date")
                    if match_date and "T" in match_date:
                        match_date = match_date.split("T")[0]
                    all_results.append({
                        "match_date": match_date,
                        "player_home": player1.get("name"),
                        "player_away": player2.get("name"),
                        "prob_home": item.get("prob_player1_wins"),
                        "prob_away": item.get("prob_player2_wins"),
                        "predicted_winner": str(item.get("predicted_winner")),
                        "source": "Bzzoiro"
                    })
                url = data.get("next")
            except Exception as e:
                logger.error(f"Error fetching from Bzzoiro: {e}")
                break
        logger.info(f"Fetched {len(all_results)} predictions from Bzzoiro")
        return all_results

    def fetch_daily(self, include_tomorrow: bool = False) -> list[dict[str, Any]]:
        from datetime import timedelta
        today = date.today().isoformat()
        if include_tomorrow:
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            return self.fetch_historical_predictions(date_from=today, date_to=tomorrow)
        return self.fetch_historical_predictions(date_from=today, date_to=today)

    def fetch_tomorrow(self) -> list[dict[str, Any]]:
        from datetime import timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return self.fetch_historical_predictions(date_from=tomorrow, date_to=tomorrow)

    def map_prediction_to_player(self, pred: dict[str, Any], player_a: str, player_b: str) -> Optional[dict[str, Any]]:
        if not pred.get("player_home") or not pred.get("player_away"):
            return None
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
            return None
        predicted_winner = pred.get("predicted_winner")
        prob = None
        if predicted_winner == "1":
            winner = "player_a" if home_is_a else "player_b"
            prob = pred.get("prob_home")
        elif predicted_winner == "2":
            winner = "player_b" if home_is_a else "player_a"
            prob = pred.get("prob_away")
        else:
            return None
        return {"predicted_winner": winner, "prediction_prob": prob, "source": "Bzzoiro"}
