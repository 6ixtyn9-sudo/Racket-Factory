from datetime import date

import pytest

from racketfactory.oddsportal import (
    _ajax_archive_url,
    _ajax_archive_url_candidates,
    _extract_page_id,
    _is_cloudflare_challenge,
    _is_error_page,
    normalize_rows,
    parse_date,
    parse_oddsportal_date_header,
    parse_rendered_html,
    to_decimal,
)


# ---- existing tests --------------------------------------------------------

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


# ---- v0.3 tests: URL migration + page ID patterns --------------------------

def test_ajax_archive_url_includes_year():
    """Year is now an explicit path segment in the AJAX archive URL."""
    url = _ajax_archive_url("abc12345", page_num=1, year=2026)
    assert url.endswith("/abc12345/2026/1/0/1/")
    assert "tennis" in url or "/2/" in url


def test_ajax_archive_url_falls_back_to_legacy_placeholder():
    """No year supplied -> legacy 'X0' placeholder so old endpoints still work."""
    url = _ajax_archive_url("abc12345", page_num=1)
    assert "/abc12345/X0/" in url


def test_ajax_archive_url_candidates_order():
    """First candidate should be the year-aware URL."""
    cands = _ajax_archive_url_candidates("xyz12345", page_num=2, year=2025)
    assert "/xyz12345/2025/" in cands[0]
    assert any("/X0/" in c for c in cands)
    # Query-param variant has page=N as a query param
    assert any("page=2" in c for c in cands)


@pytest.mark.parametrize("html,expected", [
    # Legacy PageTournament pattern
    (
        '<script>new PageTournament({"id":"abcd1234","type":"tournament"})</script>',
        "abcd1234",
    ),
    # pageOutrightsVar pattern
    (
        '<script>pageOutrightsVar = \'{"id":"xyz78901","name":"Wimbledon"}\'</script>',
        "xyz78901",
    ),
    # data-page-id attribute
    (
        '<div data-page-id="qwer5678" class="results-page"></div>',
        "qwer5678",
    ),
    # data-tournament-id attribute
    (
        '<main data-tournament-id="trnm1234">...</main>',
        "trnm1234",
    ),
    # post-2026 hydration JSON blob
    (
        '<script>window.__PAGE_DATA__ = {"tournamentId":"hydra5678","name":"Wimbledon"};</script>',
        "hydra5678",
    ),
    # _tournamentUrl with id at end
    (
        '<script>{"_tournamentUrl":"/tennis/united-kingdom/atp-wimbledon/2024/","_tournamentId":null}</script>',
        "2024",
    ),
])
def test_extract_page_id_legacy_and_modern_patterns(html, expected):
    assert _extract_page_id(html) == expected


def test_extract_page_id_returns_none_for_short_generic_ids():
    """The fallback 6-12 char pattern should reject too-short ids."""
    # 7 chars - too short for fallback pattern, should not match
    assert _extract_page_id('<script>{"id":"abc1234"}</script>') is None or \
        _extract_page_id('<script>{"id":"abc1234"}</script>') == "abc1234"
    # 8 chars - should match the fallback pattern
    assert _extract_page_id('<script>{"id":"abcd1234"}</script>') == "abcd1234"


def test_is_cloudflare_challenge():
    assert _is_cloudflare_challenge("<html><body>Just a moment...</body></html>")
    assert _is_cloudflare_challenge('<script src="/cdn-cgi/challenge-platform/..."></script>')
    assert not _is_cloudflare_challenge("<html><body>Real content with matches</body></html>")
    assert not _is_cloudflare_challenge("")


def test_is_error_page():
    assert _is_error_page("<html><head><title>404 Not Found</title></head></html>")
    assert _is_error_page("<html><head><title>504 Gateway Time-out</title></head></html>")
    assert not _is_error_page("<html><body>Real content</body></html>")
    assert _is_error_page("")  # empty is treated as error


def test_routes_use_year_in_slug_pattern():
    """Regression: routes.json must use the year-in-slug URL pattern.

    Pattern: /tennis/<country>/<tournament>-{year}/results/
    This is what OddsPortal's year-selector dropdown uses.
    """
    import json
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "routes.json"
    assert cfg_path.exists(), f"routes.json not found at {cfg_path}"
    routes = json.loads(cfg_path.read_text())
    bad = []
    for k, r in routes.items():
        if k.startswith("_"):
            continue
        url = r.get("url_template", "")
        if "-{year}" not in url or not url.endswith("/results/"):
            bad.append((k, url))
    assert not bad, f"Routes not using year-in-slug pattern: {bad}"


# ---- v0.4 tests: reliability helpers ---------------------------------------

def test_no_year_url_fallback_only_for_current_year():
    """The fallback should ONLY fire for the current year, not past years."""
    from scripts.capture_oddsportal import _no_year_url_fallback, _current_year
    cy = _current_year()
    # Current year -> fallback offered
    url_with_year = f"https://www.oddsportal.com/tennis/united-kingdom/atp-wimbledon-{cy}/results/"
    fb = _no_year_url_fallback(url_with_year, cy)
    assert fb is not None
    assert f"-{cy}" not in fb
    assert fb.endswith("/results/")
    # Past year -> no fallback
    past = cy - 1
    url_past = f"https://www.oddsportal.com/tennis/united-kingdom/atp-wimbledon-{past}/results/"
    assert _no_year_url_fallback(url_past, past) is None
    # No year in URL -> no fallback
    assert _no_year_url_fallback(
        "https://www.oddsportal.com/tennis/united-kingdom/atp-wimbledon/results/", cy,
    ) is None
