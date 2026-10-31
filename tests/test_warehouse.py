from datetime import date, datetime, timezone
import json

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


def test_collapse_live_card_never_swaps_odds_on_probability():
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

    # Labeled prices stay on their labeled side; disagreement is rejected
    # downstream by selected_odds_is_usable, never repaired here.
    assert len(collapsed) == 1
    assert collapsed.loc[0, "odds_home"] == 9.50
    assert collapsed.loc[0, "odds_away"] == 1.02


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

def test_collapse_keeps_scraped_quote_bound_to_one_source_row():
    from racketfactory.warehouse import collapse_live_card

    observed = datetime.now(timezone.utc).isoformat()
    digest = "a" * 64
    base = {
        "match_date": "2026-10-31", "match_time": "13:00", "tour": "ATP",
        "match_type": "Singles", "player_home": "Alex Example", "player_away": "Ben Sample",
        "tournament": "Test Open", "predicted_winner": "1", "prob_home": 60, "prob_away": 40,
    }
    card = pd.DataFrame([
        {**base, "source": "BetClan", "odds_home": 1.80, "odds_away": 2.10,
         "odds_observed_at": observed, "odds_response_sha256": digest,
         "odds_source_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
         "odds_event_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
         "odds_evidence_kind": "betclan_match_detail", "odds_market_evidence": "betclan_named_home_away_odds",
         "odds_kickoff_evidence": "betclan_event_timestamp"},
        {**base, "source": "BetClan", "odds_home": 1.92, "odds_away": 2.05},
    ])

    collapsed = collapse_live_card(card)
    meta = json.loads(collapsed.loc[0, "_scraped_quote_meta"])
    assert (collapsed.loc[0, "odds_home"], collapsed.loc[0, "odds_away"]) == (1.80, 2.10)
    assert meta["response_sha256"] == digest
    assert meta["origin_source"] == "BetClan"
    assert meta["odds_home"] == 1.8 and meta["odds_away"] == 2.1


