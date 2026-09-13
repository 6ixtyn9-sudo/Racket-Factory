"""Fixture tests for the OddsPortal upcoming-day adapter.

The fixture mirrors the structure observed on oddsportal.com/tennis/ on
2026-09-13: match links under /tennis/h2h/<player-a>/<player-b>/ with
"Home - Away" text and the best-across-books 1/2 decimals in the same row.
"""

from datetime import date, timedelta

from racketfactory.sources import oddsportal_upcoming as opup

FIXTURE = """
<html><body>
<div>
<a href="/tennis/usa/atp-us-open/">ATP US Open (hard)</a>
<div><span>14:00</span>
<a href="/tennis/h2h/shelton-ben-QNuG0Gzb/zverev-alexander-dGbUhw9m/">Zverev A. - Shelton B.</a>
<span>1.67</span><span>2.25</span></div>
<div><span>Finished FIN 0-2</span>
<a href="/tennis/h2h/monzon-ignacio-IFnIgEEe/popko-dmitry-vaYnE8tL/">Monzon I. - Popko D.</a>
<span>3.50</span><span>1.28</span></div>
</div>
</body></html>
"""

CHALLENGE = "<html><head><title>Just a moment...</title></head><body>cf_chl test</body></html>"


def test_parses_priced_rows():
    rows = opup.parse_tennis_page(FIXTURE, "2026-09-13")
    assert len(rows) == 1
    row = rows[0]
    assert row["player_home"] == "Zverev A."
    assert row["player_away"] == "Shelton B."
    assert row["odds_home"] == 1.67
    assert row["odds_away"] == 2.25
    assert row["match_time"] == "14:00"
    assert row["source"] == "OddsPortal"
    assert row["bookmaker"] == "OddsPortal best odds"


def test_skips_finished_rows():
    rows = opup.parse_tennis_page(FIXTURE, "2026-09-13")
    assert all(r["player_home"] != "Monzon I." for r in rows)


def test_challenge_page_yields_no_rows():
    assert opup.parse_tennis_page(CHALLENGE, "2026-09-13") == []


def test_page_routing_today_tomorrow_only():
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert opup._page_for_target(today) == (opup.TODAY_PATH, "oddsportal_upcoming_today")
    assert opup._page_for_target(tomorrow) == (opup.TOMORROW_PATH, "oddsportal_upcoming_tomorrow")
    assert opup._page_for_target("2000-01-01") is None
    assert opup.fetch_oddsportal_upcoming_rows("2000-01-01") == []


def test_disable_env_short_circuits(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_DISABLE_ODDSPORTAL_UPCOMING", "1")
    assert opup.fetch_oddsportal_upcoming_rows(date.today().isoformat()) == []
