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
    assert market_basis_for_pick("", None) == ("none", True)
    assert market_basis_for_pick("TheOddsAPI", None) == ("none", True)


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