def test_enrich_live_card_promotes_only_bound_scraped_pick_odds(monkeypatch):
    from racketfactory import warehouse

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [])
    monkeypatch.setattr(warehouse, "fetch_comparison_rows", lambda target_date: [])
    meta = {
        "source": "ScrapedPickOdds", "origin_source": "BetClan",
        "source_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
        "event_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-123",
        "evidence_kind": "betclan_match_detail", "market_evidence": "betclan_named_home_away_odds",
        "kickoff_evidence": "betclan_event_timestamp",
        "response_sha256": "b" * 64,
        "observed_at": datetime.now(timezone.utc).isoformat(), "market": "source_pick_odds",
        "period": "match", "line": None, "price_type": "scraped_pick_odds",
        "fixture_date": "2026-10-31", "match_time": "13:00",
        "player_home": "Alex Example", "player_away": "Ben Sample",
        "odds_home": 1.80, "odds_away": 2.10, "reversed": False,
    }
    card = pd.DataFrame([{
        "match_date": "2026-10-31", "match_time": "13:00", "player_home": "Alex Example",
        "player_away": "Ben Sample", "prob_home": 60, "prob_away": 40,
        "odds_home": 1.80, "odds_away": 2.10,
        "_scraped_quote_meta": json.dumps(meta),
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(
        card, "2026-10-31", supplied_odds=[], supplied_comparisons=[])
    assert enriched.loc[0, "odds_source"] == "ScrapedPickOdds"
    assert enriched.loc[0, "odds_bookmaker"] == ""
    assert json.loads(enriched.loc[0, "_quote_meta"])["response_sha256"] == "b" * 64


def test_betclan_best_effort_odds_without_market_identity_stay_paper_only(monkeypatch):
    from racketfactory import warehouse
    from racketfactory.warehouse import collapse_live_card

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [])
    monkeypatch.setattr(warehouse, "fetch_comparison_rows", lambda target_date: [])
    card = pd.DataFrame([{
        "match_date": "2026-10-31", "match_time": "13:00", "tour": "ATP",
        "match_type": "Singles", "player_home": "Alex Example", "player_away": "Ben Sample",
        "source": "BetClan", "prob_home": 60, "prob_away": 40,
        "odds_home": 1.80, "odds_away": 2.10,
        "odds_observed_at": datetime.now(timezone.utc).isoformat(),
        "odds_response_sha256": "e" * 64,
        "odds_source_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-1",
        "odds_event_url": "https://www.betclan.com/tennis/predictionsdetails/fixture-1",
        "odds_evidence_kind": "betclan_match_detail",
    }])
    collapsed = collapse_live_card(card)
    assert collapsed.loc[0, "_scraped_quote_meta"] == ""
    enriched = warehouse.enrich_live_card_with_api_odds(
        collapsed, "2026-10-31", supplied_odds=[], supplied_comparisons=[])
    assert enriched.loc[0, "odds_source"] == "ScrapedFallback"
    assert enriched.loc[0, "odds_bookmaker"] == "Validated scrape"


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
        "odds_home": 1.20,
        "odds_away": 4.40,
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(card, "2026-06-29")

    assert len(enriched) == 1
    assert enriched.loc[0, "odds_source"] == "ScrapedFallback"
    assert enriched.loc[0, "odds_bookmaker"] == "Validated scrape"
    assert enriched.loc[0, "odds_home"] == 1.20
    assert enriched.loc[0, "odds_away"] == 4.40
    assert enriched.loc[0, "api_odds_home"] is pd.NA
    assert enriched.loc[0, "scraped_odds_home"] == 1.20
    assert enriched.loc[0, "scraped_odds_away"] == 4.40


def test_enrich_live_card_preserves_side_orientation(monkeypatch):
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
        # Coherent pair that disagrees with the model: orientation is
        # preserved (rejection happens at selection, not by swapping).
        "odds_home": 3.40,
        "odds_away": 1.30,
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(card, "2026-06-29")

    assert len(enriched) == 1
    assert enriched.loc[0, "odds_home"] == 3.40
    assert enriched.loc[0, "odds_away"] == 1.30


def test_names_match_rejects_same_surname_initials():
    from racketfactory.warehouse import names_match

    assert not names_match("Johnson S.", "Johnson A.")
    assert not names_match("Zverev A.", "Zverev M.")
    assert not names_match("Zverev", "Alexander Zverev")


def test_names_match_accepts_compound_variants():
    from racketfactory.warehouse import names_match

    assert names_match("Max Alcala Gurri", "Alcala Gurri M.")
    assert names_match("Alcala Gurri", "Max Alcala Gurri")
    assert names_match("Pablo Carreno Busta", "P. Carreno-Busta")
    assert names_match("Denis Shapovalov", "D. Shapovalov")
    assert names_match("Jesper De Jong", "De Jong J.")
    assert names_match("Jaume Munar", "Munar J.")


def test_names_match_doubles_strict_partners():
    from racketfactory.warehouse import names_match

    assert names_match("Cascino / Feng", "Cascino E. / Feng S.")
    assert names_match("Siniakova / Townsend", "Townsend T. / Siniakova K.")
    assert not names_match("Siniakova / Townsend", "Siniakova / Krueger")
    assert names_match("Cascino & Feng", "Cascino / Feng")


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

    rows = warehouse.build_live_rows(target_date="2026-06-29")

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


def test_enrich_live_card_prices_from_betexplorer_consensus(monkeypatch):
    """REAL pricing path (prompt 2026-09-18 task 6): no The Odds API row, but a
    cross-checked BetExplorer consensus row must price the pick with
    odds_source=BetExplorer so downstream _market_basis=api / _is_paper=False."""
    from racketfactory import warehouse

    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda target_date: [])
    monkeypatch.setattr(
        warehouse, "fetch_comparison_rows",
        lambda target_date: [{
            "match_date": "2026-09-18",
            "player_home": "J. Mensik",
            "player_away": "L. Tien",
            "odds_home": 1.62,
            "odds_away": 2.31,
            "source": "BetExplorer",
            "bookmaker": "BetExplorer consensus",
            "odds_cross_checked": "agree",
        }],
    )
    card = pd.DataFrame([{
        "match_date": "2026-09-18",
        "match_time": "13:00",
        "tour": "ATP",
        "match_type": "Singles",
        "player_home": "Mensik J.",
        "player_away": "Tien L.",
        "tournament": "Challenger",
        "surface": "Hard",
        "source": "Forebet",
        "predicted_winner": "1",
        "prob_home": 62,
        "prob_away": 38,
    }])

    enriched = warehouse.enrich_live_card_with_api_odds(card, "2026-09-18")

    assert len(enriched) == 1
    assert enriched.loc[0, "odds_source"] == "BetExplorer"
    assert enriched.loc[0, "odds_bookmaker"] == "BetExplorer consensus"
    assert enriched.loc[0, "odds_cross_checked"] == "agree"
    assert abs(float(enriched.loc[0, "odds_home"]) - 1.62) < 1e-9
    assert abs(float(enriched.loc[0, "odds_away"]) - 2.31) < 1e-9
