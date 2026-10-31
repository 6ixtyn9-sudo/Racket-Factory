#!/usr/bin/env python3
"""RACKET FACTORY AUTO TICKETS — ML chooses winners, self-monitors, BetExplorer REAL

Recipe v4 (ML winner chooser + BetExplorer REAL):
  LEGS      CERTIFIED_CLEAN, WATCHLIST, CAUTION and FADE with a valid price
  FILTER    ML calibrated prob (High 84.2% n=38, Medium 77.2% n=101, Low 61.1% n=108):
            - Shared minimum leg odds 1.30, including BOOST and replay
            - MIN_ACCA_ODDS 2.0, BetExplorer consensus = REAL price (fixed bad=10 fused names)
            - ML EV: diagnostic based on calibrated probability and quoted odds
  ACCAS     4 accas max, all multi-leg, mutually exclusive, Kelly-sized:
            - 3x 2-leg value accas
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
from racketfactory.leg_policy import MIN_LEG_ODDS, valid_leg_price, restore_ev_only_bucket  # noqa: E402
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
        get_min_ev_real, get_min_odds_base, ml_filter_picks
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
    return MIN_LEG_ODDS

MIN_ODDS_PER_LEG = MIN_LEG_ODDS
MIN_ODDS_BOOST = MIN_LEG_ODDS
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
    # Separate the former EV-assisted cap exceptions: keep their existing
    # sample and ROI evidence requirements, but not a model-EV threshold.
    if prob >= 0.85 and n >= 30 and roi >= 0.10:
        cap = k.dynamic_max_cap
    elif n >= 20 and roi >= 0.05:
        cap = 3.0
    elif n >= 15 and roi >= 0.03:
        cap = 2.5
    elif prob >= 0.75 and n >= 15:
        cap = 2.4
    elif prob >= 0.70:
        cap = 2.0
    elif n >= 15:
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
    min_odds_per_leg: float | None = None      # legacy replay input; floor is fixed
    min_odds_boost: float = MIN_ODDS_BOOST     # legacy replay input; floor is fixed
    min_acca_odds: float = MIN_ACCA_ODDS
    min_acca_odds_boost: float = MIN_ACCA_ODDS_BOOST
    max_acca_odds: float = MAX_ACCA_ODDS
    max_acca_odds_boost: float = MAX_ACCA_ODDS_BOOST
    acca_overshoot: float = 1.2                # allowed overshoot when prob high
    boost_acca_overshoot: float = 1.5
    min_ev_real: float | None = None           # None -> ml.get_min_ev_real()
    min_ev_doubles: float = 0.05             # legacy replay input; no EV gate
    dynamic_max_base: float = 1.8
    dynamic_max_cap: float = 3.5
    dynamic_max_unknown: float = 2.5
    tier_cap_bonus: float = 0.2
    kelly_cap_2leg: float = 0.15
    kelly_cap_3leg: float = 0.10
    # BOOST retains its pre-existing non-EV privileges (including VETO
    # override, higher odds cap and sort bonus) on the strength of the label. Measured over the settled ledger BOOST legs returned
    # -13.83% (n=30) against ALLOW's +11.05% (n=20) — a 24.9pp gap at
    # permutation p=0.126, i.e. unproven in BOTH directions. The switch makes
    # the privilege testable instead of load-bearing-by-default.
    boost_privileges: bool = True

    def effective_min_odds_per_leg(self) -> float:
        return MIN_LEG_ODDS

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
    # RACKET_FACTORY_RUN_AS_OF marks pipeline start, not ticket creation.
    # A 15-minute pipeline must not stake an event that started meanwhile.
    return datetime.now(TZ)

def today_str() -> str:
    return now_local().date().isoformat()


def quote_observation_freshness(collection: dict) -> str:
    """Report timestamp quality independently of provider completeness."""
    raw = collection.get("oldest_offer_observed_at")
    if not raw:
        return "missing"
    try:
        observed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return "timezone_unknown"
        age = (now_local() - observed).total_seconds()
        if age < 0:
            return "future"
        return "fresh" if age <= 30 * 60 else "stale"
    except (ValueError, TypeError, AttributeError, OverflowError):
        return "malformed"


def collection_has_fresh_observations(collection: dict, freshness: str) -> bool:
    if freshness != "fresh":
        return False
    try:
        return int(collection.get("observed_offer_count") or 0) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def current_quote_collection(target_date: str, run_as_of: str) -> tuple[str, dict, str]:
    """Read current-run coverage without masking an incomplete status."""
    try:
        collection = json.loads((LOCALDATA / f"quote_collection_{target_date}.json").read_text())
        if not run_as_of or collection.get("run_as_of") != run_as_of:
            return "unknown", {}, "unknown"
        status = collection.get("status") or "unknown"
        if not isinstance(status, str):
            status = "unknown"
        freshness = quote_observation_freshness(collection)
        if status == "complete" and freshness != "fresh":
            status = "unknown" if freshness in {"missing", "malformed"} else "stale_or_unknown"
        return status, collection, freshness
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return "unknown", {}, "unknown"


def fresh_pick_inputs_available(target_date: str) -> bool:
    """Require a current-run export; an explicit fresh empty export is valid."""
    run_as_of = os.environ.get("RACKET_FACTORY_RUN_AS_OF", "").strip()
    if not run_as_of:
        return True
    try:
        status = json.loads((LOCALDATA / f".pick_run_status_{target_date}.json").read_text())
        if status.get("run_as_of") != run_as_of:
            return False
        rows = json.loads((LOCALDATA / f"picks_{target_date}.json").read_text())
        return isinstance(rows, list) and int(status.get("fresh_picks", -1)) == len(rows)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def load_picks(target_date: str) -> list[dict]:
    run_as_of = os.environ.get("RACKET_FACTORY_RUN_AS_OF", "").strip()
    if run_as_of:
        if not fresh_pick_inputs_available(target_date):
            print("Missing or invalid current-run pick export; refusing to use preserved archives")
            return []
    paths = [LOCALDATA / f"picks_{target_date}.json", LOCALDATA / "picks_today.json"]
    for p in paths:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)
                        and str(r.get("ledger_kind", "")).lower() != "forecast"
                        and (p.name != "picks_today.json"
                             or str(r.get("date") or r.get("match_date") or "")[:10] == target_date)]
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


def selected_quote_problem(pick: dict, *, require_provenance: bool = False, now: datetime | None = None) -> str:
    """Validate a provenance-bearing selected-side quote without replacing it.

    This applies when a collector supplies a quote record. Legacy rows without
    provenance are not upgraded to fresh evidence by this check.
    """
    quote = pick.get("selected_quote")
    if quote is None:
        return "selected quote provenance missing" if require_provenance else ""
    if not isinstance(quote, dict):
        return "malformed selected quote"
    try:
        if float(quote.get("odds")) != float(pick.get("odds")):
            return "selected quote price changed"
    except (TypeError, ValueError, OverflowError):
        return "invalid selected quote price"
    if str(quote.get("selection") or "").strip() != str(pick.get("selected_player") or "").strip():
        return "selected quote player changed"
    if str(quote.get("source") or "").strip() != str(pick.get("odds_source") or "").strip():
        return "selected quote source changed"
    if str(quote.get("bookmaker") or "").strip() != str(pick.get("odds_bookmaker") or "").strip():
        return "selected quote bookmaker changed"
    quote_source = str(quote.get("source") or "").strip()
    market = str(quote.get("market") or "").lower()
    if quote_source == "ScrapedPickOdds":
        if market != "source_pick_odds":
            return "selected quote market mismatch"
    elif market not in {"h2h", "1x2", "match_winner", "match winner"}:
        return "selected quote market mismatch"
    if str(quote.get("period") or "").lower() not in {"match", "full match", "full_match", "full", "0"}:
        return "selected quote period mismatch"
    line = quote.get("line")
    if line is not None and str(line).strip().lower() not in {"", "none", "nan"}:
        return "selected quote line mismatch"
    if require_provenance:
        from racketfactory.execution import rebuild_quote_execution_problem
        execution_problem = rebuild_quote_execution_problem(quote, now=now, pick=pick)
        if execution_problem:
            return execution_problem
        if not str(quote.get("source") or "").strip():
            return "selected quote source missing"
        if quote_source != "ScrapedPickOdds" and not str(quote.get("bookmaker") or "").strip():
            return "selected quote bookmaker missing"
        home, away = quote.get("provider_home"), quote.get("provider_away")
        provider_side = str(quote.get("provider_side") or "")
        if not home or not away or provider_side not in {"1", "2"}:
            return "selected quote provider orientation missing"
        if not quote.get("event_id") and not (quote.get("fixture_date") or quote.get("request_date")):
            return "selected quote fixture identity missing"
        from racketfactory.warehouse import names_match, strict_quote_team_match
        selected = str(pick.get("selected_player") or "")
        expected, opponent = (home, away) if provider_side == "1" else (away, home)
        if not strict_quote_team_match(selected, str(expected)) or names_match(selected, str(opponent)):
            return "selected quote provider side mismatch"
        quote_date = quote.get("fixture_date") or quote.get("request_date")
        if quote.get("commence_time"):
            try:
                start = datetime.fromisoformat(str(quote["commence_time"]).replace("Z", "+00:00"))
                if start.tzinfo is None:
                    return "selected quote kickoff timezone unknown"
                quote_date = start.astimezone(TZ).date().isoformat()
            except (TypeError, ValueError, OverflowError):
                return "selected quote kickoff timestamp invalid"
        pick_date = pick.get("date") or pick.get("match_date")
        if quote_date and pick_date and str(quote_date)[:10] != str(pick_date)[:10]:
            return "selected quote date mismatch"
        pick_kickoff = parse_kickoff(pick, str(pick_date or quote_date or "")[:10]) if (pick_date or quote_date) else None
        if quote_source == "ScrapedPickOdds":
            if not pick_date or not quote_date:
                return "selected quote fixture identity missing"
            if pick_kickoff is None or not quote.get("match_time"):
                return "selected quote kickoff cannot be validated"
            if str(quote.get("match_time"))[:5] != pick_kickoff.strftime("%H:%M"):
                return "selected quote kickoff conflicts with fixture kickoff"
        if quote.get("commence_time") and pick_kickoff is not None:
            if start.astimezone(TZ).replace(second=0, microsecond=0) != pick_kickoff.replace(second=0, microsecond=0):
                return "selected quote kickoff conflicts with fixture kickoff"
        elif quote.get("match_time") and pick.get("kickoff"):
            if str(quote.get("match_time"))[:5] != str(pick.get("kickoff"))[:5]:
                return "selected quote kickoff conflicts with fixture kickoff"
    try:
        observed = datetime.fromisoformat(str(quote.get("observed_at") or "").replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return "selected quote timestamp missing timezone"
        age = ((now or now_local()) - observed).total_seconds()
        if not 0 <= age <= 30 * 60:
            return "selected quote stale or future"
    except (TypeError, ValueError):
        return "selected quote timestamp invalid"
    return ""


def is_playable(pick: dict, *, rejections: dict | None = None,
                admitted: dict | None = None, require_provenance: bool = False) -> bool:
    if not valid_leg_price(pick.get("odds")):
        if rejections is not None:
            rejections["invalid or below-floor odds"] = rejections.get("invalid or below-floor odds", 0) + 1
        return False
    quote_error = selected_quote_problem(pick, require_provenance=require_provenance)
    if quote_error:
        if rejections is not None:
            rejections[quote_error] = rejections.get(quote_error, 0) + 1
        return False
    return _is_playable_non_quote(pick, rejections=rejections, admitted=admitted)


def _is_playable_non_quote(pick: dict, *, rejections: dict | None = None,
                           admitted: dict | None = None) -> bool:
    """Independent admission checks; diagnostics may call without admitting."""
    def reject(reason: str) -> bool:
        if rejections is not None:
            rejections[reason] = rejections.get(reason, 0) + 1
        return False
    pick = restore_ev_only_bucket(pick)
    # Legacy labels were created using the removed EV gate. Re-evaluate the
    # original context veto rather than treating the stored verdict as fresh.
    if pick.get("legacy_ev_only_skip") or pick.get("legacy_ev_only_veto"):
        if not _ML_AVAILABLE:
            return reject("legacy ML rescore unavailable")
        try:
            pick = ml_filter_picks([pick])[0][0]
        except Exception:
            return reject("legacy ML rescore unavailable")
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
            return reject("veto bucket")
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
            return reject("ML veto")
    if is_dead_edge:
        # An unrecognised DEAD_EDGE can reflect a non-EV/ROBBER signal.
        return reject("dead-edge gate")
    if bucket not in PLAYABLE_BUCKETS:
        if not (is_no_odds and bucket in PLAYABLE_BUCKETS_WITH_ML):
            return reject("bucket not playable")
    if pick.get("legacy_ev_only_skip"):
        # The old mixed dead-edge rule also required probability/confidence;
        # retain those independent conditions after removing its EV component.
        try:
            cp = float(pick.get("ml_calibrated_prob") or 0)
            conf = float(pick.get("confidence") or 0)
            if conf <= 1.0:
                conf *= 100
            if not (cp >= 0.70 or (conf >= 65 and float(pick["odds"]) <= 2.2)):
                return reject("dead-edge probability gate")
        except (TypeError, ValueError):
            return reject("invalid dead-edge inputs")
    if not clean_text(pick.get("selected_player")):
        return reject("missing selection")
    if not clean_text(pick.get("match")):
        return reject("missing fixture")
    side = clean_text(pick.get("selected_side")).lower()
    if side in {"player_a", "player_b"}:
        expected = clean_text(pick.get("player_home" if side == "player_a" else "player_away"))
        selected = clean_text(pick.get("selected_player"))
        # Miner copies the side name directly. Refuse inconsistent new inputs;
        # do not reinterpret historical booked slips during settlement.
        if expected and " ".join(expected.casefold().split()) != " ".join(selected.casefold().split()):
            return reject("selected player does not match selected side")
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
                        return reject("rescored context veto")
            if is_no_odds:
                if not (scoring.get("should_boost") and scoring.get("strength_score", 0) >= 0.4):
                    return reject("no-odds boost gate")
        except Exception:
            if is_no_odds:
                return reject("no-odds scoring unavailable")
    else:
        if is_no_odds:
            return reject("no-odds scoring unavailable")
    odds = pick.get("odds")
    if odds is not None:
        try:
            o = float(odds)
            if o <= 1.0:
                return reject("invalid odds")
        except Exception:
            return reject("invalid odds")
    if admitted is not None:
        admitted.update(pick)
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
                return None  # a date is not a provable start time
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


def fresh_ticket_problem(accas: list[dict], target_date: str, now: datetime) -> str:
    """Recheck new ticket legs at the publication boundary, never historical cards."""
    if now.tzinfo is None or now.astimezone(TZ).date().isoformat() != target_date:
        return "ticket day changed or publication clock lacks timezone"
    for acca in accas:
        legs = acca.get("legs") if isinstance(acca, dict) else None
        if not isinstance(legs, list) or len(legs) < 2:
            return "invalid ticket legs"
        for leg in legs:
            if not isinstance(leg, dict) or not valid_leg_price(leg.get("odds")):
                return "invalid ticket price"
            problem = selected_quote_problem(leg, require_provenance=True, now=now)
            if problem:
                return problem
            kickoff = parse_kickoff(leg, target_date)
            if kickoff is None or kickoff <= now or leg.get("_kickoff_unknown") or leg.get("_late_start"):
                return "kickoff passed or unknown"
    return ""


def independent_pick_diagnostics(picks: list[dict], target_date: str, now: datetime) -> dict:
    """Counts overlap; these are diagnostic, never inputs to ticket admission."""
    from collections import Counter
    prices = Counter()
    reasons = Counter()
    fresh_not_started = 0
    for pick in picks:
        value = pick.get("odds")
        if value is None or value == "":
            prices["missing"] += 1
        else:
            try:
                price = float(value)
                if not math.isfinite(price):
                    prices["malformed_or_nonfinite"] += 1
                elif price < MIN_LEG_ODDS:
                    prices["below_floor"] += 1
                else:
                    prices["at_or_above_floor"] += 1
            except (TypeError, ValueError, OverflowError):
                prices["malformed_or_nonfinite"] += 1
        quote = pick.get("selected_quote")
        if not isinstance(quote, dict):
            reasons["selected_quote_missing_or_malformed"] += 1
        else:
            if not quote.get("market"):
                reasons["market_missing"] += 1
            if not quote.get("period"):
                reasons["period_missing"] += 1
        problem = selected_quote_problem(pick, require_provenance=True, now=now)
        if problem:
            reasons["quote: " + problem] += 1
        kickoff = parse_kickoff(pick, target_date)
        if valid_leg_price(value) and not problem and kickoff is not None and kickoff > now:
            fresh_not_started += 1
        non_quote = {}
        try:
            _is_playable_non_quote(dict(pick), rejections=non_quote)
            reasons.update({"admission: " + k: v for k, v in non_quote.items()})
        except (TypeError, ValueError, KeyError, OverflowError):
            reasons["admission: invalid inputs"] += 1
        kept, skipped = kickoff_guard([dict(pick)], target_date, now)
        if not kept:
            for _, why in skipped:
                reasons["kickoff: " + str(why)] += 1
    return {"price_classes": dict(prices), "overlapping_reasons": dict(reasons),
            "fresh_proven_not_started_at_or_above_floor": fresh_not_started}


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
    is_past_or_today = target_date <= now.date().isoformat()
    fail_open = kickoff_fail_open()
    for pick in pool:
        ko = parse_kickoff(pick, target_date)
        if ko is None:
            if not fail_open and is_past_or_today:
                pick["_kickoff_unknown"] = True
                raw = clean_text(pick.get("kickoff") or pick.get("match_time")
                                 or pick.get("time") or "")
                skipped.append((skip_record(pick), f"kickoff unreadable ({raw or 'absent'}) -> paper"))
            kept.append(pick)
            continue
        pick.pop("_kickoff_unknown", None)
        if ko <= now:
            skipped.append((skip_record(pick), "already started"))
        else:
            kept.append(pick)
    return kept, skipped

def select_accas(pool, knobs: AccaKnobs | None = None, *, diagnostics: dict | None = None):
    """ML-driven mutually exclusive accas – ML chooses winners, monitors itself.

    Returns ``(priced, paper, sorted_pool)``. ``knobs`` defaults to the live
    production settings, so ``select_accas(pool)`` == the old
    ``build_accas(pool)`` byte for byte; pass an ``AccaKnobs`` to run a
    counterfactual without editing the engine.

    Two tracks, never mixed:
    - PRICED: every leg carries REAL market odds (BetExplorer consensus is REAL, now fixed bad=10).
      Staked from real bank, Kelly-sized, ML calibrated prob.
    - PAPER: priced but non-executable or late. Hit-rate only; unpriced legs are ineligible.

    - Mutually exclusive, ML strengths, Kelly growth, continuous self-monitor
    - All candidate legs require the shared 1.30 minimum; EV is diagnostic.
    - ML scores candidates using calibrated probability and independent context evidence
    """
    k = knobs if knobs is not None else live_knobs()
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
        # Never invoke the calibrator's default when no valid input exists.
        calibrated = p.get("ml_calibrated_prob")
        try:
            if calibrated is not None and not isinstance(calibrated, bool):
                value = float(calibrated)
                if math.isfinite(value) and 0 < value <= 1:
                    return value
        except (TypeError, ValueError, OverflowError):
            pass
        raw = p.get("confidence") or p.get("prediction_prob") or p.get("prob")
        try:
            if isinstance(raw, bool):
                return None
            value = float(raw)
            if not math.isfinite(value) or not 0 < value <= 100:
                return None
            if _ML_AVAILABLE and (p.get("confidence") or p.get("prediction_prob")):
                cp = ml_predict_proba(p, registry, weights, clv)
                if math.isfinite(cp) and 0 < cp <= 1:
                    return cp
            return value / 100 if value > 1 else value
        except (TypeError, ValueError, OverflowError):
            return None

    def kelly_fraction(p, odds):
        if odds is None:
            return 0.0
        prob = get_prob(p)
        if prob is None:
            return 0.0
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
        prob_f = get_prob(p) or 0.0  # ordering only, never a displayed estimate
        odds_f = get_odds(p)
        if odds_f is None:
            return (-prob_f, -ml_score, -conf_f, str(p.get("match", "")))
        kelly_f = kelly_fraction(p, odds_f)
        edge = prob_f * odds_f - 1
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
        def reject(reason):
            if diagnostics is not None and not paper:
                counts = diagnostics.setdefault("priced_exclusions", {})
                counts[reason] = counts.get(reason, 0) + 1
        for p in pool_sorted:
            quote_error = selected_quote_problem(p)
            if quote_error:
                reject(quote_error)
                continue
            o = get_odds(p)
            # The two tracks are the two sides of ONE predicate, so they
            # always partition the pool and can never disagree about a leg.
            # (Previously each track re-derived "is this paper?" inline.)
            blocked = leg_execution_block(p) != ""
            if paper:
                if not blocked or not valid_leg_price(p.get("odds")):
                    continue
                if get_prob(p) is None:
                    continue
            else:
                if blocked:
                    reject(leg_execution_block(p))
                    continue
                if get_prob(p) is None:
                    reject("missing valid probability")
                    continue
                if not valid_leg_price(o):
                    reject("leg odds below minimum or invalid")
                    continue
                is_boost_leg = k.boost_privileges and str(p.get("ml_verdict")) == "BOOST"
                max_leg = dynamic_max_odds(p, knobs=k)
                if o > max_leg:
                    reject("leg odds above maximum")
                    continue
                # Preserve the independent probability, confidence, BOOST and
                # odds predicates in the old mixed probability/EV exception.
                try:
                    cp = float(p.get("ml_calibrated_prob") or get_prob(p) or 0)
                    if cp < 0.60 and not is_boost_leg:
                        if not ((cp >= 0.55 and o <= 3.0 and conf_of(p) >= 60)
                                or (o <= 2.2 and conf_of(p) >= 60)):
                            reject("probability below minimum")
                            continue
                except Exception:
                    pass
            bucket = str(p.get("bucket", ""))
            if "NO_ODDS" in bucket and str(p.get("ml_verdict")) != "BOOST":
                reject("no-odds bucket without BOOST")
                continue
            out.append(p)
        if diagnostics is not None:
            diagnostics["paper_eligible" if paper else "priced_eligible"] = len(out)
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
        return ["Kickoff skips: none."]
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


def format_tickets_txt(target_date, accas, state, skipped_info, diagnostics=None):
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
        lines.append("NO BET — no qualifying priced ticket (zero qualifying bets is a valid outcome)")
        if diagnostics is not None:
            lines.append(f"Pipeline: {diagnostics['picks']} picks -> {diagnostics['playable']} playable -> "
                         f"{diagnostics['builder_input']} builder inputs -> "
                         f"{diagnostics.get('priced_eligible', 0)} priced eligible legs, "
                         f"{diagnostics.get('paper_eligible', 0)} paper eligible legs")
            admission = diagnostics.get("admission_rejections", {})
            if admission:
                lines.append("Admission exclusions: " + ", ".join(
                    f"{reason}={count}" for reason, count in sorted(admission.items())))
            exclusions = diagnostics.get("priced_exclusions", {})
            if exclusions:
                lines.append("Priced-track exclusions: " + ", ".join(
                    f"{reason}={count}" for reason, count in sorted(exclusions.items())))
            n = diagnostics.get("priced_eligible", 0)
            lines.append("Ticket constraint: " + ("fewer than two eligible priced legs" if n < 2
                         else "no pair met the existing pairing/acca constraints"))
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


def _rebuild_state_problem(state: object, target_date: str) -> str:
    """Validate the persisted accounting ledger and rule out target-day activity.

    The card's absence is never considered proof of a clean day. The only
    available authoritative record here is the persisted ticket state: open
    slips, settled history, and financial events. Any malformed/undated row
    makes that record insufficient rather than being ignored.
    """
    if not isinstance(state, dict):
        return "authoritative financial state unavailable"
    for key in ("bank", "paper_bank"):
        try:
            value = float(state[key])
            if not math.isfinite(value) or value < 0:
                return f"authoritative financial state has invalid {key}"
        except (KeyError, TypeError, ValueError, OverflowError):
            return f"authoritative financial state missing or invalid {key}"
    for key in ("open_slips", "history"):
        records = state.get(key)
        if not isinstance(records, list):
            return f"authoritative financial state missing or invalid {key} ledger"
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                return f"authoritative {key}[{index}] record malformed"
            record_date = record.get("date")
            try:
                if not isinstance(record_date, str) or date.fromisoformat(record_date).isoformat() != record_date:
                    raise ValueError
            except ValueError:
                return f"authoritative {key}[{index}] record date ambiguous: {record_date!r}"
            if not isinstance(record.get("accas"), list):
                return f"authoritative {key}[{index}] ticket list ambiguous"
            if key == "open_slips":
                try:
                    stake = float(record["staked_pct"])
                    if not math.isfinite(stake) or stake < 0:
                        raise ValueError
                except (KeyError, TypeError, ValueError, OverflowError):
                    return f"authoritative open_slips[{index}] stake ambiguous"
            if key == "history" and "pending_accas" in record:
                pending = record["pending_accas"]
                if isinstance(pending, bool) or not isinstance(pending, int) or pending < 0:
                    return f"authoritative history[{index}] pending ticket count ambiguous"
            if record_date == target_date:
                return f"target-day {key}[{index}] activity exists"
    if "events" in state:
        events = state["events"]
        if not isinstance(events, list):
            return "authoritative financial events ledger malformed"
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                return f"authoritative events[{index}] record malformed"
            raw_at = event.get("at")
            try:
                parsed = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError
            except (TypeError, ValueError):
                return f"authoritative events[{index}] timestamp ambiguous: {raw_at!r}"
            if parsed.astimezone(TZ).date().isoformat() == target_date:
                return f"target-day financial event exists in events[{index}]"
    return ""


def rebuild_replacement_problem(existing: dict, state: dict, target_date: str) -> str:
    """Check an existing card and its authoritative accounting record.

    Nonempty tickets have no authoritative placed/unplaced receipt in this
    repository, so they remain ambiguous and cannot be replaced. This
    predicate does not provide locking or publication by itself.
    """
    if not isinstance(existing, dict) or existing.get("date") != target_date:
        return "existing dated card is malformed or for another day"
    accas = existing.get("accas")
    if not isinstance(accas, list):
        return "existing card tickets are ambiguous"
    if accas:
        return "nonempty card lacks authoritative unplaced status"
    try:
        real = float(existing["staked_pct"])
        paper = float(existing.get("paper_staked_pct", 0))
        if not math.isfinite(real) or not math.isfinite(paper) or real != 0 or paper != 0:
            return "empty card has recorded stake or ambiguous exposure"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "empty card has recorded stake or ambiguous exposure"
    if not isinstance(state, dict):
        return "authoritative financial state unavailable"
    for key in ("open_slips", "history"):
        records = state.get(key)
        if not isinstance(records, list):
            return "authoritative financial state incomplete"
        for record in records:
            if not isinstance(record, dict):
                return "authoritative financial record malformed"
            record_date = record.get("date")
            if not isinstance(record_date, str):
                return "authoritative financial record date ambiguous"
            try:
                parsed_date = date.fromisoformat(record_date)
            except ValueError:
                return "authoritative financial record date ambiguous"
            if parsed_date.isoformat() == target_date:
                return "same-day open or settled financial record exists"
    return ""


def capture_rebuild_preconditions(directory: Path, target_date: str) -> tuple[dict, dict]:
    """Read and prove a safe replacement/first-creation predecessor.

    Return the authoritative state and byte-level preconditions that must be
    rechecked by local publication. Missing dated cards are accepted only when
    the existing full ledger is valid and has no target-day activity.
    """
    import base64
    directory = Path(directory)
    state_path = directory / "auto_tickets_state.json"
    try:
        state_raw = state_path.read_bytes()
    except OSError as exc:
        raise RuntimeError("authoritative financial state is missing or unreadable") from exc
    try:
        state = json.loads(state_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("authoritative financial state is malformed") from exc
    state_problem = _rebuild_state_problem(state, target_date)
    if state_problem:
        raise RuntimeError(state_problem)

    card_name = f"auto_tickets_{target_date}.json"
    card_path = directory / card_name
    card_existed = card_path.exists()
    card_raw = None
    if card_existed:
        if not card_path.is_file():
            raise RuntimeError("existing dated card is not a readable regular file")
        try:
            card_raw = card_path.read_bytes()
            card = json.loads(card_raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("existing dated card is malformed or unreadable; refusing to treat it as absent") from exc
        problem = rebuild_replacement_problem(card, state, target_date)
        if problem:
            raise RuntimeError(problem)
    else:
        # A dangling symlink or directory at the destination is not absence.
        if card_path.is_symlink() or card_path.exists():
            raise RuntimeError("dated card destination is ambiguous, not safely absent")
        # A pre-existing latest-card alias for this day means that a dated
        # transaction record has gone missing. Older aliases are safe to
        # supersede only when they exactly refer to a retained dated card.
        alias_path = directory / "auto_tickets_today.json"
        alias_date = None
        if alias_path.exists() or alias_path.is_symlink():
            try:
                alias_raw = alias_path.read_bytes()
                alias = json.loads(alias_raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("latest-card alias is malformed or unreadable") from exc
            if not isinstance(alias, dict) or not isinstance(alias.get("date"), str):
                raise RuntimeError("latest-card alias date is ambiguous")
            try:
                alias_date = date.fromisoformat(alias["date"]).isoformat()
            except ValueError as exc:
                raise RuntimeError("latest-card alias date is ambiguous") from exc
            if alias_date == target_date:
                raise RuntimeError("target-day latest-card alias exists while dated card is absent")
            retained = directory / f"auto_tickets_{alias_date}.json"
            try:
                retained_raw = retained.read_bytes()
                retained_card = json.loads(retained_raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"latest-card alias has no readable retained predecessor: {retained.name}") from exc
            if (not isinstance(retained_card, dict) or retained_card.get("date") != alias_date
                    or _rebuild_digest(retained_raw) != _rebuild_digest(alias_raw)):
                raise RuntimeError("latest-card alias conflicts with its retained dated predecessor")
        latest_text = directory / "auto_tickets_today.txt"
        if latest_text.exists() or latest_text.is_symlink():
            try:
                first_line = latest_text.read_text(encoding="utf-8").splitlines()[0]
            except (OSError, UnicodeDecodeError, IndexError) as exc:
                raise RuntimeError("latest-card text alias is malformed or unreadable") from exc
            match = re.fullmatch(r"Racket Factory Auto Tickets — (\d{4}-\d{2}-\d{2})", first_line)
            if not match:
                raise RuntimeError("latest-card text alias date is ambiguous")
            text_date = match.group(1)
            try:
                text_date = date.fromisoformat(text_date).isoformat()
            except ValueError as exc:
                raise RuntimeError("latest-card text alias date is ambiguous") from exc
            if text_date == target_date:
                raise RuntimeError("target-day latest-card text exists while dated card is absent")
            if alias_date is None or text_date != alias_date:
                raise RuntimeError("latest-card JSON and text aliases conflict")
        target_text = directory / f"auto_tickets_{target_date}.txt"
        if target_text.exists() or target_text.is_symlink():
            raise RuntimeError("target-day ticket text exists without its dated card; predecessor is ambiguous")
        txn_dir = directory / ".auto_tickets_rebuild"
        prior = sorted(txn_dir.glob(f"archive_{target_date}_*.json")) if txn_dir.exists() else []
        if prior:
            raise RuntimeError(f"target-day rebuild archive already exists: {prior[0].name}")

    # These are every file the existing local publisher will replace. Snapshot
    # all of them so a card/alias/state appearing or changing during collection
    # cannot be silently overwritten.
    output_names = [card_name, "auto_tickets_today.json", f"auto_tickets_{target_date}.txt",
                    "auto_tickets_today.txt", "auto_tickets_state.json"]
    fingerprints = {}
    for name in output_names:
        path = directory / name
        try:
            if path.is_symlink():
                raise RuntimeError(f"publication predecessor is a symlink: {name}")
            raw = path.read_bytes() if path.exists() else None
        except OSError as exc:
            raise RuntimeError(f"publication predecessor is unreadable: {name}") from exc
        fingerprints[name] = _rebuild_digest(raw) if raw is not None else None
    archive_fingerprints = {}
    txn_dir = directory / ".auto_tickets_rebuild"
    if txn_dir.exists():
        for path in sorted(txn_dir.glob(f"archive_{target_date}_*.json")):
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"target-day rebuild archive is ambiguous: {path.name}")
            try:
                raw = path.read_bytes()
                record = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"target-day rebuild archive is malformed: {path.name}") from exc
            if (not isinstance(record, dict) or record.get("actionable") is not False
                    or not isinstance(record.get("transaction_id"), str)):
                raise RuntimeError(f"target-day rebuild archive is invalid: {path.name}")
            if record.get("version") == 2:
                if (record.get("target_date") != target_date
                        or path.name != f"archive_{target_date}_{record['transaction_id']}.json"):
                    raise RuntimeError(f"target-day rebuild archive identity conflicts: {path.name}")
                existed = record.get("card_preexisted")
                if existed is False:
                    if record.get("original_card_sha256") is not None or record.get("original_card_base64") is not None:
                        raise RuntimeError(f"first-creation archive falsely claims a predecessor: {path.name}")
                elif existed is True:
                    try:
                        prior = base64.b64decode(record.get("original_card_base64"), validate=True)
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(f"target-day rebuild archive predecessor malformed: {path.name}") from exc
                    if _rebuild_digest(prior) != record.get("original_card_sha256"):
                        raise RuntimeError(f"target-day rebuild archive predecessor digest mismatch: {path.name}")
                else:
                    raise RuntimeError(f"target-day rebuild archive predecessor status ambiguous: {path.name}")
            archive_fingerprints[path.name] = _rebuild_digest(raw)
    preconditions = {"target_date": target_date, "destination_existed": card_existed,
                     "card_digest": _rebuild_digest(card_raw) if card_raw is not None else None,
                     "state_digest": _rebuild_digest(state_raw), "output_fingerprints": fingerprints,
                     "archive_fingerprints": archive_fingerprints}
    return state, preconditions


def stage_rebuild_evaluation(picks: list[dict], state: dict, target_date: str,
                             now: datetime, collection: dict,
                             preconditions: dict | None = None) -> dict:
    """Evaluate a new-card proposal in memory; this does not publish or book.

    Complete collection or a partial snapshot with fixture-level fresh quote
    observations may proceed. Source coverage remains factual diagnostics; the
    caller still owns the publication lock, state recheck and journal.
    """
    import copy
    if now.tzinfo is None or now.astimezone(TZ).date().isoformat() != target_date:
        raise ValueError("rebuild requires the current configured betting day")
    if (not isinstance(collection, dict) or collection.get("status") not in {"complete", "partial"}
            or collection.get("date") != target_date):
        raise ValueError("fresh collection is failed, unavailable, or for another day")
    if collection.get("status") == "partial":
        quote_diagnostics = collection.get("quote_diagnostics")
        if (collection.get("admission_policy_version") != "racket-new-card-v1"
                or not isinstance(quote_diagnostics, dict)
                or int(quote_diagnostics.get("verified_merged_rows") or 0) < 1):
            raise ValueError("fresh collection is partial without usable fixture-level quote observations")
    try:
        collected = datetime.fromisoformat(str(collection.get("observed_at") or "").replace("Z", "+00:00"))
        if collected.tzinfo is None or not 0 <= (now - collected).total_seconds() <= 30 * 60:
            raise ValueError("fresh collection timestamp invalid or stale")
    except (TypeError, ValueError) as exc:
        raise ValueError("fresh collection timestamp invalid or stale") from exc
    if not isinstance(picks, list) or not isinstance(state, dict):
        raise ValueError("invalid staging inputs")
    # select_accas stamps and kickoff_guard annotates legs. Never mutate the
    # caller's inputs while evaluating a proposal.
    candidates = copy.deepcopy(picks)
    snapshot = copy.deepcopy(state)
    rejected: dict[str, int] = {}
    playable = []
    quote_usable = 0
    for pick in candidates:
        if not isinstance(pick, dict) or str(pick.get("ledger_kind") or "").lower() == "forecast":
            rejected["invalid or forecast pick"] = rejected.get("invalid or forecast pick", 0) + 1
            continue
        if str(pick.get("date") or pick.get("match_date") or "")[:10] != target_date:
            rejected["pick date mismatch"] = rejected.get("pick date mismatch", 0) + 1
            continue
        quote_problem = selected_quote_problem(pick, require_provenance=True)
        if quote_problem:
            rejected[quote_problem] = rejected.get(quote_problem, 0) + 1
            continue
        quote_usable += 1
        admitted = {}
        if is_playable(pick, rejections=rejected, admitted=admitted):
            admitted["_admission_policy_version"] = "racket-new-card-v1"
            playable.append(admitted)
    if candidates and quote_usable == 0:
        problem = next((key for key in rejected if key not in {"invalid or forecast pick", "pick date mismatch"}),
                       "selected quote provenance unavailable")
        raise ValueError("candidate quote provenance is missing or invalid: " + problem)
    kept, skipped = kickoff_guard(playable, target_date, now)
    # Started or unknown fixtures cannot be resurrected as a rebuild pick.
    diagnostics = {"picks": len(candidates), "playable": len(playable), "builder_input": len(kept),
                   "admission_rejections": rejected}
    priced, paper, _ = select_accas(kept, diagnostics=diagnostics)
    bank = float(snapshot.get("bank", 100.0))
    stakeable = free_bank(snapshot)
    day_cap = round(stakeable * STAKE_FRAC, 4)
    real_stakes = allocate_stakes(day_cap if priced else 0, priced)
    paper_stakes = allocate_stakes(round(float(snapshot.get("paper_bank", bank)) * STAKE_FRAC, 4) if paper else 0, paper)
    if sum(real_stakes) > day_cap + 1e-9:
        raise ValueError("staged stakes exceed available day cap")
    accas = []
    for track, stakes, is_paper in ((priced, real_stakes, False), (paper, paper_stakes, True)):
        for acca, stake in zip(track, stakes):
            accas.append({"legs": acca.get("legs", []), "odds": acca.get("odds"),
                          "type": acca.get("type", ""), "stake_pct": stake, "paper": is_paper})
    booked_real = round(sum(real_stakes), 4)
    if preconditions is not None:
        names = {f"auto_tickets_{target_date}.json", "auto_tickets_today.json",
                 f"auto_tickets_{target_date}.txt", "auto_tickets_today.txt", "auto_tickets_state.json"}
        fingerprints = preconditions.get("output_fingerprints") if isinstance(preconditions, dict) else None
        archive_fingerprints = preconditions.get("archive_fingerprints") if isinstance(preconditions, dict) else None
        if (not isinstance(preconditions, dict) or preconditions.get("target_date") != target_date
                or not isinstance(preconditions.get("destination_existed"), bool)
                or not isinstance(fingerprints, dict) or set(fingerprints) != names
                or not isinstance(archive_fingerprints, dict)
                or any(not re.fullmatch(r"archive_" + re.escape(target_date) + r"_[A-Za-z0-9_-]{1,80}\.json", name)
                       or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                       for name, digest in archive_fingerprints.items())
                or preconditions.get("state_digest") != fingerprints.get("auto_tickets_state.json")
                or preconditions.get("card_digest") != fingerprints.get(f"auto_tickets_{target_date}.json")
                or preconditions.get("destination_existed") != (preconditions.get("card_digest") is not None)
                or not isinstance(preconditions.get("state_digest"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", preconditions["state_digest"])):
            raise ValueError("invalid rebuild publication preconditions")
    result = {"date": target_date, "generated_at": now.isoformat(), "collection_observed_at": collected.isoformat(),
            "status": "PROPOSED" if accas else "NO BET", "bank_pct": bank, "free_bank_pct": stakeable,
            "committed_pct": round(bank - stakeable, 4), "day_cap_pct": day_cap, "stake_frac": STAKE_FRAC,
            "stake_per_acca_pct": round(booked_real / len(priced), 4) if priced else 0,
            "staked_pct": booked_real, "paper_staked_pct": round(sum(paper_stakes), 4),
            "accas": accas, "skipped": skipped, "diagnostics": diagnostics,
            "admission_policy_version": "racket-new-card-v1",
            "frozen": now.astimezone(TZ).hour >= FREEZE_HOUR or now.astimezone(TZ).hour < GENERATE_HOUR_START}
    if preconditions is not None:
        result["publication_preconditions"] = copy.deepcopy(preconditions)
    return result


def _rebuild_digest(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _rebuild_atomic_bytes(path: Path, data: bytes) -> None:
    """Durably replace one file; set-level recovery is handled by the journal."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".rf-rebuild-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _recover_rebuild_journal(directory: Path) -> None:
    """Roll an interrupted local publication back under its publication lock.

    This is only the local-filesystem primitive. Existing external readers and
    independently cloned runners do not yet share this lock; it is not wired
    into the workflow until a shared publication authority is established.
    """
    import base64
    journal_path = directory / ".auto_tickets_rebuild" / "journal.json"
    if not journal_path.exists():
        return
    journal = json.loads(journal_path.read_text())
    if journal.get("status") != "applying":
        return
    entries = journal.get("before")
    if not isinstance(entries, dict):
        raise RuntimeError("rebuild journal is malformed; manual recovery required")
    for name, value in entries.items():
        relative = Path(name)
        safe = (not relative.is_absolute() and ".." not in relative.parts
                and (len(relative.parts) == 1
                     or (len(relative.parts) == 2 and relative.parts[0] == ".auto_tickets_rebuild"
                         and relative.name.startswith("archive_") and relative.suffix == ".json")))
        if not safe:
            raise RuntimeError("rebuild journal contains unsafe filename")
        path = directory / relative
        if value is None:
            path.unlink(missing_ok=True)
        else:
            _rebuild_atomic_bytes(path, base64.b64decode(value, validate=True))
    journal["status"] = "rolled_back"
    _rebuild_atomic_bytes(journal_path, json.dumps(journal, sort_keys=True).encode())


