"""Cross-checked comparison odds: BetExplorer x OddsPortal upcoming.

Both legs cover the tours The Odds API misses (Challenger/ITF/WTA-125K), but
they quote differently: BetExplorer's listing tracks the bookmaker consensus
while OddsPortal's lists the best price across books (optimistic by
construction). The merger matches the two legs by name and emits one row per
match:

* both legs + agree (each side within AGREE_TOLERANCE) -> price from the
  BetExplorer consensus pair (the realistic, conservative leg),
  ``odds_cross_checked = "agree"``.
* both legs + disagree -> still the BetExplorer pair (never the optimistic
  leg, never a mixed pair), flagged ``"disagree"`` for downstream audit.
* one leg only -> that leg's pair, flagged ``"single"``.

``source`` on the emitted row is the leg the price came from
("BetExplorer"/"OddsPortal"); the other leg's pair rides along in
``odds_alt_*`` for transparency. Rows keep the TheOddsAPI row shape so the
existing enrich/match/EV machinery consumes them unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

AGREE_TOLERANCE = 0.10


def _sides_agree(a_home: float, a_away: float, b_home: float, b_away: float) -> bool:
    for a, b in ((a_home, b_home), (a_away, b_away)):
        denom = max(abs(a), abs(b))
        if denom <= 0:
            return False
        if abs(a - b) / denom > AGREE_TOLERANCE:
            return False
    return True


def merge_comparison_rows(
    betexplorer_rows: list[dict[str, Any]],
    oddsportal_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pure merge of the two legs; matching via shared settlement names."""
    from racketfactory.warehouse import names_match  # lazy: warehouse enriches via this module

    def sides(row: dict[str, Any]) -> tuple[float | None, float | None]:
        try:
            h = float(row.get("odds_home")) if row.get("odds_home") is not None else None
            a = float(row.get("odds_away")) if row.get("odds_away") is not None else None
        except (TypeError, ValueError):
            return None, None
        if h is None or a is None or h <= 1.0 or a <= 1.0:
            return None, None
        return h, a

    def names(row: dict[str, Any]) -> tuple[str, str]:
        return str(row.get("player_home") or ""), str(row.get("player_away") or "")

    merged: list[dict[str, Any]] = []
    used_op: set[int] = set()
    agree = disagree = 0

    for be in betexplorer_rows:
        be_home, be_away = names(be)
        be_h, be_a = sides(be)
        if not be_home or not be_away or be_h is None:
            continue
        assert be_a is not None
        match_idx: int | None = None
        reversed_match = False
        for i, op in enumerate(oddsportal_rows):
            if i in used_op:
                continue
            op_home, op_away = names(op)
            if not op_home or not op_away:
                continue
            if names_match(be_home, op_home) and names_match(be_away, op_away):
                match_idx, reversed_match = i, False
                break
            if names_match(be_home, op_away) and names_match(be_away, op_home):
                match_idx, reversed_match = i, True
                break
        row = dict(be)
        row["source"] = "BetExplorer"
        if match_idx is None:
            row["odds_cross_checked"] = "single"
            merged.append(row)
            continue
        op = oddsportal_rows[match_idx]
        op_h, op_a = sides(op)
        if op_h is None:
            row["odds_cross_checked"] = "single"
            merged.append(row)
            continue
        assert op_a is not None
        if reversed_match:
            op_h, op_a = op_a, op_h
        used_op.add(match_idx)
        row["odds_alt_source"] = "OddsPortal"
        row["odds_alt_home"] = op_h
        row["odds_alt_away"] = op_a
        if _sides_agree(be_h, be_a, op_h, op_a):
            row["odds_cross_checked"] = "agree"
            agree += 1
        else:
            row["odds_cross_checked"] = "disagree"
            disagree += 1
        merged.append(row)

    single_op = 0
    for i, op in enumerate(oddsportal_rows):
        if i in used_op:
            continue
        op_home, op_away = names(op)
        op_h, op_a = sides(op)
        if not op_home or not op_away or op_h is None:
            continue
        row = dict(op)
        row["source"] = "OddsPortal"
        row["odds_cross_checked"] = "single"
        merged.append(row)
        single_op += 1

    logger.info(
        "Odds compare: %d merged (%d agree, %d disagree, %d single)",
        len(merged), agree, disagree, len(merged) - agree - disagree,
    )
    return merged


def fetch_comparison_rows(target_date: str) -> list[dict[str, Any]]:
    """Fetch + merge both comparison legs for target_date (fail-soft)."""
    from racketfactory.sources import betexplorer
    from racketfactory.sources import oddsportal_upcoming

    try:
        be_rows = betexplorer.fetch_betexplorer_rows(target_date)
    except Exception as exc:
        logger.warning("BetExplorer leg failed: %s", exc)
        be_rows = []
    try:
        op_rows = oddsportal_upcoming.fetch_oddsportal_upcoming_rows(target_date)
    except Exception as exc:
        logger.warning("OddsPortal upcoming leg failed: %s", exc)
        op_rows = []
    if not be_rows and not op_rows:
        return []
    try:
        return merge_comparison_rows(be_rows or [], op_rows or [])
    except Exception as exc:
        logger.warning("Odds compare merge failed: %s", exc)
        return []
