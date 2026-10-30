"""Focused offline tests for the fixture -> price -> existing ticket path."""
from datetime import datetime, timedelta, timezone
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
    live = warehouse.build_live_rows(target_date=DAY)
    assert len(live) == 1
    assert live.iloc[0]["_odds_source"] == "BetExplorer"
    assert live.iloc[0]["odds_cross_checked"] == "single"


def test_recent_bzzoiro_cache_can_price_the_correct_selected_side(tmp_path, monkeypatch):
    path = tmp_path / "bzzoiro.json"
    payload = [{"match": {"player1": {"name": "Alice Adams"}, "player2": {"name": "Beth Brown"}},
                "best_odds": [{"market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "bookmaker": "example",
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


def test_quote_matching_reversed_date_and_ambiguity():
    card = pd.Series({"match_date": DAY, "player_home": "Alice Adams", "player_away": "Beth Brown"})
    quote = {"match_date": DAY, "player_home": "Beth Brown", "player_away": "Alice Adams",
             "odds_home": 2.6, "odds_away": 1.55, "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "period": "match"}
    assert warehouse._match_api_odds_row(card, [quote]) == (quote, True)
    assert warehouse._match_api_odds_row(card, [quote, dict(quote)])[0] is None
    assert warehouse._match_api_odds_row(card, [dict(quote, match_date="2026-10-15")])[0] is None
    for changed in ({"market": "h2h_set1"}, {"period": "set1"}, {"line": "2.5"}):
        assert warehouse._match_api_odds_row(card, [dict(quote, **changed)])[0] is None


def test_bzzoiro_selected_side_unique_full_match_and_observation_time(tmp_path, monkeypatch):
    path = tmp_path / "bzzoiro.json"
    payload = [{"match": {"id": "fixture-42", "player1": {"name": "Beth Brown"},
                           "player2": {"name": "Alice Adams"}},
                "best_odds": [{"market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "period": "match", "bookmaker": "example",
                               "outcomes": [{"outcome": "1", "odd": 2.6}, {"outcome": "2", "odd": 1.55}]}]}]
    path.write_text(json.dumps({"date": DAY, "fetched_at": datetime.now(timezone.utc).isoformat(), "results": payload}))
    monkeypatch.setattr(miner, "_bzzoiro_cache_path", lambda d: path)
    monkeypatch.setattr(miner, "_bzzoiro_date_payload", lambda d: payload)
    pick = {"player_home": "Alice Adams", "player_away": "Beth Brown", "selected_player": "Alice Adams", "selected_side": "player_a"}
    payload.append({"match": {"id": "unrelated-last", "player1": {"name": "Unrelated One"}, "player2": {"name": "Unrelated Two"}}})
    quote = miner.lookup_bzzoiro_selected_odds(DAY, pick)
    assert (quote["odds"], quote["event_id"], quote["selection"], quote["bookmaker"]) == (1.55, "fixture-42", "Alice Adams", "example")
    assert datetime.fromisoformat(quote["observed_at"]).tzinfo is not None
    assert (quote["provider_home"], quote["provider_away"], quote["provider_side"], quote["request_date"]) == ("Beth Brown", "Alice Adams", "2", DAY)
    assert quote["fixture_date"] is None  # Not invented from the requested day.
    payload[0]["best_odds"][0]["market"] = "h2h_set1"
    assert miner.lookup_bzzoiro_selected_odds(DAY, pick) is None
    payload[0]["best_odds"][0]["market"] = "h2h"
    payload[0]["best_odds"][0]["period"] = "set1"
    assert miner.lookup_bzzoiro_selected_odds(DAY, pick) is None
    payload[0]["best_odds"][0]["period"] = "match"
    payload.append(dict(payload[0]))
    assert miner.lookup_bzzoiro_selected_odds(DAY, pick) is None


def test_selected_quote_provenance_survives_builder_and_changes_block(monkeypatch):
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    ts = datetime.now(timezone.utc).isoformat()
    quote = {"odds": 1.55, "source": "Bzzoiro", "bookmaker": "example", "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"},
             "period": "match", "selection": "Alice Adams", "observed_at": ts, "event_id": "fixture-42"}
    a = {"match": "Alice Adams vs Beth Brown", "selected_player": "Alice Adams", "bucket": "WATCHLIST",
         "odds": 1.55, "odds_source": "Bzzoiro", "odds_bookmaker": "example", "confidence": 75,
         "prediction_prob": .75, "selected_quote": quote}
    b = {"match": "Clara Clark vs Diana Davies", "selected_player": "Clara Clark", "bucket": "WATCHLIST",
         "odds": 1.6, "odds_source": "BetExplorer", "confidence": 75, "prediction_prob": .75}
    assert tickets.is_playable(a)
    priced, _, _ = tickets.select_accas([a, b])
    assert len(priced) == 1
    leg = next(leg for leg in priced[0]["legs"] if leg["selected_player"] == "Alice Adams")
    assert leg["selected_quote"] == quote
    for key, value in (("odds", 2.5), ("selected_player", "Beth Brown"), ("odds_source", "BetExplorer"),
                       ("odds_bookmaker", "other")):
        changed = dict(a, **{key: value})
        reasons = {}
        assert not tickets.is_playable(changed, rejections=reasons)
        assert any("selected quote" in reason for reason in reasons)
    old = dict(quote, observed_at="2026-01-01T00:00:00+00:00")
    assert not tickets.is_playable(dict(a, selected_quote=old))


def test_quote_matching_doubles_requires_both_teams():
    card = pd.Series({"match_date": DAY, "player_home": "Alice Adams / Beth Brown", "player_away": "Clara Clark / Diana Davies"})
    same = {"match_date": DAY, "player_home": "Clara Clark / Diana Davies", "player_away": "Alice Adams / Beth Brown"}
    assert warehouse._match_api_odds_row(card, [same]) == (same, True)
    for wrong in (dict(same, player_away="Alice Adams / Eve Evans"), dict(same, player_away="Alice Adams")):
        assert warehouse._match_api_odds_row(card, [wrong])[0] is None


def test_model_probabilities_do_not_reverse_real_market_quote():
    assert warehouse.align_odds_to_probabilities(0.9, 0.1, 4.0, 1.2) == (4.0, 1.2)


def test_bzzoiro_lookup_rejects_bad_time_wrong_side_and_line(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    payload = [{"match": {"id": "e", "player1": {"name": "Alice Adams"}, "player2": {"name": "Beth Brown"}},
                "best_odds": [{"market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "outcomes": [{"outcome": "1", "odd": 1.55},
                                                                  {"outcome": "2", "odd": 2.7}]}]}]
    monkeypatch.setattr(miner, "_bzzoiro_cache_path", lambda d: path)
    monkeypatch.setattr(miner, "_bzzoiro_date_payload", lambda d: payload)
    pick = {"player_home": "Alice Adams", "player_away": "Beth Brown", "selected_player": "Alice Adams", "selected_side": "player_a"}
    def write(timestamp):
        path.write_text(json.dumps({"date": DAY, "fetched_at": timestamp, "results": payload}))
    for timestamp in ("", "2026-01-01T00:00:00", "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"):
        write(timestamp)
        assert miner.lookup_bzzoiro_selected_odds(DAY, pick) is None
    write(datetime.now(timezone.utc).isoformat())
    assert miner.lookup_bzzoiro_selected_odds(DAY, dict(pick, selected_player="Beth Brown")) is None
    payload[0]["best_odds"][0]["line"] = 0
    assert miner.lookup_bzzoiro_selected_odds(DAY, pick) is None
    payload[0]["best_odds"][0].pop("line")
    underdog = dict(pick, selected_player="Beth Brown", selected_side="player_b")
    assert miner.lookup_bzzoiro_selected_odds(DAY, underdog)["odds"] == 2.7
    payload[0]["best_odds"][0]["outcomes"] = [{"outcome": "1", "odd": 1.55}]
    assert miner.lookup_bzzoiro_selected_odds(DAY, underdog) is None


def test_original_market_observation_flows_from_matching_to_builder(monkeypatch):
    from datetime import date
    class FakeDate:
        @staticmethod
        def today():
            return date(2026, 10, 14)
    monkeypatch.setattr(warehouse, "date", FakeDate)
    prediction = {"match_date": DAY, "match_time": "12:00", "match_type": "Singles",
                  "player_home": "Alice Adams", "player_away": "Beth Brown",
                  "tournament": "ATP Shanghai", "predicted_winner": "2", "prob_home": 30, "prob_away": 70}
    observed = datetime.now(timezone.utc).isoformat()
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_settlement_evidence)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    market = {"match_date": DAY, "player_home": "Beth Brown", "player_away": "Alice Adams",
              "odds_home": 1.55, "odds_away": 2.60, "source": "TheOddsAPI", "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"},
              "period": "match", "event_id": "original-event", "bookmaker": "book", "observed_at": observed}
    attach_synthetic_settlement_evidence(market)
    monkeypatch.setattr(warehouse, "cached_fetch", lambda key, fn: [prediction] if key == "betclan" else [])
    monkeypatch.setattr(warehouse, "fetch_the_odds_api_rows", lambda d: [market])
    monkeypatch.setattr(warehouse, "fetch_comparison_rows", lambda d: [])
    live = warehouse.build_live_rows(target_date=DAY)
    assert len(live) == 1
    # CSV is the actual warehouse interchange; it must not replace observation time.
    restored = pd.read_csv(__import__("io").StringIO(live.to_csv(index=False))).iloc[0]
    row = restored.copy()
    row["predicted_winner"] = "player_b"
    pick = miner.select_player_from_row(row, DAY)
    quote = miner.warehouse_selected_quote(row, pick, 1.55, "TheOddsAPI", "book")
    assert quote["observed_at"] == observed and quote["event_id"] == "original-event"
    assert quote["provider_side"] == "1" and quote["selection"] == "Beth Brown"
    pick.update(odds=1.55, odds_source="TheOddsAPI", odds_bookmaker="book", selected_quote=quote)
    assert tickets.selected_quote_problem(pick, require_provenance=True) == ""
    assert tickets.selected_quote_problem(dict(pick, selected_quote=None), require_provenance=True)
    assert tickets.selected_quote_problem(dict(pick, selected_quote=dict(quote, observed_at=None)), require_provenance=True)
    assert tickets.selected_quote_problem(dict(pick, selected_quote=dict(quote, market=None)), require_provenance=True)


def test_matching_uses_offset_aware_kickoff_for_date_rollover():
    card = pd.Series({"match_date": "2026-09-12", "player_home": "Frances Tiafoe", "player_away": "Ben Shelton"})
    api = {"match_date": "2026-09-11", "commence_time": "2026-09-11T22:30:00Z", "player_home": "Ben Shelton", "player_away": "Frances Tiafoe"}
    assert warehouse._match_api_odds_row(card, [api]) == (api, True)
    assert warehouse._match_api_odds_row(card, [dict(api, commence_time="2026-09-11T22:30:00")])[0] is None
    assert warehouse._match_api_odds_row(card, [dict(api, commence_time="2026-09-11T10:30:00Z")])[0] is None


def test_rebuild_staging_is_pure_and_requires_complete_fresh_evidence(tmp_path, monkeypatch):
    import copy
    import pytest
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    monkeypatch.setattr(tickets, "LOCALDATA", tmp_path)
    day = datetime.now(tickets.TZ).date().isoformat()
    now = datetime.fromisoformat(day + "T08:00:00+02:00")
    monkeypatch.setattr(tickets, "now_local", lambda: now)
    evidence = {"status": "complete", "date": day, "observed_at": now.isoformat()}
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_settlement_evidence)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    state = {"bank": 100.0, "paper_bank": 100.0, "open_slips": [], "history": []}
    def pick(name, other, odds):
        quote = {"odds": odds, "source": "TheOddsAPI", "bookmaker": "book", "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "period": "match",
                 "selection": name, "provider_home": name, "provider_away": other, "provider_side": "1",
                 "event_id": name + "-fixture", "fixture_date": day, "observed_at": now.isoformat()}
        attach_synthetic_settlement_evidence(quote)
        return {"date": day, "match": name + " vs " + other, "kickoff": "12:00", "selected_player": name,
                "odds": odds, "odds_source": "TheOddsAPI", "odds_bookmaker": "book", "bucket": "WATCHLIST",
                "confidence": 75, "prediction_prob": .75, "selected_quote": quote}
    picks = [pick("Alice Adams", "Beth Brown", 1.55), pick("Clara Clark", "Diana Davies", 1.60)]
    originals = copy.deepcopy((picks, state))
    proposal = tickets.stage_rebuild_evaluation(picks, state, day, now, evidence)
    assert proposal["status"] == "PROPOSED" and len(proposal["accas"]) == 1
    assert proposal["staked_pct"] == 20.0
    assert (picks, state) == originals and not list(tmp_path.iterdir())
    bad = copy.deepcopy(picks)
    bad[0]["selected_quote"]["observed_at"] = None
    empty = tickets.stage_rebuild_evaluation(bad, state, day, now, evidence)
    assert empty["status"] == "NO BET" and not empty["accas"]
    assert empty["diagnostics"]["admission_rejections"]["selected quote timestamp invalid"] == 1
    assert not list(tmp_path.iterdir())
    for outcome in ("failed", "partial", "unknown"):
        with pytest.raises(ValueError, match="fresh collection"):
            tickets.stage_rebuild_evaluation(picks, state, day, now, dict(evidence, status=outcome))
    with pytest.raises(ValueError, match="current configured betting day"):
        tickets.stage_rebuild_evaluation(picks, state, "2000-01-01", now, evidence)


def test_rebuild_strict_selected_quote_rejects_opponent_and_missing_identity(monkeypatch):
    now = tickets.now_local()
    day = now.date().isoformat()
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_settlement_evidence)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    q = {"odds": 1.55, "source": "TheOddsAPI", "bookmaker": "book", "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "period": "match",
         "selection": "Alice Adams", "provider_home": "Beth Brown", "provider_away": "Alice Adams",
         "provider_side": "2", "event_id": "event", "fixture_date": day, "observed_at": now.isoformat()}
    attach_synthetic_settlement_evidence(q)
    pick = {"odds": 1.55, "odds_source": "TheOddsAPI", "odds_bookmaker": "book",
            "selected_player": "Alice Adams", "date": day, "selected_quote": q}
    assert tickets.selected_quote_problem(pick, require_provenance=True) == ""
    assert tickets.selected_quote_problem(dict(pick, selected_quote=dict(q, provider_side="1")), require_provenance=True) == "selected quote provider side mismatch"
    assert tickets.selected_quote_problem(dict(pick, selected_quote=dict(q, provider_home="")), require_provenance=True) == "selected quote provider orientation missing"


