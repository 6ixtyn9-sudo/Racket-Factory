"""Offline subprocess regressions for the guarded rebuild CLI.

The provider adapters are replaced with empty, explicitly test-only fixtures.
The production quote/schedule/settlement validators are NOT patched, and no
synthetic authenticated quote is used. This file covers orchestration, refusal,
publication-race, and retry behavior; it is not provider evidence.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGET_DATE = datetime.now(ZoneInfo("Africa/Johannesburg")).date().isoformat()


_SITE_CUSTOMIZE = r'''import json, os, socket, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _append(path_env, value):
    path = os.environ.get(path_env)
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(value + "\n")

def _deny(*args, **kwargs):
    _append("RF_NETWORK_LOG", "blocked network call")
    raise RuntimeError("offline guarded CLI test blocked network")

socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.create_connection = _deny
try:
    import requests
    requests.sessions.Session.request = _deny
except Exception:
    pass
try:
    from curl_cffi import requests as curl_requests
    curl_requests.Session.request = _deny
except Exception:
    pass

scenario = os.environ.get("RF_GUARDED_SCENARIO", "no_bet")
local = Path(os.environ["RF_GUARDED_LOCALDATA"])
day = os.environ["RF_GUARDED_DATE"]
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources import betexplorer
from racketfactory import quote_enrichment

def _betclan(self):
    _append("RF_GUARDED_PROVIDER_LOG", "betclan")
    self.last_collection_status = "partial" if scenario == "incomplete_betclan" else "complete"
    rows = []
    if scenario == "untrusted_quote":
        rows = [{"match_date": day, "match_time": "23:50", "player_home": "Alice Adams",
                 "player_away": "Beth Brown", "prob_home": 0.75, "prob_away": 0.25,
                 "tournament": "Synthetic fixture only", "surface": "Hard"}]
    self.last_request_stats = {"test_fixture": True, "rows": len(rows)}
    if scenario == "race_card":
        (local / f"auto_tickets_{day}.json").write_text(json.dumps({
            "date": day, "test_fixture": "card inserted during collection"}))
    elif scenario == "race_state":
        state_path = local / "auto_tickets_state.json"
        state = json.loads(state_path.read_text())
        state["bank"] = 99.0
        state_path.write_text(json.dumps(state))
    return rows

BetClanPredictor.fetch_daily = _betclan
PredixSportPredictor.fetch_daily = lambda self: []

def _betexplorer(target, *, with_status=False, metrics=None):
    _append("RF_GUARDED_PROVIDER_LOG", "betexplorer")
    status = "partial" if scenario == "incomplete_betexplorer" else "complete"
    rows = []
    if scenario == "untrusted_quote":
        rows = [{"event_id": "test-event-01",
                 "match_url": "/tennis/atp/test/alice-adams-beth-brown/test-event-01/",
                 "match_date": day, "match_time": "23:50", "listing_date": day,
                 "listing_schedule_status": "unverified", "player_home": "Alice Adams",
                 "player_away": "Beth Brown", "odds_home": None, "odds_away": None}]
    if metrics is not None:
        metrics.update({"test_fixture": True, "data_dt_rows": 0,
                        "listing_coverage_status": "not_established"})
    return (rows, status) if with_status else rows

betexplorer._fetch_live = _betexplorer
# Empty-source cases use no quotes. One separate case supplies a deliberately
# unauthenticated fixture quote to assert the real term/schedule validators
# reject it; no test-only validator bypass or manufactured terms are installed.
def _comparisons(*args, **kwargs):
    if scenario != "untrusted_quote":
        return [], {"test_fixture": True}, False
    observed = datetime.now(timezone.utc).isoformat()
    quote = {"event_id": "test-event-01",
             "match_url": "/tennis/atp/test/alice-adams-beth-brown/test-event-01/",
             "match_date": day, "match_time": "23:50", "commence_time": day + "T23:50:00+02:00",
             "player_home": "Alice Adams", "player_away": "Beth Brown",
             "odds_home": 1.55, "odds_away": 2.60, "source": "BetExplorer",
             "bookmaker": "Synthetic fixture label", "bookmaker_id": "test-book",
             "price_type": "bookmaker", "market": "h2h", "period": "match", "line": None,
             "observed_at": observed, "retrieved_at": observed, "test_fixture": True}
    return [quote], {"test_fixture": True}, False

quote_enrichment.collect_prediction_comparisons = _comparisons

_real_run = subprocess.run
def _run(command, *args, **kwargs):
    if isinstance(command, (list, tuple)) and any(str(part).endswith("scripts/mine_edges.py") for part in command):
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True, exist_ok=True)
        target = command[command.index("--date") + 1]
        (output / f"picks_{target}.json").write_text("[]", encoding="utf-8")
        _append("RF_GUARDED_MINER_LOG", "empty fixture output")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return _real_run(command, *args, **kwargs)

subprocess.run = _run
'''


@pytest.fixture(scope="session")
def guarded_cli_template(tmp_path_factory):
    """Copy only runtime inputs; each subprocess gets its own isolated checkout."""
    template = tmp_path_factory.mktemp("guarded-cli-template")
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".mypy_cache")
    shutil.copytree(ROOT / "scripts", template / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "src", template / "src", ignore=ignore)
    shutil.copytree(ROOT / "config", template / "config", ignore=ignore)
    (template / "requirements.txt").write_bytes((ROOT / "requirements.txt").read_bytes())
    injection = template / "test-injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(_SITE_CUSTOMIZE, encoding="utf-8")
    return template


def _state(*, open_slips=None, history=None, events=None):
    return {"bank": 100.0, "paper_bank": 100.0,
            "open_slips": list(open_slips or []),
            "history": list(history or []), "events": list(events or [])}


def _make_case(tmp_path, template, *, scenario="no_bet", run_id="8700001",
               state=None, protected_card=None):
    checkout = tmp_path / "checkout"
    shutil.copytree(template, checkout, dirs_exist_ok=True)
    local = checkout / "localdata"
    local.mkdir()
    (local / "auto_tickets_state.json").write_text(
        json.dumps(state if state is not None else _state(), indent=2), encoding="utf-8")
    if protected_card is not None:
        (local / f"auto_tickets_{TARGET_DATE}.json").write_text(
            json.dumps(protected_card, indent=2), encoding="utf-8")
    # A tiny, non-financial historical warehouse required by the real staging
    # path. It is intentionally empty so no archived price can enter the build.
    with gzip.open(local / "warehouse.csv.gz", "wt", encoding="utf-8", newline="") as stream:
        stream.write("match_date,player_a,player_b,winner,_is_live,odds_a,odds_b\n")

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    provider_log = tmp_path / "provider.log"
    network_log = tmp_path / "network.log"
    env = os.environ.copy()
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY",
                "CALLMEBOT_APIKEY", "CALLMEBOT_PHONE", "THE_ODDS_API_KEY",
                "THE_ODDS_API_KEYS", "BZZOIRO_TOKEN", "JINA_API_KEY",
                "JINA_READER_API_KEY"):
        env[key] = ""
    env.update({
        "PYTHONPATH": os.pathsep.join((str(checkout / "test-injection"),
                                      str(checkout / "src"), str(checkout))),
        "RACKET_FACTORY_REBUILD_SERIALIZED": "1",
        "RACKET_FACTORY_DISABLE_FOREBET": "1",
        "RACKET_FACTORY_DISABLE_BETEXPLORER": "",
        "RACKET_FACTORY_DISABLE_ODDSPORTAL_UPCOMING": "1",
        "RACKET_FACTORY_QUOTA_DIR": str(tmp_path / "quota"),
        "RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR": str(diagnostics),
        "GITHUB_RUN_ID": run_id,
        "GITHUB_RUN_ATTEMPT": "1",
        "RF_GUARDED_SCENARIO": scenario,
        "RF_GUARDED_DATE": TARGET_DATE,
        "RF_GUARDED_LOCALDATA": str(local),
        "RF_GUARDED_PROVIDER_LOG": str(provider_log),
        "RF_GUARDED_NETWORK_LOG": str(network_log),
    })
    result = subprocess.run(
        [sys.executable, str(checkout / "scripts" / "daily.py"),
         "--date", TARGET_DATE, "--guarded-rebuild"],
        cwd=checkout, env=env, text=True, capture_output=True, timeout=90,
    )
    return checkout, local, diagnostics, provider_log, network_log, env, result


def _provider_calls(path):
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_guarded_cli_subprocess_complete_sources_publish_no_bet_without_state_change(tmp_path, guarded_cli_template):
    _, local, diagnostics, provider_log, network_log, _, result = _make_case(tmp_path, guarded_cli_template)
    assert result.returncode == 0, result.stderr
    card = json.loads((local / f"auto_tickets_{TARGET_DATE}.json").read_text())
    assert card["status"] == "NO BET" and card["accas"] == []
    state_path = local / "auto_tickets_state.json"
    state = json.loads(state_path.read_text())
    assert state["bank"] == 100.0 and state["paper_bank"] == 100.0
    assert state["open_slips"] == [] and state["history"] == [] and state["events"] == []
    status = json.loads((diagnostics / "rebuild_status.json").read_text())
    assert status["predecessor_card_existed"] is False
    assert status["source_outcomes"]["betclan"]["status"] == "complete"
    assert status["source_outcomes"]["betexplorer"]["status"] == "complete"
    assert _provider_calls(provider_log) == ["betclan", "betexplorer"]
    assert not network_log.exists()


def test_guarded_cli_subprocess_rejects_quote_without_applicable_terms(tmp_path, guarded_cli_template):
    _, local, diagnostics, _, network_log, _, result = _make_case(
        tmp_path, guarded_cli_template, scenario="untrusted_quote")
    assert result.returncode != 0
    assert "collection has no market-identified fresh quotes" in result.stderr
    assert not (local / f"auto_tickets_{TARGET_DATE}.json").exists()
    status = json.loads((diagnostics / "rebuild_status.json").read_text())
    rejection_reasons = status["quote_stages"]["sources"]["betexplorer"]["rejection_reasons"]
    assert any("settlement" in reason or "schedule" in reason for reason in rejection_reasons)
    assert not network_log.exists()


@pytest.mark.parametrize("exposure, message", [
    ([{"date": TARGET_DATE, "accas": [], "staked_pct": 0.0}], "target-day open_slips[0] activity exists"),
    ([{"date": TARGET_DATE + "T00:00:00", "accas": [], "staked_pct": 0.0}], "record date ambiguous"),
])
def test_guarded_cli_subprocess_refuses_existing_or_ambiguous_exposure_before_collection(
        tmp_path, guarded_cli_template, exposure, message):
    state = _state(open_slips=exposure)
    _, local, _, provider_log, _, _, result = _make_case(
        tmp_path, guarded_cli_template, state=state)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (local / f"auto_tickets_{TARGET_DATE}.json").exists()
    assert _provider_calls(provider_log) == []


def test_guarded_cli_subprocess_protects_existing_nonempty_card(tmp_path, guarded_cli_template):
    protected = {"date": TARGET_DATE, "status": "CURRENT",
                 "accas": [{"legs": [{"selected_player": "Test Only"}]}],
                 "staked_pct": 1.0, "paper_staked_pct": 0.0,
                 "test_fixture": True}
    _, local, _, provider_log, _, _, result = _make_case(
        tmp_path, guarded_cli_template, protected_card=protected)
    card = local / f"auto_tickets_{TARGET_DATE}.json"
    assert result.returncode != 0
    assert "nonempty card lacks authoritative unplaced status" in result.stderr
    assert json.loads(card.read_text()) == protected
    assert _provider_calls(provider_log) == []


@pytest.mark.parametrize("scenario, source", [
    ("incomplete_betclan", "betclan"),
    ("incomplete_betexplorer", "betexplorer"),
])
def test_guarded_cli_subprocess_refuses_incomplete_required_source(
        tmp_path, guarded_cli_template, scenario, source):
    _, local, diagnostics, _, network_log, _, result = _make_case(
        tmp_path, guarded_cli_template, scenario=scenario)
    assert result.returncode != 0
    assert "required source collection incomplete" in result.stderr
    assert not (local / f"auto_tickets_{TARGET_DATE}.json").exists()
    status = json.loads((diagnostics / "rebuild_status.json").read_text())
    assert status["source_outcomes"][source]["status"] == "partial"
    assert not network_log.exists()


@pytest.mark.parametrize("scenario, changed_path", [
    ("race_card", f"auto_tickets_{TARGET_DATE}.json"),
    ("race_state", "auto_tickets_state.json"),
])
def test_guarded_cli_subprocess_rejects_card_or_state_race(
        tmp_path, guarded_cli_template, scenario, changed_path):
    _, local, _, _, network_log, _, result = _make_case(
        tmp_path, guarded_cli_template, scenario=scenario)
    assert result.returncode != 0
    assert "changed during staging" in result.stderr
    assert changed_path in result.stderr
    assert not network_log.exists()
    if scenario == "race_card":
        assert json.loads((local / changed_path).read_text())["test_fixture"] == "card inserted during collection"
    else:
        assert json.loads((local / changed_path).read_text())["bank"] == 99.0
        assert not (local / f"auto_tickets_{TARGET_DATE}.json").exists()


def test_guarded_cli_subprocess_interrupted_retry_is_idempotently_refused(tmp_path, guarded_cli_template):
    run_id = "8700099"
    _, local, _, provider_log, _, env, first = _make_case(
        tmp_path, guarded_cli_template, run_id=run_id)
    assert first.returncode == 0, first.stderr
    card = local / f"auto_tickets_{TARGET_DATE}.json"
    state = local / "auto_tickets_state.json"
    before = card.read_bytes(), state.read_bytes()
    calls_before = _provider_calls(provider_log)
    assert calls_before == ["betclan", "betexplorer"]
    archive = list((local / ".auto_tickets_rebuild").glob("archive_*.json"))
    assert len(archive) == 1

    retry_env = dict(env)
    retry_env["GITHUB_RUN_ATTEMPT"] = "2"
    retry_env["RF_GUARDED_SCENARIO"] = "no_bet"
    retry = subprocess.run(
        [sys.executable, str(local.parent / "scripts" / "daily.py"),
         "--date", TARGET_DATE, "--guarded-rebuild"],
        cwd=local.parent, env=retry_env, text=True, capture_output=True, timeout=90,
    )
    assert retry.returncode != 0
    assert "uncommitted rebuild archive exists; refusing retry" in retry.stderr
    assert (card.read_bytes(), state.read_bytes()) == before
    assert _provider_calls(provider_log) == calls_before
