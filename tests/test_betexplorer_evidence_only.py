import json

from scripts import capture_betexplorer_evidence as probe
from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
from racketfactory.sources._page_odds import PageFetchError


def test_evidence_probe_is_bounded_and_stops_on_rate_limit(monkeypatch):
    from datetime import datetime
    from racketfactory.kickoff import SAST
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, "datetime", FakeDateTime)
    rows = [{"event_id": str(i), "match_url": f"/tennis/test/test/test/{i}/", "match_date": "2026-10-29", "match_time": "11:00", "player_home": f"Alice {i} Adams", "player_away": "Beth Brown"} for i in range(4)]
    monkeypatch.setattr(probe.betexplorer, "_fetch_live", lambda *a, **kw: (rows, "complete"))
    calls = []
    def fake(row, **kwargs):
        calls.append((row, kwargs))
        raise PageFetchError("rate_limit", "blocked")
    monkeypatch.setattr(probe.betexplorer, "fetch_fixture_comparison", fake)
    result = probe.capture("2026-10-29")
    assert len(calls) == 1 and calls[0][1]["refresh_evidence"] is True and calls[0][1]["capture_replay"] is True
    assert result["publication_attempted"] is False and result["failures"][0]["kind"] == "rate_limit"


def test_evidence_bookmaker_labels_are_bounded_and_secret_filtered(tmp_path, monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR", str(tmp_path))
    capture_betexplorer_fragment('<div></div>', 'detail', event_id='abc', observed_at='now', status=200)
    html = '<table><tr data-bookie="1"><td><img title="bet365"></td></tr><tr data-bookie="2"><td><img title="APIKEY_SUPERSECRET"></td></tr></table>'
    capture_betexplorer_fragment(html, 'comparison', event_id='abc', observed_at='now', status=200, response_body='full response')
    text = (tmp_path / 'betexplorer_fragments.json').read_text()
    assert json.loads(text)['fragments'][1]['bookmaker_labels'] == ['bet365']
    assert 'APIKEY_SUPERSECRET' not in text and 'full response' not in text


def test_synthetic_parser_failure_still_saves_replay_and_scrubs_secrets(tmp_path, monkeypatch):
    from racketfactory.sources import betexplorer as be
    import pytest
    from racketfactory.sources._page_odds import PageFetchError
    fixture = {'event_id': 'abc', 'match_url': '/tennis/atp/test/alice-beth/abc/', 'match_date': '2026-10-29', 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}
    page = '''<div data-url="/match-odds/abc/T?lang=en" data-market="Match Winner" data-period="set1"></div>
      <a href="/match-odds/abc/T?token=PRIVATESECRET">bad</a><input value="PRIVATESECRET">
      <script>match_load_tabs('abc','T',1); document.cookie='PRIVATESECRET';</script>'''
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setattr(be, '_fetch_comparison_document', lambda *a, **kw: (page, '2026-10-29T10:00:00+00:00'))
    counts = {}
    with pytest.raises(PageFetchError):
        be.fetch_fixture_comparison(fixture, refresh_evidence=True, capture_replay=True, stage_counts=counts)
    data = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())
    html = data['detail']['replay_html']
    assert '/match-odds/abc/T?lang=en' in html and 'match_load_tabs' in html
    assert 'PRIVATESECRET' not in json.dumps(data) and 'document.cookie' not in html
    assert data['detail']['redactions_or_limits'] and counts['replay_detail_file_written'] == counts['replay_detail_material_retained'] == 1
    assert data['detail']['same_current_discovery_result'] is True
    assert data['detail']['empty_discovery_does_not_prove_future_parser_fidelity'] is True
    assert be._comparison_endpoint_candidates(page, 'abc') == be._comparison_endpoint_candidates(html, 'abc') == []


