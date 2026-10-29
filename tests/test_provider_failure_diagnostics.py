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
