"""Cross-checked comparison odds: BetExplorer x OddsPortal upcoming.

Both legs cover tours the main Odds API may miss (Challenger/ITF/WTA-125K),
but their price semantics differ: the BetExplorer detail path emits named
bookmaker offers, while OddsPortal emits a best-across-books indicative pair.
The merger matches the legs by full fixture context and emits one row per
match:

* both legs + agree (each side within AGREE_TOLERANCE) -> retain the
  BetExplorer observation and flag ``odds_cross_checked = "agree"``.
* both legs + disagree -> still retain the BetExplorer observation (never the
  optimistic leg or a mixed pair), flagged ``"disagree"`` for audit.
* one leg only -> retain that leg's original pair and metadata, flagged
  ``"single"``.

``source`` and ``price_type`` remain those of the leg that supplied the price;
the other leg's pair rides along in ``odds_alt_*`` for transparency. An
OddsPortal indicative pair is never relabelled as a named bookmaker offer or
confirmed fill.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AGREE_TOLERANCE = 0.10
ROOT = Path(__file__).resolve().parents[2]
STATUS_ENV = "RACKET_FACTORY_ODDS_COMPARE_STATUS_PATH"


def _status_path() -> Path:
    override = os.getenv(STATUS_ENV, "").strip()
    if override:
        return Path(override)
    return ROOT / "localdata" / "odds_compare_status.json"


def _write_status(entries: dict[str, Any]) -> None:
    """Best-effort leg-health snapshot (committed; lets git verify the legs).

    Merged per target date so the today-card and forecast calls in one job
    do not overwrite each other; only the freshest 7 dates are kept.
    """
    try:
        path = _status_path()
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                pass
        existing.update(entries)
        for stale in sorted(existing)[:-7]:
            del existing[stale]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2, sort_keys=True))
    except Exception as exc:
        logger.warning("Could not write odds-compare status: %s", exc)


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
    from racketfactory.warehouse import names_match, live_player_key, surname_tokens  # lazy: warehouse enriches via this module

    def sides(row: dict[str, Any]) -> tuple[float | None, float | None]:
        try:
            h = float(row.get("odds_home")) if row.get("odds_home") is not None else None
            a = float(row.get("odds_away")) if row.get("odds_away") is not None else None
        except (TypeError, ValueError):
            return None, None
        if h is None or a is None or not math.isfinite(h) or not math.isfinite(a) or h <= 1.0 or a <= 1.0:
            return None, None
        return h, a

    def names(row: dict[str, Any]) -> tuple[str, str]:
        return str(row.get("player_home") or ""), str(row.get("player_away") or "")

    def _surname_overlap(a: str, b: str) -> bool:
        try:
            from racketfactory.warehouse import _live_tok_eq

            ta = surname_tokens(a)
            tb = surname_tokens(b)
            if not ta or not tb:
                return False
            if set(ta) & set(tb):
                return True
            for x in ta:
                for y in tb:
                    if _live_tok_eq(x, y):
                        return True
            return False
        except Exception:
            return False

    def _pair_matches(h1, a1, h2, a2) -> tuple[bool, bool] | None:
        if names_match(h1, h2) and names_match(a1, a2):
            return True, False
        if names_match(h1, a2) and names_match(a1, h2):
            return True, True
        # Never cross-check a doubles quote against a different partnership.
        from racketfactory.settlement import split_team
        if any(len(split_team(n)) > 1 for n in (h1, a1, h2, a2)):
            return None
        try:
            if live_player_key(h1) and live_player_key(h2) and live_player_key(a1) and live_player_key(a2):
                if live_player_key(h1) == live_player_key(h2) and live_player_key(a1) == live_player_key(a2):
                    return True, False
                if live_player_key(h1) == live_player_key(a2) and live_player_key(a1) == live_player_key(h2):
                    return True, True
                if _surname_overlap(h1, h2) and _surname_overlap(a1, a2):
                    return True, False
                if _surname_overlap(h1, a2) and _surname_overlap(a1, h2):
                    return True, True
        except Exception:
            pass
        return None

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
        candidates = []
        for i, op in enumerate(oddsportal_rows):
            if i in used_op:
                continue
            # Only compare the same normalized betting date and full-match
            # winner market. These adapters use full-match winner by default.
            be_date = str(be.get("match_date") or "")[:10]
            if not be_date or be_date != str(op.get("match_date") or "")[:10]:
                continue
            def market_key(row):
                return (str(row.get("market") or row.get("market_key") or "h2h").lower(),
                        str(row.get("period") or "match").lower(),
                        "" if row.get("line") is None else str(row.get("line")))
            be_market = market_key(be)
            if (be_market != market_key(op) or be_market[0] not in {"h2h", "1x2", "match_winner", "match winner"}
                    or be_market[1] not in {"match", "full match", "full_match", "full", "0"} or be_market[2]):
                continue
            op_home, op_away = names(op)
            if not op_home or not op_away:
                continue
            res = _pair_matches(be_home, be_away, op_home, op_away)
            flipped = _pair_matches(be_home, be_away, op_away, op_home)
            if res is not None and flipped is not None and res[1] != flipped[1]:
                candidates.append((i, res[1]))
        if len(candidates) == 1:
            idx, reverse = candidates[0]
            # Uniqueness must hold in both directions: multiple BetExplorer
            # fixtures must not compete for the same OddsPortal row.
            op = oddsportal_rows[idx]
            op_home, op_away = names(op)
            competing = 0
            for other in betexplorer_rows:
                other_home, other_away = names(other)
                if (str(other.get("match_date") or "")[:10] != be_date
                        or market_key(other) != market_key(op)):
                    continue
                pair = _pair_matches(other_home, other_away, op_home, op_away)
                flip = _pair_matches(other_home, other_away, op_away, op_home)
                if pair is not None and flip is not None and pair[1] != flip[1]:
                    competing += 1
            if competing == 1:
                match_idx, reversed_match = idx, reverse
        elif len(candidates) > 1:
            logger.warning("Ambiguous OddsPortal comparison for %s vs %s", be_home, be_away)
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
        # Preserve the actual producer label for optional legs passed through
        # this merger; the normal OddsPortal adapter already labels its rows.
        row["source"] = str(op.get("source") or "OddsPortal")
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

    errors: dict[str, str] = {}
    try:
        be_rows = betexplorer.fetch_betexplorer_rows(target_date)
    except Exception as exc:
        logger.warning("BetExplorer leg failed: %s", exc)
        errors["betexplorer"] = f"{type(exc).__name__}: {exc}"
        be_rows = []
    try:
        op_rows = oddsportal_upcoming.fetch_oddsportal_upcoming_rows(target_date)
    except Exception as exc:
        logger.warning("OddsPortal upcoming leg failed: %s", exc)
        errors["oddsportal_upcoming"] = f"{type(exc).__name__}: {exc}"
        op_rows = []
    merged: list[dict[str, Any]] = []
    if be_rows or op_rows:
        try:
            merged = merge_comparison_rows(be_rows or [], op_rows or [])
        except Exception as exc:
            logger.warning("Odds compare merge failed: %s", exc)
            errors["merge"] = f"{type(exc).__name__}: {exc}"
    flags: dict[str, int] = {}
    for row in merged:
        flag = str(row.get("odds_cross_checked") or "missing")
        flags[flag] = flags.get(flag, 0) + 1
    _write_status({
        str(target_date)[:10]: {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "betexplorer_rows": len(be_rows),
            "oddsportal_upcoming_rows": len(op_rows),
            "merged_rows": len(merged),
            "cross_checked": flags,
            # Empty lists without a verified empty-board signal are not proof
            # of a complete empty market. Partial observations remain visible.
            "source_status": {
                name: ("failed" if name in errors else
                       "partial" if any(row.get("_collection_partial") for row in rows) else
                       "rows_observed" if rows else "empty_or_unknown")
                for name, rows in (("betexplorer", be_rows), ("oddsportal_upcoming", op_rows))
            },
            "errors": errors,
        },
    })
    return merged
