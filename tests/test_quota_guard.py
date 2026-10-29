"""Tests for quota_guard: budget, block, cache behaviour."""
import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

from racketfactory.quota_guard import (
    _quota_path,
    get_count,
    get_limit,
    check_and_spend,
    record_402,
    is_blocked,
)


def _tmp_quota_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RACKET_FACTORY_QUOTA_DIR", str(tmp_path))
    for day in ("2026-09-20", "2026-09-21"):
        for service in ("bzzoiro", "jina"):
            (tmp_path / f"{service}_{day}.json").write_text(json.dumps(
                {"date": day, "service": service, "count": 0, "blocked": False}))
    return tmp_path


def test_bzzoiro_default_limit_100(monkeypatch):
    monkeypatch.delenv("RACKET_FACTORY_QUOTA_BZZOIRO", raising=False)
    assert get_limit("bzzoiro") == 100


def test_bzzoiro_env_override(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_QUOTA_BZZOIRO", "5")
    assert get_limit("bzzoiro") == 5


def test_check_and_spend_increments(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    assert get_count("bzzoiro", day="2026-09-20") == 0
    assert check_and_spend("bzzoiro", day="2026-09-20") is True
    assert get_count("bzzoiro", day="2026-09-20") == 1
    assert check_and_spend("bzzoiro", n=2, day="2026-09-20") is True
    assert get_count("bzzoiro", day="2026-09-20") == 3


def test_budget_exhaustion_blocks(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RACKET_FACTORY_QUOTA_BZZOIRO", "2")
    assert check_and_spend("bzzoiro", day="2026-09-20") is True
    assert check_and_spend("bzzoiro", day="2026-09-20") is True
    # third should fail
    assert check_and_spend("bzzoiro", day="2026-09-20") is False
    assert get_count("bzzoiro", day="2026-09-20") == 2


def test_402_marks_blocked(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    assert is_blocked("bzzoiro", day="2026-09-20") is False
    check_and_spend("bzzoiro", day="2026-09-20")
    record_402("bzzoiro", day="2026-09-20")
    assert is_blocked("bzzoiro", day="2026-09-20") is True
    # further spend should be blocked
    assert check_and_spend("bzzoiro", day="2026-09-20") is False


def test_blocked_persists_in_file(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    check_and_spend("bzzoiro", day="2026-09-20")
    record_402("bzzoiro", day="2026-09-20")
    path = _quota_path("bzzoiro", day="2026-09-20")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["blocked"] is True
    assert data["count"] == 1
    assert "last_402" in data


def test_different_days_isolated(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RACKET_FACTORY_QUOTA_BZZOIRO", "1")
    assert check_and_spend("bzzoiro", day="2026-09-20") is True
    assert check_and_spend("bzzoiro", day="2026-09-20") is False
    # different day should be fresh
    assert check_and_spend("bzzoiro", day="2026-09-21") is True


def test_jina_default_limit(monkeypatch):
    monkeypatch.delenv("RACKET_FACTORY_QUOTA_JINA", raising=False)
    assert get_limit("jina") == 1000


def test_jina_count_only(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RACKET_FACTORY_QUOTA_JINA", "3")
    assert check_and_spend("jina", day="2026-09-20") is True
    assert check_and_spend("jina", day="2026-09-20") is True
    assert check_and_spend("jina", day="2026-09-20") is True
    assert check_and_spend("jina", day="2026-09-20") is False


def test_quota_file_structure(monkeypatch, tmp_path):
    _tmp_quota_env(monkeypatch, tmp_path)
    check_and_spend("bzzoiro", day="2026-09-20")
    path = _quota_path("bzzoiro", day="2026-09-20")
    data = json.loads(path.read_text())
    assert data["date"] == "2026-09-20"
    assert data["service"] == "bzzoiro"
    assert "count" in data
    assert "blocked" in data


def test_spend_logs_do_not_crash(monkeypatch, tmp_path, caplog):
    _tmp_quota_env(monkeypatch, tmp_path)
    import logging
    caplog.set_level(logging.INFO)
    check_and_spend("bzzoiro", day="2026-09-20")
    # should have logged quota line
    assert any("quota bzzoiro" in rec.message for rec in caplog.records)


def test_402_log_includes_spend(monkeypatch, tmp_path, caplog):
    _tmp_quota_env(monkeypatch, tmp_path)
    caplog.set_level(logging.WARNING)
    check_and_spend("bzzoiro", day="2026-09-20")
    record_402("bzzoiro", day="2026-09-20")
    # warning should include spend count and account hint
    assert any("our spend today" in rec.message and "account" in rec.message.lower() for rec in caplog.records)


def test_missing_or_corrupt_authority_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv('RACKET_FACTORY_QUOTA_DIR', str(tmp_path))
    assert not check_and_spend('bzzoiro', day='2026-10-29')
    path = tmp_path / 'bzzoiro_2026-10-29.json'
    path.write_text('{broken')
    assert not check_and_spend('bzzoiro', day='2026-10-29')
    assert path.read_text() == '{broken'


def test_write_failure_fails_closed(monkeypatch, tmp_path):
    from racketfactory import quota_guard as guard
    _tmp_quota_env(monkeypatch, tmp_path)
    def fail(*args, **kwargs):
        raise OSError('no write')
    monkeypatch.setattr(guard, '_save', fail)
    assert not guard.check_and_spend('bzzoiro', day='2026-09-20')
    assert guard.get_count('bzzoiro', day='2026-09-20') == 0


def test_concurrent_reservations_obey_budget(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    _tmp_quota_env(monkeypatch, tmp_path)
    monkeypatch.setenv('RACKET_FACTORY_QUOTA_BZZOIRO', '3')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: check_and_spend('bzzoiro', day='2026-09-20'), range(20)))
    assert sum(results) == 3
    assert get_count('bzzoiro', day='2026-09-20') == 3


def test_localdata_cache_cannot_be_authority(monkeypatch, tmp_path):
    from racketfactory import quota_guard as guard
    monkeypatch.setattr(guard, 'ROOT', tmp_path)
    path = tmp_path / 'localdata' / 'quota'
    path.mkdir(parents=True)
    (path / 'bzzoiro_2026-10-29.json').write_text(json.dumps(
        {'date': '2026-10-29', 'service': 'bzzoiro', 'count': 0, 'blocked': False}))
    monkeypatch.setenv('RACKET_FACTORY_QUOTA_DIR', str(path))
    assert not check_and_spend('bzzoiro', day='2026-10-29')


def test_cooldown_does_not_shorten_and_expires(monkeypatch):
    import time
    from racketfactory.quota_guard import record_cooldown, cooldown_status, _failure_path
    record_cooldown('bzzoiro', 'credential', 'auth', 500)
    before = cooldown_status('bzzoiro', 'credential')
    record_cooldown('bzzoiro', 'credential', 'rate_limit', 1)
    assert cooldown_status('bzzoiro', 'credential') == before
    path = _failure_path('bzzoiro', 'credential')
    path.write_text(json.dumps({'kind': 'auth', 'until': time.time() - 1}))
    assert cooldown_status('bzzoiro', 'credential') is None
    assert 'credential' not in path.read_text()
