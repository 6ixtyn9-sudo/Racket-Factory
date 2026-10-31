"""Synthetic tennis schema; derived from a saved football page, not live tennis proof."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from racketfactory.sources import betexplorer as be
from tests.test_betexplorer_comparison import PAGE, HTML


FIXTURE = {'event_id': 'a1B2', 'match_url': '/tennis/atp/test/alice-beth/a1B2/',
           'listing_date': '2035-01-02', 'match_date': '', 'match_time': '',
           'listing_schedule_status': 'unverified',
           'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}


def event(**changes):
    payload = {'@context': 'https://schema.org', '@type': 'SportsEvent', 'sport': 'Tennis',
               'url': 'https://www.betexplorer.com' + FIXTURE['match_url'],
               'startDate': '2035-01-01T23:30:00+00:00',
               'eventStatus': 'https://schema.org/EventScheduled',
               'homeTeam': {'@type': 'SportsTeam', 'name': 'Alice Adams'},
               'awayTeam': {'@type': 'SportsTeam', 'name': 'Beth Brown'}}
    payload.update(changes)
    return payload


def page(obj):
    return '<script type="application/ld+json">' + json.dumps(obj) + '</script>'


def now():
    return datetime.now(timezone.utc).isoformat()


def test_offset_scheduled_event_maps_both_date_and_time_to_johannesburg():
    result = be._detail_scheduled_start(page(event()), FIXTURE, observed_at=now())
    assert result == ('2035-01-02', '01:30', '2035-01-01T23:30:00+00:00')
    # A neighboring listing date is not silently adopted.
    assert be._detail_scheduled_start(page(event()), dict(FIXTURE, listing_date='2035-01-01'), observed_at=now()) is None


@pytest.mark.parametrize('changes', [
    {'sport': 'Football'},
    {'url': 'https://www.betexplorer.com/tennis/atp/test/other/other/'},
    {'url': 'https://evil.test/tennis/atp/test/alice-beth/a1B2/'},
    {'eventStatus': '2nd Half'},
    {'eventStatus': 'https://schema.org/EventCancelled'},
    {'eventStatus': ''},
    {'startDate': '2035-01-01T23:30:00'},
    {'startDate': '2035-01-01T23:30:00-00:00'},
    {'startDate': '2035-02-30T23:30:00+00:00'},
    {'startDate': '2035-01-01T23:30:00+15:00'},
    {'startDate': '2020-01-01T23:30:00+00:00'},
    {'homeTeam': {'name': 'Beth Brown'}, 'awayTeam': {'name': 'Alice Adams'}},
    {'homeTeam': {'name': 'Other Player'}},
    {'awayTeam': {'name': 'Alice Adams'}},
])
def test_unproven_status_timestamp_or_identity_is_rejected(changes):
    assert be._detail_scheduled_start(page(event(**changes)), FIXTURE, observed_at=now()) is None


def test_ambiguous_or_stale_evidence_is_rejected():
    timestamp = now()
    assert be._detail_scheduled_start(page(event()) + page(event()), FIXTURE, observed_at=timestamp) is None
    assert be._detail_scheduled_start(page(event()) + '<script type="application/ld+json">broken</script>', FIXTURE, observed_at=timestamp) is None
    duplicate = page(event()).replace('"sport": "Tennis"', '"sport": "Tennis", "sport": "Football"')
    assert be._detail_scheduled_start(duplicate, FIXTURE, observed_at=timestamp) is None
    assert be._detail_scheduled_start(page(event()), FIXTURE, observed_at=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()) is None
    assert be._detail_scheduled_start(page(event()), FIXTURE, observed_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()) is None
    assert be._detail_scheduled_start(page(event()), FIXTURE, observed_at='2034-01-01T12:00:00') is None


def test_schedule_candidate_records_canonical_rejection_reasons():
    stale = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    reasons = []
    assert be._detail_scheduled_start(page(event()), FIXTURE, observed_at=stale,
                                     rejection_reasons=reasons) is None
    assert reasons == ["schedule_observation_invalid_or_stale"]
    reasons = []
    conflicting = page(event()) + '<div data-status="LIVE">In play</div>'
    assert be._detail_scheduled_start(conflicting, FIXTURE, observed_at=now(),
                                     rejection_reasons=reasons) is None
    assert reasons == ["schedule_conflicting_status_signal"]
    reasons = []
    components = dict(FIXTURE, listing_data_dt_components=[1, 1, 2035, 12, 0])
    assert be._detail_scheduled_start(page(event()), components, observed_at=now(),
                                     rejection_reasons=reasons) is None
    assert reasons == ["schedule_listing_components_conflict_with_request_date"]


def test_existing_detail_request_accepts_fixture_candidate_without_claiming_global_coverage(monkeypatch):
    calls = []
    def document(url, referer=None):
        calls.append((url, referer))
        return ((json.dumps({'odds': HTML}) if referer else page(event()) + PAGE), now())
    monkeypatch.setattr(be, '_fetch_comparison_document', document)
    counts = {}
    rows = be.fetch_fixture_comparison(FIXTURE, refresh_evidence=True, capture_replay=True, stage_counts=counts)
    assert len(calls) == 2 and counts['schedule_candidate_from_match_page'] == 1
    assert rows and rows[0]['schedule_evidence']['status'] == 'candidate_unverified_contract'
    assert rows[0]['match_date'] == '2035-01-02' and rows[0]['match_time'] == '01:30'
    assert rows[0]['schedule_evidence']['contract_validation'] == 'not_authenticated'
    assert FIXTURE['match_date'] == ''  # input is not mutated


def test_missing_schedule_stops_before_comparison_without_extra_request(monkeypatch):
    calls = []
    monkeypatch.setattr(be, '_fetch_comparison_document', lambda *a, **kw: calls.append(a) or (PAGE, now()))
    with pytest.raises(be.PageFetchError, match='schedule or fixture identity unverified'):
        be.fetch_fixture_comparison(FIXTURE, refresh_evidence=True, capture_replay=True)
    assert len(calls) == 1


def test_old_cache_cannot_launder_unverified_schedule(monkeypatch):
    monkeypatch.setattr(be, 'cached_fetch', lambda *a, **kw: [{'event_id': 'a1B2'}])
    monkeypatch.setattr(be, '_fetch_comparison_document', lambda *a, **kw: pytest.fail('unexpected fetch'))
    with pytest.raises(be.PageFetchError, match='cached match schedule evidence unverified'):
        be.fetch_fixture_comparison(FIXTURE, capture_replay=True)


def test_replay_keeps_only_matched_public_schedule_fields(tmp_path, monkeypatch):
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    payload = event(secret='PRIVATESECRET')
    capture_replay(page(payload) + PAGE, 'detail', FIXTURE, observed_at=now())
    data = json.loads((tmp_path / 'betexplorer_replay_a1B2.json').read_text())
    schedule = data['detail']['schedule_evidence']
    assert schedule['status'] == 'candidate_offset_sportsevent'
    assert schedule['start_date'] == '2035-01-01T23:30:00+00:00'
    assert schedule['listing_coverage'] == 'not_established'
    assert 'PRIVATESECRET' not in json.dumps(data)
    capture_replay(PAGE, 'detail', FIXTURE, observed_at=now())
    data = json.loads((tmp_path / 'betexplorer_replay_a1B2.json').read_text())
    assert data['detail']['schedule_evidence']['status'] == 'not_verified'


def test_comparison_replay_uses_original_listing_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setattr(be, '_fetch_comparison_document', lambda url, referer=None:
                        (json.dumps({'odds': HTML}) if referer else page(event()) + PAGE, now()))
    counts = {}
    rows = be.fetch_fixture_comparison(FIXTURE, refresh_evidence=True, capture_replay=True, stage_counts=counts)
    data = json.loads((tmp_path / 'betexplorer_replay_a1B2.json').read_text())
    assert rows and data['detail']['schedule_evidence']['status'] == 'candidate_offset_sportsevent'
    assert counts['replay_comparison_file_written'] == 1 and 'comparison' in data


def test_missing_schedule_still_captures_detail_without_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setattr(be, '_fetch_comparison_document', lambda *a, **kw: (PAGE, now()))
    counts = {}
    with pytest.raises(be.PageFetchError):
        be.fetch_fixture_comparison(FIXTURE, refresh_evidence=True, capture_replay=True, stage_counts=counts)
    assert counts['replay_detail_file_written'] == 1 and 'comparison_attempts' not in counts
    data = json.loads((tmp_path / 'betexplorer_replay_a1B2.json').read_text())
    assert data['detail']['schedule_evidence']['status'] == 'not_verified'
