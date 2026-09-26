"""Report-only tripwires: correct arithmetic, and correct silence.

The hardest requirement here is negative: with 50 settled legs across 9
bet-days, almost every slice MUST come back NOISE. A monitor that issues
confident verdicts on n=13 is worse than no monitor, because it launders a
coin flip into a recommendation.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from racketfactory import tripwire as tw

ROOT = Path(__file__).resolve().parent.parent


def _leg(day, odds, won, prob=None, verdict="ALLOW", bucket="WATCHLIST"):
    return {"match": f"{day} {odds}", "selected_player": "A", "odds": odds,
            "ml_calibrated_prob": prob, "ml_verdict": verdict, "bucket": bucket,
            "_settle_outcome": "WON" if won else "LOST"}


def _history(legs_by_day, paper=False):
    return [{"date": day, "accas": [{"paper": paper, "legs": legs}]}
            for day, legs in legs_by_day.items()]


def test_only_decided_priced_real_legs_are_counted():
    history = [{"date": "2026-09-20", "accas": [
        {"paper": False, "legs": [
            _leg("d", 1.5, True),
            dict(_leg("d", 1.5, True), _settle_outcome="VOID"),
            dict(_leg("d", 1.5, True), _settle_outcome="PENDING"),
            dict(_leg("d", 1.5, True), odds=None),
            dict(_leg("d", 1.5, True), odds=1.0),
            dict(_leg("d", 1.5, True), odds=float("nan")),
        ]},
        {"paper": True, "legs": [_leg("d", 1.5, True)]},
    ]}]
    assert len(tw.leg_records(history)) == 1


def test_profit_is_unit_stake():
    records = tw.leg_records(_history({"2026-09-20": [_leg("a", 2.5, True),
                                                      _leg("a", 2.5, False)]}))
    assert [r["profit"] for r in records] == [1.5, -1.0]


def test_calibration_measures_the_models_own_claim():
    """Promised 75%, delivered 50%, n=20 -> z = -2.58 but still NOISE."""
    legs = tw.leg_records(_history({"2026-09-20":
                                    [_leg("a", 1.4, i < 10, prob=0.75) for i in range(20)]}))
    row = tw.calibration_row("test", legs)
    assert row["n"] == 20
    assert row["promised_pct"] == 75.0 and row["realised_pct"] == 50.0
    assert row["gap_pp"] == -25.0
    assert row["z"] == pytest.approx(-2.58, abs=0.01)
    assert row["status"] == tw.STATUS_NOISE, "n=20 is under the n>=30 bar"


def test_the_bar_is_what_turns_a_number_into_a_verdict():
    over = tw.leg_records(_history({"2026-09-20":
                                    [_leg("a", 1.4, i < 15, prob=0.75) for i in range(30)]}))
    assert tw.calibration_row("test", over)["status"] == tw.STATUS_BLEEDING


def test_well_calibrated_slice_is_ok():
    legs = tw.leg_records(_history({"2026-09-20":
                                    [_leg("a", 1.4, i < 24, prob=0.75) for i in range(32)]}))
    assert tw.calibration_row("test", legs)["status"] == tw.STATUS_OK


def test_over_delivery_is_never_an_alarm():
    """Only over-confidence costs money; beating your own forecast is fine."""
    legs = tw.leg_records(_history({"2026-09-20":
                                    [_leg("a", 1.4, i < 32, prob=0.60) for i in range(32)]}))
    row = tw.calibration_row("test", legs)
    assert row["z"] > 0 and row["status"] == tw.STATUS_OK


def test_unscored_legs_do_not_dilute_calibration():
    legs = tw.leg_records(_history({"2026-09-20": [
        _leg("a", 1.4, True, prob=0.75), _leg("a", 1.4, False, prob=None)]}))
    assert tw.calibration_row("test", legs)["n"] == 1


def test_slice_roi_and_breakeven():
    legs = tw.leg_records(_history({"2026-09-20": [
        _leg("a", 2.0, True), _leg("a", 2.0, False)]}))
    row = tw.slice_row("test", legs)
    assert row["hit_pct"] == 50.0 and row["avg_odds"] == 2.0
    assert row["breakeven_pct"] == 50.0 and row["roi_pct"] == 0.0


def test_single_leg_slice_has_no_z_and_no_verdict():
    legs = tw.leg_records(_history({"2026-09-20": [_leg("a", 2.0, True)]}))
    row = tw.slice_row("test", legs)
    assert row["z"] is None and row["status"] == tw.STATUS_NOISE


def test_zero_variance_slice_has_no_z():
    """Every leg identical: sd is 0, so z is undefined rather than infinite."""
    legs = tw.leg_records(_history({"2026-09-20": [_leg("a", 2.0, True)] * 5}))
    assert tw.slice_row("test", legs)["z"] is None


def test_window_and_losing_streak():
    # window 0 = (09-05, 09-26], window 1 = (08-15, 09-05]
    legs = tw.leg_records(_history({
        "2026-09-20": [_leg("a", 1.5, False, verdict="BOOST")] * 3,
        "2026-09-01": [_leg("a", 1.5, False, verdict="BOOST")] * 3,
        "2026-07-01": [_leg("a", 1.5, True, verdict="BOOST")] * 3,
    }))
    recent = tw.window_legs(legs, days=21, as_of=date(2026, 9, 26))
    assert len(recent) == 3 and all(not r["won"] for r in recent)
    assert tw.losing_streak(legs, "verdict", "BOOST", as_of=date(2026, 9, 26)) == 2


def test_a_quiet_window_breaks_the_streak_rather_than_extending_it():
    legs = tw.leg_records(_history({"2026-09-26": [_leg("a", 1.5, False)] * 3}))
    assert tw.losing_streak(legs, "verdict", "ALLOW", as_of=date(2026, 9, 26)) == 1


def test_undated_history_does_not_crash_the_window():
    legs = tw.leg_records([{"accas": [{"paper": False, "legs": [_leg("a", 1.5, True)]}]}])
    assert len(tw.window_legs(legs, days=21)) == 1
    assert tw.losing_streak(legs, "verdict", "ALLOW") == 0


def test_report_on_empty_history_is_quiet_and_safe():
    report = tw.build_report([])
    assert report["n_legs"] == 0 and report["enforced"] is False
    lines = tw.render_report(report)
    assert any("report-only" in line for line in lines)
    assert not any("ALARM" in line for line in lines)


def test_nothing_is_ever_enforced():
    assert tw.build_report([])["enforced"] is False


# ------------------------------------------------------ against real data --

def test_live_ledger_reproduces_the_known_calibration_hole():
    """The finding the deep dive reported, re-derived by the shipped code."""
    state_file = ROOT / "localdata" / "auto_tickets_state.json"
    if not state_file.exists():
        pytest.skip("no live state file in this checkout")
    history = json.loads(state_file.read_text()).get("history", [])
    report = tw.build_report(history)
    if report["n_legs"] < 40:
        pytest.skip("ledger too short for the documented figures")
    assert report["calibration"]["all"]["n"] == 50
    assert report["calibration"]["all"]["realised_pct"] == 66.0
    hole = [b for b in report["calibration"]["bands"] if b["band"] == "0.70-0.75"][0]
    assert hole["n"] == 13
    assert hole["realised_pct"] == pytest.approx(30.8, abs=0.1)
    assert hole["z"] == pytest.approx(-3.5, abs=0.15)
    # ...and it is STILL tagged NOISE, because n=13 < 30. That restraint is
    # the feature: the number is alarming, the sample is not yet decisive.
    assert hole["status"] == tw.STATUS_NOISE

    by_verdict = {r["slice"]: r for r in report["by_verdict"]["rows"]}
    assert by_verdict["BOOST"]["n"] == 30
    assert by_verdict["BOOST"]["roi_pct"] == pytest.approx(-13.83, abs=0.05)
    assert by_verdict["ALLOW"]["roi_pct"] == pytest.approx(11.05, abs=0.05)
    assert all(row["status"] == tw.STATUS_NOISE or row["n"] >= tw.MIN_N
               for row in report["by_verdict"]["rows"])


def test_a_losing_slice_is_never_labelled_ok():
    """"BOOST -13.83% OK" is how a broken gate survives a review."""
    legs = tw.leg_records(_history({"2026-09-20":
                                    [_leg("a", 1.5, i < 19) for i in range(32)]}))
    row = tw.slice_row("BOOST", legs)
    assert row["roi_pct"] < 0 and -tw.Z_WATCH > row["z"] > tw.Z_WATCH
    assert row["status"] == tw.STATUS_INCONCLUSIVE


def test_ok_is_reserved_for_at_or_above_expectation():
    legs = tw.leg_records(_history({"2026-09-20":
                                    [_leg("a", 2.5, i < 20) for i in range(32)]}))
    row = tw.slice_row("good", legs)
    assert row["roi_pct"] > 0 and row["status"] == tw.STATUS_OK


def test_the_worst_calibration_cell_is_named_even_when_under_the_bar():
    history = _history({"2026-09-20": [_leg("a", 1.4, i < 4, prob=0.74)
                                       for i in range(13)]})
    lines = tw.render_report(tw.build_report(history))
    worst = [line for line in lines if "worst calibration cell" in line]
    assert worst and "PROVISIONAL" in worst[0]
    assert "0.70-0.75" in worst[0]
