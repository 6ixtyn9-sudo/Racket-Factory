import pandas as pd

from scripts import mine_edges


def _forecast_card(odds_home=1.55, odds_away=2.45):
    return pd.DataFrame([{
        "match_date": "2026-06-30",
        "player_home": "Ugo Humbert",
        "player_away": "Zizou Bergs",
        "player_a": "Ugo Humbert",
        "player_b": "Zizou Bergs",
        "prob_home": 78,
        "prob_away": 22,
        "odds_home": odds_home,
        "odds_away": odds_away,
        "odds_a": odds_home,
        "odds_b": odds_away,
        "_is_live": True,
        "_comment": "forecast_upcoming_fallback",
    }])


def test_forecast_fallback_promotes_valid_scraped_odds_when_api_missing(monkeypatch):
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])

    enriched = mine_edges.enrich_fallback_card_with_api_odds(_forecast_card(), "2026-06-30")

    assert len(enriched) == 1
    assert enriched.loc[0, "_odds_source"] == "ScrapedFallback"
    assert enriched.loc[0, "odds_a"] == 1.55
    assert enriched.loc[0, "odds_b"] == 2.45
    assert enriched.loc[0, "debug_scraped_odds_home"] == 1.55
    assert enriched.loc[0, "debug_scraped_odds_away"] == 2.45
    assert enriched.loc[0, "_comment"] == "forecast_upcoming_scraped_fallback_priced"


def test_forecast_fallback_rejects_invalid_scraped_odds_when_api_missing(monkeypatch):
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])

    enriched = mine_edges.enrich_fallback_card_with_api_odds(_forecast_card(9.50, 9.70), "2026-06-30")

    assert len(enriched) == 1
    assert str(enriched.loc[0, "_odds_source"] or "") == ""
    assert enriched.loc[0, "debug_scraped_odds_home"] == 9.50
    assert enriched.loc[0, "debug_scraped_odds_away"] == 9.70


def test_forecast_fallback_prefers_api_over_scraped_odds(monkeypatch):
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [{
        "match_date": "2026-06-30",
        "player_home": "U. Humbert",
        "player_away": "Z. Bergs",
        "odds_home": 1.59,
        "odds_away": 2.35,
    }])

    enriched = mine_edges.enrich_fallback_card_with_api_odds(_forecast_card(1.55, 2.45), "2026-06-30")

    assert len(enriched) == 1
    assert enriched.loc[0, "_odds_source"] == "TheOddsAPI"
    assert enriched.loc[0, "odds_a"] == 1.59
    assert enriched.loc[0, "odds_b"] == 2.35
    assert enriched.loc[0, "debug_scraped_odds_home"] == 1.55
    assert enriched.loc[0, "debug_scraped_odds_away"] == 2.45


def test_build_upcoming_fallback_card_preserves_grouped_scraped_odds(monkeypatch):
    class EmptyPredictor:
        def fetch_daily(self):
            return []
        def fetch_daily_predictions(self, day):
            return []

    class BetClanRows:
        def fetch_daily(self):
            return [{
                "match_date": "2026-06-30",
                "match_time": "13:30",
                "match_type": "Singles",
                "player_home": "Ugo Humbert",
                "player_away": "Zizou Bergs",
                "tournament": "Wimbledon",
                "surface": "Grass",
                "predicted_winner": "1",
                "prob_home": 78,
                "prob_away": 22,
                "odds_home": 1.55,
                "odds_away": 2.45,
            }]

    monkeypatch.setattr(mine_edges, "PredixSportPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "BetClanPredictor", BetClanRows)
    monkeypatch.setattr(mine_edges, "ForebetPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])

    card = mine_edges.build_upcoming_fallback_card("2026-06-30")

    assert len(card) == 1
    assert card.loc[0, "_odds_source"] == "ScrapedFallback"
    assert card.loc[0, "odds_a"] == 1.55
    assert card.loc[0, "odds_b"] == 2.45
    assert card.loc[0, "debug_scraped_odds_home"] == 1.55
    assert card.loc[0, "debug_scraped_odds_away"] == 2.45
