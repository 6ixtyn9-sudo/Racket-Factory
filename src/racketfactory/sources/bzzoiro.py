import os
import logging
from typing import Any, Optional
import requests
from datetime import date
from dotenv import load_dotenv

from racketfactory.sources.forebet import name_signature

from racketfactory.quota_guard import (check_and_spend, record_402, cooldown_status,
                                      record_cooldown, retry_after_seconds)

logger = logging.getLogger(__name__)

load_dotenv()
BZZOIRO_TOKEN = os.getenv("BZZOIRO_TOKEN")

class SourceFetchError(RuntimeError):
    """Provider failure is not a successful empty predictions board."""
    def __init__(self, kind: str, detail: str):
        self.kind = kind
        super().__init__(f"Bzzoiro {kind}: {detail}")


class BzzoiroPredictor:
    def __init__(self):
        if not BZZOIRO_TOKEN:
            logger.warning("Bzzoiro token not configured")

    def fetch_historical_predictions(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        from urllib.parse import urlsplit
        token = BZZOIRO_TOKEN
        if not token:
            raise SourceFetchError("auth", "token not configured; check deployment secret")
        failure = cooldown_status("bzzoiro", token)
        if failure:
            raise SourceFetchError(failure["kind"], "cooldown active; check provider account and permissions")
        url = "https://sports.bzzoiro.com/tennis/api/v2/predictions/"
        params = {"date_from": date_from, "date_to": date_to, "upcoming_only": "false"}
        headers = {"Authorization": f"Token {token}"}
        all_results = []
        visited = set()
        while url:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname != "sports.bzzoiro.com" or url in visited:
                raise SourceFetchError("parser", "invalid or repeated pagination URL")
            visited.add(url)
            if not check_and_spend("bzzoiro"):
                raise SourceFetchError("quota", "request not authorised by durable accounting")
            try:
                response = requests.get(url, headers=headers, params=params, timeout=20)
            except requests.RequestException:
                raise SourceFetchError("transport", "request failed") from None
            params = None
            code = response.status_code
            if code == 401:
                record_cooldown("bzzoiro", token, "auth", 24 * 3600)
                raise SourceFetchError("auth", "HTTP 401; check provider account/token configuration and endpoint permissions")
            if code == 402:
                record_402("bzzoiro")
                raise SourceFetchError("entitlement", "HTTP 402; check account and plan")
            if code == 429:
                record_cooldown("bzzoiro", token, "rate_limit", retry_after_seconds(getattr(response, "headers", {})))
                raise SourceFetchError("rate_limit", "HTTP 429; respecting Retry-After")
            if code != 200:
                raise SourceFetchError("http", f"HTTP {code}")
            try:
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                    raise ValueError("invalid result structure")
                for item in data["results"]:
                    match = item.get("match", {})
                    player1, player2 = match.get("player1", {}), match.get("player2", {})
                    match_date = match.get("match_date")
                    if match_date and "T" in match_date:
                        match_date = match_date.split("T")[0]
                    all_results.append({"match_date": match_date, "player_home": player1.get("name"),
                                        "player_away": player2.get("name"), "prob_home": item.get("prob_player1_wins"),
                                        "prob_away": item.get("prob_player2_wins"),
                                        "predicted_winner": str(item.get("predicted_winner")), "source": "Bzzoiro"})
                url = data.get("next")
                if url is not None and not isinstance(url, str):
                    raise ValueError("invalid pagination")
            except (ValueError, TypeError, AttributeError, KeyError):
                raise SourceFetchError("parser", "invalid response structure") from None
        logger.info("Bzzoiro successful response: %d predictions", len(all_results))
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
