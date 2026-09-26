"""select_accas() must be build_accas() with the knobs pulled out — exactly.

The fixture was captured from the PRE-refactor engine on seven real
pick-days (2026-09-20..26), in two variants each (as-is, and with every
other pick marked late so the paper track is exercised). If a single acca,
price, probability, Kelly fraction, leg order or paper reason moves, this
fails.

This is what makes the knobs safe to add: without it, "I only extracted some
constants" is a claim, not a fact.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import auto_tickets as at  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "auto_tickets_golden.json"


@pytest.fixture
def deterministic_ml(monkeypatch):
    """Pin the ML layer so the golden depends on the engine, not on state."""
    monkeypatch.setattr(at, "_ML_AVAILABLE", True)
    monkeypatch.setattr(at, "load_audit_rolling", lambda: {"stub": True})
    monkeypatch.setattr(at, "build_context_registry", lambda a: {"stub": True})
    monkeypatch.setattr(at, "source_weights_from_audit", lambda a: {"stub": 1.0})
    monkeypatch.setattr(at, "load_clv_rolling", lambda: {})
    monkeypatch.setattr(at, "get_min_ev_real", lambda: 0.02)
    monkeypatch.setattr(at, "monitor_performance", lambda: {})
    monkeypatch.setattr(at, "MIN_ODDS_PER_LEG", 1.30)

    def score_pick_strengths(pick, registry, weights):
        try:
            strength = float(pick.get("ml_strength_score") or 0)
        except (TypeError, ValueError):
            strength = 0.0
        verdict = str(pick.get("ml_verdict") or "")
        return {"strength_score": strength, "should_veto": verdict == "VETO",
                "should_boost": verdict == "BOOST", "w_score": strength}

    monkeypatch.setattr(at, "score_pick_strengths", score_pick_strengths)
    monkeypatch.setattr(at, "ml_predict_proba",
                        lambda p, r, w, clv=None: float(p.get("ml_calibrated_prob") or 0.6))
    monkeypatch.setattr(at, "ml_ev", lambda p, r, w, clv=None: p.get("ml_ev"))


def _summarise(track):
    return [{
        "type": a.get("type"), "odds": a.get("odds"), "prob": a.get("prob"),
        "kelly": a.get("kelly"), "paper": a.get("paper"),
        "legs": [{"match": leg.get("match"), "sel": leg.get("selected_player"),
                  "odds": leg.get("odds"), "paper_reason": leg.get("_paper_reason")}
                 for leg in a.get("legs", [])],
    } for a in track]


def _load():
    return json.loads(FIXTURE.read_text())


def _pool(fixture, day, variant):
    pool = json.loads(json.dumps(fixture["pools"][day]))
    if variant == "late_half":
        for index, pick in enumerate(pool):
            if index % 2 == 0:
                pick["_late_start"] = True
    return pool


# The golden file is the PRE-refactor record and is never re-baselined in
# place. Where current behaviour deliberately differs, the divergence is
# named here with its reason, so a behaviour change can never hide inside a
# refactor. Anything not listed must match the capture exactly.
INTENTIONAL_DIVERGENCES = {
    "2026-09-20:plain": (
        "fallback_2leg_mutual no longer bypasses the acca odds gates. This "
        "pair is 1.80 x 1.86 = 3.35 with two BOOST legs, so the boost cap of "
        "3.00 applies and the 1.2x overshoot needs prob_prod >= 0.65 (actual "
        "0.69 x 0.66 = 0.46). The PRIMARY path already rejected it for exactly "
        "that reason — the fallback used to re-admit it with no gate at all, "
        "which is how the 2026-09-17 acca went on at 4.08 against a 4.00 cap."
    ),
}


@pytest.mark.parametrize("case", sorted(_load()["expected"]))
def test_select_accas_matches_pre_refactor_output(case, deterministic_ml):
    fixture = _load()
    day, variant = case.split(":")
    pool = _pool(fixture, day, variant)
    priced, paper, sorted_pool = at.select_accas(pool)
    got = {
        "n_playable": len(pool),
        "priced": _summarise(priced),
        "paper": _summarise(paper),
        "sorted_head": [p.get("match") for p in sorted_pool[:12]],
    }
    expected = fixture["expected"][case]
    if case in INTENTIONAL_DIVERGENCES:
        assert got != expected, (
            f"{case} is listed as an intentional divergence but now matches "
            f"the pre-refactor capture — delete the entry")
        # The divergence must be confined to the ungated fallback and nothing
        # else: pool order and the paper track are still byte-identical.
        assert got["sorted_head"] == expected["sorted_head"]
        assert got["paper"] == expected["paper"]
        assert all("fallback" in a["type"] for a in expected["priced"])
        assert got["priced"] == []
        return
    assert got == expected


def test_ungated_fallback_is_closed(deterministic_ml):
    """A last-resort acca must clear the same bar as a first-choice one.

    2026-09-17 staked fallback_2leg_mutual at 4.08 with MAX_ACCA_ODDS = 4.00
    and returned +88 points — a large share of the entire bank gain came from
    a bet the engine's own rules rejected. It won; it was still illegal.
    """
    fixture = _load()
    pool = _pool(fixture, "2026-09-20", "plain")
    priced, _, _ = at.select_accas(pool)
    assert priced == []

    # Widen the boost cap far enough to admit 3.35 and it comes straight back,
    # proving the gate (not some unrelated change) is what excluded it.
    widened, _, _ = at.select_accas(_pool(fixture, "2026-09-20", "plain"),
                                    at.AccaKnobs(max_acca_odds_boost=4.0))
    assert [a["odds"] for a in widened] == [3.35]


def test_build_accas_shim_is_identical_to_select_accas(deterministic_ml):
    fixture = _load()
    for day in fixture["pools"]:
        a = _summarise(at.select_accas(_pool(fixture, day, "plain"))[0])
        b = _summarise(at.build_accas(_pool(fixture, day, "plain"))[0])
        assert a == b


def test_default_knobs_equal_the_live_constants():
    knobs = at.live_knobs()
    assert knobs.max_accas == at.MAX_ACCAS
    assert knobs.min_odds_boost == at.MIN_ODDS_BOOST
    assert knobs.min_acca_odds == at.MIN_ACCA_ODDS
    assert knobs.min_acca_odds_boost == at.MIN_ACCA_ODDS_BOOST
    assert knobs.max_acca_odds == at.MAX_ACCA_ODDS
    assert knobs.max_acca_odds_boost == at.MAX_ACCA_ODDS_BOOST
    assert knobs.effective_min_odds_per_leg() == at.MIN_ODDS_PER_LEG


def test_knobs_actually_bite(deterministic_ml):
    """A counterfactual must be runnable WITHOUT editing the engine.

    This is the capability the deep dive said was missing: every constant was
    hardcoded inside build_accas, so no replay could ask "what would one acca
    a day have done?" — and so the constants were never tested.
    """
    fixture = _load()
    day = "2026-09-22"  # the only fixture day that fills all four slots
    baseline = at.select_accas(_pool(fixture, day, "plain"))[0]
    assert len(baseline) == 4

    one = at.select_accas(_pool(fixture, day, "plain"),
                          at.AccaKnobs(max_accas=1))[0]
    assert len(one) == 1
    assert _summarise(one)[0] == _summarise(baseline)[0], \
        "capping slots must keep the top-ranked acca unchanged"

    # Raising the floor on BOTH the ordinary and the BOOST path (BOOST has
    # its own, much lower, floor) empties the day — including the fallback,
    # which is now gated too.
    tight = at.select_accas(_pool(fixture, day, "plain"),
                            at.AccaKnobs(min_acca_odds=3.9, min_acca_odds_boost=3.9,
                                         max_acca_odds=4.0, max_acca_odds_boost=4.0))[0]
    assert tight == []

    # Raising only the ordinary floor does NOT empty it, because every acca
    # here carries a BOOST leg and BOOST substitutes its own floor of 1.18.
    # That is the "five overrides on one label" finding, made visible.
    boost_escape = at.select_accas(_pool(fixture, day, "plain"),
                                   at.AccaKnobs(min_acca_odds=3.9))[0]
    assert boost_escape and all(a["odds"] < 3.9 for a in boost_escape)


def test_boost_privileges_can_be_switched_off(deterministic_ml):
    """BOOST unlocks five overrides on a label measured at -13.8% ROI."""
    fixture = _load()
    for day in fixture["pools"]:
        pool = _pool(fixture, day, "plain")
        if not any(str(p.get("ml_verdict")) == "BOOST" for p in pool):
            continue
        off = at.select_accas(pool, at.AccaKnobs(boost_privileges=False))[0]
        # No acca may rely on a BOOST-only relaxation: every leg clears the
        # ordinary floor and every acca clears the ordinary minimum.
        for acca in off:
            for leg in acca["legs"]:
                assert float(leg["odds"]) >= at.MIN_ODDS_PER_LEG
            if acca.get("odds"):
                assert acca["odds"] >= at.MIN_ACCA_ODDS
