"""Tests for the OddsPortal match-page leg (listing discovers, pages price)."""

from racketfactory.sources import _page_odds as po
from racketfactory.sources import oddsportal_upcoming as op

MATCH_PAGE = """
<html><body>
<div class="tabs">Home/Away Over/Under Asian Handicap Correct Score</div>
<table>
<tr><th>Bookmakers</th><th>1</th><th>2</th><th>Payout</th></tr>
<tr><td><a href="/proxy/bookmakers/bet365-us/link/">bet365.us</a></td>
  <td><a href="/proxy/bookmakers/bet365-us/betslip/p/1">1.67</a></td>
  <td><a href="/proxy/bookmakers/bet365-us/betslip/p/2">2.30</a></td>
  <td>96.8%</td></tr>
<tr><td><a href="/proxy/bookmakers/betmgm-us/link/">BetMGM.us</a></td>
  <td><a href="/proxy/bookmakers/betmgm-us/betslip/p/1">1.70</a></td>
  <td><a href="/proxy/bookmakers/betmgm-us/betslip/p/2">2.20</a></td>
  <td>95.9%</td></tr>
<tr><td><a href="/proxy/bookmakers/draftkings/link/">DraftKings</a></td>
  <td><a href="/proxy/bookmakers/draftkings/betslip/p/1">1.65</a></td>
  <td><a href="/proxy/bookmakers/draftkings/betslip/p/2">2.24</a></td>
  <td>95.0%</td></tr>
</table>
<div>Previous Matches: Zverev A.</div>
<table>
<tr><td><a href="/tennis/h2h/x/y/">Zverev A. - Van De Zandschulp B. 3 0</a></td>
  <td>1.11</td><td>6.50</td></tr>
</table>
</body></html>
"""

SINGLE_SIDE_PAGE = """
<html><body><table>
<tr><th>Bookmakers</th><th>1</th><th>2</th><th>Payout</th></tr>
<tr><td>SoloBook</td>
  <td><a href="/proxy/bookmakers/solo/betslip/p/1">1.90</a></td>
  <td><a href="/proxy/bookmakers/solo/betslip/p/2">1.95</a></td>
  <td>97.0%</td></tr>
<tr><td>SuspendedBook</td>
  <td><a href="/proxy/bookmakers/susp/betslip/p/1">2.50</a></td>
  <td>-</td><td></td></tr>
</table></body></html>
"""

LISTING = """
<html><body>
<div>Tennis / USA / ATP US Open (hard) Today, 13 Sep 1 2 20:00
<a href="/tennis/h2h/shelton-ben-QNuG0Gzb/zverev-alexander-dGbUhw9m/#MyyzpiYp">Zverev A. - Shelton B.</a> - -</div>
<div>Tennis / ITF Women W15 Pilar (clay) Today, 13 Sep Finished FIN 2 0
<a href="/tennis/h2h/ayala-lourdes-rB2aL0od/cabezas-dominguez-WG3jce9l/#zmVtaSnJ">Cabezas Dominguez E. - Ayala L.</a></div>
<div>Tennis / ITF Men M15 Leme (clay) Today, 13 Sep 1 2 18:30
<a href="/tennis/h2h/britto-remondy-xIW82Bqs/tosetto-zanellato-n1NMWrnO/inplay-odds/#0OTiAR2l">Britto L./Remondy Pagotto V. H. - Tosetto R./Zanellato N.</a> - -</div>
</body></html>
"""


def test_match_page_best_across_books():
    assert op.parse_match_page_odds(MATCH_PAGE) == (1.70, 2.30)


def test_match_page_ignores_previous_matches_and_payout():
    # 1.11/6.50 are plain text (not betslip links) and 96.8% is out of range:
    # neither may leak into the best pair.
    best_home, best_away = op.parse_match_page_odds(MATCH_PAGE)
    assert best_home == 1.70
    assert best_away == 2.30


def test_match_page_without_payout_marker_still_pairs_by_row():
    page = MATCH_PAGE.replace("<th>Payout</th>", "<th></th>")
    assert op.parse_match_page_odds(page) == (1.70, 2.30)


def test_match_page_single_side_row_ignored():
    assert op.parse_match_page_odds(SINGLE_SIDE_PAGE) == (1.90, 1.95)


def test_match_page_without_odds_returns_none():
    assert op.parse_match_page_odds("<html><body>no odds here</body></html>") == (None, None)
    assert op.parse_match_page_odds("") == (None, None)


def test_listing_without_odds_requirement_emits_link_rows():
    rows = po.parse_listing_page(
        LISTING, source_label="T", is_match_link=op._is_match_link,
        page_date="2026-09-13", require_odds=False)
    # Finished row skipped; upcoming + live rows emitted with blank odds.
    assert [(r["player_home"], r["odds_home"]) for r in rows] == [
        ("Zverev A.", None), ("Britto L./Remondy Pagotto V. H.", None)]
    # Default behavior still gates on listing odds.
    assert po.parse_listing_page(
        LISTING, source_label="T", is_match_link=op._is_match_link,
        page_date="2026-09-13") == []


