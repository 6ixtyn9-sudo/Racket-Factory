"""Synthetic capture tests: these are not the observed provider row."""
import json

import pytest
from bs4 import BeautifulSoup

from racketfactory import rebuild_diagnostics as diagnostics
from racketfactory.sources import betexplorer


MATCH_PATH = '/tennis/example/tournament/example-match/abc123/'


def _capture(monkeypatch, tmp_path, markup, day='2026-10-30'):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    row = BeautifulSoup(markup, 'html.parser').find('tr')
    diagnostics.capture_unrecognized_listing_row(row, day)
    return json.loads((tmp_path / 'betexplorer_unrecognized_rows.json').read_text())[-1]


def _texts(node, key):
    if isinstance(node, dict):
        result = [node[key]] if key in node else []
        for value in node.values():
            result.extend(_texts(value, key))
        return result
    if isinstance(node, list):
        return [item for value in node for item in _texts(value, key)]
    return []


def test_structured_public_text_preserves_order_and_redacts_sensitive_material(monkeypatch, tmp_path):
    # A made-up row, not a reconstruction of the reported four-cell row.
    sample = _capture(monkeypatch, tmp_path, f'''<tr data-dt="30,10,2026,13,05" data-token="SECRET_ATTRIBUTE">
        <td>Finished <span>PRIVATE OUTSIDE</span></td>
        <td class="table-main__tt" data-secret="SECRET_ATTRIBUTE"><a href="{MATCH_PATH}?event=abc123"
            title="SECRET_ATTRIBUTE"><span>Van Wyk Kris / De La Cruz Ana</span><span> - </span>
            <b>O'Neil Bea / Švec Iva</b><script>SECRET_SCRIPT</script><input value="SECRET_INPUT">
            <span>token SECRET_TEXT</span></a></td>
        <td>2:0</td><td><form><span>SECRET_FORM</span></form></td></tr>''')
    ev = sample['row_evidence']
    assert sample['classification'] == 'unresolved'
    assert sample['target_date'] == '2026-10-30'
    assert ev['data_dt_components'] == [30, 10, 2026, 13, 5]
    assert _texts(ev['cells'], 'name_like_text') == [
        'Van Wyk Kris / De La Cruz Ana', '-', "O'Neil Bea / Švec Iva"]
    assert _texts(ev['cells'], 'status_text') == ['Finished']
    assert _texts(ev['cells'], 'public_match_path') == [MATCH_PATH]
    assert sample['links'][0]['public_path'] == MATCH_PATH  # query is never retained
    assert ev['omissions']['text'] >= 2
    output = json.dumps(sample)
    for forbidden in ('SECRET_ATTRIBUTE', 'SECRET_SCRIPT', 'SECRET_INPUT', 'SECRET_TEXT',
                      'SECRET_FORM', 'PRIVATE OUTSIDE', '?event=', '2:0'):
        assert forbidden not in output
    assert 'not established' in ev['interpretation']


@pytest.mark.parametrize('day', ['2026-10-30', '2026-10-31'])
def test_synthetic_one_anchor_ambiguous_row_stays_partial(monkeypatch, tmp_path, day):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    html = f'''<table><tr data-dt="31,10,2026,09,00"><td>Postponed</td>
      <td class="table-main__tt"><a href="{MATCH_PATH}"><span>Van Wyk Kris</span></a></td>
      <td>-</td><td>-</td></tr></table>'''
    # Even an empty-board marker cannot resolve this unrecognized row, and
    # the generic scanner must not overwrite its status with complete.
    html += '<p>No matches</p>'
    monkeypatch.setattr(betexplorer, 'fetch_page_html', lambda *args, **kwargs: html)
    monkeypatch.setattr(betexplorer, 'parse_listing_page',
                        lambda *args, **kwargs: pytest.fail('unexpected generic fallback'))
    metrics = {}
    rows, status = betexplorer._fetch_live(day, with_status=True, metrics=metrics)
    sample = json.loads((tmp_path / 'betexplorer_unrecognized_rows.json').read_text())[0]
    assert rows == [] and status == 'partial' and metrics['unrecognized_rows'] == 1
    assert sample['classification'] == 'unresolved'
    assert sample['target_date'] == day
    assert sample['row_evidence']['data_dt_components'] == [31, 10, 2026, 9, 0]
    assert _texts(sample['row_evidence']['cells'], 'name_like_text') == ['Van Wyk Kris']
    assert _texts(sample['row_evidence']['cells'], 'status_text') == ['Postponed']
    # The listing day, date components and label are evidence, not a status decision.
    assert 'not established' in sample['row_evidence']['interpretation']
    with pytest.raises(betexplorer.PageFetchError, match='unrecognized rows'):
        betexplorer._fetch_live(day)


