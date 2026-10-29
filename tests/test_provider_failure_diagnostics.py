from types import SimpleNamespace
import pytest
from racketfactory.sources import bzzoiro
from racketfactory.quota_guard import get_count, cooldown_status


def test_bzzoiro_missing_token_makes_no_request(monkeypatch):
    monkeypatch.setattr(bzzoiro, 'BZZOIRO_TOKEN', '')
    def fail(*a, **k):
        raise AssertionError('must not request with missing token')
    monkeypatch.setattr(bzzoiro.requests, 'get', fail)
    with pytest.raises(bzzoiro.SourceFetchError) as exc:
        bzzoiro.BzzoiroPredictor().fetch_daily()
    assert exc.value.kind == 'auth'


def test_bzzoiro_401_blocks_repeat_without_exposing_secret(monkeypatch, caplog):
    monkeypatch.setattr(bzzoiro, 'BZZOIRO_TOKEN', 'private-test-token')
    calls = []
    def fake(*a, **k):
        calls.append(1)
        return SimpleNamespace(status_code=401)
    monkeypatch.setattr(bzzoiro.requests, 'get', fake)
    for _ in range(2):
        with pytest.raises(bzzoiro.SourceFetchError) as exc:
            bzzoiro.BzzoiroPredictor().fetch_daily()
        assert exc.value.kind == 'auth'
        assert 'private-test-token' not in str(exc.value)
    assert len(calls) == 1 and get_count('bzzoiro') == 1
    assert 'private-test-token' not in caplog.text


def test_empty_success_is_not_a_parser_or_provider_failure(monkeypatch):
    monkeypatch.setattr(bzzoiro, 'BZZOIRO_TOKEN', 'token')
    monkeypatch.setattr(bzzoiro.requests, 'get', lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {'results': [], 'next': None}))
    assert bzzoiro.BzzoiroPredictor().fetch_daily() == []


def test_429_respects_cooldown_without_fallback(monkeypatch):
    monkeypatch.setattr(bzzoiro, 'BZZOIRO_TOKEN', 'token')
    calls = []
    def fake(*a, **kw):
        calls.append(1)
        return SimpleNamespace(status_code=429, headers={'Retry-After': '180'})
    monkeypatch.setattr(bzzoiro.requests, 'get', fake)
    for _ in range(2):
        with pytest.raises(bzzoiro.SourceFetchError) as exc:
            bzzoiro.BzzoiroPredictor().fetch_daily()
        assert exc.value.kind == 'rate_limit'
    assert len(calls) == 1 and cooldown_status('bzzoiro', 'token')['kind'] == 'rate_limit'


def test_malformed_json_is_parser_failure(monkeypatch):
    monkeypatch.setattr(bzzoiro, 'BZZOIRO_TOKEN', 'token')
    monkeypatch.setattr(bzzoiro.requests, 'get', lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {'wrong': []}))
    with pytest.raises(bzzoiro.SourceFetchError) as exc:
        bzzoiro.BzzoiroPredictor().fetch_daily()
    assert exc.value.kind == 'parser'


def test_successful_empty_can_be_cached_but_failures_cannot(tmp_path):
    from racketfactory.fetch_cache import cached_fetch
    calls = []
    def empty():
        calls.append(1)
        return []
    assert cached_fetch('bzz-test', empty, ttl_minutes=5, cache_dir=tmp_path, cache_empty=True) == []
    assert cached_fetch('bzz-test', empty, ttl_minutes=5, cache_dir=tmp_path, cache_empty=True) == []
    assert len(calls) == 1
    def failure():
        raise bzzoiro.SourceFetchError('auth', 'rejected')
    for _ in range(2):
        with pytest.raises(bzzoiro.SourceFetchError):
            cached_fetch('failed', failure, ttl_minutes=5, cache_dir=tmp_path, cache_empty=True)
    assert not list(tmp_path.glob('fetch_failed*'))