def _local_rebuild_case(tmp_path, monkeypatch, *, picks=True):
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_settlement_evidence)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    now = tickets.now_local().replace(hour=8, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(tickets, "now_local", lambda: now)
    day = now.date().isoformat()
    old = {"date": day, "frozen": True, "accas": [], "staked_pct": 0, "paper_staked_pct": 0}
    state = {"bank": 100, "paper_bank": 100, "open_slips": [], "history": [{"date": "2026-09-25", "accas": []}]}
    card = tmp_path / f"auto_tickets_{day}.json"
    sp = tmp_path / "auto_tickets_state.json"
    card.write_text(json.dumps(old)); sp.write_text(json.dumps(state))
    (tmp_path / "auto_tickets_today.json").write_text(json.dumps(old))
    def item(name, other, price):
        q = {"odds": price, "source": "TheOddsAPI", "bookmaker": "book", "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}, "period": "match",
             "selection": name, "provider_home": name, "provider_away": other, "provider_side": "1",
             "event_id": name, "fixture_date": day, "observed_at": now.isoformat()}
        attach_synthetic_settlement_evidence(q)
        return {"date": day, "match": name + " vs " + other, "kickoff": "12:00", "selected_player": name,
                "odds": price, "odds_source": "TheOddsAPI", "odds_bookmaker": "book", "bucket": "WATCHLIST",
                "confidence": 75, "prediction_prob": .75, "selected_quote": q}
    rows = [item("Alice Adams", "Beth Brown", 1.55), item("Clara Clark", "Diana Davies", 1.6)] if picks else []
    proposal = tickets.stage_rebuild_evaluation(rows, state, day, now,
                    {"status": "complete", "date": day, "observed_at": now.isoformat()})
    return day, card, sp, proposal, tickets._rebuild_digest(card.read_bytes()), tickets._rebuild_digest(sp.read_bytes())


def test_local_empty_rebuild_and_retry_book_only_once(tmp_path, monkeypatch):
    day, card, state_path, proposal, card_sha, state_sha = _local_rebuild_case(tmp_path, monkeypatch)
    kwargs = {"expected_card_digest": card_sha, "expected_state_digest": state_sha, "run_id": "synthetic-1"}
    assert tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs) == "committed"
    result = json.loads(card.read_text())
    state = json.loads(state_path.read_text())
    assert result == json.loads((tmp_path / "auto_tickets_today.json").read_text())
    assert result["status"] == "CURRENT" and len(result["accas"]) == 1
    assert len(state["open_slips"]) == 1 and state["history"] == [{"date": "2026-09-25", "accas": []}]
    archive = json.loads((tmp_path / result["prior_archive"]).read_text())
    assert archive["actionable"] is False and archive["original_card_sha256"] == card_sha
    assert tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs) == "already_committed"
    assert len(json.loads(state_path.read_text())["open_slips"]) == 1


