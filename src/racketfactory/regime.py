"""Pipeline regime identity for picks and audit statistics.

A "regime" is a generation of pick-generation logic (sources, agree rules,
EV gates, ML calibration). When the pick logic changes materially, bump
``REGIME_ID`` and start from a clean slate: delete the previous regime's
``picks_*.json`` ledgers (the "born again" step) so the audit/ML history
describes the new system only.

Semantics
---------
- ``mine_edges`` tags every emitted pick row with ``_regime = REGIME_ID``.
- The rolling audit scopes its top-level stats (overall, by_tour, by_surface,
  by_source, ...) to the CURRENT regime only, so "daily ROI" always describes
  the system as it is right now. Every regime present on disk gets its own
  summary under ``by_regime`` so no number is ambiguous about which system
  produced it.
- The ML context registry (vetoes/weights) is built from those current-regime
  stats: old-regime results can never veto new-regime picks. A fresh regime
  (n<20 settled) yields no vetoes (``context_verdict`` -> UNKNOWN).
- Rows WITHOUT a ``_regime`` tag (emitted before tagging existed) are treated
  as the current regime — the transition cohort.

To bump a regime: change ``REGIME_ID`` and delete the previous regime's
``picks_*.json`` / ``picks_forecast_*.json`` ledgers in the same change.
"""

REGIME_ID = "genesis-2026-09-20"

# --------------------------------------------------------------------------
# Execution regime — a SEPARATE axis from the pick regime above.
# --------------------------------------------------------------------------
# REGIME_ID identifies the generation of *pick-generation* logic. It governs
# the audit statistics and the ML context registry, and bumping it throws
# away the settled pick history that feeds calibration.
#
# EXECUTION_REGIME_ID identifies the generation of *ticket-execution* logic:
# how playable picks are assembled into accas, how they are staked, and how
# they are settled. These are independent. On 2026-09-26 the execution layer
# changed substantially (the ungated fallback was closed, staking moved to
# free-bank sizing at 0.20, and the builder/grader paper split was unified)
# while pick generation was left behaviourally untouched.
#
# The distinction matters because the two have opposite correct responses to
# that change:
#
#   pick regime      UNCHANGED -> do NOT bump. The 141 settled picks and 50
#                    staked legs remain valid evidence about pick quality,
#                    and they are the scarce resource every pre-registered
#                    hypothesis is waiting on. Bumping would reset n to zero
#                    and blind the ML context registry for weeks.
#   execution regime CHANGED   -> bump. Acca-level P&L, bank trajectory and
#                    drawdown from before the boundary describe an engine
#                    that no longer exists, and one of those accas was only
#                    placed because of a bug.
#
# So acca-level performance is measured from the boundary forward, while
# leg-level calibration keeps accruing unbroken across it.
EXECUTION_REGIME_ID = "exec-2026-09-26"


def row_execution_regime(row: dict | None) -> str:
    """Execution regime of an archived acca / slip.

    Untagged rows pre-date the boundary and belong to the regime that ran
    before it, NOT the current one — the opposite default to row_regime(),
    because here the absence of a tag is positive evidence that the row was
    written by the old engine.
    """
    if row is None:
        return PRE_EXECUTION_REGIME_ID
    val = row.get("_exec_regime")
    if val is None:
        return PRE_EXECUTION_REGIME_ID
    s = str(val).strip()
    if not s or s.lower() in _EMPTY:
        return PRE_EXECUTION_REGIME_ID
    return s


PRE_EXECUTION_REGIME_ID = "exec-pre-2026-09-26"

_EMPTY = {"", "nan", "<na>", "none"}


def row_regime(row: dict | None) -> str:
    """Regime of an archived pick row.

    Untagged rows (written before regime tagging existed) belong to the
    current regime.
    """
    if row is None:
        return REGIME_ID
    val = row.get("_regime")
    if val is None:
        return REGIME_ID
    s = str(val).strip()
    if not s or s.lower() in _EMPTY:
        return REGIME_ID
    return s
