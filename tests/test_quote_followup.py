"""Offline regressions; the doubles names below are log-derived, not raw HTML."""
import json
from datetime import datetime, timedelta

import pandas as pd

from racketfactory import warehouse
from racketfactory.quote_enrichment import collect_prediction_comparisons
from racketfactory.sources import betexplorer
from scripts import auto_tickets as tickets
from scripts.validate_ticket_publication import check_card

DAY = '2026-10-29'


def test_log_derived_doubles_members_are_not_flattened_or_proved_by_bare_surname():
    assert warehouse.surname_tokens('Galarneau A. / Hach Verdugo H.') == ('galarneau', 'hach verdugo')
    assert warehouse.surname_tokens('Galarneau / Verdugo') == ('galarneau', 'verdugo')
    # The log provides no given name for Verdugo; a compound surname match is
    # useful for discovery diagnostics but is not proof of a bookmaker quote.
    assert not warehouse.strict_quote_team_match('Galarneau / Verdugo', 'Galarneau A. / Hach Verdugo H.')
    assert warehouse.strict_quote_team_match('Galarneau A. / Hach Verdugo H.', 'Hach Verdugo H. / Galarneau A.')
    assert not warehouse.strict_quote_team_match('Galarneau A. / Galarneau A.', 'Galarneau A. / Galarneau A.')


def test_later_candidate_matched_and_unmatched_never_borrows_first_price():
    fixtures = [
        {'match_date': DAY, 'player_home': 'Adam Altmaier', 'player_away': 'Jiri Lehecka', 'odds_home': 1.4, 'odds_away': 3.1},
        {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'odds_home': 1.6, 'odds_away': 2.4},
    ]
    card = pd.Series({'match_date': DAY, 'player_home': 'Beth Brown', 'player_away': 'Alice Adams'})
    row, reverse = warehouse._match_api_odds_row(card, fixtures)
    assert row is fixtures[1] and reverse
    missing = pd.Series({'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies'})
    assert warehouse._match_api_odds_row(missing, fixtures) == (None, False)
    enriched = warehouse.enrich_live_card_with_api_odds(pd.DataFrame([missing]), DAY, supplied_odds=[], supplied_comparisons=fixtures)
    assert pd.isna(enriched.iloc[0]['odds_home']) and pd.isna(enriched.iloc[0]['odds_away'])


def test_shared_targeted_collection_partial_and_limit(monkeypatch):
    fixtures = [{'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'abc'},
                {'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies', 'event_id': 'def'}]
    calls = []
    monkeypatch.setattr(betexplorer, 'MAX_COMPARISON_FIXTURES', 1)
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda f, **kwargs: calls.append(f['event_id']) or [])
    offers, counts, partial = collect_prediction_comparisons(DAY, fixtures, fixtures, 'complete')
    assert calls == ['abc'] and not offers and partial
    assert counts['matched_fixtures'] == 2 and counts['attempted'] == 1 and counts['limit_exceeded'] == 1


def test_independent_price_classes_overlap_with_quote_failures(monkeypatch):
    now = datetime.fromisoformat(DAY + 'T10:00:00+02:00')
    monkeypatch.setattr(tickets, '_ML_AVAILABLE', False)
    base = {'match': 'Alice Adams vs Beth Brown', 'selected_player': 'Alice Adams', 'selected_side': 'player_a',
            'date': DAY, 'kickoff': '12:00', 'bucket': 'WATCHLIST'}
    picks = [dict(base, odds=None), dict(base, odds='nan'), dict(base, odds=1.2),
             dict(base, odds=1.4, selected_quote={'odds': 1.4, 'selection': 'Alice Adams'})]
    diag = tickets.independent_pick_diagnostics(picks, DAY, now)
    assert diag['price_classes'] == {'missing': 1, 'malformed_or_nonfinite': 1, 'below_floor': 1, 'at_or_above_floor': 1}
    assert diag['overlapping_reasons']['selected_quote_missing_or_malformed'] == 3
    assert diag['overlapping_reasons']['market_missing'] == 1
    assert diag['overlapping_reasons']['period_missing'] == 1
    assert not tickets.is_playable(dict(base, odds=1.4), require_provenance=True)


