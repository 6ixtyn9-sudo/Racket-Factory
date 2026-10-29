"""Current Racket admission policy. All inputs are synthetic and offline."""
import copy

import pytest

from racketfactory import ml
from racketfactory.leg_policy import MIN_LEG_ODDS, restore_ev_only_bucket, valid_leg_price
from scripts import auto_tickets as tickets
from scripts import mine_edges as miner


def pick(name="A vs B", odds=1.30, ev=None, **kw):
    result = {"match": name, "selected_player": name.split(" vs ")[0],
              "bucket": "WATCHLIST", "edge_verdict": "WATCHLIST", "pick": "WATCHLIST",
              "odds": odds, "odds_source": "BetExplorer", "confidence": 75,
              "prediction_prob": .75, "ml_calibrated_prob": .75, "ml_ev": ev,
              "ml_verdict": "ALLOW", "ml_strength_score": 0, "source_count": 1}
    result.update(kw)
    return result


@pytest.fixture
def isolated_ml(monkeypatch, tmp_path):
    monkeypatch.setattr(ml, "LOCALDATA", tmp_path)
    monkeypatch.setattr(ml, "load_audit_rolling", lambda: {})
    monkeypatch.setattr(ml, "load_clv_rolling", lambda: {})
    monkeypatch.setattr(ml, "monitor_performance", lambda: {})
    monkeypatch.setattr(tickets, "load_audit_rolling", lambda: {})
    monkeypatch.setattr(tickets, "load_clv_rolling", lambda: {})


@pytest.mark.parametrize("odds,ok", [(1.2999, False), (1.30, True), (1.3001, True),
    (None, False), ("", False), ("bad", False), (float("nan"), False),
    (float("inf"), False), (float("-inf"), False), (True, False), (1, False), (0, False)])
def test_floor_in_export_and_builder(isolated_ml, odds, ok):
    assert valid_leg_price(odds) is ok
    p = pick(odds=odds)
    mark = copy.deepcopy(p)
    miner.mark_below_leg_floor(mark)
    if odds is not None and not ok:
        assert mark["bucket"] == "SKIPPED_LOW_ODDS"
    assert tickets.is_playable(p) is ok
    diag = {}
    tickets.select_accas([p], diagnostics=diag)
    assert diag["priced_eligible"] == int(ok)


