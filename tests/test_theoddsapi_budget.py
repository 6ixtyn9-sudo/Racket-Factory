"""Quota ledger + sports discovery for The Odds API (mocked HTTP)."""

import json

from racketfactory import warehouse


class _Resp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_invalid_generic_tennis_key_dropped_from_defaults():
    assert "tennis" not in warehouse.DEFAULT_THE_ODDS_API_SPORTS.split(",")


def test_ledger_blocks_when_all_keys_below_reserve(tmp_path, monkeypatch):
    ledger = {
        "month": warehouse._odds_api_month_key(),
        "keys": {"fp1": {"remaining": 3, "used": 497}},
    }
    monkeypatch.setattr(warehouse, "_odds_api_usage_path", lambda: tmp_path / "usage.json")
    (tmp_path / "usage.json").write_text(json.dumps(ledger))
    monkeypatch.setattr(warehouse, "_key_fingerprint", lambda k: "fp1")
    monkeypatch.setenv("THE_ODDS_API_MIN_REMAINING", "25")
    assert warehouse.odds_api_budget_ok(("any-key",)) is False


def test_ledger_blocks_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse, "_odds_api_usage_path", lambda: tmp_path / "usage.json")
    assert warehouse.odds_api_budget_ok(("brand-new-key",)) is False


def test_record_usage_persists_fingerprint_not_key(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse, "_odds_api_usage_path", lambda: tmp_path / "usage.json")
    warehouse.record_odds_api_usage(
        "live-secret-key",
        {"x-requests-remaining": "100", "x-requests-used": "5", "x-requests-last": "2"},
        endpoint="odds:tennis_atp_x",
    )
    payload = json.loads((tmp_path / "usage.json").read_text())
    assert "live-secret-key" not in json.dumps(payload)
    (fp, slot), = payload["keys"].items()
    assert len(fp) == 8 and slot["remaining"] == 100 and slot["used"] == 5


def test_discovery_filters_inactive_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse, "_odds_api_sports_cache_path",
                        lambda: tmp_path / "sports.json")
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        assert params and "apiKey" in params
        return _Resp([
            {"key": "tennis_atp_usopen", "group": "Tennis", "active": True},
            {"key": "tennis", "group": "Tennis", "active": True},
            {"key": "tennis_wta_dead", "group": "Tennis", "active": False},
            {"key": "soccer_epl", "group": "Soccer", "active": True},
        ])

    monkeypatch.setattr(warehouse.requests, "get", fake_get)
    keys = warehouse.discover_active_tennis_sports(("k1",))
    assert keys == ["tennis_atp_usopen", "tennis"]
    # second call served from cache (no new HTTP)
    assert warehouse.discover_active_tennis_sports(("k1",)) == keys
    assert len(calls) == 1


def _seed_credits(monkeypatch, tmp_path, key='fake-key', remaining=100):
    from racketfactory import quota_guard as q
    monkeypatch.setenv('RACKET_FACTORY_QUOTA_DIR', str(tmp_path))
    monkeypatch.setenv('THE_ODDS_API_MAX_CREDITS_PER_CALL', '4')
    service, day = q._odds_credit_identity(key)
    path = q._quota_path(service, day)
    path.write_text(json.dumps({'service': service, 'date': day, 'count': 0,
                                'blocked': False, 'remaining_credits': remaining}))
    return q, path


def test_credit_reservations_persist_and_never_refund(monkeypatch, tmp_path):
    q, path = _seed_credits(monkeypatch, tmp_path)
    assert q.reserve_odds_credits('fake-key', min_cost=4, reserve=25)
    data = json.loads(path.read_text())
    assert data['remaining_credits'] == 96 and data['count'] == 1
    assert 'fake-key' not in path.read_text()
    q.reconcile_odds_credits('fake-key', {'x-requests-remaining': '99'})
    assert json.loads(path.read_text())['remaining_credits'] == 96
    q.reconcile_odds_credits('fake-key', {'x-requests-remaining': '80'})
    assert json.loads(path.read_text())['remaining_credits'] == 80


def test_missing_corrupt_and_underestimated_credit_bounds_block(monkeypatch, tmp_path):
    q, path = _seed_credits(monkeypatch, tmp_path)
    assert not q.reserve_odds_credits('fake-key', min_cost=5, reserve=25)
    path.write_text('invalid')
    assert not q.reserve_odds_credits('fake-key', reserve=25)
    path.unlink()
    assert not q.reserve_odds_credits('fake-key', reserve=25)


