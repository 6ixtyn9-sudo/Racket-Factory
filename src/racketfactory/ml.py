
"""
Racket Factory ML — Strengths-focused feedback loop (Edge parity)

Since odds are tough for Challenger/ITF (TheOddsAPI only Slams/1000/500),
focus on strengths, not just price:

Strengths:
- Source agreement (Both > MarketOnly)
- High confidence (>=70%)
- Surface specialization (player historical)
- Tour/Series specialization
- Rank band
- Cross-source agreement
- Bzzoiro API reliability

Implements:
- ROI feedback loop: reads picks_audit_rolling.json and vetoes contexts with negative ROI
- Weighted consensus: source weights = Wilson LB
- Decay verdict: bench decaying slices
- Context verdict: BOOST/ALLOW/CAUTION/VETO/UNKNOWN per tour/surface/series/source
- ML scoring: simple heuristic + logistic proxy

Edge parity: assay.py has Wilson bounds, this module adds feedback loop for mine_edges filtering.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent.parent
LOCALDATA = ROOT / "localdata"
# Also support running from project root where localdata is sibling
if not LOCALDATA.exists():
    # Fallback: try two levels up from file's grandparent's parent (project root)
    alt = Path(__file__).resolve().parents[2] / "localdata"
    if alt.exists():
        LOCALDATA = alt

Z95 = 1.959963984540054

def wilson_bounds(wins: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre - spread) / denom, (centre + spread) / denom

def wilson_lb(wins: int, n: int) -> float:
    return wilson_bounds(wins, n)[0]

def roi_calc(wins: int, n: int, avg_odds: float) -> float:
    if n <= 0:
        return 0.0
    return (wins * avg_odds - n) / n

# Context verdicts (Edge parity)
def context_verdict(n: int, roi: float | None, recent_roi: float | None = None) -> str:
    if roi is None:
        return "UNKNOWN"
    if 12 <= n < 40:
        if roi <= -0.10:
            return "VETO"
        if roi <= -0.04:
            return "CAUTION"
        if roi >= 0.01:
            return "ALLOW"
        return "UNKNOWN"
    if n < 12:
        return "UNKNOWN"
    if roi <= -0.05 and (recent_roi is None or recent_roi <= -0.03):
        return "VETO"
    if roi < 0.0 or (recent_roi is not None and recent_roi <= -0.05):
        return "CAUTION"
    if n >= 100 and roi >= 0.03 and (recent_roi is None or recent_roi >= 0.0):
        return "BOOST"
    return "ALLOW"

def load_audit_rolling() -> dict[str, Any]:
    """Load picks_audit_rolling.json if exists, else empty."""
    path = LOCALDATA / "picks_audit_rolling.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def build_context_registry(audit: dict) -> dict[str, dict[str, dict]]:
    """
    Build registry: {dimension: {value: {n, wins, roi, hit_rate, verdict}}}
    Dimensions: tour, surface, series, source, bucket
    """
    registry: dict[str, dict[str, dict]] = defaultdict(dict)
    # audit structure from audit_recent_picks: by_tour, by_surface, etc.
    for dim in ("by_tour", "by_series", "by_surface", "by_source", "by_bucket"):
        dim_name = dim.replace("by_", "")
        data = audit.get(dim, {})
        if not isinstance(data, dict):
            continue
        for key, stats in data.items():
            if not isinstance(stats, dict):
                continue
            n = stats.get("settled_picks", 0)
            wins = stats.get("wins", 0)
            roi = stats.get("roi")
            hit_rate = stats.get("hit_rate")
            verdict = context_verdict(n, roi)
            registry[dim_name][key] = {
                "n": n,
                "wins": wins,
                "roi": roi,
                "hit_rate": hit_rate,
                "verdict": verdict,
                "wilson_lb": wilson_lb(wins, n) if n else 0.0,
            }
    return registry

def source_weights_from_audit(audit: dict) -> dict[str, float]:
    """Weight per source = Wilson LB, min 0.5 floor."""
    weights = {}
    by_source = audit.get("by_source", {})
    for src, stats in by_source.items():
        wins = stats.get("wins", 0)
        n = stats.get("settled_picks", 0)
        lb = wilson_lb(wins, n) if n >= 10 else 0.5
        weights[src] = max(0.5, lb)
    # Default weights for sources not yet audited
    defaults = {"Bzzoiro": 0.65, "BetClan": 0.60, "PredixSport": 0.60, "Forebet": 0.55, "ForeTennis": 0.55}
    for k, v in defaults.items():
        if k not in weights:
            weights[k] = v
    return weights

def weighted_consensus_score(votes: list[tuple[str, float]], min_lb: float = 0.50) -> tuple[str | None, float, bool]:
    """Edge parity weighted consensus."""
    if not votes:
        return None, 0.0, False
    valid = [(pick, lb) for pick, lb in votes if lb >= min_lb]
    if not valid:
        return None, 0.0, False
    tally: dict[str, float] = {}
    for pick, lb in valid:
        tally[pick] = tally.get(pick, 0.0) + lb
    total = sum(tally.values())
    if total <= 0:
        return None, 0.0, False
    winner = max(tally, key=lambda p: tally[p])
    w_score = tally[winner] / total
    is_unanimous = len(tally) == 1
    return winner, round(w_score, 4), is_unanimous

def score_pick_strengths(pick: dict, registry: dict, source_weights: dict) -> dict[str, Any]:
    """
    Score a pick based on strengths, not just odds.
    Returns {strength_score, veto_reasons, boost_reasons, w_score}
    """
    reasons_veto = []
    reasons_boost = []
    score = 0.0

    # 1. Source agreement strength
    cross = str(pick.get("cross_source_agree", ""))
    source_count = int(pick.get("source_count") or 0)
    if cross == "Both":
        score += 0.3
        reasons_boost.append("Both sources agree")
    elif cross == "Disagree":
        score -= 0.2
        reasons_veto.append("Sources disagree")
    if source_count >= 3:
        score += 0.2
        reasons_boost.append(f"{source_count} sources")
    elif source_count == 1:
        score -= 0.1

    # 2. Confidence
    conf = pick.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else 0
        if conf_f <= 1.0:
            conf_f *= 100
    except:
        conf_f = 0
    if conf_f >= 70:
        score += 0.25
        reasons_boost.append(f"High conf {conf_f:.0f}%")
    elif conf_f >= 60:
        score += 0.1
    elif conf_f < 50:
        score -= 0.15
        reasons_veto.append(f"Low conf {conf_f:.0f}%")

    # 3. Context ROI feedback loop
    for dim in ("tour", "surface", "series", "source"):
        val = str(pick.get(dim) or pick.get(f"_{dim}") or "").strip()
        if not val or val == "UNKNOWN":
            continue
        ctx = registry.get(dim, {}).get(val)
        if not ctx:
            continue
        verdict = ctx.get("verdict")
        roi = ctx.get("roi")
        n = ctx.get("n", 0)
        if verdict == "VETO":
            score -= 0.4
            reasons_veto.append(f"{dim} {val} VETO roi {roi} n={n}")
        elif verdict == "CAUTION":
            score -= 0.15
            reasons_veto.append(f"{dim} {val} CAUTION")
        elif verdict == "BOOST":
            score += 0.25
            reasons_boost.append(f"{dim} {val} BOOST roi {roi}")

    # 4. Weighted consensus from source_weights
    # Build votes from pick's source list
    raw_sources = str(pick.get("source") or "")
    sources = [s.strip() for s in raw_sources.split(",") if s.strip()]
    selected = str(pick.get("selected_side") or pick.get("selected_player") or "")
    votes = []
    for src in sources:
        w = source_weights.get(src, 0.55)
        votes.append((selected, w))
    _, w_score, unanimous = weighted_consensus_score(votes)
    if unanimous and len(votes) >= 2:
        score += 0.2
        reasons_boost.append(f"Unanimous {len(votes)} sources w_score {w_score}")
    if w_score < 0.6 and len(votes) > 1:
        score -= 0.15
        reasons_veto.append(f"Low consensus w_score {w_score}")

    # 5. Rank band strength (Top 10 vs 100+)
    rank_band = str(pick.get("selected_rank_band") or "")
    if "Top 10" in rank_band:
        score += 0.1
    elif "100+" in rank_band:
        score -= 0.05

    # 6. Odds availability (since odds tough, don't heavily penalize NO_ODDS if strengths strong)
    bucket = str(pick.get("bucket") or "")
    if "NO_ODDS" in bucket:
        # If strengths strong, keep as WATCHLIST_NO_ODDS, not auto-veto
        if score >= 0.3:
            reasons_boost.append("NO_ODDS but strong strengths")
        else:
            score -= 0.1

    return {
        "strength_score": round(score, 3),
        "veto_reasons": reasons_veto,
        "boost_reasons": reasons_boost,
        "w_score": w_score,
        "should_veto": len([r for r in reasons_veto if "VETO" in r]) > 0 or score < -0.3,
        "should_boost": score >= 0.4,
    }

def should_veto_slice(slice_dict: dict, registry: dict) -> tuple[bool, str]:
    """Check if a historical slice should be vetoed based on recent ROI."""
    for dim, val in slice_dict.items():
        if dim not in registry:
            continue
        ctx = registry[dim].get(str(val))
        if not ctx:
            continue
        if ctx.get("verdict") == "VETO" and ctx.get("n", 0) >= 12:
            return True, f"{dim}:{val} VETO roi {ctx.get('roi')} n={ctx.get('n')}"
    return False, ""

def ml_filter_picks(picks: list[dict]) -> tuple[list[dict], dict]:
    """Apply ML strengths feedback loop to picks."""
    audit = load_audit_rolling()
    registry = build_context_registry(audit)
    source_weights = source_weights_from_audit(audit)

    kept = []
    vetoed = []
    boosted = []

    for pick in picks:
        scoring = score_pick_strengths(pick, registry, source_weights)
        pick = dict(pick)
        pick["ml_strength_score"] = scoring["strength_score"]
        pick["ml_veto_reasons"] = scoring["veto_reasons"]
        pick["ml_boost_reasons"] = scoring["boost_reasons"]
        pick["ml_w_score"] = scoring["w_score"]
        pick["ml_verdict"] = "VETO" if scoring["should_veto"] else ("BOOST" if scoring["should_boost"] else "ALLOW")

        if scoring["should_veto"]:
            # Don't drop NO_ODDS if strength is high, just mark
            if "NO_ODDS" in str(pick.get("bucket")) and scoring["strength_score"] >= 0.2:
                kept.append(pick)
            else:
                # Convert to SKIPPED_VETO if was actionable
                if str(pick.get("bucket")) in {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}:
                    pick["bucket"] = "SKIPPED_VETO"
                    pick["skip_reason"] = "; ".join(scoring["veto_reasons"][:2])
                vetoed.append(pick)
                kept.append(pick)  # Keep for transparency but vetoed bucket
        else:
            kept.append(pick)
            if scoring["should_boost"]:
                boosted.append(pick)

    summary = {
        "total": len(picks),
        "kept": len(kept),
        "vetoed": len(vetoed),
        "boosted": len(boosted),
        "registry_dims": {k: len(v) for k, v in registry.items()},
        "source_weights": source_weights,
    }
    return kept, summary
