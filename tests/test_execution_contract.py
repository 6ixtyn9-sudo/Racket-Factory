"""The builder and the grader must agree on what may move the real bank.

These are regression tests for a live incident: the 2026-09-20 slip sat in
``open_slips`` for six days because ``auto_tickets.py`` called it paper
(late start) while ``auto_tickets_grade.py`` called it real (BetExplorer
price present). The disagreement was one-directional — such an acca could be
booked as a LOSS but never as a WIN.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from racketfactory.execution import (  # noqa: E402
    acca_block_reasons,
    acca_execution_safe,
    committed_stake,
    leg_execution_block,
    leg_execution_safe,
    leg_market_odds,
    leg_settlement_safe,
    sanitise_acca_for_replay,
    stamp_leg,
)

import auto_tickets as at  # noqa: E402
import auto_tickets_grade as g  # noqa: E402


def _leg(**kw):
    leg = {"match": "A vs B", "selected_player": "A", "odds": 1.5,
           "odds_source": "BetExplorer"}
    leg.update(kw)
    return leg


# --------------------------------------------------------------- the price --

@pytest.mark.parametrize("leg,expected", [
    (_leg(), 1.5),
    (_leg(odds_source="TheOddsAPI"), 1.5),
    (_leg(odds_source="OddsPortal"), 1.5),
    (_leg(odds_source="Bzzoiro"), 1.5),
    (_leg(odds_source="ML_Estimated"), None),
    (_leg(odds_source="ScrapedFallback"), None),
    (_leg(odds_source="nan"), None),
    (_leg(odds_source=None), None),
    (_leg(odds=None), None),
    (_leg(odds=1.0), None),
    (_leg(odds=0.5), None),
    (_leg(odds="not a number"), None),
    (_leg(odds=float("nan")), None),
    (_leg(odds=float("inf")), None),
    (_leg(odds_reject_reason="missing selected-side odds"), None),
    (_leg(_is_paper=True), None),
])
def test_market_odds_rejects_everything_unproven(leg, expected):
    assert leg_market_odds(leg) == expected


def test_nan_and_inf_never_reach_the_bank():
    """NaN fails every comparison, so a naive `odds > 1.0` lets it through."""
    assert leg_market_odds(_leg(odds=float("nan"))) is None
    assert not leg_execution_safe(_leg(odds=float("nan")))


# ----------------------------------------------------- the paper/real line --

def test_late_but_priced_leg_is_paper_to_BOTH_sides():
    """The exact 2026-09-20 shape: late start, real BetExplorer price."""
    leg = _leg(odds=1.22, _late_start=True)
    assert leg_market_odds(leg) == 1.22        # the price is real...
    assert not leg_execution_safe(leg)          # ...but the leg is not tradable
    assert not g._leg_odds_trusted(leg)         # grader agrees (it did NOT before)
    assert at._leg_paper_reason(leg) == leg_execution_block(leg)


def test_kickoff_unknown_leg_is_paper():
    leg = _leg(_kickoff_unknown=True)
    assert leg_market_odds(leg) == 1.5
    assert not leg_execution_safe(leg)
    assert "kickoff unknown" in leg_execution_block(leg)


def test_builder_and_grader_agree_on_every_leg_shape():
    """Property test over the cross-product of every flag combination."""
    import itertools
    sources = ["BetExplorer", "TheOddsAPI", "nan", "ML_Estimated", None]
    odds = [1.5, 1.0, None, float("nan")]
    flags = list(itertools.product([False, True], repeat=4))
    checked = 0
    for src, odd, (late, unknown, is_paper, reject) in itertools.product(sources, odds, flags):
        leg = _leg(odds=odd, odds_source=src)
        if late:
            leg["_late_start"] = True
        if unknown:
            leg["_kickoff_unknown"] = True
        if is_paper:
            leg["_is_paper"] = True
        if reject:
            leg["odds_reject_reason"] = "missing selected-side odds"
        builder_says_real = at._leg_real_odds(leg) is not None and not at._leg_paper_reason(leg)
        grader_says_real = g._leg_odds_trusted(leg)
        assert builder_says_real == grader_says_real, leg
        checked += 1
    assert checked == len(sources) * len(odds) * len(flags)


def test_paper_reason_wording_is_unchanged_for_the_old_cases():
    """The ticket .txt contract: these two strings predate the refactor."""
    assert leg_execution_block(_leg(_late_start=True)) == \
        "already started at generation time (stale price)"
    assert leg_execution_block(_leg(odds_source="nan")) == "no trusted market price"
    assert leg_execution_block(_leg(_is_paper=True)) == "no trusted market price"
    assert leg_execution_block(_leg()) == ""


# ------------------------------------------------------------- the stamp --

def test_stamp_records_the_verdict_and_drift_fails_closed():
    safe = stamp_leg(_leg())
    assert safe["_execution_safe"] is True and "_paper_reason" not in safe
    assert leg_settlement_safe(safe)

    # Leg was safe at build time, has since become late: AND-ing the stamp
    # with a fresh recompute demotes it rather than staking it.
    drifted = dict(safe, _late_start=True)
    assert not leg_settlement_safe(drifted)

    # Leg stamped UNSAFE that now recomputes clean is still not staked.
    stale_stamp = _leg()
    stale_stamp["_execution_safe"] = False
    stale_stamp["_execution_block"] = "was late at build time"
    assert leg_execution_safe(stale_stamp)
    assert not leg_settlement_safe(stale_stamp)


def test_stamp_clears_a_stale_paper_reason():
    leg = _leg(_late_start=True)
    stamp_leg(leg)
    assert leg["_paper_reason"]
    leg.pop("_late_start")
    stamp_leg(leg)
    assert "_paper_reason" not in leg and leg["_execution_safe"] is True


# ---------------------------------------------------------------- the acca --

def test_builder_paper_flag_is_a_hard_floor():
    """The grader used to ignore acca['paper'] entirely and recompute."""
    acca = {"paper": True, "odds": None, "stake_pct": 25.0,
            "legs": [_leg(odds=1.22), _leg(odds=1.18)]}
    assert all(leg_execution_safe(leg) for leg in acca["legs"])
    assert not acca_execution_safe(acca)
    assert "acca flagged paper by builder" in acca_block_reasons(acca)


def test_legless_acca_is_never_executable():
    assert not acca_execution_safe({"odds": 2.0, "legs": []})
    assert not acca_execution_safe({"odds": 2.0})


def test_committed_stake_counts_only_real_open_exposure():
    slips = [{"accas": [
        {"stake_pct": 10.0, "legs": [_leg()]},                       # real
        {"stake_pct": 25.0, "paper": True, "legs": [_leg()]},        # paper flag
        {"stake_pct": 7.0, "legs": [_leg(_late_start=True)]},        # late leg
        {"stake_pct": "junk", "legs": [_leg()]},                     # garbage
    ]}]
    assert committed_stake(slips) == 10.0
    assert committed_stake([]) == 0.0
    assert committed_stake(None) == 0.0
    assert committed_stake(["not a dict"]) == 0.0


# --------------------------------------------------------------- the replay --

def test_replay_zeroes_a_fictional_real_stake():
    """A ledger claiming REAL on a non-executable acca is fiction."""
    acca = {"paper": False, "stake_pct": 25.0, "odds": 1.44,
            "legs": [_leg(_late_start=True)]}
    out = sanitise_acca_for_replay(acca)
    assert out["paper"] is True and out["stake_pct"] == 0.0
    assert out["_replay_block"]
    assert acca["stake_pct"] == 25.0  # input untouched


def test_replay_keeps_a_genuine_paper_notional():
    """A ledger that already said paper carries a paper-bank notional."""
    acca = {"paper": True, "stake_pct": 25.0, "odds": None,
            "legs": [_leg(odds=None, odds_source="nan")]}
    out = sanitise_acca_for_replay(acca)
    assert out["paper"] is True and out["stake_pct"] == 25.0


def test_replay_passes_a_real_acca_through_untouched():
    acca = {"paper": False, "stake_pct": 12.5, "odds": 2.25,
            "legs": [_leg(), _leg(match="C vs D", selected_player="C")]}
    assert sanitise_acca_for_replay(acca) == acca


# ------------------------------------------------- the live state file --

def test_the_real_stuck_slip_is_now_classified_paper():
    """Guard against regressing on the actual incident, using real data."""
    state_file = ROOT / "localdata" / "auto_tickets_state.json"
    if not state_file.exists():
        pytest.skip("no live state file in this checkout")
    state = json.loads(state_file.read_text())
    for slip in state.get("open_slips", []):
        for acca in slip.get("accas", []):
            if acca.get("paper") is True:
                assert not acca_execution_safe(acca), (
                    f"{slip.get('date')}: an acca the builder wrote as paper is "
                    f"executable to the grader — the 2026-09-20 bug is back")
