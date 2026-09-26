"""The reset must restart the bank without destroying the evidence base."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import reset_execution_cycle as rx  # noqa: E402
from racketfactory import regime  # noqa: E402


def _state(**over):
    base = {
        "bank": 130.168518,
        "paper_bank": 100.0,
        "cycle_base": 100.0,
        "open_slips": [],
        "history": [
            {"date": "2026-09-17", "accas": [
                {"odds": 4.08, "stake_pct": 28.656, "return_pct": 116.92,
                 "paper": False, "won": True, "type": "fallback_2leg_mutual",
                 "legs": [{"odds": 2.18, "_settle_outcome": "WON",
                           "match": "A vs B", "ml_calibrated_prob": 0.7}]}]},
            {"date": "2026-09-22", "accas": [
                {"odds": 1.66, "stake_pct": 9.0, "return_pct": 0.0,
                 "paper": False, "won": False, "type": "value_2leg_mutual",
                 "legs": [{"odds": 1.29, "_settle_outcome": "LOST",
                           "match": "C vs D", "ml_calibrated_prob": 0.75}]}]},
        ],
        "events": [],
    }
    base.update(over)
    return base


# ------------------------------------------------------------ the plan --

def test_the_plan_names_the_reason_and_changes_nothing():
    state = _state()
    snapshot = json.dumps(state, sort_keys=True)
    p = rx.plan(state)
    assert p["breaches"], "the 4.08 breach must be the stated reason"
    assert p["booked_pnl_pct"] == pytest.approx(79.26, abs=0.01)
    assert p["adjusted_pnl_pct"] == pytest.approx(-9.0, abs=0.01)
    assert json.dumps(state, sort_keys=True) == snapshot


def test_paper_only_slips_do_not_count_as_capital_in_flight():
    """Both slips open at the boundary were paper; blocking on the raw count
    would have fired the safety rail on nothing."""
    state = _state(open_slips=[
        {"date": "2026-09-20", "staked_pct": 0,
         "accas": [{"paper": True, "stake_pct": 25.0, "legs": []}]}])
    p = rx.plan(state)
    assert p["open_slips"] == 1
    assert p["committed_pct"] == 0.0


def test_real_committed_capital_is_counted():
    state = _state(open_slips=[
        {"date": "2026-09-20", "staked_pct": 12.5,
         "accas": [{"paper": False, "stake_pct": 12.5,
                    "odds": 2.0, "legs": [
                        {"odds": 2.0, "odds_source": "betexplorer"}]}]}])
    assert rx.plan(state)["committed_pct"] > 0


# ------------------------------------------------- what reset preserves --

def test_history_is_preserved_not_deleted():
    """The 141 settled picks are the scarce resource every hypothesis waits
    on. A reset that deletes them sets every n back to zero."""
    new = rx.apply_reset(_state(), note="test")
    assert len(new["history"]) == 2
    assert new["history"][0]["accas"][0]["odds"] == 4.08


def test_the_bank_restarts_at_baseline():
    new = rx.apply_reset(_state(), note="test")
    assert new["bank"] == 100.0
    assert new["paper_bank"] == 100.0
    assert new["cycle_base"] == 100.0


def test_the_pick_regime_is_untouched():
    """Pick generation did not change at the boundary, so REGIME_ID must not
    move — bumping it would blind the ML context registry."""
    before = regime.REGIME_ID
    new = rx.apply_reset(_state(), note="test")
    assert regime.REGIME_ID == before
    assert new.get("_regime") is None


def test_the_boundary_is_recorded_with_both_figures():
    new = rx.apply_reset(_state(), note="because of the 4.08")
    event = new["events"][-1]
    assert event["event"] == "execution_cycle_reset"
    assert event["execution_regime"] == regime.EXECUTION_REGIME_ID
    assert event["pick_regime"] == regime.REGIME_ID
    assert event["bank_before"] == pytest.approx(130.168518)
    assert event["booked_pnl_pct"] != event["adjusted_pnl_pct"]
    assert event["history_entries_preserved"] == 2
    assert event["note"] == "because of the 4.08"


def test_existing_events_are_not_clobbered():
    new = rx.apply_reset(_state(events=[{"event": "earlier"}]), note="t")
    assert new["events"][0]["event"] == "earlier"
    assert len(new["events"]) == 2


# ---------------------------------------------------------------- CLI --

def _write(tmp_path, state):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    return path


def test_cli_dry_run_is_the_default_and_writes_nothing(tmp_path, capsys):
    path = _write(tmp_path, _state())
    before = path.read_text()
    assert rx.main(["--state", str(path)]) == 0
    assert path.read_text() == before
    assert "DRY RUN" in capsys.readouterr().out


def test_cli_refuses_while_real_capital_is_in_flight(tmp_path, capsys):
    path = _write(tmp_path, _state(open_slips=[
        {"date": "2026-09-20", "staked_pct": 12.5,
         "accas": [{"paper": False, "stake_pct": 12.5, "odds": 2.0,
                    "legs": [{"odds": 2.0, "odds_source": "betexplorer"}]}]}]))
    before = path.read_text()
    assert rx.main(["--state", str(path), "--apply"]) == 1
    assert path.read_text() == before
    assert "REFUSED" in capsys.readouterr().out


def test_cli_force_overrides_the_capital_guard(tmp_path):
    path = _write(tmp_path, _state(open_slips=[
        {"date": "2026-09-20", "staked_pct": 12.5,
         "accas": [{"paper": False, "stake_pct": 12.5, "odds": 2.0,
                    "legs": [{"odds": 2.0, "odds_source": "betexplorer"}]}]}]))
    assert rx.main(["--state", str(path), "--apply", "--force"]) == 0
    assert json.loads(path.read_text())["bank"] == 100.0


def test_cli_archives_before_writing(tmp_path):
    path = _write(tmp_path, _state())
    assert rx.main(["--state", str(path), "--apply"]) == 0
    archives = list(tmp_path.glob("state.pre-reset-*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text())["bank"] == pytest.approx(130.168518)
    assert json.loads(path.read_text())["bank"] == 100.0


def test_cli_on_a_missing_state_is_a_noop(tmp_path, capsys):
    assert rx.main(["--state", str(tmp_path / "nope.json")]) == 0
    assert "nothing to reset" in capsys.readouterr().out


def test_a_clean_ledger_resets_without_inventing_a_reason(tmp_path, capsys):
    clean = _state(history=[{"date": "2026-09-22", "accas": [
        {"odds": 2.0, "stake_pct": 10.0, "return_pct": 20.0, "paper": False,
         "won": True, "type": "value_2leg_mutual", "legs": []}]}])
    path = _write(tmp_path, clean)
    assert rx.main(["--state", str(path), "--apply"]) == 0
    assert "why:" not in capsys.readouterr().out


# ----------------------------------------------------- regime identity --

def test_execution_regime_is_separate_from_the_pick_regime():
    assert regime.EXECUTION_REGIME_ID != regime.REGIME_ID


def test_untagged_accas_belong_to_the_regime_before_the_boundary():
    """Opposite default to row_regime(): here a missing tag is positive
    evidence the row was written by the old engine."""
    assert regime.row_execution_regime({}) == regime.PRE_EXECUTION_REGIME_ID
    assert regime.row_execution_regime(None) == regime.PRE_EXECUTION_REGIME_ID
    assert regime.row_execution_regime(
        {"_exec_regime": regime.EXECUTION_REGIME_ID}) == regime.EXECUTION_REGIME_ID


def test_new_slips_are_stamped_with_the_execution_regime():
    from racketfactory.execution import stamp_acca
    acca = stamp_acca({"type": "value_2leg_mutual"})
    assert regime.row_execution_regime(acca) == regime.EXECUTION_REGIME_ID
