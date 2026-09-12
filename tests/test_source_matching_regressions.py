"""September settlement investigation: identity, date/price capture and finality."""
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from racketfactory.matching import names_match
from racketfactory.sources.forebet import ForebetPredictor
from racketfactory.sources import foretennis
from racketfactory.sources.tennisexplorer import parse_daily, load_prematch_odds
from racketfactory.results import foretennis_winner_side
from racketfactory import warehouse
from scripts import audit_recent_picks as audit, auto_tickets_grade as grade
from scripts.backfill_forebet import _copy_forebet_result_fields, _forebet_result_rows_from_predictions
from scripts.backfill_foretennis import oriented_actual_result
from scripts.mine_edges import selected_odds_is_usable, _split_match_text_for_odds

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('full,short', [
    ('Vitaliy Sachko', 'Sachko V.'), ('Ilya Ivashka', 'Ivashka I.'),
    ('Joao Lucas Reis da Silva', 'Reis Da Silva J.'),
    ('Genaro Alberto Olivieri', 'Olivieri G.'),
    ('Noma Noha Akugue', 'Noha Akugue N.'),
    ('Guiomar Maristany Zuleta De Reales', 'Maristany Zuleta De Reales G.'),
    ('Cascino / Feng', 'Feng S. & Cascino E.'),
])
def test_source_identity(full, short):
    assert names_match(full, short)
    assert names_match(short, full)
    assert audit.names_match(full, short) and grade.names_match(full, short)

@pytest.mark.parametrize('a,b', [
    ('A. Zverev','M. Zverev'), ('Alexander Zverev','Mischa Zverev'),
    ('Feng / Cascino','Feng / Papamichail'), ('Feng / Cascino','Feng'),
    ('Juan Manuel Cerundolo', 'Francisco Cerundolo'),
])
def test_no_shared_surname_or_partial_team_false_match(a,b):
    assert not names_match(a,b)


def forebet_html(date='09/11/2026 6:15 AM', status='FT'):
    # Reduced schema fixture, with values checked against the public Tulln row.
    return f'''<div class="rcnt"><a class="tnmscn" href="/en/tennis/matches/challenger-men/tulln/m-soto-v-sachko/358376">
    <span class="homeTeam">M. Soto</span><span class="awayTeam">V. Sachko</span><span class="date_bah">{date}</span></a>
    <div class="fprc"><span>46</span><span>54</span></div>
    <div class="predict"><span class="forepr"><span>2</span></span></div>
    <div class="haodd"><span>+175</span><span>-250</span></div>
    <div class="predQ"><div class="fj_column"><span>3</span><span>6</span></div>
    <div class="fj_column"><span>6</span><span>1</span></div>
    <div class="fj_column"><span>2</span><span>6</span></div></div>{status}</div>'''

@pytest.mark.parametrize('date', ['09/11/2026 6:15 AM','11/09/2026 06:15'])
def test_forebet_dates_odds_and_results_survive_pipeline(date):
    row = ForebetPredictor().parse_page(forebet_html(date))[0]
    assert row['match_date'] == '2026-09-11'
    assert (row['odds_home'],row['odds_away']) == (2.75,1.4)
    assert row['result_winner'] == '2'
    result = _copy_forebet_result_fields(dict(match_date=row['match_date'],player_a='M. Soto',player_b='V. Sachko'),row,'M. Soto','V. Sachko')
    df = _forebet_result_rows_from_predictions([result])
    assert grade.settle_leg(dict(date='2026-09-11',match='Matias Soto vs Vitaliy Sachko',selected_player='Vitaliy Sachko'),df) is True


def test_forebet_in_progress_is_not_a_result():
    row = ForebetPredictor().parse_page(forebet_html(status='LIVE'))[0]
    out = _copy_forebet_result_fields(dict(match_date=row['match_date'],player_a='M. Soto',player_b='V. Sachko'),row,'M. Soto','V. Sachko')
    assert _forebet_result_rows_from_predictions([out]).empty


