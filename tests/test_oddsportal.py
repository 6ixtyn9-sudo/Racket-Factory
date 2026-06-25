from racketfactory.oddsportal import normalize_rows, parse_date, parse_rendered_html


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
