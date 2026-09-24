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
from racketfactory.settlement import clean_str, settle_selection  # noqa: E402
LOCALDATA = ROOT / "localdata"
STATE_FILE = LOCALDATA / "auto_tickets_state.json"
WAREHOUSE = LOCALDATA / "warehouse.csv.gz"

TZ = ZoneInfo("Africa/Johannesburg")

def _leg_odds_trusted(leg) -> bool:
    """A leg price counts as real only with a named odds source.

    Legs whose odds were estimated from confidence carry odds_source nan
    (or an odds_reject_reason); accas containing them are paper, never
    allowed to move the real bank.
    """
    src = clean_str(leg.get("odds_source"))
    if not src or leg.get("odds_reject_reason"):
        return False
    try:
        return float(leg.get("odds") or 0) > 1.0
    except (TypeError, ValueError):
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
    """Settle one leg via the shared settlement module.

    Returns the outcome string (WON / LOST / PENDING / VOID / CONFLICT) and
    records the evidence (reason + basis rows) on the leg dict so history
    keeps the full settlement trail.
    """
    match_text = clean_str(leg.get("match"))
    selected = clean_str(leg.get("selected_player"))
    if not match_text or not selected:
        leg["_settle_outcome"] = "PENDING"
        leg["_settle_reason"] = "leg missing match/selection"
        return "PENDING"

    def date_filter(frame):
        if frame is None or frame.empty or not target_date or "match_date" not in frame.columns:
            return frame if frame is not None else pd.DataFrame()
        try:
            from datetime import datetime, timedelta
            base = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
            allowed = {base.isoformat(), (base + timedelta(days=1)).isoformat(),
                       (base - timedelta(days=1)).isoformat()}
            return frame[frame["match_date"].astype(str).str[:10].isin(allowed)]
        except Exception:
            return frame

    frames = []
    for frame in (date_filter(df), date_filter(additional_df)):
        if frame is not None and not frame.empty:
            frames.append(frame)
    candidates = []
    for frame in frames:
        for row in frame.to_dict(orient="records"):
            candidates.append({k: (None if (isinstance(v, float) and v != v) else v)
                               for k, v in row.items()})
    if not target_date:
        target_date = clean_str(leg.get("date"))
    result = settle_selection(match_text, selected, candidates, target_date or "")
    leg["_settle_outcome"] = result.outcome
    leg["_settle_reason"] = result.reason
    leg["_settle_basis"] = result.basis
    if result.bare_surname:
        leg["_settle_bare_surname"] = True
    return result.outcome


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
        paper_return = 0.0
        paper_staked = 0.0
        wins = 0
        losses = 0
        paper_wins = 0
        paper_unpriced = 0
        for acca in slip.get("accas", []):
            legs = acca.get("legs", [])
            leg_outcomes = [settle_leg(leg, df, additional_df, target_date=date_str)
                            for leg in legs]
            if any(o in ("PENDING", "CONFLICT") for o in leg_outcomes):
                pending_accas.append(acca)
                for leg, o in zip(legs, leg_outcomes):
                    if o in ("PENDING", "CONFLICT"):
                        logs.append(f"  {date_str} {acca.get('type')}: leg {o}: "
                                    f"{leg.get('match')} -- {leg.get('_settle_reason')}")
                continue
            paper = any(not _leg_odds_trusted(leg) for leg in legs)
            stake_pct = float(acca.get("stake_pct") or slip.get("stake_per_acca_pct") or 0)
            try:
                acca_odds = float(acca.get("odds") or 0)
            except (TypeError, ValueError):
                acca_odds = 0.0
            voids = [leg for leg, o in zip(legs, leg_outcomes) if o == "VOID"]
            refunded = False
            priceable = acca_odds > 1.0
            if any(o == "LOST" for o in leg_outcomes):
                won_acca, acca_return = False, (0.0 if priceable else None)
            elif voids and len(voids) == len(legs):
                won_acca, refunded = False, True
                acca_return = stake_pct if priceable else None
            elif voids and priceable:
                eff, repriceable = acca_odds, True
                for leg in voids:
                    try:
                        lo = float(leg.get("odds") or 0)
                    except (TypeError, ValueError):
                        lo = 0.0
                    if lo > 1.0:
                        eff /= lo
                    else:
                        repriceable = False
                if not repriceable:
                    pending_accas.append(acca)
                    logs.append(f"  {date_str} {acca.get('type')}: void leg without "
                                f"valid odds, cannot reprice -- acca held open")
                    continue
                won_acca, acca_odds, acca_return = True, round(eff, 4), stake_pct * eff
            elif priceable:
                won_acca, acca_return = True, stake_pct * acca_odds
            elif paper:
                # Unpriced paper acca: legs decided, no market price exists.
                # Settle W/L for hit-rate only; never touches any bank.
                won_acca, acca_return = True, None
            else:
                pending_accas.append(acca)
                logs.append(f"  {date_str} {acca.get('type')}: won legs but acca has "
                            f"no valid odds -- held open, never pays on fiction")
                continue
            unpriced = acca_return is None
            if unpriced:
                # Hit-rate only: excluded from every bank calculation.
                paper_wins += int(won_acca)
                paper_unpriced += 1
            elif paper:
                paper_staked += stake_pct
                paper_return += acca_return
                paper_wins += int(won_acca)
            else:
                total_staked_settled += stake_pct
                total_return += acca_return
                wins += int(won_acca)
                losses += int(not won_acca and not refunded)
            settled_accas.append({
                "odds": acca_odds,
                "won": bool(won_acca),
                "stake_pct": stake_pct,
                "return_pct": acca_return,
                "type": acca.get("type"),
                "legs": legs,
                "paper": paper,
                "refunded": refunded,
                "unpriced": unpriced,
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
                old_paper_bank = state.get("paper_bank", 100.0)
                paper_pnl = paper_return - paper_staked
                state["paper_bank"] = old_paper_bank + paper_pnl
                hist_entry = {
                    "date": date_str,
                    "staked_pct": staked,
                    "return_pct": total_return,
                    "pnl_pct": pnl,
                    "bank_pct": new_bank,
                    "paper_staked_pct": paper_staked,
                    "paper_return_pct": paper_return,
                    "paper_pnl_pct": paper_pnl,
                    "paper_bank_pct": state["paper_bank"],
                    "accas": settled_accas,
                    "partial": True,
                    "pending_accas": len(pending_accas),
                }
                state["history"].append(hist_entry)
                logs.append(f"{date_str}: partially settled {wins}W/{losses}L "
                            f"({paper_wins} paper wins, {paper_unpriced} unpriced) + {len(pending_accas)} pending  "
                            f"PnL {pnl:+.1f}% (paper {paper_pnl:+.1f}%)  "
                            f"bank {old_bank:.1f}% -> {new_bank:.1f}%")
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
        staked = total_staked_settled
        pnl = total_return - staked
        old_bank = state.get("bank", 100.0)
        new_bank = old_bank + pnl
        state["bank"] = new_bank
        old_paper_bank = state.get("paper_bank", 100.0)
        paper_pnl = paper_return - paper_staked
        state["paper_bank"] = old_paper_bank + paper_pnl
        hist_entry = {
            "date": date_str,
            "staked_pct": staked,
            "return_pct": total_return,
            "pnl_pct": pnl,
            "bank_pct": new_bank,
            "paper_staked_pct": paper_staked,
            "paper_return_pct": paper_return,
            "paper_pnl_pct": paper_pnl,
            "paper_bank_pct": state["paper_bank"],
            "accas": settled_accas,
        }
        state["history"].append(hist_entry)
        logs.append(f"{date_str}: settled {wins}W/{losses}L "
                    f"({paper_wins} paper wins, {paper_unpriced} unpriced)  PnL {pnl:+.1f}% (paper {paper_pnl:+.1f}%)  "
                    f"bank {old_bank:.1f}% -> {new_bank:.1f}%")
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

# Odds bands for the staked-leg price ledger (lower bound inclusive): does the
# 1.30 per-leg floor (ml.get_min_odds_base) sit in the right place?
PRICE_BANDS = (("<1.30", 0.0, 1.30), ("1.30-1.59", 1.30, 1.60), ("1.60+", 1.60, float("inf")))


def _band_row(label, legs):
    """n, W-L, hit%, avg odds, breakeven% (=100/avg odds), spread for (odds, won) legs."""
    n = len(legs)
    wins = sum(1 for _, won in legs if won)
    row = {"band": label, "n": n, "wins": wins, "losses": n - wins,
           "hit_pct": None, "avg_odds": None, "breakeven_pct": None, "spread_pp": None}
    if n:
        avg = sum(o for o, _ in legs) / n
        hit, be = round(100.0 * wins / n, 1), round(100.0 / avg, 1)
        row.update(hit_pct=hit, avg_odds=round(avg, 3), breakeven_pct=be,
                   spread_pp=round(hit - be, 1))
    return row


def price_band_ledger(history):
    """Hit% vs breakeven% per odds band over STAKED (non-paper) acca legs.

    Every history entry settles an acca exactly once (partial passes carry
    only the newly settled accas), so raw history counts each leg once, the
    same basis as the REAL W/L tally. A leg needs numeric odds > 1.0 and a
    WON/LOST _settle_outcome (VOID/PENDING/CONFLICT carry no verdict).
    """
    legs = []
    for h in history:
        for a in h.get("accas", []):
            if a.get("paper"):
                continue
            for leg in a.get("legs", []):
                outcome = leg.get("_settle_outcome")
                if outcome not in ("WON", "LOST"):
                    continue
                try:
                    odds = float(leg.get("odds"))
                except (TypeError, ValueError):
                    continue
                if odds > 1.0:  # also rejects NaN
                    legs.append((odds, outcome == "WON"))
    return {
        "bands": [_band_row(label, [x for x in legs if lo <= x[0] < hi])
                  for label, lo, hi in PRICE_BANDS],
        ">=1.30": _band_row(">=1.30", [x for x in legs if x[0] >= 1.30]),
        "all": _band_row("all", legs),
    }


def write_performance(state):
    bank = state.get("bank", 100.0)
    base = state.get("base_pct", 100.0)
    cycle_base = state.get("cycle_base", 100.0)
    multiple = bank / base if base else 0
    next_target = cycle_base * 2.0

    history = state.get("history", [])
    accas = [a for h in history for a in h.get("accas", [])]
    real = [a for a in accas if not a.get("paper")]
    paper = [a for a in accas if a.get("paper")]
    wins = sum(1 for a in real if a.get("won"))
    losses = len(real) - wins
    paper_wins = sum(1 for a in paper if a.get("won"))
    paper_bank = state.get("paper_bank")
    paper_pnl_total = round(sum(float(h.get("paper_pnl_pct") or 0) for h in history), 4)

    # Merge history by date for display and counting. A bet-day can settle in
    # several passes: each grading run settles whichever accas have final
    # results, banks them into a "partial" history entry, and carries the rest
    # over as a new open slip. Every pass's accas belong to the same bet-day,
    # so concatenate them and keep the last bank snapshot (end-of-day) instead
    # of showing only the newest pass (which hid earlier wins/losses, e.g.
    # 2026-09-23 showed only @1.88L while the @2.62W sat in the partial entry).
    merged_by_date = {}
    ordered_dates = []
    for h in history:
        d = h.get("date")
        if d not in merged_by_date:
            merged_by_date[d] = {"date": d, "accas": [],
                                 "bank_pct": h.get("bank_pct"), "paper_pnl_pct": 0.0}
            ordered_dates.append(d)
        m = merged_by_date[d]
        m["accas"].extend(h.get("accas", []))
        if h.get("bank_pct") is not None:
            m["bank_pct"] = h.get("bank_pct")  # later pass = end-of-day bank
        if h.get("paper_pnl_pct"):
            m["paper_pnl_pct"] += float(h["paper_pnl_pct"])
    unique_history = [merged_by_date[d] for d in ordered_dates]
    unique_days = len(ordered_dates)

    lines = []
    lines.append("AUTO-TICKETS (TENNIS) PERFORMANCE — percentages of capital only")
    lines.append("="*62)
    lines.append(f"generated {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"bank {bank:.1f}% of capital (x{multiple:.2f}) · cycle baseline {cycle_base:.1f}% · next take-profit at {next_target:.1f}% (+100% per cycle)")
    if real:
        lines.append(f"bet-days {unique_days} (unique) · REAL accas {wins}W/{losses}L (hit {wins/len(real):.1%})")
    else:
        lines.append(f"bet-days {unique_days} (unique) · no settled REAL accas yet")
    if paper:
        lines.append(f"PAPER accas (phantom odds, excluded from bank): {paper_wins}W/{len(paper)-paper_wins}L  paper-PnL {paper_pnl_total:+.1f}%"
                     + (f"  paper-bank {paper_bank:.1f}%" if paper_bank is not None else ""))
    lines.append(f"open slips {len(state.get('open_slips',[]))} · {len(state.get('events',[]))} take-profit notification(s)")
    lines.append("")
    lines.append("--- bet-days (most recent first) ---")
    # Latest 15 unique bet-days (already merged per date, newest first)
    deduped = list(reversed(unique_history))[:15]
    for h in deduped:
        def fmt(a):
            try:
                o = float(a.get("odds") or 0)
            except Exception:
                o = 0
            if o <= 1.0:
                return f"PAPER-{'W' if a.get('won') else 'L'}●"
            return f"@{o:.2f}{'W' if a['won'] else 'L'}{'●' if a.get('paper') else ''}{'~' if a.get('refunded') else ''}"
        acc_str = " ".join(fmt(a) for a in h.get("accas", []))
        extra = ""
        if h.get("paper_pnl_pct"):
            extra = f" (paper {float(h['paper_pnl_pct']):+.1f}%)"
        lines.append(f"  {h['date']}  {acc_str:44s} bank {h['bank_pct']:7.1f}%{extra}")
    for e in state.get("events", []):
        lines.append(f"  🔔 {e['date']}: TAKE-PROFIT — +{e['gain_pct']:.1f}% (bank {e['bank_after_pct']:.1f}%, next {e['next_target_pct']:.1f}%)")

    price_bands = price_band_ledger(history)
    lines.append("")
    lines.append("--- STAKED legs by price band: hit% vs breakeven% (paper excluded) ---")
    lines.append(f"  {'band':<10}{'n':>4}{'W-L':>8}{'hit%':>8}{'avg odds':>10}{'breakeven':>11}{'spread':>10}")
    for r in price_bands["bands"] + [price_bands[">=1.30"], price_bands["all"]]:
        if r["n"]:
            stats = (f"{r['hit_pct']:>7.1f}%{r['avg_odds']:>10.3f}{r['breakeven_pct']:>10.1f}%"
                     f"{r['spread_pp']:>+8.1f}pp")
        else:
            stats = f"{'—':>8}{'—':>10}{'—':>11}{'—':>10}"
        wl = f"{r['wins']}-{r['losses']}"
        lines.append(f"  {r['band']:<10}{r['n']:>4}{wl:>8}{stats}")

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
        "paper_accas": {"wins": paper_wins, "losses": len(paper) - paper_wins,
                        "pnl_pct": paper_pnl_total, "bank_pct": paper_bank},
        "price_bands": price_bands,
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
