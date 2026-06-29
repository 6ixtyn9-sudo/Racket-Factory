from datetime import date

import pandas as pd

from racketfactory.oddsportal import COLUMNS
from racketfactory.warehouse import build_warehouse


def test_build_warehouse_counts(tmp_path):
    df = pd.DataFrame([{
        "match_date": "2026-06-24",
        "tour": "ATP",
        "tournament": "Test Open",
        "round": "R32",
        "player_a": "A Player",
        "player_b": "B Player",
        "winner": "A Player",
        "score": "6-4 6-4",
        "odds_a": 1.8,
        "odds_b": 2.1,
        "bookmaker": "fixture",
        "source": "fixture",
        "captured_at": "2026-06-24T00:00:00Z",
        "oddsportal_url": "",
    }], columns=COLUMNS)
    df.to_csv(tmp_path / "oddsportal_tennis_2026-06.csv.gz", index=False, compression="gzip")
    counts = build_warehouse(data_dir=tmp_path, db_path=tmp_path / "warehouse.duckdb")
    assert counts is not None
    assert counts["oddsportal_rows"] == 1
    assert counts["settled_matches"] == 1
    assert counts["market_sides"] == 2


def test_live_odds_alignment_identifies_likely_side_inversions():
    from racketfactory.warehouse import odds_suspicious_for_probability, valid_two_way_decimal_pair

    assert not valid_two_way_decimal_pair(9.50, 9.70)
    assert odds_suspicious_for_probability(79, 9.50)
    assert odds_suspicious_for_probability(73, 3.40)
    assert not odds_suspicious_for_probability(82, 1.02)


def test_collapse_live_card_repairs_side_inverted_live_odds():
    from racketfactory.warehouse import collapse_live_card

    card = pd.DataFrame([{
        "match_date": "2026-06-29",
        "match_time": "13:00",
        "tour": "WTA",
        "match_type": "Singles",
        "player_home": "Jelena Ostapenko",
        "player_away": "Harriet Dart",
        "tournament": "Wimbledon",
        "surface": "Grass",
        "source": "BetClan",
        "predicted_winner": "1",
        "prob_home": 79,
        "prob_away": 21,
        "odds_home": 9.50,
        "odds_away": 1.02,
    }])

    collapsed = collapse_live_card(card)

    assert len(collapsed) == 1
    assert collapsed.loc[0, "odds_home"] == 1.02
    assert collapsed.loc[0, "odds_away"] == 9.50


def test_collapse_live_card_reorients_reversed_source_before_aggregating_odds():
    from racketfactory.warehouse import collapse_live_card

    card = pd.DataFrame([
        {
            "match_date": "2026-06-29",
            "match_time": "12:00",
            "tour": "ATP",
            "match_type": "Singles",
            "player_home": "Denis Shapovalov",
            "player_away": "Pablo Carreno Busta",
            "tournament": "Wimbledon",
            "surface": "Grass",
            "source": "BetClan",
            "predicted_winner": "2",
            "prob_home": 27,
            "prob_away": 73,
            "odds_home": 3.80,
            "odds_away": 1.28,
        },
        {
            "match_date": "2026-06-29",
            "match_time": "12:00",
            "tour": "ATP",
            "match_type": "Singles",
            "player_home": "P. Carreno-Busta",
            "player_away": "D. Shapovalov",
            "tournament": "Wimbledon",
            "surface": "Grass",
            "source": "Forebet",
            "predicted_winner": "1",
            "prob_home": 73,
            "prob_away": 27,
            "odds_home": 1.30,
            "odds_away": 3.60,
        },
    ])

    collapsed = collapse_live_card(card)

    assert len(collapsed) == 1
    assert collapsed.loc[0, "player_home"] == "Denis Shapovalov"
    assert collapsed.loc[0, "player_away"] == "Pablo Carreno Busta"
    assert collapsed.loc[0, "odds_home"] == 3.80
    assert collapsed.loc[0, "odds_away"] == 1.30

