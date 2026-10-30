"""Offline provider request accounting and bounded evidence regressions."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from racketfactory.sources import betclan, betexplorer
from racketfactory import betexplorer_evidence as evidence


def test_betclan_only_page_derived_same_event_links():
    source = betclan.BetClanPredictor()
    url = 'https://www.betclan.com/tennis/predictionsdetails/fixture-123/'
    assert source._find_odds_subpage(BeautifulSoup('<div></div>', 'html.parser'), url) == []
    html = '''<a href="/tennis/odds/fixture-123/">odds</a>
    <a href="/tennis/odds/fixture-123">duplicate</a>
    <a href="/tennis/odds/wrong/">wrong</a>
    <a href="https://evil.example/tennis/odds/fixture-123/">external</a>
    <a href="/tennis/odds/fixture-123/?token=secret">query</a>
    <a href="/tennis/odds/fixture-123/#fragment">fragment</a>
    <a href="/tennis/predictionsdetails/fixture-123/">prediction</a>'''
    assert source._find_odds_subpage(BeautifulSoup(html, 'html.parser'), url) == [
        'https://www.betclan.com/tennis/odds/fixture-123/']


def test_betclan_no_guesses_dedupes_details_and_preserves_partial(monkeypatch):
    url = 'https://www.betclan.com/tennis/predictionsdetails/example/'
    bad = 'https://www.betclan.com/tennis/predictionsdetails/bad/'
    good = '<div class="teamtophome">Alice Adams</div><div class="teamtopaway">Beth Brown</div><h4>Winner</h4><h5>Alice Adams</h5>'
    calls = []
    def get(link, **kwargs):
        calls.append(link)
        if 'yesterdays-tennis-predictions' in link:
            return SimpleNamespace(status_code=200, text=f'<a href="{url}">match</a>')
        if 'todays-tennis-predictions' in link:
            return SimpleNamespace(status_code=200, text=f'<a href="{url}">match</a><a href="{bad}">bad</a>')
        if 'tomorrows-tennis-predictions' in link:
            return SimpleNamespace(status_code=200, text='no matches')
        if link == url:
            return SimpleNamespace(status_code=200, text=good)
        if link == bad:
            return SimpleNamespace(status_code=503, text='')
        pytest.fail('unsupported guessed request: ' + link)
    monkeypatch.setattr(betclan.requests, 'get', get)
    source = betclan.BetClanPredictor()
    rows = source.fetch_daily()
    assert len(rows) >= 1
    assert source.last_collection_status == 'partial'
    stats = source.last_request_stats
    assert calls.count(url) == 1 and stats['detail_duplicate_skips'] == 1
    assert stats['today_detail_reused'] == 1
    assert stats['today_detail_attempted'] == 1 and stats['today_detail_processed'] == 2
    assert stats['today_detail_failed'] == 1
    assert stats['odds_subpage_requests'] == 0
    assert stats['by_category']['detail']['http_statuses']['503'] == 1
    assert stats['by_category']['detail']['http_failures'] == stats['http_failures'] == 1


def test_optional_odds_404_is_event_scoped_and_repeated_url_is_not_refetched(monkeypatch):
    source = betclan.BetClanPredictor()
    calls = []
    def get(link, **kwargs):
        calls.append((link, kwargs))
        return SimpleNamespace(status_code=404, text='')
    monkeypatch.setattr(betclan.requests, 'get', get)
    first = 'https://www.betclan.com/tennis/odds/a/'
    second = 'https://www.betclan.com/tennis/odds/b/'
    assert source._fetch_odds_subpage(first) is None
    assert source._fetch_odds_subpage(first) is None
    assert source._fetch_odds_subpage(second) is None
    assert len(calls) == 2 and all(x[1]['allow_redirects'] is False for x in calls)


@pytest.mark.parametrize('status', [401, 403, 429])
def test_optional_odds_restriction_stops_further_optional_requests(monkeypatch, status):
    source = betclan.BetClanPredictor()
    calls = []
    monkeypatch.setattr(betclan.requests, 'get', lambda url, **kwargs: calls.append(url) or SimpleNamespace(status_code=status, text=''))
    assert source._fetch_odds_subpage('https://www.betclan.com/tennis/odds/a/') is None
    assert source._fetch_odds_subpage('https://www.betclan.com/tennis/odds/b/') is None
    assert len(calls) == 1


def test_unrecognized_listing_sample_is_bounded_and_remains_partial(monkeypatch, tmp_path):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    html = '''<table><tr data-dt="2026,10,29,12,0"><td><a href="/tennis/atp/tour/fixture/abc/">Alice - Beth</a><span class="odd">1.50 2.00</span></td></tr>
    <tr data-dt="2026,10,29,13,0"><td><input value="PRIVATESECRET"><a href="/tennis/unknown/route?token=PRIVATESECRET">unrecognised</a></td></tr></table>'''
    monkeypatch.setattr(betexplorer, 'fetch_page_html', lambda *a, **k: html)
    metrics = {}
    rows, status = betexplorer._fetch_live('2026-10-29', with_status=True, metrics=metrics)
    assert status == 'partial' and metrics['unrecognized_rows'] == 1
    data = json.loads((tmp_path / 'betexplorer_unrecognized_rows.json').read_text())
    assert len(data) == 1 and data[0]['classification'] == 'unresolved'
    assert 'PRIVATESECRET' not in json.dumps(data)
    assert data[0]['links'][0]['public_path'] is None


FIXTURE = {'event_id': 'abc', 'match_url': '/tennis/atp/test/alice-beth/abc/',
           'match_date': '2026-10-29', 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}


class FakeRequest:
    def __init__(self, url, kind='document', method='GET', redirected_from=None):
        self.url, self.resource_type, self.method, self.redirected_from = url, kind, method, redirected_from


class FakeRoute:
    def __init__(self, body='', status=200, headers=None):
        self.body_text, self.status = body, status
        self.headers = headers if headers is not None else {'content-length': str(len(body.encode()))}
        self.actions = []
    def continue_(self): self.actions.append(('continue',))
    def abort(self): self.actions.append(('abort',))
    def response(self, request):
        return SimpleNamespace(request=request, status=self.status, headers=self.headers,
                               body=lambda: self.body_text.encode())


def test_browser_observer_only_page_initiated_bounded_sanitized_requests(monkeypatch, tmp_path):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setattr('racketfactory.sources._page_odds._throttle', lambda _: None)
    observer = evidence.BrowserObservation(FIXTURE)
    script = FakeRoute()
    observer.handle(script, FakeRequest('https://www.betexplorer.com/js/app.js', 'script'))
    assert script.actions == [('abort',)]
    page = FakeRoute('<div data-market="Match Winner" data-period="match"></div>')
    request = FakeRequest(observer.document_url)
    observer.handle(page, request)
    assert page.actions[-1] == ('continue',)
    observer.on_response(page.response(request))
    observer.on_request_finished(request)
    unsafe = FakeRoute()
    observer.handle(unsafe, FakeRequest('https://evil.example/match/odds/abc', 'xhr'))
    assert unsafe.actions == [('abort',)]
    secret = FakeRoute()
    observer.handle(secret, FakeRequest('https://www.betexplorer.com/match-odds/abc/?token=PRIVATESECRET', 'xhr'))
    assert secret.actions == [('abort',)]
    html = '<table><tr data-bookie="4"><td><img title="Book One"></td><td data-odd="1.5"></td></tr></table>'
    comparison = FakeRoute(json.dumps({'odds': html, 'secret': 'PRIVATESECRET'}))
    request = FakeRequest('https://www.betexplorer.com/match-odds/abc/', 'xhr')
    observer.handle(comparison, request)
    assert comparison.actions[-1] == ('continue',)
    observer.on_response(comparison.response(request))
    observer.on_request_finished(request)
    assert observer.result['requests_allowed'] == 2
    assert observer.result['quote_eligibility'] == 'not_evaluated'
    assert 'PRIVATESECRET' not in (tmp_path / 'betexplorer_replay_abc.json').read_text()
    more = FakeRoute()
    observer.handle(more, FakeRequest('https://www.betexplorer.com/match-odds/other/', 'xhr'))
    assert more.actions == [('abort',)] and observer.result['requests_allowed'] == 2


@pytest.mark.parametrize(('status', 'body', 'reason'), [(403, '', 'access_restricted'), (429, '', 'rate_limit'), (200, 'verify you are human', 'challenge')])
def test_browser_observer_stops_on_restriction(monkeypatch, tmp_path, status, body, reason):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setattr('racketfactory.sources._page_odds._throttle', lambda _: None)
    monkeypatch.setattr('racketfactory.quota_guard.record_cooldown', lambda *a: None)
    observer = evidence.BrowserObservation(FIXTURE)
    route = FakeRoute(body, status)
    request = FakeRequest(observer.document_url)
    observer.handle(route, request)
    observer.on_response(route.response(request))
    observer.on_request_finished(request)
    assert observer.result['restriction'] == reason and route.actions[-1] == ('continue',)
    another = FakeRoute()
    observer.handle(another, FakeRequest('https://www.betexplorer.com/match-odds/abc/', 'xhr'))
    assert another.actions == [('abort',)]


def test_browser_observer_refuses_unbounded_body_and_redirect(monkeypatch):
    monkeypatch.setattr('racketfactory.sources._page_odds._throttle', lambda _: None)
    observer = evidence.BrowserObservation(FIXTURE)
    route = FakeRoute('x', headers={})
    request = FakeRequest(observer.document_url)
    observer.handle(route, request)
    observer.on_response(route.response(request))
    observer.on_request_finished(request)
    assert route.actions[-1] == ('continue',) and observer.stopped
    other = evidence.BrowserObservation(FIXTURE)
    redirect = FakeRoute()
    other.handle(redirect, FakeRequest(other.document_url, redirected_from=object()))
    assert redirect.actions == [('abort',)] and other.allowed == 0


def test_browser_probe_substitutes_for_direct_capture(monkeypatch):
    from scripts import capture_betexplorer_evidence as probe
    from racketfactory.kickoff import SAST
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(probe, 'datetime', Clock)
    monkeypatch.setattr(probe.betexplorer, '_fetch_live', lambda *a, **k: ([dict(FIXTURE, match_time='11:00')], 'complete'))
    monkeypatch.setattr(probe.betexplorer, 'fetch_fixture_comparison', lambda *a, **k: pytest.fail('direct fetch must not run'))
    result = {'responses': [{'resource': 'document', 'http_status': 200}],
              'detail_file_written': True, 'detail_material_retained': False,
              'detail_material_sufficiency': 'insufficient', 'comparison_material_retained': False}
    monkeypatch.setattr(evidence, 'observe_in_browser', lambda fixture: result)
    out = probe.capture('2026-10-29', browser_observer=True)
    assert out['fixtures_attempted'] == 1 and out['capture_status'] == 'browser_observation_inconclusive'
    assert out['detail_response_fetched'] and not out['discovery_material_retained']
    assert out['quote_eligibility'] == 'not_evaluated'


def test_browser_observer_rejects_invalid_fixture_and_existing_cooldown(monkeypatch):
    with pytest.raises(ValueError):
        evidence.BrowserObservation(dict(FIXTURE, event_id='different'))
    monkeypatch.setattr('racketfactory.quota_guard.cooldown_status', lambda *a: {'kind': 'rate_limit'})
    result = evidence.observe_in_browser(FIXTURE)
    assert result['restriction'] == 'existing_cooldown' and result['requests_allowed'] == 0


def test_disabled_forebet_label_does_not_report_failed_relay(monkeypatch, caplog):
    from racketfactory.sources.forebet import ForebetPredictor
    monkeypatch.setenv('RACKET_FACTORY_DISABLE_FOREBET', '1')
    monkeypatch.setattr(ForebetPredictor, '_fetch_daily_page', lambda *a: pytest.fail('disabled'))
    with caplog.at_level('INFO'):
        assert ForebetPredictor().fetch_daily_predictions('today') == []
    assert 'live collection disabled' in caplog.text and 'relay failed' not in caplog.text


def test_optional_odds_failure_is_not_required_prediction_failure(monkeypatch):
    url = 'https://www.betclan.com/tennis/predictionsdetails/example/'
    odds = 'https://www.betclan.com/tennis/odds/example/'
    good = ('<div class="teamtophome">Alice Adams</div><div class="teamtopaway">Beth Brown</div>'
            '<h4>Winner</h4><h5>Alice Adams</h5><a href="/tennis/odds/example/">odds</a>')
    def get(link, **kwargs):
        if 'todays-tennis-predictions' in link:
            return SimpleNamespace(status_code=200, text=f'<a href="{url}">match</a>')
        if link == url:
            return SimpleNamespace(status_code=200, text=good)
        if link == odds:
            return SimpleNamespace(status_code=404, text='')
        return SimpleNamespace(status_code=200, text='no matches')
    monkeypatch.setattr(betclan.requests, 'get', get)
    source = betclan.BetClanPredictor()
    rows = source.fetch_daily()
    stats = source.last_request_stats
    assert len(rows) == 1 and source.last_collection_status == 'complete'
    assert stats['today_detail_failed'] == 0 and stats['odds_subpage_requests'] == 1
    assert stats['by_category']['odds_subpage']['http_statuses'] == {'404': 1}
    assert '404' in stats['by_category']['odds_subpage']['http_status_seconds']


def test_observer_rejects_wrong_or_unbound_event_identity(monkeypatch):
    monkeypatch.setattr('racketfactory.sources._page_odds._throttle', lambda _: None)
    observer = evidence.BrowserObservation(FIXTURE)
    observer.document_seen = True
    for url in ('https://www.betexplorer.com/match-odds/different/',
                'https://www.betexplorer.com/match-odds/abc/?event_id=different'):
        route = FakeRoute()
        observer.handle(route, FakeRequest(url, 'xhr'))
        assert route.actions == [('abort',)]
    assert observer.allowed == 0


def test_browser_entrypoint_wires_interception_without_direct_fetch(monkeypatch, tmp_path):
    import playwright.sync_api
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    monkeypatch.setattr('racketfactory.sources._page_odds._throttle', lambda _: None)
    monkeypatch.setattr('racketfactory.quota_guard.cooldown_status', lambda *a: None)
    seen = []
    class FakePage:
        def __init__(self, context): self.context = context
        def goto(self, url, **kwargs):
            seen.append(('goto', url))
            request = FakeRequest(url)
            route = FakeRoute('<div data-market="Match Winner"></div>')
            self.context.handler(route, request)
            assert route.actions == [('continue',)]
            self.context.handlers['response'](route.response(request))
            self.context.handlers['requestfinished'](request)
        def wait_for_timeout(self, timeout): seen.append(('wait', timeout))
    class FakeContext:
        def __init__(self): self.handlers = {}
        def route(self, pattern, handler): self.handler = handler
        def on(self, event, handler): self.handlers[event] = handler
        def new_page(self): return FakePage(self)
        def close(self): seen.append(('context_closed',))
    class FakeBrowser:
        def new_context(self, **kwargs):
            assert kwargs == {'service_workers': 'block', 'accept_downloads': False}
            return FakeContext()
        def close(self): seen.append(('browser_closed',))
    class Manager:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: FakeBrowser()))
        def __exit__(self, *a): pass
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', Manager)
    out = evidence.observe_in_browser(FIXTURE)
    assert out['requests_allowed'] == 1 and out['detail_file_written']
    assert seen[0][0] == 'goto' and ('context_closed',) in seen and ('browser_closed',) in seen


def test_browser_inflight_and_failed_request_stop_additional_network(monkeypatch):
    monkeypatch.setattr('racketfactory.sources._page_odds._throttle', lambda _: None)
    observer = evidence.BrowserObservation(FIXTURE)
    request = FakeRequest(observer.document_url)
    route = FakeRoute()
    observer.handle(route, request)
    second = FakeRoute()
    observer.handle(second, FakeRequest('https://www.betexplorer.com/match-odds/abc/', 'xhr'))
    assert second.actions == [('abort',)] and observer.allowed == 1
    observer.on_request_failed(request)
    third = FakeRoute()
    observer.handle(third, FakeRequest('https://www.betexplorer.com/match-odds/abc/', 'xhr'))
    assert third.actions == [('abort',)] and observer.result['response_error'] == 'request_failed'
