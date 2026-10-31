"""Offline, temporary-state tests for run-bounded targeted collection failures."""
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from racketfactory import fetch_cache, quote_enrichment as qe, quota_guard
from racketfactory.sources import _page_odds as po, betclan, betexplorer, oddsportal_upcoming

DAY = '2026-10-30'
RUN = DAY + 'T10:00:00+02:00'
PREDICTIONS = [{'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown'}]


@pytest.fixture
def setup_targeted(monkeypatch, tmp_path):
    (tmp_path / 'localdata').mkdir()
    monkeypatch.setattr(qe, '__file__', str(tmp_path / 'src/racketfactory/quote_enrichment.py'))
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', RUN)
    monkeypatch.delenv(betexplorer.DISABLE_ENV, raising=False)
    monkeypatch.setattr(betclan, 'current_run_status', lambda _: 'complete')
    monkeypatch.setattr(oddsportal_upcoming, 'fetch_oddsportal_upcoming_rows', lambda _: [])
    return tmp_path


def status(root):
    return json.loads((root / 'localdata' / f'quote_collection_{DAY}.json').read_text())


def keys(run=RUN):
    key = hashlib.sha256(json.dumps([run, DAY]).encode()).hexdigest()[:24]
    return ('targeted_quotes_' + key, 'targeted_attempt_' + key)


def test_429_never_populates_positive_cache_or_retries_same_run(setup_targeted, monkeypatch):
    root = setup_targeted
    calls = []
    monkeypatch.setattr(po, '_run_order', lambda *a, **kw: calls.append('http') or ('', 429, 900))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    first = status(root)
    assert first['status'] == 'failed' and first['failure_kind'] == 'rate_limit'
    assert first['cooldown_state'] == 'active_in_current_runner'
    positive, attempt = keys()
    assert not fetch_cache.cache_path(positive, DAY).exists()
    marker = json.loads(fetch_cache.cache_path(attempt, DAY).read_text())
    assert marker['state'] == 'failed' and marker['failure_kind'] == 'rate_limit'
    assert 'offers' not in marker and 'cooldown_until' not in marker
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert len(calls) == 1 and status(root)['attempt_guard'] == 'previous_attempt'
    assert status(root)['failure_kind'] == 'rate_limit'
    # A different run on this runner respects its active host marker.
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', DAY + 'T10:01:00+02:00')
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert len(calls) == 1 and status(root)['failure_kind'] == 'rate_limit'
    # A provider cooldown expiry never triggers same-run recollection.
    quota_guard._failure_path('betexplorer', 'host:www.betexplorer.com').write_text(
        json.dumps({'kind': 'rate_limit', 'until': 0}))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert len(calls) == 1 and status(root)['cooldown_state'] == 'not_established_in_current_runner'
    # A distinct run after expiry may perform its one bounded collection.
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', DAY + 'T10:02:00+02:00')
    html = ('<table><tr data-dt="30,10,2026,11,0"><td><a href="/tennis/atp/test/a-b/abc/">'
            '<span>Adams A.</span><span>Brown B.</span></a></td></tr></table>')
    monkeypatch.setattr(po, '_run_order', lambda *a, **kw: calls.append('http') or (html, 200, 0))
    detail_calls = []
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison',
                        lambda fixture, **kwargs: detail_calls.append(fixture['event_id']) or [])
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert len(calls) == 2 and detail_calls == ['abc'] and status(root)['status'] == 'partial'
    assert status(root)['counts'].get('attempted', 0) == 1
    assert status(root)['counts']['stage_statuses']['quote_resolution'] == 'failed'
    assert status(root)['counts'].get('listing_status') == 'partial'


@pytest.mark.parametrize('kind', ['transport', 'parser', 'challenge'])
def test_failed_collection_is_explicit_and_only_one_attempt(setup_targeted, monkeypatch, kind):
    root = setup_targeted
    calls = []
    def fetch(*a, **kw):
        calls.append(1)
        raise po.PageFetchError(kind, 'synthetic')
    monkeypatch.setattr(betexplorer, '_fetch_live', fetch)
    for _ in range(2):
        assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
        assert status(root)['status'] == 'failed' and status(root)['failure_kind'] == kind
    assert calls == [1]
    assert not fetch_cache.cache_path(keys()[0], DAY).exists()


