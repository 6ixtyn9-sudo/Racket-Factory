#!/usr/bin/env python3
"""Blind market audit: favorite/underdog ROI by tour, tournament and odds band.

Reads the unified warehouse (localdata/warehouse.csv.gz) and expands every
settled two-way match into its two market sides, then summarises ROI with
assay.score_rows. This answers the HANDOVER "first research questions"
(blind favorite/underdog ROI by odds band) directly from the CSV engine.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.assay import odds_band, score_rows

EMPTY_WINNER = {"", "nan", "<NA>", "none", "null"}


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _to_odds(value: object) -> float | None:
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    return odds if odds > 1.0 else None


def market_sides_from_warehouse(warehouse_path: Path) -> list[dict]:
    """Expand settled two-way matches into one row per market side.

    Each emitted side carries the keys assay.score_rows consumes
    (decimal_odds, won) plus context used for grouping.
    """
    df = pd.read_csv(warehouse_path, low_memory=False)
    required = {"player_a", "player_b", "winner", "odds_a", "odds_b"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"warehouse missing required columns: {sorted(missing)}")

    sides: list[dict] = []
    for row in df.to_dict("records"):
        winner = _clean(row.get("winner"))
        if winner.lower() in EMPTY_WINNER:
            continue  # unsettled / live-injected / abandoned -- not auditable
        player_a = _clean(row.get("player_a"))
        player_b = _clean(row.get("player_b"))
        odds_a = _to_odds(row.get("odds_a"))
        odds_b = _to_odds(row.get("odds_b"))
        tour = _clean(row.get("tour")) or "UNKNOWN"
        tournament = _clean(row.get("tournament")) or "UNKNOWN"

        # Favorite = the side with the shorter (lower) decimal odds, when both
        # prices exist. Ties and one-sided rows fall back to non-favorite.
        fav_side = None
        if odds_a is not None and odds_b is not None:
            fav_side = "a" if odds_a < odds_b else ("b" if odds_b < odds_a else None)

        for side, name, odds in (("a", player_a, odds_a), ("b", player_b, odds_b)):
            if odds is None or not name:
                continue
            sides.append({
                "tour": tour,
                "tournament": tournament,
                "decimal_odds": odds,
                "won": name == winner,
                "is_favorite": (side == fav_side),
            })
    return sides


def _summary_line(key: str, rows: list[dict], summary: dict) -> str:
    n = summary["n"]
    wins = summary["wins"]
    hit = wins / n if n else 0.0
    avg_odds = (sum(float(r["decimal_odds"]) for r in rows) / n) if n else 0.0
    return (f"  {key}: n={n} hit={hit:.4f} roi={summary['roi']:.4f} "
            f"avg_odds={avg_odds:.4f}")


def main() -> int:
    warehouse_path = ROOT / "localdata" / "warehouse.csv.gz"
    if not warehouse_path.exists():
        raise SystemExit(
            f"warehouse not found: {warehouse_path}\n"
            "Build it first: PYTHONPATH=src python3 scripts/build_warehouse.py"
        )

    rows = market_sides_from_warehouse(warehouse_path)

    groups: dict[str, list[dict]] = defaultdict(list)
    groups["overall"] = rows
    for row in rows:
        fav_key = "favorite" if row.get("is_favorite") else "underdog"
        band = odds_band(row["decimal_odds"])
        groups[fav_key].append(row)
        groups[f"tour={row.get('tour') or 'UNKNOWN'}"].append(row)
        groups[f"tournament={row.get('tournament') or 'UNKNOWN'}"].append(row)
        groups[f"odds={band}"].append(row)
        groups[f"{fav_key}|odds={band}"].append(row)
        groups[f"tour={row.get('tour') or 'UNKNOWN'}|{fav_key}|odds={band}"].append(row)

    report = {key: score_rows(value) for key, value in sorted(groups.items())}
    out = ROOT / "localdata" / "market_audit.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"market audit -> {out}  (market sides: {len(rows)})")
    for key in sorted(groups):
        if (key in {"overall", "favorite", "underdog"}
                or key.startswith("favorite|odds=")
                or key.startswith("underdog|odds=")):
            print(_summary_line(key, groups[key], report[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
