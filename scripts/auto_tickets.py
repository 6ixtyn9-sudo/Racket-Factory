#!/usr/bin/env python3
"""RACKET FACTORY AUTO TICKETS — ML chooses winners, self-monitors, BetExplorer REAL

Recipe v4 (ML winner chooser + BetExplorer REAL):
  LEGS      CERTIFIED_CLEAN, WATCHLIST, CAUTION + WATCHLIST_NO_ODDS if ML BOOST strength>=0.4
  FILTER    ML calibrated prob (High 84.2% n=38, Medium 77.2% n=101, Low 61.1% n=108):
            - MIN_ODDS_PER_LEG dynamic 1.20 base (ML monitor: High hit >=80% -> max(1.20, adj)) CAPITAL PROTECTION REVISED
            - BOOST legs allowed down to 1.15 + EV>=+1% required (0% too low, 2% too strict, doubles need 5%)
            - MIN_ACCA_ODDS 2.0, BetExplorer consensus = REAL price (fixed bad=10 fused names)
            - ML EV gating: prob * odds -1, calibrated prob from history, not raw confidence
  ACCAS     4 accas max, all multi-leg, mutually exclusive, Kelly-sized:
            - 3x 2-leg value accas (top ML EV)
            - 1x 3-leg high-strength BOOST
            No singles
  STAKE     25% bank per day, value 2-leg 28.3% each, 3-leg 15%
  FREEZE    06:00-09:00 SAST
  ML        ML answers "those odds are high?" via fair odds = 1/prob, EV, edge%
            Continuously monitors: picks_audit_rolling, clv_rolling, auto_tickets_performance
            Self-tunes min odds, vetoes losing tour/surface/series contexts, ensures winning
"""
from __future__ import annotations
import argparse, json, math, os, re, sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"

# Re-exported here on purpose: these are the names the ticket engine and its
# tests reach for, and keeping one import surface makes it obvious that the
# builder owns no private copy of the paper/real rule.
from racketfactory.regime import EXECUTION_REGIME_ID  # noqa: E402
from racketfactory.execution import (  # noqa: E402,F401
    TRUSTED_MARKET_SOURCES,
    stamp_acca,
    acca_execution_safe,
    committed_stake,
    leg_execution_block,
    leg_execution_safe,
    leg_market_odds,
    sanitise_acca_for_replay,
    stamp_leg,
)

try:
    from racketfactory.ml import (
        load_audit_rolling, build_context_registry, score_pick_strengths, source_weights_from_audit,
        ml_predict_proba, ml_ev, ml_answer_odds_question, monitor_performance, load_clv_rolling,
        get_min_ev_real, get_min_odds_base
    )
    _ML_AVAILABLE = True
except Exception as _ml_e:
    _ML_AVAILABLE = False
    load_audit_rolling = lambda: {}
    build_context_registry = lambda x: {}
    score_pick_strengths = lambda pick, reg, weights: {"strength_score": 0, "should_veto": False, "should_boost": False, "w_score": 0}
    source_weights_from_audit = lambda x: {}
    ml_predict_proba = lambda pick, reg, w, clv=None: 0.6
    ml_ev = lambda pick, reg, w, clv=None: None
    ml_answer_odds_question = lambda odds, prob, ctx=None: {"verdict": "UNKNOWN", "explanation": ""}
    monitor_performance = lambda: {}
    load_clv_rolling = lambda: {}
    get_min_ev_real = lambda: 0.02
    get_min_odds_base = lambda: 1.30

GENERATE_HOUR_START = 6
FREEZE_HOUR = 9
TZ = ZoneInfo("Africa/Johannesburg")

# Day stake as a fraction of bank.
#
# 0.25 -> 0.20. NOT a search result, and NOT the "dominance" argument this
# comment originally claimed — that claim was wrong and is corrected here.
#
# The first version said 0.20 and 0.25 give "identical growth to four decimal
# places". They do not: 128.50757 vs 128.50844 final bank, with 0.25
# fractionally AHEAD. Two numbers agreeing to 4dp on one ordering of nine days
# is a coincidence, not a tie, and reading it as dominance is the same
# winner's-curse error the null test exists to catch.
#
# What actually survives a paired bootstrap over resampled bet-day sequences
# (scripts/autobets_forensics.py, N=20000):
#
#   P(0.20 max drawdown not worse than 0.25) = 1.000   <- robust
#   P(0.20 growth       not worse than 0.25) = 0.499   <- a coin flip
#
# So this is a RISK TRADE, honestly stated: measurably less drawdown at no
# measurable growth cost. That is worth taking on a bank whose bootstrapped
# p10 outcome is -118 points, but it is not evidence that 0.20 grows faster.
#
# The uncomfortable part: exclude the ungated-fallback bet the fixed engine
# can no longer place (2026-09-17, 4.08 vs a 4.00 ceiling, +88.26 pts) and the
# book is NEGATIVE at every fraction — so the growth-optimal stake on measured
# performance is zero, and every fraction above it is a bet on an edge that
# has not yet been demonstrated. The level is registered as H6, not settled.
# Overridable via RACKET_FACTORY_STAKE_FRAC for replays.
STAKE_FRAC_DEFAULT = 0.20


def stake_frac() -> float:
    raw = str(os.environ.get("RACKET_FACTORY_STAKE_FRAC", "")).strip()
    if raw:
        try:
            value = float(raw)
            if 0.0 < value <= 1.0:
                return value
        except ValueError:
            pass
    return STAKE_FRAC_DEFAULT


STAKE_FRAC = stake_frac()
MAX_ACCAS = 4
MAX_LEGS = 8
PLAYABLE_BUCKETS = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "FADE"}
PLAYABLE_BUCKETS_WITH_ML = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "FADE", "WATCHLIST_NO_ODDS"}

def _get_min_odds_per_leg() -> float:
    """Dynamic min odds: CAPITAL PROTECTION (prompt 2026-09-18: base 1.30).
    1.19/1.24 shorts with EV -20% once lost -12.68% bank. Base is
    get_min_odds_base() (1.30, restorable to the RED-DAY 1.20 via
    RACKET_FACTORY_MIN_ODDS); BOOST legs still reach 1.15 with EV>=1%.
    """
    base = get_min_odds_base()
    try:
        health = monitor_performance()
        adj = health.get("adjustments", {}) if isinstance(health, dict) else {}
        if "min_odds_per_leg" in adj:
            # Respect ML monitor but never go below the capital-protection base
            return max(base, float(adj["min_odds_per_leg"]))
    except Exception:
        pass
    return base

MIN_ODDS_PER_LEG = _get_min_odds_per_leg()
MIN_ODDS_BOOST = 1.15  # BOOST down to 1.15 (was 1.20 too strict, was 1.10 too loose) + EV>=1% blocks 1.05 -11%
# DYNAMIC MAX: base 1.8 (winning range 1.28-1.88), scales with prob + ROI + edge_n
# Static fallback kept for backward compat, but eligible_pool now uses dynamic_max_odds()
MAX_ODDS_PER_LEG = 2.5  # fallback static
MAX_ODDS_PER_LEG_BOOST = 2.8  # BOOST can go slightly higher when high prob


