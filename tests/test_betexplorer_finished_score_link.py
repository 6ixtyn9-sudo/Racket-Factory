"""Retained BetExplorer row excerpts and synthetic adversarial controls.

The earlier reduced samples and the selected parser-replay excerpts omit parts
of their source responses; none are original raw HTML or schedule proof.
"""
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from racketfactory import quote_enrichment
from racketfactory.sources import betexplorer as be


PATH = '/tennis/atp-singles/basel/vacherot-valentin-jodar-rafael/zui4APmJ/'
OTHER = '/tennis/atp-singles/basel/michelsen-alex-arthur-fils/Sdd0CkDT/'


def listing(*, path=PATH, score_path=PATH, status='FIN', home='Vacherot V.',
            away='Jodar R.', score='2:1', row_attr='', name_attr='',
            score_attr='', extra='', status_extra='', winner_away=False,
            dt='29,10,2026,12,10'):
    home_text = home if winner_away else f'<strong>{home}</strong>'
    away_text = f'<strong>{away}</strong>' if winner_away else away
    return f'''<table><tr data-dt="{dt}" {row_attr}>
      <td class="table-main__tt" {name_attr}><span>{status}{status_extra}</span>
        <a href="{path}"><span>{home_text}</span><span>{away_text}</span></a></td>
      <td><a href="{score_path}" {score_attr}><strong>{score}</strong></a></td>
      <td colspan="2">{extra}</td></tr></table>'''


@pytest.mark.parametrize(('home', 'away', 'path', 'score', 'winner_away', 'dt'), [
    ('Vacherot V.', 'Jodar R.', PATH, '2:1', False, '29,10,2026,12,10'),
    ('Michelsen A.', 'Fils A.', OTHER, '1:2', True, '29,10,2026,15,0'),
])
def test_reduced_retained_fin_and_same_event_score_are_terminal_only(home, away, path, score, winner_away, dt):
    metrics = {}
    rows = be.parse_results_page(listing(home=home, away=away, path=path,
                                         score_path=path, score=score,
                                         winner_away=winner_away, dt=dt),
                                 '2026-10-30', metrics=metrics, include_unpriced=True)
    assert rows == []
    assert metrics['data_dt_rows'] == metrics['finished_rows'] == 1
    assert metrics['unrecognized_rows'] == 0
    # A FIN-shaped row is not a schedule, an odds offer or complete coverage.
    assert metrics['unverified_schedule_rows'] == 1
    assert metrics['parsed_priced_rows'] == 0


@pytest.mark.parametrize('kwargs', [
    {'status': 'LIVE'},
    {'status': 'Fin'},
    {'status_extra': '<!-- unknown status -->'},
    {'status_extra': '<span>LIVE</span>'},
    {'score_path': OTHER},
    {'score_path': 'https://other.invalid' + PATH},
    {'path': PATH + '?event=zui4APmJ', 'score_path': PATH + '?event=zui4APmJ'},
    {'score': ''},
    {'score': '2:2'},
    {'score': '1:0'},
    {'score': '2.10'},
    {'score': '2:1 LIVE'},
    {'score_attr': 'aria-hidden="true"'},
    {'name_attr': 'style="display:none"'},
    {'row_attr': 'data-status="live"'},
    {'row_attr': 'class="match-live"'},
    {'extra': 'Postponed'},
    {'extra': '<span></span>'},
    {'away': 'Vacherot V.'},
    {'home': 'Vacherot V. / Jodar R.'},
    {'home': 'Player 123'},
    {'home': 'Winner V.'},
])
def test_ambiguous_or_conflicting_shape_remains_unrecognized(kwargs):
    metrics = {}
    rows = be.parse_results_page(listing(**kwargs), '2026-10-30',
                                 metrics=metrics, include_unpriced=True)
    assert rows == []
    assert metrics['finished_rows'] == 0
    assert metrics['unrecognized_rows'] == 1
    assert metrics['unverified_schedule_rows'] == 1


def test_hyphenated_initial_is_one_participant_token_and_terminal_row_is_excluded():
    html = listing(home='Djokovic N.', away='Struff J-L.', score='1:2', winner_away=True)
    metrics = {}
    rows = be.parse_results_page(html, '2026-10-30', metrics=metrics, include_unpriced=True)
    assert rows == []
    assert metrics['finished_rows'] == 1
    assert metrics['unrecognized_rows'] == 0
    assert metrics['row_accounting_complete'] is True



def test_explicit_finished_label_is_terminal_without_treating_case_variant_as_fin():
    metrics = {}
    rows = be.parse_results_page(listing(status='FINISHED'), '2026-10-30',
                                 metrics=metrics, include_unpriced=True)
    assert rows == [] and metrics['finished_rows'] == 1
    assert metrics['unrecognized_rows'] == 0