def test_synthetic_replay_roundtrips_endpoint_and_comparison_rows(tmp_path, monkeypatch):
    from racketfactory.betexplorer_evidence import capture_replay
    from racketfactory.sources import betexplorer as be
    fixture = {'event_id': 'abc', 'match_url': '/tennis/atp/test/alice-beth/abc/', 'match_date': '2026-10-29', 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}
    page = '<div data-url="/match-odds/abc/T?lang=en" data-market="Match Winner" data-period="match" data-market-id="T"></div>'
    html = '<table><thead><tr><th>Bookmaker</th><th>Alice Adams</th><th>Beth Brown</th></tr></thead><tbody><tr data-bookie="4"><td><img title="Book One"></td><td data-odd="1.5"></td><td data-odd="2.5"></td></tr></tbody></table>'
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    assert capture_replay(page, 'detail', fixture, observed_at='time', request_url=fixture['match_url'])['discovery_material_retained']
    payload = json.dumps({'odds': html})
    assert capture_replay(html, 'comparison', fixture, observed_at='time', request_url='https://www.betexplorer.com/match-odds/abc/T?lang=en', response_body=payload)['discovery_material_retained']
    evidence = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())
    assert be._comparison_endpoint_candidates(page, 'abc') == be._comparison_endpoint_candidates(evidence['detail']['replay_html'], 'abc')
    context = be._comparison_endpoint_candidates(page, 'abc')[0]
    original, _ = be._comparison_response_rows(payload, fixture, context, 'time')
    replay, _ = be._comparison_response_rows(json.dumps({'odds': evidence['comparison']['replay_html']}), fixture, context, 'time')
    assert [(x['bookmaker'], x['odds_home'], x['odds_away']) for x in replay] == [(x['bookmaker'], x['odds_home'], x['odds_away']) for x in original]
    assert replay[0]['settlement_rules'] is None


def test_probe_selects_one_unambiguously_upcoming_fixture(monkeypatch):
    from datetime import datetime
    from racketfactory.kickoff import SAST
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, 'datetime', FakeDateTime)
    def row(eid, time):
        return {'event_id': eid, 'match_url': f'/tennis/atp/test/alice-beth/{eid}/',
                'match_date': '2026-10-29', 'match_time': time, 'player_home': 'Alice ' + eid + ' Adams', 'player_away': 'Beth Brown'}
    rows = [row('started', '09:00'), row('unknown', '00:00'), row('later', '12:00'), row('soon', '11:00')]
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: (rows, 'complete'))
    calls = []
    def fake(fixture, **kwargs):
        calls.append(fixture['event_id'])
        kwargs['stage_counts'].update(match_page_success=1, replay_detail_file_written=1, replay_detail_material_retained=1)
        raise PageFetchError('parser', 'synthetic')
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', fake)
    result = probe.capture('2026-10-29')
    assert calls == ['soon'] and result['fixtures_attempted'] == 1
    assert result['capture_status'] == 'detail_saved' and result['parser_status'] == 'failed_unknown'
    assert result['quote_eligibility'] == 'not_evaluated'


def _fixture():
    return {'event_id': 'abc', 'match_url': '/tennis/atp/test/alice-beth/abc/',
            'match_date': '2026-10-29', 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}


def test_boolean_initializer_retained_as_typed_literal(tmp_path, monkeypatch):
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    page = '<script>match_load_tabs("abc", "T", 1, true);</script>'
    result = capture_replay(page, 'detail', _fixture(), observed_at='observed')
    detail = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert result == {'file_written': True, 'discovery_material_retained': True, 'material_sufficiency': 'not_established'}
    assert 'match_load_tabs("abc", "T", 1, true)' in detail['replay_html']
    assert detail['http_status'] == 200 and detail['observed_at'] == 'observed'


def test_opaque_ids_are_omitted_but_metadata_survives(tmp_path, monkeypatch):
    import hashlib
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    page = '<script>match_load_tabs("abc", "T", "homeId", "awayId");</script>'
    result = capture_replay(page, 'detail', _fixture(), observed_at='observed')
    detail = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert result == {'file_written': True, 'discovery_material_retained': False, 'material_sufficiency': 'insufficient'}
    assert not result
    assert detail['replay_html'] == '<div></div>'
    assert detail['response_sha256'] == hashlib.sha256(page.encode()).hexdigest()
    assert 'initializer_arguments_or_syntax_not_allowlisted' in detail['omission_categories']
    assert 'homeId' not in json.dumps(detail) and 'awayId' not in json.dumps(detail)


