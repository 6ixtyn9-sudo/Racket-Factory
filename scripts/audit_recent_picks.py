#!/usr/bin/env python3
"""Racket Factory — Recent picks audit against settled warehouse results.

Generates highly detailed Markdown tracking reports and JSON summaries,
matching Edge-Factory parity.
"""
from __future__ import annotations

import argparse
import json
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


def local_today() -> str:
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


def archived_picks_path(day: str) -> Path:
    return LOCALDATA / f"picks_{day}.json"


def load_archived_picks(start: str, end: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in daterange(start, end):
        path = archived_picks_path(day)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if isinstance(row, dict):
                row = dict(row)
                row.setdefault("date", day)
                out.append(row)
    return out


def load_warehouse_df(warehouse_path: Path) -> pd.DataFrame:
    if not warehouse_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(warehouse_path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def settle_pick(pick: dict[str, Any], df: pd.DataFrame) -> SettledPick | None:
    match_date = str(pick.get("date") or "")[:10]
    player_home = str(pick.get("player_home") or "").strip()
    player_away = str(pick.get("player_away") or "").strip()
    selected_player = str(pick.get("selected_player") or "").strip()

    if not match_date or not player_home or not player_away or not selected_player:
        return None

    if df.empty:
        return None

    # Filter by date
    candidates = df[df.get("match_date", pd.Series(dtype=object)).astype(str) == match_date].copy()
    if candidates.empty:
        return None

    # Match player_a / player_b (normal order)
    subset = candidates[
        (candidates.get("player_a", pd.Series(dtype=object)).astype(str) == player_home)
        & (candidates.get("player_b", pd.Series(dtype=object)).astype(str) == player_away)
    ]
    if subset.empty:
        # Match player_a / player_b (reverse order)
        subset = candidates[
            (candidates.get("player_a", pd.Series(dtype=object)).astype(str) == player_away)
            & (candidates.get("player_b", pd.Series(dtype=object)).astype(str) == player_home)
        ]

    if subset.empty or "winner" not in subset.columns:
        return None

    settled_rows = subset[subset["winner"].notna() & (subset["winner"].astype(str).str.strip() != "")]
    if settled_rows.empty:
        return None

    row = settled_rows.iloc[0]
    winner = str(row.get("winner") or "").strip()
    won = winner == selected_player

    # Correctly identify odds based on actual player string or fallback to side/captured odds
    odds = None
    player_a_val = str(row.get("player_a", "")).strip()
    player_b_val = str(row.get("player_b", "")).strip()

    if selected_player == player_a_val:
        odds = row.get("odds_a")
    elif selected_player == player_b_val:
        odds = row.get("odds_b")
    else:
        side = str(pick.get("selected_side", ""))
        if side in ("player_a", "1"):
            odds = row.get("odds_a")
        elif side in ("player_b", "2"):
            odds = row.get("odds_b")

    try:
        odds = float(odds) if pd.notna(odds) else None
    except (TypeError, ValueError):
        odds = None

    # Fallback to pick's captured odds if warehouse closing odds are missing
    if odds is None or pd.isna(odds):
        try:
            odds_val = pick.get("odds")
            odds = float(odds_val) if odds_val is not None else None
        except (TypeError, ValueError):
            odds = None

    pnl = None if odds is None else (odds - 1.0 if won else -1.0)

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
    )


def summarize_scored(rows: list[SettledPick]) -> dict[str, Any]:
    settled = len(rows)
    wins = sum(1 for row in rows if row.won)
    with_odds = [row for row in rows if row.pnl is not None]
    pnl_sum = sum(float(row.pnl or 0.0) for row in with_odds)
    return {
        "settled_picks": settled,
        "wins": wins,
        "hit_rate": round(wins / settled, 6) if settled else None,
        "priced_picks": len(with_odds),
        "roi": round(pnl_sum / len(with_odds), 6) if with_odds else None,
    }


def summarize_by(rows: list[SettledPick], attr: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[SettledPick]] = defaultdict(list)
    for row in rows:
        grouped[str(getattr(row, attr) or "UNKNOWN")].append(row)
    return {name: summarize_scored(group_rows) for name, group_rows in sorted(grouped.items())}


def build_report(start: str, end: str, warehouse_path: Path, *, include_same_day: bool = False) -> dict[str, Any]:
    picks = load_archived_picks(start, end)
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
        "overall": summarize_scored(settled_rows),
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
        "",
        "## Settlement policy",
        "",
        f"- include same-day picks: {report.get('include_same_day')}",
        f"- same-day cutoff date: {report.get('same_day_cutoff')}",
        f"- same-day rows excluded: {report.get('same_day_excluded', 0)}",
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
        "--include-same-day",
        action="store_true",
        help="Allow same-day archived picks to count as settled. Default is OFF to avoid live/in-progress false settlements.",
    )
    args = ap.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    start = (end - timedelta(days=max(0, args.days - 1))).isoformat()
    report = build_report(start, end.isoformat(), Path(args.warehouse), include_same_day=args.include_same_day)

    LOCALDATA.mkdir(parents=True, exist_ok=True)
    json_path = LOCALDATA / "picks_audit_rolling.json"
    md_path = LOCALDATA / f"picks_audit_{end.isoformat()}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    write_markdown(md_path, report)

    overall = report.get("overall", {})
    print(f"Recent picks audit — {start} to {end.isoformat()}")
    print(f" archived pick rows: {report.get('archived_pick_rows', 0)}")
    print(f" archived pick dates: {len(report.get('archived_pick_dates', []))}")
    print(f" same-day rows excluded: {report.get('same_day_excluded', 0)}")
    print(f" settled picks: {overall.get('settled_picks', 0)}")
    print(f" hit rate: {overall.get('hit_rate')}")
    print(f" ROI: {overall.get('roi')}")
    print(f" json: {json_path}")
    print(f" markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())