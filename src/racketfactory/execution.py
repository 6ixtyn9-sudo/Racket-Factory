"""Single source of truth for "may this leg move the real bank?".

WHY THIS MODULE EXISTS
----------------------
The ticket builder (``scripts/auto_tickets.py``) and the grader
(``scripts/auto_tickets_grade.py``) used to answer that question with two
independent, *different* rules:

    builder  : paper if (no trusted market price) OR (_late_start)
    grader   : paper if (odds_source blank) OR (odds_reject_reason)

The grader never looked at ``_late_start``/``_is_paper``, so a leg that was
late at generation time but carried a BetExplorer price was PAPER to the
builder and REAL to the grader. Observed live on the 2026-09-20 open slip
(both legs late + BetExplorer priced, acca ``paper: true``, ``odds: null``,
``stake_pct: 25.0``). Consequence, traced branch by branch through
``settle_open_slips``:

    both legs win  -> acca_odds is None -> "never pays on fiction" -> the
                      slip is held open FOREVER (it had sat open 6 days)
    one leg loses  -> booked as a loss

i.e. the disagreement was strictly one-directional: a mismatched acca could
be booked as a LOSS but never as a WIN.

The fix is not "make the grader match the builder" — that just re-creates
the same class of bug next time someone edits one side. Both sides now call
the functions below, and the verdict is *stamped onto the leg at build time*
so a later grading run can detect drift instead of silently disagreeing.

FAIL-CLOSED CONTRACT
--------------------
Every rule here answers "can we PROVE this leg is executable at the recorded
price?". Anything unproven is blocked (paper track: hit-rate only, never
touches the bank). Blocking is cheap; a fabricated bank curve is not.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

# Market prices only: anything else (scraped fallback, ML estimates, blanks)
# is paper-track, never staked. Mirrors market_basis_for_pick's api set.
# BetExplorer legs quote the bookmaker consensus (indicative, not the ticket
# price at any single book) — staked because EV gating runs on the same leg.
TRUSTED_MARKET_SOURCES = frozenset({"theoddsapi", "oddsportal", "bzzoiro", "betexplorer"})

# Stamped onto every leg the builder emits, so the grader can cross-check.
STAMP_SAFE = "_execution_safe"
STAMP_BLOCK = "_execution_block"

# Reason strings are part of the ticket .txt output contract; keep them stable.
BLOCK_LATE = "already started at generation time (stale price)"
BLOCK_NO_PRICE = "no trusted market price"
BLOCK_KICKOFF_UNKNOWN = "kickoff unknown (cannot prove match had not started)"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>", "nat", "null"}:
        return ""
    return text


def leg_market_odds(leg: Mapping[str, Any]) -> float | None:
    """The tradable market price for a leg, or None when there isn't one.

    NEVER estimated. A leg without a trusted, finite, >1.0 market price goes
    to the paper track instead of being staked on a fabricated number.
    """
    if _clean(leg.get("odds_reject_reason")):
        return None
    if leg.get("_is_paper"):
        return None
    if _clean(leg.get("odds_source")).lower() not in TRUSTED_MARKET_SOURCES:
        return None
    try:
        odds = float(leg.get("odds"))
    except (TypeError, ValueError):
        return None
    # NaN fails every comparison, inf is not a real price.
    if not (odds > 1.0) or odds == float("inf"):
        return None
    return odds


def leg_execution_block(leg: Mapping[str, Any]) -> str:
    """"" when the leg may be staked, else the human reason it may not.

    Order is chosen for message quality, not logic: a late leg that is also
    unpriced reads better as "already started".
    """
    if leg.get("_late_start"):
        return BLOCK_LATE
    if leg.get("_kickoff_unknown"):
        return BLOCK_KICKOFF_UNKNOWN
    # _is_paper is deliberately NOT a separate branch: leg_market_odds()
    # already rejects it, so such legs report "no trusted market price" —
    # the wording the ticket .txt has always used for them.
    if leg_market_odds(leg) is None:
        return BLOCK_NO_PRICE
    return ""


def leg_execution_safe(leg: Mapping[str, Any]) -> bool:
    return leg_execution_block(leg) == ""


def stamp_leg(leg: dict) -> dict:
    """Record the build-time verdict on the leg (mutates and returns it)."""
    block = leg_execution_block(leg)
    leg[STAMP_SAFE] = block == ""
    leg[STAMP_BLOCK] = block
    # Legacy field kept so existing ticket .txt rendering and any downstream
    # reader that predates the stamp keeps working. Only written when there
    # IS a reason: an executable leg carries no paper reason, same as before.
    if block:
        leg["_paper_reason"] = block
    else:
        leg.pop("_paper_reason", None)
    return leg


def stamp_acca(acca: dict) -> dict:
    """Tag an acca with the execution regime that built it.

    Acca-level P&L is only comparable within one execution regime: the
    2026-09-26 boundary closed the ungated fallback, moved staking to
    free-bank sizing at 0.20 and unified the builder/grader paper split.
    Untagged accas pre-date the boundary.
    """
    from racketfactory.regime import EXECUTION_REGIME_ID
    acca["_exec_regime"] = EXECUTION_REGIME_ID
    return acca


def leg_settlement_safe(leg: Mapping[str, Any]) -> bool:
    """Grader-side verdict: AND of the build-time stamp and a fresh recompute.

    Taking the AND (rather than trusting either side) means the two can only
    ever disagree in the safe direction. A leg stamped safe that no longer
    recomputes as safe — the exact drift this module exists to prevent — is
    demoted to paper rather than silently staked.
    """
    if not leg_execution_safe(leg):
        return False
    if STAMP_SAFE in leg and not leg.get(STAMP_SAFE):
        return False
    return True


def acca_execution_safe(acca: Mapping[str, Any]) -> bool:
    """An acca is real only if every leg is real AND nobody flagged it paper.

    ``acca["paper"]`` is the builder's own verdict and is honoured as a hard
    floor. The grader used to ignore it entirely and recompute from legs,
    which is how a slip the builder had explicitly marked ``paper: true``
    ended up on the real-money branch.
    """
    if acca.get("paper") is True:
        return False
    legs = acca.get("legs") or []
    if not legs:
        return False
    return all(leg_settlement_safe(leg) for leg in legs)


def acca_block_reasons(acca: Mapping[str, Any]) -> list[str]:
    """Distinct reasons this acca cannot be staked (empty == executable)."""
    reasons: list[str] = []
    if acca.get("paper") is True:
        reasons.append("acca flagged paper by builder")
    for leg in acca.get("legs") or []:
        block = leg_execution_block(leg)
        if not block and STAMP_SAFE in leg and not leg.get(STAMP_SAFE):
            block = _clean(leg.get(STAMP_BLOCK)) or "stamped unsafe at build time"
        if block and block not in reasons:
            reasons.append(block)
    if not (acca.get("legs") or []):
        reasons.append("acca has no legs")
    return reasons


def committed_stake(open_slips: Iterable[Mapping[str, Any]]) -> float:
    """Real bank already committed to unsettled slips, in bank-%.

    Paper accas commit nothing. Used to size new bets off FREE bank rather
    than total bank: ``state["bank"]`` only moves at settlement, so with
    slips open the old sizing was staking capital that was already at risk.
    """
    total = 0.0
    for slip in open_slips or []:
        if not isinstance(slip, Mapping):
            continue
        for acca in slip.get("accas") or []:
            if not isinstance(acca, Mapping):
                continue
            if not acca_execution_safe(acca):
                continue
            try:
                total += float(acca.get("stake_pct") or 0.0)
            except (TypeError, ValueError):
                continue
    return round(total, 4)


def sanitise_acca_for_replay(acca: Mapping[str, Any]) -> dict:
    """Copy of an acca safe to re-import into ``open_slips``.

    Historical ticket ledgers are replayed back into state when a slip went
    missing. The loop used to re-import them verbatim, which carried paper
    accas back in still holding their *real* ``stake_pct`` (2026-09-20 came
    back with 25.0). Anything not provably executable is re-imported as
    paper with the stake zeroed: it still settles for hit-rate, it can never
    move the bank.
    """
    out = dict(acca)
    if acca_execution_safe(acca):
        return out
    was_paper = acca.get("paper") is True
    out["paper"] = True
    out["_replay_block"] = acca_block_reasons(acca)
    if not was_paper:
        # The ledger claimed this was a real bet and it is not executable, so
        # its stake_pct is fiction: zero it. An acca the ledger ALREADY marked
        # paper keeps its number, which is a paper-track notional and is what
        # the paper bank is supposed to be accounted against.
        out["stake_pct"] = 0.0
    return out
