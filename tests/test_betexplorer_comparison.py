"""Synthetic tennis markup only; not evidence of BetExplorer's live contract."""
import json
from datetime import datetime, timezone

import pytest

from racketfactory.sources import betexplorer as be
from racketfactory.sources._page_odds import PageFetchError
from racketfactory.execution import rebuild_quote_execution_problem

DAY = "2026-10-29"
OBS = "2026-10-29T10:00:00+00:00"
FIXTURE = {"event_id": "a1B2", "match_url": "/tennis/atp/test/alice-beth/a1B2/",
           "match_date": DAY, "match_time": "14:00", "player_home": "Alice Adams", "player_away": "Beth Brown"}
CONTEXT = {"raw_market": "match winner", "raw_period": "match", "market_id": "T"}
PAGE = '<div data-url="/match-odds/a1B2/T?lang=en" data-market="Match Winner" data-period="match" data-market-id="T"></div>'
HTML = '''<table><thead><tr><th>Bookmaker</th><th data-pos="b">Beth Brown</th><th data-pos="a">Alice Adams</th></tr></thead><tbody>
<tr data-bookie="4"><td><img title="Book One"></td><td data-odd="2.50" data-created="100"></td><td data-odd="1.50" data-created="101"></td></tr>
<tr class="average"><td>Average</td><td data-odd="2.40"></td><td data-odd="1.45"></td></tr>
<tr><td></td><td data-odd="2.30"></td><td data-odd="1.55"></td></tr>
<tr><td><img title="Bad"></td><td data-odd="NaN"></td><td data-odd="1.55"></td></tr>
</tbody></table>
<div class="oddsComparisonAll__rowBookie" data-bookie="4"><img title="Book One"><span data-pos="a" data-odd="1.50"></span><span data-pos="b" data-odd="2.50"></span></div>
<div class="oddsComparisonAll__rowBookie" data-bookie="5"><img title="Book Two"><span data-pos="b" data-odd="3.00"></span><span data-pos="a" data-odd="1.40"></span></div>'''


def test_endpoint_requires_literal_same_event_host_market_and_scope():
    assert len(be._comparison_endpoint_candidates(PAGE, "a1B2")) == 1
    for changed in (PAGE.replace('Match Winner', 'Set Winner'), PAGE.replace('data-period="match"', 'data-period="set1"'),
                    PAGE.replace('/match-odds/a1B2/', '/match-odds/other/'),
                    PAGE.replace('/match-odds/', 'https://evil.test/match-odds/'),
                    PAGE.replace('?lang=en', '?token=secret')):
        assert not be._comparison_endpoint_candidates(changed, "a1B2")
    assert not be._comparison_endpoint_candidates(PAGE + PAGE.replace('/T?', '/X?'), "a1B2")


def test_comparison_reversed_columns_hidden_dedupe_bad_and_unknown_rules():
    rows, metrics = be._comparison_response_rows(json.dumps({"odds": HTML}), FIXTURE, CONTEXT, OBS)
    assert [(r["bookmaker"], r["odds_home"], r["odds_away"]) for r in rows] == [("Book One", 1.5, 2.5), ("Book Two", 1.4, 3.0)]
    assert metrics["average_rows"] == metrics["duplicate_rows"] == metrics["missing_bookmaker"] == metrics["invalid_price"] == 1
    assert metrics["average_prices"] == [[1.45, 2.4]]
    assert rows[0]["provider_price_updated_at"] == ["101", "100"]
    assert rows[0]["observed_at"] == OBS and len(rows[0]["response_sha256"]) == 64
    assert rebuild_quote_execution_problem(rows[0]) == "bookmaker settlement rules evidence missing"


def test_bad_columns_and_doubles_do_not_assume_order():
    rows, stats = be._comparison_response_rows(json.dumps({"odds": HTML.replace('Beth Brown', '1').replace('Alice Adams', '2')}), FIXTURE, CONTEXT, OBS)
    assert not rows and stats["ambiguous_columns"] == 1
    doubles = dict(FIXTURE, player_home="Alice Adams / Cara Cook", player_away="Beth Brown / Dora Davis")
    html = HTML.replace('Alice Adams', doubles['player_home']).replace('Beth Brown', doubles['player_away'])
    rows, _ = be._comparison_response_rows(json.dumps({"odds": html}), doubles, CONTEXT, OBS)
    assert len(rows) == 2 and rows[0]["odds_home"] == 1.5
    with pytest.raises(PageFetchError):
        be._comparison_response_rows(json.dumps({"odds": HTML}), dict(FIXTURE, player_away="Alice Adams"), CONTEXT, OBS)


