#!/usr/bin/env python3
"""Grade Racket Factory auto tickets against warehouse results.

Mirrors Edge Factory's auto_tickets_grade.py but for tennis.

Settles open slips whose matches have finished, updates bank % and history.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOCALDATA = ROOT / "localdata"
STATE_FILE = LOCALDATA / "auto_tickets_state.json"
WAREHOUSE = LOCALDATA / "warehouse.csv.gz"

TZ = ZoneInfo("Africa/Johannesburg")

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
    # Keep initials for strict matching
    return " ".join(parts)

# Alias map for known player name variations (reviewed from public sources)
PLAYER_ALIASES = {
    "irene burillo": "irene burillo escorihuela",
    "burillo escorihuela": "irene burillo escorihuela",
    "irene burillo escorihuela": "irene burillo escorihuela",
}

def apply_alias(name: str) -> str:
    norm = normalize_name(name)
    # Check alias map
    for short, full in PLAYER_ALIASES.items():
        if norm == normalize_name(short) or norm == normalize_name(full) or short in norm or norm in short:
            # If alias matches, return full
            if "burillo" in norm:
                return normalize_name(full)
    return norm

def names_match(a, b) -> bool:
    # STRICT matching: reject Alexander vs Mischa, Smith J vs Smith A, partial doubles
    str_a, str_b = str(a), str(b)
    # Handle doubles: require both sides have "/" and same team size, reject partial
    has_slash_a = "/" in str_a
    has_slash_b = "/" in str_b
    if has_slash_a or has_slash_b:
        if not (has_slash_a and has_slash_b):
            return False  # partial team: one doubles, one singles -> reject
        parts_a = [p.strip() for p in str_a.split("/")]
        parts_b = [p.strip() for p in str_b.split("/")]
        if len(parts_a) != len(parts_b):
            return False
        # Require full team match in any order
        if all(names_match(pa, pb) for pa, pb in zip(parts_a, parts_b)):
            return True
        if all(names_match(pa, pb) for pa, pb in zip(parts_a, reversed(parts_b))):
            return True
        return False

    na = apply_alias(a)
    nb = apply_alias(b)
    if not na or not nb:
        return False
    if na == nb:
        return True

    # Alias for Burillo already handled in apply_alias, but allow substring only for burillo
    if "burillo" in na and "burillo" in nb:
        return True

    ta = na.split()
    tb = nb.split()
    if not ta or not tb:
        return False

    # Helper to extract surname and firstname/initial
    def parse(tokens):
        # tokens normalized lower, no punctuation, initials kept as single letters
        if len(tokens) == 1:
            return (tokens[0], None, None)  # surname, firstname, initial
        if len(tokens) == 2:
            # Cases: "Ivashka I" -> surname first, initial last
            # "I Ivashka" -> initial first, surname last
            # "Ilya Ivashka" -> firstname surname
            if len(tokens[0]) == 1 and len(tokens[1]) > 1:
                return (tokens[1], None, tokens[0])
            if len(tokens[1]) == 1 and len(tokens[0]) > 1:
                return (tokens[0], None, tokens[1])
            # Both full
            return (tokens[-1], tokens[0], None)
        # len >2: could be compound surname
        # Check for trailing initial
        if len(tokens[-1]) == 1:
            # e.g., "Burillo Escorihuela I" -> surname compound, initial
            surname = " ".join(tokens[:-1])
            return (surname, None, tokens[-1])
        if len(tokens[0]) == 1:
            # "I Burillo Escorihuela"
            surname = " ".join(tokens[1:])
            return (surname, None, tokens[0])
        # No initial, assume last token surname, rest firstname(s)
        surname = tokens[-1]
        firstname = tokens[0]
        # For compound surnames like "burillo escorihuela", consider last 2 as surname
        if len(tokens) >= 3 and tokens[-2] in ("burillo", "escorihuela") or "burillo" in " ".join(tokens):
            surname = " ".join(tokens[1:])
        return (surname, firstname, None)

    sur_a, first_a, init_a = parse(ta)
    sur_b, first_b, init_b = parse(tb)

    # Surname must match exactly (or compound contains)
    # For compound, allow exact or last token match if burillo case already handled
    if sur_a != sur_b:
        # Allow compound surname where one is suffix of other (e.g., "burillo escorihuela" vs "escorihuela")
        # Only for burillo already returned True, so strict here
        # Check if surnames share last token but firstnames must also match
        # For strictness, reject if surnames differ
        # Exception: if one surname is two tokens and other is one token that equals last token of compound, require firstname match
        # e.g., "burillo escorihuela" vs "escorihuela" with same firstname -> would be handled by alias
        return False

    # Surnames match, now check firstname/initial compatibility
    # If both have full firstnames, they must match exactly (or one is initial of other)
    if first_a and first_b:
        if first_a == first_b:
            return True
        # If firstnames differ (Alexander vs Mischa), reject
        return False
    if first_a and init_b:
        # first_a full, init_b initial: check initial matches first letter
        if first_a[0] == init_b[0]:
            return True
        return False
    if first_b and init_a:
        if first_b[0] == init_a[0]:
            return True
        return False
    if init_a and init_b:
        # Both initials: must match
        if init_a[0] == init_b[0]:
            return True
        return False
    # Allow surname-only vs surname+firstname/initial for doubles settlement
    # e.g., Cascino / Feng (surnames only) vs Cascino E / Feng S. (surname+initial) should match
    # Full vs full with different firstnames (Alexander vs Mischa) already rejected above
    if (len(ta) == 1 or len(tb) == 1) and sur_a == sur_b:
        return True

    return False

def load_state():
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None

def save_state(state):
    LOCALDATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))

def load_warehouse():
    if not WAREHOUSE.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(WAREHOUSE, low_memory=False)
    except Exception:
        return pd.DataFrame()

def load_additional_results():
    """Load foretennis_results, forebet_results, challenger_results, theoddsapi_scores, predictions for settlement."""
    dfs=[]
    for pattern in ["foretennis_results_*.csv.gz", "forebet_results_*.csv.gz", "challenger_results_*.csv.gz", "theoddsapi_scores_*.csv.gz"]:
        for f in LOCALDATA.glob(pattern):
            try:
                df=pd.read_csv(f, low_memory=False)
                if not df.empty and "winner" in df.columns:
                    # Filter only rows with winner
                    df=df[df["winner"].astype(str).str.strip() != ""]
                    if not df.empty:
                        dfs.append(df)
            except Exception:
                pass
    # NOTE: predictions_foretennis_*.csv.gz is deliberately NOT merged.
    # Its actual_result rows duplicate the backfill output (31/35 dupes by
    # match_id); the 4 unique rows are garbage (player_b="US Open"
    # phantoms + match 1324, whose positional digit reading names the wrong
    # winner — quarantined, TE id=3320729). Results files only.
    if not dfs:
        return pd.DataFrame()
    try:
        return pd.concat(dfs, ignore_index=True, sort=False)
    except Exception:
        return pd.DataFrame()

def settle_leg(leg, df, additional_df=None, target_date: str | None = None):
    # Date-scoped matching (Edge parity) — only consider rows within +/-1 day of target_date
    if df.empty and (additional_df is None or additional_df.empty):
        return None
    match_text = clean_text(leg.get("match"))
    selected = clean_text(leg.get("selected_player"))
    if not match_text or not selected:
        return None
    parts = re.split(r"\s+v(?:s\.?)?\s+", match_text, flags=re.IGNORECASE)
    if len(parts) == 2:
        p_home, p_away = clean_text(parts[0]), clean_text(parts[1])
    else:
        p_home, p_away = "", ""

    def date_filter(frame):
        if frame.empty or not target_date or "match_date" not in frame.columns:
            return frame
        try:
            from datetime import datetime, timedelta
            base = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
            allowed = {base.isoformat(), (base + timedelta(days=1)).isoformat(), (base - timedelta(days=1)).isoformat()}
            return frame[frame["match_date"].astype(str).str[:10].isin(allowed)]
        except Exception:
            return frame

    candidates = date_filter(df)
    additional_candidates = date_filter(additional_df) if additional_df is not None else pd.DataFrame()

    def row_match(row):
        a = clean_text(row.get("player_a"))
        b = clean_text(row.get("player_b"))
        if p_home and p_away:
            return (names_match(a, p_home) and names_match(b, p_away)) or (names_match(a, p_away) and names_match(b, p_home))
        else:
            return names_match(a, selected) or names_match(b, selected)

    matched = candidates[candidates.apply(row_match, axis=1)] if not candidates.empty else pd.DataFrame()
    additional_matched = additional_candidates[additional_candidates.apply(row_match, axis=1)] if not additional_candidates.empty else pd.DataFrame()

    # Only final rows
    def is_final(row):
        winner = clean_text(row.get("winner"))
        if not winner:
            return False
        status = " ".join([clean_text(row.get(k)) for k in ("score","status","_comment")]).lower()
        if any(tok in status for tok in ("live","to finish","suspended","postponed","not started")):
            return False
        return True

    final_rows = matched[matched.apply(is_final, axis=1)] if not matched.empty else pd.DataFrame()
    # FIX: If warehouse has pending row (empty winner), check fallback sources even if warehouse matched
    if final_rows.empty and not additional_matched.empty:
        final_rows = additional_matched[additional_matched.apply(is_final, axis=1)] if not additional_matched.empty else pd.DataFrame()
        if not final_rows.empty:
            matched = additional_matched

    if final_rows.empty:
        # If warehouse had match but no final, and no fallback final, return None (pending)
        # But if there was no warehouse match at all, also return None
        return None

    # Take first
    row = final_rows.iloc[0]
    winner = clean_text(row.get("winner"))
    won = names_match(winner, selected)
    return won

def settle_open_slips(state, df, additional_df=None):
    logs = []
    remaining_open = []
    # Idempotency: don't double-settle already in history, but allow partial history to continue settling pending accas
    # Build map of date -> is_fully_settled (True if any history entry for date is not partial)
    history_fully_settled = {}
    for h in state.get("history", []):
        d = h.get("date")
        if not d:
            continue
        is_partial = bool(h.get("partial"))
        # If any entry for date is not partial, consider it fully settled (or at least don't skip partials)
        if d not in history_fully_settled:
            history_fully_settled[d] = not is_partial
        else:
            # If we have both partial and full, full wins
            if not is_partial:
                history_fully_settled[d] = True
    for slip in state.get("open_slips", []):
        date_str = slip.get("date")
        if date_str in history_fully_settled and history_fully_settled[date_str]:
            logs.append(f"{date_str}: already in history (fully settled), skipping (idempotent)")
            continue
        # Per-acca settlement (Edge parity) — accas settle independently with their own stake
        settled_accas = []
        pending_accas = []
        total_return = 0.0
        total_staked_settled = 0.0
        wins = 0
        for acca in slip.get("accas", []):
            legs = acca.get("legs", [])
            leg_outcomes = []
            all_legs_settled = True
            for leg in legs:
                won = settle_leg(leg, df, additional_df, target_date=date_str)
                if won is None:
                    all_legs_settled = False
                    leg_outcomes.append(None)
                else:
                    leg_outcomes.append(won)
            if not all_legs_settled:
                pending_accas.append(acca)
                continue
            won_acca = all(leg_outcomes) if leg_outcomes else False
            stake_pct = float(acca.get("stake_pct") or slip.get("stake_per_acca_pct") or 0)
            total_staked_settled += stake_pct
            if won_acca:
                total_return += stake_pct * float(acca.get("odds", 1.0))
                wins += 1
            settled_accas.append({
                "odds": acca.get("odds"),
                "won": bool(won_acca),
                "stake_pct": stake_pct,
                "type": acca.get("type"),
                "legs": acca.get("legs", []),
            })
        # If some accas still pending, keep slip open with pending accas
        if pending_accas:
            # If at least one acca settled, move settled to history and keep pending open
            if settled_accas:
                staked = total_staked_settled
                pnl = total_return - staked
                old_bank = state.get("bank", 100.0)
                new_bank = old_bank + pnl
                state["bank"] = new_bank
                hist_entry = {
                    "date": date_str,
                    "staked_pct": staked,
                    "return_pct": total_return,
                    "pnl_pct": pnl,
                    "bank_pct": new_bank,
                    "accas": settled_accas,
                    "partial": True,
                    "pending_accas": len(pending_accas),
                }
                state["history"].append(hist_entry)
                logs.append(f"{date_str}: partially settled {wins}W/{len(settled_accas)-wins}L + {len(pending_accas)} pending  PnL {pnl:+.1f}%  bank {old_bank:.1f}% -> {new_bank:.1f}%")
                # Keep pending as new open slip
                remaining_open.append({
                    "date": date_str,
                    "generated_at": slip.get("generated_at"),
                    "accas": pending_accas,
                    "staked_pct": round(sum(float(a.get("stake_pct") or 0) for a in pending_accas),4),
                    "stake_per_acca_pct": slip.get("stake_per_acca_pct"),
                })
            else:
                remaining_open.append(slip)
                logs.append(f"{date_str}: still open ({len(pending_accas)} accas pending)")
            continue
        # All accas settled
        if not settled_accas:
            remaining_open.append(slip)
            logs.append(f"{date_str}: still open ({len(slip.get('accas',[]))} accas)")
            continue
        staked = total_staked_settled or slip.get("staked_pct", 0)
        pnl = total_return - staked
        old_bank = state.get("bank", 100.0)
        new_bank = old_bank + pnl
        state["bank"] = new_bank
        hist_entry = {
            "date": date_str,
            "staked_pct": staked,
            "return_pct": total_return,
            "pnl_pct": pnl,
            "bank_pct": new_bank,
            "accas": settled_accas,
        }
        state["history"].append(hist_entry)
        logs.append(f"{date_str}: settled {wins}W/{len(settled_accas)-wins}L  PnL {pnl:+.1f}%  bank {old_bank:.1f}% -> {new_bank:.1f}%")
        cycle_base = state.get("cycle_base", 100.0)
        target = cycle_base * 2.0
        if new_bank >= target:
            event = {
                "date": date_str,
                "bank_after_pct": new_bank,
                "gain_pct": new_bank - cycle_base,
                "next_target_pct": new_bank * 2.0,
            }
            state.setdefault("events", []).append(event)
            state["cycle_base"] = new_bank
            logs.append(f"  TAKE-PROFIT bank {new_bank:.1f}% >= target {target:.1f}%")
    state["open_slips"] = remaining_open
    # Write grading log for CI debugging
    try:
        (LOCALDATA / "auto_tickets_grade.log").write_text("\n".join(logs) + "\n")
    except Exception:
        pass
    return logs

def write_performance(state):
    bank = state.get("bank", 100.0)
    base = state.get("base_pct", 100.0)
    cycle_base = state.get("cycle_base", 100.0)
    multiple = bank / base if base else 0
    next_target = cycle_base * 2.0

    history = state.get("history", [])
    accas = [a for h in history for a in h.get("accas", [])]
    wins = sum(1 for a in accas if a.get("won"))
    losses = len(accas) - wins

    lines = []
    lines.append("AUTO-TICKETS (TENNIS) PERFORMANCE — percentages of capital only")
    lines.append("="*62)
    lines.append(f"generated {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"bank {bank:.1f}% of capital (x{multiple:.2f}) · cycle baseline {cycle_base:.1f}% · next take-profit at {next_target:.1f}% (+100% per cycle)")
    if accas:
        lines.append(f"bet-days {len(history)} · accas {wins}W/{losses}L (hit {wins/len(accas):.1%})" if accas else "no settled accas yet")
    else:
        lines.append(f"bet-days {len(history)} · no settled accas yet")
    lines.append(f"open slips {len(state.get('open_slips',[]))} · {len(state.get('events',[]))} take-profit notification(s)")
    lines.append("")
    lines.append("--- bet-days (most recent first) ---")
    for h in reversed(history[-15:]):
        acc_str = " ".join(f"@{a['odds']:.2f}{'W' if a['won'] else 'L'}" for a in h.get("accas", []))
        lines.append(f"  {h['date']}  {acc_str:44s} bank {h['bank_pct']:7.1f}%")
    for e in state.get("events", []):
        lines.append(f"  🔔 {e['date']}: TAKE-PROFIT — +{e['gain_pct']:.1f}% (bank {e['bank_after_pct']:.1f}%, next {e['next_target_pct']:.1f}%)")

    txt = "\n".join(lines)
    (LOCALDATA / "auto_tickets_performance.txt").write_text(txt + "\n")
    (LOCALDATA / "auto_tickets_performance.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "unit": "percent_of_capital",
        "base_pct": base,
        "bank_pct": bank,
        "multiple": multiple,
        "cycle_base_pct": cycle_base,
        "next_take_profit_pct": round(next_target,2),
        "bet_days": len(history),
        "accas": {"wins": wins, "losses": losses},
        "open_slips": state.get("open_slips", []),
        "events": state.get("events", []),
        "history": history,
    }, indent=2, default=str))
    print(txt)

def main():
    state = load_state()
    if not state:
        print("no state yet — run auto_tickets.py first")
        return 0
    df = load_warehouse()
    additional_df = load_additional_results()
    if not additional_df.empty:
        print(f"Loaded {len(additional_df)} additional result rows from foretennis/forebet")
    for line in settle_open_slips(state, df, additional_df):
        print(line)
    write_performance(state)
    save_state(state)
    return 0

if __name__ == "__main__":
    sys.exit(main())
