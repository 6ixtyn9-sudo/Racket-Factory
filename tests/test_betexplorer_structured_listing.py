"""Reduced evidence-derived and synthetic fixtures, never full provider replay."""

import pytest

from racketfactory import quote_enrichment
from racketfactory.sources import betexplorer as be


PATH = '/tennis/itf-men-singles/m15-monastir-37/van-wyk-kris-marmousez-lilian/E7omKkv8/'


def listing(content, dt='30,10,2026,11,0', suffix=''):
    # The capture retained four cells and one match link with two spans. One
    # text node and two elements were omitted: this is NOT the original HTML.
    return (f'<table><tr data-dt="{dt}"><td></td><td><a href="{PATH}">{content}</a></td>'
            f'<td></td><td></td></tr>{suffix}</table>')


@pytest.mark.parametrize('day', ['2026-10-30', '2026-10-31'])
def test_retained_participant_boundaries_are_preserved_without_inventing_date(day):
    metrics = {}
    rows = be.parse_results_page(listing('<span>van Wyk K.</span><span>Marmousez L.</span>'),
                                 day, metrics=metrics, include_unpriced=True)
    assert len(rows) == 1 and metrics['unrecognized_rows'] == 0
    row = rows[0]
    assert (row['player_home'], row['player_away']) == ('van Wyk K.', 'Marmousez L.')
    assert row['listing_date'] == day
    assert row['listing_data_dt_components'] == [30, 10, 2026, 11, 0]
    assert row['listing_schedule_status'] == 'unverified'
    assert row['match_date'] == row['match_time'] == ''
    assert metrics['unverified_schedule_rows'] == 1


@pytest.mark.parametrize(('content', 'home', 'away'), [
    ('<span>Marmousez L.</span><span>van Wyk K.</span>', 'Marmousez L.', 'van Wyk K.'),
    ('<span><b>de la Cruz</b> A.</span><span><em>van Wyk</em> K.</span>', 'de la Cruz A.', 'van Wyk K.'),
    ("<span>van Wyk K. / de la Cruz A.</span><span>O'Neil B. / Švec I.</span>",
     'van Wyk K. / de la Cruz A.', "O'Neil B. / Švec I."),
])
def test_nested_markup_doubles_and_orientation_remain_explicit(content, home, away):
    metrics = {}
    rows = be.parse_results_page(listing(content), '2026-10-30', metrics=metrics, include_unpriced=True)
    assert [(r['player_home'], r['player_away']) for r in rows] == [(home, away)]
    assert metrics['unrecognized_rows'] == 0
    assert rows[0]['match_date'] == ''


@pytest.mark.parametrize('content', [
    '<span>van Wyk K.</span><span>Marmousez L.</span><span>Alpha A.</span>',
    '<span>van Wyk K.</span> versus <span>Marmousez L.</span>',
    '<span>van Wyk K.</span><span>Set 1</span>',
    '<span>van Wyk K.</span><span>van Wyk K.</span>',
    '<span>van Wyk K. / Smith A.</span><span>Smith A. / Jones B.</span>',
    '<span>van Wyk K. / Smith A.</span><span>Jones B.</span>',
    '<span class="match-score">van Wyk K.</span><span>Marmousez L.</span>',
    '<div><span>van Wyk K.</span><span>Marmousez L.</span></div>',
    '<span>van Wyk K. Marmousez L.</span><span>Alpha A.</span>',
    '<span style="display:none">van Wyk K.</span><span>Marmousez L.</span>',
    '<span>van Wyk K.</span><span aria-hidden="true">Marmousez L.</span>',
    '<span>van Wyk K.</span><span>Marmousez L.</span><script>ignored</script>',
    '<span>van Wyk Kris</span><span>Marmousez Lilian</span>',
])
def test_ambiguous_span_structure_stays_unrecognized(content):
    metrics = {}
    rows = be.parse_results_page(listing(content), '2026-10-30', metrics=metrics, include_unpriced=True)
    assert not rows and metrics['unrecognized_rows'] == 1