def test_participant_ids_retained_only_with_explicit_public_page_binding(tmp_path, monkeypatch):
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    page = '''<div data-home="Alice Adams" data-away="Beth Brown" data-home-id="homeId" data-away-id="awayId"></div>
    <script>match_load_tabs("abc", "T", "homeId", "awayId", false);</script>'''
    result = capture_replay(page, 'detail', _fixture(), observed_at='observed')
    detail = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert result['discovery_material_retained'] and result['material_sufficiency'] == 'not_established'
    assert 'data-home-id="homeId"' in detail['replay_html'] and 'data-away-id="awayId"' in detail['replay_html']
    assert 'match_load_tabs("abc", "T", "homeId", "awayId", false)' in detail['replay_html']
    assert 'not independently verified' in json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['fixture']['identity_source']


def test_empty_response_preserves_metadata_but_is_insufficient(tmp_path, monkeypatch):
    import hashlib
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    result = capture_replay('<html><body></body></html>', 'detail', _fixture(), observed_at='observed')
    detail = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert result['file_written'] and not result['discovery_material_retained'] and result['material_sufficiency'] == 'insufficient'
    assert not result
    assert detail['replay_html'] == '<div></div>' and detail['observed_at'] == 'observed'
    assert detail['response_sha256'] == hashlib.sha256(b'<html><body></body></html>').hexdigest()
    assert 'no_relevant_discovery_material' in detail['omission_categories']


def test_evidence_mode_reports_empty_capture_as_failure_even_with_empty_replay_agreement(tmp_path, monkeypatch):
    from datetime import datetime
    from racketfactory.kickoff import SAST
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, 'datetime', FakeDateTime)
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    fixture = dict(_fixture(), match_time='11:00')
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: ([fixture], 'complete'))
    monkeypatch.setattr(probe.betexplorer, '_fetch_comparison_document', lambda *a, **kw: ('<html><body></body></html>', 'observed'))
    assert probe.main() == 1
    status = json.loads((tmp_path / 'evidence_collection.json').read_text())
    assert status['detail_response_fetched'] and status['evidence_file_written']
    assert not status['discovery_material_retained'] and status['material_sufficiency'] == 'insufficient'
    assert status['capture_status'] == 'detail_insufficient'
    assert status['current_parser_replay_agreement'] == 'match_empty_inconclusive'
    assert status['comparison_request'] == 'not_attempted' and status['quote_eligibility'] == 'not_evaluated'
    assert not status['publication_attempted']


def test_probe_distinguishes_nonempty_replay_comparison_response_and_not_evaluated_quote(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from racketfactory.kickoff import SAST
    from racketfactory import execution
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, 'datetime', FakeDateTime)
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    fixture = dict(_fixture(), match_time='11:00')
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: ([fixture], 'complete'))
    page = '<div data-url="/match-odds/abc/T?lang=en" data-market="Match Winner" data-period="match" data-market-id="T"></div>'
    odds = '<table><thead><tr><th>Bookmaker</th><th>Alice Adams</th><th>Beth Brown</th></tr></thead><tbody><tr data-bookie="4"><td><img title="Book One"></td><td data-odd="1.5"></td><td data-odd="2.5"></td></tr></tbody></table>'
    observed = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(probe.betexplorer, '_fetch_comparison_document',
                        lambda url, referer=None, **kwargs: (json.dumps({'odds': odds}) if referer else page, observed))
    monkeypatch.setattr(execution, 'rebuild_quote_execution_problem',
                        lambda *a: (_ for _ in ()).throw(AssertionError('evidence-only must not evaluate eligibility')))
    assert probe.main() == 0
    status = json.loads((tmp_path / 'evidence_collection.json').read_text())
    assert status['detail_response_fetched'] and status['evidence_file_written'] and status['discovery_material_retained']
    assert status['capture_status'] == 'detail_saved' and status['material_sufficiency'] == 'not_established'
    assert status['parser_status'] == 'parsed' and status['current_parser_replay_agreement'] == 'match_nonempty'
    assert status['comparison_request'] == 'response_observed' and status['quote_eligibility'] == 'not_evaluated'
    evidence = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())
    assert evidence['comparison']['file_written'] and evidence['comparison']['discovery_material_retained']