def test_changed_predictions_after_failure_do_not_recrawl_or_become_complete(setup_targeted, monkeypatch):
    calls = []
    def fetch(*a, **kw):
        calls.append(1)
        raise po.PageFetchError('transport', 'synthetic')
    monkeypatch.setattr(betexplorer, '_fetch_live', fetch)
    qe.targeted_comparison_rows(DAY, PREDICTIONS)
    qe.targeted_comparison_rows(DAY, [dict(PREDICTIONS[0], player_home='Carla Clark')])
    result = status(setup_targeted)
    # A changed set is reported without disguising a provider failure as partial.
    assert result['status'] == 'failed' and result['fixture_set_changed'] is True
    assert len(calls) == 1


def test_legacy_cached_failure_is_not_promoted_or_recrawled(setup_targeted, monkeypatch):
    fingerprint = hashlib.sha256(json.dumps([(DAY, 'Alice Adams', 'Beth Brown')]).encode()).hexdigest()
    fetch_cache.write_cached_rows(keys()[0], [{'status': 'failed', 'failure_kind': 'rate_limit',
        'offers': [], 'prediction_fingerprint': fingerprint}], DAY)
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: pytest.fail('unexpected collection'))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['status'] == 'failed'
    assert status(setup_targeted)['failure_kind'] == 'rate_limit'


def test_success_is_reused_and_missing_result_never_triggers_recrawl(setup_targeted, monkeypatch):
    calls = []
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: (calls.append(1) or [], 'complete'))
    qe.targeted_comparison_rows(DAY, PREDICTIONS)
    assert status(setup_targeted)['status'] == 'partial'
    assert fetch_cache.cache_path(keys()[0], DAY).exists()
    assert json.loads(fetch_cache.cache_path(keys()[1], DAY).read_text())['state'] == 'completed'
    qe.targeted_comparison_rows(DAY, PREDICTIONS)
    assert calls == [1]
    fetch_cache.cache_path(keys()[0], DAY).unlink()
    qe.targeted_comparison_rows(DAY, PREDICTIONS)
    assert calls == [1] and status(setup_targeted)['status'] == 'failed'


def test_corrupt_attempt_marker_fails_closed_without_network(setup_targeted, monkeypatch):
    path = fetch_cache.cache_path(keys()[1], DAY)
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: pytest.fail('unexpected collection'))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['failure_kind'] == 'attempt_guard_unavailable'


def test_unwritable_attempt_marker_fails_before_provider_access(setup_targeted, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic')))
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: pytest.fail('unexpected collection'))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['failure_kind'] == 'attempt_guard_unavailable'


def test_same_run_concurrent_builds_do_not_duplicate_collection(setup_targeted, monkeypatch):
    calls = []
    entered, release = threading.Event(), threading.Event()
    def fetch(*a, **kw):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise po.PageFetchError('transport', 'synthetic')
    monkeypatch.setattr(betexplorer, '_fetch_live', fetch)
    errors = []
    def run():
        try:
            assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
        except BaseException as exc:
            errors.append(exc)
    a = threading.Thread(target=run)
    b = threading.Thread(target=run)
    a.start()
    assert entered.wait(5)
    b.start()
    release.set()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive() and not errors
    assert calls == [1]


def test_proven_empty_synthetic_success_is_still_cached(setup_targeted, monkeypatch):
    calls = []
    def fetch_empty(*a, **kw):
        calls.append(1)
        metrics = kw.get('metrics')
        if isinstance(metrics, dict):
            metrics.update({"data_dt_rows": 0, "row_accounting_total": 0,
                            "row_accounting_complete": True, "listing_recognition_status": "complete",
                            "listing_coverage_status": "complete",
                            "schedule_verification_status": "not_required_for_excluded_rows",
                            "actionable_schedule_unverified_rows": 0})
        return [], 'complete'
    monkeypatch.setattr(betexplorer, '_fetch_live', fetch_empty)
    assert qe.targeted_comparison_rows(DAY, []) == []
    assert status(setup_targeted)['status'] == 'partial'  # BetClan readiness is unknown in this fixture
    assert fetch_cache.cache_path(keys()[0], DAY).exists()
    assert qe.targeted_comparison_rows(DAY, []) == []
    assert calls == [1] and status(setup_targeted)['status'] == 'partial'


def test_unavailable_positive_cache_after_success_never_causes_recollection(setup_targeted, monkeypatch):
    calls = []
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: (calls.append(1) or [], 'complete'))
    # Model the shared writer's best-effort write failing without raising.
    monkeypatch.setattr(fetch_cache, 'write_cached_rows', lambda *a, **kw: None)
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['status'] == 'partial' and calls == [1]
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert calls == [1] and status(setup_targeted)['failure_kind'] == 'prior_result_unavailable'


