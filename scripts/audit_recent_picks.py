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


HISTORY_PATH = LOCALDATA / "picks_audit_history.json"


def archived_picks_path(day: str) -> Path:
    return LOCALDATA / f"picks_{day}.json"


def forecast_picks_path(day: str) -> Path:
    return LOCALDATA / f"picks_forecast_{day}.json"


def discover_archived_pick_dates(*, ledger_kind: str = "official") -> list[str]:
    """Every dated pick ledger on disk, oldest first. Window is not truncated here."""
    dates: set[str] = set()
    if ledger_kind in {"official", "both"}:
        for path in LOCALDATA.glob("picks_20[0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
            if m:
                dates.add(m.group(1))
    if ledger_kind in {"forecast", "both"}:
        for path in LOCALDATA.glob("picks_forecast_20[0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
            if m:
                dates.add(m.group(1))
    return sorted(dates)


def _pick_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """History/ledger key: (ledger_kind, date, normalized match, normalized selection).

    Normalized so spelling/case variants of the same pick collapse to one key.
    Legacy history rows without a ledger_kind are treated as official.
    """
    kind = clean_text(row.get("ledger_kind")) or "official"
    return (
        kind,
        str(row.get("date") or "")[:10],
        normalize_name(row.get("match")),
        normalize_name(row.get("selected_player") or row.get("selection")),
    )


def pick_identity(row: dict[str, Any]) -> tuple[tuple[str, ...], str]:
    """Identity of the physical pick (match + selection).

    Independent of the daily-ledger file and of home/away ordering, so a
    carryover re-pick of the same match archived in two daily ledgers (e.g.
    picked the day before AND on match day) collapses to one pick.
    """
    match = clean_text(row.get("match"))
    home = away = ""
    if match:
        parts = re.split(r"\s+v(?:s\.?)?\s+", match, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            home, away = clean_text(parts[0]), clean_text(parts[1])
        else:
            home = match
    teams = tuple(sorted(normalize_name(t) for t in (home, away) if t))
    selection = normalize_name(row.get("selected_player") or row.get("selection"))
    return (teams, selection)


def archive_pick_keys(ledger_kind: str) -> set[tuple[str, str, str, str]]:
    """Keys of every archived pick row (all dates) for one ledger kind.

    Used to prune the cumulative history: a pick that no longer exists in any
    archived ledger (its daily file was rewritten/pruned) must not keep
    resurrecting itself in every future audit.
    """
    dates = discover_archived_pick_dates(ledger_kind=ledger_kind)
    if not dates:
        return set()
    keys: set[tuple[str, str, str, str]] = set()
    for row in load_archived_picks(dates[0], dates[-1], ledger_kind=ledger_kind):
        keys.add(_pick_key(row))
    return keys


def load_audit_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def merge_audit_history(
    current: list[dict[str, Any]],
    valid_keys: set[tuple[str, str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Keep a cumulative per-pick ledger so a 3-day prune cannot blank the audit.

    Newer settled (won/lost/void/conflict) rows replace pending ones. A fresh
    pending row does not overwrite a previously settled result.

    When ``valid_keys`` is provided (the keys of every archived pick row),
    rows of the current ledger kind whose pick no longer exists in any
    archived ledger are pruned — a rewritten daily file must not keep
    resurrecting stale picks in every future audit. History rows of other
    ledger kinds are preserved untouched for their own runs.
    """
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in load_audit_history() + list(current):
        key = _pick_key(row)
        if not key[1] or not key[2]:
            continue
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = row
            continue
        prev_status = str(prev.get("status") or "")
        new_status = str(row.get("status") or "")
        prev_done = prev_status in {"won", "lost", "void", "conflict"}
        new_done = new_status in {"won", "lost", "void", "conflict"}
        if new_done or not prev_done:
            by_key[key] = row
    merged = [by_key[k] for k in sorted(by_key)]
    if valid_keys is not None:
        current_kinds = {
            clean_text(r.get("ledger_kind")) or "official" for r in current
        }
        merged = [
            r for r in merged
            if _pick_key(r) in valid_keys
            or (clean_text(r.get("ledger_kind")) or "official") not in current_kinds
        ]
    try:
        LOCALDATA.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(merged, indent=2, sort_keys=True))
    except Exception:
        pass
    return merged


_STATUS_RANK = {"won": 3, "lost": 3, "void": 2, "conflict": 2}


def dedupe_by_identity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows that are the same physical pick re-archived in several
    daily ledgers (carryover re-picks: identical match + identical selection).

    Canonical row: most advanced status first (settled beats pending), then
    earliest date (the original pick). Losers are recorded on the canonical
    row under ``duplicate_picks`` so the merge stays visible in the report.
    """
    groups: dict[tuple[tuple[str, ...], str], list[dict[str, Any]]] = {}
    order: list[tuple[tuple[str, ...], str]] = []
    for row in rows:
        key = pick_identity(row)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    out: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(group[0])
            continue
        group = sorted(
            group,
            key=lambda r: (-_STATUS_RANK.get(str(r.get("status") or ""), 1),
                           str(r.get("date") or "")),
        )
        canonical = dict(group[0])
        canonical["duplicate_picks"] = [
            f"{str(r.get('date') or '')[:10]} {clean_text(r.get('bucket')) or 'UNKNOWN'} ({r.get('status')})"
            for r in group[1:]
        ]
        out.append(canonical)
    return out


def settled_from_all_row(pp: dict[str, Any]) -> SettledPick | None:
    status = str(pp.get("status") or "")
    if status not in {"won", "lost"}:
        return None
    odds = pp.get("odds")
    try:
        odds_f = float(odds) if odds is not None and str(odds).strip() not in {"", "nan", "None"} else None
        if odds_f is not None and odds_f <= 1.0:
            odds_f = None
    except (TypeError, ValueError):
        odds_f = None
    pnl = pp.get("pnl")
    try:
        pnl_f = float(pnl) if pnl is not None and str(pnl).strip() not in {"", "nan", "None"} else None
    except (TypeError, ValueError):
        pnl_f = None
    if pnl_f is None and odds_f is not None:
        pnl_f = (odds_f - 1.0) if status == "won" else -1.0

    def _bool(key: str):
        v = pp.get(key)
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in {"true", "1", "yes"}:
            return True
        if s in {"false", "0", "no"}:
            return False
        return None

    def _int(key: str):
        v = pp.get(key)
        if v is None or str(v).strip() in {"", "nan", "None"}:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return SettledPick(
        date=str(pp.get("date") or "")[:10],
        tour=str(pp.get("tour") or "UNKNOWN"),
        series=str(pp.get("series") or "UNKNOWN"),
        surface=str(pp.get("surface") or "UNKNOWN"),
        bucket=str(pp.get("bucket") or "UNKNOWN"),
        source=str(pp.get("source") or "UNKNOWN"),
        match=str(pp.get("match") or ""),
        selected_player=str(pp.get("selected_player") or ""),
        winner=str(pp.get("winner") or ""),
        won=status == "won",
        odds=odds_f,
        pnl=pnl_f,
        selected_sets_won=_int("selected_sets_won"),
        selected_sets_lost=_int("selected_sets_lost"),
        selected_won_any_set=_bool("selected_won_any_set"),
        selected_won_set1=_bool("selected_won_set1"),
        selected_won_set2=_bool("selected_won_set2"),
        selected_won_set3=_bool("selected_won_set3"),
        ledger_kind=str(pp.get("ledger_kind") or "official"),
        settle_source=str(pp.get("settle_source") or ""),
        settle_date=str(pp.get("settle_date") or ""),
        settle_score=str(pp.get("settle_score") or ""),
        settle_reason=str(pp.get("settle_reason") or ""),
        settle_status=str(pp.get("settle_status") or ""),
        odds_basis=str(pp.get("odds_basis") or "none"),
        market_basis=str(pp.get("market_basis") or ""),
        is_paper=bool(pp.get("is_paper")),
    )


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


def _side_team_match(x: tuple[str, ...], y: tuple[str, ...]) -> bool:
    """Order-insensitive full-side (team) match via the shared matcher."""
    if len(x) == 1 and len(y) == 1:
        return bool(players_match(x[0], y[0])[0])
    if len(x) != 2 or len(y) != 2:
        return False
    return bool(
        (players_match(x[0], y[0])[0] and players_match(x[1], y[1])[0])
        or (players_match(x[0], y[1])[0] and players_match(x[1], y[0])[0])
    )


def _near_miss_details(match_text: str, rows: list[dict[str, Any]],
                       limit: int = 2) -> list[str]:
    """Find result rows where exactly ONE side of the pick matches.

    A full teams_match is required to settle; a single-side match is the
    signature of a name-variant gap (e.g. initials "L. E." vs "L.", bare
    surname vs compound surname) rather than a missing result. Surfacing it
    lets the report distinguish resolvable matcher gaps from real data gaps.
    """
    from racketfactory.settlement import parse_match_teams

    pa, pb = parse_match_teams(match_text)
    if not pa or not pb:
        return []
    out: list[str] = []
    for row in rows:
        ra = tuple(p.strip() for p in str(row.get("player_a") or "").split("/") if p.strip())
        rb = tuple(p.strip() for p in str(row.get("player_b") or "").split("/") if p.strip())
        if len(ra) != len(pa) or len(rb) != len(pb):
            continue
        straight = (_side_team_match(pa, ra), _side_team_match(pb, rb))
        swapped = (_side_team_match(pa, rb), _side_team_match(pb, ra))
        hit_side = None
        if straight[0] and not straight[1]:
            hit_side = clean_text(row.get("player_a"))
        elif straight[1] and not straight[0]:
            hit_side = clean_text(row.get("player_b"))
        elif swapped[0] and not swapped[1]:
            hit_side = clean_text(row.get("player_b"))
        elif swapped[1] and not swapped[0]:
            hit_side = clean_text(row.get("player_a"))
        if not hit_side:
            continue
        detail = (f"{clean_text(row.get('match_date'))[:10]} "
                  f"{clean_text(row.get('player_a'))} vs {clean_text(row.get('player_b'))}")
        score = clean_text(row.get("score"))
        if score:
            detail += f" {score}"
        src = clean_text(row.get("source"))
        if src:
            detail += f" [{src}]"
        out.append(f"{detail} ({hit_side} side matches, other side unresolvable)")
        if len(out) >= limit:
            break
    return out


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
        reason = outcome.reason
        if "no matching result rows found" in reason:
            near = _near_miss_details(match_text, rows)
            if near:
                reason = f"{reason} | near-miss candidates: " + " ;; ".join(near)
        return None, {"status": "pending_no_result", "reason": reason,
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


def summarize_by(rows: list[SettledPick], attr: str,
                 all_rows: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Group summaries with full ledger coverage.

    ``rows`` are the settled (won/lost) picks and drive hit rate/ROI;
    ``all_rows`` (every audited pick, including pending/void/conflict) drives
    the total/pending counts so every group reconciles against the archived
    ledger — a group whose picks are all pending still appears, with
    settled=0, instead of vanishing.
    """
    grouped: dict[str, list[SettledPick]] = defaultdict(list)
    for row in rows:
        grouped[str(getattr(row, attr) or "UNKNOWN")].append(row)
    out: dict[str, dict[str, Any]] = {
        name: summarize_scored(group_rows) for name, group_rows in sorted(grouped.items())
    }
    if all_rows:
        total: dict[str, int] = defaultdict(int)
        pend: dict[str, int] = defaultdict(int)
        voids: dict[str, int] = defaultdict(int)
        confl: dict[str, int] = defaultdict(int)
        for r in all_rows:
            if isinstance(r, dict):
                g = str(r.get(attr) or "UNKNOWN")
                st = str(r.get("status") or "")
            else:  # SettledPick (already settled by definition)
                g = str(getattr(r, attr, None) or "UNKNOWN")
                st = "won" if r.won else "lost"
            total[g] += 1
            if st.startswith("pending"):
                pend[g] += 1
            elif st == "void":
                voids[g] += 1
            elif st == "conflict":
                confl[g] += 1
        for g in total:
            out.setdefault(g, summarize_scored([]))
        for g, s in out.items():
            s["total_picks"] = total.get(g, 0)
            s["pending_picks"] = pend.get(g, 0)
            s["void_picks"] = voids.get(g, 0)
            s["conflict_picks"] = confl.get(g, 0)
    return out


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
                "ledger_kind": ledger_kind,
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
                "ledger_kind": ledger_kind,
                "selected_sets_won": settled.selected_sets_won,
                "selected_sets_lost": settled.selected_sets_lost,
                "selected_won_any_set": settled.selected_won_any_set,
                "selected_won_set1": settled.selected_won_set1,
                "selected_won_set2": settled.selected_won_set2,
                "selected_won_set3": settled.selected_won_set3,
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
                "ledger_kind": ledger_kind,
            })

    # Cumulative history merge with pruning: rows of this ledger kind whose
    # pick no longer exists in any archived ledger are dropped, so a
    # rewritten daily file cannot resurrect stale picks in future audits.
    valid_keys = archive_pick_keys(ledger_kind)
    prior_history = load_audit_history()
    stale_pruned = sum(
        1 for r in prior_history
        if (clean_text(r.get("ledger_kind")) or "official") == ledger_kind
        and _pick_key(r) not in valid_keys
    )
    all_rows = merge_audit_history(all_rows, valid_keys=valid_keys)
    # Keep only this run's ledger kind (the history file is shared between
    # official and forecast runs and must not cross-contaminate reports).
    all_rows = [r for r in all_rows
                if (clean_text(r.get("ledger_kind")) or "official") == ledger_kind]
    # Collapse carryover re-picks: the same match + selection archived in
    # multiple daily ledgers is one pick, not one per ledger.
    deduped_rows = dedupe_by_identity(all_rows)
    duplicates_merged = len(all_rows) - len(deduped_rows)
    all_rows = deduped_rows

    archived_dates = sorted({str(r.get("date") or "")[:10] for r in all_rows if r.get("date")})
    settled_rows = [s for s in (settled_from_all_row(r) for r in all_rows) if s is not None]

    # Summary includes pending/void/conflict counts
    pending = sum(1 for r in all_rows if str(r.get("status") or "").startswith("pending"))
    voids = sum(1 for r in all_rows if r.get("status") == "void")
    conflicts = sum(1 for r in all_rows if r.get("status") == "conflict")
    reason_counts: dict[str, int] = {}
    for r in all_rows:
        st = str(r.get("status") or "")
        if st.startswith("pending") or st in ("void", "conflict"):
            base = str(r.get("reason") or r.get("settle_reason") or "")
            base = base.split(" | near-miss")[0] or "(unspecified)"
            label = f"{st}: {base}"
            reason_counts[label] = reason_counts.get(label, 0) + 1
    return {
        "start": start,
        "end": end,
        "archived_pick_rows": len(all_rows),
        "archived_pick_dates": archived_dates,
        "ledger_pick_rows": len(picks),
        "duplicates_merged": duplicates_merged,
        "stale_rows_pruned": stale_pruned,
        "pending_reasons": dict(sorted(reason_counts.items(),
                                       key=lambda kv: (-kv[1], kv[0]))),
        "same_day_excluded": same_day_excluded,
        "same_day_cutoff": today_local,
        "include_same_day": include_same_day,
        "ledger_kind": ledger_kind,
        "overall": {**summarize_scored(settled_rows), "pending_picks": pending,
                    "void_picks": voids, "conflict_picks": conflicts,
                    "total_picks": len(all_rows)},
        "by_ledger_kind": summarize_by(settled_rows, "ledger_kind"),
        "by_tour": summarize_by(settled_rows, "tour", all_rows),
        "by_series": summarize_by(settled_rows, "series", all_rows),
        "by_surface": summarize_by(settled_rows, "surface", all_rows),
        "by_bucket": summarize_by(settled_rows, "bucket", all_rows),
        "by_source": summarize_by(settled_rows, "source", all_rows),
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
        f"- ledger pick rows (in window): {report.get('ledger_pick_rows', 0)}",
        f"- duplicate rows merged (same match + selection re-picked): {report.get('duplicates_merged', 0)}",
        f"- stale history rows pruned (pick no longer in ledger): {report.get('stale_rows_pruned', 0)}",
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
        "## Ledger reconciliation",
        "",
        f"- ledger rows in window ({report.get('ledger_kind')}): {report.get('ledger_pick_rows', 0)}",
        f"- duplicate rows merged (same match + selection in multiple daily ledgers): {report.get('duplicates_merged', 0)}",
        f"- stale history rows pruned (pick no longer in any archived ledger): {report.get('stale_rows_pruned', 0)}",
        f"- audited rows: {report.get('archived_pick_rows', 0)}",
        "- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)",
    ]
    reasons = report.get("pending_reasons") or {}
    if reasons:
        lines.append("- unresolved rows by reason:")
        for label, count in reasons.items():
            lines.append(f"  - {label}: {count}")
    lines.extend([
        "",
        "## Per-pick audit (won/lost/pending)",
        "",
    ])
    all_picks = report.get("all_picks", [])
    if all_picks:
        for pp in all_picks:
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
            dups = pp.get("duplicate_picks")
            if dups:
                extra += f" dup_merged=[{'; '.join(dups)}]"
            lines.append(f"- {d} {match} selected={sel} winner={winner} status={status}{extra}")
    else:
        lines.append("- none")

    def _group_section(title: str, data: dict[str, dict[str, Any]]) -> None:
        lines.extend(["", f"## {title}", ""])
        if not data:
            lines.append("- none")
            return
        for key, summary in data.items():
            if "total_picks" in summary:
                entry = (
                    f"total={summary.get('total_picks', 0)}, "
                    f"settled={summary.get('settled_picks', 0)}, "
                    f"wins={summary.get('wins', 0)}, "
                    f"hit_rate={summary.get('hit_rate')}, "
                    f"ROI={summary.get('roi')}, "
                    f"pending={summary.get('pending_picks', 0)}"
                )
                if summary.get("void_picks"):
                    entry += f", void={summary['void_picks']}"
                if summary.get("conflict_picks"):
                    entry += f", conflict={summary['conflict_picks']}"
            else:
                entry = (
                    f"settled={summary.get('settled_picks', 0)}, "
                    f"wins={summary.get('wins', 0)}, "
                    f"hit_rate={summary.get('hit_rate')}, "
                    f"ROI={summary.get('roi')}"
                )
            lines.append(f"- `{key}`: {entry}")

    _group_section("By Tour", report.get("by_tour", {}))
    _group_section("By Series", report.get("by_series", {}))
    _group_section("By Surface", report.get("by_surface", {}))
    _group_section("By Bucket", report.get("by_bucket", {}))
    _group_section("By Source", report.get("by_source", {}))
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit recent archived daily picks against settled warehouse results.")
    ap.add_argument("--end", default=date.today().isoformat(), help="End date inclusive (YYYY-MM-DD).")
    ap.add_argument("--days", type=int, default=0,
                    help="Rolling window length in days. 0 = all archived pick files (default).")
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
    if args.days and args.days > 0:
        start = (end - timedelta(days=max(0, args.days - 1))).isoformat()
    else:
        discovered = discover_archived_pick_dates(ledger_kind=args.ledger_kind)
        in_range = [d for d in discovered if d <= end.isoformat()]
        start = in_range[0] if in_range else end.isoformat()
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
    print(f" ledger pick rows: {report.get('ledger_pick_rows', 0)}")
    print(f" archived pick rows: {report.get('archived_pick_rows', 0)}")
    print(f" duplicate rows merged: {report.get('duplicates_merged', 0)}")
    print(f" stale rows pruned: {report.get('stale_rows_pruned', 0)}")
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