def test_publication_rechecks_time_and_never_revalidates_retained_card(monkeypatch):
    start = datetime.fromisoformat(DAY + 'T10:00:00+02:00')
    quote = {'odds': 1.5, 'selection': 'Alice Adams', 'source': 'BetExplorer', 'bookmaker': 'Book One',
             'market': 'h2h', 'period': 'match', 'price_type': 'bookmaker',
             'settlement_rules': {'retirement': 'advancer', 'walkover': 'void', 'cancelled': 'void'},
             'observed_at': start.isoformat(), 'provider_home': 'Alice Adams', 'provider_away': 'Beth Brown',
             'provider_side': '1', 'event_id': 'abc', 'fixture_date': DAY}
    leg = {'odds': 1.5, 'selected_player': 'Alice Adams', 'odds_source': 'BetExplorer',
           'odds_bookmaker': 'Book One', 'date': DAY, 'kickoff': '10:15', 'selected_quote': quote}
    card = json.dumps({'date': DAY, 'collection_observed_at': start.isoformat(), 'accas': [{'legs': [leg, leg]}]}).encode()
    monkeypatch.setattr('scripts.validate_ticket_publication.now_local', lambda: start + timedelta(minutes=10))
    assert check_card(card, None, DAY) == ''
    monkeypatch.setattr('scripts.validate_ticket_publication.now_local', lambda: start + timedelta(minutes=16))
    assert check_card(card, None, DAY) == 'kickoff passed or unknown'
    assert check_card(card, card, DAY) == ''  # unchanged ledger remains historical
    monkeypatch.setattr('scripts.validate_ticket_publication.now_local', lambda: start + timedelta(minutes=31))
    assert 'stale' in check_card(card, None, DAY)


def test_normal_targeted_detail_budget_and_changed_fixture_set_fail_closed(monkeypatch, tmp_path):
    from racketfactory import quote_enrichment as qe
    from racketfactory import fetch_cache
    from racketfactory.sources import betclan, oddsportal_upcoming
    monkeypatch.setattr(betclan, 'current_run_status', lambda _: 'complete')
    (tmp_path / 'localdata').mkdir()
    monkeypatch.setattr(qe, '__file__', str(tmp_path / 'src/racketfactory/quote_enrichment.py'))
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', DAY + 'T10:00:00+02:00')
    monkeypatch.delenv(betexplorer.DISABLE_ENV, raising=False)
    cache = {}
    def once(key, fn, **kwargs):
        if key not in cache:
            cache[key] = fn()
        return cache[key]
    monkeypatch.setattr(fetch_cache, 'cached_fetch', once)
    fixture = {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'abc'}
    calls = []
    monkeypatch.setattr(betexplorer, '_fetch_live', lambda *a, **kw: (calls.append('listing') or [fixture], 'complete'))
    offer = {**fixture, 'odds_home': 1.5, 'odds_away': 2.5, 'source': 'BetExplorer', 'bookmaker': 'Book One',
             'price_type': 'bookmaker', 'settlement_rules': {'retirement': 'advancer', 'walkover': 'void', 'cancelled': 'void'},
             'market': 'h2h', 'period': 'match', 'observed_at': datetime.now(tickets.TZ).isoformat()}
    monkeypatch.setattr(qe, 'collect_prediction_comparisons', lambda *a: ([offer], {'matched_fixtures': 1}, False))
    monkeypatch.setattr(oddsportal_upcoming, 'fetch_oddsportal_upcoming_rows', lambda _: [])
    assert qe.targeted_comparison_rows(DAY, [fixture])
    assert qe.targeted_comparison_rows(DAY, [fixture])
    assert calls == ['listing']
    status = json.loads((tmp_path / 'localdata' / f'quote_collection_{DAY}.json').read_text())
    assert status['status'] == 'complete'
    other = dict(fixture, player_home='Carol Clark')
    qe.targeted_comparison_rows(DAY, [other])
    status = json.loads((tmp_path / 'localdata' / f'quote_collection_{DAY}.json').read_text())
    assert status['status'] == 'partial' and status['fixture_set_changed']
    assert calls == ['listing']


def test_changed_empty_card_requires_fresh_collection(monkeypatch):
    start = datetime.fromisoformat(DAY + 'T10:00:00+02:00')
    monkeypatch.setattr('scripts.validate_ticket_publication.now_local', lambda: start + timedelta(minutes=31))
    empty = json.dumps({'date': DAY, 'accas': [], 'collection_observed_at': start.isoformat()}).encode()
    assert check_card(empty, None, DAY) == 'collection observation stale or future'
    assert check_card(empty, empty, DAY) == ''


