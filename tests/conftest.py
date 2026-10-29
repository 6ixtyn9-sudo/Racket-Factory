"""Shared pytest fixtures: bypass the live-fetch disk cache by default.

The same-job fetch cache (racketfactory.fetch_cache) persists successful
site crawls under localdata/fetch_cache. Unit tests must never read or write
that shared state (cross-test contamination + repo pollution), so every test
runs with the cache bypassed unless it opts into an explicit tmp cache dir.
"""
import os
import socket

# Do this at collection time as several adapters read credentials at import.
# Subprocesses inherit empty values rather than the workflow's real secrets.
for _name in ("BZZOIRO_TOKEN", "JINA_API_KEY", "JINA_READER_API_KEY", "THE_ODDS_API_KEY", "THE_ODDS_API_KEYS",
              "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY",
              "CALLMEBOT_APIKEY", "CALLMEBOT_PHONE"):
    os.environ[_name] = ""

import pytest


@pytest.fixture(autouse=True)
def _bypass_fetch_cache(monkeypatch, tmp_path, tmp_path_factory):
    monkeypatch.setenv("RACKET_FACTORY_FETCH_CACHE_TTL_MINUTES", "0")
    quota = tmp_path_factory.mktemp("quota")
    monkeypatch.setenv("RACKET_FACTORY_QUOTA_DIR", str(quota))
    # These are explicit synthetic counters, never the production authority.
    import json
    from datetime import date
    for service in ("jina", "bzzoiro"):
        day = date.today().isoformat()
        (quota / f"{service}_{day}.json").write_text(json.dumps(
            {"date": day, "service": service, "count": 0, "blocked": False}))
    # Some adapters explicitly pass a nonzero TTL, bypassing the env setting.
    # Redirect their default cache too, rather than deleting a real cache.
    from racketfactory import fetch_cache
    monkeypatch.setattr(fetch_cache, "DEFAULT_CACHE_DIR", tmp_path / "fetch_cache")
    from racketfactory.sources import betclan
    monkeypatch.setattr(betclan, "_run_status_path", lambda: tmp_path / "betclan_run_status.json")
    def no_network(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    # curl_cffi uses native sockets and is not stopped by socket.connect.
    import requests
    from curl_cffi import requests as curl_requests
    import urllib.request
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    monkeypatch.setattr(curl_requests.Session, "request", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    from scripts import capture_theoddsapi_scores
    monkeypatch.setattr(capture_theoddsapi_scores, "urlopen", no_network)
    # Same isolation for the odds-compare leg-health snapshot: enrich paths
    # under test must not write the real committed status file.
    monkeypatch.setenv(
        "RACKET_FACTORY_ODDS_COMPARE_STATUS_PATH",
        str(tmp_path / "odds_compare_status.json"),
    )
    # Comparison legs must never hit the live web from unit tests (enrich
    # paths under test call them with fixture dates); adapter tests that
    # need the fetch layer re-enable per-test with monkeypatch.delenv.
    monkeypatch.setenv("RACKET_FACTORY_DISABLE_BETEXPLORER", "1")
    monkeypatch.setenv("RACKET_FACTORY_DISABLE_ODDSPORTAL_UPCOMING", "1")
    yield


@pytest.fixture(scope="session", autouse=True)
def _localdata_is_not_a_scratch_dir():
    """Fail the run if the suite mutated the committed ledger.

    Found the hard way: tests calling auto_tickets_grade.settle_open_slips
    wrote the real localdata/auto_tickets_grade.log and created a real
    auto_tickets_stale_slips.json, because those writes target the module's
    LOCALDATA at call time. A test suite that edits the live betting ledger
    can turn a green run into a silent state change, so the guard is a hard
    failure rather than a cleanup.

    Uses git as the source of truth; skips silently outside a checkout.
    """
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    def dirty() -> set[str]:
        try:
            out = subprocess.run(
                ["git", "status", "--porcelain", "--", "localdata"],
                cwd=root, capture_output=True, text=True, timeout=60)
        except Exception:
            return set()
        if out.returncode != 0:
            return set()
        return {line[3:].strip() for line in out.stdout.splitlines() if line.strip()}

    before = dirty()
    yield
    after = dirty()
    introduced = after - before
    if introduced:
        raise AssertionError(
            "tests mutated the committed ledger under localdata/: "
            + ", ".join(sorted(introduced))
            + " — monkeypatch the module's LOCALDATA onto tmp_path instead."
        )
