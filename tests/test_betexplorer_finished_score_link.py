"""Reduced retained-row reconstructions and synthetic adversarial controls.

The run-41016336262 samples omit text, elements and attributes. These tests do
not replay the original response or establish tennis schedule semantics.
"""
import pytest

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
    {'home': 'Vacherot Valentin'},
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
