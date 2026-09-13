"""Shared pytest fixtures: bypass the live-fetch disk cache by default.

The same-job fetch cache (racketfactory.fetch_cache) persists successful
site crawls under localdata/fetch_cache. Unit tests must never read or write
that shared state (cross-test contamination + repo pollution), so every test
runs with the cache bypassed unless it opts into an explicit tmp cache dir.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _bypass_fetch_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("RACKET_FACTORY_FETCH_CACHE_TTL_MINUTES", "0")
    # Same isolation for the odds-compare leg-health snapshot: enrich paths
    # under test must not write the real committed status file.
    monkeypatch.setenv(
        "RACKET_FACTORY_ODDS_COMPARE_STATUS_PATH",
        str(tmp_path / "odds_compare_status.json"),
    )
    yield
    # Defensive cleanup: no test should create the real cache dir, but if one
    # does (explicit ttl override without tmp dir), remove it afterwards.
    import shutil
    from pathlib import Path

    real_cache = Path(__file__).resolve().parents[1] / "localdata" / "fetch_cache"
    if real_cache.exists():
        shutil.rmtree(real_cache, ignore_errors=True)
