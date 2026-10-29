"""Current selection policy; never used to re-grade historical slips."""
from __future__ import annotations

import math
import re

MIN_LEG_ODDS = 1.30


def valid_leg_price(value) -> bool:
    """Accept only finite decimal leg prices at or above the common floor."""
    if value is None or isinstance(value, bool):
        return False
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return False
    return math.isfinite(value) and value >= MIN_LEG_ODDS


def restore_ev_only_bucket(pick: dict) -> dict:
    """Recover only a known legacy EV-only export skip, without mutating it.

    Unknown/other DEAD_EDGE reasons remain blocked. ML must be independently
    rescored; this function does not clear an existing ML verdict.
    """
    p = dict(pick)
    bucket_before = p.get("bucket")
    reason = str(p.get("skip_reason") or "").lower().strip()
    number = r"-?\d+\.\d{3}"
    ev_export_skip = bucket_before == "SKIPPED_DEAD_EDGE" and bool(
        re.fullmatch(rf"negative ev \({number} < {number}\)", reason) or
        re.fullmatch(rf"fade ev \({number} < {number}\) at live fade price", reason))
    # A VETO label can be removed only when its recorded reason was exclusively
    # the old EV gate; independent context scoring must still be rerun.
    ev_veto_skip = bucket_before == "SKIPPED_VETO" and ";" not in reason and (
        reason.startswith("ml ev ") or reason.startswith("doubles ev "))
    if not (ev_export_skip or ev_veto_skip):
        return p
    verdict = str(p.get("edge_verdict") or p.get("pick") or "").upper()
    if str(p.get("pick") or "").upper() == "FADE":
        bucket = "FADE"
    elif verdict == "EDGE CONFIRMED":
        bucket = "CERTIFIED_CLEAN"
    elif verdict == "WATCHLIST":
        bucket = "WATCHLIST"
    else:
        return p
    p["bucket"] = bucket
    p["legacy_ev_only_skip"] = ev_export_skip
    p["legacy_ev_only_veto"] = ev_veto_skip
    p["skip_reason"] = ""
    return p
