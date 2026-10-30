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
def test_unverified_or_conflicting_date_blocks_detail_and_legacy_prices(monkeypatch, dt):
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
    assert not offers and not calls and counts['matching_skipped_reason'] == 'listing_not_complete'
    with pytest.raises(be.PageFetchError, match='schedule is unverified'):
        be._fetch_live('2026-10-31')


def test_existing_cached_listing_cannot_bypass_schedule_guard(monkeypatch):
    monkeypatch.delenv(be.DISABLE_ENV, raising=False)
    monkeypatch.setattr(be, 'cached_fetch', lambda *a, **kw: [{'match_date': '2026-10-31', 'odds_home': 1.5}])
    with pytest.raises(be.PageFetchError, match='schedule is unverified'):
        be.fetch_betexplorer_rows('2026-10-31')


def test_finished_listing_is_still_partial_when_schedule_unknown(monkeypatch):
    html = listing('<span>van Wyk K.</span><span>Marmousez L.</span>')
    html = html.replace('<td></td><td><a', '<td>Finished</td><td><a', 1)
    monkeypatch.setattr(be, 'fetch_page_html', lambda *a, **kw: html)
    rows, status = be._fetch_live('2026-10-31', with_status=True)
    assert rows == [] and status == 'partial'


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
