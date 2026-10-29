"""Regression tests use temporary ledgers only; never replay into localdata."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import auto_tickets_grade as grade
from scripts import daily
from racketfactory import doctor, ml


def test_retained_legacy_event_reports_without_mutating_it(tmp_path):
    retained = json.loads((Path(__file__).resolve().parents[1] / 'localdata/auto_tickets_state.json').read_text())
    original = copy.deepcopy(retained)
    grade.write_performance(retained, output_dir=tmp_path)
    result = json.loads((tmp_path / 'auto_tickets_performance.json').read_text())
    assert retained == original
    assert result['events'] == original['events']
    assert 'execution_cycle_reset' in (tmp_path / 'auto_tickets_performance.txt').read_text()
    assert result['bank_pct'] == original['bank']


def _setup(monkeypatch, tmp_path):
    state = {'bank': 190.0, 'paper_bank': 100.0, 'cycle_base': 100.0,
             'history': [], 'events': [{'event': 'execution_cycle_reset', 'at': 'before'}],
             'open_slips': [{'date': '2026-10-29', 'accas': [
                 {'paper': False, 'odds': 2, 'stake_pct': 20, 'legs': [{'match': 'won'}]},
             ]}, {'date': '2026-10-30', 'accas': [
                 {'paper': True, 'odds': 2, 'stake_pct': 5, 'legs': [{'match': 'pending'}]},
             ]}]}
    monkeypatch.setattr(grade, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(grade, 'STATE_FILE', tmp_path / 'state.json')
    (tmp_path / 'state.json').write_text(json.dumps(state))
    monkeypatch.setattr(grade, 'load_warehouse', lambda: pd.DataFrame())
    monkeypatch.setattr(grade, 'load_additional_results', lambda: pd.DataFrame())
    monkeypatch.setattr(grade, 'settle_leg', lambda leg, *a, **kw: 'WON' if leg['match'] == 'won' else 'PENDING')
    monkeypatch.setattr(grade, 'acca_execution_safe', lambda acca: not acca['paper'])
    return state


def test_render_failure_keeps_original_ledger_and_retry_is_idempotent(monkeypatch, tmp_path):
    original = _setup(monkeypatch, tmp_path)
    render = grade.write_performance
    def fail(*args, **kwargs):
        raise OSError('simulated reporting failure')
    monkeypatch.setattr(grade, 'write_performance', fail)
    with pytest.raises(OSError):
        grade.main([])
    assert json.loads((tmp_path / 'state.json').read_text()) == original
    assert not (tmp_path / 'auto_tickets_performance.json').exists()
    monkeypatch.setattr(grade, 'write_performance', render)
    assert grade.main([]) == 0
    first = json.loads((tmp_path / 'state.json').read_text())
    assert first['bank'] == 210 and len(first['history']) == 1 and len(first['events']) == 2
    assert len(first['open_slips'][0]['accas']) == 1
    assert grade.main([]) == 0
    assert json.loads((tmp_path / 'state.json').read_text()) == first


def test_publish_failure_after_commit_does_not_double_settle(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    replace = grade.os.replace
    def fail_json(src, dest):
        if str(dest).endswith('auto_tickets_performance.json'):
            raise OSError('simulated publication failure')
        return replace(src, dest)
    monkeypatch.setattr(grade.os, 'replace', fail_json)
    with pytest.raises(OSError):
        grade.main([])
    first = json.loads((tmp_path / 'state.json').read_text())
    assert first['bank'] == 210 and len(first['events']) == 2
    monkeypatch.setattr(grade.os, 'replace', replace)
    assert grade.main([]) == 0
    assert json.loads((tmp_path / 'state.json').read_text()) == first


def test_same_date_existing_history_does_not_drop_open_slip(monkeypatch, tmp_path):
    state = _setup(monkeypatch, tmp_path)
    state['history'] = [{'date': '2026-10-29', 'bank_pct': 190, 'accas': []}]
    before = copy.deepcopy(state['open_slips'])
    grade.settle_open_slips(state, pd.DataFrame())
    assert state['open_slips'] == before
    assert state['bank'] == 190


def test_grade_failure_marker_and_doctor_health(monkeypatch, tmp_path):
    monkeypatch.setattr(daily, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(doctor, 'LOCALDATA', tmp_path)
    monkeypatch.setattr(daily.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=7))
    with pytest.raises(SystemExit) as exc:
        daily.run_critical_grade('unused', env={}, run_as_of='2026-10-29T12:00:00+02:00')
    assert exc.value.code == 7
    status = json.loads((tmp_path / 'auto_tickets_grade_status.json').read_text())
    assert status['status'] == 'failed'
    assert doctor.run_health_checks(as_of='2026-10-29')['tickets:grading']['status'] == 'CRITICAL'


def test_ml_does_not_prefer_stale_performance(monkeypatch, tmp_path):
    monkeypatch.setattr(ml, 'LOCALDATA', tmp_path)
    (tmp_path / 'auto_tickets_state.json').write_text(json.dumps({'bank': 100, 'paper_bank': 100, 'events': [], 'history': [], 'open_slips': [1]}))
    (tmp_path / 'auto_tickets_performance.json').write_text(json.dumps({'bank_pct': 130.168518, 'events': [], 'history': [], 'open_slips': []}))
    health = ml.monitor_performance()
    assert health['bank_pct'] == 100
    assert 'stale' in health['performance_source_status']


def test_grade_marker_from_a_previous_workflow_attempt_is_not_current(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, 'LOCALDATA', tmp_path)
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    (tmp_path / 'auto_tickets_grade_status.json').write_text(json.dumps(
        {'status': 'ok', 'github_run_id': '123', 'github_run_attempt': '1'}))
    assert doctor.run_health_checks(as_of='2026-10-29')['tickets:grading']['status'] == 'CRITICAL'



def test_historical_backfill_not_in_daily_autobets_path():
    source = (Path(__file__).resolve().parents[1] / 'scripts' / 'daily.py').read_text()
    assert 'scripts/backfill_bzzoiro.py' not in source
    assert 'scripts/backfill_forebet.py' not in source