def test_local_empty_no_bet_published_and_other_date_untouched(tmp_path, monkeypatch):
    day, card, state_path, proposal, card_sha, state_sha = _local_rebuild_case(tmp_path, monkeypatch, picks=False)
    unrelated = tmp_path / "auto_tickets_2026-09-25.json"
    unrelated.write_bytes(b"unchanged")
    before_state = json.loads(state_path.read_text())
    assert tickets.publish_staged_rebuild_local(tmp_path, day, proposal, expected_card_digest=card_sha,
                      expected_state_digest=state_sha, run_id="empty") == "committed"
    assert json.loads(card.read_text())["status"] == "NO BET"
    assert json.loads(state_path.read_text()) == before_state and unrelated.read_bytes() == b"unchanged"


def test_local_rebuild_aborts_when_state_changes_or_lock_is_held(tmp_path, monkeypatch):
    import fcntl
    import pytest
    day, card, state_path, proposal, card_sha, state_sha = _local_rebuild_case(tmp_path, monkeypatch)
    state_path.write_text(json.dumps({"bank": 90, "open_slips": [], "history": []}))
    before = card.read_bytes()
    kwargs = {"expected_card_digest": card_sha, "expected_state_digest": state_sha, "run_id": "race"}
    with pytest.raises(RuntimeError, match="changed during staging"):
        tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs)
    assert card.read_bytes() == before
    lockpath = tmp_path / ".auto_tickets_rebuild" / "publication.lock"
    with lockpath.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="another local rebuild"):
            tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs)


