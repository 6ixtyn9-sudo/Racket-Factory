#!/usr/bin/env python3
"""
Fetch today's and tomorrow's predictions for immediate review.
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor

def main():
    print("="*80)
    print("🎾 RACKET FACTORY: UPCOMING PREDICTIONS")
    print("="*80)
    
    print("\n[1] Fetching PredixSport AI...")
    px = PredixSportPredictor()
    px_preds = px.fetch_daily()
    
    if px_preds:
        df_px = pd.DataFrame(px_preds)
        print(df_px[["match_date", "player_home", "player_away", "prob_home", "prob_away", "predicted_winner_name"]].to_string(index=False))
    else:
        print("No matches found on PredixSport.")
        
    print("\n[2] Fetching BetClan AI...")
    bc = BetClanPredictor()
    bc_preds = bc.fetch_daily()
    
    if bc_preds:
        df_bc = pd.DataFrame(bc_preds)
        print(df_bc[["match_date", "player_home", "player_away", "prob_home", "prob_away", "predicted_winner_name"]].to_string(index=False))
    else:
        print("No matches found on BetClan.")
        
    print("\n="*80)
    print("Good luck, and have a wonderful time at church!")
    print("="*80)

if __name__ == "__main__":
    main()