@pytest.mark.parametrize('dt', ['30,10,2026,11,0', '31,10,2026,11,0', 'bad-date', '30,10,2026,25,0'])
def test_unverified_or_conflicting_date_uses_bounded_fixture_matching_without_legacy_prices(monkeypatch, dt):
    html = listing('<span>van Wyk K.</span><span>Marmousez L.</span>', dt=dt)
    monkeypatch.setattr(be, 'fetch_page_html', lambda *a, **kw: html)
    metrics = {}
    rows, status = be._fetch_live('2026-10-31', with_status=True, metrics=metrics)
    assert status == 'partial' and len(rows) == 1 and metrics['unverified_schedule_rows'] == 1
    assert rows[0]['match_date'] == rows[0]['match_time'] == ''
    calls = []
    monkeypatch.setattr(be, 'fetch_fixture_comparison', lambda *a, **kw: calls.append(a))
    prediction = {'match_date': '2026-10-31', 'player_home': 'van Wyk K.', 'player_away': 'Marmousez L.'}
    offers, counts, _ = quote_enrichment.collect_prediction_comparisons('2026-10-31', [prediction], rows, status)
    # Conflicting/unknown listing dates cannot be rebound to the prediction;
    # bounded detail matching is reserved for uniquely dated fixtures.
    assert not offers and calls == []
    assert counts.get('attempted', 0) == 0
    with pytest.raises(be.PageFetchError, match='schedule is unverified'):
        be._fetch_live('2026-10-31')


def test_existing_cached_listing_cannot_bypass_schedule_guard(monkeypatch):
    monkeypatch.delenv(be.DISABLE_ENV, raising=False)
    monkeypatch.setattr(be, 'cached_fetch', lambda *a, **kw: [{'match_date': '2026-10-31', 'odds_home': 1.5}])
    with pytest.raises(be.PageFetchError, match='cached schedule evidence is unverified'):
        be.fetch_betexplorer_rows('2026-10-31')


def test_finished_listing_row_accounting_does_not_claim_target_date_coverage(monkeypatch):
    html = listing('<span>van Wyk K.</span><span>Marmousez L.</span>')
    html = html.replace('<td></td><td><a', '<td>Finished</td><td><a', 1)
    monkeypatch.setattr(be, 'fetch_page_html', lambda *a, **kw: html)
    metrics = {}
    rows, status = be._fetch_live('2026-10-31', with_status=True, metrics=metrics)
    assert rows == [] and status == 'partial'
    assert metrics['listing_recognition_status'] == 'complete'
    assert metrics['listing_coverage_status'] == 'not_established'
    assert metrics['schedule_verification_status'] == 'not_required_for_excluded_rows'


def test_generic_fallback_does_not_assign_listing_date_or_crawl_details(monkeypatch):
    calls = []
    monkeypatch.setattr(be, 'fetch_page_html', lambda url, *a, **kw: calls.append(url) or '<html>fixture</html>')
    fallback = {'match_date': '2026-10-31', 'match_time': '11:00', 'match_url': PATH,
                'player_home': 'van Wyk K.', 'player_away': 'Marmousez L.'}
    monkeypatch.setattr(be, 'parse_listing_page', lambda *a, **kw: [dict(fallback)])
    rows, status = be._fetch_live('2026-10-31', with_status=True)
    assert status == 'partial' and len(calls) == 1
    assert rows[0]['match_date'] == rows[0]['match_time'] == ''
    with pytest.raises(be.PageFetchError, match='schedule is unverified'):
        be._fetch_live('2026-10-31')
    assert len(calls) == 2  # one listing request per invocation; no detail crawl


def test_extra_empty_match_anchor_keeps_the_structure_ambiguous():
    html = listing('<span>van Wyk K.</span><span>Marmousez L.</span>')
    html = html.replace('<td></td><td></td></tr>',
                        '<td><a href="/tennis/atp/tour/other/xyz123/"></a></td><td></td></tr>')
    metrics = {}
    rows = be.parse_results_page(html, '2026-10-30', metrics=metrics, include_unpriced=True)
    assert rows == [] and metrics['unrecognized_rows'] == 1


