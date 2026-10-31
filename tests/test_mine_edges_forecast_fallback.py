import json
from datetime import datetime, timezone

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


def test_build_upcoming_fallback_card_handles_reversed_source_row(monkeypatch):
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
                "odds_home": None,
                "odds_away": None,
            }]

    class ForebetRows:
        def fetch_daily_predictions(self, day):
            if day not in {"2026-06-30", "tomorrow", "today"}:
                return []
            assert day  # requested through forebet_day_for_target
            return [{
                "match_date": "2026-06-30",
                "match_time": "13:30",
                "match_type": "Singles",
                "player_home": "Zizou Bergs",
                "player_away": "Ugo Humbert",
                "tournament": "Wimbledon",
                "surface": "Grass",
                "predicted_winner": "2",
                "prob_home": 22,
                "prob_away": 78,
                "odds_home": 2.45,
                "odds_away": 1.55,
            }]

    monkeypatch.setattr(mine_edges, "PredixSportPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "BetClanPredictor", BetClanRows)
    monkeypatch.setattr(mine_edges, "ForebetPredictor", ForebetRows)
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])

    card = mine_edges.build_upcoming_fallback_card("2026-06-30")

    assert len(card) == 1
    assert card.loc[0, "_odds_source"] == "ScrapedFallback"
    assert card.loc[0, "player_a"] == "Ugo Humbert"
    assert card.loc[0, "odds_a"] == 1.55
    assert card.loc[0, "odds_b"] == 2.45
    assert card.loc[0, "debug_scraped_odds_home"] == 1.55
    assert card.loc[0, "debug_scraped_odds_away"] == 2.45


def test_forecast_fallback_promotes_single_side_scraped_odds(monkeypatch):
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])

    enriched = mine_edges.enrich_fallback_card_with_api_odds(_forecast_card(1.78, None), "2026-06-30")

    assert len(enriched) == 1
    assert enriched.loc[0, "_odds_source"] == "ScrapedFallback"
    assert enriched.loc[0, "odds_a"] == 1.78
    assert pd.isna(enriched.loc[0, "odds_b"])
    assert enriched.loc[0, "_comment"] == "forecast_upcoming_scraped_fallback_priced"


def test_build_upcoming_fallback_card_prices_single_side_forebet_row(monkeypatch):
    # Run-#209 class: fresh Forebet rows carry one honest coef (away side
    # here). The grouped card must keep it and price the pick (paper).
    class EmptyPredictor:
        def fetch_daily(self):
            return []
        def fetch_daily_predictions(self, day):
            return []

    class ForebetRows:
        def fetch_daily_predictions(self, day):
            return [{
                "match_date": "2026-06-30",
                "match_time": "13:30",
                "match_type": "Singles",
                "player_home": "C. Tabur",
                "player_away": "H. Mayot",
                "tournament": "Challenger Blois",
                "surface": "Clay",
                "predicted_winner": "2",
                "prob_home": 36,
                "prob_away": 64,
                "odds_home": None,
                "odds_away": 1.78,
            }]

    monkeypatch.setattr(mine_edges, "PredixSportPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "BetClanPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "ForebetPredictor", ForebetRows)
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])

    card = mine_edges.build_upcoming_fallback_card("2026-06-30")

    assert len(card) == 1
    assert card.loc[0, "_odds_source"] == "ScrapedFallback"
    assert pd.isna(card.loc[0, "odds_a"])
    assert card.loc[0, "odds_b"] == 1.78


def test_build_upcoming_fallback_card_promotes_bound_scrape_with_quote_provenance(monkeypatch):
    observed = datetime.now(timezone.utc).isoformat()
    digest = "d" * 64

    class EmptyPredictor:
        def fetch_daily(self):
            return []
        def fetch_daily_predictions(self, day):
            return []

    class BetClanRows:
        def fetch_daily(self):
            return [{
                "match_date": "2026-10-31", "match_time": "13:30", "match_type": "Singles",
                "player_home": "Ugo Humbert", "player_away": "Zizou Bergs",
                "tournament": "Wimbledon", "surface": "Grass", "predicted_winner": "1",
                "prob_home": 78, "prob_away": 22, "odds_home": 1.55, "odds_away": 2.45,
                "odds_observed_at": observed, "odds_response_sha256": digest,
                "odds_source_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
                "odds_event_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
                "odds_evidence_kind": "betclan_match_detail",
                "odds_market_evidence": "betclan_named_home_away_odds",
                "odds_kickoff_evidence": "betclan_event_timestamp",
            }]

    monkeypatch.setattr(mine_edges, "PredixSportPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "BetClanPredictor", BetClanRows)
    monkeypatch.setattr(mine_edges, "ForebetPredictor", EmptyPredictor)
    monkeypatch.setattr(mine_edges, "cached_fetch", lambda key, fetcher, **kwargs: fetcher())
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda target_date: [])
    monkeypatch.setattr(mine_edges, "fetch_comparison_rows", lambda target_date: [])

    card = mine_edges.build_upcoming_fallback_card("2026-10-31")

    assert len(card) == 1
    assert card.loc[0, "_odds_source"] == "ScrapedPickOdds"
    assert bool(card.loc[0, "_is_live"]) is True
    meta = json.loads(card.loc[0, "_quote_meta"])
    assert meta["source"] == "ScrapedPickOdds"
    assert meta["response_sha256"] == digest
    assert meta["odds_home"] == 1.55 and meta["odds_away"] == 2.45
    assert not meta.get("bookmaker")


def test_forecast_api_reversed_and_ambiguous_matches(monkeypatch):
    monkeypatch.setattr(mine_edges, "fetch_comparison_rows", lambda day: [])
    match = {"match_date": "2026-06-30", "player_home": "Zizou Bergs", "player_away": "Ugo Humbert",
             "odds_home": 2.35, "odds_away": 1.59}
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda day: [match])
    row = mine_edges.enrich_fallback_card_with_api_odds(_forecast_card(None, None), "2026-06-30").iloc[0]
    assert (row["odds_a"], row["odds_b"]) == (1.59, 2.35)
    monkeypatch.setattr(mine_edges, "fetch_the_odds_api_rows", lambda day: [match, dict(match)])
    row = mine_edges.enrich_fallback_card_with_api_odds(_forecast_card(None, None), "2026-06-30").iloc[0]
    assert pd.isna(row["odds_a"]) and pd.isna(row["odds_b"])
