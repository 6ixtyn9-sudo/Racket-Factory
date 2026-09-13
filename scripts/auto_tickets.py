#!/usr/bin/env python3
"""RACKET FACTORY AUTO TICKETS — Strengths-focused, avoids super short odds, no singles

Recipe v3 (user dislikes singles):
  LEGS      CERTIFIED_CLEAN, WATCHLIST, CAUTION + WATCHLIST_NO_ODDS if ML BOOST strength>=0.4
  FILTER    Avoid super short odds:
            - MIN_ODDS_PER_LEG 1.35, MIN_ACCA_ODDS 2.0
            - Value estimated: 85%->1.65 not 1.15, 75%->1.9, 65%->2.25
  ACCAS     4 accas max, all multi-leg:
            - 3x 2-leg value accas (top value)
            - 1x 3-leg high-strength BOOST
            No singles
  STAKE     25% bank per day, value 2-leg 28.3% each, 3-leg 15%
  FREEZE    06:00-09:00 SAST
"""
from __future__ import annotations
import argparse, json, math, os, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"

try:
    from racketfactory.ml import load_audit_rolling, build_context_registry, score_pick_strengths, source_weights_from_audit
    _ML_AVAILABLE = True
except Exception:
    _ML_AVAILABLE = False
    load_audit_rolling = lambda: {}
    build_context_registry = lambda x: {}
    score_pick_strengths = lambda pick, reg, weights: {"strength_score": 0, "should_veto": False, "should_boost": False, "w_score": 0}
    source_weights_from_audit = lambda x: {}

GENERATE_HOUR_START = 6
FREEZE_HOUR = 9
TZ = ZoneInfo("Africa/Johannesburg")
STAKE_FRAC = 0.25
MAX_ACCAS = 4
MAX_LEGS = 8
PLAYABLE_BUCKETS = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}
PLAYABLE_BUCKETS_WITH_ML = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "WATCHLIST_NO_ODDS"}
MIN_ODDS_PER_LEG = 1.35
MIN_ACCA_ODDS = 2.0
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

_UNTRUSTED_ODDS_SOURCES = {"", "nan", "none", "ml_estimated", "<na>"}


def _leg_real_odds(pick: dict):
    """Real market odds for a leg, or None when unpriced.

    NEVER estimated: legs without a trusted market price go to the paper
    track (hit-rate only) instead of being staked on fabricated odds.
    """
    if pick.get("odds_reject_reason"):
        return None
    src = str(pick.get("odds_source") or "").strip().lower()
    if src in _UNTRUSTED_ODDS_SOURCES:
        return None
    try:
        o = float(pick.get("odds"))
    except (TypeError, ValueError):
        return None
    return o if o > 1.0 else None


def _leg_paper_reason(pick: dict) -> str:
    if pick.get("_late_start"):
        return "already started at generation time (stale price)"
    if _leg_real_odds(pick) is None:
        return "no trusted market price"
    return ""


def is_playable(pick: dict) -> bool:
    bucket = str(pick.get("bucket","")).upper()
    is_no_odds = "NO_ODDS" in bucket
    if bucket not in PLAYABLE_BUCKETS and not (is_no_odds and bucket in PLAYABLE_BUCKETS_WITH_ML):
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
            if scoring.get("should_veto") and scoring.get("strength_score", 0) < -0.3:
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

def kickoff_guard(pool, target_date, now):
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

