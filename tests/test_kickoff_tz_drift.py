"""The BetClan timezone assumption has an expiry date. Prove it is watched.

BetClan is the single source of kickoff for essentially every staked leg and
publishes naive timestamps read as a fixed UTC+1. If it is really
Europe/London, Britain leaving BST on 2026-10-25 shifts every kickoff by an
hour and kickoff_guard waves through matches already in progress.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from racketfactory import kickoff as ko  # noqa: E402

import kickoff_tz_audit as audit  # noqa: E402


# ------------------------------------------------------------ the status --

def test_no_measurement_is_unknown_not_ok():
    """Fail-safe: silence must never render as a green tick."""
    status = ko.betclan_tz_status(today=date(2026, 9, 26))
    assert status["status"] == ko.STATUS_UNKNOWN
    assert status["days_to_review"] == 29


def test_review_becomes_due_the_day_britain_leaves_bst():
    assert ko.betclan_tz_status(today=date(2026, 10, 24))["status"] == ko.STATUS_UNKNOWN
    assert ko.betclan_tz_status(today=date(2026, 10, 25))["status"] == ko.STATUS_REVIEW_DUE
    assert ko.betclan_tz_status(today=date(2026, 11, 1))["status"] == ko.STATUS_REVIEW_DUE


def test_agreement_clears_the_assumption():
    status = ko.betclan_tz_status(today=date(2026, 9, 26),
                                  measured_offset_minutes=0, sample_size=12)
    assert status["status"] == ko.STATUS_OK


def test_an_hour_of_drift_is_an_alarm():
    status = ko.betclan_tz_status(today=date(2026, 9, 26),
                                  measured_offset_minutes=-60, sample_size=9)
    assert status["status"] == ko.STATUS_DRIFT
    assert "RACKET_FACTORY_BETCLAN_TZ" in status["detail"]


def test_evidence_of_drift_outranks_the_calendar():
    status = ko.betclan_tz_status(today=date(2026, 11, 1),
                                  measured_offset_minutes=60, sample_size=9)
    assert status["status"] == ko.STATUS_DRIFT


def test_small_jitter_is_not_drift():
    status = ko.betclan_tz_status(today=date(2026, 9, 26),
                                  measured_offset_minutes=15, sample_size=9)
    assert status["status"] == ko.STATUS_OK


def test_a_measurement_with_no_samples_is_not_trusted():
    status = ko.betclan_tz_status(today=date(2026, 9, 26),
                                  measured_offset_minutes=0, sample_size=0)
    assert status["status"] == ko.STATUS_UNKNOWN


# --------------------------------------------------------- the override --

def test_zone_is_overridable_without_a_code_change(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_BETCLAN_TZ", "Europe/London")
    assert ko.betclan_tz_status(today=date(2026, 9, 26))["assumed_tz"] == "Europe/London"


def test_a_bad_zone_name_falls_back_instead_of_exploding(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_BETCLAN_TZ", "Mars/Olympus_Mons")
    assert ko.betclan_tz_status(today=date(2026, 9, 26))["assumed_tz"] == "Etc/GMT-1"


def test_default_conversion_is_unchanged():
    """The override must not move today's kickoffs by accident."""
    assert ko.convert_kickoff("2026-09-26", "09:00", ko.BETCLAN_TZ) == ("2026-09-26", "10:00")
    assert ko.convert_kickoff("2026-09-25", "23:30", ko.BETCLAN_TZ) == ("2026-09-26", "00:30")
    assert ko.utc_commence_to_sast("2026-09-25T22:30:00Z") == ("2026-09-26", "00:30")


# ------------------------------------------------------------ the maths --

@pytest.mark.parametrize("a,b,expected", [
    ("10:00", "10:00", 0),
    ("10:00", "09:00", 60),
    ("09:00", "10:00", -60),
    ("00:10", "23:50", 20),      # must wrap, not report 23h40
    ("23:50", "00:10", -20),
    ("10:00", "n/a", None),
    ("", "10:00", None),
    ("25:00", "10:00", None),
    ("10:61", "10:00", None),
])
def test_wall_time_delta_wraps_at_midnight(a, b, expected):
    assert ko.minutes_between_wall_times(a, b) == expected


# ------------------------------------------------------------ the audit --

def _write(tmp_path, picks, odds_rows, day="2026-09-26"):
    (tmp_path / f"picks_{day}.json").write_text(json.dumps(picks))
    (tmp_path / f"theoddsapi_odds_cache_{day}.json").write_text(
        json.dumps({"meta": {}, "rows": odds_rows}))


def test_audit_detects_a_one_hour_shift(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    _write(tmp_path,
           [{"source": "BetClan", "match": "Daniil Medvedev vs Valentin Royer",
             "kickoff": "10:30"},
            {"source": "BetClan", "match": "Federico Cina vs Alexandre Muller",
             "kickoff": "08:30"}],
           [{"player_home": "Daniil Medvedev", "player_away": "Valentin Royer",
             "match_time": "11:30", "match_date": "2026-09-26"},
            {"player_home": "Federico Cina", "player_away": "Alexandre Muller",
             "match_time": "09:30", "match_date": "2026-09-26"}])
    report = audit.audit(["2026-09-26"])
    assert report["matched_fixtures"] == 2
    assert report["median_offset_minutes"] == -60
    assert report["status"] == ko.STATUS_DRIFT


def test_audit_confirms_agreement(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    _write(tmp_path,
           [{"source": "BetClan", "match": "A Player vs B Player", "kickoff": "11:30"}],
           [{"player_home": "B Player", "player_away": "A Player",
             "match_time": "11:30", "match_date": "2026-09-26"}])
    report = audit.audit(["2026-09-26"])
    assert report["matched_fixtures"] == 1 and report["status"] == ko.STATUS_OK


def test_audit_ignores_non_betclan_picks(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    _write(tmp_path,
           [{"source": "Forebet", "match": "A Player vs B Player", "kickoff": "09:00"}],
           [{"player_home": "A Player", "player_away": "B Player",
             "match_time": "11:30", "match_date": "2026-09-26"}])
    assert audit.audit(["2026-09-26"])["matched_fixtures"] == 0


def test_audit_is_fail_safe_on_missing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    report = audit.audit(["2026-09-26"])
    assert report["matched_fixtures"] == 0
    assert report["median_offset_minutes"] is None
    assert report["status"] == ko.STATUS_UNKNOWN


def test_audit_survives_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    (tmp_path / "picks_2026-09-26.json").write_text("{not json")
    (tmp_path / "theoddsapi_odds_cache_2026-09-26.json").write_text("[]")
    assert audit.audit(["2026-09-26"])["status"] == ko.STATUS_UNKNOWN


def test_audit_cli_reports_without_failing_the_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    monkeypatch.setattr(audit, "OUT_FILE", tmp_path / "kickoff_tz_audit.json")
    assert audit.main(["--date", "2026-09-26", "--days", "1"]) == 0
    assert "BetClan timezone assumption: UNKNOWN" in capsys.readouterr().out
    assert (tmp_path / "kickoff_tz_audit.json").exists()


def test_audit_cli_strict_mode_can_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOCALDATA", tmp_path)
    monkeypatch.setattr(audit, "OUT_FILE", tmp_path / "kickoff_tz_audit.json")
    _write(tmp_path,
           [{"source": "BetClan", "match": "A Player vs B Player", "kickoff": "10:30"}],
           [{"player_home": "A Player", "player_away": "B Player",
             "match_time": "11:30", "match_date": "2026-09-26"}])
    assert audit.main(["--date", "2026-09-26", "--days", "1", "--strict"]) == 1