def test_concurrent_credit_reservations_preserve_reserve(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    q, path = _seed_credits(monkeypatch, tmp_path, remaining=37)
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: q.reserve_odds_credits('fake-key', min_cost=4, reserve=25), range(20)))
    assert sum(result) == 3
    payload = json.loads(path.read_text())
    assert payload['remaining_credits'] == 25 and payload['count'] == 3


def test_discovery_401_blocks_retries_and_does_not_leak_key(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(warehouse, '_odds_api_sports_cache_path', lambda: tmp_path / 'sports.json')
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        return _Resp([], status=401, headers={})
    monkeypatch.setattr(warehouse.requests, 'get', fake)
    assert warehouse.discover_active_tennis_sports(('secret-key',)) is None
    assert warehouse.discover_active_tennis_sports(('secret-key',)) is None
    assert len(calls) == 1
    assert 'secret-key' not in caplog.text


def test_uncached_odds_reserve_before_http_and_never_rotate(monkeypatch, tmp_path):
    q, path = _seed_credits(monkeypatch, tmp_path, key='first-key', remaining=100)
    monkeypatch.setenv('THE_ODDS_API_KEYS', 'first-key,second-key')
    monkeypatch.setenv('THE_ODDS_API_REGIONS', 'uk,eu,us,au')
    monkeypatch.setenv('THE_ODDS_API_SPORT_KEYS', 'tennis_atp,tennis_wta')
    monkeypatch.setattr(warehouse, 'load_warehouse_env', lambda: None)
    monkeypatch.setattr(warehouse, 'discover_active_tennis_sports', lambda keys: None)
    monkeypatch.setattr(warehouse, '_the_odds_api_cache_file', lambda _: tmp_path / 'odds.json')
    calls = []
    def fake(url, params=None, **kwargs):
        calls.append(params['apiKey'])
        # Reservation must have hit durable storage before the HTTP boundary.
        assert json.loads(path.read_text())['remaining_credits'] == 96
        return _Resp([], status=401, headers={})
    monkeypatch.setattr(warehouse.requests, 'get', fake)
    assert warehouse.fetch_the_odds_api_rows('2026-10-29') == []
    assert calls == ['first-key']
    assert q.cooldown_status('theoddsapi', 'first-key')['kind'] == 'auth'
    assert json.loads(path.read_text())['count'] == 1


def test_provider_cost_above_bound_blocks_further_calls(monkeypatch, tmp_path):
    q, path = _seed_credits(monkeypatch, tmp_path, remaining=100)
    assert q.reserve_odds_credits('fake-key', min_cost=4, reserve=25)
    q.reconcile_odds_credits('fake-key', {'x-requests-last': '5', 'x-requests-remaining': '95'})
    data = json.loads(path.read_text())
    assert data['blocked'] is True and data['remaining_credits'] == 95
    assert not q.reserve_odds_credits('fake-key', min_cost=4, reserve=25)


def test_scores_401_is_a_failure_and_cooldown_blocks_retry(monkeypatch, tmp_path):
    from urllib.error import HTTPError
    import pytest
    from scripts import capture_theoddsapi_scores as scores
    q, path = _seed_credits(monkeypatch, tmp_path, key='fake-key', remaining=100)
    monkeypatch.setattr(scores, 'LOCALDATA', tmp_path)
    calls = []
    def fake(request, timeout=None):
        calls.append(1)
        raise HTTPError('https://example.test/?apiKey=fake-key', 401, 'rejected', {}, None)
    monkeypatch.setattr(scores, 'urlopen', fake)
    with pytest.raises(scores.ScoresFetchError) as exc:
        scores.fetch_scores('tennis_atp', 3, 'fake-key')
    assert exc.value.kind == 'auth' and 'fake-key' not in str(exc.value)
    with pytest.raises(scores.ScoresFetchError):
        scores.fetch_scores('tennis_atp', 3, 'fake-key')
    assert len(calls) == 1
    assert json.loads(path.read_text())['remaining_credits'] == 96
    assert q.cooldown_status('theoddsapi', 'fake-key')['kind'] == 'auth'