def test_market_label_alone_is_not_claimed_as_reconstructable(tmp_path, monkeypatch):
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    result = capture_replay('<h2 data-market="Match Winner">Match Winner</h2>', 'detail', _fixture(), observed_at='time')
    evidence = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert result['file_written'] and result['discovery_material_retained']
    assert result['material_sufficiency'] == 'insufficient' and not result
    assert 'no_retained_request_or_initializer' in evidence['omission_categories']


def test_mixed_supported_and_unsupported_initializers_report_omission(tmp_path, monkeypatch):
    from racketfactory.betexplorer_evidence import capture_replay
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    page = '<script>match_load_tabs("abc", "T", 1, true); match_other(load_config());</script>'
    result = capture_replay(page, 'detail', _fixture(), observed_at='time')
    detail = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert result['file_written'] and result['discovery_material_retained']
    assert result['material_sufficiency'] == 'insufficient' and not result
    assert 'initializer_shape_not_recognized' in detail['omission_categories']
    assert 'load_config' not in detail['replay_html']


def test_parser_replay_mismatch_marks_evidence_insufficient(tmp_path, monkeypatch):
    import pytest
    from racketfactory.sources import betexplorer as be
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    # Production parser accepts this synthetic long path segment; sanitization
    # rejects token-like values. A missing URL must never be called a replay.
    page = '<div data-url="/match-odds/abc/ABCDEFGHIJKLMNOPQRSTUVWX?lang=en" data-market="Match Winner" data-period="match" data-market-id="T"></div>'
    def fetch(url, referer=None):
        if referer:
            raise PageFetchError('transport', 'synthetic')
        return page, 'observed'
    monkeypatch.setattr(be, '_fetch_comparison_document', fetch)
    counts = {}
    with pytest.raises(PageFetchError):
        be.fetch_fixture_comparison(_fixture(), refresh_evidence=True, capture_replay=True, stage_counts=counts)
    detail = json.loads((tmp_path / 'betexplorer_replay_abc.json').read_text())['detail']
    assert detail['current_parser_replay_agreement'] == 'mismatch'
    assert detail['material_sufficiency'] == 'insufficient'
    assert 'current_parser_replay_mismatch' in detail['omission_categories']
    assert counts['replay_discovery_mismatch'] == 1 and counts['replay_detail_insufficient'] >= 1


def test_explicit_partial_listing_selection_is_diagnostic_not_upcoming(monkeypatch):
    row = {'event_id': 'abc', 'match_url': '/tennis/atp/test/alice-beth/abc/',
           'listing_date': '2026-10-29', 'listing_schedule_status': 'unverified',
           'match_date': '', 'match_time': '', 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: ([row], 'partial'))
    calls = []
    def fetch(fixture, **kwargs):
        calls.append(fixture)
        raise PageFetchError('parser', 'unknown schedule')
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', fetch)
    assert probe.capture('2026-10-29')['fixtures_attempted'] == 0
    result = probe.capture('2026-10-29', event_id='abc')
    assert len(calls) == 1 and calls[0]['match_date'] == ''
    assert result['selection'] == 'explicit_listing_event_schedule_unknown'
    assert result['listing_status'] == 'partial' and result['publication_attempted'] is False
    assert result['quote_eligibility'] == 'not_evaluated' and 'upcoming_candidates' not in result


