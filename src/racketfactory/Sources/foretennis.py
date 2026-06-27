"""
ForeTennis Predictor Adapter
Extracts mathematical predictions and probabilities from ForeTennis.
Complements Forebet with a different algorithmic model.
"""
from __future__ import annotations
import logging
import re
from typing import Any, Optional
from datetime import datetime
from curl_cffi import requests
from bs4 import BeautifulSoup
from racketfactory.entities import normalize_player, player_key
from racketfactory.sources.forebet import name_signature

logger = logging.getLogger(__name__)

BASE_URL = "https://www.foretennis.com"


def _fetch_page(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, impersonate="chrome133a", timeout=20)
        if resp.status_code != 200:
            logger.warning("ForeTennis returned %d for %s", resp.status_code, url)
            return None
        if "Just a moment" in resp.text or "challenge" in resp.text.lower():
            logger.warning("ForeTennis Cloudflare challenge for %s", url)
            return None
        return resp.text
    except Exception as e:
        logger.warning("ForeTennis fetch error for %s: %s", url, e)
        return None


def _parse_lastpredictions_page(html: str) -> list[dict[str, Any]]:
    """
    Parse the /lastpredictions page which shows finished matches with
    predictions, probabilities, and actual results.

    Returns list of dicts with:
        match_date, tournament, player_home, player_away,
        prob_home, prob_away, predicted_winner (1 or 2),
        predicted_sets, predicted_games, actual_result, prediction_correct
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", id=re.compile(r"\d+"))
    results = []

    for tr in rows:
        # --- Tournament ---
        tour_cell = tr.find("td", class_="centered")
        tournament = ""
        if tour_cell:
            tour_span = tour_cell.find("span", class_="font11")
            if tour_span:
                tournament = tour_span.get_text(strip=True)

        # --- Players and date ---
        match_cell = tr.find("td", class_="lefted pnames")
        if not match_cell:
            continue

        # Player names are in <span> elements inside the <a>, separated by <br>
        # Some pages have one <a> with two spans, others have two <a> tags
        player_links = match_cell.find_all("a")
        players = []
        for a in player_links:
            # Try to split by <br> tags first
            brs = a.find_all("br")
            if brs:
                # Split the contents by <br> position
                children = list(a.children)
                current = []
                for child in children:
                    if getattr(child, 'name', None) == 'br':
                        if current:
                            text = ''.join(str(c) for c in current)
                            clean = re.sub(r"<[^>]+>", "", text)
                            clean = re.sub(r"\([A-Z]{3}\)", "", clean).strip()
                            if clean and len(clean) > 2:
                                players.append(clean)
                        current = []
                    else:
                        current.append(child)
                if current:
                    text = ''.join(str(c) for c in current)
                    clean = re.sub(r"<[^>]+>", "", text)
                    clean = re.sub(r"\([A-Z]{3}\)", "", clean).strip()
                    if clean and len(clean) > 2:
                        players.append(clean)
            else:
                # Single player per <a> tag
                text = a.get_text(strip=True)
                clean = re.sub(r"\([A-Z]{3}\)", "", text).strip()
                if clean and len(clean) > 2 and clean not in players:
                    players.append(clean)

        # Deduplicate and ensure we have at least 2 distinct players
        seen = set()
        unique_players = []
        for p in players:
            if p not in seen:
                seen.add(p)
                unique_players.append(p)
        players = unique_players

        if len(players) < 2:
            continue

        player_home, player_away = players[0], players[1]

        # Date
        date_div = match_cell.find("div", class_="date_match")
        match_date = None
        if date_div:
            date_text = date_div.get_text(strip=True)
            try:
                dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")
                match_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                match_date = None

        # --- Probabilities ---
        prob_cells = tr.find_all("td", class_=re.compile(r"centered"))
        probs = []
        for td in prob_cells:
            text = td.get_text(strip=True)
            if text.isdigit():
                probs.append(int(text))

        prob_home = probs[0] if len(probs) >= 1 else None
        prob_away = probs[1] if len(probs) >= 2 else None

        # --- Predicted winner (Tip) ---
        tip_span = tr.find("span", class_=re.compile(r"predict_"))
        predicted_winner = tip_span.get_text(strip=True) if tip_span else None
        prediction_correct = None
        if tip_span:
            cls = tip_span.get("class", [])
            if any("predict_y" in str(c) for c in cls):
                prediction_correct = True
            elif any("predict_n" in str(c) for c in cls):
                prediction_correct = False

        # --- Predicted set scores ---
        set_divs = match_cell.find_all("div")
        set_preds = [d.get_text(strip=True) for d in set_divs
                     if d.get_text(strip=True).isdigit()]
        predicted_sets = "-".join(set_preds[:2]) if len(set_preds) >= 2 else None

        # --- Actual result (FT) ---
        # The last centered td is the FT column
        actual_result = ""
        if prob_cells:
            ft_text = prob_cells[-1].get_text(strip=True)
            if ft_text.isdigit():
                actual_result = ft_text

        results.append({
            "match_date": match_date,
            "tournament": tournament,
            "player_home": player_home,
            "player_away": player_away,
            "prob_home": prob_home,
            "prob_away": prob_away,
            "predicted_winner": predicted_winner,  # "1" or "2"
            "predicted_sets": predicted_sets,
            "actual_result": actual_result,
            "prediction_correct": prediction_correct,
            "source": "ForeTennis",
        })

    logger.info("Parsed %d predictions from ForeTennis lastpredictions", len(results))
    return results


class ForeTennisPredictor:
    """
    Handles extraction of predictions from ForeTennis.
    """

    def __init__(self):
        pass

    def fetch_lastpredictions(self) -> list[dict[str, Any]]:
        """Fetch the lastpredictions page (finished matches with results)."""
        url = f"{BASE_URL}/lastpredictions"
        html = _fetch_page(url)
        if not html:
            return []
        return _parse_lastpredictions_page(html)

    def map_prediction_to_player(
        self, pred: dict[str, Any], player_a: str, player_b: str
    ) -> Optional[dict[str, Any]]:
        """
        Map a ForeTennis prediction (home/away) to warehouse player_a/player_b.
        Uses the same name_signature as Forebet for consistency.
        """
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
            logger.debug(
                "Cannot map %s/%s to %s/%s",
                pred["player_home"], pred["player_away"], player_a, player_b,
            )
            return None

        predicted_winner = pred.get("predicted_winner")
        prob = None
        if predicted_winner == "1":
            winner = "player_a" if home_is_a else "player_b"
            prob = pred["prob_home"] / 100 if pred["prob_home"] is not None else None
        elif predicted_winner == "2":
            winner = "player_b" if home_is_a else "player_a"
            prob = pred["prob_away"] / 100 if pred["prob_away"] is not None else None
        else:
            return None

        return {
            "predicted_winner": winner,
            "prediction_prob": prob,
            "source": "ForeTennis",
        }
