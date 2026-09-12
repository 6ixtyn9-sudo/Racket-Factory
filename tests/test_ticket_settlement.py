import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

grade = module('auto_tickets_grade')
generate = module('auto_tickets')

def leg(day='2026-09-11'):
    return dict(match='Alexander Zverev vs Karen Khachanov', selected_player='Alexander Zverev', date=day)

def result(day='2026-09-11', winner='Alexander Zverev'):
    return dict(match_date=day, player_a='Alexander Zverev', player_b='Karen Khachanov', winner=winner)

def test_partial_weighted_idempotent():
    state = dict(bank=100, history=[], open_slips=[dict(date='2026-09-11', staked_pct=10, accas=[
        dict(type='single', odds=1.24, stake_pct=7, legs=[leg()]),
        dict(type='double', odds=3, stake_pct=3, legs=[leg('2026-09-12')]),
    ])])
    df = pd.DataFrame([result()])
    grade.settle_open_slips(state, df)
    assert state['bank'] == pytest.approx(101.68)
    assert len(state['history'][0]['accas']) == 1
    assert len(state['open_slips'][0]['accas']) == 1
    snapshot = json.dumps(state, sort_keys=True)
    grade.settle_open_slips(state, df)
    assert json.dumps(state, sort_keys=True) == snapshot
    grade.settle_open_slips(state, pd.DataFrame([result('2026-09-12', 'Karen Khachanov')]))
    assert state['bank'] == pytest.approx(98.68)
    assert state['open_slips'] == []
    assert len(state['history']) == 1
    assert state['history'][0]['complete'] is True

def test_results_are_date_scoped_and_fallback_final():
    assert grade.settle_leg(leg(), pd.DataFrame([result('2025-09-11')])) is None
    assert grade.settle_leg(leg(), pd.DataFrame([dict(result(), winner=None)]), pd.DataFrame([result()])) is True
    assert grade.settle_leg(leg(), pd.DataFrame([dict(result(), status='live')])) is None
    assert grade.settle_leg(leg(), pd.DataFrame([result(), result(winner='Karen Khachanov')])) is None

def test_reconstruction_frozen_saves_without_overwriting_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(generate, 'STATE_FILE', tmp_path / 'auto_tickets_state.json')
    archive = dict(date='2026-09-11', accas=[dict(odds=1.24, stake_pct=7, legs=[leg()])], staked_pct=7)
    (tmp_path / 'auto_tickets_2026-09-11.json').write_text(json.dumps(archive))
    state = dict(bank=100, history=[], open_slips=[dict(date='2026-09-12', accas=[])])
    generate.save_state(state)
    monkeypatch.setattr(generate, 'load_picks', lambda day: [])
    monkeypatch.setattr(generate, 'now_local', lambda: generate.datetime(2026, 9, 12, 12, tzinfo=generate.TZ))
    monkeypatch.setattr('sys.argv', ['auto_tickets.py', '--date', '2026-09-12'])
    generate.main()
    restored = generate.load_state()
    assert [s['date'] for s in restored['open_slips']] == ['2026-09-12', '2026-09-11']
    generate.main()
    assert generate.load_state() == restored
    assert not (tmp_path / 'auto_tickets_2026-09-12.json').exists()

def test_lost_leg_resolves_acca_with_pending_leg():
    state = dict(bank=100, history=[], open_slips=[dict(date='2026-09-11', accas=[
        dict(odds=4, stake_pct=2, legs=[leg(), leg('2026-09-12')])])])
    grade.settle_open_slips(state, pd.DataFrame([result(winner='Karen Khachanov')]))
    assert state['bank'] == 98
    assert not state['open_slips']

def test_tennisexplorer_paired_rows_and_no_guessed_winners():
    backfill = module('backfill_challenger_results')
    html = '''<table class="result"><tr><td class="t-name">Shanghai challenger</td></tr>
    <tr><td rowspan="2">12:00</td><td class="t-name"><a href="/player/a/">Player A</a></td><td class="result">0</td></tr>
    <tr><td class="t-name"><a href="/player/b/">Player B</a></td><td class="result">2</td></tr></table>'''
    rows = backfill.parse_tennisexplorer_results(html, '2026-09-11')
    assert len(rows) == 1
    assert rows[0]['winner'] == 'Player B'
    assert rows[0]['tour'] == 'CHALLENGER'
    assert not backfill.parse_tennisexplorer_results(html.replace('>2</td>', '>RET</td>'), '2026-09-11')
    assert not backfill.parse_tennisexplorer_results(html.replace('>2</td>', '>1</td>'), '2026-09-11')

def test_audit_loads_results_without_warehouse(tmp_path):
    import sys
    spec = importlib.util.spec_from_file_location('audit_test', ROOT / 'scripts/audit_recent_picks.py')
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    pd.DataFrame([result()]).to_csv(tmp_path / 'challenger_results_tennis_2026-09.csv.gz', index=False)
    assert len(audit.load_warehouse_df(tmp_path / 'missing.csv.gz')) == 1