def test_explicit_selection_rejects_wrong_duplicate_or_unbound_identity(monkeypatch):
    row = {'event_id': 'abc', 'match_url': '/tennis/atp/test/alice-beth/abc/',
           'listing_date': '2026-10-29', 'listing_schedule_status': 'unverified',
           'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not fetch')))
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: ([row, row], 'partial'))
    assert probe.capture('2026-10-29', event_id='abc')['capture_status'] == 'selected_event_not_unique'
    assert probe.capture('2026-10-29', event_id='other')['fixtures_attempted'] == 0
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: ([dict(row, match_url='/tennis/atp/test/alice-beth/other/')], 'partial'))
    assert probe.capture('2026-10-29', event_id='abc')['capture_status'] == 'selected_event_identity_unverified'


def test_explicit_event_cli_reaches_partial_listing_detail_without_publication(tmp_path, monkeypatch):
    row = {'event_id': 'abc123', 'match_url': '/tennis/atp/test/alice-beth/abc123/',
           'listing_date': '2026-10-29', 'listing_schedule_status': 'unverified',
           'match_date': '', 'match_time': '', 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}
    from datetime import datetime
    from racketfactory.kickoff import SAST
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, 'datetime', FakeDateTime)
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    calls = []
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda target, **kw: (calls.append(('listing', target)) or [row], 'partial'))
    def detail(fixture, **kwargs):
        calls.append(('detail', fixture['event_id']))
        assert kwargs['refresh_evidence'] and kwargs['capture_replay']
        kwargs['stage_counts'].update(match_page_success=1, replay_detail_file_written=1,
                                      replay_detail_material_retained=1)
        raise PageFetchError('parser', 'synthetic')
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', detail)
    assert probe.main(['--event-id', 'abc123']) == 0
    assert calls == [('listing', '2026-10-29'), ('detail', 'abc123')]
    result = json.loads((tmp_path / 'evidence_collection.json').read_text())
    assert result['selection'] == 'explicit_listing_event_schedule_unknown'
    assert result['listing_status'] == 'partial' and not result['publication_attempted']
    assert result['quote_eligibility'] == 'not_evaluated'


def test_invalid_event_id_is_rejected_before_listing_or_detail_fetch(monkeypatch):
    import pytest
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: pytest.fail('unexpected listing request'))
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: pytest.fail('unexpected detail request'))
    for event_id in ('', '-x', 'abc/def', 'abc;touch file', 'a' * 33):
        result = probe.capture('2026-10-29', event_id=event_id)
        assert result['capture_status'] == 'invalid_event_id'
        assert not result['listing_attempted'] and result['fixtures_attempted'] == 0


def test_partial_listing_without_explicit_event_never_falls_back_to_detail(monkeypatch):
    import pytest
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **kw: ([], 'partial'))
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: pytest.fail('unexpected detail request'))
    result = probe.capture('2026-10-29')
    assert result['capture_status'] == 'explicit_event_id_required'
    assert result['fixtures_attempted'] == 0 and not result['publication_attempted']