def test_inflight_marker_after_crash_prevents_provider_request(setup_targeted, monkeypatch):
    _, attempt = keys()
    path = fetch_cache.cache_path(attempt, DAY)
    path.parent.mkdir(parents=True)
    fingerprint = hashlib.sha256(json.dumps([(DAY, 'Alice Adams', 'Beth Brown')]).encode()).hexdigest()
    key = attempt.removeprefix('targeted_attempt_')
    path.write_text(json.dumps({'version': 1, 'key': key, 'state': 'started', 'prediction_fingerprint': fingerprint}))
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: pytest.fail('unexpected collection'))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['status'] == 'failed'
    assert status(setup_targeted)['failure_kind'] == 'attempt_incomplete'


def test_malformed_legacy_cache_fails_closed_without_new_collection(setup_targeted, monkeypatch):
    fetch_cache.write_cached_rows(keys()[0], [{'status': 'complete', 'offers': 'not a list'}], DAY)
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: pytest.fail('unexpected collection'))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['status'] == 'failed'


def test_same_timestamp_with_new_workflow_id_fails_closed_without_recrawl(setup_targeted, monkeypatch):
    calls = []
    monkeypatch.setenv('GITHUB_RUN_ID', '100')
    def fetch(*a, **kw):
        calls.append(1)
        raise po.PageFetchError('transport', 'synthetic')
    monkeypatch.setattr(betexplorer, '_fetch_live', fetch)
    qe.targeted_comparison_rows(DAY, PREDICTIONS)
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    # Existing cache keys use run_as_of and date, not the GitHub run ID. A
    # timestamp collision must never imply a new budget or a proven reset.
    qe.targeted_comparison_rows(DAY, PREDICTIONS)
    assert calls == [1] and status(setup_targeted)['status'] == 'failed'