def test_local_interrupted_rebuild_rolls_back_then_retries(tmp_path, monkeypatch):
    import pytest
    day, card, state_path, proposal, card_sha, state_sha = _local_rebuild_case(tmp_path, monkeypatch)
    old_card, old_state = card.read_bytes(), state_path.read_bytes()
    atomic = tickets._rebuild_atomic_bytes
    writes = []
    def interrupted(path, data):
        atomic(path, data)
        if path == card:
            writes.append(path)
            if len(writes) == 2:
                raise SystemExit("synthetic hard interruption")
    monkeypatch.setattr(tickets, "_rebuild_atomic_bytes", interrupted)
    kwargs = {"expected_card_digest": card_sha, "expected_state_digest": state_sha, "run_id": "interrupted"}
    with pytest.raises(SystemExit, match="synthetic hard interruption"):
        tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs)
    assert json.loads((tmp_path / ".auto_tickets_rebuild/journal.json").read_text())["status"] == "applying"
    monkeypatch.setattr(tickets, "_rebuild_atomic_bytes", atomic)
    tickets._recover_rebuild_journal(tmp_path)
    assert card.read_bytes() == old_card and state_path.read_bytes() == old_state
    assert tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs) == "committed"
    assert len(json.loads(state_path.read_text())["open_slips"]) == 1


def test_local_rebuild_preserves_exposed_and_ambiguous_records(tmp_path, monkeypatch):
    import pytest
    day, card, state_path, proposal, _, _ = _local_rebuild_case(tmp_path, monkeypatch)
    old = json.loads(card.read_text())
    old["accas"] = [{"legs": [{"selected_player": "Already recorded"}]}]
    card.write_text(json.dumps(old))
    before_card, before_state = card.read_bytes(), state_path.read_bytes()
    with pytest.raises(RuntimeError, match="authoritative unplaced"):
        tickets.publish_staged_rebuild_local(tmp_path, day, proposal, run_id="ambiguous",
            expected_card_digest=tickets._rebuild_digest(before_card), expected_state_digest=tickets._rebuild_digest(before_state))
    assert card.read_bytes() == before_card and state_path.read_bytes() == before_state
    old["accas"] = []
    card.write_text(json.dumps(old))
    state = json.loads(state_path.read_text())
    state["open_slips"].append({"date": day, "staked_pct": 1, "accas": []})
    state_path.write_text(json.dumps(state))
    before_card, before_state = card.read_bytes(), state_path.read_bytes()
    with pytest.raises(RuntimeError, match="same-day open or settled"):
        tickets.publish_staged_rebuild_local(tmp_path, day, proposal, run_id="exposed",
            expected_card_digest=tickets._rebuild_digest(before_card), expected_state_digest=tickets._rebuild_digest(before_state))
    assert card.read_bytes() == before_card and state_path.read_bytes() == before_state


def test_local_rebuild_reused_transaction_id_cannot_change_proposal(tmp_path, monkeypatch):
    import pytest
    day, card, state, proposal, card_sha, state_sha = _local_rebuild_case(tmp_path, monkeypatch, picks=False)
    kwargs = {"expected_card_digest": card_sha, "expected_state_digest": state_sha, "run_id": "same-id"}
    assert tickets.publish_staged_rebuild_local(tmp_path, day, proposal, **kwargs) == "committed"
    changed = dict(proposal, diagnostics={"tampered": True})
    with pytest.raises(RuntimeError, match="different proposal"):
        tickets.publish_staged_rebuild_local(tmp_path, day, changed, **kwargs)


