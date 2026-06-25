from datetime import date

from racketfactory.oddsportal import normalize_rows, parse_date, parse_oddsportal_date_header, parse_rendered_html, to_decimal


def test_parse_date_variants():
    assert parse_date("2026-06-24") == "2026-06-24"
    assert parse_date("24.06.2026") == "2026-06-24"


def test_normalize_rows_contract():
    rows = normalize_rows([{
        "date": "2026-06-24",
        "tour": "atp",
        "tournament": "Halle",
        "player_a": "Jannik Sinner",
        "player_b": "Carlos Alcaraz",
        "winner": "A",
        "odds_a": "1.80",
        "odds_b": "2.05",
    }])
    assert len(rows) == 1
    assert rows[0]["tour"] == "ATP"
    assert rows[0]["winner"] == "Jannik Sinner"
    assert rows[0]["odds_a"] == 1.8
    assert "bookmaker" in rows[0]


def test_parse_rendered_html_simple_event_row():
    html = """
    <div class="eventRow">
      <span class="participant">Jannik Sinner</span>
      <span class="participant">Carlos Alcaraz</span>
      <span>2026-06-24</span>
      <span>1.80</span><span>2.05</span>
    </div>
    """
    rows = parse_rendered_html(html, default_date="2026-06-24", tour="ATP", tournament="Test Open")
    assert len(rows) == 1
    assert rows[0]["player_a"] == "Jannik Sinner"
    assert rows[0]["player_b"] == "Carlos Alcaraz"
    assert rows[0]["tour"] == "ATP"


def test_american_odds_conversion():
    assert to_decimal("+200") == 3.0
    assert to_decimal("-250") == 1.4


def test_parse_oddsportal_date_header_relative():
    today = date(2026, 6, 25)
    assert parse_oddsportal_date_header("Today, 25 Jun  - Singles - Qualification", today=today) == "2026-06-25"
    assert parse_oddsportal_date_header("Yesterday, 24 Jun  - Singles", today=today) == "2026-06-24"
    assert parse_oddsportal_date_header("13 Jul  - Singles", default_year=2025, today=today) == "2025-07-13"


def test_parse_rendered_html_oddsportal_event_row_american_odds():
    html = """
    <div class="eventRow" id="match1">
      <div data-testid="date-header">Yesterday, 24 Jun  - Singles - Qualification</div>
      <div data-testid="game-row">
        <div data-testid="time-item"><span>Finished</span><span>FIN</span></div>
        <div data-testid="event-participants">
          <a title="Skatov T."><p class="participant-name">Skatov T.</p></a>
          <div class="relative"><div>0</div><a>–</a><div>3</div></div>
          <a title="Jacquet K."><p class="participant-name">Jacquet K.</p></a>
        </div>
        <p data-testid="odd-container-default">+200</p>
        <p data-testid="odd-container-default">-238</p>
      </div>
    </div>
    """
    rows = parse_rendered_html(html, tour="ATP", tournament="Wimbledon", today=date(2026, 6, 25))
    assert len(rows) == 1
    assert rows[0]["match_date"] == "2026-06-24"
    assert rows[0]["round"] == "Singles - Qualification"
    assert rows[0]["player_a"] == "Skatov T."
    assert rows[0]["player_b"] == "Jacquet K."
    assert rows[0]["winner"] == "Jacquet K."
    assert rows[0]["score"] == "0-3"
    assert rows[0]["odds_a"] == 3.0
    assert round(rows[0]["odds_b"], 6) == round(1 + 100 / 238, 6)