def build_accas(pool):
    """ML-driven mutually exclusive accas for capital growth (Edge parity).

    Two tracks, never mixed:

    - PRICED: every leg carries real market odds (>= MIN_ODDS_PER_LEG).
      Staked from the real bank, Kelly-sized.
    - PAPER: any leg unpriced or already started. Hit-rate only; the
      grader settles W/L but the legs never touch any bank.

    - Mutually exclusive: no leg reused across accas (prudent, avoids correlated risk)
    - ML strengths: source_weights (Wilson LB), context ROI veto/boost, strength_score
    - Kelly growth: fractional Kelly per leg and per acca, using prob vs REAL odds only
    """
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
        return _leg_real_odds(p)

    def get_prob(p):
        # Probability from confidence or prediction_prob
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
        # Fallback from confidence
        conf = p.get("confidence") or 60
        try:
            cf = float(conf)
            if cf <= 1.0:
                cf *= 100
            return max(0.51, min(0.85, cf / 100.0))
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
        return max(0.0, min(0.25, f))  # Cap at 25% Kelly, fractional

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
        prob_f = get_prob(p)
        odds_f = get_odds(p)
        if odds_f is None:
            # Paper legs have no price: rank on prob/ML/conf only.
            return (-prob_f, -ml_score, -conf_f, str(p.get("match", "")))
        kelly_f = kelly_fraction(p, odds_f)
        edge = prob_f * odds_f - 1
        source_count = int(p.get("source_count") or 1)
        value_score = (edge if edge > 0 else 0.02) * (1 + ml_score) * (1 + kelly_f * 2) * (1 + source_count * 0.05)
        return (-value_score, -ml_score, -conf_f, -kelly_f, odds_f, str(p.get("match", "")))

    def match_key(p):
        return normalize_name(p.get("match") or f"{p.get('player_a')} vs {p.get('player_b')}")

    def eligible_pool(pool_sorted, paper):
        out = []
        for p in pool_sorted:
            o = get_odds(p)
            late = bool(p.get("_late_start"))
            if paper:
                if o is not None and not late:
                    continue
            else:
                if o is None or late or o < MIN_ODDS_PER_LEG:
                    continue
            bucket = str(p.get("bucket", ""))
            if "NO_ODDS" in bucket and str(p.get("ml_verdict")) != "BOOST":
                continue
            out.append(p)
        return out

    def build_track(pool_sorted, paper, used_matches):
        """Build 2-leg + 3-leg + fallback accas for one track."""
        track = []
        suffix = "_paper" if paper else ""
        idx = 0
        while idx < len(pool_sorted) and len(track) < MAX_ACCAS:
            chunk = []
            while len(chunk) < 2 and idx < len(pool_sorted):
                p = pool_sorted[idx]
                mk = match_key(p)
                if mk not in used_matches:
                    chunk.append(p)
                    used_matches.add(mk)
                idx += 1
            if len(chunk) == 2:
                if paper:
                    for leg in chunk:
                        leg["_paper_reason"] = _leg_paper_reason(leg)
                    track.append({"legs": chunk, "odds": None, "type": f"value_2leg_mutual{suffix}",
                                  "prob": round(math.prod([get_prob(leg) for leg in chunk]), 4),
                                  "paper": True})
                else:
                    prod = math.prod([get_odds(leg) for leg in chunk])
                    if prod >= MIN_ACCA_ODDS:
                        prob_prod = math.prod([get_prob(leg) for leg in chunk])
                        kelly_acca = (prod * prob_prod - (1 - prob_prod)) / (prod - 1) if prod > 1 else 0
                        kelly_acca = max(0.0, min(0.15, kelly_acca))
                        track.append({"legs": chunk, "odds": round(prod, 2), "type": "value_2leg_mutual",
                                      "kelly": round(kelly_acca, 4), "prob": round(prob_prod, 4), "paper": False})
        if _ML_AVAILABLE:
            boost_picks = [p for p in pool_sorted if str(p.get("ml_verdict")) == "BOOST"]
            boost_unused = [p for p in boost_picks if match_key(p) not in used_matches]
            if len(boost_unused) >= 3 and len(track) < MAX_ACCAS:
                chunk = boost_unused[:3]
                for leg in chunk:
                    used_matches.add(match_key(leg))
                if paper:
                    for leg in chunk:
                        leg["_paper_reason"] = _leg_paper_reason(leg)
                    track.append({"legs": chunk, "odds": None, "type": f"high_strength_3leg_mutual{suffix}",
                                  "prob": round(math.prod([get_prob(leg) for leg in chunk]), 4),
                                  "paper": True})
                else:
                    prod = math.prod([get_odds(leg) for leg in chunk])
                    if prod >= 2.0:
                        prob_prod = math.prod([get_prob(leg) for leg in chunk])
                        kelly_acca = (prod * prob_prod - (1 - prob_prod)) / (prod - 1) if prod > 1 else 0
                        kelly_acca = max(0.0, min(0.10, kelly_acca))
                        track.append({"legs": chunk, "odds": round(prod, 2), "type": "high_strength_3leg_mutual",
                                      "kelly": round(kelly_acca, 4), "prob": round(prob_prod, 4), "paper": False})
        if not track and len(pool_sorted) >= 2:
            chunk1 = pool_sorted[:2]
            if paper:
                for leg in chunk1:
                    leg["_paper_reason"] = _leg_paper_reason(leg)
                track.append({"legs": chunk1, "odds": None, "type": f"fallback_2leg_mutual{suffix}", "paper": True})
            else:
                prod1 = math.prod([get_odds(leg) for leg in chunk1])
                track.append({"legs": chunk1, "odds": round(prod1, 2), "type": "fallback_2leg_mutual", "paper": False})
        return track[:MAX_ACCAS]

    pool_sorted = sorted(pool, key=sort_key)
    used_matches: set = set()
    priced = build_track(eligible_pool(pool_sorted, False), False, used_matches)
    paper = build_track(eligible_pool(pool_sorted, True), True, used_matches)
    return priced, paper, pool_sorted


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

