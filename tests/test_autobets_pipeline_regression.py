"""Focused offline tests for the fixture -> price -> existing ticket path."""
from datetime import datetime, timezone
import json

import pandas as pd

from racketfactory import warehouse
from racketfactory.odds_compare import merge_comparison_rows
from scripts import auto_tickets as tickets
from scripts import mine_edges as miner

DAY = "2026-10-14"


def test_priced_pair_flows_through_matching_selection_and_ticket_builder(monkeypatch):
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    rows = [
        {"match_date": DAY, "player_home": "Alice Adams", "player_away": "Beth Brown",
         "match_time": "12:00", "prob_home": 70, "prob_away": 30},
        {"match_date": DAY, "player_home": "Clara Clark", "player_away": "Diana Davies",
         "match_time": "13:00", "prob_home": 30, "prob_away": 70},
    ]
    prices = [
        {"match_date": DAY, "player_home": "Alice Adams", "player_away": "Beth Brown",
         "odds_home": 1.55, "odds_away": 2.60, "source": "BetExplorer", "odds_cross_checked": "single"},
        # The market lists the selected player first, opposite to the prediction feed.
        {"match_date": DAY, "player_home": "Diana Davies", "player_away": "Clara Clark",
         "odds_home": 1.60, "odds_away": 2.50, "source": "BetExplorer", "odds_cross_checked": "single"},
    ]
    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda d: [])
    monkeypatch.setattr(warehouse, "fetch_comparison_rows", lambda d: prices)
    enriched = warehouse.enrich_live_card_with_api_odds(pd.DataFrame(rows), DAY)
    picks = []
    for i, side in enumerate(("player_a", "player_b")):
        r = enriched.iloc[i]
        row = pd.Series({"match_date": DAY, "match_time": r.match_time,
                         "player_a": r.player_home, "player_b": r.player_away,
                         "predicted_winner": side, "odds_a": r.odds_home, "odds_b": r.odds_away,
                         "_is_live": True, "_odds_source": r.odds_source})
        pick = miner.select_player_from_row(row, DAY)
        odds, reason = miner.selected_odds_is_usable(row, side, 70)
        assert reason is None
        pick.update(bucket="WATCHLIST", odds=odds, odds_source=r.odds_source,
                    confidence=70, ml_calibrated_prob=0.70, ml_ev=0.08, ml_verdict="ALLOW")
        picks.append(pick)
    assert [(p["selected_player"], p["odds"]) for p in picks] == [("Alice Adams", 1.55), ("Diana Davies", 1.60)]
    playable = [p for p in picks if tickets.is_playable(p)]
    kept, _ = tickets.kickoff_guard(playable, DAY, datetime.fromisoformat(DAY + "T08:15:00+02:00"))
    diag = {}
    priced, paper, _ = tickets.select_accas(kept, diagnostics=diag)
    assert diag["priced_eligible"] == 2 and len(priced) == 1 and not paper
    assert {p["selected_player"] for p in priced[0]["legs"]} == {"Alice Adams", "Diana Davies"}


def test_wrong_doubles_partner_never_matches_or_cross_checks():
    card = pd.Series({"match_date": DAY, "player_home": "Siniakova / Townsend", "player_away": "Cascino / Feng"})
    wrong = {"match_date": DAY, "player_home": "Siniakova / Krueger", "player_away": "Cascino / Feng",
             "odds_home": 1.55, "odds_away": 2.60}
    assert warehouse._match_api_odds_row(card, [wrong])[0] is None
    right = {**wrong, "player_home": "Townsend / Siniakova"}
    assert warehouse._match_api_odds_row(card, [right])[0] is right
    be = [{**wrong, "player_home": "Siniakova / Townsend"}]
    merged = merge_comparison_rows(be, [wrong])
    assert len(merged) == 2 and all(x["odds_cross_checked"] == "single" for x in merged)


def test_stale_start_missing_odds_and_no_pair_stay_blocked(monkeypatch):
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    now = datetime.fromisoformat(DAY + "T08:15:00+02:00")
    late = {"match": "Alice Adams vs Beth Brown", "selected_player": "Alice Adams", "kickoff": "08:10",
            "odds": 1.55, "odds_source": "BetExplorer", "bucket": "WATCHLIST"}
    missing = {"match": "Clara Clark vs Diana Davies", "selected_player": "Diana Davies", "kickoff": "12:00",
               "odds": None, "odds_source": "", "bucket": "WATCHLIST"}
    kept, skipped = tickets.kickoff_guard([late, missing], DAY, now)
    assert kept == [missing] and skipped[0][1] == "already started"
    diag = {}
    priced, _, _ = tickets.select_accas(kept, diagnostics=diag)
    assert not priced and diag["priced_eligible"] == 0
    assert diag["priced_exclusions"] == {"no trusted market price": 1}
    assert tickets.parse_kickoff({"kickoff": DAY}, DAY) is None


def test_run_start_timestamp_cannot_override_ticket_wall_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(DAY + "T08:15:00+02:00")
    monkeypatch.setattr(tickets, "datetime", Clock)
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", DAY + "T08:00:00+02:00")
    assert tickets.now_local().minute == 15


def test_forecast_and_wrong_day_fallback_never_become_official_picks(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets, "LOCALDATA", tmp_path)
    (tmp_path / f"picks_{DAY}.json").write_text(json.dumps([{"ledger_kind": "forecast", "date": DAY}]))
    assert tickets.load_picks(DAY) == []
    (tmp_path / f"picks_{DAY}.json").unlink()
    (tmp_path / "picks_today.json").write_text(json.dumps([{"date": "2026-10-13"}, {"date": DAY, "ledger_kind": "forecast"}]))
    assert tickets.load_picks(DAY) == []