def test_capture_bundle_retains_redacted_replay_metadata_coverage_and_linkage(tmp_path, monkeypatch):
    import hashlib
    from datetime import datetime
    from racketfactory.kickoff import SAST
    from racketfactory.sources import betexplorer as be

    target = '2026-10-29'
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, 'datetime', FakeDateTime)
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    fixture = dict(_fixture(), match_time='11:00', listing_date=target,
                   listing_schedule_status='verified')
    listing = f'''<table><tr data-dt="29,10,2026,11,0"><td></td><td><a href="{fixture['match_url']}"><span>Alice Adams</span><span>Beth Brown</span></a></td><td>1.55 2.60</td></tr></table>
    <nav class="pagination"><a rel="next" href="/tennis/results/?year=2026&amp;month=10&amp;day=29&amp;page=2">Next</a></nav>
    <form><input value="FORM_SECRET_123"></form>'''
    detail = '<div data-url="/match-odds/abc/T?lang=en" data-market="Match Winner" data-period="match" data-market-id="T"></div><input value="DETAIL_SECRET_123"><script>match_load_tabs("abc", "T", 1); document.cookie="COOKIE_SECRET_123";</script>'
    odds = '<table><thead><tr><th>Bookmaker</th><th>Alice Adams</th><th>Beth Brown</th></tr></thead><tbody><tr data-bookie="4"><td><img title="Book One"></td><td data-odd="1.5" data-created="100"></td><td data-odd="2.5" data-created="101"></td></tr></tbody></table>'
    comparison = json.dumps({'odds': odds, 'session': 'JSON_SECRET_123'})
    now = '2026-10-29T10:00:00+00:00'
    def metadata(url, body, status=200):
        raw = body.encode()
        return {'requested_url': url, 'final_url': url, 'request_started_at': now,
                'response_received_at': now, 'http_status': status,
                'headers': {'Content-Type': 'text/html; charset=utf-8',
                            'Content-Length': str(len(raw)), 'Date': 'Thu, 29 Oct 2026 10:00:00 GMT',
                            'Set-Cookie': 'session=HEADER_SECRET_123'},
                'body_bytes': raw, 'body_text': body, 'transport': 'requests'}

    listing_url = 'https://www.betexplorer.com/tennis/results/?year=2026&month=10&day=29'
    def fetch_listing(requested, *, with_status=False, response_observer=None, **kwargs):
        if response_observer:
            response_observer('listing', metadata(listing_url, listing))
        return ([fixture], 'partial') if with_status else [fixture]
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', fetch_listing)

    def fetch_document(url, referer=None, *, response_observer=None, **kwargs):
        if referer:
            if response_observer:
                response_observer(metadata('https://www.betexplorer.com/match-odds/abc/T?lang=en', comparison))
            return comparison, now
        if response_observer:
            response_observer(metadata('https://www.betexplorer.com' + fixture['match_url'], detail))
        return detail, now
    monkeypatch.setattr(be, '_fetch_comparison_document', fetch_document)
    result = probe.capture(target, event_id='abc')
    assert result['capture_status'] == 'detail_saved', result

    paths = list(tmp_path.glob('betexplorer_capture_2026-10-29_*.json'))
    assert len(paths) == 1
    bundle = json.loads(paths[0].read_text())
    assert bundle['schema_version'] == 2
    assert bundle['target_timezone'] == 'Africa/Johannesburg'
    assert bundle['provider_completeness'] == 'not_established'
    assert bundle['linkage']['listing_event_id'] == bundle['linkage']['detail_event_id'] == 'abc'
    assert bundle['linkage']['comparison_event_id'] == 'abc'
    assert bundle['linkage']['detail_response_id'] == 'detail-1'
    assert bundle['linkage']['comparison_response_id'] == 'comparison-1'
    records = {row['stage']: row for row in bundle['responses']}
    page = records['listing']
    assert page['requested_url'] == listing_url
    assert page['request_started_at'] == page['response_received_at'] == now
    assert page['safe_response_headers']['content-length'] > 0
    assert page['sensitive_response_headers_omitted']
    assert page['coverage_signals']['provider_timezone'] == 'not stated/unknown'
    assert page['coverage_signals']['coverage_status'] == 'not_established'
    assert page['coverage_signals']['pagination_controls'][0]['path'].endswith('&page=2')
    assert 'alice-beth/abc' in page['parser_replay_body']
    for record in records.values():
        body = record['parser_replay_body']
        assert body
        assert record['retained_body_sha256'] == hashlib.sha256(body.encode()).hexdigest()
        assert record['retained_body_bytes'] == len(body.encode())
    detail_replay = records['detail']['parser_replay_body']
    assert len(be._comparison_endpoint_candidates(detail_replay, 'abc')) == 1
    replay_rows, _ = be._comparison_response_rows(records['comparison']['parser_replay_body'], fixture,
        {'raw_market': 'match winner', 'raw_period': 'match', 'market_id': 'T'}, now)
    assert [(row['bookmaker'], row['odds_home'], row['odds_away']) for row in replay_rows] == [
        ('Book One', 1.5, 2.5)]
    serialized = paths[0].read_text()
    for secret in ('FORM_SECRET_123', 'DETAIL_SECRET_123', 'COOKIE_SECRET_123',
                   'JSON_SECRET_123', 'HEADER_SECRET_123', 'Set-Cookie'):
        assert secret not in serialized


