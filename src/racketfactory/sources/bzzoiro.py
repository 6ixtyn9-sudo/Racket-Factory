import os
import logging
from typing import Any, Optional
import requests
from dotenv import load_dotenv

from racketfactory.sources.forebet import name_signature

logger = logging.getLogger(__name__)

load_dotenv()
BZZOIRO_TOKEN = os.getenv("BZZOIRO_TOKEN")

class BzzoiroPredictor:
    """
    Handles extraction of predictions from Bzzoiro API.
    """
    def __init__(self):
        if not BZZOIRO_TOKEN:
            logger.warning("BZZOIRO_TOKEN not found in .env. Bzzoiro Predictor will fail.")

    def fetch_historical_predictions(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        """
        Fetch historical predictions between two dates from Bzzoiro API.
        """
        all_results = []
        url = "https://sports.bzzoiro.com/tennis/api/predictions/"
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "upcoming_only": "false"
        }
        
        headers = {
            "Authorization": f"Token {BZZOIRO_TOKEN}"
        }
        
        while url:
            logger.info(f"Fetching Bzzoiro predictions from {url}")
            try:
                # Use standard requests as instructed, bypass curl_cffi for this one
                response = requests.get(url, headers=headers, params=params, timeout=20)
                # clear params after first request so next URL can include its own query parameters
                params = None
                if response.status_code != 200:
                    logger.error(f"Bzzoiro API failed: HTTP {response.status_code}")
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

    def map_prediction_to_player(
        self, pred: dict[str, Any], player_a: str, player_b: str
    ) -> Optional[dict[str, Any]]:
        """
        Map a Bzzoiro prediction to warehouse player_a/player_b using name_signature.
        """
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

        return {
            "predicted_winner": winner,
            "prediction_prob": prob,
            "source": "Bzzoiro",
        }
