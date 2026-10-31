import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from scripts.mine_edges import selected_odds_is_usable


def _live_row(source="ScrapedFallback", odds_a=1.80, odds_b=2.05):
    return pd.Series({
        "_is_live": True,
        "_odds_source": source,
        "odds_a": odds_a,
        "odds_b": odds_b,
    })


def test_selected_odds_accepts_scraped_fallback_live_source():
    odds, reason = selected_odds_is_usable(_live_row(), "player_a", 0.57)

    assert odds == 1.80
    assert reason is None


def test_selected_odds_still_rejects_unlabelled_live_scrape_noise():
    odds, reason = selected_odds_is_usable(_live_row(source=""), "player_a", 0.57)

    assert odds is None
    assert reason == "missing usable live odds"


def test_selected_odds_accepts_provenance_labeled_scraped_pick_pair():
    odds, reason = selected_odds_is_usable(
        _live_row(source="ScrapedPickOdds", odds_a=1.80, odds_b=2.05), "player_a", 0.57)
    assert odds == 1.80
    assert reason is None


def test_selected_odds_requires_a_complete_pair_for_staked_scrape_source():
    odds, reason = selected_odds_is_usable(
        _live_row(source="ScrapedPickOdds", odds_a=1.80, odds_b=None), "player_a", 0.57)
    assert odds is None
    assert reason == "incomplete/invalid ScrapedPickOdds live odds pair"


def test_selected_odds_revalidates_scraped_fallback_pair():
    odds, reason = selected_odds_is_usable(_live_row(odds_a=9.50, odds_b=9.70), "player_a", 0.57)

    assert odds is None
    assert reason == "incomplete/invalid ScrapedFallback live odds pair"


def test_selected_odds_rejects_side_disagreement_without_repair():
    from scripts.mine_edges import selected_odds_is_usable

    # 79% model on a @9.50-labeled side with a coherent pair: the old code
    # "corrected" this into fake value; now it must reject.
    row = _live_row(odds_a=9.50, odds_b=1.02)
    odds, reason = selected_odds_is_usable(row, "player_a", 79)
    assert odds is None
    assert reason is not None and "disagreement" in reason


def test_market_basis_labels():
    from scripts.mine_edges import market_basis_for_pick

    assert market_basis_for_pick("TheOddsAPI", 1.80) == ("api", False)
    assert market_basis_for_pick("OddsPortal", 2.10) == ("api", False)
    assert market_basis_for_pick("Bzzoiro", 2.10) == ("api", False)
    assert market_basis_for_pick("ScrapedFallback", 1.80) == ("scraped_fallback", True)
    assert market_basis_for_pick("ScrapedPickOdds", 1.80) == ("scraped_fallback", True)
    assert market_basis_for_pick("", None) == ("none", True)
    assert market_basis_for_pick("TheOddsAPI", None) == ("none", True)

    now = datetime.now(timezone.utc)
    quote = {
        "odds": 1.80, "source": "ScrapedPickOdds", "bookmaker": "",
        "market": "source_pick_odds", "period": "match", "line": None,
        "price_type": "scraped_pick_odds", "origin_source": "BetClan",
        "evidence_kind": "betclan_match_detail", "market_evidence": "betclan_named_home_away_odds",
        "kickoff_evidence": "betclan_event_timestamp",
        "source_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
        "event_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
        "response_sha256": "f" * 64, "observed_at": (now - timedelta(minutes=2)).isoformat(),
        "fixture_date": "2026-10-31", "match_time": "13:00",
        "provider_home": "Alex Example", "provider_away": "Ben Sample", "provider_side": "1",
        "selection": "Alex Example", "odds_home": 1.80, "odds_away": 2.10, "reversed": False,
        "source_player_home": "Alex Example", "source_player_away": "Ben Sample",
        "source_odds_home": 1.80, "source_odds_away": 2.10, "source_reversed": False,
    }
    pick = {"selected_side": "1", "selected_player": "Alex Example", "date": "2026-10-31", "kickoff": "13:00"}
    assert market_basis_for_pick(
        "ScrapedPickOdds", 1.80, quote=quote, pick=pick, now=now) == ("scraped_pick_odds", False)
    stale_quote = dict(quote, observed_at=(now - timedelta(minutes=31)).isoformat())
    assert market_basis_for_pick(
        "ScrapedPickOdds", 1.80, quote=stale_quote, pick=pick, now=now) == ("scraped_fallback", True)
    wrong_side = dict(quote, provider_side="2", odds=2.10, selection="Alex Example")
    assert market_basis_for_pick(
        "ScrapedPickOdds", 2.10, quote=wrong_side, pick=pick, now=now) == ("scraped_fallback", True)


