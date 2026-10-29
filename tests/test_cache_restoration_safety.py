import json
import os
import time

from racketfactory import fetch_cache, warehouse
from scripts import capture_theoddsapi_scores as scores


def test_restored_fetch_cache_cannot_refresh_old_observation(tmp_path):
    fetch_cache.write_cached_rows('x', [{'row': 1}], cache_dir=tmp_path)
    path = fetch_cache.cache_path('x', cache_dir=tmp_path)
    payload = json.loads(path.read_text())
    payload['cached_at'] = time.time() - 7200
    path.write_text(json.dumps(payload))
    os.utime(path, None)
    assert fetch_cache.read_cached_rows('x', ttl_minutes=30, cache_dir=tmp_path) is None


def test_restored_odds_cache_uses_original_capture_time(tmp_path, monkeypatch):
    path = tmp_path / 'odds.json'
    monkeypatch.setattr(warehouse, '_the_odds_api_cache_file', lambda _: path)
    monkeypatch.setenv('THE_ODDS_API_CACHE_TTL_MINUTES', '30')
    path.write_text(json.dumps({'meta': {'cached_at': time.time() - 7200, 'sports': ['s'],
                                         'regions': 'uk', 'bookmakers': ''}, 'rows': [{'x': 1}]}))
    assert warehouse._load_the_odds_api_cache('2026-10-29', ('s',), 'uk', '') is None


def test_scores_cache_uses_capture_time_not_restored_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(scores, 'LOCALDATA', tmp_path)
    monkeypatch.setenv('THE_ODDS_API_SCORES_CACHE_MINUTES', '60')
    path = scores._scores_cache_path('tennis_atp', 3, '2026-10-29')
    path.write_text(json.dumps({'cached_at': time.time() - 7200, 'events': [{'x': 1}]}))
    assert scores._load_scores_cache('tennis_atp', 3, '2026-10-29') is None
    scores._write_scores_cache('tennis_atp', 3, '2026-10-29', [{'x': 1}])
    assert scores._load_scores_cache('tennis_atp', 3, '2026-10-29') == [{'x': 1}]


def test_scores_credit_failure_is_not_successful_empty(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(scores, 'LOCALDATA', tmp_path)
    monkeypatch.delenv('THE_ODDS_API_MAX_CREDITS_PER_CALL', raising=False)
    with pytest.raises(scores.ScoresFetchError) as exc:
        scores.fetch_scores('tennis_atp', 3, 'not-a-real-secret')
    assert exc.value.kind == 'quota'
    assert 'not-a-real-secret' not in str(exc.value)


def test_fresh_paid_quote_cache_works_without_key_or_authority(tmp_path, monkeypatch):
    path = tmp_path / 'odds.json'
    monkeypatch.setattr(warehouse, '_the_odds_api_cache_file', lambda _: path)
    monkeypatch.setattr(warehouse, 'load_warehouse_env', lambda: None)
    monkeypatch.setenv('THE_ODDS_API_SPORT_KEYS', 'tennis_atp,tennis_wta')
    monkeypatch.setenv('THE_ODDS_API_REGIONS', 'uk,eu')
    monkeypatch.delenv('THE_ODDS_API_BOOKMAKERS', raising=False)
    monkeypatch.delenv('THE_ODDS_API_KEY', raising=False)
    monkeypatch.delenv('THE_ODDS_API_KEYS', raising=False)
    monkeypatch.delenv('RACKET_FACTORY_QUOTA_DIR', raising=False)
    rows = [{'player_home': 'Alice', 'player_away': 'Beth', 'odds_home': 1.5, 'odds_away': 2.6}]
    warehouse._write_the_odds_api_cache('2026-10-29', ('tennis_atp', 'tennis_wta'), 'uk,eu', '', rows)
    monkeypatch.setattr(warehouse, 'discover_active_tennis_sports', lambda _: (_ for _ in ()).throw(AssertionError('discovery should not run')))
    assert warehouse.fetch_the_odds_api_rows('2026-10-29') == rows
    data = json.loads(path.read_text())
    data['meta']['cached_at'] = time.time() - 7200
    path.write_text(json.dumps(data))
    assert warehouse.fetch_the_odds_api_rows('2026-10-29') == []


def test_optional_provider_quota_block_is_health_warning(tmp_path, monkeypatch):
    from racketfactory import doctor
    monkeypatch.setattr(doctor, 'LOCALDATA', tmp_path)
    monkeypatch.setenv('BZZOIRO_TOKEN', 'fake-key')
    monkeypatch.setenv('THE_ODDS_API_KEY', 'fake-key')
    monkeypatch.delenv('RACKET_FACTORY_QUOTA_DIR', raising=False)
    checks = doctor.run_health_checks(as_of='2026-10-29')
    assert checks['providers:credit_authority']['status'] == 'WARN'
    assert 'blocked' in checks['providers:credit_authority']['detail']
    assert checks['providers:quota_authority']['status'] == 'WARN'
    assert 'blocked' in checks['providers:quota_authority']['detail']


def test_scores_cli_uses_fresh_cache_with_no_key(tmp_path, monkeypatch):
    import sys
    from datetime import date
    monkeypatch.setattr(scores, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(scores, 'load_env', lambda: None)
    monkeypatch.setattr(warehouse, 'load_warehouse_env', lambda: None)
    monkeypatch.delenv('THE_ODDS_API_KEY', raising=False)
    monkeypatch.delenv('THE_ODDS_API_KEYS', raising=False)
    monkeypatch.setenv('THE_ODDS_API_SCORE_SPORT_KEYS', 'tennis_atp')
    monkeypatch.setattr(warehouse, 'discover_active_tennis_sports', lambda _: (_ for _ in ()).throw(AssertionError('must not discover')))
    scores._write_scores_cache('tennis_atp', 3, date.today().isoformat(), [])
    monkeypatch.setattr(sys, 'argv', ['scores', '--output-dir', str(tmp_path)])
    assert scores.main() == 0
    scores._scores_cache_path('tennis_atp', 3, date.today().isoformat()).unlink()
    assert scores.main() == 1
