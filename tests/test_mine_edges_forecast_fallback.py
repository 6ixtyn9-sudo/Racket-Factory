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