@pytest.mark.parametrize(('first', 'second'), [
    ('Kim D. J.', 'Borisiouk M.'),
    ('Borisiouk M.', 'Kim D. J.'),
    ('van Wyk K. A.', 'Marmousez L.'),
    ('Kim D. J. / de la Cruz A.', "O'Neil B. / Švec I."),
])
def test_explicit_spans_preserve_multiple_initials(first, second):
    metrics = {}
    rows = be.parse_results_page(listing(f'<span>{first}</span><span>{second}</span>'),
                                 '2026-10-30', metrics=metrics, include_unpriced=True)
    assert [(row['player_home'], row['player_away']) for row in rows] == [(first, second)]
    assert metrics['unrecognized_rows'] == 0
    assert rows[0]['match_date'] == rows[0]['match_time'] == ''


def _two_anchors(first, second, *, path2=PATH, separator=' vs '):
    return (f'<table><tr data-dt="30,10,2026,11,0"><td><a href="{PATH}">{first}</a>'
            f'{separator}<a href="{path2}">{second}</a></td><td>-</td><td>-</td></tr></table>')


@pytest.mark.parametrize(('first', 'second', 'home', 'away'), [
    ('<span><b>Kim</b> D. J.</span>', '<span>Borisiouk M.</span>', 'Kim D. J.', 'Borisiouk M.'),
    ('<span>Borisiouk M.</span>', '<span><em>Kim</em> D. J.</span>', 'Borisiouk M.', 'Kim D. J.'),
    ('<span>Kim D. J. / van Wyk K.</span>', '<span>Borisiouk M. / de la Cruz A.</span>',
     'Kim D. J. / van Wyk K.', 'Borisiouk M. / de la Cruz A.'),
])
def test_same_event_anchors_with_nested_spans_preserve_order(first, second, home, away):
    metrics = {}
    rows = be.parse_results_page(_two_anchors(first, second), '2026-10-30', metrics=metrics, include_unpriced=True)
    assert [(row['player_home'], row['player_away']) for row in rows] == [(home, away)]
    assert metrics['unrecognized_rows'] == 0
    assert rows[0]['match_date'] == rows[0]['match_time'] == ''


@pytest.mark.parametrize('html', [
    _two_anchors('<span>Kim D. J.</span>', '<span>Borisiouk M.</span>', path2='/tennis/atp/tour/other/xyz123/'),
    _two_anchors('<span>Kim D. J.</span>', '<span>Borisiouk M.</span>', path2='https://evil.example' + PATH),
    _two_anchors('<span>Kim D. J.</span><span>Alpha A.</span>', '<span>Borisiouk M.</span>'),
    _two_anchors('<span>Kim D. J.</span>', '<span>Borisiouk M.</span>', separator=' unknown '),
    _two_anchors('<span>Kim D. J.</span>', '<span aria-hidden="true">Borisiouk M.</span>'),
    _two_anchors('<span>Kim D. J. Borisiouk M.</span>', '<span>Alpha A.</span>'),
    _two_anchors('<span>Kim D. J.</span>', '<span>Kim D. J.</span>'),
    _two_anchors('Kim D. J.', 'Borisiouk M.', path2='/tennis/atp/tour/other/xyz123/'),
    _two_anchors('Kim D. J.', 'Borisiouk M.', separator=' unknown '),
])
def test_ambiguous_anchor_structures_remain_unrecognized(html):
    metrics = {}
    assert be.parse_results_page(html, '2026-10-30', metrics=metrics, include_unpriced=True) == []
    assert metrics['unrecognized_rows'] == 1


def test_plain_undelimited_full_names_are_not_split_by_whitespace():
    metrics = {}
    assert be.parse_results_page(listing('Van Wyk Kris De La Cruz Ana'), '2026-10-30',
                                 metrics=metrics, include_unpriced=True) == []
    assert metrics['unrecognized_rows'] == 1