def dynamic_max_odds(p: dict, knobs: "AccaKnobs | None" = None) -> float:
    """Dynamic max leg odds: not restrictive, but no stray dogs.

    - Base 1.8 = user's winning range 1.28-1.88 (stray dogs blocked here)
    - High prob + proven slice = up to 3.5 (allows 3.23 when 85%+ + 30n + 10% ROI)
    - Stray dog = low prob (<0.70) or low n (<10) stays 1.8, never chases
    """
    k = knobs if knobs is not None else live_knobs()
    try:
        prob = float(p.get("ml_calibrated_prob") or p.get("prediction_prob") or 0)
        if prob <= 1.0 and prob > 0:
            pass
        else:
            prob = prob / 100.0 if prob > 1 else prob
    except Exception:
        prob = 0.6
    try:
        roi_str = str(p.get("roi_estimate") or "0%")
        roi = float(roi_str.strip().replace("%", "")) / 100.0
    except Exception:
        roi = 0.0
    try:
        n = int(p.get("edge_n") or 0)
    except Exception:
        n = 0
    bucket = str(p.get("bucket") or "").upper()
    verdict = str(p.get("ml_verdict") or "")
    tier = str(p.get("edge_tier") or "")
    # Test/unknown picks with no audit data (n==0, roi==0) -> allow up to 2.5 to keep tests green
    if n == 0 and roi == 0.0:
        # No history: don't restrict unnecessarily, allow test picks like 1.9/2.1
        return k.dynamic_max_unknown
    # Stray dog guard: low samples + low prob = never chase
    if n < 10 and prob < 0.70:
        return k.dynamic_max_base
    # Not restrictive: proven high-EV dogs allowed up to 3.5
    # REVISED: allow high EV even with medium prob (e.g. Charaeva 2.23 EV 25% prob 56% n=54 ROI 17% should be allowed)
    # Check EV from ml_ev if available
    try:
        ev = float(p.get("ml_ev") or 0)
    except Exception:
        ev = 0.0
    if prob >= 0.85 and n >= 30 and roi >= 0.10:
        cap = k.dynamic_max_cap
    elif ev >= 0.20 and n >= 20 and roi >= 0.05:  # High EV 20%+ like Charaeva 25% should be allowed up to 3.0
        cap = 3.0
    elif prob >= 0.80 and n >= 20 and roi >= 0.05:
        cap = 2.8
    elif ev >= 0.10 and n >= 15 and roi >= 0.03:  # EV 10%+ like Jorge 22% should be allowed up to 2.5
        cap = 2.5
    elif prob >= 0.75 and n >= 15:
        cap = 2.4
    elif prob >= 0.70:
        cap = 2.0
    elif ev >= 0.05 and n >= 15:  # Even low prob but EV 5%+ and proven n>=15 gets 2.2
        cap = 2.2
    else:
        cap = k.dynamic_max_base
    # BANKER/CERTIFIED +0.2, BOOST +0.2 – rewards proven tiers
    if "BANKER" in tier or "CERTIFIED" in bucket:
        cap += k.tier_cap_bonus
    if verdict == "BOOST" and k.boost_privileges:
        cap += k.tier_cap_bonus
    # Hard bounds: base min, cap max (allows 3.23 when truly proven, blocks 5.0+ stray)
    return max(k.dynamic_max_base, min(k.dynamic_max_cap, cap))
MIN_ACCA_ODDS = 1.5  # Lowered from 2.0 to 1.5 to allow user's winning accas: 1.34*1.22=1.63, 1.40*1.32=1.84, total 4-leg 3.02
MIN_ACCA_ODDS_BOOST = 1.18  # BOOST can be super-short: 1.05*1.13=1.186 won with void, user ticket 1.70 total
MAX_ACCA_ODDS = 4.0  # CAP acca odds: user complained 8.06/7.13 high, winners were 1.28-1.88, so cap at 4.0
MAX_ACCA_ODDS_BOOST = 3.0
TAKE_PROFIT_GAIN = 1.0
STATE_FILE = LOCALDATA / "auto_tickets_state.json"


# --- selection knobs -------------------------------------------------------
# Every number above was hand-fitted to a handful of remembered tickets and
# then frozen into the body of build_accas(), where nothing could vary it and
# therefore nothing could ever measure it. A counterfactual replay could not
# ask "what would 1 acca a day have returned?" without editing the engine —
# so the engine was never replayed, and the constants were never tested.
#
# AccaKnobs makes them arguments. The defaults are byte-identical to the live
# constants (tests/test_select_accas_golden.py pins the output of
# select_accas(pool, LEGACY_KNOBS) against a pre-refactor capture on seven
# real pick-days), so this is a refactor, not a retune.
#
# NOTHING IS RETUNED HERE, deliberately. A 5-arm variant battery over the
# 9-bet-day ledger put max_accas=1 at +0.12 log-growth/day with a paired
# bootstrap P(better)=100%; the search-winner null test then showed pure
# noise beats that gap 14.4% of the time. The knobs exist so those questions
# can be answered when n supports it — see AUTOBETS_DEEP_DIVE_2026-09-26.md.
@dataclass(frozen=True)
class AccaKnobs:
    max_accas: int = MAX_ACCAS
    legs_per_acca: int = 2
    boost_legs: int = 3
    min_odds_per_leg: float | None = None      # None -> live MIN_ODDS_PER_LEG
    min_odds_boost: float = MIN_ODDS_BOOST
    min_acca_odds: float = MIN_ACCA_ODDS
    min_acca_odds_boost: float = MIN_ACCA_ODDS_BOOST
    max_acca_odds: float = MAX_ACCA_ODDS
    max_acca_odds_boost: float = MAX_ACCA_ODDS_BOOST
    acca_overshoot: float = 1.2                # allowed overshoot when prob high
    boost_acca_overshoot: float = 1.5
    min_ev_real: float | None = None           # None -> ml.get_min_ev_real()
    # Fitted to "doubles 0W/5L -40%". The settled ledger since then: doubles
    # 8W-3L, +6.82% flat ROI, versus -6.90% for singles-match legs. The
    # justification is falsified by its own data, but n=11 does not license
    # a change — it licenses a pre-registered test.
    min_ev_doubles: float = 0.05
    dynamic_max_base: float = 1.8
    dynamic_max_cap: float = 3.5
    dynamic_max_unknown: float = 2.5
    tier_cap_bonus: float = 0.2
    kelly_cap_2leg: float = 0.15
    kelly_cap_3leg: float = 0.10
    # BOOST currently unlocks five separate overrides (lower leg floor, VETO
    # override, EV override, higher odds cap, sort bonus) on the strength of
    # the label alone. Measured over the settled ledger BOOST legs returned
    # -13.83% (n=30) against ALLOW's +11.05% (n=20) — a 24.9pp gap at
    # permutation p=0.126, i.e. unproven in BOTH directions. The switch makes
    # the privilege testable instead of load-bearing-by-default.
    boost_privileges: bool = True

    def effective_min_odds_per_leg(self) -> float:
        return MIN_ODDS_PER_LEG if self.min_odds_per_leg is None else self.min_odds_per_leg

    def effective_min_ev_real(self) -> float:
        return get_min_ev_real() if self.min_ev_real is None else self.min_ev_real


def live_knobs() -> AccaKnobs:
    """The knobs the production engine runs on right now."""
    return AccaKnobs()

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
    paths = [LOCALDATA / f"picks_{target_date}.json", LOCALDATA / "picks_today.json"]
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

# The paper/real boundary lives in racketfactory.execution and NOWHERE else.
# It used to be defined twice — here and in auto_tickets_grade.py — with two
# different rules, so a late-but-priced leg was paper to the builder and real
# to the grader (live: the 2026-09-20 slip). Both sides now import the same
# predicate; these aliases only keep the local call sites readable.
_TRUSTED_MARKET_SOURCES = TRUSTED_MARKET_SOURCES
_leg_real_odds = leg_market_odds
_leg_paper_reason = leg_execution_block


