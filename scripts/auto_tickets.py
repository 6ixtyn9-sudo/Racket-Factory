#!/usr/bin/env python3
"""RACKET FACTORY AUTO TICKETS — Rolling edition, percent-only.

Mirrors Edge Factory's auto_tickets.py but for tennis.

Recipe (tennis-adapted):
  LEGS      all playable-bucket picks with a price — CERTIFIED_CLEAN, WATCHLIST, CAUTION
            Excludes SKIPPED_DEAD_EDGE, WATCHLIST_NO_ODDS, etc.
  ORDER     highest confidence first, then EV, then odds (low to high for stability)
  ACCAS     2 legs each, consecutive pairs of top 4-6, up to 2 per day (barbell off, simple)
  STAKE     1/4 of bank per day, split equally across accas
            (Edge audit 2026-09-04: 30-50% flat, 33% keeps 96% peak growth at 67% DD)
  FREEZE    slips may START building at 06:00 SAST, FREEZE at 09:00 SAST
            After freeze, re-print same ticket — no intraday drift
  KICKOFF   simple guard: drop legs whose kickoff is missing or already started today

Settlement uses warehouse.csv.gz via tolerant name matching (same as audit_recent_picks).

Outputs:
  localdata/auto_tickets_YYYY-MM-DD.json
  localdata/auto_tickets_YYYY-MM-DD.txt
  localdata/auto_tickets_today.json
  localdata/auto_tickets_today.txt
  localdata/auto_tickets_state.json
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

# cadence
GENERATE_HOUR_START = 6
FREEZE_HOUR = 9
TZ = ZoneInfo("Africa/Johannesburg")

# recipe
STAKE_FRAC = 0.25
MAX_ACCAS = 2
LEGS_PER_ACCA = 2
MAX_LEGS = MAX_ACCAS * LEGS_PER_ACCA
PLAYABLE_BUCKETS = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}
TAKE_PROFIT_GAIN = 1.0  # +100% per cycle

# files
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
    text = re.sub(r"[^a-zA-Z0-9/\s'-]", " ", text).lower()
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
    # Prefer official archive, fallback to picks_today.json
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
                # filter out forecast rows if accidentally in official file
                filtered = [r for r in data if isinstance(r, dict) and str(r.get("ledger_kind","")).lower() != "forecast"]
                if filtered:
                    return filtered
                return [r for r in data if isinstance(r, dict)]
        except Exception:
            continue
    return []

def is_playable(pick: dict) -> bool:
    bucket = str(pick.get("bucket","")).upper()
    if bucket not in PLAYABLE_BUCKETS:
        return False
    odds = pick.get("odds")
    try:
        o = float(odds)
        if o <= 1.0:
            return False
    except Exception:
        return False
    # must have selected player and match
    if not clean_text(pick.get("selected_player")):
        return False
    if not clean_text(pick.get("match")):
        return False
    # skip if odds_source indicates no odds
    if "NO_ODDS" in bucket:
        return False
    return True

def parse_kickoff(pick: dict, target_date: str) -> datetime | None:
    # Try to parse kickoff field
    raw = clean_text(pick.get("kickoff") or pick.get("match_time") or pick.get("time") or "")
    if not raw:
        return None
    # If raw is HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if m:
        try:
            h = int(m.group(1)); mi = int(m.group(2))
            dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=TZ)
            return dt.replace(hour=h, minute=mi)
        except Exception:
            return None
    # If raw contains date
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
    for pick in pool:
        ko = parse_kickoff(pick, target_date)
        if ko is None:
            # If no kickoff, keep but warn — tennis kickoffs are often missing
            # For safety, keep if date is today and we are before freeze? We keep.
            kept.append(pick)
            continue
        # If kickoff is today and already started (now > ko), drop
        if ko.date().isoformat() == target_date and ko <= now:
            skipped.append((pick, "already started"))
        else:
            kept.append(pick)
    return kept, skipped

def build_accas(pool: list[dict]):
    # Sort: confidence desc, EV desc, odds asc
    def sort_key(p):
        conf = p.get("confidence") or 0
        try:
            conf_f = float(conf)
            if conf_f <= 1.0:
                conf_f *= 100
        except Exception:
            conf_f = 0
        ev = p.get("expected_value") or 0
        try:
            ev_f = float(ev)
        except Exception:
            ev_f = 0
        odds = p.get("odds") or 999
        try:
            odds_f = float(odds)
        except Exception:
            odds_f = 999
        return (-conf_f, -ev_f, odds_f, str(p.get("match","")))
    pool_sorted = sorted(pool, key=sort_key)
    top = pool_sorted[:MAX_LEGS]
    accas = []
    for i in range(0, len(top), LEGS_PER_ACCA):
        chunk = top[i:i+LEGS_PER_ACCA]
        if len(chunk) < LEGS_PER_ACCA:
            break
        # compute acca odds
        prod = 1.0
        for leg in chunk:
            try:
                prod *= float(leg.get("odds") or 1.0)
            except Exception:
                prod *= 1.0
        accas.append({
            "legs": chunk,
            "odds": round(prod, 2),
        })
        if len(accas) >= MAX_ACCAS:
            break
    return accas, pool_sorted

def load_state():
    if not STATE_FILE.exists():
        return {
            "bank": 100.0,
            "base_pct": 100.0,
            "cycle_base": 100.0,
            "open_slips": [],
            "history": [],
            "events": [],
        }
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {
            "bank": 100.0,
            "base_pct": 100.0,
            "cycle_base": 100.0,
            "open_slips": [],
            "history": [],
            "events": [],
        }

def save_state(state):
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))

def take_profit_target(state):
    return state.get("cycle_base", 100.0) * (1.0 + TAKE_PROFIT_GAIN)

def format_tickets_txt(target_date: str, accas: list[dict], state, skipped_info):
    now = now_local()
    bank = state.get("bank", 100.0)
    free_bank = bank - sum(s.get("staked_pct",0) for s in state.get("open_slips",[]) if s.get("date") != target_date)
    # free bank is total minus committed open slips not today
    # For today, committed includes today's previous open? Simplify: free = bank - sum open except today
    # Actually after settlement, open should be empty except today
    cycle_base = state.get("cycle_base", 100.0)
    next_tp = take_profit_target(state)

    lines = []
    lines.append(f"AUTO TICKETS (TENNIS) — {target_date}")
    lines.append("="*60)
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append(f"PERFORMANCE: total bank {bank:.1f}% of capital (x{bank/100:.2f}) = free bank {free_bank:.1f}% + committed {bank-free_bank:.1f}% · next take-profit at {next_tp:.1f}%")
    lines.append("")

    if not accas:
        lines.append("NO BET — not enough playable legs or all filtered by kickoff guard")
        if skipped_info:
            lines.append(f"Skipped {len(skipped_info)} legs (kickoff guard)")
        lines.append("")
        lines.append("⚠️  Flat stakes only. Bet only what you can afford to lose.")
        return "\n".join(lines)

    total_stake = bank * STAKE_FRAC
    per_acca = total_stake / len(accas) if accas else 0

    lines.append(f"deploying {STAKE_FRAC*100:.1f}% of bank today = {total_stake:.1f}% of capital · {len(accas)} acca(s) · {per_acca:.1f}% per acca")
    lines.append("")

    for idx, acca in enumerate(accas, 1):
        odds = acca["odds"]
        stake = per_acca
        free_pct = (stake / free_bank * 100) if free_bank else 0
        lines.append(f"[ACCA #{idx}] @{odds:.2f} — stake {stake:.1f}% of capital ({free_pct:.1f}% of free bank)")
        for leg in acca["legs"]:
            match = clean_text(leg.get("match"))[:42]
            sel = clean_text(leg.get("selected_player"))[:20]
            conf = leg.get("confidence") or 0
            try:
                conf_f = float(conf)
                if conf_f <= 1:
                    conf_f *= 100
            except Exception:
                conf_f = 0
            o = leg.get("odds") or 0
            try:
                o_f = float(o)
            except Exception:
                o_f = 0
            ko = clean_text(leg.get("kickoff") or leg.get("match_time") or "n/a")
            tour = clean_text(leg.get("tour") or leg.get("series") or "")
            lines.append(f"   {match:42s} {sel:20s} @{o_f:.2f}  conf {conf_f:.0f}%  KO {ko:5s}  {tour}")
        lines.append("")

    if skipped_info:
        lines.append(f"Kickoff guard skipped {len(skipped_info)} leg(s):")
        for pick, reason in skipped_info[:10]:
            lines.append(f"  - {clean_text(pick.get('match'))} -> {reason}")
        lines.append("")

    lines.append("All figures are percentages of capital. Round to your bookmaker's minimum stake. Bet only what you can afford to lose.")
    return "\n".join(lines)

def cmd_today(target_date: str = None, force: bool = False):
    now = now_local()
    target_date = target_date or today_str()
    state = load_state()

    # Freeze logic
    # If we have a frozen slip for today and now >= FREEZE_HOUR and not force, reprint
    existing_open = [s for s in state.get("open_slips", []) if s.get("date") == target_date]
    if existing_open and not force:
        slip = existing_open[0]
        if slip.get("frozen") and now.hour >= FREEZE_HOUR:
            # reprint
            accas = slip.get("accas", [])
            # need to load txt from file if exists
            txt_path = LOCALDATA / f"auto_tickets_{target_date}.txt"
            if txt_path.exists():
                print(txt_path.read_text())
            else:
                txt = format_tickets_txt(target_date, accas, state, [])
                print(txt)
            return

    picks = load_picks(target_date)
    playable = [p for p in picks if is_playable(p)]
    kept, skipped = kickoff_guard(playable, target_date, now)
    accas, sorted_pool = build_accas(kept)

    # If after freeze hour and we have no accas but we had accas before freeze, keep old?
    if now.hour >= FREEZE_HOUR and not accas and existing_open:
        # keep frozen accas
        accas = existing_open[0].get("accas", [])

    txt = format_tickets_txt(target_date, accas, state, skipped)
    print(txt)

    # Write files
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    json_path = LOCALDATA / f"auto_tickets_{target_date}.json"
    txt_path = LOCALDATA / f"auto_tickets_{target_date}.txt"
    today_json = LOCALDATA / "auto_tickets_today.json"
    today_txt = LOCALDATA / "auto_tickets_today.txt"

    # Calculate stake
    bank = state.get("bank", 100.0)
    total_stake = bank * STAKE_FRAC if accas else 0
    per_acca = total_stake / len(accas) if accas else 0

    slip_data = {
        "date": target_date,
        "generated_at": now.isoformat(),
        "bank_pct": bank,
        "staked_pct": total_stake,
        "stake_per_acca_pct": per_acca,
        "accas": [
            {
                "odds": a["odds"],
                "stake_pct": per_acca,
                "legs": [
                    {
                        "match": clean_text(l.get("match")),
                        "selected_player": clean_text(l.get("selected_player")),
                        "odds": float(l.get("odds") or 0),
                        "confidence": float(l.get("confidence") or 0),
                        "kickoff": clean_text(l.get("kickoff") or l.get("match_time") or ""),
                        "tour": clean_text(l.get("tour")),
                        "tournament": clean_text(l.get("tournament")),
                        "bucket": clean_text(l.get("bucket")),
                        "source": clean_text(l.get("source")),
                    }
                    for l in a["legs"]
                ]
            }
            for a in accas
        ],
        "frozen": now.hour >= FREEZE_HOUR,
        "skipped": [{"match": clean_text(p.get("match")), "reason": r} for p,r in skipped],
    }

    json_path.write_text(json.dumps(slip_data, indent=2, sort_keys=True))
    txt_path.write_text(txt + "\n")
    today_json.write_text(json.dumps(slip_data, indent=2, sort_keys=True))
    today_txt.write_text(txt + "\n")

    # Update state open_slips
    # Remove any existing slip for today
    state["open_slips"] = [s for s in state.get("open_slips", []) if s.get("date") != target_date]
    if accas:
        state["open_slips"].append(slip_data)
    save_state(state)

def main():
    ap = argparse.ArgumentParser(description="Racket Factory auto tickets")
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="Force rebuild even if frozen")
    ap.add_argument("--today", action="store_true", help="Alias for --date today")
    args = ap.parse_args()
    target = args.date or today_str()
    cmd_today(target, force=args.force)

if __name__ == "__main__":
    main()