@pytest.mark.parametrize('intrusion', ['LIVE', '<!-- unknown -->'])
def test_extra_row_level_content_is_unrecognized(intrusion):
    html = listing().replace('<td colspan="2">', intrusion + '<td colspan="2">')
    metrics = {}
    assert be.parse_results_page(html, '2026-10-30', metrics=metrics, include_unpriced=True) == []
    assert metrics['finished_rows'] == 0 and metrics['unrecognized_rows'] == 1
    assert metrics['unverified_schedule_rows'] == 1


def test_terminal_shape_never_makes_listing_complete_or_starts_comparison(monkeypatch):
    monkeypatch.setattr(be, 'fetch_page_html', lambda *args, **kwargs: listing())
    metrics = {}
    rows, status = be._fetch_live('2026-10-30', with_status=True, metrics=metrics)
    assert rows == [] and status == 'partial'
    assert metrics['finished_rows'] == 1 and metrics['unverified_schedule_rows'] == 1
    assert metrics['actionable_schedule_unverified_rows'] == 0
    assert metrics['listing_recognition_status'] == 'complete'
    with pytest.raises(be.PageFetchError, match='requested-day listing coverage'):
        be._fetch_live('2026-10-30')
    monkeypatch.setattr(be, 'fetch_fixture_comparison',
                        lambda *args, **kwargs: pytest.fail('unexpected detail request'))
    offers, counts, _ = quote_enrichment.collect_prediction_comparisons(
        '2026-10-30', [{'match_date': '2026-10-30'}], rows, status)
    assert offers == [] and counts['attempted'] == 0
    assert counts['unmatched_or_ambiguous'] == 1
    assert counts['attempted'] == 0


def test_fin_row_does_not_mask_another_unrecognized_row(monkeypatch):
    html = listing() + listing(status='LIVE')
    monkeypatch.setattr(be, 'fetch_page_html', lambda *args, **kwargs: html)
    metrics = {}
    rows, status = be._fetch_live('2026-10-30', with_status=True, metrics=metrics)
    assert rows == [] and status == 'partial'
    assert metrics['finished_rows'] == 1 and metrics['unrecognized_rows'] == 1
    assert metrics['unverified_schedule_rows'] == 2


AUTHENTIC_TERMINAL_ROWS = Path(__file__).parent / 'fixtures' / 'betexplorer_terminal_rows.html'
AUTHENTIC_DAY = '2026-10-30'


def test_retained_authentic_rows_cover_score_anchors_set_scores_retirement_and_walkover():
    # These are selected row excerpts from the supplied retained parser replay,
    # not raw response HTML; see the fixture header for its partial-retention SHA.
    html = AUTHENTIC_TERMINAL_ROWS.read_text()
    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.find_all('tr', attrs={'data-dt': True})
    assert len(rows) == 7

    terminal = [row for row in rows if be._listing_status_state(row)[0] == 'terminal']
    assert len(terminal) == 6
    assert all(be._finished_score_link_row(row) for row in terminal)

    # This authentic row was previously rejected only by the strict participant
    # name lexer. Its explicit two-span structure is sufficient for terminal
    # exclusion, but the participant identity is still not emitted.
    lexical_reasons = []
    assert be._structured_participants(
        [terminal[-1].find('td', class_='table-main__tt').find('a', href=True)],
        rejection_reasons=lexical_reasons) is None
    assert lexical_reasons == ['participant_name_format']

    first = terminal[0].find('td', class_='table-main__tt').find('a', href=True)
    second = terminal[1].find('td', class_='table-main__tt').find('a', href=True)
    doubles = terminal[2].find('td', class_='table-main__tt').find('a', href=True)
    same_event_links = [a for a in terminal[0].find_all('a', href=True)
                        if be._is_match_link(a.get('href', ''))]
    reasons = []
    assert be._structured_participants(same_event_links, rejection_reasons=reasons) is None
    assert 'participant_sibling_anchors_not_co_located' in reasons
    # The fixed terminal path isolates only the two-span participant anchor.
    assert be._structured_participants([first]) == ('Vacherot V.', 'Jodar R.')
    assert be._structured_participants([second]) == ('Michelsen A.', 'Fils A.')
    assert be._structured_participants([doubles]) == (
        'Harrison C. / Hewitt D.', 'Sobolieva A. / Yaneva E.')

    # The fourth terminal excerpt is a retained retirement with a partial set;
    # the fifth is the observed walkover shape with an empty third cell.
    assert 'RET.' in terminal[3].find_all('td', recursive=False)[1].get_text(' ', strip=True)
    assert terminal[3].find_all('td', recursive=False)[2].get_text(' ', strip=True) == '(6:2, 2:0)'
    assert terminal[4].find_all('td', recursive=False)[1].get_text(' ', strip=True) == 'WO.'
    assert not terminal[4].find_all('td', recursive=False)[2].get_text(' ', strip=True)