def test_guarded_runner_empty_card_success_and_failures_are_isolated(tmp_path, monkeypatch):
    import copy
    import pytest
    from scripts import daily
    day, card, state_path, _, _, _ = _local_rebuild_case(tmp_path, monkeypatch, picks=False)
    now = tickets.now_local()
    monkeypatch.setattr(daily, "LOCALDATA", tmp_path)
    monkeypatch.setattr(daily, "_now_local", lambda: now)
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_SERIALIZED", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    collection = {"status": "complete", "date": day, "observed_at": now.isoformat(), "outcomes": {},
                  "predictions": [], "comparisons": []}
    monkeypatch.setattr(daily, "collect_rebuild_sources", lambda d, **kwargs: copy.deepcopy(collection))
    monkeypatch.setattr(daily, "stage_fresh_rebuild_picks", lambda *a: [])
    before_state = state_path.read_bytes()
    daily.run_guarded_empty_rebuild(day)
    assert json.loads(card.read_text())["status"] == "NO BET"
    assert json.loads(state_path.read_bytes()) == json.loads(before_state)
    # A distinct run with required-source failure must not publish or alter the old card.
    monkeypatch.setenv("GITHUB_RUN_ID", "12346")
    old = {"date": day, "frozen": True, "accas": [], "staked_pct": 0, "paper_staked_pct": 0}
    card.write_text(json.dumps(old))
    before_card = card.read_bytes()
    monkeypatch.setattr(daily, "collect_rebuild_sources", lambda d, **kwargs: dict(collection, status="insufficient"))
    with pytest.raises(RuntimeError, match="required source collection incomplete"):
        daily.run_guarded_empty_rebuild(day)
    assert card.read_bytes() == before_card and json.loads(state_path.read_bytes()) == json.loads(before_state)


def test_guarded_runner_state_change_during_staging_aborts(tmp_path, monkeypatch):
    import pytest
    from scripts import daily
    day, card, state_path, _, _, _ = _local_rebuild_case(tmp_path, monkeypatch, picks=False)
    now = tickets.now_local()
    monkeypatch.setattr(daily, "LOCALDATA", tmp_path)
    monkeypatch.setattr(daily, "_now_local", lambda: now)
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_SERIALIZED", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(daily, "collect_rebuild_sources", lambda d, **kwargs: {"status": "complete", "date": day,
        "observed_at": now.isoformat(), "outcomes": {}, "predictions": [], "comparisons": []})
    def mutate(*args):
        state_path.write_text(json.dumps({"bank": 90, "open_slips": [], "history": []}))
        return []
    monkeypatch.setattr(daily, "stage_fresh_rebuild_picks", mutate)
    before = card.read_bytes()
    with pytest.raises(RuntimeError, match="changed during staging"):
        daily.run_guarded_empty_rebuild(day)
    assert card.read_bytes() == before