def test_listing_replay_preserves_event_status_rows_but_not_completion_claim(tmp_path, monkeypatch):
    import hashlib
    from datetime import datetime
    from racketfactory.betexplorer_evidence import EvidenceCapture
    from racketfactory.kickoff import SAST
    from racketfactory.sources.betexplorer import parse_results_page
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr('racketfactory.betexplorer_evidence.datetime', FakeDateTime)
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    session = EvidenceCapture('2026-10-29')
    html = '''<table><tr data-dt="29,10,2026,11,0"><td></td><td><a href="/tennis/itf-men-singles/m15-monastir-37/van-wyk-kris-marmousez-lilian/E7omKkv8/"><span>van Wyk K.</span><span>Marmousez L.</span></a></td><td></td><td></td></tr>
    <tr data-dt="29,10,2026,12,0"><td>FIN</td><td><a href="/tennis/itf-men-singles/m15-monastir-37/van-wyk-kris-marmousez-lilian/E7omKkv9/"><span>van Wyk K.</span><span>Marmousez L.</span></a></td><td></td><td></td></tr></table>'''
    raw = html.encode()
    session.observe('listing', {'requested_url': 'https://www.betexplorer.com/tennis/results/?year=2026&month=10&day=29',
        'final_url': 'https://www.betexplorer.com/tennis/results/?year=2026&month=10&day=29',
        'request_started_at': 'start', 'response_received_at': 'end', 'http_status': 200,
        'headers': {}, 'body_bytes': raw, 'body_text': html, 'transport': 'requests'})
    bundle = json.loads(session.file.read_text())
    retained = bundle['responses'][0]['parser_replay_body']
    assert bundle['responses'][0]['retained_body_sha256'] == hashlib.sha256(retained.encode()).hexdigest()
    replayed = parse_results_page(retained, '2026-10-29', include_unpriced=True)
    assert len(replayed) == 1
    assert (replayed[0]['player_home'], replayed[0]['player_away']) == ('van Wyk K.', 'Marmousez L.')
    assert bundle['responses'][0]['coverage_signals']['row_status_state_counts']['terminal'] == 1
    assert bundle['provider_completeness'] == 'not_established'


def test_capture_linkage_does_not_claim_selected_event_absent_from_listing(tmp_path, monkeypatch):
    from datetime import datetime
    from racketfactory.betexplorer_evidence import EvidenceCapture
    from racketfactory.kickoff import SAST
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr('racketfactory.betexplorer_evidence.datetime', FakeDateTime)
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    session = EvidenceCapture('2026-10-29')
    listing_url = 'https://www.betexplorer.com/tennis/results/?year=2026&month=10&day=29'
    session.observe('listing', {'requested_url': listing_url, 'final_url': listing_url,
        'request_started_at': 'start', 'response_received_at': 'end', 'http_status': 200,
        'headers': {}, 'body_bytes': b'<table></table>', 'body_text': '<table></table>',
        'transport': 'requests'})
    session.link_fixture(_fixture())
    session.observe('detail', {'requested_url': 'https://www.betexplorer.com' + _fixture()['match_url'],
        'final_url': 'https://www.betexplorer.com' + _fixture()['match_url'],
        'request_started_at': 'start', 'response_received_at': 'end', 'http_status': 200,
        'headers': {}, 'body_bytes': b'<div></div>', 'body_text': '<div></div>',
        'transport': 'requests'})
    bundle = json.loads(session.file.read_text())
    assert bundle['linkage']['listing_event_id'] is None
    assert bundle['linkage']['detail_event_id'] is None
    assert bundle['linkage']['listing_event_status'] == 'listing_path_not_observed'
    assert bundle['linkage']['detail_event_status'] == 'listing_event_link_unverified'