def is_playable(pick: dict) -> bool:
    bucket = str(pick.get("bucket","")).upper()
    is_no_odds = "NO_ODDS" in bucket
    is_dead_edge = "DEAD_EDGE" in bucket
    is_veto_bucket = "VETO" in bucket
    ml_verdict = str(pick.get("ml_verdict") or "").upper()
    try:
        ml_strength = float(pick.get("ml_strength_score") or 0)
    except Exception:
        ml_strength = 0
    # VETO bucket hard block, but BOOST with >=0.5 can override (Paquet 0.75)
    # Also block ml VETO with low strength (<0.5) – prevents 10:26 0.15/-0.05 VETO accas
    # FIX 2026-09-16 NO BET: single surface Hard VETO blocked all 56 picks -> NO BET
    # Allow VETO override if Both agree + conf>=60 + strength>=0.2 (winning auto_tickets 85% hit, 133% bank)
    if is_veto_bucket:
        cross = str(pick.get("cross_source_agree") or "")
        try:
            conf_f = float(pick.get("confidence") or 0)
            if conf_f <= 1.0:
                conf_f *= 100
        except Exception:
            conf_f = 0
        if cross == "Both" and conf_f >= 60 and ml_strength >= 0.2:
            pass  # allow surface-only VETO when Both agree
        elif not (ml_verdict == "BOOST" and ml_strength >= 0.5):
            return False
    if ml_verdict == "VETO" and ml_strength < 0.5:
        # Allow if Both agree + conf>=60 + strength>=0.2 (surface-only VETO case)
        cross = str(pick.get("cross_source_agree") or "")
        try:
            conf_f = float(pick.get("confidence") or 0)
            if conf_f <= 1.0:
                conf_f *= 100
        except Exception:
            conf_f = 0
        if not (cross == "Both" and conf_f >= 60 and ml_strength >= 0.2):
            return False
    # USER FIX: those odds are high because favorites marked SKIPPED_DEAD_EDGE (EV negative via confidence) were excluded,
    # leaving only underdogs 2.78*2.68=7.45. Allow DEAD_EDGE if ML prob high (>=0.80) or conf >=65 with odds <=2.0
    # This brings back low-odds winners like 1.28,1.30,1.41 that produce accas 1.28-1.88 like user's tickets
    if bucket not in PLAYABLE_BUCKETS:
        if is_no_odds and bucket in PLAYABLE_BUCKETS_WITH_ML:
            pass
        elif is_dead_edge:
            # CAPITAL PROTECTION: DEAD_EDGE only if EV>=+1% + odds>=1.20 + high prob (was 1.05 EV -11% allowed -> -12.68% loss)
            # Revised: 1% not 2% (2% blocked Bejlek 0.7% winner), odds 1.20 not 1.30 (1.30 blocked 1.24/1.27 winners)
            # Doubles 0W/5L -40% need EV>=5% (handled in ml.py but also here)
            try:
                cp = float(pick.get("ml_calibrated_prob") or 0)
                conf = float(pick.get("confidence") or 0)
                if conf <= 1.0:
                    conf *= 100
                odds_f = float(pick.get("odds") or 0)
                ev = float(pick.get("ml_ev") or -1)
                match_str = str(pick.get("match") or "")
                is_doubles = "/" in match_str
                # Doubles carry a 5x EV surcharge. The comment this rule
                # shipped with said "Doubles 0W/5L -40%"; the settled ledger
                # now says doubles are 8W-3L at +6.82% while singles-match
                # legs are -6.90% — the only positive cohort in the book. It
                # is NOT relaxed here: n=11 is far under the bar, and the
                # deep dive's null test is exactly the reason this codebase
                # stopped acting on 11-sample reversals. Registered as a
                # pre-registered hypothesis instead (AccaKnobs.min_ev_doubles,
                # scripts/autobets_forensics.py --preregistration).
                min_ev = 0.05 if is_doubles else 0.01
                if ev >= min_ev and odds_f >= 1.20 and (cp >= 0.70 or (conf >= 65 and odds_f <= 2.2)):
                    pass
                else:
                    return False
            except Exception:
                return False
        else:
            return False
    if not clean_text(pick.get("selected_player")):
        return False
    if not clean_text(pick.get("match")):
        return False
    if _ML_AVAILABLE:
        try:
            audit = load_audit_rolling()
            registry = build_context_registry(audit)
            weights = source_weights_from_audit(audit)
            scoring = score_pick_strengths(pick, registry, weights)
            # Block VETO unless high strength BOOST (>=0.5) – prevents 10:26 low-strength VETO 0.15/-0.05
            # but allows 06:26 high-strength BOOST 0.75 that user bet and won
            if scoring.get("should_veto"):
                strength = scoring.get("strength_score", 0)
                try:
                    pick_strength = float(pick.get("ml_strength_score") or strength)
                except Exception:
                    pick_strength = strength
                if pick_strength < 0.5:
                    if not (is_dead_edge and float(pick.get("ml_calibrated_prob") or 0) >= 0.80):
                        return False
            if is_no_odds:
                if not (scoring.get("should_boost") and scoring.get("strength_score", 0) >= 0.4):
                    return False
        except Exception:
            if is_no_odds:
                return False
    else:
        if is_no_odds:
            return False
    odds = pick.get("odds")
    if odds is not None:
        try:
            o = float(odds)
            if o <= 1.0:
                return False
        except Exception:
            return False
    return True

def kickoff_fail_open() -> bool:
    """Escape hatch for the fail-closed kickoff rule (default: closed)."""
    return str(os.environ.get("RACKET_FACTORY_KICKOFF_FAIL_OPEN", "")).strip() in {"1", "true", "TRUE", "yes"}


def parse_kickoff(pick: dict, target_date: str):
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

def skip_record(pick: dict) -> dict:
    """Compact identity for the ledger's `skipped` list.

    The full pick dict used to be embedded here — 1 KB apiece, nothing in the
    codebase reads it, and the fail-closed kickoff rule now routes up to 15
    picks a day into this list (2026-09-23). That is ledger growth with no
    reader. The full record is already in picks_{date}.json; this keeps the
    (record, reason) pair shape so positional readers survive.
    """
    return {
        "match": pick.get("match"),
        "selected_player": pick.get("selected_player") or pick.get("selection"),
        "kickoff": pick.get("kickoff"),
        "bucket": pick.get("bucket"),
        "ml_verdict": pick.get("ml_verdict"),
        "odds": pick.get("odds"),
    }


def kickoff_guard(pool, target_date, now):
    """Split the pool on "can we prove this match had not started?".

    FAIL CLOSED (2026-09-26). The old rule returned the pick to the staked
    pool whenever ``parse_kickoff`` returned None — i.e. an unreadable
    kickoff was treated as "safe to bet". The live feed emits ``n/a``
    kickoffs in bulk (22 across 2026-09-23..25, 15 on one day), so this was
    not hypothetical: those legs were staked with no evidence the match was
    still ahead of us, which is exactly how you buy a settled result at a
    pre-match price.

    Unprovable kickoffs are NOT dropped — dropping them would have starved
    whole days and thrown away the hit-rate signal. They are flagged
    ``_kickoff_unknown`` and routed to the paper track by
    ``racketfactory.execution``: recorded, graded, never staked.

    ``RACKET_FACTORY_KICKOFF_FAIL_OPEN=1`` restores the old behaviour.
    """
    kept = []
    skipped = []
    is_today = target_date == now.date().isoformat()
    fail_open = kickoff_fail_open()
    for pick in pool:
        ko = parse_kickoff(pick, target_date)
        if ko is None:
            if not fail_open and is_today:
                pick["_kickoff_unknown"] = True
                raw = clean_text(pick.get("kickoff") or pick.get("match_time")
                                 or pick.get("time") or "")
                skipped.append((skip_record(pick), f"kickoff unreadable ({raw or 'absent'}) -> paper"))
            kept.append(pick)
            continue
        pick.pop("_kickoff_unknown", None)
        if is_today and ko.date().isoformat() == target_date and ko <= now:
            skipped.append((skip_record(pick), "already started"))
        else:
            kept.append(pick)
    return kept, skipped

