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


RESULTS_FIXTURE = """
<html><body><table>
<tr class="js-tournament"><td colspan="9"><a class="table-main__tournament">Portugal: Caldas da Rainha, hard</a></td></tr>
<tr data-dt="13,09,2026,09,30"><td>09:30</td>
  <td class="table-main__tt"><a href="/tennis/challenger-women-singles/caldas-da-rainha/rouvroy-margaux-johnson-sofia/pUUHFqo4/">Rouvroy M. - Johnson S.</a></td>
  <td>3.61</td><td>1.26</td></tr>
<tr data-dt="13,09,2026,10,00"><td>Finished</td>
  <td class="table-main__tt"><a href="/tennis/challenger-women-singles/caldas-da-rainha/done-match/AAAAAAAA/">Done A. - Over B.</a></td>
  <td>2:0</td><td>1.50</td><td>2.50</td></tr>
</table></body></html>
"""


def test_results_parse_uses_data_dt_rows():
    rows = be.parse_results_page(RESULTS_FIXTURE, "2026-09-13")
    assert len(rows) == 1
    row = rows[0]
    assert (row["player_home"], row["player_away"]) == ("Rouvroy M.", "Johnson S.")
    assert (row["odds_home"], row["odds_away"]) == (3.61, 1.26)
    assert row["match_time"] == "09:30"
    assert row["tournament"] == "caldas da rainha"


def test_fetch_uses_dated_url_for_any_date(monkeypatch):
    monkeypatch.delenv("RACKET_FACTORY_DISABLE_BETEXPLORER")
    seen: list[str] = []

    def fake_fetch(url, label, **kwargs):
        seen.append(url)
        assert kwargs.get("prefer_plain") is True
        return RESULTS_FIXTURE

    monkeypatch.setattr(be, "fetch_page_html", fake_fetch)
    rows = be.fetch_betexplorer_rows("2026-09-14")
    assert seen == ["https://www.betexplorer.com/tennis/results/?year=2026&month=09&day=14"]
    assert len(rows) == 1
    assert rows[0]["source"] == "BetExplorer"
    assert rows[0]["match_date"] == "2026-09-14"


def test_disable_env_short_circuits(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_DISABLE_BETEXPLORER", "1")
    from datetime import date
    assert be.fetch_betexplorer_rows(date.today().isoformat()) == []


def test_original_observation_is_preserved_by_cache(tmp_path, monkeypatch):
    from datetime import datetime
    from racketfactory.fetch_cache import cached_fetch
    calls = []
    monkeypatch.setattr(be, "fetch_page_html", lambda *a, **k: calls.append(1) or RESULTS_FIXTURE)
    first = cached_fetch("betexplorer-observation", lambda: be._fetch_live("2026-09-13"),
                         fetch_day="2026-09-13", cache_dir=tmp_path, ttl_minutes=10)
    second = cached_fetch("betexplorer-observation", lambda: (_ for _ in ()).throw(AssertionError("cache miss")),
                          fetch_day="2026-09-13", cache_dir=tmp_path, ttl_minutes=10)
    assert len(calls) == 1
    assert datetime.fromisoformat(first[0]["observed_at"]).tzinfo is not None
    assert second[0]["observed_at"] == first[0]["observed_at"]
    assert "market" not in first[0]  # Parser has not established market identity.


def test_guarded_collection_distinguishes_verified_empty_day(monkeypatch):
    from datetime import date
    day = date.today().isoformat()
    responses = iter(["<html>unknown shape</html>", "<html>No matches</html>"])
    monkeypatch.setattr(be, "fetch_page_html", lambda *a, **k: next(responses))
    rows, status = be._fetch_live(day, with_status=True)
    assert rows == [] and status == "complete"


def test_guarded_results_shape_discovers_unpriced_fixtures(monkeypatch):
    html = RESULTS_FIXTURE.replace('</table>', '''
<tr data-dt="13,09,2026,11,00"><td>11:00</td>
<td><a href="/tennis/challenger/valencia/alpha-beta/ABCD1234/">Alpha A. - Beta B.</a></td>
<td>-</td><td>-</td></tr></table>''')
    monkeypatch.setattr(be, "fetch_page_html", lambda *a, **k: html)
    metrics = {}
    rows, status = be._fetch_live("2026-09-13", with_status=True, metrics=metrics)
    assert len(rows) == 2 and status == "complete"
    assert rows[1]["odds_home"] is None
    assert metrics == {"data_dt_rows": 3, "parsed_priced_rows": 1, "discovered_rows": 2, "finished_rows": 1,
                       "no_odds_rows": 1, "unrecognized_rows": 0}