def test_fetch_live_prices_unfinished_skips_live(monkeypatch, caplog):
    def fake_fetch(url, source_label, **kwargs):
        if url == op.BASE_URL + op.TODAY_PATH:
            return LISTING
        if "zverev-alexander" in url:
            assert url == ("https://www.oddsportal.com/tennis/h2h/"
                           "shelton-ben-QNuG0Gzb/zverev-alexander-dGbUhw9m/")
            return MATCH_PAGE
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(op, "fetch_page_html", fake_fetch)
    with caplog.at_level("INFO", logger="racketfactory.sources.oddsportal_upcoming"):
        rows = op._fetch_live(op.TODAY_PATH, "2026-09-13")
    assert len(rows) == 1
    row = rows[0]
    assert (row["player_home"], row["player_away"]) == ("Zverev A.", "Shelton B.")
    assert (row["odds_home"], row["odds_away"]) == (1.70, 2.30)
    assert row["bookmaker"] == op.BOOK_LABEL
    assert row["source"] == op.SOURCE_NAME
    assert row["price_type"] == "best_across_books"
    assert row["market"] == "h2h" and row["period"] == "match"
    assert row["match_date"] == row["match_time"] == ""
    assert row["listing_date"] == "2026-09-13"
    assert row["event_id"] == "zverev-alexander-dGbUhw9m"
    assert "OddsPortal match Zverev A. - Shelton B.: " in caplog.text
    assert "betslip=" in caplog.text


def test_synthetic_match_page_schedule_enables_real_indicative_price_producer_route(monkeypatch):
    """Synthetic reduced page only: exercises parser, warehouse, provenance, side map."""
    import json
    import pandas as pd
    from racketfactory import warehouse
    from racketfactory.execution import rebuild_quote_execution_problem
    from scripts.mine_edges import (classify_bucket, market_basis_for_pick,
                                    should_write_pick_outputs, valid_leg_price,
                                    warehouse_selected_quote)
    from scripts.auto_tickets import selected_quote_problem

    target = "2026-12-01"
    match_path = "/tennis/h2h/shelton-ben-QNuG0Gzb/zverev-alexander-dGbUhw9m/"
    event = {
        "@context": "https://schema.org", "@type": "SportsEvent", "sport": "Tennis",
        "url": op.BASE_URL + match_path, "eventStatus": "https://schema.org/EventScheduled",
        "startDate": "2026-12-01T13:00:00Z",
        "homeTeam": {"@type": "SportsTeam", "name": "Zverev A."},
        "awayTeam": {"@type": "SportsTeam", "name": "Shelton B."},
    }
    page = MATCH_PAGE.replace(
        "</body>",
        '<script type="application/ld+json">' + json.dumps(event) + "</script></body>",
    )

    def fake_fetch(url, source_label, **kwargs):
        if url == op.BASE_URL + op.TODAY_PATH:
            return LISTING
        if url == op.BASE_URL + match_path:
            return page
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(op, "fetch_page_html", fake_fetch)
    rows = op._fetch_live(op.TODAY_PATH, target)
    assert len(rows) == 1
    quote = rows[0]
    assert quote["match_date"] == target and quote["match_time"] == "15:00"
    assert quote["price_type"] == "best_across_books"
    assert quote["schedule_evidence"]["provider"] == "OddsPortal"
    # This synthetic page exercises code paths only; it is not a retained
    # provider capture and does not establish current live-site support.
    assert rebuild_quote_execution_problem(quote) == ""

    prediction = {
        "match_date": target, "match_time": "15:00", "player_home": "Zverev A.",
        "player_away": "Shelton B.", "prob_home": 60, "prob_away": 40,
        "predicted_winner": "1", "tournament": "ATP Shanghai", "tour_slug": "atp",
        "surface": "Hard", "source": "SyntheticForecast",
    }
    built = warehouse.build_live_rows(
        supplied_predictions=[prediction], target_date=target, supplied_odds=[],
        supplied_comparisons=rows,
    )
    assert len(built) == 1
    result = built.iloc[0]
    assert result["_odds_source"] == "OddsPortal"
    assert result["odds_a"] == 1.70 and result["odds_b"] == 2.30
    selected = warehouse_selected_quote(
        result, {"selected_side": "player_a", "selected_player": "Zverev A."},
        result["odds_a"], "OddsPortal", result["bookmaker"],
    )
    assert selected["provider_side"] == "1"
    assert selected["price_type"] == "best_across_books"
    assert selected["schedule_evidence"]["event_id"] == quote["event_id"]
    pick = {
        "odds": selected["odds"], "selected_player": "Zverev A.", "selected_side": "player_a",
        "odds_source": "OddsPortal", "odds_bookmaker": op.BOOK_LABEL,
        "date": target, "kickoff": "15:00", "selected_quote": selected,
    }
    assert selected_quote_problem(pick, require_provenance=True) == ""
    assert valid_leg_price(pick["odds"])  # existing 1.30 minimum-odds gate remains active
    assert market_basis_for_pick("OddsPortal", pick["odds"]) == ("api", False)
    assert classify_bucket({"Verdict": "WATCHLIST"}) == "WATCHLIST"
    assert should_write_pick_outputs([], [{"match": "existing protected pick"}]) is False

    reversed_card = pd.DataFrame([{
        "match_date": target, "match_time": "15:00", "player_home": "Shelton B.",
        "player_away": "Zverev A.", "prob_home": 40, "prob_away": 60,
    }])
    reversed_row = warehouse.enrich_live_card_with_api_odds(
        reversed_card, target, supplied_odds=[], supplied_comparisons=rows,
    ).iloc[0]
    reversed_selected = warehouse_selected_quote(
        reversed_row, {"selected_side": "player_a", "selected_player": "Shelton B."},
        reversed_row["odds_home"], "OddsPortal", reversed_row["odds_bookmaker"],
    )
    assert reversed_selected["provider_side"] == "2"
    assert reversed_row["odds_home"] == 2.30
