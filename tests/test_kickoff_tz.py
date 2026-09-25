"""Kickoff timezone normalisation: every source converts to SAST at ingestion.

Anchored in the 2026-09-24/25 evidence: BetClan stamps measured exactly
UTC+1 (Betway's SAST kickoff minus 1h on every singles checked), and The
Odds API commence_time is ISO UTC. Date and time convert together so late
kickoffs roll into the next SAST day.
"""
from racketfactory.kickoff import (
    BETCLAN_TZ,
    SAST,
    convert_kickoff,
    parse_source_timestamp,
    to_sast_parts,
    utc_commence_to_sast,
)
from racketfactory.sources.betclan import extract_kickoff_sast
from racketfactory.warehouse import _local_date_from_api_time, _local_parts_from_api_time


def test_betclan_naive_stamp_is_utc_plus_1():
    # Measured 2026-09-24/25: BetClan "09:30" == Betway 10:30 SAST
    assert convert_kickoff("2026-09-25", "09:30", BETCLAN_TZ) == ("2026-09-25", "10:30")


def test_betclan_date_rolls_over_at_midnight_sast():
    # 23:30 BetClan (UTC+1) is already the next SAST day
    assert convert_kickoff("2026-09-25", "23:30", BETCLAN_TZ) == ("2026-09-26", "00:30")


def test_explicit_offsets_are_honoured_over_default():
    # Explicit +01:00 agrees with the default zone
    assert parse_source_timestamp("2026-09-25T09:30+01:00") .utcoffset().total_seconds() == 3600
    assert to_sast_parts(parse_source_timestamp("2026-09-25T09:30+01:00")) == ("2026-09-25", "10:30")
    # Explicit Z means UTC even though the default is UTC+1
    assert to_sast_parts(parse_source_timestamp("2026-09-25T09:30:00Z")) == ("2026-09-25", "11:30")


def test_theoddsapi_utc_commence_to_sast():
    assert utc_commence_to_sast("2026-09-25T08:30:00Z") == ("2026-09-25", "10:30")


def test_theoddsapi_utc_commence_rolls_over():
    assert utc_commence_to_sast("2026-09-25T22:30:00Z") == ("2026-09-26", "00:30")


def test_unparseable_inputs_return_empty():
    assert convert_kickoff("", "09:30", BETCLAN_TZ) == ("", "")
    assert convert_kickoff("2026-09-25", "not-a-time", BETCLAN_TZ) == ("", "")
    assert utc_commence_to_sast("") == ("", "")
    assert utc_commence_to_sast("garbage") == ("", "")


def test_betclan_adapter_stores_sast_kickoff():
    # Page stamp is BetClan-local (UTC+1); adapter must store SAST.
    page = "2026-09-25T09:30"
    assert extract_kickoff_sast(page, "2026-09-25") == ("2026-09-25", "10:30")
    # Rollover: late-evening BetClan stamp lands on the next SAST day
    assert extract_kickoff_sast("2026-09-25T23:30", "2026-09-25") == ("2026-09-26", "00:30")
    # An explicit offset on the page wins over the assumed zone
    assert extract_kickoff_sast("2026-09-25T09:30:00Z", "2026-09-25") == ("2026-09-25", "11:30")
    # No stamp anywhere: SAST target-date midnight placeholder, unchanged
    assert extract_kickoff_sast("<html>no stamps here</html>", "2026-09-25") == ("2026-09-25", "00:00")


def test_warehouse_api_time_helpers_are_sast():
    assert _local_parts_from_api_time("2026-09-25T08:30:00Z") == ("2026-09-25", "10:30")
    assert _local_date_from_api_time("2026-09-25T22:30:00Z") == "2026-09-26"
    assert _local_date_from_api_time("") == ""


def test_sast_constant_is_africa_johannesburg():
    # Pipeline time is SAST (UTC+2, no DST)
    assert str(SAST) == "Africa/Johannesburg"