def _proven_cache_payload(observed=None):
    observed = observed or datetime.now(timezone.utc).isoformat()
    from tests._synthetic_evidence import attach_synthetic_schedule_evidence, attach_synthetic_settlement_evidence
    offer = {"match_date": DAY, "player_home": "Alice Adams", "player_away": "Beth Brown",
             "event_id": "fixture-1", "match_url": "/tennis/atp/test/alice-beth/fixture-1/",
             "commence_time": DAY + "T12:00:00+02:00", "odds_home": 1.55, "odds_away": 2.40,
             "bookmaker": "Synthetic Book", "bookmaker_id": "synthetic-book-id",
             "source": "BetExplorer", "market": "match_winner", "period": "match",
             "price_type": "bookmaker", "line": None, "observed_at": observed,
             "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}}
    attach_synthetic_settlement_evidence(offer)
    attach_synthetic_schedule_evidence(offer, observed_at=observed)
    stages = {"required_source_readiness": "ready", "listing_recognition": "complete",
              "listing_row_accounting": "complete", "listing_coverage": "complete",
              "listing_schedule_verification": "verified", "identity_matching": "complete",
              "schedule_verification": "complete", "detail_discovery": "complete",
              "quote_resolution": "complete", "unexamined_fixtures": "none"}
    return [{"status": "complete", "listing_rows": 1, "offers": [offer],
             "prediction_fingerprint": hashlib.sha256(json.dumps([(DAY, 'Alice Adams', 'Beth Brown')]).encode()).hexdigest(),
             "counts": {"stage_statuses": stages, "readiness_blockers": []}}]


def test_cached_complete_quote_survives_only_when_all_evidence_is_revalidated(setup_targeted, monkeypatch):
    from tests._synthetic_evidence import allow_synthetic_evidence_in_pipeline
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    positive, _ = keys()
    observed = datetime.now(timezone.utc).isoformat()
    fetch_cache.write_cached_rows(positive, _proven_cache_payload(observed), DAY)
    monkeypatch.setattr(betexplorer, "_fetch_live", lambda *a, **k: pytest.fail("cache reuse must not recollect"))
    rows = qe.targeted_comparison_rows(DAY, PREDICTIONS)
    result = status(setup_targeted)
    assert len(rows) == 1 and rows[0]["observed_at"] == observed
    assert rows[0]["settlement_evidence"]["test_only"] is True
    assert rows[0]["schedule_evidence"]["test_only"] is True
    assert result["status"] == "complete" and result["prediction_set_status"] == "unchanged"
    assert rows[0]["observed_at"] == observed  # original time is retained, never refreshed


@pytest.mark.parametrize("fault", ["stale", "incomplete_readiness", "changed_prediction_set"])
def test_cached_quote_without_fresh_complete_matching_proof_stays_blocked(setup_targeted, monkeypatch, fault):
    from tests._synthetic_evidence import allow_synthetic_evidence_in_pipeline
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    positive, _ = keys()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    payload = _proven_cache_payload(observed=stale if fault == "stale" else datetime.now(timezone.utc).isoformat())
    if fault == "incomplete_readiness":
        payload[0]["counts"]["stage_statuses"]["listing_coverage"] = "not_established"
        payload[0]["status"] = "partial"
    if fault == "changed_prediction_set":
        payload[0]["prediction_fingerprint"] = "0" * 64
    fetch_cache.write_cached_rows(positive, payload, DAY)
    monkeypatch.setattr(betexplorer, "_fetch_live", lambda *a, **k: pytest.fail("blocked cache must not recollect"))
    rows = qe.targeted_comparison_rows(DAY, PREDICTIONS)
    result = status(setup_targeted)
    if fault == "incomplete_readiness":
        # Listing-wide incompleteness does not invalidate this fresh,
        # prediction-matched fixture's own supported schedule and quote.
        assert len(rows) == 1 and result["status"] == "partial"
        assert result.get("schedule_guard_reason") is None
    else:
        assert rows == [] and result["status"] == "partial"
        assert result["schedule_guard_reason"] == "cached_schedule_evidence_unavailable"


def test_429_cooldown_write_failure_never_claims_active_marker_or_retries(setup_targeted, monkeypatch):
    calls = []
    monkeypatch.setattr(po, '_run_order', lambda *a, **kw: calls.append(1) or ('', 429, 900))
    monkeypatch.setattr(quota_guard, 'record_cooldown', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic')))
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert status(setup_targeted)['failure_kind'] == 'rate_limit'
    assert status(setup_targeted)['cooldown_state'] == 'not_established_in_current_runner'
    assert qe.targeted_comparison_rows(DAY, PREDICTIONS) == []
    assert calls == [1]


def test_oddsportal_empty_scan_is_attempt_cached_across_repeated_consumers(tmp_path, monkeypatch):
    monkeypatch.delenv(oddsportal_upcoming.DISABLE_ENV, raising=False)
    monkeypatch.delenv('RACKET_FACTORY_DISABLE_ODDSPORTAL', raising=False)
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', RUN)
    calls = []
    monkeypatch.setattr(fetch_cache, 'DEFAULT_CACHE_DIR', tmp_path / 'fetch_cache')
    page_calls = []
    monkeypatch.setattr(oddsportal_upcoming, '_page_for_target',
                        lambda _: page_calls.append(1) or ('/tennis/', 'unused-key'))
    monkeypatch.setattr(oddsportal_upcoming, '_fetch_live',
                        lambda *args, **kwargs: calls.append(1) or [])

    assert oddsportal_upcoming.fetch_oddsportal_upcoming_rows(DAY) == []
    assert oddsportal_upcoming.fetch_oddsportal_upcoming_rows(DAY) == []
    assert page_calls == [1, 1]
    assert calls == [1]


def test_oddsportal_invalid_route_is_reported_without_repeating_scan(tmp_path, monkeypatch):
    monkeypatch.delenv(oddsportal_upcoming.DISABLE_ENV, raising=False)
    monkeypatch.delenv('RACKET_FACTORY_DISABLE_ODDSPORTAL', raising=False)
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', RUN)
    monkeypatch.setattr(fetch_cache, 'DEFAULT_CACHE_DIR', tmp_path / 'fetch_cache')
    monkeypatch.setattr(oddsportal_upcoming, '_page_for_target', lambda _: ('/tennis/', 'unused-key'))
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise po.PageFetchError('route_invalid', 'synthetic 404')
    monkeypatch.setattr(oddsportal_upcoming, '_fetch_live', fail)

    for _ in range(2):
        with pytest.raises(po.PageFetchError) as exc:
            oddsportal_upcoming.fetch_oddsportal_upcoming_rows(DAY)
        assert exc.value.kind == 'route_invalid'
    assert calls == [1]


def test_oddsportal_explicit_disable_switches_short_circuit_upcoming_scan(monkeypatch):
    monkeypatch.delenv(oddsportal_upcoming.DISABLE_ENV, raising=False)
    monkeypatch.setenv('RACKET_FACTORY_DISABLE_ODDSPORTAL', '1')
    monkeypatch.setattr(oddsportal_upcoming, '_fetch_live',
                        lambda *args, **kwargs: pytest.fail('disabled source must not scan'))
    assert oddsportal_upcoming.fetch_oddsportal_upcoming_rows(DAY) == []
