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
    assert data['detail']['redactions_or_limits'] and counts['replay_detail_saved'] == 1
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
    assert capture_replay(page, 'detail', fixture, observed_at='time', request_url=fixture['match_url'])
    payload = json.dumps({'odds': html})
    assert capture_replay(html, 'comparison', fixture, observed_at='time', request_url='https://www.betexplorer.com/match-odds/abc/T?lang=en', response_body=payload)
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
        kwargs['stage_counts']['replay_detail_saved'] = 1
        raise PageFetchError('parser', 'synthetic')
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', fake)
    result = probe.capture('2026-10-29')
    assert calls == ['soon'] and result['fixtures_attempted'] == 1
    assert result['capture_status'] == 'detail_saved' and result['parser_status'] == 'failed_unknown'
    assert result['quote_eligibility'] == 'not_evaluated'
