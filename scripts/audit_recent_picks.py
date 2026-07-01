#!/usr/bin/env python3
"""Racket Factory — Recent picks audit against settled warehouse results.

Generates highly detailed Markdown tracking reports and JSON summaries,
matching Edge-Factory parity.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LOCALDATA = ROOT / "localdata"
WAREHOUSE = LOCALDATA / "warehouse.csv.gz"
DEFAULT_LOCAL_TZ = "Africa/Johannesburg"


@dataclass
class SettledPick:
    date: str
    tour: str
    series: str
    surface: str
    bucket: str
    source: str
    match: str
    selected_player: str
    winner: str
    won: bool
    odds: float | None
    pnl: float | None
    selected_sets_won: int | None = None
    selected_sets_lost: int | None = None
    selected_won_any_set: bool | None = None
    selected_won_set1: bool | None = None
    selected_won_set2: bool | None = None
    selected_won_set3: bool | None = None
    ledger_kind: str = "official"


def local_today() -> str:
    run_as_of = str(os.environ.get("RACKET_FACTORY_RUN_AS_OF") or "").strip()
    if run_as_of:
        try:
            dt = datetime.fromisoformat(run_as_of.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(ZoneInfo(DEFAULT_LOCAL_TZ)).date().isoformat()
            return dt.date().isoformat()
        except Exception:
            pass
    try:
        return datetime.now(ZoneInfo(DEFAULT_LOCAL_TZ)).date().isoformat()
    except Exception:
        return date.today().isoformat()


def daterange(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)




def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>", "nat"}:
        return ""
    return text


def normalize_name(value: Any) -> str:
    text = clean_text(value)
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


def name_tokens(value: Any) -> set[str]:
    return {t for t in normalize_name(value).split() if t and t != "/"}


def surname_tail(value: Any) -> tuple[str, ...]:
    toks = [t for t in normalize_name(value).split() if t and t != "/"]
    if not toks:
        return tuple()
    return tuple(toks[-2:]) if len(toks) >= 2 else (toks[-1],)


def names_match(a: Any, b: Any) -> bool:
    na = normalize_name(a)
    nb = normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if surname_tail(a) and surname_tail(a) == surname_tail(b):
        return True

    ta_list = [t for t in na.split() if t and t != "/"]
    tb_list = [t for t in nb.split() if t and t != "/"]
    if not ta_list or not tb_list:
        return False

    # Initial-aware matching for source aliases:
    #   "J. M. Cerundolo"  <-> "Juan Manuel Cerundolo"
    #   "A. Davidovich Fokina" <-> "Alejandro Davidovich Fokina"
    #
    # Require the final surname token to agree, then allow preceding initials
    # to match full forenames/middle names by first letter.  This is deliberately
    # narrower than pure surname matching to avoid merging different players
    # who share a common surname.
    if ta_list[-1] == tb_list[-1]:
        pre_a = ta_list[:-1]
        pre_b = tb_list[:-1]

        full_overlap = {x for x in pre_a if len(x) > 1} & {x for x in pre_b if len(x) > 1}
        if full_overlap:
            return True

        initials_a = [x for x in pre_a if len(x) == 1]
        initials_b = [x for x in pre_b if len(x) == 1]

        if initials_a and all(any(y.startswith(x) for y in pre_b) for x in initials_a):
            return True
        if initials_b and all(any(y.startswith(x) for y in pre_a) for x in initials_b):
            return True

    ta = set(ta_list)
    tb = set(tb_list)
    overlap = ta & tb
    return bool(overlap) and len(overlap) >= min(len(ta), len(tb))


def pick_match_date(pick: dict[str, Any]) -> str:
    for key in ("match_date", "date", "kickoff", "match_time", "time", "start_time", "ko"):
        val = pick.get(key)
        text = clean_text(val)
        if len(text) >= 10:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            if m:
                return m.group(1)
    return ""


def pick_players(pick: dict[str, Any]) -> tuple[str, str]:
    home = clean_text(pick.get("player_home") or pick.get("player_a"))
    away = clean_text(pick.get("player_away") or pick.get("player_b"))
    if home and away:
        return home, away
    match = clean_text(pick.get("match"))
    if match:
        parts = re.split(r"\s+v(?:s\.)?\s+", match, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            return clean_text(parts[0]), clean_text(parts[1])
    return home, away


def archived_picks_path(day: str) -> Path:
    return LOCALDATA / f"picks_{day}.json"


def forecast_picks_path(day: str) -> Path:
    return LOCALDATA / f"picks_forecast_{day}.json"


def _load_pick_rows_from_path(path: Path, day: str, ledger_kind: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        r.setdefault("date", day)
        r["ledger_kind"] = ledger_kind
        out.append(r)
    return out


def load_archived_picks(start: str, end: str, *, ledger_kind: str = "official") -> list[dict[str, Any]]:
    """Load official same-day ledgers or forecast ledgers through one audit path.

    official:
      localdata/picks_YYYY-MM-DD.json

    forecast:
      localdata/picks_forecast_YYYY-MM-DD.json
    """
    out: list[dict[str, Any]] = []

    for day in daterange(start, end):
        if ledger_kind in {"official", "both"}:
            for row in _load_pick_rows_from_path(archived_picks_path(day), day, "official"):
                # Defensive: if an older run wrote forecast rows into the official
                # date archive, do not count them as official.
                if str(row.get("ledger_kind") or "").lower() == "forecast":
                    continue
                row["ledger_kind"] = "official"
                out.append(row)

        if ledger_kind in {"forecast", "both"}:
            out.extend(_load_pick_rows_from_path(forecast_picks_path(day), day, "forecast"))

    return out


def load_warehouse_df(warehouse_path: Path) -> pd.DataFrame:
    if not warehouse_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(warehouse_path, low_memory=False)
    except Exception:
        return pd.DataFrame()



def _parse_int_pair_from_token(token: str) -> tuple[int, int] | None:
    """Parse set score token like '7-6', '6-7(5)', '10-8'."""
    nums = re.findall(r"\d+", str(token or ""))
    if len(nums) < 2:
        return None
    try:
        return int(nums[0]), int(nums[1])
    except ValueError:
        return None


def _winner_side_from_row_values(winner: str, player_a: str, player_b: str) -> str | None:
    if names_match(winner, player_a):
        return "player_a"
    if names_match(winner, player_b):
        return "player_b"
    return None


def _result_row_is_final(row: pd.Series) -> bool:
    """Return False for live/suspended/to-finish rows masquerading as results.

    Settlement must only count final outcomes.  Some source rows can contain a
    winner-like value while the score/status still says the match is live,
    suspended, or "to finish" the next day.
    """
    winner = clean_text(row.get("winner"))
    if not winner:
        return False

    status_parts = []
    for key in ("score", "status", "match_status", "state", "_comment"):
        val = clean_text(row.get(key))
        if val:
            status_parts.append(val)

    status_text = " ".join(status_parts).lower()

    if any(
        token in status_text
        for token in (
            "to finish",
            "leads",
            "live",
            "in progress",
            "suspended",
            "interrupted",
            "not started",
            "postponed",
        )
    ):
        return False

    completed = clean_text(row.get("completed")).lower()
    if completed in {"false", "0", "no"}:
        return False

    score = clean_text(row.get("score")).lower()
    if score:
        compact_score = score.replace("—", "-").replace("–", "-").strip()
        if compact_score in {"-", "(l)", "- (l)", "(live)", "- (live)"}:
            return False

        # Ordered game-score rows must not include a live point score such as
        # "40-30" after completed sets.  Example bad row:
        # "6-3 7-6 40-30" = match in progress, not final.
        if re.search(r"\b(?:0|15|30|40|ad)-(?:0|15|30|40|ad)\b", score):
            return False

        score_perspective = clean_text(row.get("_score_perspective") or row.get("score_perspective"))
        if score_perspective == "player_a_sets-player_b_sets":
            digits = [int(ch) for ch in re.findall(r"\d", score)]
            if not digits:
                return False

            player_a = clean_text(row.get("player_a"))
            player_b = clean_text(row.get("player_b"))
            winner_side = _winner_side_from_row_values(winner, player_a, player_b)
            if winner_side not in {"player_a", "player_b"}:
                return False

            if len(digits) >= 2:
                a_sets, b_sets = digits[0], digits[1]
            else:
                # CSV coercion can strip a leading zero, e.g. "02" -> "2".
                # That can be valid for best-of-3, but not for ATP Grand Slam
                # men's singles where the winner must reach 3 sets.
                if winner_side == "player_a":
                    a_sets, b_sets = digits[0], 0
                else:
                    a_sets, b_sets = 0, digits[0]

            if a_sets == b_sets:
                return False

            winner_sets = a_sets if winner_side == "player_a" else b_sets
            match_text = " ".join(
                clean_text(row.get(k))
                for k in ("tour", "tournament", "series", "_series")
            ).lower()

            if winner_sets < 2:
                return False

            if (
                "atp" in match_text
                and any(x in match_text for x in ("grand slam", "wimbledon", "french open", "australian open", "us open"))
                and winner_sets < 3
            ):
                return False

        has_digit = bool(re.search(r"\d", score))
        has_final_marker = bool(re.search(r"\b(w/o|wo|walkover|ret\.?|retired|default)\b", score))
        if not has_digit and not has_final_marker:
            return False

    return True


def _set_diagnostics_from_score(
    score_value: Any,
    score_perspective: Any,
    selected_side: str | None,
    winner_side: str | None,
) -> dict[str, Any]:
    """Return set diagnostics for selected side.

    Supported score perspectives:
      - player_a_games-player_b_games:
          Ordered set scores like '7-6 3-6 1-6'. Can compute set 1/2/3.
      - player_a_sets-player_b_sets:
          Set totals like '20', '02', '21', '12'. Can compute sets won/lost
          and won_any_set, but not set order.

    These diagnostics are informational. They are not ROI because set-market
    odds are not captured.
    """
    out = {
        "selected_sets_won": None,
        "selected_sets_lost": None,
        "selected_won_any_set": None,
        "selected_won_set1": None,
        "selected_won_set2": None,
        "selected_won_set3": None,
    }
    if selected_side not in {"player_a", "player_b"}:
        return out

    score = clean_text(score_value)
    perspective = clean_text(score_perspective)

    if not score:
        return out

    # Forebet ordered set scores: '7-6 3-6 1-6'
    if perspective == "player_a_games-player_b_games" or "-" in score:
        set_tokens = [tok for tok in re.split(r"\s+", score.strip()) if tok]
        selected_set_results: list[bool] = []

        for token in set_tokens:
            pair = _parse_int_pair_from_token(token)
            if pair is None:
                continue
            a_games, b_games = pair
            if a_games == b_games:
                continue
            set_winner = "player_a" if a_games > b_games else "player_b"
            selected_set_results.append(set_winner == selected_side)

        if selected_set_results:
            won = sum(1 for x in selected_set_results if x)
            lost = len(selected_set_results) - won
            out["selected_sets_won"] = won
            out["selected_sets_lost"] = lost
            out["selected_won_any_set"] = won > 0
            if len(selected_set_results) >= 1:
                out["selected_won_set1"] = selected_set_results[0]
            if len(selected_set_results) >= 2:
                out["selected_won_set2"] = selected_set_results[1]
            if len(selected_set_results) >= 3:
                out["selected_won_set3"] = selected_set_results[2]
        return out

    # ForeTennis set-total strings: '20', '02', '21', '12'.
    # Pandas may read '02' as '2', so if only one digit remains, use winner_side.
    digits = [int(ch) for ch in re.findall(r"\d", score)]
    if len(digits) >= 2:
        a_sets, b_sets = digits[0], digits[1]
    elif len(digits) == 1 and winner_side in {"player_a", "player_b"}:
        # Interpret single digit as winner set count, loser zero after CSV
        # coercion stripped leading zero from e.g. '02' -> '2'.
        if winner_side == "player_a":
            a_sets, b_sets = digits[0], 0
        else:
            a_sets, b_sets = 0, digits[0]
    else:
        return out

    selected_sets = a_sets if selected_side == "player_a" else b_sets
    other_sets = b_sets if selected_side == "player_a" else a_sets

    out["selected_sets_won"] = selected_sets
    out["selected_sets_lost"] = other_sets
    out["selected_won_any_set"] = selected_sets > 0
    return out

def settle_pick(pick: dict[str, Any], df: pd.DataFrame) -> SettledPick | None:
    match_date = pick_match_date(pick)
    player_home, player_away = pick_players(pick)
    selected_player = clean_text(pick.get("selected_player") or pick.get("selection") or pick.get("pick_player"))

    if not match_date or not player_home or not player_away or not selected_player:
        return None

    if df.empty:
        return None

    # Filter by date first, then use tolerant player matching. Warehouse rows
    # can be TennisData style ("Ostapenko J."), OddsPortal style, or full-name
    # live rows; exact string equality is too brittle for settlement.
    #
    # Source publish dates and warehouse match dates can differ by one day
    # around overnight / timezone boundaries.  This is common for early
    # PredixSport rows and Grand Slam order-of-play timing.  Prefer exact-date
    # matches, but allow +/- 1 day so those picks settle once results land.
    date_col = df["match_date"].astype(str).str[:10] if "match_date" in df.columns else pd.Series(dtype=object)
    try:
        base_match_date = datetime.strptime(match_date, "%Y-%m-%d").date()
        candidate_date_rank = {
            base_match_date.isoformat(): 0,
            (base_match_date + timedelta(days=1)).isoformat(): 1,
            (base_match_date - timedelta(days=1)).isoformat(): 1,
        }
    except ValueError:
        candidate_date_rank = {match_date: 0}

    candidates = df[date_col.isin(candidate_date_rank.keys())].copy()
    if candidates.empty:
        return None

    def row_is_match(row: pd.Series) -> bool:
        a = row.get("player_a", "")
        b = row.get("player_b", "")
        normal = names_match(a, player_home) and names_match(b, player_away)
        reverse = names_match(a, player_away) and names_match(b, player_home)
        return bool(normal or reverse)

    subset = candidates[candidates.apply(row_is_match, axis=1)]
    if subset.empty or "winner" not in subset.columns:
        return None

    if "match_date" in subset.columns:
        subset = subset.copy()
        subset["_settlement_date_rank"] = (
            subset["match_date"].astype(str).str[:10].map(candidate_date_rank).fillna(99)
        )
        subset = subset.sort_values("_settlement_date_rank")

    settled_rows = subset[subset["winner"].notna() & (~subset["winner"].astype(str).str.strip().isin(["", "nan", "<NA>", "None"]))]
    if settled_rows.empty:
        return None

    # Do not settle live/suspended/to-finish rows.  A winner-like field is not
    # enough if the score/status still shows the match was not final.
    settled_rows = settled_rows[settled_rows.apply(_result_row_is_final, axis=1)]
    if settled_rows.empty:
        return None

    # Prefer rows with usable odds for ROI; otherwise any settled result can
    # still score hit-rate and use captured pick odds as fallback.
    if {"odds_a", "odds_b"}.issubset(set(settled_rows.columns)):
        priced = settled_rows[
            pd.to_numeric(settled_rows["odds_a"], errors="coerce").notna()
            | pd.to_numeric(settled_rows["odds_b"], errors="coerce").notna()
        ]
        if not priced.empty:
            settled_rows = priced

    row = settled_rows.iloc[0]
    winner = clean_text(row.get("winner"))
    won = names_match(winner, selected_player)

    odds = None
    selected_side_norm = None
    player_a_val = clean_text(row.get("player_a"))
    player_b_val = clean_text(row.get("player_b"))

    if names_match(selected_player, player_a_val):
        selected_side_norm = "player_a"
        odds = row.get("odds_a")
    elif names_match(selected_player, player_b_val):
        selected_side_norm = "player_b"
        odds = row.get("odds_b")
    else:
        side = clean_text(pick.get("selected_side"))
        if side in ("player_a", "1"):
            selected_side_norm = "player_a"
            odds = row.get("odds_a")
        elif side in ("player_b", "2"):
            selected_side_norm = "player_b"
            odds = row.get("odds_b")

    try:
        odds = float(odds) if odds is not None and str(odds).strip() not in {"", "nan", "<NA>", "None"} else None # type: ignore
        if odds is not None and odds <= 1.0: # type: ignore
            odds = None
    except (TypeError, ValueError, AttributeError):
        odds = None

    # ROI must use the archived pick price, not an arbitrary warehouse side.
    # Warehouse odds can be closing prices from another source, or side-misaligned
    # after tolerant name matching.  They are only a last-resort fallback for
    # genuinely priced picks.  NO_ODDS buckets must remain unpriced.
    bucket_text = str(pick.get("bucket") or "")
    pick_odds = None
    try:
        odds_val = pick.get("odds") or pick.get("decimal_odds")
        pick_odds = float(odds_val) if odds_val is not None and str(odds_val).strip() not in {"", "nan", "<NA>", "None"} else None # type: ignore
        if pick_odds is not None and pick_odds <= 1.0: # type: ignore
            pick_odds = None
    except (TypeError, ValueError, AttributeError):
        pick_odds = None

    if "NO_ODDS" in bucket_text.upper():
        odds = None
    elif pick_odds is not None:
        odds = pick_odds

    pnl = None if odds is None else (odds - 1.0 if won else -1.0)

    winner_side = _winner_side_from_row_values(winner, player_a_val, player_b_val)
    set_diag = _set_diagnostics_from_score(
        row.get("score"),
        row.get("_score_perspective"),
        selected_side_norm,
        winner_side,
    )

    return SettledPick(
        date=match_date,
        tour=str(pick.get("tour") or "UNKNOWN"),
        series=str(pick.get("series") or pick.get("_series") or "UNKNOWN"),
        surface=str(pick.get("surface") or pick.get("_surface") or "UNKNOWN"),
        bucket=str(pick.get("bucket") or "UNKNOWN"),
        source=str(pick.get("source") or "UNKNOWN"),
        match=str(pick.get("match") or f"{player_home} vs {player_away}"),
        selected_player=selected_player,
        winner=winner,
        won=won,
        odds=odds,
        pnl=pnl,
        selected_sets_won=set_diag.get("selected_sets_won"),
        selected_sets_lost=set_diag.get("selected_sets_lost"),
        selected_won_any_set=set_diag.get("selected_won_any_set"),
        selected_won_set1=set_diag.get("selected_won_set1"),
        selected_won_set2=set_diag.get("selected_won_set2"),
        selected_won_set3=set_diag.get("selected_won_set3"),
        ledger_kind=str(pick.get("ledger_kind") or "official"),
    )


def summarize_scored(rows: list[SettledPick]) -> dict[str, Any]:
    settled = len(rows)
    wins = sum(1 for row in rows if row.won)
    with_odds = [row for row in rows if row.pnl is not None]
    pnl_sum = sum(float(row.pnl or 0.0) for row in with_odds)

    set_rows = [row for row in rows if row.selected_won_any_set is not None]
    set1_rows = [row for row in rows if row.selected_won_set1 is not None]
    set2_rows = [row for row in rows if row.selected_won_set2 is not None]
    set3_rows = [row for row in rows if row.selected_won_set3 is not None]

    return {
        "settled_picks": settled,
        "wins": wins,
        "hit_rate": round(wins / settled, 6) if settled else None,
        "priced_picks": len(with_odds),
        "roi": round(pnl_sum / len(with_odds), 6) if with_odds else None,
        "set_diagnostic_picks": len(set_rows),
        "selected_won_any_set": sum(1 for row in set_rows if row.selected_won_any_set),
        "selected_won_any_set_rate": round(sum(1 for row in set_rows if row.selected_won_any_set) / len(set_rows), 6) if set_rows else None,
        "selected_won_set1": sum(1 for row in set1_rows if row.selected_won_set1),
        "selected_won_set1_rate": round(sum(1 for row in set1_rows if row.selected_won_set1) / len(set1_rows), 6) if set1_rows else None,
        "selected_won_set2": sum(1 for row in set2_rows if row.selected_won_set2),
        "selected_won_set2_rate": round(sum(1 for row in set2_rows if row.selected_won_set2) / len(set2_rows), 6) if set2_rows else None,
        "selected_won_set3": sum(1 for row in set3_rows if row.selected_won_set3),
        "selected_won_set3_rate": round(sum(1 for row in set3_rows if row.selected_won_set3) / len(set3_rows), 6) if set3_rows else None,
    }


def summarize_by(rows: list[SettledPick], attr: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[SettledPick]] = defaultdict(list)
    for row in rows:
        grouped[str(getattr(row, attr) or "UNKNOWN")].append(row)
    return {name: summarize_scored(group_rows) for name, group_rows in sorted(grouped.items())}


def build_report(
    start: str,
    end: str,
    warehouse_path: Path,
    *,
    include_same_day: bool = False,
    ledger_kind: str = "official",
) -> dict[str, Any]:
    picks = load_archived_picks(start, end, ledger_kind=ledger_kind)
    df = load_warehouse_df(warehouse_path)
    settled_rows: list[SettledPick] = []
    archived_dates = sorted({str(p.get("date") or "")[:10] for p in picks if p.get("date")})
    today_local = local_today()
    same_day_excluded = 0

    for pick in picks:
        pick_date = str(pick.get("date") or "")[:10]
        if not include_same_day and pick_date >= today_local:
            same_day_excluded += 1
            continue
        settled = settle_pick(pick, df)
        if settled is not None:
            settled_rows.append(settled)

    return {
        "start": start,
        "end": end,
        "archived_pick_rows": len(picks),
        "archived_pick_dates": archived_dates,
        "same_day_excluded": same_day_excluded,
        "same_day_cutoff": today_local,
        "include_same_day": include_same_day,
        "ledger_kind": ledger_kind,
        "overall": summarize_scored(settled_rows),
        "by_ledger_kind": summarize_by(settled_rows, "ledger_kind"),
        "by_tour": summarize_by(settled_rows, "tour"),
        "by_series": summarize_by(settled_rows, "series"),
        "by_surface": summarize_by(settled_rows, "surface"),
        "by_bucket": summarize_by(settled_rows, "bucket"),
        "by_source": summarize_by(settled_rows, "source"),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    overall = report.get("overall", {})
    lines = [
        f"# Racket Factory — Recent picks audit ({report['start']} to {report['end']})",
        "",
        "## Overall",
        "",
        f"- archived pick rows: {report.get('archived_pick_rows', 0)}",
        f"- archived pick dates: {len(report.get('archived_pick_dates', []))}",
        f"- settled picks: {overall.get('settled_picks', 0)}",
        f"- wins: {overall.get('wins', 0)}",
        f"- hit rate: {overall.get('hit_rate')}",
        f"- priced picks: {overall.get('priced_picks', 0)}",
        f"- ROI: {overall.get('roi')}",
        f"- set diagnostic picks: {overall.get('set_diagnostic_picks', 0)}",
        f"- selected won any set: {overall.get('selected_won_any_set')} ({overall.get('selected_won_any_set_rate')})",
        f"- selected won set 1: {overall.get('selected_won_set1')} ({overall.get('selected_won_set1_rate')})",
        f"- selected won set 2: {overall.get('selected_won_set2')} ({overall.get('selected_won_set2_rate')})",
        f"- selected won set 3: {overall.get('selected_won_set3')} ({overall.get('selected_won_set3_rate')})",
        "",
        "## Settlement policy",
        "",
        f"- ledger kind: {report.get('ledger_kind')}",
        f"- include same-day picks: {report.get('include_same_day')}",
        f"- same-day cutoff date: {report.get('same_day_cutoff')}",
        f"- same-day rows excluded: {report.get('same_day_excluded', 0)}",
        "- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed",
        "- settlement finality guard: live/suspended/to-finish rows are rejected",
        "",
        "## By Tour",
        "",
    ]
    by_tour = report.get("by_tour", {})
    if not by_tour:
        lines.append("- none")
    else:
        for key, summary in by_tour.items():
            lines.append(
                f"- `{key}`: settled={summary.get('settled_picks', 0)}, wins={summary.get('wins', 0)}, hit_rate={summary.get('hit_rate')}, ROI={summary.get('roi')}"
            )
    lines.extend(["", "## By Series", ""])
    by_series = report.get("by_series", {})
    if not by_series:
        lines.append("- none")
    else:
        for key, summary in by_series.items():
            lines.append(
                f"- `{key}`: settled={summary.get('settled_picks', 0)}, wins={summary.get('wins', 0)}, hit_rate={summary.get('hit_rate')}, ROI={summary.get('roi')}"
            )
    lines.extend(["", "## By Surface", ""])
    by_surface = report.get("by_surface", {})
    if not by_surface:
        lines.append("- none")
    else:
        for key, summary in by_surface.items():
            lines.append(
                f"- `{key}`: settled={summary.get('settled_picks', 0)}, wins={summary.get('wins', 0)}, hit_rate={summary.get('hit_rate')}, ROI={summary.get('roi')}"
            )
    lines.extend(["", "## By Bucket", ""])
    by_bucket = report.get("by_bucket", {})
    if not by_bucket:
        lines.append("- none")
    else:
        for key, summary in by_bucket.items():
            lines.append(
                f"- `{key}`: settled={summary.get('settled_picks', 0)}, wins={summary.get('wins', 0)}, hit_rate={summary.get('hit_rate')}, ROI={summary.get('roi')}"
            )
    lines.extend(["", "## By Source", ""])
    by_source = report.get("by_source", {})
    if not by_source:
        lines.append("- none")
    else:
        for key, summary in by_source.items():
            lines.append(
                f"- `{key}`: settled={summary.get('settled_picks', 0)}, wins={summary.get('wins', 0)}, hit_rate={summary.get('hit_rate')}, ROI={summary.get('roi')}"
            )
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit recent archived daily picks against settled warehouse results.")
    ap.add_argument("--end", default=date.today().isoformat(), help="End date inclusive (YYYY-MM-DD).")
    ap.add_argument("--days", type=int, default=30, help="Rolling window length in days (default: 30).")
    ap.add_argument("--warehouse", default=str(WAREHOUSE), help="Path to warehouse.csv.gz")
    ap.add_argument(
        "--ledger-kind",
        choices=["official", "forecast", "both"],
        default="official",
        help="Which ledger to audit. Default: official same-day picks.",
    )
    ap.add_argument(
        "--include-same-day",
        action="store_true",
        help="Allow same-day archived picks to count as settled. Default is OFF to avoid live/in-progress false settlements.",
    )
    args = ap.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    start = (end - timedelta(days=max(0, args.days - 1))).isoformat()
    report = build_report(
        start,
        end.isoformat(),
        Path(args.warehouse),
        include_same_day=args.include_same_day,
        ledger_kind=args.ledger_kind,
    )

    LOCALDATA.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.ledger_kind == "official" else f"_{args.ledger_kind}"
    json_path = LOCALDATA / f"picks_audit{suffix}_rolling.json"
    md_path = LOCALDATA / f"picks_audit{suffix}_{end.isoformat()}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    write_markdown(md_path, report)

    overall = report.get("overall", {})
    print(f"Recent picks audit — {start} to {end.isoformat()}")
    print(f" archived pick rows: {report.get('archived_pick_rows', 0)}")
    print(f" archived pick dates: {len(report.get('archived_pick_dates', []))}")
    print(f" ledger kind: {report.get('ledger_kind')}")
    print(f" same-day rows excluded: {report.get('same_day_excluded', 0)}")
    print(f" settled picks: {overall.get('settled_picks', 0)}")
    print(f" hit rate: {overall.get('hit_rate')}")
    print(f" ROI: {overall.get('roi')}")
    print(f" json: {json_path}")
    print(f" markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())