def test_selection_basis_tracked():
    from scripts.mine_edges import select_player_from_row

    row = pd.Series({
        "player_a": "A Player", "player_b": "B Player",
        "predicted_winner": "player_a",
        "match_date": "2026-06-30",
    })
    base = select_player_from_row(row, "2026-06-30")
    assert base["_selection_basis"] == "prediction"
    assert base["selected_player"] == "A Player"

    row = pd.Series({
        "player_a": "A Player", "player_b": "B Player",
        "odds_a": 1.50, "odds_b": 2.60,
        "match_date": "2026-06-30",
    })
    base = select_player_from_row(row, "2026-06-30")
    assert base["_selection_basis"] == "odds_favorite"
    assert base["selected_player"] == "A Player"

    row = pd.Series({
        "player_a": "A Player", "player_b": "B Player",
        "match_date": "2026-06-30",
    })
    base = select_player_from_row(row, "2026-06-30")
    assert base["_selection_basis"] == "arbitrary_default"


def test_selected_odds_accepts_single_side_scraped_fallback():
    # Forebet publishes one honest coef on its predicted side: usable for
    # the selected side (paper track downstream).
    odds, reason = selected_odds_is_usable(_live_row(odds_a=1.78, odds_b=None), "player_a", 0.64)

    assert odds == 1.78
    assert reason is None


def test_selected_odds_rejects_single_side_scraped_when_selected_missing():
    odds, reason = selected_odds_is_usable(_live_row(odds_a=1.78, odds_b=None), "player_b", 0.36)

    assert odds is None
    assert reason == "missing selected-side odds"


def test_selected_odds_rejects_incoherent_scraped_pair_with_both_sides():
    # Both sides present but incoherent (implied 0.66): a corrupt pair, not
    # an honest single side.
    odds, reason = selected_odds_is_usable(_live_row(odds_a=1.78, odds_b=9.70), "player_a", 0.64)

    assert odds is None
    assert reason == "incomplete/invalid ScrapedFallback live odds pair"


def test_warehouse_quote_export_preserves_scrape_provenance_without_bookmaker_label():
    from scripts.mine_edges import warehouse_selected_quote

    meta = {
        "source": "ScrapedPickOdds", "origin_source": "BetClan",
        "source_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
        "event_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
        "evidence_kind": "betclan_match_detail", "market_evidence": "betclan_named_home_away_odds",
        "kickoff_evidence": "betclan_event_timestamp",
        "response_sha256": "c" * 64,
        "observed_at": "2026-10-31T12:00:00+00:00", "market": "source_pick_odds",
        "period": "match", "price_type": "scraped_pick_odds", "fixture_date": "2026-10-31",
        "match_time": "13:00", "player_home": "Alex Example", "player_away": "Ben Sample",
        "odds_home": 1.80, "odds_away": 2.10, "reversed": False,
        "source_player_home": "Alex Example", "source_player_away": "Ben Sample",
        "source_odds_home": 1.80, "source_odds_away": 2.10, "source_reversed": False,
    }
    quote = warehouse_selected_quote(
        pd.Series({"_quote_meta": json.dumps(meta)}),
        {"selected_side": "player_a", "selected_player": "Alex Example"},
        1.80, "ScrapedPickOdds", "")
    assert quote["source"] == "ScrapedPickOdds"
    assert quote["bookmaker"] == ""
    assert quote["provider_side"] == "1"
    assert quote["source_odds_home"] == 1.80
    assert quote["response_sha256"] == "c" * 64


def test_selected_odds_keeps_api_strict_pair():
    odds, reason = selected_odds_is_usable(
        _live_row(source="TheOddsAPI", odds_a=1.78, odds_b=None), "player_a", 0.64)

    assert odds is None
    assert reason == "incomplete/invalid TheOddsAPI live odds pair"
