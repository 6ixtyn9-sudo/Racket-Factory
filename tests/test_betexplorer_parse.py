"""Fixture tests for the BetExplorer day-page adapter.

The fixture mirrors the structure observed on betexplorer.com/tennis/ on
2026-09-13: tournament links (/tennis/<cat>/<tournament>/), match links
(/tennis/<cat>/<tournament>/<slug>/<id>/) with "Home - Away" text, and the
1/2 consensus decimals in the same row.
"""

from racketfactory.sources import betexplorer as be

FIXTURE = """
<html><body>
<a href="/tennis/challenger-women-singles/valencia/">Challenger Women - Singles: Valencia, clay</a>
<table><tbody>
<tr><td>09:15</td>
  <td><a href="/tennis/challenger-women-singles/valencia/steur-joelle-lilly-sophie-torner-sensano-neus/vmSRiLy0/">Steur J. L. S. - Torner Sensano N.</a></td>
  <td>1.20</td><td>4.25</td></tr>
<tr><td>09:30</td>
  <td><a href="/tennis/challenger-women-singles/caldas-da-rainha/rouvroy-margaux-johnson-sofia/pUUHFqo4/">Rouvroy M. - Johnson S.</a></td>
  <td>3.61</td><td>1.26</td></tr>
<tr><td>Finished</td>
  <td><a href="/tennis/challenger-women-singles/valencia/schunk-nastasja-kazionova-ekaterina/C0QZka6C/">Schunk N. - Kazionova E.</a></td>
  <td>1:0 RET.</td><td>1.13</td><td>5.50</td></tr>
<tr><td>10:00</td>
  <td><a href="/tennis/challenger-women-singles/valencia/some-match-no-odds/AAAAAAAA/">Alpha B. - Beta C.</a></td>
  <td>-</td><td>-</td></tr>
</tbody></table>
<a href="/tennis/player/steur-joelle-lilly-sophie/UTdl3B3U/">Steur Joelle Lilly Sophie</a>
</body></html>
"""


def test_parses_priced_rows_with_context():
    rows = be.parse_tennis_page(FIXTURE, "2026-09-13")
    assert len(rows) == 2
    first = rows[0]
    assert first["player_home"] == "Steur J. L. S."
    assert first["player_away"] == "Torner Sensano N."
    assert first["odds_home"] == 1.20
    assert first["odds_away"] == 4.25
    assert first["match_time"] == "09:15"
    assert first["match_date"] == "2026-09-13"
    assert first["tournament"] == "valencia"
    assert first["source"] == "BetExplorer"
    assert first["bookmaker"] == "BetExplorer consensus"
    assert rows[1]["odds_home"] == 3.61
    assert rows[1]["odds_away"] == 1.26


def test_skips_finished_unpriced_and_non_match_links():
    rows = be.parse_tennis_page(FIXTURE, "2026-09-13")
    names = [(r["player_home"], r["player_away"]) for r in rows]
    assert ("Schunk N.", "Kazionova E.") not in names  # finished RET.
    assert ("Alpha B.", "Beta C.") not in names  # no odds


def test_empty_page_yields_no_rows():
    assert be.parse_tennis_page("", "2026-09-13") == []
    assert be.parse_tennis_page("<html><body>no tennis here</body></html>", "2026-09-13") == []


def test_out_of_range_target_returns_no_rows_without_network():
    # Day page only: any non-today target short-circuits before any fetch.
    assert be.fetch_betexplorer_rows("2000-01-01") == []


def test_disable_env_short_circuits(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_DISABLE_BETEXPLORER", "1")
    from datetime import date
    assert be.fetch_betexplorer_rows(date.today().isoformat()) == []