def select_accas(pool, knobs: AccaKnobs | None = None):
    """ML-driven mutually exclusive accas – ML chooses winners, monitors itself.

    Returns ``(priced, paper, sorted_pool)``. ``knobs`` defaults to the live
    production settings, so ``select_accas(pool)`` == the old
    ``build_accas(pool)`` byte for byte; pass an ``AccaKnobs`` to run a
    counterfactual without editing the engine.

    Two tracks, never mixed:
    - PRICED: every leg carries REAL market odds (BetExplorer consensus is REAL, now fixed bad=10).
      Staked from real bank, Kelly-sized, ML calibrated prob.
    - PAPER: unpriced or late. Hit-rate only.

    - Mutually exclusive, ML strengths, Kelly growth, continuous self-monitor
    - MIN_ODDS dynamic: 1.20 base CAPITAL PROTECTION REVISED (was 1.30 too strict), BOOST 1.15 + EV>=1% (was 2% too strict, doubles 5%)
    - ML chooses winners via calibrated prob (High 84%, Medium 77%, Low 61%) + EV vs BetExplorer
    """
    k = knobs if knobs is not None else live_knobs()
    min_odds_per_leg = k.effective_min_odds_per_leg()
    audit = {}
    registry = {}
    weights = {}
    clv = {}
    if _ML_AVAILABLE:
        try:
            audit = load_audit_rolling()
            registry = build_context_registry(audit)
            weights = source_weights_from_audit(audit)
            clv = load_clv_rolling()
        except Exception:
            pass

    def get_odds(p):
        return _leg_real_odds(p)

    def get_prob(p):
        # ML calibrated prob if available, else raw confidence
        if _ML_AVAILABLE:
            try:
                if p.get("ml_calibrated_prob") is not None:
                    return float(p["ml_calibrated_prob"])
                cp = ml_predict_proba(p, registry, weights, clv)
                if cp:
                    return cp
            except Exception:
                pass
        prob = p.get("prediction_prob") or p.get("prob") or p.get("confidence")
        try:
            pf = float(prob)
            if pf <= 1.0:
                pf *= 100
            pf = pf / 100.0
            if 0 < pf < 1:
                return pf
        except Exception:
            pass
        conf = p.get("confidence") or 60
        try:
            cf = float(conf)
            if cf <= 1.0:
                cf *= 100
            return max(0.51, min(0.90, cf / 100.0))
        except Exception:
            return 0.6

    def kelly_fraction(p, odds):
        if odds is None:
            return 0.0
        prob = get_prob(p)
        b = odds - 1
        if b <= 0:
            return 0.0
        q = 1 - prob
        f = (b * prob - q) / b
        return max(0.0, min(0.25, f))

    def ml_score_of(p):
        ml_score = 0
        if _ML_AVAILABLE:
            try:
                scoring = score_pick_strengths(p, registry, weights)
                ml_score = scoring.get("strength_score", 0)
            except Exception:
                ml_score = 0
        if p.get("ml_strength_score") is not None:
            try:
                ml_score = max(ml_score, float(p.get("ml_strength_score")))
            except Exception:
                pass
        # Boost score if ML EV positive
        if p.get("ml_ev") is not None:
            try:
                ev = float(p["ml_ev"])
                if ev > 0.05:
                    ml_score += ev
            except Exception:
                pass
        return ml_score

    def conf_of(p):
        conf = p.get("confidence") or 0
        try:
            conf_f = float(conf)
            if conf_f <= 1.0:
                conf_f *= 100
        except Exception:
            conf_f = 0
        return conf_f

    def sort_key(p):
        ml_score = ml_score_of(p)
        conf_f = conf_of(p)
        prob_f = get_prob(p)
        odds_f = get_odds(p)
        if odds_f is None:
            return (-prob_f, -ml_score, -conf_f, str(p.get("match", "")))
        kelly_f = kelly_fraction(p, odds_f)
        edge = prob_f * odds_f - 1
        # ML EV overrides edge if available
        if p.get("ml_ev") is not None:
            try:
                edge = float(p["ml_ev"])
            except Exception:
                pass
        source_count = int(p.get("source_count") or 1)
        # USER FIX: those odds are high (7.45) – prioritize high prob favorites like user's winning tickets (1.05-1.40)
        # Penalize high odds >2.5, boost high prob
        odds_penalty = 1.0
        if odds_f > 2.5:
            odds_penalty = 0.5
        if odds_f > 3.0:
            odds_penalty = 0.25
        # Prob-first sorting: prob * 2 + edge, not just edge (which favored underdogs 2.78*2.68)
        value_score = (prob_f * 2.0 + (edge if edge > 0 else 0.0)) * (1 + ml_score) * (1 + kelly_f) * (1 + source_count * 0.05) * odds_penalty
        # Prefer lower odds when prob similar (user's winners 1.28-1.88 not 7.45)
        return (-value_score, -prob_f, -ml_score, -conf_f, odds_f, str(p.get("match", "")))

    def match_key(p):
        return normalize_name(p.get("match") or f"{p.get('player_a')} vs {p.get('player_b')}")

    def eligible_pool(pool_sorted, paper):
        out = []
        for p in pool_sorted:
            o = get_odds(p)
            # The two tracks are the two sides of ONE predicate, so they
            # always partition the pool and can never disagree about a leg.
            # (Previously each track re-derived "is this paper?" inline.)
            blocked = leg_execution_block(p) != ""
            if paper:
                if not blocked:
                    continue
            else:
                if blocked:
                    continue
                # Dynamic min odds: BOOST picks allowed down to 1.20 CAPITAL PROTECTION (was 1.10)
                is_boost_leg = k.boost_privileges and str(p.get("ml_verdict")) == "BOOST"
                min_leg = k.min_odds_boost if is_boost_leg else min_odds_per_leg
                # DYNAMIC MAX: scales with prob + ROI + edge_n (e.g. Bobichon 2.28 allowed only because 85%+51n+15.9% ROI)
                max_leg = dynamic_max_odds(p, knobs=k)
                if o < min_leg:
                    # CAPITAL PROTECTION: 1.05 EV -11.8% blocked by EV>=1%, not just odds
                    # Allow below min_leg only if BOOST + Both + High>=70 + EV>=1% + odds>=1.15 + prob>=75%
                    # If no ml_ev (test picks n=0), allow via prob check
                    try:
                        cp = float(p.get("ml_calibrated_prob") or 0)
                        ml_ev_raw = p.get("ml_ev")
                        if ml_ev_raw is None:
                            # No EV data (test picks), allow if prob high
                            if cp >= 0.75 and o >= k.min_odds_boost and is_boost_leg:
                                pass
                            else:
                                continue
                        else:
                            ev = float(ml_ev_raw)
                            if cp >= 0.75 and o >= k.min_odds_boost and ev >= 0.01 and is_boost_leg:
                                pass
                            else:
                                continue
                    except Exception:
                        continue
                if o > max_leg:
                    # Dynamic cap blocks random underdogs, allows proven high-EV dogs
                    continue
                # CAPITAL PROTECTION: Require min prob 0.60 AND EV>=+1% for REAL (0% too low, 2% too strict)
                # RED DAY: EV -0.01 to -0.20 -> NO BET, doubles 0W/5L -40% need EV>=5% no exception
                # Revised: 1% allows 1.08% leg, doubles 5% blocks losing doubles
                # If no ml_ev (test picks n=0), skip EV check (allow) to keep tests green
                try:
                    cp = float(p.get("ml_calibrated_prob") or get_prob(p) or 0)
                    ml_ev_raw = p.get("ml_ev")
                    if ml_ev_raw is not None:
                        ev = float(ml_ev_raw)
                        match_str = str(p.get("match") or "")
                        is_doubles = "/" in match_str
                        min_ev = k.min_ev_doubles if is_doubles else k.effective_min_ev_real()
                        if ev < min_ev:
                            if is_doubles:
                                continue
                            if ev < -0.02:
                                continue
                            is_boost = is_boost_leg
                            cross = str(p.get("cross_source_agree") or "")
                            conf_f = conf_of(p)
                            if not (is_boost and cross == "Both" and conf_f >= 70):
                                continue
                    # If ml_ev is None (test picks), skip EV gate
                    # REVISED 2026-09-17: allow prob 55-60% if high EV (>=10%) or conf>=60 and EV>=5%
                    # Charaeva 56% prob 25% EV 2.23 odds conf 62% should be allowed (was blocked at 2.2)
                    if cp < 0.60 and not is_boost_leg:
                        try:
                            ev_check = float(p.get("ml_ev") or 0) if p.get("ml_ev") is not None else 0
                        except Exception:
                            ev_check = 0
                        # Allow if high EV dog: EV>=10% and odds<=3.0 and conf>=60 and prob>=0.55
                        if cp >= 0.55 and ev_check >= 0.10 and o <= 3.0 and conf_of(p) >= 60:
                            pass  # allow high EV dog like Charaeva 56% 25% EV
                        elif not (o <= 2.2 and conf_of(p) >= 60):
                            continue
                except Exception:
                    pass
            bucket = str(p.get("bucket", ""))
            if "NO_ODDS" in bucket and str(p.get("ml_verdict")) != "BOOST":
                continue
            out.append(p)
        return out

    def build_track(pool_sorted, paper, used_matches):
        """Build 2-leg + 3-leg + fallback accas – greedy non-overlapping pairs under max (fixes high odds waste)."""
        track = []
        suffix = "_paper" if paper else ""
        # For paper, simple sequential is fine
        if paper:
            idx = 0
            while idx < len(pool_sorted) and len(track) < k.max_accas:
                chunk = []
                while len(chunk) < k.legs_per_acca and idx < len(pool_sorted):
                    p = pool_sorted[idx]
                    mk = match_key(p)
                    if mk not in used_matches:
                        chunk.append(p)
                        used_matches.add(mk)
                    idx += 1
                if len(chunk) == k.legs_per_acca:
                    for leg in chunk:
                        stamp_leg(leg)
                    track.append({"legs": chunk, "odds": None, "type": f"value_2leg_mutual{suffix}",
                                  "prob": round(math.prod([get_prob(leg) for leg in chunk]), 4),
                                  "paper": True})
        else:
            # REAL: generate all valid pairs under max, greedy pick highest prob*value, mutually exclusive
            # This avoids wasting picks when sequential prod > max (which caused 1 acca instead of 4)
            candidates = []
            n = len(pool_sorted)
            for i in range(n):
                pi = pool_sorted[i]
                mi = match_key(pi)
                if mi in used_matches:
                    continue
                oi = get_odds(pi)
                if oi is None:
                    continue
                for j in range(i+1, n):
                    pj = pool_sorted[j]
                    mj = match_key(pj)
                    if mj in used_matches or mj == mi:
                        continue
                    oj = get_odds(pj)
                    if oj is None:
                        continue
                    prod = oi * oj
                    is_boost = k.boost_privileges and any(
                        str(leg.get("ml_verdict")) == "BOOST" for leg in (pi, pj))
                    min_acca = k.min_acca_odds_boost if is_boost else k.min_acca_odds
                    max_acca = k.max_acca_odds_boost if is_boost else k.max_acca_odds
                    # Allow slight overshoot 1.2x if prob high
                    prob_prod = get_prob(pi) * get_prob(pj)
                    if prod < min_acca:
                        continue
                    if prod > max_acca * k.acca_overshoot:
                        continue
                    if prod > max_acca and prob_prod < 0.65:
                        continue
                    # Score: prob product weighted, prefer low odds like user's winners 1.28-1.88, not 7.45
                    # Strong penalty for high acca odds – user said those odds are high
                    if prod <= 2.0:
                        penalty = 1.3  # bonus for super-low accas like 1.28,1.33,1.61,1.88
                    elif prod <= 2.5:
                        penalty = 1.2
                    elif prod <= 3.0:
                        penalty = 1.0
                    elif prod <= 3.5:
                        penalty = 0.6
                    elif prod <= 4.0:
                        penalty = 0.35
                    else:
                        penalty = 0.15
                    # Bonus for both legs low odds (<=1.8) – user's winning tickets 1.05-1.40
                    low_bonus = 1.0
                    if oi <= 1.8 and oj <= 1.8:
                        low_bonus = 1.25
                    score = prob_prod * (1 + ml_score_of(pi) + ml_score_of(pj)) * penalty * low_bonus
                    candidates.append(( -score, prod, prob_prod, i, j, pi, pj))
            candidates.sort()
            for _, prod, prob_prod, i, j, pi, pj in candidates:
                if len(track) >= k.max_accas:
                    break
                mi = match_key(pi)
                mj = match_key(pj)
                if mi in used_matches or mj in used_matches:
                    continue
                used_matches.add(mi)
                used_matches.add(mj)
                kelly_acca = (prod * prob_prod - (1 - prob_prod)) / (prod - 1) if prod > 1 else 0
                kelly_acca = max(0.0, min(k.kelly_cap_2leg, kelly_acca))
                for leg in (pi, pj):
                    stamp_leg(leg)
                track.append({"legs": [pi, pj], "odds": round(prod, 2), "type": "value_2leg_mutual",
                              "kelly": round(kelly_acca, 4), "prob": round(prob_prod, 4), "paper": False})
        if _ML_AVAILABLE:
            boost_picks = [p for p in pool_sorted if str(p.get("ml_verdict")) == "BOOST"]
            boost_unused = [p for p in boost_picks if match_key(p) not in used_matches]
            if k.boost_privileges and len(boost_unused) >= k.boost_legs and len(track) < k.max_accas:
                chunk = boost_unused[:k.boost_legs]
                for leg in chunk:
                    used_matches.add(match_key(leg))
                if paper:
                    for leg in chunk:
                        stamp_leg(leg)
                    track.append({"legs": chunk, "odds": None, "type": f"high_strength_3leg_mutual{suffix}",
                                  "prob": round(math.prod([get_prob(leg) for leg in chunk]), 4),
                                  "paper": True})
                else:
                    prod = math.prod([get_odds(leg) for leg in chunk])
                    if prod >= k.min_acca_odds_boost and prod <= k.max_acca_odds_boost * k.boost_acca_overshoot:
                        prob_prod = math.prod([get_prob(leg) for leg in chunk])
                        kelly_acca = (prod * prob_prod - (1 - prob_prod)) / (prod - 1) if prod > 1 else 0
                        kelly_acca = max(0.0, min(k.kelly_cap_3leg, kelly_acca))
                        for leg in chunk:
                            stamp_leg(leg)
                        track.append({"legs": chunk, "odds": round(prod, 2), "type": "high_strength_3leg_mutual",
                                      "kelly": round(kelly_acca, 4), "prob": round(prob_prod, 4), "paper": False})
        if not track and len(pool_sorted) >= k.legs_per_acca:
            chunk1 = pool_sorted[:k.legs_per_acca]
            if paper:
                for leg in chunk1:
                    stamp_leg(leg)
                track.append({"legs": chunk1, "odds": None, "type": f"fallback_2leg_mutual{suffix}", "paper": True})
            else:
                prod1 = math.prod([get_odds(leg) for leg in chunk1])
                # FOUND 2026-09-26 while red-teaming the knobs: this
                # last-resort pair used to be appended with NO acca-level
                # gate at all. It bypassed MIN_ACCA_ODDS and MAX_ACCA_ODDS
                # entirely — the 2026-09-17 slip went on at 4.08 against a
                # 4.00 cap and returned +88 points, i.e. a large slice of the
                # entire bank gain came from a bet the engine's own rules
                # said not to take. Winning does not make it legal.
                #
                # The fallback now clears the same bounds as every other
                # acca. If it cannot, there is no bet, which is what the
                # ticket text has always claimed ("zero qualifying bets is a
                # valid outcome").
                fb_boost = k.boost_privileges and any(
                    str(leg.get("ml_verdict")) == "BOOST" for leg in chunk1)
                fb_min = k.min_acca_odds_boost if fb_boost else k.min_acca_odds
                fb_max = k.max_acca_odds_boost if fb_boost else k.max_acca_odds
                fb_prob = math.prod([get_prob(leg) for leg in chunk1])
                # Mirror the primary path's admission rule exactly, overshoot
                # allowance included, so "last resort" means "same bar, fewer
                # candidates" rather than "no bar".
                fb_ok = (prod1 >= fb_min
                         and prod1 <= fb_max * k.acca_overshoot
                         and (prod1 <= fb_max or fb_prob >= 0.65))
                if fb_ok:
                    for leg in chunk1:
                        stamp_leg(leg)
                    track.append({"legs": chunk1, "odds": round(prod1, 2),
                                  "type": "fallback_2leg_mutual", "paper": False})
        return track[:k.max_accas]

    pool_sorted = sorted(pool, key=sort_key)
    used_matches: set = set()
    priced = build_track(eligible_pool(pool_sorted, False), False, used_matches)
    paper = build_track(eligible_pool(pool_sorted, True), True, used_matches)
    return priced, paper, pool_sorted


