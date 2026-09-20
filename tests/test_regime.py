"""Regime-scoped audit + ML registry: old regimes never veto the current one.

Semantics under test (see src/racketfactory/regime.py):
- pick rows are tagged with _regime
- the rolling audit scopes top-level stats to the current regime
- the ML context registry is built from those top-level stats, so an old
  regime's losses can never veto a new regime's picks
- a fresh regime (no current-regime history) yields no vetoes
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from racketfactory.regime import REGIME_ID, row_regime  # noqa: E402
from racketfactory.ml import build_context_registry, source_weights_from_audit  # noqa: E402


def test_regime_id_is_set():
    assert REGIME_ID and REGIME_ID.strip()


def test_row_regime_untagged_is_current():
    assert row_regime({}) == REGIME_ID
    assert row_regime({"_regime": ""}) == REGIME_ID
    assert row_regime({"_regime": None}) == REGIME_ID
    assert row_regime(None) == REGIME_ID


def test_row_regime_tagged_other_stays_other():
    assert row_regime({"_regime": "legacy-1"}) == "legacy-1"
    assert row_regime({"_regime": "  legacy-1  "}) == "legacy-1"


def test_fresh_regime_registry_has_no_vetoes():
    """Legacy data present under by_regime must NOT produce vetoes.

    Simulates the audit shape after regime scoping: top-level groups are
    current-regime only (empty on a fresh birth); by_regime carries the
    legacy summary for visibility only.
    """
    audit = {
        "regime": REGIME_ID,
        "by_regime": {
            "legacy-1": {"settled_picks": 63, "wins": 20, "roi": -0.206731, "hit_rate": 0.32},
            REGIME_ID: {"settled_picks": 0, "wins": 0, "roi": None, "hit_rate": None},
        },
        "by_tour": {},
        "by_series": {},
        "by_surface": {},
        "by_source": {},
        "by_bucket": {},
    }
    registry = build_context_registry(audit)
    assert all(len(v) == 0 for v in registry.values())


def test_legacy_surface_veto_does_not_leak_into_current_regime():
    """The 2026-09-20 incident: 'surface Hard VETO roi -0.206731 n=63' was
    legacy data vetoing the new regime's whole slate. After scoping, an
    empty current-regime surface group produces no context at all."""
    audit = {
        "regime": REGIME_ID,
        "by_regime": {
            "legacy-1": {
                "by_surface": {"Hard": {"settled_picks": 63, "wins": 20, "roi": -0.206731}},
            },
            REGIME_ID: {},
        },
        # top-level = current regime only -> no legacy Hard stats here
        "by_tour": {},
        "by_series": {},
        "by_surface": {},
        "by_source": {},
        "by_bucket": {},
    }
    registry = build_context_registry(audit)
    assert not registry.get("surface", {}).get("Hard")


def test_current_regime_stats_do_produce_vetoes_when_bad():
    """Once the CURRENT regime accumulates its own bad data, vetoes return."""
    audit = {
        "regime": REGIME_ID,
        "by_tour": {},
        "by_series": {},
        "by_surface": {
            "Hard": {"settled_picks": 55, "wins": 18, "roi": -0.25, "hit_rate": 0.33},
        },
        "by_source": {},
        "by_bucket": {},
    }
    registry = build_context_registry(audit)
    ctx = registry["surface"]["Hard"]
    assert ctx["verdict"] == "VETO"  # n>=50 and roi <= -0.15


def test_source_weights_fresh_regime_uses_defaults():
    weights = source_weights_from_audit({"by_source": {}})
    assert weights["Bzzoiro"] == 0.55
    assert weights["BetClan"] == 0.35
    assert weights["Forebet"] == 0.45


def test_source_weights_current_regime_overrides_defaults():
    # 35/40 settled wins in the CURRENT regime -> Wilson LB high -> weight up
    audit = {
        "by_source": {
            "BetClan": {"settled_picks": 40, "wins": 35, "roi": 0.10, "hit_rate": 0.875},
        },
    }
    weights = source_weights_from_audit(audit)
    assert weights["BetClan"] > 0.55


def test_no_legacy_prior_when_no_current_regime_clv():
    """Born-again: with no current-regime CLV data, raw confidence must NOT be
    dragged by the old hardcoded legacy priors (High 66.23% / Medium 71.04% /
    Low 51.04% — calibrated on pre-genesis picks)."""
    from racketfactory import ml as mlmod

    pick = {
        "confidence": 88.41,
        "pred_confidence": "High",
        "source": "",
        "cross_source_agree": "Both",
    }
    prob = mlmod.calibrated_prob_from_history(pick, {}, clv={})
    # Legacy prior would have given 0.6623*0.6 + 0.8841*0.4 = 0.751
    assert prob > 0.85


def test_pick_row_is_tagged_with_regime():
    import mine_edges

    row = pd.Series({
        "player_a": "A Player",
        "player_b": "B Player",
        "player_home": "A Player",
        "player_away": "B Player",
        "match_date": "2026-09-21",
        "match_time": "12:00",
        "tournament": "Test Open",
        "predicted_winner": "player_a",
        "source": "Forebet",
        "tour": "ATP",
        "odds_a": 1.8,
        "odds_b": 2.0,
    })
    pick = mine_edges.select_player_from_row(row, "2026-09-21")
    assert pick["_regime"] == REGIME_ID
    assert pick["selected_side"] == "player_a"