def test_doctor_distinguishes_current_failed_evaluation_from_saved_card(monkeypatch, tmp_path):
    from racketfactory import doctor
    monkeypatch.setattr(doctor, 'LOCALDATA', tmp_path)
    monkeypatch.setenv('GITHUB_RUN_ID', '12345')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    monkeypatch.delenv('RACKET_FACTORY_RUN_AS_OF', raising=False)
    (tmp_path / f'auto_tickets_{DAY}.json').write_text(json.dumps({
        'date': DAY, 'generated_at': DAY + 'T08:00:00+02:00', 'accas': [],
        'diagnostics': {'playable': 4, 'builder_input': 4}}))
    (tmp_path / f'auto_tickets_evaluation_{DAY}.json').write_text(json.dumps({
        'date': DAY, 'github_run_id': '12345', 'github_run_attempt': '2',
        'evaluated_at': DAY + 'T10:20:00+02:00', 'run_as_of': DAY + 'T10:00:00+02:00',
        'collection_status': 'partial', 'publication': 'retained_previous_card',
        'diagnostics': {'playable': 0, 'builder_input': 0}}))
    checks = doctor.run_health_checks(as_of=DAY)
    assert checks['tickets:current_evaluation']['status'] == 'CRITICAL'
    assert 'current run' in checks['tickets:current_evaluation']['detail']
    assert 'saved ticket snapshot generated' in checks['tickets:stages']['detail']


def test_betclan_required_status_is_run_scoped(monkeypatch, tmp_path):
    from racketfactory.sources import betclan
    path = tmp_path / 'betclan_status.json'
    monkeypatch.setattr(betclan, '_run_status_path', lambda: path)
    monkeypatch.setenv('RACKET_FACTORY_RUN_AS_OF', 'run-one')
    betclan._record_run_status('partial', 3, {'detail_requests': 5})
    assert betclan.current_run_status('run-one') == 'partial'
    assert betclan.current_run_status('run-two') == 'unknown'
    betclan._record_run_status('complete', 3, {'detail_requests': 5})
    assert betclan.current_run_status('run-one') == 'complete'


def test_detail_budget_prioritizes_upcoming_and_never_fetches_known_started(monkeypatch):
    """Synthetic kickoff metadata; exclusions never make the source complete."""
    from racketfactory import quote_enrichment as qe
    from racketfactory.kickoff import SAST
    now = datetime(2026, 10, 29, 10, 0, tzinfo=SAST)
    monkeypatch.setattr(qe, '_collection_now', lambda: now)
    fixtures = [
        {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'future', 'match_time': '11:00'},
        {'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies', 'event_id': 'started', 'match_time': '09:00'},
        {'match_date': DAY, 'player_home': 'Erin Evans', 'player_away': 'Fiona Ford', 'event_id': 'unknown', 'match_time': '00:00'},
        {'match_date': DAY, 'player_home': 'Gina Green', 'player_away': 'Helen Hall', 'event_id': 'conflict', 'match_time': '09:00'},
    ]
    predictions = fixtures + [dict(fixtures[3], match_time='12:00')]
    calls = []
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda f, **kw: calls.append(f['event_id']) or [])
    _, counts, partial = qe.collect_prediction_comparisons(DAY, predictions, fixtures, 'complete')
    assert calls == ['future', 'unknown']
    assert counts['matched_fixtures'] == 4
    assert counts['skipped_started_fixtures'] == 1
    assert counts['skipped_conflicting_kickoff_fixtures'] == 1
    assert partial


def test_detail_skips_match_that_starts_while_earlier_requests_run(monkeypatch):
    from racketfactory import quote_enrichment as qe
    from racketfactory.kickoff import SAST
    start = datetime(2026, 10, 29, 10, 0, tzinfo=SAST)
    times = iter([start, start, start + timedelta(minutes=40)])
    monkeypatch.setattr(qe, '_collection_now', lambda: next(times))
    fixtures = [
        {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'first', 'match_time': '10:30'},
        {'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies', 'event_id': 'later', 'match_time': '10:35'},
    ]
    # The second fixture passes its kickoff while the first fetch runs.
    calls = []
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda f, **kw: calls.append(f['event_id']) or [])
    _, counts, partial = qe.collect_prediction_comparisons(DAY, fixtures, fixtures, 'complete')
    assert calls == ['first'] and counts['skipped_started_fixtures'] == 1 and partial


