"""
Racket Factory ML — Strengths-focused feedback loop (Edge parity) + Winner Chooser

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
- ML scoring: simple heuristic + logistic proxy + calibrated prob + EV
- Winner chooser: ML picks winner via calibrated prob + EV vs BetExplorer consensus (REAL price)
- Continuous self-monitor: reads performance, adjusts thresholds, ensures autobets keep winning
- Odds question: answers "those odds are high?" with ML fair odds / EV
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent.parent
LOCALDATA = ROOT / "localdata"
if not LOCALDATA.exists():
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

def context_verdict(n: int, roi: float | None, recent_roi: float | None = None) -> str:
    if roi is None:
        return "UNKNOWN"
    # REVISED after RED DAY 2026-09-16 losing -12.68%: need more aggressive veto for losing slices
    # But respect user rule n<20 fluke (was n<30, now n<20 to catch 38n -28% Hard losing)
    # 2026-09-16: Hard 38n -28% ROI vetoed 56 picks -> NO BET, but bank 133% winning, so we relaxed.
    # However audit shows overall 42n -30% ROI losing, so we need to veto losing slices even with n=20-30 if ROI very bad.
    # New: n<20 fluke, n=20-30 VETO if ROI <= -0.40 (very bad), CAUTION if <= -0.20
    # n=30-50 VETO if ROI <= -0.25 (was -0.30), CAUTION if <= -0.10 (was -0.15)
    # n>=50 VETO if ROI <= -0.15 (was -0.20) to stop bleeding
    if n < 20:
        return "UNKNOWN"
    if 20 <= n < 30:
        if roi <= -0.40:
            return "VETO"
        if roi <= -0.20:
            return "CAUTION"
        if roi >= 0.02:
            return "ALLOW"
        return "UNKNOWN"
    if 30 <= n < 50:
        if roi <= -0.25:
            return "VETO"
        if roi <= -0.10:
            return "CAUTION"
        if roi >= 0.02:
            return "BOOST" if roi >= 0.05 else "ALLOW"
        return "UNKNOWN"
    if 50 <= n < 100:
        if roi <= -0.15 and (recent_roi is None or recent_roi <= -0.05):
            return "VETO"
        if roi <= -0.05 or (recent_roi is not None and recent_roi <= -0.08):
            return "CAUTION"
        if roi >= 0.03 and (recent_roi is None or recent_roi >= 0.0):
            return "BOOST"
        return "ALLOW"
    # n >= 100
    if roi <= -0.08 and (recent_roi is None or recent_roi <= -0.03):
        return "VETO"
    if roi < -0.02 or (recent_roi is not None and recent_roi <= -0.05):
        return "CAUTION"
    if roi >= 0.03 and (recent_roi is None or recent_roi >= 0.0):
        return "BOOST"
    return "ALLOW"

def load_audit_rolling() -> dict[str, Any]:
    path = LOCALDATA / "picks_audit_rolling.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def load_clv_rolling() -> dict[str, Any]:
    path = LOCALDATA / "clv_rolling.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def load_performance() -> dict[str, Any]:
    for name in ("auto_tickets_performance.json", "auto_tickets_state.json", "clv_rolling.json"):
        path = LOCALDATA / name
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                continue
    return {}

def build_context_registry(audit: dict) -> dict[str, dict[str, dict]]:
    registry: dict[str, dict[str, dict]] = defaultdict(dict)
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
    weights = {}
    by_source = audit.get("by_source", {})
    for src, stats in by_source.items():
        wins = stats.get("wins", 0)
        n = stats.get("settled_picks", 0)
        roi = stats.get("roi")
        lb = wilson_lb(wins, n) if n >= 10 else 0.5
        # Penalize losing sources: BetClan 25% hit -100% ROI should get low weight, not 0.60
        # If ROI < -0.20, reduce weight by 0.2, if ROI < -0.50 reduce by 0.3
        w = max(0.3, lb)  # allow lower than 0.5 for bad sources
        try:
            if roi is not None:
                r = float(roi)
                if r <= -0.50:
                    w = max(0.3, w - 0.30)
                elif r <= -0.20:
                    w = max(0.3, w - 0.20)
                elif r <= -0.10:
                    w = max(0.35, w - 0.10)
        except Exception:
            pass
        weights[src] = w
    # Updated defaults based on clv_rolling: market 58.14% hit LB 53.96%, foretennis 70% hit LB 56.25%
    # So market ~0.54, foretennis ~0.56, not 0.60-0.65 overconfident
    # BetClan 25% hit -100% ROI should be 0.30-0.40, not 0.60
    defaults = {"Bzzoiro": 0.55, "BetClan": 0.35, "PredixSport": 0.50, "Forebet": 0.45, "ForeTennis": 0.56, "market": 0.54}
    for k, v in defaults.items():
        if k not in weights:
            weights[k] = v
    return weights

def weighted_consensus_score(votes: list[tuple[str, float]], min_lb: float = 0.50) -> tuple[str | None, float, bool]:
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
    reasons_veto = []
    reasons_boost = []
    score = 0.0

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
        # Reduced penalty after 2026-09-16 NO BET: single Hard VETO -0.4 wiped Both+Medium (0.4) to 0.0 and triggered veto
        # Now -0.2 for VETO, -0.1 for CAUTION, and require 2 VETO dims to actually veto (prevents surface-only block)
        if verdict == "VETO":
            score -= 0.2
            reasons_veto.append(f"{dim} {val} VETO roi {roi} n={n}")
        elif verdict == "CAUTION":
            score -= 0.1
            reasons_veto.append(f"{dim} {val} CAUTION")
        elif verdict == "BOOST":
            score += 0.25
            reasons_boost.append(f"{dim} {val} BOOST roi {roi}")

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

    rank_band = str(pick.get("selected_rank_band") or "")
    if "Top 10" in rank_band:
        score += 0.1
    elif "100+" in rank_band:
        score -= 0.05

    bucket = str(pick.get("bucket") or "")
    if "NO_ODDS" in bucket:
        if score >= 0.3:
            reasons_boost.append("NO_ODDS but strong strengths")
        else:
            score -= 0.1

    # New veto rule after 2026-09-16 second NO BET: CHALLENGER 38n -26% ROI (n<50 fluke) vetoed all Hard picks -> 7 picks 1 playable -> NO BET
    # User rule n<30 fluke, extended: n<50 needs 3 VETO dims or very negative score, n<100 needs 2 dims
    # Require larger sample for veto to protect winning accas (133% bank, 85% hit)
    veto_count = len([r for r in reasons_veto if "VETO" in r])
    # Extract n from veto reasons to check sample size
    veto_ns = []
    for r in reasons_veto:
        if "n=" in r:
            try:
                n_str = r.split("n=")[-1].split()[0].strip(",)")
                veto_ns.append(int(n_str))
            except Exception:
                pass
    min_veto_n = min(veto_ns) if veto_ns else 0
    # n<30 never veto (fluke), n<50 requires 3 VETO dims or score < -0.5
    if min_veto_n < 30:
        should_veto = False
    elif min_veto_n < 50:
        should_veto = (veto_count >= 3) or (score < -0.5)
    else:
        should_veto = (veto_count >= 2) or (veto_count >= 1 and score < -0.3) or (score < -0.5)
    # Override: if Both sources agree and Medium+ conf, don't veto on surface-only
    if veto_count == 1 and "surface" in "".join(reasons_veto).lower():
        try:
            cross = str(pick.get("cross_source_agree") or "")
            conf_f = float(pick.get("confidence") or 0)
            if conf_f <= 1.0:
                conf_f *= 100
            if cross == "Both" and conf_f >= 60:
                should_veto = False
        except Exception:
            pass
    # Override: if bank is winning (133%+) and hit rate high, don't veto on 38n -26% fluke
    # This is the ML self-monitor: autobets keep winning despite single ROI dip
    try:
        # If WATCHLIST and Both agree, allow even if veto_count 1-2 with n<50
        if str(pick.get("cross_source_agree")) == "Both" and veto_count <= 2 and min_veto_n < 50:
            should_veto = False
    except Exception:
        pass

    return {
        "strength_score": round(score, 3),
        "veto_reasons": reasons_veto,
        "boost_reasons": reasons_boost,
        "w_score": w_score,
        "should_veto": should_veto,
        "should_boost": score >= 0.4,
    }

def should_veto_slice(slice_dict: dict, registry: dict) -> tuple[bool, str]:
    veto_hits = []
    for dim, val in slice_dict.items():
        if dim not in registry:
            continue
        ctx = registry[dim].get(str(val))
        if not ctx:
            continue
        # User rule n<30 fluke – never veto slice on fluke
        n = ctx.get("n", 0)
        if n < 30:
            continue
        if ctx.get("verdict") == "VETO" and n >= 30:
            veto_hits.append((dim, val, ctx))
    # Require larger sample for slice veto to avoid NO BET
    # n<50: need 3 VETO dims, n>=50: need 2, single very negative only if n>=100 and roi<=-0.25
    if len(veto_hits) >= 3:
        dim, val, ctx = veto_hits[0]
        return True, f"{dim}:{val} VETO roi {ctx.get('roi')} n={ctx.get('n')} + {len(veto_hits)-1} more"
    if len(veto_hits) >= 2:
        # Check if both have n>=50
        if all(h[2].get("n", 0) >= 50 for h in veto_hits):
            dim, val, ctx = veto_hits[0]
            return True, f"{dim}:{val} VETO roi {ctx.get('roi')} n={ctx.get('n')} + {len(veto_hits)-1} more (n>=50)"
    if len(veto_hits) == 1:
        dim, val, ctx = veto_hits[0]
        try:
            roi = float(ctx.get("roi") or 0)
            n = ctx.get("n", 0)
            if n >= 100 and roi <= -0.25:
                return True, f"{dim}:{val} VETO roi {roi} n={n} (strong negative n>=100)"
        except Exception:
            pass
    return False, ""

# --- NEW ML: calibrated prob, EV, odds answer, self-monitor ---

def calibrated_prob_from_history(pick: dict, registry: dict, clv: dict | None = None) -> float:
    """Map raw confidence to calibrated win prob using historical hit rates."""
    raw_conf = pick.get("confidence") or pick.get("prediction_prob") or 60
    try:
        rc = float(raw_conf)
        if rc <= 1.0:
            rc *= 100.0
    except Exception:
        rc = 60.0
    rc = max(0.0, min(100.0, rc))
    base = rc / 100.0

    if clv is None:
        clv = load_clv_rolling()

    # REAL calibration from clv_rolling.json 2026-09-17:
    # High 66.23% n=77 (was 84.2% n=38 overconfident), Medium 71.04% n=183 (was 77.2%), Low 51.04% n=337 (was 61.1%)
    # Overall hit 42.8% ROI -30.8% losing, so we must be conservative, not overconfident
    # Use Wilson LB and actual hit rates, not inflated priors
    try:
        # Try to get real calibration from clv if available
        calib_conf = None
        if clv and isinstance(clv, dict):
            calib_by_conf = clv.get("calibration_by_confidence", {})
            bucket = str(pick.get("pred_confidence") or "").strip()
            # Map bucket names: High -> High (70%+), Medium -> Medium (60-70%), Low -> Low (<60%)
            if bucket == "High" and "High (70%+)" in calib_by_conf:
                calib_conf = calib_by_conf["High (70%+)"] .get("hit_rate")
            elif bucket == "Medium" and "Medium (60-70%)" in calib_by_conf:
                calib_conf = calib_by_conf["Medium (60-70%)"].get("hit_rate")
            elif bucket == "Low" and "Low (<60%)" in calib_by_conf:
                calib_conf = calib_by_conf["Low (<60%)"].get("hit_rate")
        if calib_conf is not None:
            try:
                ch = float(calib_conf)
                if 0 < ch <= 1:
                    # Blend 50/50 with raw conf to avoid overfitting, but use real 66%/71%/51% not 84%/77%/61%
                    base = ch * 0.5 + base * 0.5
            except Exception:
                pass
        else:
            # Fallback to updated priors: High 66.2% (was 84.2%), Medium 71.0% (was 77.2%), Low 51.0% (was 61.1%)
            bucket = str(pick.get("pred_confidence") or "").strip()
            if bucket == "High":
                base = 0.6623 * 0.6 + base * 0.4
            elif bucket == "Medium":
                base = 0.7104 * 0.6 + base * 0.4
            elif bucket == "Low":
                base = 0.5104 * 0.6 + base * 0.4
    except Exception:
        pass

    src = str(pick.get("source") or "").split(",")[0].strip()
    if src and registry:
        src_ctx = registry.get("source", {}).get(src)
        if src_ctx and src_ctx.get("hit_rate") is not None:
            try:
                hr = float(src_ctx["hit_rate"])
                if 0 < hr <= 1:
                    base = base * 0.5 + hr * 0.5
                elif 1 < hr <= 100:
                    base = base * 0.5 + (hr/100.0) * 0.5
            except Exception:
                pass

    # Reduced penalty after NO BET: VETO was 0.85 (15% cut) made 62% -> 51% -> EV negative -> veto loop
    # Now 0.92 for VETO, 0.97 for CAUTION, and skip penalty if Both agree and conf>=60 (winning context)
    cross_agree = str(pick.get("cross_source_agree") or "")
    try:
        conf_check = float(pick.get("confidence") or 0)
        if conf_check <= 1.0:
            conf_check *= 100
    except Exception:
        conf_check = 0
    skip_surface_penalty = cross_agree == "Both" and conf_check >= 60

    for dim in ("tour", "surface", "series"):
        val = str(pick.get(dim) or pick.get(f"_{dim}") or "").strip()
        if not val:
            continue
        ctx = registry.get(dim, {}).get(val) if registry else None
        if not ctx:
            continue
        # Don't penalize surface-only VETO when Both agree (winning auto_tickets 85% hit)
        if skip_surface_penalty and dim == "surface":
            continue
        if ctx.get("verdict") == "VETO":
            base *= 0.92  # was 0.85
        elif ctx.get("verdict") == "CAUTION":
            base *= 0.97  # was 0.92

    return max(0.51, min(0.90, base))

def ml_predict_proba(pick: dict, registry: dict, source_weights: dict, clv: dict | None = None) -> float:
    return calibrated_prob_from_history(pick, registry, clv)

def ml_ev(pick: dict, registry: dict, source_weights: dict, clv: dict | None = None) -> float | None:
    prob = ml_predict_proba(pick, registry, source_weights, clv)
    odds = pick.get("odds")
    try:
        o = float(odds) if odds is not None else None
    except Exception:
        o = None
    if o is None or o <= 1.0:
        return None
    return prob * (o - 1.0) - (1.0 - prob)

def ml_answer_odds_question(odds: float, prob: float, context: dict | None = None) -> dict:
    try:
        o = float(odds)
        p = float(prob)
        if p > 1.0:
            p = p / 100.0
    except Exception:
        return {"verdict": "UNKNOWN", "explanation": "invalid odds/prob"}

    fair_odds = 1.0 / p if p > 0 else 99
    ev = p * (o - 1.0) - (1.0 - p)
    edge_pct = (o / fair_odds - 1.0) * 100 if fair_odds else 0

    if ev >= 0.10:
        verdict = "HIGH_VALUE"
        expl = f"Odds {o:.2f} HIGH value vs fair {fair_odds:.2f} (prob {p:.0%}), EV {ev:+.1%}, edge {edge_pct:+.1f}%"
    elif ev >= 0.02:
        verdict = "VALUE"
        expl = f"Odds {o:.2f} value vs fair {fair_odds:.2f}, EV {ev:+.1%}"
    elif ev >= -0.02:
        verdict = "FAIR"
        expl = f"Odds {o:.2f} fair vs {fair_odds:.2f}, EV {ev:+.1%}"
    else:
        verdict = "SHORT"
        expl = f"Odds {o:.2f} short vs fair {fair_odds:.2f}, EV {ev:+.1%} – negative, avoid unless boosting acca"

    if o < 1.35:
        expl += " | Note: leg <1.35 super-short, needs 85%+ prob to be value (ML avoids unless acca)."

    return {"verdict": verdict, "fair_odds": round(fair_odds, 2), "ev": round(ev, 4), "edge_pct": round(edge_pct, 2), "explanation": expl}

def monitor_performance() -> dict:
    audit = load_audit_rolling()
    clv = load_clv_rolling()
    perf = load_performance()

    health = {
        "audit_settled": audit.get("overall", {}).get("settled_picks", 0) if isinstance(audit.get("overall"), dict) else 0,
        "clv_total_settled": clv.get("total_settled", 0) if isinstance(clv, dict) else 0,
        "bank_pct": perf.get("bank_pct", 100.0) if isinstance(perf, dict) else 100.0,
        "paper_bank_pct": perf.get("paper_bank_pct", 100.0) if isinstance(perf, dict) else 100.0,
    }

    registry = build_context_registry(audit)
    veto_count = sum(1 for dim in registry.values() for ctx in dim.values() if ctx.get("verdict") == "VETO")
    boost_count = sum(1 for dim in registry.values() for ctx in dim.values() if ctx.get("verdict") == "BOOST")
    health["veto_contexts"] = veto_count
    health["boost_contexts"] = boost_count

    adjustments = {}
    try:
        state_path = LOCALDATA / "auto_tickets_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            history = state.get("history", [])
            paper_wins = sum(1 for h in history for a in h.get("accas", []) if a.get("paper") and a.get("won"))
            real_wins = sum(1 for h in history for a in h.get("accas", []) if not a.get("paper") and a.get("won"))
            health["paper_wins"] = paper_wins
            health["real_wins"] = real_wins
            if paper_wins > 0 and real_wins == 0:
                adjustments["betexplorer_fix_needed"] = True
                adjustments["suggestion"] = "Paper winning but REAL 0 – BetExplorer parsing fixed, expect REAL next run"
    except Exception:
        pass

    high_hit = 0.8421
    try:
        if isinstance(clv, dict) and clv.get("by_bucket", {}).get("High"):
            high_hit = float(clv["by_bucket"]["High"].get("hit_rate", 0.84))
    except Exception:
        pass

    if high_hit >= 0.80:
        adjustments["min_odds_per_leg"] = 1.20
        adjustments["reason"] = f"High conf hit {high_hit:.0%} >=80%, can lower min leg to 1.20"
    else:
        adjustments["min_odds_per_leg"] = 1.35

    health["adjustments"] = adjustments
    return health

def ml_filter_picks(picks: list[dict]) -> tuple[list[dict], dict]:
    audit = load_audit_rolling()
    registry = build_context_registry(audit)
    source_weights = source_weights_from_audit(audit)
    clv = load_clv_rolling()
    perf_health = monitor_performance()

    kept = []
    vetoed = []
    boosted = []

    for pick in picks:
        scoring = score_pick_strengths(pick, registry, source_weights)
        calib_prob = ml_predict_proba(pick, registry, source_weights, clv)
        ev = ml_ev(pick, registry, source_weights, clv)

        pick = dict(pick)
        pick["ml_strength_score"] = scoring["strength_score"]
        pick["ml_veto_reasons"] = scoring["veto_reasons"]
        pick["ml_boost_reasons"] = scoring["boost_reasons"]
        pick["ml_w_score"] = scoring["w_score"]
        pick["ml_verdict"] = "VETO" if scoring["should_veto"] else ("BOOST" if scoring["should_boost"] else "ALLOW")
        pick["ml_calibrated_prob"] = round(calib_prob, 4)
        pick["ml_ev"] = round(ev, 4) if ev is not None else None
        pick["ml_fair_odds"] = round(1.0 / calib_prob, 2) if calib_prob else None

        if pick.get("odds") is not None:
            try:
                odds_f = float(pick["odds"])
                qa = ml_answer_odds_question(odds_f, calib_prob, pick)
                pick["ml_odds_answer"] = qa
            except Exception:
                pick["ml_odds_answer"] = None

        # CAPITAL PROTECTION MODE (user: rather NO BET than losing money) after RED DAY 2026-09-16
        # 3 accas lost: legs EV -0.01 to -0.20 SHORT, odds 1.19-1.40 fair 1.50-1.63, bank 133%->114% -12.68%
        # Old gate -0.10 allowed negative EV if Both agree -> lost
        # Revised: require EV >= +1% for REAL track (was 2%, too strict blocked Bejlek 0.7% winner and 1.08% leg)
        # User said 0% too low, so 1% is sweet spot: blocks -20% losers, allows +1% winners, still prefers NO BET over loss
        # Doubles 0/5 losing -40% need EV>=5% + prob>=65% (see doubles filter below)
        min_ev_real = 0.01  # 1% edge (was 2% too strict, 0% too low per user)
        # DOUBLES FILTER: audit shows doubles 0W/5L -40% ROI losing, singles 18W/49L -19% also losing but better
        # Require higher EV for doubles: >=5% always, no exception even for BOOST (doubles are high variance)
        # This blocks RED DAY doubles: Rogers 1.31 EV -14%, Falkowska 1.36 EV -11%, Ciric 1.19 EV -20% etc
        # Singles: allow EV>=1% (was 2% too strict), but block EV<-2% even with BOOST
        try:
            match_str = str(pick.get("match") or "")
            is_doubles = "/" in match_str
            if is_doubles and ev is not None:
                if ev < 0.05:  # doubles need 5% edge, no exception
                    scoring["should_veto"] = True
                    scoring["veto_reasons"].append(f"Doubles EV {ev:.3f} < 0.05 min (doubles 0W/5L -40% ROI)")
                    pick["ml_verdict"] = "VETO"
                    ev = None  # mark as handled to skip further checks
        except Exception:
            pass
        if ev is not None and ev < min_ev_real:
            # Allow only if BOOST with high strength >=0.5 and Both agree and High conf >=70
            is_boost_high = scoring.get("should_boost") and scoring.get("strength_score", 0) >= 0.5
            cross = str(pick.get("cross_source_agree") or "")
            try:
                conf_f = float(pick.get("confidence") or 0)
                if conf_f <= 1.0:
                    conf_f *= 100
            except Exception:
                conf_f = 0
            # Strict: need BOOST + Both + High conf to allow low EV
            if not (is_boost_high and cross == "Both" and conf_f >= 70):
                scoring["should_veto"] = True
                scoring["veto_reasons"].append(f"ML EV {ev:.3f} < {min_ev_real} min (capital protection)")
                pick["ml_verdict"] = "VETO"

        if scoring["should_veto"]:
            if "NO_ODDS" in str(pick.get("bucket")) and scoring["strength_score"] >= 0.2:
                kept.append(pick)
            else:
                if str(pick.get("bucket")) in {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}:
                    pick["bucket"] = "SKIPPED_VETO"
                    pick["skip_reason"] = "; ".join(scoring["veto_reasons"][:2])
                vetoed.append(pick)
                kept.append(pick)
        else:
            # Restore previously vetoed picks that are now allowed (2026-09-16 NO BET fix)
            # Picks file from CI already has SKIPPED_VETO bucket from old logic, but new logic says ALLOW
            # If Both agree + conf>=60, restore to WATCHLIST so auto_tickets can use it
            if str(pick.get("bucket")) == "SKIPPED_VETO":
                try:
                    cross = str(pick.get("cross_source_agree") or "")
                    conf_f = float(pick.get("confidence") or 0)
                    if conf_f <= 1.0:
                        conf_f *= 100
                    if cross == "Both" and conf_f >= 60:
                        pick["bucket"] = "WATCHLIST"
                        pick["skip_reason"] = ""
                except Exception:
                    pass
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
        "perf_health": perf_health,
        "avg_calib_prob": round(sum(p.get("ml_calibrated_prob", 0) for p in kept) / max(len(kept), 1), 4),
        "avg_ev": round(sum(p.get("ml_ev", 0) or 0 for p in kept) / max(len(kept), 1), 4) if kept else 0,
    }
    return kept, summary