def test_challenger_text_is_not_cloudflare(monkeypatch):
    monkeypatch.setattr(foretennis.requests,'get',lambda *a,**k: SimpleNamespace(status_code=200,text='<p>Challenger Shanghai</p>'))
    assert foretennis._fetch_page('https://example.test') is not None


def test_named_api_prices_not_swapped_to_agree_with_prediction(monkeypatch):
    monkeypatch.setattr(warehouse,'fetch_the_odds_api_rows',lambda day:[dict(match_date=day,player_home='Brunold M.',player_away='Heide G.',odds_home=2.99,odds_away=1.36,source='TheOddsAPI')])
    card=pd.DataFrame([dict(match_date='2026-09-11',player_home='Mika Brunold',player_away='Gustavo Heide',prob_home=85,prob_away=15)])
    out=warehouse.enrich_live_card_with_api_odds(card,'2026-09-11').iloc[0]
    assert out.odds_home==2.99 and out.odds_away==1.36
    odds,reason=selected_odds_is_usable(pd.Series(dict(_is_live=True,_odds_source='TheOddsAPI',odds_a=2.99,odds_b=1.36)),'player_a',.85)
    assert odds==2.99 and reason is None


def test_verified_result_snapshot_settles_missing_acca_legs():
    rows=json.loads((ROOT/'tests/fixtures/tennisexplorer_verified_2026-09-11_12.json').read_text())
    df=pd.DataFrame(rows)
    leg=dict(date='2026-09-11',match='Elsa Jacquemot vs Anna Blinkova',selected_player='Anna Blinkova')
    assert grade.settle_leg(leg,df) is True # overnight source date is September 12
    leg=dict(date='2026-09-11',match='Brancaccio / Papamichail vs Cascino / Feng',selected_player='Cascino / Feng')
    assert grade.settle_leg(leg,df) is True
    # Reference prices must not become hindsight pick prices.
    pick=dict(leg,bucket='WATCHLIST_NO_ODDS',odds=None)
    settled=audit.settle_pick(pick,df)
    assert settled.won and settled.odds is None and settled.pnl is None


def test_doubles_use_full_title_and_ignore_second_date_table():
    table='''<table class="result"><tr><td class="t-name">Montreux WTA</td></tr>
    <tr><td rowspan="2">20:10</td><td><a href="/doubles-team/cascino/feng/" title="Cascino E. / Feng S.">Cascino E / Feng S.</a></td><td>2</td><td class="course">1.63</td><td class="course">2.15</td><td><a href="/match-detail/?id=3320653">info</a></td></tr>
    <tr><td><a href="/doubles-team/brancaccio/papamichail/" title="Brancaccio N. / Papamichail D.">Brancacci / Papamicha</a></td><td>0</td></tr></table>'''
    rows=parse_daily(table+table,'2026-09-11')
    assert len(rows)==1
    assert rows[0]['player_b']=='Brancaccio N. / Papamichail D.'
    assert rows[0]['odds_a']==1.63 and rows[0]['price_kind']=='result_reference'
    assert parse_daily(table,'2026-09-11',completed=False)==[]


def test_foretennis_zero_and_side_orientation():
    assert foretennis_winner_side('03')=='player_b'
    assert foretennis_winner_side('2.0') is None # ambiguous legacy coercion, no invented winner
    assert foretennis_winner_side('21.0')=='player_a'
    assert oriented_actual_result(dict(player_home='M. Soto',actual_result='12'),'V. Sachko')=='21'


def test_oddsportal_match_text_accepts_vs():
    assert _split_match_text_for_odds('Matias Soto vs Vitaliy Sachko')==('Matias Soto','Vitaliy Sachko')


def test_reviewed_burillo_alias_not_generic_surname_guess():
    assert names_match('Irene Burillo','Burillo Escorihuela I.')
    assert not names_match('Irene Burillo','Burillo Escorihuela M.')