def test_coverage_excludes_started_only_and_still_requires_all_relevant(monkeypatch):
    from datetime import timezone
    from racketfactory import quote_enrichment as qe
    from racketfactory.kickoff import SAST
    monkeypatch.setattr(qe, '_collection_now', lambda: datetime(2026, 10, 29, 10, 0, tzinfo=SAST))
    fixtures = [
        {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'future', 'match_time': '11:00'},
        {'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies', 'event_id': 'started', 'match_time': '09:00'},
    ]
    def quote(f, **kwargs):
        return [dict(f, odds_home=1.5, odds_away=2.5, market='match_winner', period='match',
                     observed_at=datetime.now(timezone.utc).isoformat(), price_type='bookmaker', bookmaker='Synthetic Book',
                     settlement_rules={'retirement':'advancer', 'walkover':'void', 'cancelled':'void'})]
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', quote)
    _, counts, partial = qe.collect_prediction_comparisons(DAY, fixtures, fixtures, 'complete')
    assert not partial
    assert (counts['matched_fixtures'], counts['excluded_started_fixtures'], counts['relevant_upcoming_fixtures'], counts['fresh_settlement_compatible_fixtures']) == (2, 1, 1, 1)
    assert counts['unresolved_fixtures'] == 0
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: [])
    _, counts, partial = qe.collect_prediction_comparisons(DAY, fixtures, fixtures, 'complete')
    assert partial and counts['unresolved_fixtures'] == 1


def test_unknown_kickoff_is_unresolved_even_with_a_compatible_quote(monkeypatch):
    from datetime import timezone
    from racketfactory import quote_enrichment as qe
    fixture = {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'future', 'match_time': '00:00'}
    row = dict(fixture, odds_home=1.5, odds_away=2.5, market='match_winner', period='match',
               observed_at=datetime.now(timezone.utc).isoformat(), price_type='bookmaker', bookmaker='Synthetic Book',
               settlement_rules={'retirement':'advancer', 'walkover':'void', 'cancelled':'void'})
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: [row])
    _, counts, partial = qe.collect_prediction_comparisons(DAY, [fixture], [fixture], 'complete')
    assert partial and counts['unknown_or_conflicting_kickoff_fixtures'] == 1
    assert counts['excluded_started_fixtures'] == 0 and counts['unresolved_fixtures'] == 1


def test_coverage_counts_unmatched_predictions_and_budget_exhaustion(monkeypatch):
    from racketfactory import quote_enrichment as qe
    from racketfactory.kickoff import SAST
    monkeypatch.setattr(qe, '_collection_now', lambda: datetime(2026, 10, 29, 10, 0, tzinfo=SAST))
    monkeypatch.setattr(betexplorer, 'MAX_COMPARISON_FIXTURES', 1)
    fixtures = [
        {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'a', 'match_time': '11:00'},
        {'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies', 'event_id': 'b', 'match_time': '12:00'},
    ]
    extra = {'match_date': DAY, 'player_home': 'Erin Evans', 'player_away': 'Fiona Ford', 'match_time': '13:00'}
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', lambda *a, **kw: [])
    _, counts, partial = qe.collect_prediction_comparisons(DAY, fixtures + [dict(fixtures[0]), extra], fixtures, 'complete')
    assert partial and counts['predictions'] == 4 and counts['matched_fixtures'] == 2
    assert counts['unmatched_or_ambiguous'] == 1 and counts['limit_exceeded'] == 1
    assert counts['unexamined_fixtures'] == 1 and counts['unresolved_fixtures'] == 2


def test_started_during_previous_fetch_is_excluded_from_verified_denominator(monkeypatch):
    from datetime import timezone
    from racketfactory import quote_enrichment as qe
    from racketfactory.kickoff import SAST
    start = datetime(2026, 10, 29, 10, 0, tzinfo=SAST)
    times = iter([start, start, start + timedelta(minutes=40)])
    monkeypatch.setattr(qe, '_collection_now', lambda: next(times))
    fixtures = [
        {'match_date': DAY, 'player_home': 'Alice Adams', 'player_away': 'Beth Brown', 'event_id': 'first', 'match_time': '10:30'},
        {'match_date': DAY, 'player_home': 'Carla Clark', 'player_away': 'Diana Davies', 'event_id': 'later', 'match_time': '10:35'},
    ]
    calls = []
    def quote(f, **kwargs):
        calls.append(f['event_id'])
        return [dict(f, odds_home=1.5, odds_away=2.5, market='h2h', period='match',
                     observed_at=datetime.now(timezone.utc).isoformat(), price_type='bookmaker', bookmaker='Synthetic Book',
                     settlement_rules={'retirement':'advancer', 'walkover':'void', 'cancelled':'void'})]
    monkeypatch.setattr(betexplorer, 'fetch_fixture_comparison', quote)
    _, counts, partial = qe.collect_prediction_comparisons(DAY, fixtures, fixtures, 'complete')
    assert calls == ['first'] and not partial
    assert counts['excluded_started_fixtures'] == 1 and counts['relevant_upcoming_fixtures'] == 1
    assert counts['fresh_settlement_compatible_fixtures'] == 1