def test_bzzoiro_rechecks_after_auth_cooldown_expires(monkeypatch):
    import json, time
    from racketfactory.quota_guard import _failure_path
    monkeypatch.setattr(bzzoiro, 'BZZOIRO_TOKEN', 'token')
    responses = [SimpleNamespace(status_code=401),
                 SimpleNamespace(status_code=200, json=lambda: {'results': [], 'next': None})]
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        return responses.pop(0)
    monkeypatch.setattr(bzzoiro.requests, 'get', fake)
    with pytest.raises(bzzoiro.SourceFetchError):
        bzzoiro.BzzoiroPredictor().fetch_daily()
    _failure_path('bzzoiro', 'token').write_text(json.dumps({'kind': 'auth', 'until': time.time() - 1}))
    assert bzzoiro.BzzoiroPredictor().fetch_daily() == []
    assert len(calls) == 2


def test_prediction_sources_fail_on_transport_not_success_empty(monkeypatch):
    from racketfactory.sources import betclan, predixsport
    from racketfactory.sources._page_odds import PageFetchError
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic offline failure")
    monkeypatch.setattr(betclan.requests, "get", fail)
    with pytest.raises(PageFetchError, match="BetClan today listing unavailable"):
        betclan.BetClanPredictor().fetch_daily()
    monkeypatch.setattr(predixsport.requests, "get", fail)
    with pytest.raises(PageFetchError, match="PredixSport index unavailable"):
        predixsport.PredixSportPredictor().fetch_daily()


def test_comparison_provider_failure_is_recorded(tmp_path, monkeypatch):
    import json
    from racketfactory import odds_compare
    from racketfactory.sources import betexplorer, oddsportal_upcoming
    from racketfactory.sources._page_odds import PageFetchError
    monkeypatch.setenv(odds_compare.STATUS_ENV, str(tmp_path / "status.json"))
    monkeypatch.setattr(betexplorer, "fetch_betexplorer_rows", lambda *a: (_ for _ in ()).throw(PageFetchError("transport", "failed")))
    monkeypatch.setattr(oddsportal_upcoming, "fetch_oddsportal_upcoming_rows", lambda *a: [])
    assert odds_compare.fetch_comparison_rows("2026-10-29") == []
    status = json.loads((tmp_path / "status.json").read_text())["2026-10-29"]
    assert "transport" in status["errors"]["betexplorer"]


def test_comparison_status_keeps_partial_and_unknown_distinct(tmp_path, monkeypatch):
    import json
    from racketfactory import odds_compare
    from racketfactory.sources import betexplorer, oddsportal_upcoming
    monkeypatch.setenv(odds_compare.STATUS_ENV, str(tmp_path / "status.json"))
    monkeypatch.setattr(betexplorer, "fetch_betexplorer_rows", lambda *a: [{
        "match_date": "2026-10-29", "player_home": "Alice Adams", "player_away": "Beth Brown",
        "odds_home": 1.5, "odds_away": 2.5, "_collection_partial": True}])
    monkeypatch.setattr(oddsportal_upcoming, "fetch_oddsportal_upcoming_rows", lambda *a: [])
    assert len(odds_compare.fetch_comparison_rows("2026-10-29")) == 1
    status = json.loads((tmp_path / "status.json").read_text())["2026-10-29"]["source_status"]
    assert status == {"betexplorer": "partial", "oddsportal_upcoming": "empty_or_unknown"}


