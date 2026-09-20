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