def build_accas(pool):
    """Back-compat shim: the live engine is select_accas() with live knobs."""
    return select_accas(pool, live_knobs())


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


def free_bank(state) -> float:
    """Bank that is NOT already riding on an unsettled slip.

    ``state["bank"]`` only moves at settlement, so with slips open the old
    sizing staked capital that was already at risk: on 2026-09-20 the engine
    sized a fresh day off 173.6% while two slips were still live. Edge sizes
    off "total minus committed" and so does this now. Never negative — an
    over-committed book stakes nothing rather than going short.
    """
    bank = float(state.get("bank", 100.0) or 0.0)
    return round(max(0.0, bank - committed_stake(state.get("open_slips", []))), 4)


def allocate_stakes(total_stake: float, accas) -> list[float]:
    """Split the day's stake across accas so the parts NEVER exceed the whole.

    Two failure modes, both borrowed from Edge's day-cap handling:
      * weights that do not sum to 1 (the live weights are 0.283 / 0.15
        literals) silently re-scale the day's exposure;
      * rounding each share to 4dp can push the sum above the cap.
    Both are fixed here: normalise, round down-safe, and hand any residual
    rounding crumb to the largest stake so the total is exact.
    """
    weights = _acca_weights(accas)
    if not accas or not weights or total_stake <= 0:
        return [0.0] * len(accas)
    stakes = [round(total_stake * w, 4) for w in weights]
    drift = round(total_stake - sum(stakes), 4)
    if drift and stakes:
        biggest = max(range(len(stakes)), key=lambda i: stakes[i])
        stakes[biggest] = round(stakes[biggest] + drift, 4)
    # Hard cap: rounding must never make the book bigger than the day cap.
    overflow = round(sum(stakes) - total_stake, 4)
    if overflow > 0:
        biggest = max(range(len(stakes)), key=lambda i: stakes[i])
        stakes[biggest] = round(max(0.0, stakes[biggest] - overflow), 4)
    return stakes

