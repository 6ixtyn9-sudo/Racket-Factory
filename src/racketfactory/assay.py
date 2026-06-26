"""
Racket Factory Assay Engine
Statistical verification of betting edges using Wilson Score Intervals.
"""
import math
import pandas as pd
import numpy as np
from typing import NamedTuple, Optional

class AssayResult(NamedTuple):
    win_rate: float
    roi: float
    n: int
    wilson_lb: float
    wilson_ub: float
    grade: str
    verdict: str

def wilson_score_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """Calculate the Wilson score interval for a binomial proportion."""
    if trials == 0:
        return 0.0, 0.0
    z = 1.96 
    p_hat = successes / trials
    denom = 1 + z**2 / trials
    center = p_hat + z**2 / (2 * trials)
    spread = z * math.sqrt((p_hat * (1 - p_hat) / trials) + (z**2 / (4 * trials**2)))
    return (center - spread) / denom, (center + spread) / denom

def calculate_grade(lb: float, roi: float, n: int, break_even: float = 0.5238) -> str:
    """
    Grade an edge based on the Wilson Lower Bound and ROI.
    Crucially: No grade above SILVER is possible with a negative ROI.
    """
    if n < 30:
        return "CHARCOAL"  # Insufficient data
    
    # The 'House Edge' case: High win rate, but negative ROI
    if roi <= 0:
        if lb < break_even - 0.05:
            return "BRONZE" # Clearly losing
        return "IRON"       # Neutral/Slow bleed
    
    # Positive ROI cases: now we check for statistical significance
    if lb > break_even + 0.05 and n > 100:
        return "PLATINUM"  # Statistically dominant and profitable
    if lb > break_even and n > 50:
        return "GOLD"      # Valid and profitable
    if lb > break_even - 0.02:
        return "SILVER"    # Promising, but noisy
        
    return "IRON"

def assay_segment(df: pd.DataFrame, break_even: float = 0.5238) -> AssayResult:
    """
    Perform a full statistical assay on a slice of match data.
    SIMULATION: We bet on the MARKET FAVORITE (the player with the lowest odds).
    """
    n = len(df)
    if n == 0:
        return AssayResult(0, 0, 0, 0, 0, "CHARCOAL", "No Data")

    required = ['winner', 'odds_a', 'odds_b', 'player_a', 'player_b']
    if not all(col in df.columns for col in required):
        return AssayResult(0, 0, n, 0, 0, "CHARCOAL", "Missing Data Columns")

    def get_favorite(row):
        oa, ob = row['odds_a'], row['odds_b']
        if pd.isna(oa) or pd.isna(ob): return None
        return 'a' if oa < ob else 'b'

    df = df.copy()
    df['fav'] = df.apply(get_favorite, axis=1)
    df = df.dropna(subset=['fav'])
    
    n = len(df)
    if n == 0:
        return AssayResult(0, 0, 0, 0, 0, "CHARCOAL", "No Valid Odds")

    def check_win(row):
        if row['fav'] == 'a' and row['winner'] == row['player_a']:
            return 1
        if row['fav'] == 'b' and row['winner'] == row['player_b']:
            return 1
        return 0

    wins_series = df.apply(check_win, axis=1)
    wins = wins_series.sum()
    win_rate = wins / n
    
    def get_return(row):
        if row['fav'] == 'a':
            return row['odds_a'] if row['winner'] == row['player_a'] else 0.0
        else:
            return row['odds_b'] if row['winner'] == row['player_b'] else 0.0

    returns = df.apply(get_return, axis=1)
    roi = returns.mean() - 1
    
    lb, ub = wilson_score_interval(wins, n)
    grade = calculate_grade(lb, roi, n, break_even)
    
    if grade in ["PLATINUM", "GOLD"]:
        verdict = "EDGE CONFIRMED"
    elif grade == "CHARCOAL":
        verdict = "INSUFFICIENT SAMPLE"
    elif grade == "BRONZE":
        verdict = "HOUSE EDGE"
    else:
        verdict = "NO STAT SIG"

    return AssayResult(
        win_rate=float(win_rate),
        roi=float(roi),
        n=int(n),
        wilson_lb=float(lb),
        wilson_ub=float(ub),
        grade=grade,
        verdict=verdict
    )
