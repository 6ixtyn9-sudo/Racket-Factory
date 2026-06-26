"""
Forebet Predictor Adapter
Extracts mathematical predictions and probabilities from Forebet.
"""
from __future__ import annotations
import logging
import re
from typing import Any, Optional
import pandas as pd
from curl_cffi import requests

logger = logging.getLogger(__name__)

class ForebetPredictor:
    """
    Handles the extraction of pre-match predictions from Forebet.
    """
    BASE_URL = "https://www.forebet.com/en/predictions/tennis"

    def __init__(self, impersonate: str = "chrome131"):
        self.impersonate = impersonate

    def fetch_prediction(self, tournament_slug: str, match_slug: str) -> Optional[dict[str, Any]]:
        """
        Fetch the prediction for a specific match.
        Returns a dict with predicted_winner and probability.
        """
        url = f"{self.BASE_URL}/{tournament_slug}/{match_slug}"
        
        try:
            resp = requests.get(url, impersonate=self.impersonate, timeout=15)
            if resp.status_code != 200:
                return None
            
            html = resp.text
            
            # Forebet typically stores the prediction in a specific div or span
            # This is a generalized regex to find the predicted winner from the HTML
            # Example: <span class="prediction">Player A</span>
            pred_match = re.search(r'class="prediction">([^<]+)</span>', html)
            prob_match = re.search(r'class="prob">(\d+)%', html)
            
            if not pred_match:
                return None
                
            return {
                "predicted_winner": pred_match.group(1).strip(),
                "probability": float(prob_match.group(1)) / 100 if prob_match else None,
                "source": "Forebet"
            }
        except Exception as e:
            logger.debug("Forebet fetch error for %s: %s", url, e)
            return None

    def map_prediction_to_player(self, pred_name: str, player_a: str, player_b: str) -> Optional[str]:
        """
        Maps the predictor's name to either 'player_a' or 'player_b'.
        """
        from racketfactory.entities import normalize_player
        
        p_norm = normalize_player(pred_name)
        a_norm = normalize_player(player_a)
        b_norm = normalize_player(player_b)
        
        if p_norm == a_norm: return "player_a"
        if p_norm == b_norm: return "player_b"
        
        # Fuzzy match if exact normalization fails
        if p_norm in a_norm or a_norm in p_norm: return "player_a"
        if p_norm in b_norm or b_norm in p_norm: return "player_b"
        
        return None