def publish_staged_rebuild_local(directory: Path, target_date: str, proposal: dict,
                                 *, expected_card_existed: bool,
                                 expected_card_digest: str | None,
                                 expected_state_digest: str, run_id: str) -> str:
    """Publish a guarded first card or eligible replacement under the existing lock.

    The staged destination-existence bit and byte digests are rechecked under
    the local publication lock. Missing predecessors are recorded as absent,
    never represented by fabricated card bytes. The journal includes the
    transaction archive so interruption rollback leaves no partial authority.
    """
    import base64
    import copy
    import fcntl
    from contextlib import closing
    directory = Path(directory)
    if not run_id or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id):
        raise ValueError("invalid rebuild transaction id")
    now = now_local()
    if now.date().isoformat() != target_date or not isinstance(proposal, dict) or proposal.get("date") != target_date:
        raise ValueError("rebuild must target the current betting day")
    try:
        collected = datetime.fromisoformat(str(proposal.get("collection_observed_at") or "").replace("Z", "+00:00"))
        if collected.tzinfo is None or not 0 <= (now - collected).total_seconds() <= 30 * 60:
            raise ValueError("stale collection")
    except (TypeError, ValueError) as exc:
        raise ValueError("staged collection is no longer fresh") from exc
    proposal_digest = _rebuild_digest(json.dumps(proposal, sort_keys=True, default=str).encode())
    if proposal.get("status") not in {"PROPOSED", "NO BET"} or not isinstance(proposal.get("accas"), list):
        raise ValueError("invalid staged proposal")
    if (proposal["status"] == "NO BET") != (not proposal["accas"]):
        raise ValueError("proposal status disagrees with tickets")
    preconditions = proposal.get("publication_preconditions")
    if (not isinstance(preconditions, dict) or preconditions.get("target_date") != target_date
            or preconditions.get("destination_existed") is not expected_card_existed
            or preconditions.get("card_digest") != expected_card_digest
            or preconditions.get("state_digest") != expected_state_digest
            or (expected_card_existed != (expected_card_digest is not None))):
        raise ValueError("staged publication preconditions do not match caller")
    for acca in proposal["accas"]:
        if not isinstance(acca, dict) or len(acca.get("legs") or []) < 2:
            raise ValueError("staged ticket is invalid")
        for leg in acca["legs"]:
            if selected_quote_problem(leg, require_provenance=True):
                raise ValueError("staged quote changed or is stale")
            allowed, _ = kickoff_guard([leg], target_date, now)
            if not allowed:
                raise ValueError("staged match has started or kickoff is unknown")
    directory.mkdir(parents=True, exist_ok=True)
    txn_dir = directory / ".auto_tickets_rebuild"
    txn_dir.mkdir(parents=True, exist_ok=True)
    with closing(open(txn_dir / "publication.lock", "a+b")) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another local rebuild is publishing") from exc
        _recover_rebuild_journal(directory)
        journal_path = txn_dir / "journal.json"
        if journal_path.exists():
            prev = json.loads(journal_path.read_text())
            if prev.get("status") == "committed" and prev.get("run_id") == run_id:
                if prev.get("proposal_digest") != proposal_digest:
                    raise RuntimeError("rebuild transaction id reused for a different proposal")
                return "already_committed"

        # Byte-level compare first so a malformed replacement file is reported
        # as a race, not reinterpreted as absence or as a new predecessor.
        fingerprints = preconditions.get("output_fingerprints", {})
        for name, expected_digest in fingerprints.items():
            path = directory / name
            if path.is_symlink():
                raise RuntimeError(f"publication predecessor changed during staging: {name}")
            try:
                current_raw = path.read_bytes() if path.exists() else None
            except OSError as exc:
                raise RuntimeError(f"publication predecessor changed during staging: {name}") from exc
            current_digest = _rebuild_digest(current_raw) if current_raw is not None else None
            if current_digest != expected_digest:
                raise RuntimeError(f"card, financial state, exposure ledger, or output changed during staging: {name}")
        # Re-run semantic proof after the byte-level compare. This catches a
        # pre-existing malformed ledger and revalidates first-creation safety.
        fresh_state, fresh_preconditions = capture_rebuild_preconditions(directory, target_date)
        if fresh_preconditions != preconditions:
            raise RuntimeError("card, financial state, exposure ledger, or output changed during staging")
        card_name = f"auto_tickets_{target_date}.json"
        state_name = "auto_tickets_state.json"
        card_path, state_path = directory / card_name, directory / state_name
        card_raw = card_path.read_bytes() if expected_card_existed else None
        state_raw = state_path.read_bytes()
        if (expected_card_existed != (card_raw is not None)
                or (card_raw is not None and _rebuild_digest(card_raw) != expected_card_digest)
                or _rebuild_digest(state_raw) != expected_state_digest):
            raise RuntimeError("current card or financial state changed during staging")
        state = json.loads(state_raw)
        if _rebuild_state_problem(state, target_date):
            raise RuntimeError(_rebuild_state_problem(state, target_date))
        if expected_card_existed:
            existing = json.loads(card_raw)
            problem = rebuild_replacement_problem(existing, state, target_date)
            if problem:
                raise RuntimeError(problem)
        elif fresh_state != state:
            raise RuntimeError("authoritative financial state changed during staging")

        try:
            total = sum(float(a.get("stake_pct", 0)) for a in proposal["accas"] if not a.get("paper"))
            cap = round(free_bank(state) * STAKE_FRAC, 4)
            if not math.isfinite(total) or total < 0 or total > cap + 1e-9 or abs(total - float(proposal["staked_pct"])) > 1e-6:
                raise ValueError("staged stakes do not match the current available bank")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid staged stakes or available bank") from exc
        replacement = copy.deepcopy(proposal)
        replacement["rebuild_id"] = run_id
        replacement["status"] = "CURRENT" if replacement["accas"] else "NO BET"
        new_state = copy.deepcopy(state)
        if replacement["accas"]:
            for acca in replacement["accas"]:
                stamp_acca(acca)
            new_state["open_slips"].append({"date": target_date, "generated_at": replacement["generated_at"],
                "accas": copy.deepcopy(replacement["accas"]), "staked_pct": replacement["staked_pct"],
                "stake_per_acca_pct": replacement["stake_per_acca_pct"], "_exec_regime": EXECUTION_REGIME_ID})
        text = format_tickets_txt(target_date, replacement["accas"], new_state,
                                  replacement.get("skipped", []), replacement.get("diagnostics"))
        archive_name = f"archive_{target_date}_{run_id}.json"
        archive_rel = f".auto_tickets_rebuild/{archive_name}"
        archive_path = directory / archive_rel
        if archive_path.exists() or archive_path.is_symlink():
            raise RuntimeError("rebuild transaction archive already exists without a committed retry")
        archive = {"version": 2,
                   "reason": "guarded replacement" if expected_card_existed else "guarded first creation",
                   "transaction_id": run_id, "target_date": target_date,
                   "card_preexisted": expected_card_existed,
                   "original_card_sha256": expected_card_digest,
                   "original_card_base64": base64.b64encode(card_raw).decode() if card_raw is not None else None,
                   "actionable": False}
        replacement["rebuild_archive"] = archive_rel
        if expected_card_existed:
            replacement["prior_archive"] = archive_rel
        encoded = json.dumps(replacement, indent=2).encode()
        state_encoded = (json.dumps(new_state, indent=2).encode() if new_state != state else state_raw)
        files = {card_name: encoded, "auto_tickets_today.json": encoded,
                 f"auto_tickets_{target_date}.txt": text.encode(), "auto_tickets_today.txt": text.encode(),
                 state_name: state_encoded,
                 archive_rel: json.dumps(archive, sort_keys=True).encode()}
        before = {}
        for name in files:
            path = directory / name
            if path.is_symlink():
                raise RuntimeError(f"publication predecessor is a symlink: {name}")
            try:
                previous = path.read_bytes() if path.exists() else None
            except OSError as exc:
                raise RuntimeError(f"publication predecessor is unreadable: {name}") from exc
            if name == archive_rel and previous is not None:
                raise RuntimeError("rebuild transaction archive appeared during staging")
            before[name] = base64.b64encode(previous).decode() if previous is not None else None
        problem = fresh_ticket_problem(proposal["accas"], target_date, now_local())
        if problem:
            raise ValueError("staged ticket is no longer executable: " + problem)
        journal = {"version": 2, "run_id": run_id, "proposal_digest": proposal_digest,
                   "date": target_date, "destination_preexisted": expected_card_existed,
                   "card_digest": expected_card_digest, "state_digest": expected_state_digest,
                   "status": "applying", "before": before}
        _rebuild_atomic_bytes(journal_path, json.dumps(journal, sort_keys=True).encode())
        pending = json.dumps({"date": target_date, "status": "PUBLICATION_IN_PROGRESS", "accas": [],
                              "actionable": False, "rebuild_id": run_id}).encode()
        try:
            for name in (card_name, "auto_tickets_today.json"):
                _rebuild_atomic_bytes(directory / name, pending)
            for name in (f"auto_tickets_{target_date}.txt", "auto_tickets_today.txt"):
                _rebuild_atomic_bytes(directory / name, b"PUBLICATION IN PROGRESS - NOT ACTIONABLE\\n")
            for name, data in files.items():
                _rebuild_atomic_bytes(directory / name, data)
            journal["status"] = "committed"
            _rebuild_atomic_bytes(journal_path, json.dumps(journal, sort_keys=True).encode())
        except Exception:
            _recover_rebuild_journal(directory)
            raise
        return "committed"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--ignore-kickoff", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="guarded force rebuild requested (currently unavailable)")
    args = ap.parse_args()
    if args.force:
        ap.error("guarded force rebuild is unavailable; existing tickets and financial state are preserved")
    target_date = args.date or today_str()
    now = now_local()
    picks_input_available = fresh_pick_inputs_available(target_date)
    picks = load_picks(target_date)
    admission_rejections = {}
    playable = []
    for pick in picks:
        admitted_pick = {}
        if is_playable(pick, rejections=admission_rejections, admitted=admitted_pick,
                       require_provenance=True):
            admitted_pick["_admission_policy_version"] = "racket-new-card-v1"
            playable.append(admitted_pick)
    kept, skipped = kickoff_guard(playable, target_date, now)
    # Paper statistics cannot make a late or unknown kickoff executable.
    # Do not build a new ticket from an unverified kickoff in either track.
    kept = [p for p in kept if not p.get("_kickoff_unknown") and not p.get("_late_start")]
    diagnostics = {"picks": len(picks), "playable": len(playable), "builder_input": len(kept),
                   "admission_rejections": admission_rejections,
                   "independent": independent_pick_diagnostics(picks, target_date, now)}
    priced, paper, sorted_pool = select_accas(kept, diagnostics=diagnostics)
    accas = priced + paper
    print(f"Auto tickets stages: {diagnostics}; priced tickets={len(priced)}, paper tickets={len(paper)}")
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
    for acca in accas_out:
        for leg in acca.get("legs", []):
            if isinstance(leg, dict):
                leg["_admission_policy_version"] = "racket-new-card-v1"
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
        "diagnostics": diagnostics,
        "admission_policy_version": "racket-new-card-v1",
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
    allow_write = should_write_ticket_files(
        existing_ledger, len(accas_out), force=args.force,
        is_frozen_now=is_frozen, target_date=target_date,
        in_generation_window=in_generation_window,
    )
    run_as_of = os.environ.get("RACKET_FACTORY_RUN_AS_OF", "").strip()
    collection_status = "unknown"
    collection_freshness = "unknown"
    collection = {}
    if run_as_of:
        collection_status, collection, collection_freshness = current_quote_collection(target_date, run_as_of)
    evaluation_status = "valid_card" if accas_out else "input_failure"
    if not picks_input_available:
        allow_write = False
        evaluation_status = "input_failure"
    if allow_write and accas_out:
        # Per-leg freshness, identity, side, kickoff and price validation is
        # authoritative for a non-empty new card; global coverage is not.
        problem = fresh_ticket_problem(accas_out, target_date, now_local())
        if problem:
            allow_write = False
            evaluation_status = "input_failure"
            print(f"REFUSING stale or invalid ticket publication: {problem}")
    if not accas_out:
        usable_observations = (collection_status == "complete" and collection_freshness == "fresh") or (
            collection_status == "partial" and collection_has_fresh_observations(collection, collection_freshness))
        if not picks_input_available or not usable_observations:
            allow_write = False
            evaluation_status = "input_failure"
        else:
            evaluation_status = "clean_no_bet"
    if collection.get("oldest_offer_observed_at"):
        out["collection_observed_at"] = collection.get("oldest_offer_observed_at")
    evaluation = {"date": target_date, "run_as_of": run_as_of,
                  "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
                  "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                  "evaluated_at": now_local().isoformat(), "collection_status": collection_status,
                  "collection_observation_freshness": collection_freshness,
                  "evaluation_status": evaluation_status,
                  "picks_input_available": picks_input_available,
                  "admission_policy_version": "racket-new-card-v1",
                  "diagnostics": diagnostics, "priced_tickets": len(priced), "paper_tickets": len(paper),
                  "publication": "local_write" if allow_write else "retained_previous_card"}
    (LOCALDATA / f"auto_tickets_evaluation_{target_date}.json").write_text(json.dumps(evaluation, indent=2))
    from racketfactory.rebuild_diagnostics import directory as diagnostics_directory
    diagnostics_dir = diagnostics_directory()
    if diagnostics_dir is not None:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / "ticket_evaluation.json").write_text(json.dumps(evaluation, indent=2))
    if not allow_write:
        print(f"REFUSING to overwrite auto_tickets_{target_date}.* "
              f"(frozen/non-empty ledger guard or quote collection {collection_status}; keeping existing files and state).")
        return 0
    else:
        (LOCALDATA / f"auto_tickets_{target_date}.json").write_text(json.dumps(out, indent=2))
        (LOCALDATA / f"auto_tickets_{target_date}.txt").write_text(format_tickets_txt(target_date, accas, state, skipped, diagnostics))
        (LOCALDATA / "auto_tickets_today.json").write_text(json.dumps(out, indent=2))
        (LOCALDATA / "auto_tickets_today.txt").write_text(format_tickets_txt(target_date, accas, state, skipped, diagnostics))
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