def test_staged_miner_uses_only_supplied_fresh_sources(tmp_path, monkeypatch):
    import os
    import subprocess
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_schedule_evidence,
                                           attach_synthetic_settlement_evidence)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    from scripts import daily
    day = tickets.now_local().date().isoformat()
    obs = datetime.now(timezone.utc).isoformat()
    local = tmp_path / "localdata"
    local.mkdir()
    monkeypatch.setattr(daily, "LOCALDATA", local)
    hist = [{"match_date": "2025-10-01", "player_a": "Historical Alice", "player_b": "Historical Beth",
             "tour": "ATP", "tournament": "ATP Shanghai", "_series": "ATP", "_surface": "Hard",
             "winner": "Historical Alice", "odds_a": 1.5, "odds_b": 2.5, "_is_live": False,
             "predicted_winner_live": "player_a", "prediction_prob_live": 70, "rank_a": 5, "rank_b": 20}]
    pd.DataFrame(hist).to_csv(local / "warehouse.csv.gz", index=False, compression="gzip")
    preds = [{"match_date": day, "match_time": "12:00", "player_home": "Alice Adams", "player_away": "Beth Brown",
              "tournament": "ATP Shanghai", "surface": "Hard", "predicted_winner": "1", "prob_home": 70,
              "prob_away": 30, "source": "BetClan"}]
    quotes = [{"match_date": day, "player_home": "Alice Adams", "player_away": "Beth Brown", "odds_home": 1.5,
               "odds_away": 2.5, "source": "BetExplorer", "bookmaker": "testbook", "market": "h2h", "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"},
               "period": "match", "event_id": "fake-fixture", "observed_at": obs,
               "match_url": "/tennis/atp/test/alice-beth/fake-fixture/", "market_id": "synthetic",
               "raw_market": "match winner", "raw_period": "match", "retrieved_at": obs,
               "provider_price_updated_at": ["100", "101"], "response_sha256": "a" * 64,
               "bookmaker_id": "synthetic-book", "match_time": "12:00",
               "commence_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}]
    attach_synthetic_settlement_evidence(quotes[0])
    attach_synthetic_schedule_evidence(quotes[0])
    # In addition to empty credentials, block Python sockets and native HTTP
    # clients in the subprocess before its imports; no external provider calls.
    isolation = tmp_path / "isolation"
    isolation.mkdir()
    (isolation / "sitecustomize.py").write_text('''import socket, os\nfrom pathlib import Path\ndef deny(*a, **k): raise RuntimeError("offline subprocess")\nsocket.socket.connect = deny\nsocket.socket.connect_ex = deny\ntry:\n import requests\n requests.sessions.Session.request = deny\n from curl_cffi import requests as curl_requests\n curl_requests.Session.request = deny\nexcept ImportError:\n pass\nfrom racketfactory import ml, execution\nml.LOCALDATA = Path(os.environ["RACKET_TEST_ML_DATA"])\n_orig_settle = execution.settlement_evidence_problem\n_orig_schedule = execution.betexplorer_schedule_evidence_problem\nexecution.settlement_evidence_problem = lambda q, *a, **k: "" if (q.get("settlement_evidence") or {}).get("test_only") else _orig_settle(q, *a, **k)\nexecution.betexplorer_schedule_evidence_problem = lambda q, *a, **k: "" if (q.get("schedule_evidence") or {}).get("test_only") else _orig_schedule(q, *a, **k)\n''')
    real_run = subprocess.run
    def run(*args, **kwargs):
        env = dict(kwargs["env"])
        env["PYTHONPATH"] = str(isolation) + os.pathsep + env["PYTHONPATH"]
        env["RACKET_TEST_ML_DATA"] = str(local)
        kwargs["env"] = env
        return real_run(*args, **kwargs)
    monkeypatch.setattr(daily.subprocess, "run", run)
    picks = daily.stage_fresh_rebuild_picks(day, {"status": "complete", "date": day, "predictions": preds,
        "raw_quotes": {"betexplorer": quotes, "oddsportal": []}}, tmp_path / "staging")
    assert len(picks) == 1
    assert picks[0]["selected_quote"]["observed_at"] == obs
    assert picks[0]["selected_quote"]["event_id"] == "fake-fixture"
    assert picks[0]["selected_quote"]["market"] == "h2h"
    assert picks[0]["selected_quote"]["period"] == "match"
    assert picks[0]["selected_quote"]["price_type"] == "bookmaker"
    assert picks[0]["selected_quote"]["settlement_rules"] == {"retirement": "advancer", "walkover": "void", "cancelled": "void"}
    assert picks[0]["selected_quote"]["provider_side"] == "1"
    assert picks[0]["selected_quote"]["response_sha256"] == "a" * 64
    assert picks[0]["selected_quote"]["match_url"] == quotes[0]["match_url"]
    assert picks[0]["selected_quote"]["provider_price_updated_at"] == ["100", "101"]
    assert tickets.selected_quote_problem(picks[0], require_provenance=True) == ""
    assert (tmp_path / "staging" / f"picks_{day}.json").exists()
    assert not list(local.glob("picks*.json"))


def test_guarded_staging_does_not_let_unproven_primary_hide_valid_optional(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import daily
    from racketfactory import warehouse
    day = tickets.now_local().date().isoformat()
    observed = datetime.now(timezone.utc).isoformat()
    local = tmp_path / "localdata"; local.mkdir()
    monkeypatch.setattr(daily, "LOCALDATA", local)
    pd.DataFrame([{"match_date": "2025-01-01", "winner": "Alice", "_is_live": False},
                  {"match_date": day, "winner": "Stale result", "_is_live": False}]).to_csv(local / "warehouse.csv.gz", index=False)
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_settlement_evidence)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    common = {"match_date": day, "player_home": "Alice Adams", "player_away": "Beth Brown",
              "odds_home": 1.5, "odds_away": 2.5, "observed_at": observed, "bookmaker": "book"}
    unproven = dict(common, source="BetExplorer")
    valid = dict(common, source="OddsPortal", market="h2h", period="match", price_type="bookmaker", settlement_rules={"retirement": "advancer", "walkover": "void", "cancelled": "void"})
    attach_synthetic_settlement_evidence(valid)
    used = []
    def build(**kwargs):
        used.extend(kwargs["supplied_comparisons"])
        return pd.DataFrame()
    monkeypatch.setattr(warehouse, "build_live_rows", build)
    def miner(cmd, **kwargs):
        staged = pd.read_csv(cmd[cmd.index("--warehouse") + 1])
        assert day not in staged["match_date"].astype(str).tolist()
        out = cmd[cmd.index("--output-dir") + 1]
        ( __import__("pathlib").Path(out) / f"picks_{day}.json").write_text("[]")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess, "run", miner)
    result = daily.stage_fresh_rebuild_picks(day, {"status": "complete", "predictions": [],
        "raw_quotes": {"betexplorer": [unproven], "oddsportal": [valid]}}, tmp_path / "stage")
    assert result == [] and len(used) == 1 and used[0]["source"] == "OddsPortal"


def test_guarded_runner_committed_retry_is_a_noop(tmp_path, monkeypatch):
    import pytest
    import base64
    import subprocess
    from types import SimpleNamespace
    from scripts import daily
    day, card, state, _, _, _ = _local_rebuild_case(tmp_path, monkeypatch, picks=False)
    monkeypatch.setattr(daily, "LOCALDATA", tmp_path)
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_SERIALIZED", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    original = card.read_bytes()
    txn = tmp_path / ".auto_tickets_rebuild"; txn.mkdir()
    archive = txn / f"archive_{day}_12345.json"
    archive.write_text(json.dumps({"transaction_id": "12345", "actionable": False,
        "original_card_base64": base64.b64encode(original).decode(), "original_card_sha256": tickets._rebuild_digest(original)}))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=archive.read_bytes()))
    def no_collection(*a): raise AssertionError("a committed retry must not recollect")
    monkeypatch.setattr(daily, "collect_rebuild_sources", no_collection)
    before = card.read_bytes(), state.read_bytes()
    daily.run_guarded_empty_rebuild(day)
    assert (card.read_bytes(), state.read_bytes()) == before
    # An archive not present at HEAD cannot be treated as a durable commit.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=b""))
    with pytest.raises(RuntimeError, match="uncommitted rebuild archive"):
        daily.run_guarded_empty_rebuild(day)


