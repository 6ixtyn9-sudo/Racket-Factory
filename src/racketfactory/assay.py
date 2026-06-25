from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


@dataclass
class Score:
    n: int
    wins: int
    hit_rate: float | None
    roi: float | None
    avg_odds: float | None


def roi_from_bets(odds: Iterable[float | None], wins: Iterable[bool]) -> float | None:
    pnl = []
    for odd, won in zip(odds, wins):
        if odd is None:
            continue
        pnl.append(float(odd) - 1.0 if won else -1.0)
    return round(sum(pnl) / len(pnl), 6) if pnl else None


def score_rows(rows: list[dict]) -> dict:
    n = len(rows)
    wins = sum(1 for r in rows if bool(r.get("won")))
    odds = [float(r["decimal_odds"]) for r in rows if r.get("decimal_odds") is not None]
    score = Score(
        n=n,
        wins=wins,
        hit_rate=round(wins / n, 6) if n else None,
        roi=roi_from_bets([r.get("decimal_odds") for r in rows], [bool(r.get("won")) for r in rows]),
        avg_odds=round(sum(odds) / len(odds), 6) if odds else None,
    )
    return asdict(score)


def odds_band(odds: float | None) -> str:
    if odds is None:
        return "NO_ODDS"
    o = float(odds)
    if o < 1.20:
        return "1.00-1.20"
    if o < 1.50:
        return "1.20-1.50"
    if o < 1.75:
        return "1.50-1.75"
    if o < 2.00:
        return "1.75-2.00"
    if o < 2.50:
        return "2.00-2.50"
    return "2.50+"
