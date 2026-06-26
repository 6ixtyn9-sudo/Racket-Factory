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
    """
    Calculate the Wilson score interval for a binomial proportion.
    This provides a lower bound (LB) and upper bound (UB) for the true win rate.
    """
    if trials == 0:
        return 0.0, 0.0
    
    # z-score for 95% confidence
    z = 1.96 
    
    p_hat = successes / trials
    denom = 1 + z**2 / trials
    center = p_hat + z**2 / (2 * trials)
    spread = z * math.sqrt((p_hat * (1 - p_hat) / trials) + (z**2 / (4 * trials**2)))
    
    return (center - spread) / denom, (center + spread) / denom

def calculate_grade(lb: float, roi: float, n: int, break_even: float = 0.5238) -> str:
    """
    Grade an edge based on the Wilson Lower Bound and sample size.
    Break-even for 1.91 odds is ~52.38%.
    """
    if n < 30:
        return "CHARCOAL"  # Insufficient data to make any claim
    
    if lb > break_even + 0.05 and n > 100:
        return "PLATINUM"  # Statistically dominant edge
    if lb > break_even and n > 50:
        return "GOLD"      # Valid, statistically significant edge
    if lb > break_even - 0.02 and roi > 0:
        return "SILVER"    # Promising, but the LB hasn't cleared break-even yet
    if lb < break_even - 0.05:
        return "BRONZE"    # Likely a losing strategy
    
    return "IRON"          # Neutral/Noisy

def assay_segment(df: pd.DataFrame, break_even: float = 0.5238) -> AssayResult:
    """
    Perform a full statistical assay on a slice of match data.
    """
    n = len(df)
    if n == 0:
        return AssayResult(0, 0, 0, 0, 0, "CHARCOAL", "No Data")

    # Ensure we have the necessary columns
    if 'winner' not in df.columns or 'player_a' not in df.columns:
        return AssayResult(0, 0, n, 0, 0, "CHARCOAL", "Missing Data Columns")

    # We test the 'edge' of player_a.
    # A win is when player_a is the winner.
    wins = (df['winner'] == df['player_a']).sum()
    win_rate = wins / n
    
    # ROI calculation: (Total Return - Total Stake) / Total Stake
    # We use odds_a. We assume stake is 1 unit per match.
    if 'odds_a' not in df.columns:
        return AssayResult(win_rate, 0, n, 0, 0, "CHARCOAL", "Missing Odds Column")

    # Filter out NaN or invalid odds to prevent ROI corruption
    valid_odds = pd.to_numeric(df['odds_a'], errors='coerce').fillna(1.0)
    returns = valid_odds.where(df['winner'] == df['player_a'], 0.0)
    roi = returns.mean() - 1
    
    lb, ub = wilson_score_interval(wins, n)
    grade = calculate_grade(lb, roi, n, break_even)
    
    if grade in ["PLATINUM", "GOLD"]:
        verdict = "EDGE CONFIRMED"
    elif grade == "CHARCOAL":
        verdict = "INSUFFICIENT SAMPLE"
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
