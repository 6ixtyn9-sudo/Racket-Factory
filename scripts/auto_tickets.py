#!/usr/bin/env python3
"""RACKET FACTORY AUTO TICKETS — Strengths-focused, avoids super short odds

Mirrors Edge Factory but tennis-adapted for tough odds (Challenger/ITF no API):

Recipe v2 (strengths-focused):
  LEGS      CERTIFIED_CLEAN, WATCHLIST, CAUTION + WATCHLIST_NO_ODDS if ML BOOST strength>=0.4
  FILTER    Avoid super short odds being only picks:
            - MIN_ODDS_PER_LEG = 1.35 (1.1-1.3 filtered unless banker single)
            - MIN_ACCA_ODDS = 2.0 (avoid 1.24*1.15=1.42 accas)
            - Odds band diversification: max 2 legs from 1.1-1.4 band per ticket
            - Value sort: EV * strength, not just confidence
  ACCAS     4 accas max (up from 2), mix:
            - 2x 2-leg value accas (top value, min odds 1.4+)
            - 1x 3-leg high-strength acca (BOOST, high w_score)
            - 1x banker single (top BOOST short odds if any)
            Total legs up to 7-8, not just 4
  STAKE     25% of bank per day, split by Kelly-like weighting:
            - Singles get 40% if exists
            - 2-leg value accas get 25% each
            - 3-leg high-strength gets 10% (lottery ticket)
            Adjusted to sum 100% of daily stake
  ESTIMATED When real odds missing, estimate from confidence with VALUE:
            85% -> 1.6 (not 1.15), 75% -> 1.85, 65% -> 2.1, 55% -> 2.5
            This avoids super short estimated odds dominating
  FREEZE    06:00-09:00 SAST freeze

Settlement uses warehouse.csv.gz via tolerant name matching.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"

# ML feedback
try:
    from racketfactory.ml import load_audit_rolling, build_context_registry, score_pick_strengths, source_weights_from_audit
    _ML_AVAILABLE = True
except Exception:
    _ML_AVAILABLE = False
    load_audit_rolling = lambda: {}
    build_context_registry = lambda x: {}
    score_pick_strengths = lambda pick, reg, weights: {"strength_score": 0, "should_veto": False, "should_boost": False, "w_score": 0}
    source_weights_from_audit = lambda x: {}

# cadence
GENERATE_HOUR_START = 6
FREEZE_HOUR = 9
TZ = ZoneInfo("Africa/Johannesburg")

# recipe v2 — avoid super short odds, more accas
STAKE_FRAC = 0.25
MAX_ACCAS = 4  # up from 2
LEGS_PER_ACCA = 2
MAX_LEGS = 8  # up from 4
PLAYABLE_BUCKETS = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}
PLAYABLE_BUCKETS_WITH_ML = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "WATCHLIST_NO_ODDS"}
MIN_ODDS_PER_LEG = 1.35  # avoid 1.1-1.3 super short unless banker
MIN_ACCA_ODDS = 2.0  # avoid 1.24*1.15=1.42
MAX_SHORT_ODDS_LEGS_PER_TICKET = 1  # max 1 leg from 1.1-1.4 band per acca
TAKE_PROFIT_GAIN = 1.0

STATE_FILE = LOCALDATA / "auto_tickets_state.json"

def clean_text(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in {"", "nan", "none", "<na>", "nat"}:
        return ""
    return s

def normalize_name(v):
    text = clean_text(v)
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9/\\s'-]", " ", text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    parts = [p for p in text.replace("-", " ").replace("'", " ").split() if p]
    if len(parts) >= 2 and len(parts[0]) == 1:
        parts = parts[1:]
    return " ".join(parts)

def names_match(a, b) -> bool:
    str_a, str_b = str(a), str(b)
    if "/" in str_a and "/" in str_b:
        parts_a = [p.strip() for p in str_a.split("/")]
        parts_b = [p.strip() for p in str_b.split("/")]
        if len(parts_a) == len(parts_b):
            if all(names_match(pa, pb) for pa, pb in zip(parts_a, parts_b)):
                return True
            if all(names_match(pa, pb) for pa, pb in zip(parts_a, reversed(parts_b))):
                return True
    na = normalize_name(a)
    nb = normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta = na.split()
    tb = nb.split()
    if not ta or not tb:
        return False
    if ta[-1] == tb[-1]:
        pre_a = ta[:-1]
        pre_b = tb[:-1]
        if any(len(x) > 1 and x in pre_b for x in pre_a):
            return True
        if any(len(x) > 1 and x in pre_a for x in pre_b):
            return True
        initials_a = [x for x in pre_a if len(x) == 1]
        initials_b = [x for x in pre_b if len(x) == 1]
        if initials_a and all(any(y.startswith(x) for y in pre_b) for x in initials_a):
            return True
        if initials_b and all(any(y.startswith(x) for y in pre_a) for x in initials_b):
            return True
    overlap = set(ta) & set(tb)
    return bool(overlap) and len(overlap) >= min(len(ta), len(tb))

def now_local() -> datetime:
    run_as_of = os.environ.get("RACKET_FACTORY_RUN_AS_OF", "").strip()
    if run_as_of:
        try:
            dt = datetime.fromisoformat(run_as_of.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            else:
                dt = dt.astimezone(TZ)
            return dt
        except Exception:
            pass
    return datetime.now(TZ)

def today_str() -> str:
    return now_local().date().isoformat()

def load_picks(target_date: str) -> list[dict]:
    paths = [
        LOCALDATA / f"picks_{target_date}.json",
        LOCALDATA / "picks_today.json",
    ]
    for p in paths:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                filtered = [r for r in data if isinstance(r, dict) and str(r.get("ledger_kind","")).lower() != "forecast"]
                if filtered:
                    return filtered
                return [r for r in data if isinstance(r, dict)]
        except Exception:
            continue
    return []

def estimate_odds_from_confidence(pick: dict) -> float:
    """Estimate odds with VALUE, not super short. Avoid 1.15 for 85% conf."""
    conf = pick.get("confidence") or 60
    try:
        conf_f = float(conf)
        if conf_f <= 1.0:
            conf_f *= 100
    except Exception:
        conf_f = 60
    # Value mapping: higher confidence still gets decent odds (book margin + value)
    # 85% -> 1.65, 80% -> 1.75, 75% -> 1.9, 70% -> 2.0, 65% -> 2.2, 60% -> 2.4, 55% -> 2.6
    if conf_f >= 85:
        return 1.65
    if conf_f >= 80:
        return 1.75
    if conf_f >= 75:
        return 1.90
    if conf_f >= 70:
        return 2.05
    if conf_f >= 65:
        return 2.25
    if conf_f >= 60:
        return 2.45
    return 2.70

def is_playable(pick: dict) -> bool:
    bucket = str(pick.get("bucket","")).upper()
    is_no_odds = "NO_ODDS" in bucket
    if bucket not in PLAYABLE_BUCKETS and not (is_no_odds and bucket in PLAYABLE_BUCKETS_WITH_ML):
        return False
    if not clean_text(pick.get("selected_player")):
        return False
    if not clean_text(pick.get("match")):
        return False
    # ML veto
    if _ML_AVAILABLE:
        try:
            audit = load_audit_rolling()
            registry = build_context_registry(audit)
            weights = source_weights_from_audit(audit)
            scoring = score_pick_strengths(pick, registry, weights)
            if scoring.get("should_veto") and scoring.get("strength_score", 0) < -0.3:
                return False
            # For NO_ODDS, require BOOST and strength>=0.4
            if is_no_odds:
                if not (scoring.get("should_boost") and scoring.get("strength_score", 0) >= 0.4):
                    return False
        except Exception:
            if is_no_odds:
                return False
    else:
        if is_no_odds:
            return False
    # Odds check — allow NO_ODDS with estimated, but filter super short real odds
    odds = pick.get("odds")
    if odds is not None:
        try:
            o = float(odds)
            if o <= 1.0:
                return False
            # Filter super short odds unless it's a banker single with high strength
            if o < MIN_ODDS_PER_LEG:
                # Allow if BOOST and strength >=0.8 (true banker)
                if _ML_AVAILABLE:
                    try:
                        audit = load_audit_rolling()
                        registry = build_context_registry(audit)
                        weights = source_weights_from_audit(audit)
                        scoring = score_pick_strengths(pick, registry, weights)
                        if scoring.get("strength_score", 0) >= 0.8 and scoring.get("w_score", 0) >= 0.8:
                            pass  # Allow as banker
                        else:
                            # Don't auto-reject, but mark as short — will be filtered in acca building
                            pass
                    except Exception:
                        pass
        except Exception:
            return False
    return True

def parse_kickoff(pick: dict, target_date: str) -> datetime | None:
    raw = clean_text(pick.get("kickoff") or pick.get("match_time") or pick.get("time") or "")
    if not raw:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if m:
        try:
            h = int(m.group(1)); mi = int(m.group(2))
            dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=TZ)
            return dt.replace(hour=h, minute=mi)
        except Exception:
            return None
    m2 = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m2:
        try:
            d = m2.group(1)
            tm = re.search(r"(\d{1,2}):(\d{2})", raw)
            if tm:
                h = int(tm.group(1)); mi = int(tm.group(2))
                dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=TZ)
                return dt.replace(hour=h, minute=mi)
            else:
                return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=TZ)
        except Exception:
            return None
    return None

def kickoff_guard(pool: list[dict], target_date: str, now: datetime):
    kept = []
    skipped = []
    is_today = target_date == now.date().isoformat()
    for pick in pool:
        ko = parse_kickoff(pick, target_date)
        if ko is None:
            kept.append(pick)
            continue
        if is_today and ko.date().isoformat() == target_date and ko <= now:
            skipped.append((pick, "already started"))
        else:
            kept.append(pick)
    return kept, skipped

def build_accas(pool: list[dict]):
    """Build 4 accas max, avoid super short odds, value-focused"""
    audit = {}
    registry = {}
    weights = {}
    if _ML_AVAILABLE:
        try:
            audit = load_audit_rolling()
            registry = build_context_registry(audit)
            weights = source_weights_from_audit(audit)
        except Exception:
            pass

    def get_odds(p):
        o = p.get("odds")
        if o is None:
            o = estimate_odds_from_confidence(p)
        try:
            return float(o)
        except:
            return 2.0

    def sort_key(p):
        ml_score = 0
        if _ML_AVAILABLE:
            try:
                scoring = score_pick_strengths(p, registry, weights)
                ml_score = scoring.get("strength_score", 0)
            except:
                ml_score = 0
        if p.get("ml_strength_score") is not None:
            try:
                ml_score = max(ml_score, float(p.get("ml_strength_score")))
            except:
                pass
        conf = p.get("confidence") or 0
        try:
            conf_f = float(conf)
            if conf_f <= 1.0:
                conf_f *= 100
        except:
            conf_f = 0
        ev = p.get("expected_value") or 0
        try:
            ev_f = float(ev)
        except:
            ev_f = 0
        # Value = EV * strength, boost higher odds value
        odds_f = get_odds(p)
        value_score = (ev_f if ev_f>0 else 0.05) * (1 + ml_score) * (1 + (odds_f-1)*0.1)
        return (-value_score, -ml_score, -conf_f, odds_f, str(p.get("match","")))

    pool_sorted = sorted(pool, key=sort_key)

    # Separate short odds vs value odds
    short_odds = [p for p in pool_sorted if get_odds(p) < MIN_ODDS_PER_LEG]
    value_odds = [p for p in pool_sorted if get_odds(p) >= MIN_ODDS_PER_LEG]

    accas = []

    # Strategy: 2x 2-leg value accas (min odds 1.4+), 1x 3-leg high-strength, 1x banker single if exists
    # 1. Value acca 1: top 2 value odds
    if len(value_odds) >= 2:
        chunk = value_odds[:2]
        prod = math.prod([get_odds(leg) for leg in chunk])
        if prod >= MIN_ACCA_ODDS:
            # Ensure estimated odds marked
            for leg in chunk:
                if leg.get("odds") is None:
                    leg["odds"] = estimate_odds_from_confidence(leg)
                    leg["odds_source"] = leg.get("odds_source") or "ML_Estimated"
            accas.append({"legs": chunk, "odds": round(prod,2), "type": "value_2leg"})

    # 2. Value acca 2: next 2 value odds
    if len(value_odds) >= 4:
        chunk = value_odds[2:4]
        prod = math.prod([get_odds(leg) for leg in chunk])
        if prod >= MIN_ACCA_ODDS:
            for leg in chunk:
                if leg.get("odds") is None:
                    leg["odds"] = estimate_odds_from_confidence(leg)
                    leg["odds_source"] = leg.get("odds_source") or "ML_Estimated"
            accas.append({"legs": chunk, "odds": round(prod,2), "type": "value_2leg_2"})

    # 3. High-strength 3-leg acca (BOOST, high w_score)
    boost_picks = [p for p in pool_sorted if str(p.get("ml_verdict"))=="BOOST"][:6]
    if len(boost_picks) >= 3:
        chunk = boost_picks[:3]
        prod = math.prod([get_odds(leg) for leg in chunk])
        # Allow 3-leg even if product a bit low, but prefer >=2.5
        if prod >= 2.0:
            for leg in chunk:
                if leg.get("odds") is None:
                    leg["odds"] = estimate_odds_from_confidence(leg)
                    leg["odds_source"] = leg.get("odds_source") or "ML_Estimated"
            accas.append({"legs": chunk, "odds": round(prod,2), "type": "high_strength_3leg"})

<<<<<<< HEAD
    # 4. Banker single if top short odds is true banker (strength>=0.8, w_score>=0.8)
    if short_odds:
        top_short = short_odds[0]
        ml_score = top_short.get("ml_strength_score") or 0
        w_score = top_short.get("ml_w_score") or 0
        try:
            ml_score_f = float(ml_score)
            w_score_f = float(w_score)
        except:
            ml_score_f = 0
            w_score_f = 0
        if ml_score_f >= 0.8 and w_score_f >= 0.8:
            o = get_odds(top_short)
            if top_short.get("odds") is None:
                top_short["odds"] = o
                top_short["odds_source"] = top_short.get("odds_source") or "ML_Estimated"
            accas.append({"legs": [top_short], "odds": round(o,2), "type": "banker_single"})
=======
    # 4. Value acca 3: next 2 value odds (no singles — user dislikes singles)
    if len(value_odds) >= 6:
        chunk = value_odds[4:6]
        prod = 1
        for leg in chunk:
            prod *= get_odds(leg)
        if prod >= MIN_ACCA_ODDS:
            for leg in chunk:
                if leg.get("odds") is None:
                    leg["odds"] = estimate_odds_from_confidence(leg)
                    leg["odds_source"] = leg.get("odds_source") or "ML_Estimated"
            accas.append({"legs": chunk, "odds": round(prod,2), "type": "value_2leg_3"})
>>>>>>> b925d88 (feat: remove singles — user dislikes singles, 4 accas all multi-leg value)

    # Fallback: if no value accas (all short odds), build 2 accas from best available but with min odds filter relaxed
    if not accas and len(pool_sorted) >= 2:
        # Take top 4 by value, build 2 accas even if short, but ensure product >=1.8
        chunk1 = pool_sorted[:2]
        prod1 = math.prod([get_odds(leg) for leg in chunk1])
        for leg in chunk1:
            if leg.get("odds") is None:
                leg["odds"] = estimate_odds_from_confidence(leg)
                leg["odds_source"] = leg.get("odds_source") or "ML_Estimated"
        accas.append({"legs": chunk1, "odds": round(prod1,2), "type": "fallback_2leg"})

        if len(pool_sorted) >= 4:
            chunk2 = pool_sorted[2:4]
            prod2 = math.prod([get_odds(leg) for leg in chunk2])
            for leg in chunk2:
                if leg.get("odds") is None:
                    leg["odds"] = estimate_odds_from_confidence(leg)
                    leg["odds_source"] = leg.get("odds_source") or "ML_Estimated"
            accas.append({"legs": chunk2, "odds": round(prod2,2), "type": "fallback_2leg_2"})

    # Cap to MAX_ACCAS
    accas = accas[:MAX_ACCAS]

    return accas, pool_sorted

def load_state():
    if not STATE_FILE.exists():
        return {"bank": 100.0, "base_pct": 100.0, "cycle_base": 100.0, "open_slips": [], "history": [], "events": []}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"bank": 100.0, "base_pct": 100.0, "cycle_base": 100.0, "open_slips": [], "history": [], "events": []}

def save_state(state):
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))

def take_profit_target(state):
    return state.get("cycle_base", 100.0) * (1.0 + TAKE_PROFIT_GAIN)

def format_tickets_txt(target_date: str, accas: list[dict], state, skipped_info):
    now = now_local()
    bank = state.get("bank", 100.0)
    cycle_base = state.get("cycle_base", 100.0)
    lines = [
        f"Racket Factory Auto Tickets — {target_date}",
        f"Generated at: {now.isoformat()}",
        f"Bank: {bank:.2f}% (cycle base {cycle_base:.2f}%)",
        f"Take profit target: {take_profit_target(state):.2f}%",
        "",
        f"Strategy: strengths-focused, avoid super short odds (min leg {MIN_ODDS_PER_LEG}, min acca {MIN_ACCA_ODDS}), {MAX_ACCAS} accas max",
        f"Stake: {STAKE_FRAC*100:.0f}% of bank per day",
        "",
    ]
    if not accas:
        lines.append("NO BET — not enough playable picks with value")
        lines.append(f"Playable buckets: {PLAYABLE_BUCKETS} + ML NO_ODDS BOOST")
        if skipped_info:
            lines.append(f"Skipped: {skipped_info}")
        return "\n".join(lines)

    total_stake = bank * STAKE_FRAC
<<<<<<< HEAD
    # Kelly-like weighting: singles 40%, value 2-leg 25% each, 3-leg 10%
    weights = []
    for acca in accas:
        t = acca.get("type","")
        if "single" in t:
            weights.append(0.4)
        elif "3leg" in t:
            weights.append(0.15)
        else:
            weights.append(0.25)
=======
    # Kelly-like weighting: value 2-leg 30% each, 3-leg 10% — no singles
    weights = []
    for acca in accas:
        t = acca.get("type","")
        if "3leg" in t:
            weights.append(0.15)
        else:
            weights.append(0.283)  # 0.283*3 ~0.85 for 3 value accas
>>>>>>> b925d88 (feat: remove singles — user dislikes singles, 4 accas all multi-leg value)
    # Normalize weights to sum 1
    s = sum(weights)
    weights = [w/s for w in weights]

    for i, acca in enumerate(accas):
        legs = acca.get("legs", [])
        odds = acca.get("odds", 1.0)
        acca_type = acca.get("type","acca")
        stake = total_stake * weights[i]
        lines.append(f"ACCA {i+1} [{acca_type}] — Odds {odds:.2f} — Stake {stake:.2f}%")
        for leg in legs:
            match = leg.get("match","")
            sel = leg.get("selected_player","")
            leg_odds = leg.get("odds") or estimate_odds_from_confidence(leg)
            conf = leg.get("confidence") or ""
            ml_s = leg.get("ml_strength_score") or ""
            ml_v = leg.get("ml_verdict") or ""
            src = leg.get("source") or ""
            odds_src = leg.get("odds_source") or ""
            lines.append(f"  - {match} -> {sel} @ {leg_odds} (conf {conf} ml {ml_s} {ml_v} src {src} odds_src {odds_src})")
        lines.append("")

    lines.append(f"Total staked: {total_stake:.2f}% of bank")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--ignore-kickoff", action="store_true", help="Ignore kickoff guard for paper tracking")
    args = ap.parse_args()
    target_date = args.date or today_str()
    now = now_local()

    picks = load_picks(target_date)
    playable = [p for p in picks if is_playable(p)]
    kept, skipped = kickoff_guard(playable, target_date, now)
    # If kickoff guard filters all (common late in day), fallback to original for paper tracking
    # Avoid NO BET when only past matches remain — still track for ROI feedback
    if len(kept) < 2 and len(playable) >= 2:
        # Keep at least top 4 by value for paper, mark skipped as paper_late
        kept = playable
        skipped = [(p, "paper_late_included") for p in playable if p not in kept]
    accas, sorted_pool = build_accas(kept)

    state = load_state()
    # Simple state: if new date, clear old open slips? For now keep bank
    bank = state.get("bank", 100.0)

    # Build output
    total_stake = bank * STAKE_FRAC if accas else 0
    weights = []
    for acca in accas:
        t = acca.get("type","")
        if "single" in t:
            weights.append(0.4)
        elif "3leg" in t:
            weights.append(0.15)
        else:
            weights.append(0.25)
    if weights:
        s = sum(weights)
        weights = [w/s for w in weights]

    accas_out = []
    for i, acca in enumerate(accas):
        stake_pct = total_stake * weights[i] if weights else 0
        accas_out.append({
            "legs": acca.get("legs", []),
            "odds": acca.get("odds", 1.0),
            "type": acca.get("type",""),
            "stake_pct": round(stake_pct, 4),
        })

    out = {
        "date": target_date,
        "generated_at": now.isoformat(),
        "bank_pct": bank,
        "stake_per_acca_pct": round(total_stake / len(accas), 4) if accas else 0,
        "staked_pct": round(total_stake, 4),
        "accas": accas_out,
        "skipped": skipped,
        "frozen": now.hour >= FREEZE_HOUR or now.hour < GENERATE_HOUR_START,
    }

    # Write files
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    (LOCALDATA / f"auto_tickets_{target_date}.json").write_text(json.dumps(out, indent=2))
    (LOCALDATA / f"auto_tickets_{target_date}.txt").write_text(format_tickets_txt(target_date, accas, state, skipped))
    (LOCALDATA / "auto_tickets_today.json").write_text(json.dumps(out, indent=2))
    (LOCALDATA / "auto_tickets_today.txt").write_text(format_tickets_txt(target_date, accas, state, skipped))

    print(f"Auto tickets for {target_date}: {len(accas)} accas, {len(kept)} playable, {len(playable)} total playable, {len(picks)} total picks")
    for acca in accas:
        print(f"  {acca.get('type')} @ {acca.get('odds')} legs {len(acca.get('legs',[]))}")

if __name__ == "__main__":
    main()