def test_enrich_live_card_uses_validated_scraped_fallback_when_api_missing(monkeypatch):
    from racketfactory import warehouse

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [])
    card = pd.DataFrame([{
        "match_date": "2026-06-29",
        "match_time": "13:00",
        "tour": "WTA",
        "match_type": "Singles",
        "player_home": "Jelena Ostapenko",
        "player_away": "Harriet Dart",
        "tournament": "Wimbledon",
        "surface": "Grass",
        "source": "BetClan",
        "predicted_winner": "1",
        "prob_home": 79,
        "prob_away": 21,
        # Valid pair, but side-inverted relative to the prediction probabilities.
        "odds_home": 9.50,
        "odds_away": 1.02,
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(card, "2026-06-29")

    assert len(enriched) == 1
    assert enriched.loc[0, "odds_source"] == "ScrapedFallback"
    assert enriched.loc[0, "odds_bookmaker"] == "Validated scrape"
    assert enriched.loc[0, "odds_home"] == 1.02
    assert enriched.loc[0, "odds_away"] == 9.50
    assert enriched.loc[0, "api_odds_home"] is pd.NA
    assert enriched.loc[0, "scraped_odds_home"] == 9.50
    assert enriched.loc[0, "scraped_odds_away"] == 1.02


def test_enrich_live_card_rejects_invalid_scraped_fallback_pair_when_api_missing(monkeypatch):
    from racketfactory import warehouse

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [])
    card = pd.DataFrame([{
        "match_date": "2026-06-29",
        "match_time": "13:00",
        "tour": "WTA",
        "match_type": "Singles",
        "player_home": "Player A",
        "player_away": "Player B",
        "tournament": "Wimbledon",
        "surface": "Grass",
        "source": "BetClan",
        "predicted_winner": "1",
        "prob_home": 55,
        "prob_away": 45,
        # Both sides long: impossible overround for a normal two-way market.
        "odds_home": 9.50,
        "odds_away": 9.70,
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(card, "2026-06-29")

    assert len(enriched) == 1
    assert enriched.loc[0, "odds_source"] == ""
    assert pd.isna(enriched.loc[0, "odds_home"])
    assert pd.isna(enriched.loc[0, "odds_away"])
    assert enriched.loc[0, "scraped_odds_home"] == 9.50
    assert enriched.loc[0, "scraped_odds_away"] == 9.70


def test_live_card_export_preserves_scraped_fallback_odds_source(monkeypatch):
    from racketfactory import warehouse

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [])
    monkeypatch.setattr(warehouse, "date", type("FakeDate", (), {"today": staticmethod(lambda: date(2026, 6, 29))}))

    class EmptyPredictor:
        def fetch_daily(self):
            return []
        def fetch_daily_predictions(self, day):
            return []

    class OneRowPredictor:
        def fetch_daily(self):
            return [{
                "match_date": "2026-06-29",
                "match_time": "13:00",
                "match_type": "Singles",
                "player_home": "Jelena Ostapenko",
                "player_away": "Harriet Dart",
                "tournament": "Wimbledon",
                "surface": "Grass",
                "predicted_winner": "1",
                "prob_home": 79,
                "prob_away": 21,
                "odds_home": 1.20,
                "odds_away": 4.40,
            }]

    monkeypatch.setattr(warehouse, "PredixSportPredictor", EmptyPredictor)
    monkeypatch.setattr(warehouse, "BetClanPredictor", OneRowPredictor)
    monkeypatch.setattr(warehouse, "ForebetPredictor", EmptyPredictor)

    rows = warehouse.build_live_rows()

    assert len(rows) == 1
    assert rows.loc[0, "odds_a"] == 1.20
    assert rows.loc[0, "odds_b"] == 4.40
    assert rows.loc[0, "_odds_source"] == "ScrapedFallback"
    assert rows.loc[0, "api_odds_a"] is pd.NA
    assert rows.loc[0, "debug_scraped_odds_a"] == 1.20
    assert rows.loc[0, "debug_scraped_odds_b"] == 4.40


def test_enrich_live_card_prefers_api_odds_over_scraped_fallback(monkeypatch):
    from racketfactory import warehouse

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [{
        "match_date": "2026-06-29",
        "player_home": "Jelena Ostapenko",
        "player_away": "Harriet Dart",
        "odds_home": 1.18,
        "odds_away": 4.80,
        "bookmaker": "pinnacle",
    }])
    card = pd.DataFrame([{
        "match_date": "2026-06-29",
        "match_time": "13:00",
        "tour": "WTA",
        "match_type": "Singles",
        "player_home": "Jelena Ostapenko",
        "player_away": "Harriet Dart",
        "tournament": "Wimbledon",
        "surface": "Grass",
        "source": "BetClan",
        "predicted_winner": "1",
        "prob_home": 79,
        "prob_away": 21,
        "odds_home": 1.20,
        "odds_away": 4.40,
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(card, "2026-06-29")

    assert len(enriched) == 1
    assert enriched.loc[0, "odds_source"] == "TheOddsAPI"
    assert enriched.loc[0, "odds_bookmaker"] == "pinnacle"
    assert enriched.loc[0, "odds_home"] == 1.18
    assert enriched.loc[0, "odds_away"] == 4.80
    assert enriched.loc[0, "api_odds_home"] == 1.18
    assert enriched.loc[0, "api_odds_away"] == 4.80
    assert enriched.loc[0, "scraped_odds_home"] == 1.20
    assert enriched.loc[0, "scraped_odds_away"] == 4.40