def test_forebet_reversed_result_score_stays_with_players():
    p=ForebetPredictor().parse_page(forebet_html())[0]
    row=_copy_forebet_result_fields({},p,'V. Sachko','M. Soto')
    assert row['result_winner']=='player_a'
    assert row['result_score']=='6-3 1-6 6-2'


def test_stale_reference_odds_cannot_price_live_picks(tmp_path):
    rows=json.loads((ROOT/'tests/fixtures/tennisexplorer_verified_2026-09-11_12.json').read_text())
    (tmp_path/'tennisexplorer_odds_2026-09-11.json').write_text(json.dumps(rows))
    assert load_prematch_odds(tmp_path,'2026-09-11') == []


def test_predix_decimal_probabilities_and_no_refresh_date_relabelling(monkeypatch):
    from racketfactory.sources import predixsport
    index='<p>Last updated: 2026-09-11</p><a href="/tennis/a_vs_b.html">analysis</a>'
    detail='<h2 class="player-name">Alexander Zverev</h2><h2 class="player-name">Karen Khachanov</h2><div class="win-probability">70.5%</div><div class="win-probability">29.5%</div><div class="main-content">37.4 Games 11.4 Aces 8.3 Aces</div>'
    monkeypatch.setattr(predixsport.requests,'get',lambda url,**kw:SimpleNamespace(status_code=200,text=index if url.endswith('tennis_predictions') else detail))
    row=predixsport.PredixSportPredictor().fetch_daily()[0]
    assert row['match_date']=='2026-09-11'
    assert row['prob_home']==70.5
    assert row['odds_home'] is None and row['odds_away'] is None
    assert row['date_confidence']=='LOW'


def test_forebet_daily_capture_end_to_end_retains_correct_price_sides(tmp_path,monkeypatch):
    from scripts import backfill_forebet as backfill
    parsed=ForebetPredictor().parse_page(forebet_html())
    stub=ForebetPredictor()
    monkeypatch.setattr(stub,'fetch_daily_predictions',lambda day:parsed)
    monkeypatch.setattr(backfill,'ForebetPredictor',lambda:stub)
    wh=tmp_path/'warehouse.csv.gz'
    pd.DataFrame([dict(match_date='2026-09-11',tour='CHALLENGER',tournament='Tulln',player_a='M. Soto',player_b='V. Sachko')]).to_csv(wh,index=False)
    args=SimpleNamespace(warehouse=str(wh),output_dir=str(tmp_path),days=['yesterday'],delay=0)
    assert backfill.mode_daily(args)==0
    df=pd.read_csv(tmp_path/'forebet_results_tennis_2026-09.csv.gz')
    assert df.iloc[0]['odds_a']==2.75 and df.iloc[0]['odds_b']==1.4
    assert df.iloc[0]['winner']=='V. Sachko'
    health=json.loads((tmp_path/'source_capture_forebet.json').read_text())
    assert health['pages'][0]['with_date']==1 and health['pages'][0]['finished']==1


def test_warehouse_live_rebuild_cannot_erase_finished_match(tmp_path, monkeypatch):
    record=dict(match_date='2026-09-11',tour='CHALLENGER',tournament='Tulln',
                player_a='M. Soto',player_b='V. Sachko',winner='V. Sachko',score='12',source='TennisExplorer_results',_is_live=False)
    pd.DataFrame([record]).to_csv(tmp_path/'challenger_results_tennis_2026-09.csv.gz',index=False)
    monkeypatch.setattr(warehouse,'build_live_rows',lambda:pd.DataFrame([dict(record,winner='',score='',_is_live=True,source='BetClan')]))
    monkeypatch.setattr(warehouse,'build_wta_official_result_rows',lambda path:pd.DataFrame())
    path=warehouse.build_warehouse(data_dir=tmp_path,inject_live=True)
    df=pd.read_csv(path)
    assert len(df)==1 and df.iloc[0]['winner']=='V. Sachko'