def test_authentic_excerpt_stays_partial_and_cannot_create_actionable_rows():
    html = AUTHENTIC_TERMINAL_ROWS.read_text()
    metrics = {}
    assert be.parse_results_page(html, AUTHENTIC_DAY, metrics=metrics, include_unpriced=True) == []
    assert metrics['data_dt_rows'] == 7
    assert metrics['finished_rows'] == 6
    assert metrics['unrecognized_rows'] == 1
    assert metrics['conflicting_status_rows'] == 1
    assert metrics['recognized_actionable_rows'] == 0
    assert metrics['row_accounting_complete'] is True
    assert metrics['listing_recognition_status'] == 'partial'
    assert metrics['listing_coverage_status'] == 'not_established'

    unknown = next(row for row in BeautifulSoup(html, 'html.parser').find_all('tr')
                   if row.has_attr('data-live'))
    state, signals = be._listing_status_state(unknown)
    assert state == 'conflicting_or_unknown'
    assert 'UNRECOGNIZED_STATUS_ATTRIBUTE' in signals
    match_ids = [a.get('href', '').rstrip('/').split('/')[-1]
                 for a in unknown.find_all('a', href=True) if be._is_match_link(a.get('href', ''))]
    assert unknown['data-live'] in match_ids  # value resemblance does not decode its semantics


def test_terminal_parser_rejects_mismatched_or_ambiguous_score_markup():
    cases = [
        listing(score_path=OTHER),
        listing(extra='(7:6, malformed)'),
        listing(extra='(7:6, 15:6)'),
        listing(extra='<span>(7:6, 2:6)</span>'),
        listing(extra='(7:6, 2:6)<!-- unexplained -->'),
        listing(extra='<a href="/tennis/atp-singles/basel/extra-match/AbCd1234/">extra</a>'),
        listing(status_extra='<span>LIVE</span>'),
        listing(row_attr='data-status="POSTPONED"'),
        listing(name_attr='style="display:none"'),
        listing(score_attr='aria-hidden="true"'),
        listing(score='1:0', extra=''),
        listing(score='2:2', extra=''),
    ]
    extra_participant = listing().replace(
        '</span></a></td>', '</span><span>Extra C.</span></a></td>', 1)
    cases.append(extra_participant)
    for html in cases:
        metrics = {}
        assert be.parse_results_page(html, AUTHENTIC_DAY, metrics=metrics, include_unpriced=True) == []
        assert metrics['actionable_schedule_unverified_rows'] == 0
        assert metrics['discovered_rows'] == 0
        assert metrics['row_accounting_complete'] is True


def test_authentic_terminal_excerpt_through_source_and_quote_gates(monkeypatch):
    html = AUTHENTIC_TERMINAL_ROWS.read_text()
    monkeypatch.delenv(be.DISABLE_ENV, raising=False)
    monkeypatch.delenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', raising=False)
    monkeypatch.setattr(be, 'fetch_page_html', lambda *args, **kwargs: html)
    # Exercise the normal source producer without writing its shared disk cache.
    monkeypatch.setattr(be, 'cached_fetch', lambda _key, callback: callback())
    with pytest.raises(be.PageFetchError, match='dated listing contains unrecognized rows'):
        be.fetch_betexplorer_rows(AUTHENTIC_DAY)

    listing_metrics = {}
    rows, status = be._fetch_live(AUTHENTIC_DAY, with_status=True, metrics=listing_metrics)
    assert rows == [] and status == 'partial'
    assert listing_metrics['finished_rows'] == 6
    assert listing_metrics['conflicting_status_rows'] == 1
    assert listing_metrics['listing_coverage_status'] == 'not_established'
    assert listing_metrics['schedule_verification_status'] == 'not_required_for_excluded_rows'

    detail_calls = []
    monkeypatch.setattr(be, 'fetch_fixture_comparison',
                        lambda *args, **kwargs: detail_calls.append(args))
    prediction = {'match_date': AUTHENTIC_DAY, 'player_home': 'Alice Adams',
                  'player_away': 'Beth Brown'}
    offers, counts, partial = quote_enrichment.collect_prediction_comparisons(
        AUTHENTIC_DAY, [prediction], rows, status, listing_metrics=listing_metrics)
    assert offers == [] and partial is True and detail_calls == []
    assert counts['attempted'] == counts['comparison_attempts'] == 0
    assert counts['stage_statuses']['listing_coverage'] == 'not_established'
    assert counts['stage_statuses']['detail_discovery'] == 'not_attempted'
    assert counts['stage_statuses']['quote_availability'] == 'not_attempted'
    assert counts['stage_statuses']['required_source_readiness'] == 'blocked'