@pytest.mark.parametrize(('raw', 'expected', 'status'), [
    ('30,10,2026,11,0', [30, 10, 2026, 11, 0], 'valid_unverified'),
    ('31,10,2026,23,59', [31, 10, 2026, 23, 59], 'valid_unverified'),
    ('1,11,2026,0,1', [1, 11, 2026, 0, 1], 'valid_unverified'),
    ('29,2,2024,0,0', [29, 2, 2024, 0, 0], 'valid_unverified'),
    ('29,2,2026,0,0', None, 'invalid'),
    ('31,10,2026,24,0', None, 'invalid'),
    ('31,10,2026,23,60', None, 'invalid'),
    ('2026,10,31,11,0', None, 'invalid'),
    ('bad-date', None, 'invalid'),
])
def test_component_shape_does_not_verify_timezone_or_schedule(raw, expected, status):
    assert be._listing_dt_components(raw) == (expected, status)
    metrics = {}
    rows = be.parse_results_page(listing('<span>van Wyk K.</span><span>Marmousez L.</span>', dt=raw),
                                 '2026-10-31', metrics=metrics, include_unpriced=True)
    assert len(rows) == 1
    assert rows[0]['listing_data_dt_components'] == expected
    assert rows[0]['listing_component_status'] == status
    assert rows[0]['listing_schedule_status'] == 'unverified'
    assert rows[0]['match_date'] == rows[0]['match_time'] == ''


def test_finished_status_is_counted_only_after_unambiguous_name_extraction(monkeypatch):
    html = listing('<span>Kim D. J.</span><span>Borisiouk M.</span>').replace('<td></td><td><a', '<td>Finished</td><td><a', 1)
    monkeypatch.setattr(be, 'fetch_page_html', lambda *a, **kw: html)
    metrics = {}
    rows, status = be._fetch_live('2026-10-30', with_status=True, metrics=metrics)
    assert not rows and metrics['finished_rows'] == 1 and metrics['unrecognized_rows'] == 0
    assert status == 'partial'  # row recognition is not requested-day coverage
    assert metrics['actionable_schedule_unverified_rows'] == 0


def test_reduced_capture_replay_reaches_partial_collection_and_blocks_staging(monkeypatch, tmp_path):
    """Reduced event markup plus synthetic other providers; not a full live replay."""
    from racketfactory.sources import betclan, predixsport, oddsportal_upcoming
    from scripts import daily

    html = listing('<span>van Wyk K.</span><span>Marmousez L.</span>')
    calls = []
    monkeypatch.delenv(be.DISABLE_ENV, raising=False)
    monkeypatch.setenv(oddsportal_upcoming.DISABLE_ENV, '1')
    monkeypatch.setattr(be, 'fetch_page_html', lambda *a, **kw: calls.append('listing') or html)
    monkeypatch.setattr(be, 'fetch_fixture_comparison', lambda *a, **kw: calls.append('detail'))
    class Clan:
        last_collection_status = 'complete'
        def fetch_daily(self):
            return [{'match_date': '2026-10-30', 'match_time': '12:00',
                     'player_home': 'Marmousez L.', 'player_away': 'van Wyk K.'}]
    class Predix:
        def fetch_daily(self): return []
    monkeypatch.setattr(betclan, 'BetClanPredictor', Clan)
    monkeypatch.setattr(predixsport, 'PredixSportPredictor', Predix)
    card = tmp_path / 'auto_tickets.json'
    card.write_text('{"accas": [{"fixture": "preserve"}]}')
    before = card.read_bytes()
    result = daily.collect_rebuild_sources('2026-10-30')
    assert calls == ['listing']
    assert result['status'] == 'partial'
    assert result['outcomes']['betclan']['status'] == 'complete'
    assert result['outcomes']['betexplorer']['status'] == 'partial'
    counts = result['outcomes']['betexplorer']['detail_counts']
    assert counts.get('attempted', 0) == 0  # no target-date identity was established
    assert len(result['raw_quotes']['betexplorer']) == 1
    assert result['raw_quotes']['betexplorer'][0]['match_date'] == ''
    with pytest.raises(RuntimeError, match='no usable fresh fixture quote observations'):
        daily.stage_fresh_rebuild_picks('2026-10-30', result, tmp_path / 'stage')
    assert card.read_bytes() == before