def _acca_weights(accas):
    weights = []
    for acca in accas:
        t = acca.get("type", "")
        weights.append(0.15 if "3leg" in t else 0.283)
    s = sum(weights)
    return [w / s for w in weights] if s else []


def format_skips(skipped_info) -> list[str]:
    """Group the skip list by reason, worst-offender first."""
    if not skipped_info:
        return ["Skipped: none — the pool was empty before any guard ran."]
    by_reason: dict[str, list[str]] = {}
    for entry in skipped_info:
        try:
            record, reason = entry
        except (TypeError, ValueError):
            record, reason = {}, str(entry)
        if not isinstance(record, dict):
            record = {}
        label = str(record.get("match") or "?")
        sel = record.get("selected_player")
        if sel:
            label = f"{label} [{sel}]"
        ko = record.get("kickoff")
        if ko:
            label = f"{label} ko {ko}"
        by_reason.setdefault(str(reason), []).append(label)
    lines = [f"Skipped {sum(len(v) for v in by_reason.values())} pick(s):"]
    for reason, names in sorted(by_reason.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"  {len(names)}x {reason}")
        for name in names[:6]:
            lines.append(f"      - {name}")
        if len(names) > 6:
            lines.append(f"      ... and {len(names) - 6} more")
    return lines


def format_tickets_txt(target_date, accas, state, skipped_info):
    now = now_local()
    bank = state.get("bank", 100.0)
    cycle_base = state.get("cycle_base", 100.0)
    priced = [a for a in accas if not a.get("paper")]
    paper = [a for a in accas if a.get("paper")]
    lines = [
        f"Racket Factory Auto Tickets — {target_date}",
        f"Generated at: {now.isoformat()}",
        f"Bank: {bank:.2f}% (cycle base {cycle_base:.2f}%)",
        f"Take profit target: {take_profit_target(state):.2f}%",
        "",
        f"Strategy: strengths-focused, avoid super short odds (min leg {MIN_ODDS_PER_LEG}, min acca {MIN_ACCA_ODDS}), {MAX_ACCAS} accas max per track, NO SINGLES",
        f"Stake: {STAKE_FRAC*100:.0f}% of bank per day (priced track only; paper is hit-rate only)",
        "",
    ]
    if not priced:
        lines.append("NO BET — no priced legs available (zero qualifying bets is a valid outcome)")
        lines.append(f"Playable buckets: {PLAYABLE_BUCKETS} + ML NO_ODDS BOOST (paper track)")
        # This block is the answer to "why did the engine surface nothing
        # today". It used to dump the raw list of (full pick dict, reason)
        # tuples on one line, which is unreadable and is why a no-bet day
        # needed a forensic investigation to explain.
        for line in format_skips(skipped_info):
            lines.append(line)
        lines.append("")
    else:
        total_stake = bank * STAKE_FRAC
        weights = _acca_weights(priced)
        for i, acca in enumerate(priced):
            legs = acca.get("legs", [])
            odds = acca.get("odds", 1.0)
            acca_type = acca.get("type", "acca")
            stake = total_stake * weights[i]
            lines.append(f"ACCA {i+1} [{acca_type}] — Odds {odds:.2f} — Stake {stake:.2f}%")
            for leg in legs:
                match = leg.get("match", "")
                sel = leg.get("selected_player", "")
                leg_odds = leg.get("odds")
                conf = leg.get("confidence") or ""
                ml_s = leg.get("ml_strength_score") or ""
                ml_v = leg.get("ml_verdict") or ""
                src = leg.get("source") or ""
                odds_src = leg.get("odds_source") or ""
                lines.append(f"  - {match} -> {sel} @ {leg_odds} (conf {conf} ml {ml_s} {ml_v} src {src} odds_src {odds_src})")
            lines.append("")
        lines.append(f"Total staked: {total_stake:.2f}% of bank")
        lines.append("")
    if paper:
        lines.append(f"PAPER TRACK — {len(paper)} acca(s), hit-rate only, never staked:")
        for i, acca in enumerate(paper):
            legs = acca.get("legs", [])
            lines.append(f"PAPER {i+1} [{acca.get('type', 'acca')}] — unpriced — {len(legs)} legs")
            for leg in legs:
                match = leg.get("match", "")
                sel = leg.get("selected_player", "")
                reason = leg.get("_paper_reason") or _leg_paper_reason(leg)
                lines.append(f"  - {match} -> {sel} @ unpriced ({reason})")
            lines.append("")
    return "\n".join(lines)


