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
import sys
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

sys_path = str(ROOT / "src")
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from racketfactory.settlement import (  # noqa: E402
    players_match,
    rank_candidates,
    row_finality,
    settle_selection,
    teams_match,
)


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
    settle_source: str = ""
    settle_date: str = ""
    settle_score: str = ""
    settle_reason: str = ""
    settle_status: str = ""
    odds_basis: str = "none"
    market_basis: str = ""
    is_paper: bool = False


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
    s = str(value).strip()
    if s.lower() in {"", "nan", "none", "<na>", "nat", "null"}:
        return ""
    return s


def normalize_name(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9/\s'-]", " ", text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    parts = [p for p in text.replace("-", " ").replace("'", " ").split() if p]
    # Keep initials (do not strip leading single letter) — needed for strict Smith J vs Smith A check
    return " ".join(parts)


def names_match(a: Any, b: Any) -> bool:
    """Cross-source identity via the shared settlement module.

    Replaces the old bespoke matcher (including the Burillo alias hack:
    compound surnames now match structurally). Doubles compare member-wise
    through :func:`teams_match` semantics for single-team strings.
    """
    from racketfactory.settlement import split_team
    ma, mb = split_team(a), split_team(b)
    if len(ma) == 1 and len(mb) == 1:
        return bool(players_match(ma[0], mb[0])[0])
    if len(ma) == 2 and len(mb) == 2:
        straight = players_match(ma[0], mb[0])[0] and players_match(ma[1], mb[1])[0]
        if straight:
            return True
        return bool(players_match(ma[0], mb[1])[0] and players_match(ma[1], mb[0])[0])
    return False


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
        parts = re.split(r"\s+v(?:s\.?)?\s+", match, maxsplit=1, flags=re.IGNORECASE)
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
    # No early return when the warehouse file is absent: the additional
    # result sources below must still load (offline/local runs).
    try:
        df=pd.read_csv(warehouse_path, low_memory=False)
    except Exception:
        df=pd.DataFrame()
    # Merge additional result sources for settlement robustness — even if warehouse absent
    try:
        localdata=warehouse_path.parent
        add=[]
        for pattern in ["foretennis_results_*.csv.gz", "forebet_results_*.csv.gz", "challenger_results_*.csv.gz", "theoddsapi_scores_*.csv.gz"]:
            for f in localdata.glob(pattern):
                try:
                    adf=pd.read_csv(f, low_memory=False)
                    if not adf.empty and "winner" in adf.columns:
                        add.append(adf)
                except Exception:
                    pass
        # NOTE: predictions_foretennis_*.csv.gz is deliberately NOT merged here.
        # Its actual_result rows duplicate the backfill output in
        # foretennis_results_*.csv.gz (31/35 dupes by match_id), and the 4
        # unique rows are garbage: player_b="US Open" phantoms plus
        # match 1324 whose positional digit reading names the wrong winner
        # (quarantined; TE id=3320729). Settlement reads results files only.
        if add:
            if df.empty:
                combined=pd.concat(add, ignore_index=True, sort=False)
                print(f"Loaded {len(combined)} additional result rows for settlement (warehouse was empty)")
            else:
                combined=pd.concat([df]+add, ignore_index=True, sort=False)
                print(f"Loaded {len(combined)-len(df)} additional result rows for settlement")
            return combined
    except Exception as e:
        print(f"additional results load failed: {e}")
        import traceback
        traceback.print_exc()
    return df



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
    if players_match(winner, player_a)[0]:
        return "player_a"
    if players_match(winner, player_b)[0]:
        return "player_b"
    return None


def _result_row_is_final(row: pd.Series) -> bool:
    """Finality via the shared settlement module (single source of truth)."""
    try:
        record = row.to_dict()
    except Exception:
        record = dict(row)
    return bool(row_finality(record).final)


def _set_diagnostics_from_score(
    score_value: Any,
    score_perspective: Any,
    selected_side: str | None,
    winner_side: str | None,
    row: dict[str, Any] | None = None,
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
        sets_pair = None
        if row:
            from racketfactory.settlement import sets_only_pair as _sop
            sets_pair = _sop(row)
        if sets_pair is None:
            return out
        selected_sets = sets_pair[0] if selected_side == "player_a" else sets_pair[1]
        other_sets = sets_pair[1] if selected_side == "player_a" else sets_pair[0]
        out["selected_sets_won"] = selected_sets
        out["selected_sets_lost"] = other_sets
        out["selected_won_any_set"] = selected_sets > 0
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

def _pick_odds_value(pick: dict[str, Any]) -> float | None:
    try:
        odds_val = pick.get("odds") or pick.get("decimal_odds")
        if odds_val is None or str(odds_val).strip() in {"", "nan", "<NA>", "None"}:
            return None
        odds = float(odds_val)
        return odds if odds > 1.0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def _pick_market_labels(pick: dict[str, Any]) -> tuple[str, bool]:
    """Return (market_basis, is_paper), preferring emitted labels."""
    basis = clean_text(pick.get("_market_basis"))
    if basis:
        is_paper = str(pick.get("_is_paper")).strip().lower() in {"true", "1", "yes"}
        return basis, is_paper
    source = clean_text(pick.get("odds_source"))
    odds = _pick_odds_value(pick)
    if odds is None:
        return "none", True
    if source in {"TheOddsAPI", "OddsPortal", "Bzzoiro", "BetExplorer"}:
        return "api", False
    if source == "ScrapedFallback":
        return "scraped_fallback", True
    return "none", True


def settle_pick(pick: dict[str, Any], df: pd.DataFrame) -> tuple[SettledPick | None, dict[str, Any]]:
    """Settle one pick via the shared settlement module.

    Returns (SettledPick|None, info) where info carries status/reason/basis
    for every outcome including pending/void/conflict, so the report can
    explain unsettled rows instead of silently dropping them.
    """
    match_date = pick_match_date(pick)
    player_home, player_away = pick_players(pick)
    selected_player = clean_text(pick.get("selected_player") or pick.get("selection") or pick.get("pick_player"))
    match_text = clean_text(pick.get("match")) or f"{player_home} vs {player_away}"

    if not match_date or not player_home or not player_away or not selected_player:
        return None, {"status": "pending_unparseable_pick",
                      "reason": "missing date/players/selection"}
    if df.empty or "winner" not in df.columns:
        return None, {"status": "pending_no_result", "reason": "no result rows loaded"}

    try:
        base = datetime.strptime(match_date, "%Y-%m-%d").date()
        want = {base.isoformat(), (base + timedelta(days=1)).isoformat(),
                (base - timedelta(days=1)).isoformat()}
    except ValueError:
        want = {match_date}
    if "match_date" in df.columns:
        date_col = df["match_date"].astype(str).str[:10]
        candidates = df[date_col.isin(want)]
    else:
        candidates = df
    if candidates.empty:
        return None, {"status": "pending_no_result",
                      "reason": "no result rows within +/-1 day"}

    rows = candidates.to_dict(orient="records")
    outcome = settle_selection(match_text, selected_player, rows, match_date)
    basis = dict(outcome.basis or {})
    if outcome.outcome == "CONFLICT":
        return None, {"status": "conflict", "reason": outcome.reason, "basis": basis}
    if outcome.outcome == "VOID":
        return None, {"status": "void", "reason": outcome.reason, "basis": basis}
    if outcome.outcome == "PENDING":
        return None, {"status": "pending_no_result", "reason": outcome.reason,
                      "basis": basis}

    won = outcome.outcome == "WON"

    # Recover the winning basis row deterministically (same ranking).
    matched = [r for r in rows
               if teams_match(match_text, r.get("player_a"), r.get("player_b"))[0]]
    basis_row: dict[str, Any] = {}
    for _, row, fin in rank_candidates(matched, match_date):
        if fin.final:
            basis_row = row
            break

    player_a_val = clean_text(basis_row.get("player_a"))
    player_b_val = clean_text(basis_row.get("player_b"))
    selected_side_norm = None
    warehouse_odds = None
    if players_match(selected_player, player_a_val)[0]:
        selected_side_norm = "player_a"
        warehouse_odds = basis_row.get("odds_a")
    elif players_match(selected_player, player_b_val)[0]:
        selected_side_norm = "player_b"
        warehouse_odds = basis_row.get("odds_b")
    else:
        side = clean_text(pick.get("selected_side"))
        if side in ("player_a", "1"):
            selected_side_norm = "player_a"
            warehouse_odds = basis_row.get("odds_a")
        elif side in ("player_b", "2"):
            selected_side_norm = "player_b"
            warehouse_odds = basis_row.get("odds_b")
    try:
        warehouse_odds = float(warehouse_odds) if warehouse_odds is not None and str(warehouse_odds).strip() not in {"", "nan", "<NA>", "None"} else None
        if warehouse_odds is not None and warehouse_odds <= 1.0:
            warehouse_odds = None
    except (TypeError, ValueError, AttributeError):
        warehouse_odds = None

    # ROI must use the archived pick price. Warehouse odds are a last-resort
    # fallback for genuinely priced picks; NO_ODDS buckets stay unpriced.
    bucket_text = str(pick.get("bucket") or "")
    pick_odds = _pick_odds_value(pick)
    odds_basis = "none"
    if "NO_ODDS" in bucket_text.upper():
        odds = None
    elif pick_odds is not None:
        odds, odds_basis = pick_odds, "pick"
    elif warehouse_odds is not None:
        odds, odds_basis = warehouse_odds, "warehouse"
    else:
        odds = None

    pnl = None if odds is None else (odds - 1.0 if won else -1.0)
    market_basis, is_paper = _pick_market_labels(pick)

    winner_side = _winner_side_from_row_values(clean_text(basis.get("winner")),
                                               player_a_val, player_b_val)
    set_diag = _set_diagnostics_from_score(
        basis_row.get("score"),
        basis_row.get("_score_perspective"),
        selected_side_norm,
        winner_side,
        row=basis_row,
    )

    settled = SettledPick(
        date=match_date,
        tour=str(pick.get("tour") or "UNKNOWN"),
        series=str(pick.get("series") or pick.get("_series") or "UNKNOWN"),
        surface=str(pick.get("surface") or pick.get("_surface") or "UNKNOWN"),
        bucket=str(pick.get("bucket") or "UNKNOWN"),
        source=str(pick.get("source") or "UNKNOWN"),
        match=str(pick.get("match") or f"{player_home} vs {player_away}"),
        selected_player=selected_player,
        winner=clean_text(basis.get("winner")),
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
        settle_source=clean_text(basis.get("source")),
        settle_date=clean_text(basis.get("match_date")),
        settle_score=clean_text(basis.get("score")),
        settle_reason=outcome.reason,
        settle_status=clean_text(basis.get("status")),
        odds_basis=odds_basis,
        market_basis=market_basis,
        is_paper=is_paper,
    )
    return settled, {"status": "won" if won else "lost", "reason": outcome.reason,
                     "basis": basis}


def summarize_scored(rows: list[SettledPick]) -> dict[str, Any]:
    settled = len(rows)
    wins = sum(1 for row in rows if row.won)
    with_odds = [row for row in rows if row.pnl is not None]
    pnl_sum = sum(float(row.pnl or 0.0) for row in with_odds)
    real = [row for row in with_odds if not row.is_paper]
    paper = [row for row in with_odds if row.is_paper]
    real_sum = sum(float(row.pnl or 0.0) for row in real)
    paper_sum = sum(float(row.pnl or 0.0) for row in paper)

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
        "priced_real": len(real),
        "roi_real": round(real_sum / len(real), 6) if real else None,
        "priced_paper": len(paper),
        "roi_paper": round(paper_sum / len(paper), 6) if paper else None,
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
    all_rows: list[dict] = []  # For per-pick audit with pending status
    archived_dates = sorted({str(p.get("date") or "")[:10] for p in picks if p.get("date")})
    today_local = local_today()
    same_day_excluded = 0

    for pick in picks:
        pick_date = str(pick.get("date") or "")[:10]
        if not include_same_day and pick_date >= today_local:
            same_day_excluded += 1
            # Still track as pending for visibility
            all_rows.append({
                "date": pick_date,
                "match": pick.get("match") or f"{pick.get('player_home')} vs {pick.get('player_away')}",
                "selected_player": pick.get("selected_player") or pick.get("selection"),
                "tour": pick.get("tour"),
                "surface": pick.get("surface") or pick.get("_surface"),
                "series": pick.get("series") or pick.get("_series"),
                "source": pick.get("source"),
                "bucket": pick.get("bucket"),
                "status": "pending_same_day_excluded",
                "won": None,
            })
            continue
        settled, info = settle_pick(pick, df)
        status = str(info.get("status") or "pending_no_result")
        reason = str(info.get("reason") or "")
        basis = info.get("basis") or {}
        if settled is not None:
            settled_rows.append(settled)
            all_rows.append({
                "date": pick_date,
                "match": settled.match,
                "selected_player": settled.selected_player,
                "winner": settled.winner,
                "tour": settled.tour,
                "surface": settled.surface,
                "series": settled.series,
                "source": settled.source,
                "bucket": settled.bucket,
                "status": status,
                "won": settled.won,
                "odds": settled.odds,
                "pnl": settled.pnl,
                "settle_source": settled.settle_source,
                "settle_date": settled.settle_date,
                "settle_score": settled.settle_score,
                "settle_status": settled.settle_status,
                "settle_reason": settled.settle_reason,
                "odds_basis": settled.odds_basis,
                "market_basis": settled.market_basis,
                "is_paper": settled.is_paper,
            })
        else:
            all_rows.append({
                "date": pick_date,
                "match": pick.get("match") or f"{pick.get('player_home')} vs {pick.get('player_away')}",
                "selected_player": pick.get("selected_player") or pick.get("selection"),
                "tour": pick.get("tour"),
                "surface": pick.get("surface") or pick.get("_surface"),
                "series": pick.get("series") or pick.get("_series"),
                "source": pick.get("source"),
                "bucket": pick.get("bucket"),
                "status": status,
                "won": None,
                "reason": reason,
                "settle_source": basis.get("source", ""),
                "settle_date": basis.get("match_date", ""),
                "settle_score": basis.get("score", ""),
            })

    # Summary includes pending/void/conflict counts
    pending = sum(1 for r in all_rows if r["status"].startswith("pending"))
    voids = sum(1 for r in all_rows if r["status"] == "void")
    conflicts = sum(1 for r in all_rows if r["status"] == "conflict")
    return {
        "start": start,
        "end": end,
        "archived_pick_rows": len(picks),
        "archived_pick_dates": archived_dates,
        "same_day_excluded": same_day_excluded,
        "same_day_cutoff": today_local,
        "include_same_day": include_same_day,
        "ledger_kind": ledger_kind,
        "overall": {**summarize_scored(settled_rows), "pending_picks": pending,
                    "void_picks": voids, "conflict_picks": conflicts,
                    "total_picks": len(picks)},
        "by_ledger_kind": summarize_by(settled_rows, "ledger_kind"),
        "by_tour": summarize_by(settled_rows, "tour"),
        "by_series": summarize_by(settled_rows, "series"),
        "by_surface": summarize_by(settled_rows, "surface"),
        "by_bucket": summarize_by(settled_rows, "bucket"),
        "by_source": summarize_by(settled_rows, "source"),
        "all_picks": all_rows,  # Per-pick audit with pending status
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
        f"- ROI (real-priced): {overall.get('roi_real')} (n={overall.get('priced_real', 0)})",
        f"- ROI (paper-priced): {overall.get('roi_paper')} (n={overall.get('priced_paper', 0)})",
        f"- pending picks: {overall.get('pending_picks', 0)}",
        f"- void picks: {overall.get('void_picks', 0)}",
        f"- conflict picks: {overall.get('conflict_picks', 0)}",
        f"- total picks: {overall.get('total_picks', 0)}",
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
        "- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)",
        "- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged",
        "- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)",
        "",
        "## Per-pick audit (won/lost/pending)",
        "",
    ]
    all_picks = report.get("all_picks", [])
    if all_picks:
        for pp in all_picks[:100]:
            status = pp.get("status")
            match = pp.get("match")
            sel = pp.get("selected_player")
            winner = pp.get("winner", "?")
            d = pp.get("date")
            extra = ""
            if status not in ("won", "lost"):
                reason = pp.get("reason") or pp.get("settle_reason") or ""
                extra = f" reason={reason}" if reason else ""
            else:
                basis = f"{pp.get('settle_source', '')}@{pp.get('settle_date', '')}:{pp.get('settle_score', '')}"
                extra = f" basis={basis}"
            lines.append(f"- {d} {match} selected={sel} winner={winner} status={status}{extra}")
        if len(all_picks) > 100:
            lines.append(f"- ... and {len(all_picks)-100} more")
    else:
        lines.append("- none")
    lines.extend(["", "## By Tour", ""])
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