@pytest.mark.parametrize(('label', 'home', 'away'), [
    ('Van Wyk K. - De La Cruz A.', 'Van Wyk K.', 'De La Cruz A.'),
    ("O'Neil B. / De La Cruz A. - Van Wyk K. / Švec I.",
     "O'Neil B. / De La Cruz A.", 'Van Wyk K. / Švec I.'),
])
def test_existing_clear_match_text_preserves_team_orientation(label, home, away):
    # Synthetic clearly delimited labels, not proof of the real row's shape.
    html = f'''<table><tr data-dt="31,10,2026,09,00"><td>
      <a href="{MATCH_PATH}">{label}</a></td><td>1.40</td><td>2.80</td></tr></table>'''
    rows = betexplorer.parse_results_page(html, '2026-10-31', include_unpriced=True)
    assert [(row['player_home'], row['player_away']) for row in rows] == [(home, away)]


def test_invalid_link_does_not_expose_text_or_status_guess(monkeypatch, tmp_path):
    sample = _capture(monkeypatch, tmp_path, '''<tr data-dt="30,10,2026,13,05,secret">
       <td><a href="https://evil.example/tennis/a/b/c/d/">Private Person</a></td>
       <td>pending review</td><td>12:00</td></tr>''')
    ev = sample['row_evidence']
    assert ev['data_dt_components'] is None
    assert _texts(ev['cells'], 'name_like_text') == []
    assert _texts(ev['cells'], 'status_text') == []
    assert 'Private Person' not in json.dumps(sample)


def test_structural_capture_is_bounded_and_reports_omissions(monkeypatch, tmp_path):
    spans = ''.join('<span>Alice</span>' for _ in range(200))
    sample = _capture(monkeypatch, tmp_path, f'''<tr data-dt="30,10,2026,13,05">
       <td><a href="{MATCH_PATH}">{spans}</a></td></tr>''')
    ev = sample['row_evidence']
    assert ev['omissions']['bounded'] is True
    assert len(_texts(ev['cells'], 'name_like_text')) < 80
    assert sample['classification'] == 'unresolved'


def test_unrecognized_row_capture_retains_many_samples_with_a_bounded_limit(monkeypatch, tmp_path):
    monkeypatch.setenv('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR', str(tmp_path))
    row = BeautifulSoup(f'<tr data-dt="30,10,2026,13,05"><td><a href="{MATCH_PATH}">Alice</a></td></tr>',
                        'html.parser').find('tr')
    for _ in range(diagnostics.MAX_UNRECOGNIZED_ROW_SAMPLES + 10):
        diagnostics.capture_unrecognized_listing_row(row, '2026-10-30')
    retained = json.loads((tmp_path / 'betexplorer_unrecognized_rows.json').read_text())
    assert len(retained) == diagnostics.MAX_UNRECOGNIZED_ROW_SAMPLES == 50


@pytest.mark.parametrize('fallback_rows', [[], [{'match_url': MATCH_PATH, 'player_home': 'A', 'player_away': 'B'}]],
                         ids=['fallback-would-be-empty', 'fallback-would-return-rows'])