def test_offline_required_source_to_two_leg_ticket_doctor_and_blocked_states(tmp_path, monkeypatch):
    """Synthetic end-to-end plumbing; it does not authenticate BetExplorer or bookmaker terms."""
    import os
    import pytest
    import subprocess
    from pathlib import Path
    from racketfactory import doctor, execution
    from racketfactory.kickoff import SAST
    from racketfactory.sources import betclan, betexplorer, oddsportal_upcoming, predixsport
    from scripts import daily
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_schedule_evidence,
                                           attach_synthetic_settlement_evidence)

    day = tickets.now_local().date().isoformat()
    now = tickets.now_local()
    observation = datetime.now(timezone.utc).isoformat()
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    monkeypatch.setattr(tickets, "_ML_AVAILABLE", False)
    monkeypatch.setattr(daily, "LOCALDATA", tmp_path / "localdata")
    daily.LOCALDATA.mkdir()
    monkeypatch.setattr(doctor, "LOCALDATA", tmp_path / "localdata")
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", observation)
    monkeypatch.delenv(betexplorer.DISABLE_ENV, raising=False)
    monkeypatch.setenv(oddsportal_upcoming.DISABLE_ENV, "1")

    identities = [
        ("Alice Adams", "Beth Brown", "fixture-1", "ATP Shanghai"),
        ("Clara Clark", "Diana Davies", "fixture-2", "WTA Beijing"),
    ]
    predictions = [{"match_date": day, "match_time": "23:30", "player_home": home,
                    "player_away": away, "tournament": event, "surface": "Hard",
                    "predicted_winner": "1", "prob_home": 72, "prob_away": 28}
                   for home, away, _, event in identities]

    class Clan:
        last_collection_status = "complete"
        def fetch_daily(self): return predictions
    class Predix:
        def fetch_daily(self): return []
    monkeypatch.setattr(betclan, "BetClanPredictor", Clan)
    monkeypatch.setattr(predixsport, "PredixSportPredictor", Predix)

    listing = []
    quote_by_event = {}
    for home, away, event_id, event in identities:
        commence = datetime.combine(datetime.fromisoformat(day).date(), datetime.strptime("23:30", "%H:%M").time(), SAST)
        fixture = {"match_date": day, "match_time": "23:30", "commence_time": commence.isoformat(),
                   "listing_date": day, "listing_schedule_status": "verified",
                   "player_home": home, "player_away": away, "event_id": event_id,
                   "match_url": f"/tennis/atp/{event.lower().replace(' ', '-')}/{home.split()[0].lower()}-{away.split()[0].lower()}/{event_id}/",
                   "odds_home": 1.55, "odds_away": 2.60, "tournament": event}
        listing.append(fixture)
        quote = {**fixture, "source": "BetExplorer", "market": "h2h", "period": "match",
                 "price_type": "bookmaker", "bookmaker": "Synthetic Book", "bookmaker_id": "synthetic-book",
                 "operator_jurisdiction": "TEST", "observed_at": observation,
                 "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"},
                 "raw_market": "match winner", "raw_period": "match",
                 "response_sha256": "a" * 64}
        attach_synthetic_settlement_evidence(quote)
        attach_synthetic_schedule_evidence(quote)
        quote_by_event[event_id] = quote

    def fetch_listing(target, *, with_status=False, metrics=None):
        if isinstance(metrics, dict):
            metrics.update({"data_dt_rows": 2, "row_accounting_total": 2, "row_accounting_complete": True,
                            "listing_recognition_status": "complete", "listing_coverage_status": "complete",
                            "schedule_verification_status": "verified", "actionable_schedule_unverified_rows": 0})
        return listing, "complete"
    monkeypatch.setattr(betexplorer, "_fetch_live", fetch_listing)
    monkeypatch.setattr(betexplorer, "fetch_fixture_comparison",
                        lambda fixture, **kwargs: [quote_by_event[fixture["event_id"]]])

    # Each actual provider row is synthetic, tagged test_only, and accepted only
    # at the mocked authentication boundary; all existing pipeline functions run.
    collection = daily.collect_rebuild_sources(day)
    assert collection["status"] == "complete"
    assert collection["required_source_readiness"] == {
        "status": "ready", "sources": {"betclan": "complete", "betexplorer": "complete"}, "blockers": []}
    assert collection["prediction_set"]["status"] == "captured_snapshot"
    assert collection["prediction_set"]["unique_fixture_keys"] == 2
    assert len(collection["prediction_set"]["sha256"]) == 64
    assert collection["outcomes"]["betexplorer"]["parser_counts"]["row_accounting_complete"] is True
    assert collection["outcomes"]["betexplorer"]["detail_counts"]["stage_statuses"]["required_source_readiness"] == "ready"
    assert len(collection["raw_quotes"]["betexplorer"]) == 2
    assert all(row["settlement_evidence"]["test_only"] and row["schedule_evidence"]["test_only"]
               for row in collection["raw_quotes"]["betexplorer"])

    # Offline subprocess uses the real miner against the staged warehouse.
    hist = [{"match_date": "2025-10-01", "player_a": "Historical Alice", "player_b": "Historical Beth",
             "tour": "ATP", "tournament": "ATP Shanghai", "_series": "ATP", "_surface": "Hard",
             "winner": "Historical Alice", "odds_a": 1.5, "odds_b": 2.5, "_is_live": False,
             "predicted_winner_live": "player_a", "prediction_prob_live": 70, "rank_a": 5, "rank_b": 20}]
    pd.DataFrame(hist).to_csv(daily.LOCALDATA / "warehouse.csv.gz", index=False, compression="gzip")
    isolation = tmp_path / "isolation"
    isolation.mkdir()
    (isolation / "sitecustomize.py").write_text('''import socket, os\nfrom pathlib import Path\ndef deny(*a, **k): raise RuntimeError("offline subprocess")\nsocket.socket.connect = deny\nsocket.socket.connect_ex = deny\ntry:\n import requests\n requests.sessions.Session.request = deny\n from curl_cffi import requests as cr\n cr.Session.request = deny\nexcept ImportError:\n pass\nfrom racketfactory import ml, execution\nml.LOCALDATA = Path(os.environ["RACKET_TEST_ML_DATA"])\n_s = execution.settlement_evidence_problem\n_e = execution.betexplorer_schedule_evidence_problem\nexecution.settlement_evidence_problem = lambda q, *a, **k: "" if (q.get("settlement_evidence") or {}).get("test_only") else _s(q, *a, **k)\nexecution.betexplorer_schedule_evidence_problem = lambda q, *a, **k: "" if (q.get("schedule_evidence") or {}).get("test_only") else _e(q, *a, **k)\n''')
    real_run = subprocess.run
    def offline_run(*args, **kwargs):
        env = dict(kwargs["env"])
        env["PYTHONPATH"] = str(isolation) + os.pathsep + env["PYTHONPATH"]
        env["RACKET_TEST_ML_DATA"] = str(daily.LOCALDATA)
        kwargs["env"] = env
        return real_run(*args, **kwargs)
    monkeypatch.setattr(daily.subprocess, "run", offline_run)

    picks = daily.stage_fresh_rebuild_picks(day, collection, tmp_path / "stage")
    assert len(picks) == 2
    assert all(float(pick["odds"]) >= 1.30 for pick in picks)
    state = {"bank": 100.0, "paper_bank": 100.0, "open_slips": [], "history": []}
    original = json.dumps((picks, state), sort_keys=True)
    evaluation = tickets.stage_rebuild_evaluation(
        picks, state, day, now,
        {"status": "complete", "date": day, "observed_at": collection["observed_at"]})
    assert evaluation["status"] == "PROPOSED" and len(evaluation["accas"]) == 1
    assert len(evaluation["accas"][0]["legs"]) == 2
    assert json.dumps((picks, state), sort_keys=True) == original  # no card/state mutation

    no_bet = tickets.stage_rebuild_evaluation(
        [], state, day, now,
        {"status": "complete", "date": day, "observed_at": collection["observed_at"]})
    assert no_bet["status"] == "NO BET" and no_bet["accas"] == []
    unproven = json.loads(json.dumps(picks))
    unproven[0]["selected_quote"].pop("settlement_evidence", None)
    blocked = tickets.stage_rebuild_evaluation(
        [unproven[0]], state, day, now,
        {"status": "complete", "date": day, "observed_at": collection["observed_at"]})
    assert blocked["status"] == "NO BET" and not blocked["accas"]
    assert blocked["diagnostics"]["admission_rejections"]["bookmaker settlement rules evidence missing"] == 1
    with pytest.raises(ValueError, match="fresh collection"):
        tickets.stage_rebuild_evaluation(picks, state, day, now,
            {"status": "partial", "date": day, "observed_at": collection["observed_at"]})

    # Doctor consumes only a temporary current-run evaluation record.
    ticket_file = daily.LOCALDATA / f"auto_tickets_{day}.json"
    ticket_file.write_text(json.dumps({"date": day, "generated_at": now.isoformat(),
                                      "diagnostics": evaluation["diagnostics"], "accas": evaluation["accas"]}))
    evaluation_file = daily.LOCALDATA / f"auto_tickets_evaluation_{day}.json"
    evaluation_file.write_text(json.dumps({"date": day, "run_as_of": observation,
                                           "evaluated_at": now.isoformat(), "collection_status": "complete",
                                           "publication": "staged_only", "diagnostics": evaluation["diagnostics"]}))
    doctor_checks = doctor.run_health_checks(as_of=day)
    assert doctor_checks["tickets:current_evaluation"]["status"] == "OK"
    # Unproven/incomplete evaluation cannot be presented as Doctor-ready.
    evaluation_file.write_text(json.dumps({"date": day, "run_as_of": observation,
                                           "evaluated_at": now.isoformat(), "collection_status": "partial",
                                           "publication": "retained_previous_card", "diagnostics": {}}))
    doctor_checks = doctor.run_health_checks(as_of=day)
    assert doctor_checks["tickets:current_evaluation"]["status"] == "CRITICAL"

    # Distinct incomplete/unproven required-source states remain blocked.
    class PartialClan(Clan):
        last_collection_status = "partial"
    monkeypatch.setattr(betclan, "BetClanPredictor", PartialClan)
    partial_betclan = daily.collect_rebuild_sources(day)
    assert partial_betclan["status"] == "insufficient"
    assert partial_betclan["required_source_readiness"]["status"] == "blocked"
    assert partial_betclan["required_source_readiness"]["sources"]["betclan"] == "partial"

    monkeypatch.setattr(betclan, "BetClanPredictor", Clan)
    monkeypatch.setattr(betexplorer, "_fetch_live", lambda *a, **kw: (listing, "partial"))
    skipped_listing = daily.collect_rebuild_sources(day)
    assert skipped_listing["status"] == "insufficient"
    assert skipped_listing["outcomes"]["betexplorer"]["detail_counts"]["attempted"] == 0
    assert skipped_listing["outcomes"]["betexplorer"]["detail_counts"]["unexamined_fixtures"] == 2

    monkeypatch.setattr(betexplorer, "_fetch_live", fetch_listing)
    quote_by_event = {key: dict(value) for key, value in quote_by_event.items()}
    for quote in quote_by_event.values():
        quote.pop("settlement_evidence", None)
    unproven_quotes = daily.collect_rebuild_sources(day)
    assert unproven_quotes["status"] == "insufficient"
    assert unproven_quotes["required_source_readiness"]["status"] == "blocked"
    assert unproven_quotes["outcomes"]["betexplorer"]["detail_counts"]["stage_statuses"]["quote_resolution"] == "partial"
