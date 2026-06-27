#!/usr/bin/env python3
"""
Fetch today's predictions and dispatch them to WhatsApp.
"""
import os
import sys
from pathlib import Path
from datetime import date
from dotenv import load_dotenv
import logging

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.forebet import ForebetPredictor
from racketfactory.whatsapp import format_whatsapp_message, send_callmebot_whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
logger = logging.getLogger("notify_whatsapp")

def main():
    load_dotenv()
    apikey = os.getenv("CALLMEBOT_APIKEY")
    phone = os.getenv("CALLMEBOT_PHONE")
    
    if not apikey or not phone:
        logger.error("CALLMEBOT_APIKEY or CALLMEBOT_PHONE missing from .env")
        return
        
    today_str = date.today().strftime("%Y-%m-%d")
    
    logger.info("Fetching PredixSport predictions...")
    px_preds = [p for p in PredixSportPredictor().fetch_daily() if p["match_date"] == today_str]
    
    logger.info("Fetching BetClan predictions...")
    # Deduplicate BetClan matches on the fly
    bc_preds = []
    seen = set()
    for p in BetClanPredictor().fetch_daily():
        if p["match_date"] == today_str and (p["player_home"], p["player_away"]) not in seen:
            seen.add((p["player_home"], p["player_away"]))
            bc_preds.append(p)

    logger.info("Fetching Forebet predictions for today...")
    try:
        fb_preds = ForebetPredictor().fetch_daily_predictions(day="today")
    except Exception as e:
        logger.warning(f"Forebet fetch failed: {e}")
        fb_preds = []
    
    message = format_whatsapp_message(today_str, px_preds, bc_preds, fb_preds)
    
    logger.info("Dispatching WhatsApp message via CallMeBot...")
    resp = send_callmebot_whatsapp(apikey, phone, message)
    
    if resp and "Error" not in resp:
        logger.info("✅ CallMeBot Dispatch Success!")
    else:
        logger.error(f"❌ CallMeBot Dispatch Failed: {resp}")

if __name__ == "__main__":
    main()