def test_fetch_isolated_cache_preserves_original_observation_and_diagnostics(tmp_path, monkeypatch):
    from racketfactory import fetch_cache
    monkeypatch.setattr(fetch_cache, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR", str(tmp_path / "diag"))
    calls = []
    def fetch(url, referer=None):
        calls.append((url, referer))
        return (json.dumps({"odds": HTML}) if referer else PAGE, OBS)
    monkeypatch.setattr(be, "_fetch_comparison_document", fetch)
    first = be.fetch_fixture_comparison(FIXTURE)
    second = be.fetch_fixture_comparison(FIXTURE)
    assert len(calls) == 2 and second == first and second[0]["observed_at"] == OBS
    evidence = json.loads((tmp_path / "diag/betexplorer_fragments.json").read_text())['fragments']
    assert [(x['kind'], x['http_status'], x['retrieved_at']) for x in evidence] == [('detail', 200, OBS), ('comparison', 200, OBS)]
    summary = (tmp_path / "diag/betexplorer_comparison_summary.json").read_text()
    assert 'Book One' not in summary and '1.5' in summary
    with pytest.raises(PageFetchError):
        be.fetch_fixture_comparison(dict(FIXTURE, player_home="Wrong Opponent"))


def test_detail_failure_does_not_reuse_previous_fixture(tmp_path, monkeypatch):
    from racketfactory import fetch_cache
    monkeypatch.setattr(fetch_cache, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR", str(tmp_path / "diag"))
    monkeypatch.setattr(be, "_fetch_comparison_document", lambda url, referer=None: (json.dumps({"odds": HTML}) if referer else PAGE, OBS))
    assert be.fetch_fixture_comparison(FIXTURE)
    other = dict(FIXTURE, event_id="other", match_url="/tennis/atp/test/alice-beth/other/")
    with pytest.raises(PageFetchError, match="endpoint"):
        be.fetch_fixture_comparison(other)
    assert json.loads((tmp_path / "diag/betexplorer_detail_failures.json").read_text())[0]["event_id"] == "other"


def test_discovery_retains_unpriced_fixture():
    html = '''<table><tr data-dt="29,10,2026,14,00"><td><a href="/tennis/atp/test/alice-beth/a1B2/">Alice Adams - Beth Brown</a></td><td>-</td><td>-</td></tr></table>'''
    rows = be.parse_results_page(html, DAY, include_unpriced=True)
    assert len(rows) == 1 and rows[0]["odds_home"] is None
    assert be._finalize(rows)[0]["match_url"] == FIXTURE["match_url"]


def test_prediction_pool_targets_unpriced_deduplicates_and_optional_failure(monkeypatch):
    from racketfactory.sources import betclan, predixsport, oddsportal_upcoming
    from scripts import daily
    from racketfactory import quote_enrichment
    from racketfactory.kickoff import SAST
    monkeypatch.setattr(quote_enrichment, "_collection_now", lambda: datetime(2026, 10, 29, 10, 0, tzinfo=SAST))
    discovery = dict(FIXTURE, source="BetExplorer", odds_home=None, odds_away=None)
    other = dict(discovery, event_id="other", match_url="/tennis/atp/test/other/other/", player_home="Carl Clark", player_away="Dora Dale")
    class Clan:
        last_collection_status = "complete"
        def fetch_daily(self): return [dict(discovery, player_home="Beth Brown", player_away="Alice Adams")]
    class Predix:
        def fetch_daily(self): return [dict(discovery)]
    monkeypatch.delenv(be.DISABLE_ENV, raising=False)
    monkeypatch.setattr(betclan, "BetClanPredictor", Clan)
    monkeypatch.setattr(predixsport, "PredixSportPredictor", Predix)
    monkeypatch.setattr(be, "_fetch_live", lambda *a, **kw: ([discovery, other], "complete"))
    calls = []
    monkeypatch.setattr(be, "fetch_fixture_comparison", lambda f, **kwargs: calls.append(f['event_id']) or be._comparison_response_rows(json.dumps({"odds": HTML}), f, CONTEXT, OBS)[0])
    monkeypatch.setattr(oddsportal_upcoming, "_page_for_target", lambda d: (_ for _ in ()).throw(RuntimeError("optional failure")))
    monkeypatch.delenv(oddsportal_upcoming.DISABLE_ENV, raising=False)
    result = daily.collect_rebuild_sources(DAY)
    assert calls == ["a1B2"]
    assert result['status'] == 'insufficient' and result['outcomes']['oddsportal']['status'] == 'failed'
    assert result['outcomes']['betexplorer']['detail_counts']['offers'] == 2
    assert result['raw_quotes']['betexplorer'][0]['settlement_rules'] is None


def test_quote_provenance_reversed_survives_warehouse_export():
    import pandas as pd
    from racketfactory import warehouse
    from scripts import mine_edges
    row = be._comparison_response_rows(json.dumps({"odds": HTML}), FIXTURE, CONTEXT, OBS)[0][0]
    row['settlement_rules'] = {"retirement": "advancer", "walkover": "void", "cancelled": "void"}  # synthetic evidence only
    row['tournament'] = "Synthetic tournament"
    card = pd.DataFrame([{"match_date": DAY, "player_home": "Beth Brown", "player_away": "Alice Adams", "prob_home": .4, "prob_away": .6}])
    out = warehouse.enrich_live_card_with_api_odds(card, DAY, supplied_odds=[], supplied_comparisons=[row]).iloc[0]
    pick = {"selected_side": "player_a", "selected_player": "Beth Brown"}
    quote = mine_edges.warehouse_selected_quote(out, pick, out['odds_home'], 'BetExplorer', 'Book One')
    assert quote['odds'] == 2.5 and quote['provider_side'] == '2' and quote['provider_side_bookmaker'] == 'Book One'
    assert quote['response_sha256'] == row['response_sha256'] and quote['match_url'] == FIXTURE['match_url']
    assert quote['provider_price_updated_at'] == ['101', '100'] and quote['provider_match_time'] == '14:00'
    assert quote['provider_tournament'] == 'Synthetic tournament' and quote['observed_at'] == OBS


def test_three_outcome_and_conflicting_positions_are_rejected():
    three = HTML.replace('<th>Bookmaker</th>', '<th>Bookmaker</th><th data-pos="x">Draw</th>')
    rows, stats = be._comparison_response_rows(json.dumps({"odds": three}), FIXTURE, CONTEXT, OBS)
    assert rows == [] and stats['ambiguous_columns'] == 1
    conflict = HTML.replace('data-odd="2.50" data-created="100"', 'data-odd="2.50" data-pos="a" data-created="100"')
    rows, stats = be._comparison_response_rows(json.dumps({"odds": conflict}), FIXTURE, CONTEXT, OBS)
    # Visible row rejected; hidden duplicate is independently bound by data-pos.
    assert stats['invalid_price'] == 2 and rows[0]['bookmaker'] == 'Book One'


def test_http_refusal_and_rate_limit_stop_without_retry(tmp_path, monkeypatch):
    import requests
    from racketfactory.quota_guard import cooldown_status
    from racketfactory.sources import _page_odds
    class Response:
        def __init__(self, status): self.status_code=status; self.headers={'Retry-After': '120'}; self.text='blocked'
    calls=[]
    monkeypatch.setattr(_page_odds, '_throttle', lambda *a: None)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: calls.append(1) or Response(429))
    with pytest.raises(PageFetchError, match='rate_limit'):
        be._fetch_comparison_document('https://www.betexplorer.com/match-odds/a1B2/T')
    assert len(calls) == 1 and cooldown_status('betexplorer', 'host:www.betexplorer.com')
    with pytest.raises(PageFetchError, match='cooldown'):
        be._fetch_comparison_document('https://www.betexplorer.com/match-odds/a1B2/T')
    assert len(calls) == 1


def test_conflicting_rows_for_same_book_are_not_selected():
    html = HTML.replace('data-pos="b" data-odd="2.50"', 'data-pos="b" data-odd="2.60"')
    rows, stats = be._comparison_response_rows(json.dumps({"odds": html}), FIXTURE, CONTEXT, OBS)
    assert [r['bookmaker'] for r in rows] == ['Book Two']
    assert stats['ambiguous_bookmaker_rows'] == 1


def test_listing_challenge_is_not_retried_via_another_route(monkeypatch):
    calls = []
    def refused(url, *args, **kwargs):
        calls.append(url)
        raise PageFetchError('challenge', 'HTTP 403')
    monkeypatch.setattr(be, 'fetch_page_html', refused)
    with pytest.raises(PageFetchError, match='challenge'):
        be._fetch_live(DAY, with_status=True)
    assert len(calls) == 1


def test_synthetic_discovery_counts_rejections_and_fails_conflicting_context():
    stats = {}
    page = (PAGE.replace('data-period="match"', 'data-period="set1"') +
            PAGE.replace('Match Winner', 'Set Winner') +
            PAGE.replace('/match-odds/a1B2/', '/match-odds/other/'))
    assert be._comparison_endpoint_candidates(page, 'a1B2', metrics=stats) == []
    assert stats['endpoint_nodes_scanned'] == 3
    assert stats['endpoint_period_rejected'] == 1
    assert stats['endpoint_market_rejected'] == 1
    assert stats['endpoint_url_rejected'] == 1
    stats = {}
    assert be._comparison_endpoint_candidates(PAGE + PAGE.replace('data-market-id="T"', 'data-market-id="X"'), 'a1B2', metrics=stats) == []
    assert stats['endpoint_conflicting_context'] == 1


def test_synthetic_success_http_unexpected_page_never_requests_comparison(monkeypatch):
    calls = []
    monkeypatch.setattr(be, '_fetch_comparison_document', lambda url, referer=None: (calls.append((url, referer)) or '<html>unrelated</html>', OBS))
    counts = {}
    with pytest.raises(PageFetchError, match='endpoint') as exc:
        be.fetch_fixture_comparison(FIXTURE, refresh_evidence=True, stage_counts=counts)
    assert exc.value.stage == 'endpoint_discovery'
    assert counts['match_page_success'] == 1 and counts.get('comparison_attempts', 0) == 0
    assert len(calls) == 1


def test_same_endpoint_conflicting_rejected_before_market_period_filter():
    # Synthetic regression: a rejected set or totals context must not vanish
    # before conflict detection for the very same endpoint.
    assert be._comparison_endpoint_candidates(PAGE + PAGE.replace('data-period="match"', 'data-period="set1"'), 'a1B2') == []
    assert be._comparison_endpoint_candidates(PAGE + PAGE.replace('Match Winner', 'Total Games'), 'a1B2') == []
    unrelated = PAGE.replace('/a1B2/', '/other/').replace('data-period="match"', 'data-period="set1"')
    assert len(be._comparison_endpoint_candidates(PAGE + unrelated, 'a1B2')) == 1
    assert len(be._comparison_endpoint_candidates(PAGE + PAGE.replace('Match Winner', 'h2h').replace('data-period="match"', 'data-period="full match"'), 'a1B2')) == 1


def test_synthetic_complete_source_plumbing_still_blocks_unknown_settlement(monkeypatch, tmp_path):
    """Mocked complete listing is plumbing only, not proof of a live schedule."""
    from racketfactory.sources import betclan, predixsport, oddsportal_upcoming
    from racketfactory import quote_enrichment
    from racketfactory.kickoff import SAST
    from scripts import daily

    monkeypatch.setattr(quote_enrichment, '_collection_now', lambda: datetime(2026, 10, 29, 10, 0, tzinfo=SAST))
    prediction = dict(FIXTURE, player_home='Beth Brown', player_away='Alice Adams')
    class Clan:
        last_collection_status = 'complete'
        def fetch_daily(self): return [prediction]
    class Predix:
        def fetch_daily(self): return []
    monkeypatch.setattr(betclan, 'BetClanPredictor', Clan)
    monkeypatch.setattr(predixsport, 'PredixSportPredictor', Predix)
    monkeypatch.delenv(be.DISABLE_ENV, raising=False)
    monkeypatch.setenv(oddsportal_upcoming.DISABLE_ENV, '1')
    # The mock supplies a complete schedule; no production verifier exists.
    monkeypatch.setattr(be, '_fetch_live', lambda *a, **kw: ([dict(FIXTURE)], 'complete'))
    calls = []
    observed = datetime.now(timezone.utc).isoformat()
    def document(url, referer=None):
        calls.append((url, referer))
        return (json.dumps({'odds': HTML}) if referer else PAGE, observed)
    monkeypatch.setattr(be, '_fetch_comparison_document', document)
    collection = daily.collect_rebuild_sources(DAY)
    counts = collection['outcomes']['betexplorer']['detail_counts']
    assert counts['matched_fixtures'] == counts['attempted'] == 1
    assert len(calls) == 2  # one match page and its discovered comparison
    assert len(collection['raw_quotes']['betexplorer']) == 2
    assert all(rebuild_quote_execution_problem(row) == 'bookmaker settlement rules evidence missing'
               for row in collection['raw_quotes']['betexplorer'])
    assert collection['outcomes']['betexplorer']['status'] == 'partial'
    assert collection['status'] == 'insufficient'
    card = tmp_path / 'card.json'
    card.write_text('{"previous": true}')
    with pytest.raises(RuntimeError, match='required fresh collection is incomplete'):
        daily.stage_fresh_rebuild_picks(DAY, collection, tmp_path / 'stage')
    assert card.read_text() == '{"previous": true}'
