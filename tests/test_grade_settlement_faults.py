"""Settlement paths that used to lose money, invent money, or hang forever."""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import auto_tickets_grade as g


@pytest.fixture(autouse=True)
def _isolate_ledger_writes(tmp_path, monkeypatch):
    """settle_open_slips writes auto_tickets_grade.log and the stale-slip
    alarm file to g.LOCALDATA at call time. Point it somewhere disposable so
    the suite never edits the committed ledger."""
    monkeypatch.setattr(g, "LOCALDATA", tmp_path)
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "auto_tickets_state.json")


def _results(rows):
    return pd.DataFrame(rows)


WIN_BOTH = _results([
    {"player_a": "Angelina Voloshchuk", "player_b": "Jana Otzipka",
     "winner": "Angelina Voloshchuk", "score": "2-0 6-3 6-4",
     "match_date": "2026-09-20", "source": "T"},
    {"player_a": "Semra Aksu", "player_b": "Ekaterina Yashina",
     "winner": "Ekaterina Yashina", "score": "0-2 2-6 1-6",
     "match_date": "2026-09-20", "source": "T"},
])
SPLIT = _results([
    {"player_a": "Angelina Voloshchuk", "player_b": "Jana Otzipka",
     "winner": "Jana Otzipka", "score": "0-2 4-6 4-6",
     "match_date": "2026-09-20", "source": "T"},
    {"player_a": "Semra Aksu", "player_b": "Ekaterina Yashina",
     "winner": "Ekaterina Yashina", "score": "0-2 2-6 1-6",
     "match_date": "2026-09-20", "source": "T"},
])


def _stuck_slip():
    """The real 2026-09-20 slip, field for field."""
    return {
        "date": "2026-09-20", "generated_at": "2026-09-20T08:02:00+02:00",
        "staked_pct": 0, "stake_per_acca_pct": 0.0,
        "accas": [{
            "type": "value_2leg_mutual_paper", "odds": None,
            "stake_pct": 25.0, "paper": True,
            "legs": [
                {"match": "Angelina Voloshchuk vs Jana Otzipka",
                 "selected_player": "Angelina Voloshchuk", "odds": 1.22,
                 "odds_source": "BetExplorer", "_late_start": True,
                 "date": "2026-09-20"},
                {"match": "Semra Aksu vs Ekaterina Yashina",
                 "selected_player": "Ekaterina Yashina", "odds": 1.18,
                 "odds_source": "BetExplorer", "_late_start": True,
                 "date": "2026-09-20"},
            ],
        }],
    }


def _state(slip):
    return {"bank": 130.168518, "paper_bank": 100.0, "cycle_base": 100.0,
            "base_pct": 100.0, "open_slips": [slip], "history": [], "events": []}


def test_winning_stuck_slip_now_settles_instead_of_hanging():
    """It used to hit "never pays on fiction" and stay open forever."""
    state = _state(_stuck_slip())
    logs = g.settle_open_slips(state, WIN_BOTH, None)
    assert state["open_slips"] == [], "\n".join(logs)
    assert state["bank"] == pytest.approx(130.168518), "paper must not move the bank"
    entry = state["history"][0]
    assert entry["accas"][0]["paper"] is True
    assert entry["accas"][0]["won"] is True
    assert entry["accas"][0]["unpriced"] is True
    assert entry["pnl_pct"] == 0.0


def test_losing_stuck_slip_is_no_longer_booked_as_a_real_loss():
    """The asymmetry: it could be booked as a LOSS but never as a WIN."""
    state = _state(_stuck_slip())
    g.settle_open_slips(state, SPLIT, None)
    assert state["bank"] == pytest.approx(130.168518)
    assert state["history"][0]["staked_pct"] == 0.0
    assert state["history"][0]["accas"][0]["won"] is False


def test_a_genuinely_real_acca_still_moves_the_bank():
    """The fix must not turn every acca into paper."""
    slip = _stuck_slip()
    acca = slip["accas"][0]
    acca["paper"] = False
    acca["odds"] = 1.44
    acca["stake_pct"] = 10.0
    for leg in acca["legs"]:
        leg.pop("_late_start")
    state = _state(slip)
    g.settle_open_slips(state, WIN_BOTH, None)
    assert state["bank"] == pytest.approx(130.168518 + 10.0 * 1.44 - 10.0)


def test_legless_acca_is_never_a_free_win():
    """`any([])` is False, so an empty acca walked straight into the payout."""
    slip = {"date": "2026-09-20", "generated_at": "x", "accas": [
        {"type": "value_2leg_mutual", "odds": 5.0, "stake_pct": 20.0,
         "paper": False, "legs": []}]}
    state = _state(slip)
    logs = g.settle_open_slips(state, WIN_BOTH, None)
    assert state["bank"] == pytest.approx(130.168518), "\n".join(logs)
    entry = state["history"][0]
    assert entry["accas"][0]["won"] is False
    assert entry["accas"][0]["refunded"] is True
    assert any("no legs" in line for line in logs)


def test_ledger_claiming_real_on_an_unstakeable_acca_is_demoted_and_voided():
    slip = _stuck_slip()
    slip["accas"][0]["paper"] = False
    slip["accas"][0]["odds"] = 1.44
    state = _state(slip)
    logs = g.settle_open_slips(state, WIN_BOTH, None)
    assert state["bank"] == pytest.approx(130.168518)
    assert any("demoted to paper" in line for line in logs)
    assert state["history"][0]["accas"][0]["stake_pct"] == 0.0


# ---------------------------------------------------------- stale slips --

def test_stale_slip_alarm_fires_and_fresh_ones_are_quiet():
    state = _state(_stuck_slip())
    assert g.stale_slips(state, today=date(2026, 9, 21)) == []
    stale = g.stale_slips(state, today=date(2026, 9, 26))
    assert len(stale) == 1
    assert stale[0]["age_days"] == 6
    assert stale[0]["committed_pct"] == 0.0, "a paper slip commits nothing"
    assert stale[0]["reasons"]


def test_stale_alarm_surfaces_a_real_settlement_fault():
    slip = _stuck_slip()
    slip["accas"][0]["_settlement_fault"] = "real acca without a usable price"
    stale = g.stale_slips(_state(slip), today=date(2026, 9, 26))
    assert stale[0]["faults"] == ["real acca without a usable price"]


def test_stale_alarm_survives_a_malformed_date():
    state = _state({"date": "not-a-date", "accas": []})
    assert g.stale_slips(state, today=date(2026, 9, 26)) == []


def test_closing_stale_slips_is_deliberate_and_never_moves_the_bank():
    state = _state(_stuck_slip())
    assert g.close_stale_slips(state, days=30, today=date(2026, 9, 26)) == []
    assert state["open_slips"], "30d threshold must not touch a 6d slip"

    logs = g.close_stale_slips(state, days=3, today=date(2026, 9, 26))
    assert state["open_slips"] == []
    assert state["bank"] == pytest.approx(130.168518)
    entry = state["history"][-1]
    assert entry["closed_stale"] is True and entry["pnl_pct"] == 0.0
    assert entry["accas"][0]["voided_reason"].startswith("closed stale")
    assert any("CLOSED STALE" in line for line in logs)


def test_audit_writes_the_alarm_file(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "LOCALDATA", tmp_path)
    g.audit_stale_slips(_state(_stuck_slip()), today=date(2026, 9, 26))
    payload = json.loads((tmp_path / "auto_tickets_stale_slips.json").read_text())
    assert payload["stale"][0]["date"] == "2026-09-20"
    assert payload["alarm_after_days"] == g.STALE_SLIP_ALARM_DAYS
