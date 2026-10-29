import urllib.error
import urllib.request

import pytest

from racketfactory.quota_guard import get_count, is_blocked
from racketfactory.sources.forebet import relay_get


def test_402_blocks_later_relay_requests(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError('https://r.jina.ai/x', 402, 'payment', {}, None)
    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    with pytest.raises(urllib.error.HTTPError):
        relay_get('https://r.jina.ai/x')
    with pytest.raises(RuntimeError):
        relay_get('https://r.jina.ai/y')
    assert len(calls) == 1 and get_count('jina') == 1 and is_blocked('jina')


def test_each_retry_counts_as_a_separate_request(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'ok'
    calls = []
    def response(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError('https://r.jina.ai/x', 503, 'unavailable', {}, None)
        return Response()
    monkeypatch.setattr(urllib.request, 'urlopen', response)
    monkeypatch.setattr('racketfactory.sources.forebet._sleep_with_jitter', lambda _: None)
    assert relay_get('https://r.jina.ai/x') == b'ok'
    assert get_count('jina') == 2


def test_disabled_forebet_does_not_attempt_relay_or_direct(monkeypatch):
    from racketfactory.sources.forebet import ForebetPredictor
    monkeypatch.setenv('RACKET_FACTORY_DISABLE_FOREBET', '1')
    p = ForebetPredictor()
    def fail(*args, **kwargs):
        raise AssertionError('disabled source must not access provider')
    monkeypatch.setattr(p, '_fetch_via_relay', fail)
    monkeypatch.setattr(p, '_fetch_direct', fail)
    assert p._fetch('https://example.com') is None