def test_no_bet_diagnostic_distinguishes_one_priced_leg():
    diag = {"picks": 43, "playable": 1, "builder_input": 1,
            "priced_eligible": 1, "paper_eligible": 0, "admission_rejections": {"ML veto": 21}}
    text = tickets.format_tickets_txt(DAY, [], {}, [], diag)
    assert "1 priced eligible legs" in text
    assert "fewer than two eligible priced legs" in text
    assert "pool was empty" not in text and "no priced legs available" not in text


def test_archived_and_stale_fallback_prices_are_not_executable(tmp_path, monkeypatch):
    assert miner.lookup_local_oddsportal_selected_odds(DAY, {}) is None
    path = tmp_path / "bzzoiro.json"
    path.write_text(json.dumps({"date": DAY, "results": [{"placeholder": True}]}))
    monkeypatch.setattr(miner, "_bzzoiro_cache_path", lambda d: path)
    monkeypatch.setattr(miner, "_bzzoiro_date_payload", lambda d: [{"placeholder": True}])
    assert miner.lookup_bzzoiro_selected_odds(DAY, {}) is None
    path.write_text(json.dumps({"fetched_at": "2026-01-01T00:00:00+00:00", "results": []}))
    assert miner.lookup_bzzoiro_selected_odds(DAY, {}) is None


def test_crosscheck_label_survives_live_warehouse_injection(monkeypatch):
    from datetime import date
    class FakeDate:
        @staticmethod
        def today():
            return date(2026, 10, 14)
    monkeypatch.setattr(warehouse, "date", FakeDate)
    prediction = {"match_date": DAY, "match_time": "12:00", "match_type": "Singles",
                  "player_home": "Alice Adams", "player_away": "Beth Brown",
                  "tournament": "ATP Shanghai", "predicted_winner": "1",
                  "prob_home": 70, "prob_away": 30}
    monkeypatch.setattr(warehouse, "cached_fetch", lambda key, fn: [prediction] if key == "betclan" else [])
    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda d: [])
    monkeypatch.setattr(warehouse, "fetch_comparison_rows", lambda d: [
        {"match_date": DAY, "player_home": "Alice Adams", "player_away": "Beth Brown",
         "odds_home": 1.55, "odds_away": 2.60, "source": "BetExplorer", "odds_cross_checked": "single"}])
    live = warehouse.build_live_rows()
    assert len(live) == 1
    assert live.iloc[0]["_odds_source"] == "BetExplorer"
    assert live.iloc[0]["odds_cross_checked"] == "single"


def test_recent_bzzoiro_cache_can_price_the_correct_selected_side(tmp_path, monkeypatch):
    path = tmp_path / "bzzoiro.json"
    payload = [{"match": {"player1": {"name": "Alice Adams"}, "player2": {"name": "Beth Brown"}},
                "best_odds": [{"market": "h2h", "bookmaker": "example",
                               "outcomes": [{"outcome": "1", "odd": 1.55}, {"outcome": "2", "odd": 2.60}]}]}]
    path.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "results": payload}))
    monkeypatch.setattr(miner, "_bzzoiro_cache_path", lambda d: path)
    monkeypatch.setattr(miner, "_bzzoiro_date_payload", lambda d: payload)
    pick = {"player_home": "Beth Brown", "player_away": "Alice Adams", "selected_player": "Alice Adams"}
    assert miner.lookup_bzzoiro_selected_odds(DAY, pick)["odds"] == 1.55
    pick["player_home"] = "Different Player"
    assert miner.lookup_bzzoiro_selected_odds(DAY, pick) is None


def test_historical_row_does_not_become_live_odds():
    row = pd.Series({"_is_live": False, "_odds_source": "OddsPortal", "odds_a": 1.5, "odds_b": 2.6})
    assert miner.selected_odds_is_usable(row, "player_a", 70) == (None, "historical price is not a live quote")


def test_empty_mine_preserves_archive_but_does_not_feed_it_to_tickets(tmp_path, monkeypatch):
    monkeypatch.setattr(miner, "ROOT", tmp_path)
    data = tmp_path / "localdata"
    data.mkdir()
    monkeypatch.setattr(tickets, "LOCALDATA", data)
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", DAY + "T08:00:00+02:00")
    saved = [{"date": DAY, "match": "Alice Adams vs Beth Brown", "odds": 1.55, "odds_source": "BetExplorer"}]
    (data / f"picks_{DAY}.json").write_text(json.dumps(saved))
    miner.write_official_pick_outputs(DAY, [])
    assert json.loads((data / f"picks_{DAY}.json").read_text()) == saved
    assert tickets.load_picks(DAY) == []
    miner.write_official_pick_outputs(DAY, saved)
    assert tickets.load_picks(DAY) == saved
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", DAY + "T09:00:00+02:00")
    assert tickets.load_picks(DAY) == []  # status belongs to an earlier run


def test_mismatched_selected_side_cannot_reach_builder(monkeypatch):
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    pick = {"match": "Alice Adams vs Beth Brown", "bucket": "WATCHLIST",
            "selected_side": "player_b", "selected_player": "Alice Adams",
            "player_home": "Alice Adams", "player_away": "Beth Brown",
            "odds": 1.55, "odds_source": "BetExplorer"}
    rejected = {}
    assert not tickets.is_playable(pick, rejections=rejected)
    assert rejected == {"selected player does not match selected side": 1}
    pick["selected_player"] = "Beth Brown"
    assert tickets.is_playable(pick)
