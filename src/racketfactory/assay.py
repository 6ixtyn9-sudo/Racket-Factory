"""
Racket Factory Assay Engine (Ma Golide Enhanced)
Statistical verification of betting edges using Wilson intervals, 
shrinkage estimators, and Banker/Robber classification.
"""
import math
import pandas as pd
import numpy as np
from scipy.stats import norm
from typing import NamedTuple, Optional

class AssayResult(NamedTuple):
    win_rate: float
    shrunk_rate: float
    roi: float
    n: int
    wilson_lb: float
    wilson_ub: float
    grade: str
    tier: str  # BANKER, ROBBER, or NEUTRAL
    verdict: str

def wilson_score_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """Calculate the Wilson score interval for a binomial proportion."""
    if trials == 0:
        return 0.0, 0.0
    z = norm.ppf((1 + confidence) / 2)
    p_hat = successes / trials
    denom = 1 + z**2 / trials
    center = p_hat + z**2 / (2 * trials)
    spread = z * math.sqrt((p_hat * (1 - p_hat) / trials) + (z**2 / (4 * trials**2)))
    return (center - spread) / denom, (center + spread) / denom

def shrink_rate(wins: int, n: int, prior_alpha: float = 2.0, prior_beta: float = 2.0) -> float:
    """
    Bayesian Shrinkage Estimator.
    Pulls small sample win rates toward a prior mean (0.5) to prevent 
    overestimating edges from tiny samples.
    """
    return (wins + prior_alpha) / (n + prior_alpha + prior_beta)

def calculate_grade(win_rate: float, shrunk_rate: float, lb: float, roi: float, n: int, break_even: float) -> str:
    """
    Assigns a grade based on the intersection of ROI, WinRate, and Wilson LB.
    Shrunk rate is used to ensure the grade is grounded in sample size.
    """
    if n < 10: return "CHARCOAL"
    
    if roi <= 0:
        return "BRONZE" if lb < break_even - 0.05 else "IRON"
    
    # Use shrunk_rate for the grade to penalize low-N 'fake' edges
    if shrunk_rate > 0.85 and lb > break_even + 0.05 and n > 100:
        return "PLATINUM"
    if shrunk_rate > 0.72 and lb > break_even and n > 50:
        return "GOLD"
    if shrunk_rate > 0.62 and lb > break_even - 0.02:
        return "SILVER"
        
    return "IRON"

def classify_tier(win_rate: float, lb: float, n: int) -> str:
    """
    Classifies the signal as a BANKER (High Reliability) 
    or a ROBBER (Consistently Wrong).
    """
    if n < 10: return "NEUTRAL"
    if lb >= 0.60 and win_rate >= 0.72:
        return "BANKER"
    if win_rate < 0.40 and lb < 0.40:
        return "ROBBER"
    return "NEUTRAL"

def assay_segment(df: pd.DataFrame, break_even: Optional[float] = None) -> AssayResult:
    """
    Perform a full statistical assay on a slice of match data.
    SIMULATION: We bet on the MARKET FAVORITE.
    """
    n = len(df)
    if n == 0:
        return AssayResult(0, 0, 0, 0, 0, 0, "CHARCOAL", "NEUTRAL", "No Data")

    required = ['winner', 'odds_a', 'odds_b', 'player_a', 'player_b']
    if not all(col in df.columns for col in required):
        return AssayResult(0, 0, n, 0, 0, 0, "CHARCOAL", "NEUTRAL", "Missing Data Columns")

    def get_favorite(row):
        oa, ob = row['odds_a'], row['odds_b']
        if pd.isna(oa) or pd.isna(ob): return None
        return 'a' if oa < ob else 'b'

    df = df.copy()
    df['fav'] = df.apply(get_favorite, axis=1)
    df = df.dropna(subset=['fav'])
    
    n = len(df)
    if n == 0:
        return AssayResult(0, 0, 0, 0, 0, 0, "CHARCOAL", "NEUTRAL", "No Valid Odds")

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
    
    if break_even is None:
        fav_odds = df.apply(lambda r: r['odds_a'] if r['fav'] == 'a' else r['odds_b'], axis=1)
        break_even = float((1.0 / fav_odds).mean())

    lb, ub = wilson_score_interval(wins, n)
    shrunk = shrink_rate(wins, n)
    grade = calculate_grade(win_rate, shrunk, lb, roi, n, break_even)
    tier = classify_tier(win_rate, lb, n)
    
    if grade in ["PLATINUM", "GOLD"]:
        verdict = "EDGE CONFIRMED"
    elif tier == "ROBBER":
        verdict = "FADE THIS SIGNAL"
    elif grade == "CHARCOAL":
        verdict = "INSUFFICIENT SAMPLE"
    else:
        verdict = "NO STAT SIG"

    return AssayResult(
        win_rate=float(win_rate),
        shrunk_rate=float(shrunk),
        roi=float(roi),
        n=int(n),
        wilson_lb=float(lb),
        wilson_ub=float(ub),
        grade=grade,
        tier=tier,
        verdict=verdict
    )
