"""Run-scoped collection diagnostics must not weaken no-clobber guards."""
import json
import sys
from datetime import datetime, timedelta

import pytest

from racketfactory import doctor
from racketfactory.quote_enrichment import collect_prediction_comparisons
from racketfactory.sources import betexplorer
from scripts import auto_tickets as tickets

DAY = '2026-10-29'
NOW = datetime.fromisoformat(DAY + 'T07:00:00+02:00')
RUN = (NOW - timedelta(minutes=20)).isoformat()


def write_collection(tmp_path, **fields):
    payload = {'run_as_of': RUN, 'status': 'partial', 'oldest_offer_observed_at': None}
    payload.update(fields)
    (tmp_path / f'quote_collection_{DAY}.json').write_text(json.dumps(payload))


@pytest.mark.parametrize('status', ['partial', 'failed', 'disabled', 'unknown'])
@pytest.mark.parametrize('observed', [None, 'invalid', NOW.isoformat()])
def test_incomplete_status_survives_missing_or_invalid_timestamp(monkeypatch, tmp_path, status, observed):
    monkeypatch.setattr(tickets, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(tickets, 'now_local', lambda: NOW)
    write_collection(tmp_path, status=status, oldest_offer_observed_at=observed)
    actual, _, freshness = tickets.current_quote_collection(DAY, RUN)
    assert freshness == ("missing" if observed is None else "malformed" if observed == "invalid" else "fresh")
    assert actual == status


@pytest.mark.parametrize(('observed', 'expected'), [
    (None, 'unknown'),
    ('not-a-timestamp', 'unknown'),
    (DAY + 'T07:00:00', 'stale_or_unknown'),
    ((NOW - timedelta(minutes=30)).isoformat(), 'complete'),
    ((NOW - timedelta(minutes=30, seconds=1)).isoformat(), 'stale_or_unknown'),
    ((NOW + timedelta(seconds=1)).isoformat(), 'stale_or_unknown'),
    (NOW.isoformat(), 'complete'),
])
def test_complete_still_requires_a_fresh_aware_observation(monkeypatch, tmp_path, observed, expected):
    monkeypatch.setattr(tickets, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(tickets, 'now_local', lambda: NOW)
    write_collection(tmp_path, status='complete', oldest_offer_observed_at=observed)
    assert tickets.current_quote_collection(DAY, RUN)[0] == expected


def test_mismatched_missing_or_malformed_collection_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(tickets, 'LOCALDATA', tmp_path)
    path = tmp_path / f'quote_collection_{DAY}.json'
    assert tickets.current_quote_collection(DAY, RUN)[0] == 'unknown'
    path.write_text('{broken')
    assert tickets.current_quote_collection(DAY, RUN)[0] == 'unknown'
    path.write_text('[]')
    assert tickets.current_quote_collection(DAY, RUN)[0] == 'unknown'
    write_collection(tmp_path, run_as_of='another-run', status='partial')
    assert tickets.current_quote_collection(DAY, RUN)[0] == 'unknown'


@pytest.mark.parametrize('candidate', [False, True])
@pytest.mark.parametrize(('source_status', 'timestamp', 'expected'), [
    ('partial', None, 'partial'),
    ('failed', None, 'failed'),
    ('unknown', None, 'unknown'),
    ('complete', None, 'unknown'),
    ('complete', (NOW - timedelta(minutes=31)).isoformat(), 'stale_or_unknown'),
])
def test_incomplete_evaluation_retains_card_and_fails_health(monkeypatch, tmp_path, source_status, timestamp, expected, candidate):
    monkeypatch.setattr(tickets, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(tickets, 'STATE_FILE', tmp_path / 'auto_tickets_state.json')
    monkeypatch.setattr(tickets, 'now_local', lambda: NOW)
    monkeypatch.setattr(tickets, 'load_picks', lambda _: [])
    priced = [{'legs': [], 'odds': 2.0, 'type': 'synthetic'}] if candidate else []
    monkeypatch.setattr(tickets, 'select_accas', lambda *a, **kw: (priced, [], []))
    monkeypatch.setattr(tickets, 'fresh_ticket_problem', lambda *a: pytest.fail('collection must block first'))
    monkeypatch.setattr(sys, 'argv', ['auto_tickets.py', '--date', DAY])
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', RUN)
    monkeypatch.setenv('GITHUB_RUN_ID', '12345')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.delenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', raising=False)
    monkeypatch.setattr(doctor, 'LOCALDATA', tmp_path)
    card = tmp_path / f'auto_tickets_{DAY}.json'
    card.write_text(json.dumps({'date': DAY, 'frozen': False, 'accas': []}))
    state = tmp_path / 'auto_tickets_state.json'
    state.write_text(json.dumps({'bank': 82.432, 'paper_bank': 100, 'open_slips': [], 'history': []}))
    before = card.read_bytes(), state.read_bytes()
    write_collection(tmp_path, status=source_status, oldest_offer_observed_at=timestamp)
    assert tickets.main() == 0
    assert (card.read_bytes(), state.read_bytes()) == before
    result = json.loads((tmp_path / f'auto_tickets_evaluation_{DAY}.json').read_text())
    assert result['collection_status'] == expected
    assert result['collection_observation_freshness'] == ('missing' if timestamp is None else 'stale')
    assert result['publication'] == 'retained_previous_card'
    assert doctor.run_health_checks(as_of=DAY)['tickets:current_evaluation']['status'] == 'CRITICAL'


@pytest.mark.parametrize('status', ['partial', 'failed', 'unknown'])
def test_incomplete_listing_still_attempts_supported_per_fixture_detail(monkeypatch, status):
    calls = []
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: calls.append(a) or [])
    fixture = {'match_date': DAY, 'listing_date': DAY, 'listing_schedule_status': 'unverified',
               'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'abc',
               'match_url': '/tennis/atp/event/alice-beth/abc/'}
    offers, counts, partial = collect_prediction_comparisons(DAY, [fixture], [fixture], status)
    assert calls and not offers and partial
    assert counts['predictions'] == counts['matched_fixtures'] == 1
    assert counts['unmatched_or_ambiguous'] == 0
    assert counts['stage_statuses']['identity_matching'] == 'complete'
    assert counts['stage_statuses']['required_source_readiness'] == 'blocked'
    assert counts['prediction_set_input_rows'] == 1
    assert counts['listing_status'] == status


def test_partial_listing_diagnostic_is_persisted_without_becoming_complete(monkeypatch, tmp_path):
    from racketfactory import quote_enrichment as qe
    from racketfactory.sources import betclan, oddsportal_upcoming

    (tmp_path / 'localdata').mkdir()
    monkeypatch.setattr(qe, '__file__', str(tmp_path / 'src/racketfactory/quote_enrichment.py'))
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', RUN)
    monkeypatch.delenv(betexplorer.DISABLE_ENV, raising=False)
    monkeypatch.delenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', raising=False)
    monkeypatch.setattr(betclan, 'current_run_status', lambda _: 'partial')
    monkeypatch.setattr(oddsportal_upcoming, 'fetch_oddsportal_upcoming_rows', lambda _: [])
    fixture = {'match_date': DAY, 'listing_date': DAY, 'listing_schedule_status': 'unverified',
               'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'abc',
               'match_url': '/tennis/atp/event/alice-beth/abc/'}
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: ([fixture], 'partial'))
    detail_calls = []
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: detail_calls.append(a) or [])
    assert not qe.targeted_comparison_rows(DAY, [fixture])
    result = json.loads((tmp_path / 'localdata' / f'quote_collection_{DAY}.json').read_text())
    assert result['status'] == result['betclan_status'] == 'partial'
    assert result['oldest_offer_observed_at'] is None
    assert len(detail_calls) == 1
    assert result['counts']['predictions'] == result['counts']['matched_fixtures'] == 1
    assert result['counts']['unmatched_or_ambiguous'] == 0
    assert result['counts']['stage_statuses']['required_source_readiness'] == 'blocked'