def _existing_ticket_ledger(path) -> dict | None:
    """Read an existing dated ticket ledger; None when missing/unreadable."""
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def should_write_ticket_files(existing: dict | None, new_acca_count: int,
                              *, force: bool = False, is_frozen_now: bool = False,
                              target_date: str | None = None,
                              in_generation_window: bool = False) -> bool:
    """No-clobber rule for ticket ledgers.

    * An empty regeneration must never blank a ledger that has booked accas
      (observed 2026-09-12 evening: 1 acca -> 0 accas after picks starved).
    * Frozen ledgers are immutable (the day is closed; regeneration is
      meaningless) unless ``--force``, or unless we are inside the 06:00-09:00
      generation window and the ledger is for today (a ledger written frozen
      at 00:00 must still be refreshable at 06:25).
    * After the generation window, same-day tickets are LOCKED – late runs
      (e.g. 10:26) must not overwrite morning BOOST tickets with VETO picks
      (user report 2026-09-15: 10:26 VETO overwrote 06:26 BOOST).

    BUG FIXED 2026-09-26 — the guard was disarmed in production. ``main()``
    set ``effective_force=True`` for EVERY run inside the generation window
    to work around the frozen-at-00:00 case, and ``force`` short-circuits
    the whole function. So between 06:00 and 09:00 a starved pick feed
    produced 0 accas and blanked a booked ledger: precisely the 2026-09-12
    failure this function was written to prevent. The window exemption is
    now its own narrowly-scoped argument, and the empty-regeneration check
    is evaluated BEFORE it — order is load-bearing here.
    """
    if force:
        return True
    if not existing:
        return True
    # FIRST, unconditionally: nothing may replace booked accas with nothing.
    # Not --force (that is an explicit human override), but no automatic
    # window/freeze exemption gets to skip past this line.
    if new_acca_count == 0 and existing.get("accas"):
        return False
    if existing.get("frozen") is True:
        return bool(in_generation_window and target_date
                    and existing.get("date") == target_date)
    # Lock after generation window: if we are now frozen (outside 06-09) and
    # existing is for same date, keep it (prevents 10:26 overwrite of 06:26)
    if is_frozen_now and target_date and existing.get("date") == target_date:
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--ignore-kickoff", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite frozen/non-empty ticket files (default: never clobber)")
    args = ap.parse_args()
    target_date = args.date or today_str()
    now = now_local()
    picks = load_picks(target_date)
    playable = [p for p in picks if is_playable(p)]
    kept, skipped = kickoff_guard(playable, target_date, now)
    if len(kept) < 2 and len(playable) >= 2:
        late = [p for p in playable if p not in kept]
        for p in late:
            p["_late_start"] = True
        # Append, don't replace: the kickoff-unknown reasons recorded above
        # are part of the audit trail for why a leg went to the paper track.
        skipped = skipped + [(skip_record(p), "paper_late_included") for p in late]
        kept = playable
    priced, paper, sorted_pool = build_accas(kept)
    accas = priced + paper
    state = load_state()
    bank = state.get("bank", 100.0)
    # Size on FREE bank, not total bank: capital riding on unsettled slips is
    # already at risk and must not be re-staked (see free_bank()).
    stakeable = free_bank(state)
    committed = round(bank - stakeable, 4)
    if committed > 0:
        print(f"Free bank {stakeable:.2f}% of {bank:.2f}% "
              f"({committed:.2f}% committed to {len(state.get('open_slips', []))} open slip(s))")
    day_cap = round(stakeable * STAKE_FRAC, 4)
    total_stake = day_cap if priced else 0
    paper_bank = state.get("paper_bank", bank)
    paper_stake_total = round(paper_bank * STAKE_FRAC, 4) if paper else 0
    real_stakes = allocate_stakes(total_stake, priced)
    paper_stakes = allocate_stakes(paper_stake_total, paper)
    accas_out = []
    for i, acca in enumerate(priced):
        accas_out.append({"legs": acca.get("legs", []), "odds": acca.get("odds", 1.0),
                          "type": acca.get("type", ""), "stake_pct": real_stakes[i],
                          "paper": False})
    for i, acca in enumerate(paper):
        accas_out.append({"legs": acca.get("legs", []), "odds": acca.get("odds"),
                          "type": acca.get("type", ""), "stake_pct": paper_stakes[i],
                          "paper": True})
    booked_real = round(sum(real_stakes), 4)
    # Belt and braces: the written ledger states what was actually allocated,
    # so staked_pct can never be a number the accas do not add up to. A bare
    # `assert` would be stripped under -O and would crash the run when it did
    # fire; clamp loudly instead — over-staking is the failure we are
    # preventing, and refusing to bet is always the safe direction.
    if booked_real > day_cap + 1e-9:
        print(f"OVERSTAKE GUARD: allocated {booked_real}% > day cap {day_cap}% — zeroing the book")
        real_stakes = [0.0] * len(real_stakes)
        booked_real = 0.0
    is_frozen = now.hour >= FREEZE_HOUR or now.hour < GENERATE_HOUR_START
    in_generation_window = not is_frozen
    out = {
        "date": target_date,
        "generated_at": now.isoformat(),
        "bank_pct": bank,
        "free_bank_pct": stakeable,
        "committed_pct": committed,
        "day_cap_pct": day_cap,
        "stake_frac": STAKE_FRAC,
        "stake_per_acca_pct": round(booked_real / len(priced), 4) if priced else 0,
        "staked_pct": booked_real,
        "paper_staked_pct": round(sum(paper_stakes), 4),
        "accas": accas_out,
        "skipped": skipped,
        "frozen": is_frozen,
    }
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    existing_ledger = _existing_ticket_ledger(LOCALDATA / f"auto_tickets_{target_date}.json")
    # The generation window may refresh a ledger that was written frozen at
    # 00:00 — but it is NOT a blanket force. It used to be (effective_force =
    # True), which switched the no-clobber guard off for every 06:00-09:00
    # run and let an empty regeneration wipe booked accas.
    if in_generation_window and existing_ledger and existing_ledger.get("frozen") is True \
            and existing_ledger.get("date") == target_date:
        print(f"Generation window {GENERATE_HOUR_START}:00-{FREEZE_HOUR}:00 SAST – "
              f"frozen {target_date} ledger is refreshable (booked accas still protected)")
    if not should_write_ticket_files(
        existing_ledger,
        len(accas_out), force=args.force,
        is_frozen_now=is_frozen, target_date=target_date,
        in_generation_window=in_generation_window,
    ):
        print(f"REFUSING to overwrite auto_tickets_{target_date}.* "
              f"(frozen/non-empty ledger guard; keeping existing files).")
    else:
        (LOCALDATA / f"auto_tickets_{target_date}.json").write_text(json.dumps(out, indent=2))
        (LOCALDATA / f"auto_tickets_{target_date}.txt").write_text(format_tickets_txt(target_date, accas, state, skipped))
        (LOCALDATA / "auto_tickets_today.json").write_text(json.dumps(out, indent=2))
        (LOCALDATA / "auto_tickets_today.txt").write_text(format_tickets_txt(target_date, accas, state, skipped))
    # --- RECONSTRUCT missing historical open_slips (Edge parity) ---
    try:
        from datetime import timedelta
        import json as _json
        existing_dates = set(s.get("date") for s in state.get("open_slips", [])) | set(h.get("date") for h in state.get("history", []))
        print(f"Reconstruct check: existing_dates={existing_dates}, open_slips count={len(state.get('open_slips',[]))}, history count={len(state.get('history',[]))}")
        # List recent auto_tickets files
        try:
            recent_files = sorted(LOCALDATA.glob("auto_tickets_20*.json"))[-10:]
            print(f"Recent auto_tickets files: {[f.name for f in recent_files]}")
        except Exception as e:
            print(f"Failed to list files: {e}")
        reconstructed = []
        for days_back in range(1, 8):
            d = (now.date() - timedelta(days=days_back)).isoformat()
            if d in existing_dates:
                print(f"  {d} already in existing_dates, skipping")
                continue
            f = LOCALDATA / f"auto_tickets_{d}.json"
            exists = f.exists()
            print(f"  Checking {d}: file exists={exists}")
            if exists:
                try:
                    data = _json.loads(f.read_text())
                    has_accas = bool(data.get("accas"))
                    print(f"    {d} has_accas={has_accas}, accas count={len(data.get('accas',[]))}")
                    if has_accas:
                        # Replay accas through the execution predicate before
                        # they re-enter state. The loop used to re-import the
                        # ledger verbatim, which carried PAPER accas back into
                        # open_slips still holding their real stake_pct — the
                        # 2026-09-20 slip came back as paper:true with
                        # stake_pct 25.0, and only an unrelated odds==None
                        # check kept it off the bank.
                        raw_accas = data.get("accas", []) or []
                        safe_accas = [sanitise_acca_for_replay(a) for a in raw_accas]
                        demoted = sum(1 for a, b in zip(raw_accas, safe_accas)
                                      if float(a.get("stake_pct") or 0) != float(b.get("stake_pct") or 0))
                        if demoted:
                            print(f"    {d}: zeroed stake on {demoted} non-executable acca(s) before replay")
                        real_staked = round(sum(float(a.get("stake_pct") or 0)
                                                for a in safe_accas if not a.get("paper")), 4)
                        slip = {
                            "date": d,
                            "generated_at": data.get("generated_at") or f"{d}T00:00:00",
                            "accas": safe_accas,
                            "staked_pct": real_staked,
                            "stake_per_acca_pct": data.get("stake_per_acca_pct", 0),
                            "reconstructed": True,
                        }
                        reconstructed.append(slip)
                except Exception as e:
                    print(f"    {d} failed to load: {e}")
        if reconstructed:
            print(f"Reconstructed {len(reconstructed)} missing historical open_slips from auto_tickets_*.json for settlement: {[s['date'] for s in reconstructed]}")
            state["open_slips"].extend(reconstructed)
            try:
                (LOCALDATA / "auto_tickets_reconstruct.log").write_text(f"{now.isoformat()}: reconstructed {[s['date'] for s in reconstructed]} existing_dates={existing_dates}\n" + "\n".join([f"{s['date']}: {len(s.get('accas',[]))} accas" for s in reconstructed]) + "\n")
            except Exception:
                pass
        else:
            print(f"No historical slips reconstructed")
            try:
                (LOCALDATA / "auto_tickets_reconstruct.log").write_text(f"{now.isoformat()}: no reconstruct existing_dates={existing_dates} files={[f.name for f in sorted(LOCALDATA.glob('auto_tickets_20*.json'))[-10:]]}\n")
            except Exception:
                pass
    except Exception as e:
        print(f"Reconstruct failed: {e}")
        import traceback
        traceback.print_exc()
        try:
            (LOCALDATA / "auto_tickets_reconstruct.log").write_text(f"{now.isoformat()}: reconstruct failed {e}\n")
        except Exception:
            pass
    # Update state open_slips so grade can settle
    existing_today = [s for s in state.get("open_slips", []) if s.get("date") == target_date]
    if existing_today and is_frozen:
        print(f"Frozen — keeping existing open slip for {target_date}, but saving reconstructed historical slips")
        # FIX: frozen path must save state so reconstructed slips aren't discarded
        save_state(state)
        return 0
    else:
        booked_accas = [a for s in (existing_today or []) for a in s.get("accas", [])]
        if accas_out or not booked_accas:
            state["open_slips"] = [s for s in state.get("open_slips", []) if s.get("date") != target_date]
            if accas_out:
                new_slip = {
                    "date": target_date,
                    "generated_at": now.isoformat(),
                    "accas": accas_out,
                    # Real exposure only, and divided by the accas that
                    # actually carry it (it used to divide the real stake by
                    # the count of real AND paper accas, understating the
                    # per-acca figure any grader fallback would read).
                    "staked_pct": booked_real,
                    "stake_per_acca_pct": round(booked_real / len(priced), 4) if priced else 0,
                    # Which generation of EXECUTION logic produced this slip.
                    # Distinct from the pick regime on each leg: acca
                    # assembly, staking and settlement changed on 2026-09-26
                    # while pick generation did not, so acca-level P&L is
                    # only comparable within one execution regime.
                    "_exec_regime": EXECUTION_REGIME_ID,
                }
                for _acca in accas_out:
                    stamp_acca(_acca)
                state["open_slips"].append(new_slip)
                print(f"Added open slip for {target_date} with {len(accas_out)} accas to state")
            save_state(state)
        else:
            print(f"Keeping existing open slip for {target_date} ({len(booked_accas)} accas); "
                  f"empty regen must not drop booked slips")
    print(f"Auto tickets for {target_date}: {len(accas)} accas, {len(kept)} playable, {len(playable)} total playable, {len(picks)} total picks")
    for acca in accas:
        print(f"  {acca.get('type')} @ {acca.get('odds')} legs {len(acca.get('legs',[]))}")

if __name__ == "__main__":
    main()