def test_guarded_collection_required_and_optional_outcomes(monkeypatch):
    from scripts import daily
    from racketfactory.sources import betclan, predixsport, betexplorer, oddsportal_upcoming
    class FakeBetClan:
        last_collection_status = "complete"
        def fetch_daily(self):
            return [{"match_date": "2026-10-29", "player_home": "Alice Adams", "player_away": "Beth Brown"}]
    class FailedPredix:
        def fetch_daily(self):
            raise RuntimeError("offline")
    monkeypatch.setattr(betclan, "BetClanPredictor", FakeBetClan)
    monkeypatch.setattr(predixsport, "PredixSportPredictor", FailedPredix)
    monkeypatch.delenv(betexplorer.DISABLE_ENV, raising=False)
    monkeypatch.setenv(oddsportal_upcoming.DISABLE_ENV, "1")
    from datetime import datetime, timezone
    quote = {"match_date": "2026-10-29", "player_home": "Alice Adams", "player_away": "Beth Brown", "odds_home": 1.5, "odds_away": 2.5, "event_id": "synthetic",
             "market": "h2h", "period": "match", "observed_at": datetime.now(timezone.utc).isoformat(),
             "price_type": "bookmaker", "bookmaker": "Synthetic Book",
             "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}}
    monkeypatch.setattr(betexplorer, "fetch_fixture_comparison", lambda f, **kwargs: [quote])
    monkeypatch.setattr(betexplorer, "_fetch_live", lambda d, with_status=False, metrics=None: ([quote], "complete"))
    collection = daily.collect_rebuild_sources("2026-10-29")
    assert collection["status"] == "complete"
    assert collection["outcomes"]["predixsport"]["status"] == "failed"
    assert collection["outcomes"]["oddsportal"]["status"] == "disabled"
    assert collection["outcomes"]["theoddsapi"]["status"] == "not_attempted"
    assert collection["outcomes"]["bzzoiro"]["status"] == "not_attempted"
    monkeypatch.setattr(betexplorer, "_fetch_live", lambda d, with_status=False, metrics=None: ([quote], "partial"))
    assert daily.collect_rebuild_sources("2026-10-29")["status"] == "insufficient"


def test_betclan_required_status_detects_partial_match_pages(monkeypatch):
    from racketfactory.sources import betclan
    class Response:
        status_code = 200
        def __init__(self, text): self.text = text
    url = "https://www.betclan.com/tennis/predictionsdetails/example/"
    def fake_get(link, **kwargs):
        if "todays-tennis-predictions" in link:
            return Response(f'<a href="{url}">Match</a>')
        if "predictionsdetails" in link:
            raise RuntimeError("synthetic match-page failure")
        return Response("<html>No predictions</html>")
    monkeypatch.setattr(betclan.requests, "get", fake_get)
    source = betclan.BetClanPredictor()
    assert source.fetch_daily() == []
    assert source.last_collection_status == "partial"
    profile = source.last_request_stats
    assert profile["today_discovered_unique"] == profile["today_detail_attempted"] == 1
    assert profile["today_detail_parsed"] == 0 and profile["today_detail_failed"] == 1
    assert profile["today_detail_failure_kinds"]["exception"] == 1


def test_betclan_detail_profile_counts_success_http_markup_and_exception(monkeypatch):
    from racketfactory.sources import betclan
    class Response:
        def __init__(self, text='', status=200):
            self.status_code, self.text = status, text
    urls = [f'https://www.betclan.com/tennis/predictionsdetails/{key}/' for key in ('good', 'badhttp', 'missing', 'error')]
    good = '<div class="teamtophome">Alice Adams</div><div class="teamtopaway">Beth Brown</div><h4>Winner</h4><h5>Alice Adams</h5>'
    def fake_get(link, **kwargs):
        if 'todays-tennis-predictions' in link:
            return Response(''.join(f'<a href="{u}">Match</a>' for u in urls))
        if '/predictionsdetails/good/' in link:
            return Response(good)
        if '/predictionsdetails/badhttp/' in link:
            return Response(status=503)
        if '/predictionsdetails/missing/' in link:
            return Response('<html>unexpected</html>')
        if '/predictionsdetails/error/' in link:
            raise RuntimeError('synthetic')
        return Response('<html>No predictions</html>')
    monkeypatch.setattr(betclan.requests, 'get', fake_get)
    monkeypatch.setattr(betclan.BetClanPredictor, '_extract_odds_from_soup', lambda *_: (1.5, 2.5))
    source = betclan.BetClanPredictor()
    rows = source.fetch_daily()
    profile = source.last_request_stats
    assert len(rows) == 1 and source.last_collection_status == 'partial'
    assert profile['today_discovered_unique'] == profile['today_detail_attempted'] == 4
    assert profile['today_detail_parsed'] == 1 and profile['today_detail_failed'] == 3
    assert profile['today_detail_failure_kinds'] == {'http': 1, 'participant_markup': 1, 'winner_markup': 0, 'winner_ambiguous': 0, 'exception': 1}