def test_all_unrecognized_dated_rows_never_let_fallback_erase_incompleteness(monkeypatch, fallback_rows):
    html = f'''<table><tr data-dt="31,10,2026,09,00"><td>Postponed</td>
       <td><a href="{MATCH_PATH}">Van Wyk Kris</a></td><td>-</td><td>-</td></tr></table>
       <p>No matches</p>'''
    calls = []
    monkeypatch.setattr(betexplorer, 'fetch_page_html', lambda *a, **k: html)
    monkeypatch.setattr(betexplorer, 'parse_listing_page',
                        lambda *a, **k: calls.append('fallback') or fallback_rows)
    metrics = {}
    rows, status = betexplorer._fetch_live('2026-10-31', with_status=True, metrics=metrics)
    assert rows == [] and status == 'partial' and metrics['unrecognized_rows'] == 1
    assert calls == []  # fallback results are not evidence that the dated row is resolved


def test_mixed_recognized_and_unrecognized_dated_rows_remain_partial(monkeypatch):
    html = f'''<table><tr data-dt="31,10,2026,09,00"><td>
       <a href="{MATCH_PATH}">Van Wyk Kris</a></td><td>-</td><td>-</td></tr>
       <tr data-dt="31,10,2026,10,00"><td><a href="/tennis/example/tournament/known/def456/">
       Alice A. - Beth B.</a></td><td>1.50</td><td>2.40</td></tr></table>'''
    monkeypatch.setattr(betexplorer, 'fetch_page_html', lambda *a, **k: html)
    monkeypatch.setattr(betexplorer, 'parse_listing_page',
                        lambda *a, **k: pytest.fail('unexpected generic fallback'))
    metrics = {}
    rows, status = betexplorer._fetch_live('2026-10-31', with_status=True, metrics=metrics)
    assert len(rows) == 1 and status == 'partial' and metrics['unrecognized_rows'] == 1
    assert rows[0]['_collection_partial'] is True


def test_generic_empty_marker_without_rows_stays_unverified(monkeypatch):
    monkeypatch.setattr(betexplorer, 'fetch_page_html', lambda *a, **k: '<html>No matches</html>')
    monkeypatch.setattr(betexplorer, 'parse_listing_page', lambda *a, **k: [])
    metrics = {}
    rows, status = betexplorer._fetch_live('2026-10-31', with_status=True, metrics=metrics)
    assert rows == [] and status == 'partial' and metrics['unrecognized_rows'] == 0
    assert metrics['empty_board_marker_contract'] == 'unverified'
    assert metrics['listing_coverage_status'] == 'not_established'


def test_credential_canaries_scripts_and_arbitrary_attributes_not_retained(monkeypatch, tmp_path):
    sample = _capture(monkeypatch, tmp_path, f'''<tr data-dt="30,10,2026,13,05"
       data-random="attrCanaryAlpha" data-session="sessionCanaryAlpha">
       <td><a href="{MATCH_PATH}?token=credentialCanaryAlpha" onclick="attrCanaryBeta">
       <span title="attrCanaryGamma">Name A.</span><script>scriptCanaryAlpha()</script>
       <style>scriptCanaryBeta</style><input value="cookieCanaryAlpha"></a></td>
       <td><iframe src="https://example.test/cookieCanaryBeta"></iframe></td></tr>''')
    output = json.dumps(sample)
    for value in ('attrCanaryAlpha', 'attrCanaryBeta', 'attrCanaryGamma', 'sessionCanaryAlpha',
                  'credentialCanaryAlpha', 'scriptCanaryAlpha', 'scriptCanaryBeta',
                  'cookieCanaryAlpha', 'cookieCanaryBeta'):
        assert value not in output


def test_cell_and_depth_limits_are_reported(monkeypatch, tmp_path):
    nested = '<span>' * 9 + 'Alice' + '</span>' * 9
    cells = ''.join('<td>extra</td>' for _ in range(13))
    sample = _capture(monkeypatch, tmp_path, f'''<tr data-dt="30,10,2026,13,05">
       <td><a href="{MATCH_PATH}">{nested}</a></td>{cells}</tr>''')
    ev = sample['row_evidence']
    assert len(ev['cells']) == 12
    assert ev['omissions']['bounded'] is True
    assert 'Alice' not in _texts(ev['cells'], 'name_like_text')
