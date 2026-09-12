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


def test_ledger_allows_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse, "_odds_api_usage_path", lambda: tmp_path / "usage.json")
    assert warehouse.odds_api_budget_ok(("brand-new-key",)) is True


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