def _acca_weights(accas):
    weights = []
    for acca in accas:
        t = acca.get("type", "")
        weights.append(0.15 if "3leg" in t else 0.283)
    s = sum(weights)
    return [w / s for w in weights] if s else []


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
        if skipped_info:
            lines.append(f"Skipped: {skipped_info}")
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
                              *, force: bool = False) -> bool:
    """No-clobber rule for ticket ledgers.

    * Frozen ledgers are immutable (the day is closed; regeneration is
      meaningless) unless ``--force`` is passed.
    * An empty regeneration must never blank a ledger that has booked accas
      (observed 2026-09-12 evening: 1 acca -> 0 accas after picks starved).
    """
    if force:
        return True
    if not existing:
        return True
    if existing.get("frozen") is True:
        return False
    if new_acca_count == 0 and existing.get("accas"):
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
        skipped = [(p, "paper_late_included") for p in late]
        kept = playable
    priced, paper, sorted_pool = build_accas(kept)
    accas = priced + paper
    state = load_state()
    bank = state.get("bank", 100.0)
    total_stake = bank * STAKE_FRAC if priced else 0
    weights = _acca_weights(priced)
    paper_bank = state.get("paper_bank", bank)
    paper_stake_total = paper_bank * STAKE_FRAC if paper else 0
    paper_weights = _acca_weights(paper)
    accas_out = []
    for i, acca in enumerate(priced):
        stake_pct = total_stake * weights[i] if weights else 0
        accas_out.append({"legs": acca.get("legs", []), "odds": acca.get("odds", 1.0),
                          "type": acca.get("type", ""), "stake_pct": round(stake_pct, 4),
                          "paper": False})
    for i, acca in enumerate(paper):
        stake_pct = paper_stake_total * paper_weights[i] if paper_weights else 0
        accas_out.append({"legs": acca.get("legs", []), "odds": acca.get("odds"),
                          "type": acca.get("type", ""), "stake_pct": round(stake_pct, 4),
                          "paper": True})
    is_frozen = now.hour >= FREEZE_HOUR or now.hour < GENERATE_HOUR_START
    out = {
        "date": target_date,
        "generated_at": now.isoformat(),
        "bank_pct": bank,
        "stake_per_acca_pct": round(total_stake / len(priced), 4) if priced else 0,
        "staked_pct": round(total_stake, 4),
        "paper_staked_pct": round(paper_stake_total, 4),
        "accas": accas_out,
        "skipped": skipped,
        "frozen": is_frozen,
    }
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    if not should_write_ticket_files(
        _existing_ticket_ledger(LOCALDATA / f"auto_tickets_{target_date}.json"),
        len(accas_out), force=args.force,
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
                        slip = {
                            "date": d,
                            "generated_at": data.get("generated_at") or f"{d}T00:00:00",
                            "accas": data.get("accas", []),
                            "staked_pct": data.get("staked_pct", 0),
                            "stake_per_acca_pct": data.get("stake_per_acca_pct", 0),
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
                    "staked_pct": round(total_stake, 4),
                    "stake_per_acca_pct": round(total_stake / len(accas), 4) if accas else 0,
                }
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