def test_floor_is_identical_for_boost_and_replay_knobs(isolated_ml, monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_MIN_ODDS", "1.01")
    assert ml.get_min_odds_base() == tickets._get_min_odds_per_leg() == MIN_LEG_ODDS == 1.30
    knobs = tickets.AccaKnobs(min_odds_per_leg=1.01, min_odds_boost=1.01)
    assert knobs.effective_min_odds_per_leg() == 1.30
    diag = {}
    tickets.select_accas([pick(odds=1.29, ml_verdict="BOOST")], knobs, diagnostics=diag)
    assert diag["priced_eligible"] == 0


@pytest.mark.parametrize("ev", [-.30, -.01, 0, .01, .20, None])
def test_ev_does_not_veto_or_rank_ticket_legs(isolated_ml, ev):
    picks = [pick("A vs B", ev=ev), pick("C vs D", odds=1.45, ev=ev)]
    assert all(tickets.is_playable(p) for p in picks)
    diag = {}
    priced, paper, _ = tickets.select_accas(picks, diagnostics=diag)
    assert diag["priced_eligible"] == 2
    assert len(priced) == 1 and paper == []


def test_ev_does_not_control_mixed_evidence_cap(isolated_ml):
    p = pick(odds=2.3, ml_calibrated_prob=.56, confidence=65,
             edge_n=54, roi_estimate="17%")
    assert {tickets.dynamic_max_odds(dict(p, ml_ev=ev)) for ev in (-.3, 0, .5, None)} == {3.0}
    # Probability, confidence, odds and evidence remain independently tested.
    diag = {}
    tickets.select_accas([p], diagnostics=diag)
    assert diag["priced_eligible"] == 1
    p["ml_calibrated_prob"] = .54
    diag = {}
    tickets.select_accas([p], diagnostics=diag)
    assert diag["priced_eligible"] == 0
    assert diag["priced_exclusions"]["probability below minimum"] == 1


def test_ev_only_label_restored_but_other_dead_edges_stay_blocked(isolated_ml):
    p = pick(odds=1.5, bucket="SKIPPED_DEAD_EDGE", skip_reason="negative EV (-0.100 < 0.020)")
    recovered = restore_ev_only_bucket(p)
    assert recovered["bucket"] == "WATCHLIST" and recovered["legacy_ev_only_skip"]
    assert p["bucket"] == "SKIPPED_DEAD_EDGE"  # input/history not mutated
    admitted = {}
    assert tickets.is_playable(p, admitted=admitted)
    assert admitted["bucket"] == "WATCHLIST" and admitted["ml_verdict"] != "VETO"
    assert p["bucket"] == "SKIPPED_DEAD_EDGE"
    paired = pick("C vs D", odds=1.45)
    priced, _, _ = tickets.select_accas([admitted, paired])
    assert len(priced) == 1
    assert all(leg["bucket"] == "WATCHLIST" for leg in priced[0]["legs"])
    for reason in ("short odds 1.04 < 1.15 hard floor", "FADE THIS SIGNAL", "negative EV (-0.100 < 0.020); context veto"):
        other = dict(p, skip_reason=reason)
        assert restore_ev_only_bucket(other)["bucket"] == "SKIPPED_DEAD_EDGE"
        assert not tickets.is_playable(other)


def test_stale_ev_only_ml_veto_rescored_but_context_veto_preserved(isolated_ml, monkeypatch):
    p = pick(odds=1.5, bucket="SKIPPED_VETO", skip_reason="ML EV -0.100 < 0.02 min (capital protection)",
             ml_verdict="VETO", ml_veto_reasons=["ML EV -0.100 < 0.02 min (capital protection)"])
    assert tickets.is_playable(p)
    def veto(*args):
        return {"strength_score": 0, "should_veto": True, "should_boost": False,
                "veto_reasons": ["independent context VETO"], "boost_reasons": [], "w_score": 0}
    monkeypatch.setattr(ml, "score_pick_strengths", veto)
    monkeypatch.setattr(tickets, "score_pick_strengths", veto)
    assert not tickets.is_playable(p)
    assert ml.ml_filter_picks([p])[0][0]["bucket"] == "SKIPPED_VETO"


def test_missing_probability_and_ev_are_not_fabricated(isolated_ml):
    p = pick(odds=1.5, confidence=None, prediction_prob=None, ml_calibrated_prob=None)
    out, summary = ml.ml_filter_picks([p])
    assert out[0]["ml_ev"] is None and out[0]["ml_calibrated_prob"] is None
    assert summary["avg_ev"] is None and summary["avg_calib_prob"] is None
    assert out[0]["ml_ev_provenance"] is None
    diag = {}
    tickets.select_accas([p], diagnostics=diag)
    assert diag["priced_eligible"] == 0
    assert diag["priced_exclusions"]["missing valid probability"] == 1


def test_independent_fixture_price_and_freshness_guards(isolated_ml):
    p = pick(odds=1.5, selected_side="player_a", player_home="Someone Else")
    assert not tickets.is_playable(p)
    other = pick("C vs D", odds=1.5, _late_start=True)
    priced, _, _ = tickets.select_accas([pick(odds=1.5, _late_start=True), other])
    assert priced == []
    diag = {}
    tickets.select_accas([pick(odds=1.5, odds_source="ML_Estimated")], diagnostics=diag)
    assert diag["priced_eligible"] == 0


def test_legacy_ev_label_requires_independent_rescore(isolated_ml, monkeypatch):
    p = pick(odds=1.5, bucket="SKIPPED_DEAD_EDGE", skip_reason="negative EV (-0.100 < 0.020)")
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    reasons = {}
    assert not tickets.is_playable(p, rejections=reasons)
    assert reasons == {"legacy ML rescore unavailable": 1}


def test_forecast_export_does_not_filter_low_or_missing_ev(monkeypatch):
    monkeypatch.setattr(miner, "_is_future_target", lambda _: True)
    monkeypatch.setenv("RACKET_FACTORY_FORECAST_MIN_EV", "0.99")
    rows = [pick("A vs B", odds=1.5, expected_value=ev) for ev in (-.3, 0, None)]
    for i, row in enumerate(rows):
        row["match"] = f"Match {i}"
    assert len(miner.apply_forecast_hygiene("2099-01-01", rows)) == 3


def test_rejected_low_odds_remain_visible_in_report(tmp_path, monkeypatch):
    from scripts import daily
    monkeypatch.setattr(daily, "LOCALDATA", tmp_path)
    p = pick(odds=1.29, bucket="SKIPPED_LOW_ODDS", confidence=75)
    report = tmp_path / "report.txt"
    assert daily.generate_daily_report("2026-10-29", output_path=report, source_picks=[p]) == report
    text = report.read_text()
    assert "SKIPPED — INVALID OR BELOW-FLOOR ODDS (1.30 MINIMUM)" in text
    assert "A VS B" in text.upper()


@pytest.mark.parametrize("ev", [-.25, 0, .001, None])
@pytest.mark.parametrize("name", ["A vs B", "A / B vs C / D"])
def test_ml_does_not_veto_singles_or_doubles_for_ev(isolated_ml, monkeypatch, ev, name):
    monkeypatch.setattr(ml, "ml_ev", lambda *args: ev)
    out, _ = ml.ml_filter_picks([pick(name, odds=1.5)])
    assert out[0]["bucket"] == "WATCHLIST"
    assert out[0]["ml_verdict"] != "VETO"
    assert out[0]["ml_ev"] == ev


def test_ev_value_does_not_change_pool_order_or_selection(isolated_ml):
    base = [pick("A vs B", odds=1.30), pick("C vs D", odds=1.45),
            pick("E vs F", odds=1.55)]
    def result(evs):
        pool = [dict(row, ml_ev=ev) for row, ev in zip(base, evs)]
        priced, _, ordered = tickets.select_accas(pool)
        return ([p["match"] for p in ordered],
                [[p["match"] for p in a["legs"]] for a in priced])
    assert result([-.5, 0, .7]) == result([.7, -.5, None])


def test_nonfinite_model_inputs_do_not_produce_ev_diagnostics(isolated_ml):
    p = pick(odds=float("nan"), confidence=float("nan"), prediction_prob=None)
    out, summary = ml.ml_filter_picks([p])
    assert out[0]["ml_ev"] is None
    assert out[0]["ml_calibrated_prob"] is None
    assert out[0]["ml_odds_answer"] is None
    assert summary["avg_ev"] is None
