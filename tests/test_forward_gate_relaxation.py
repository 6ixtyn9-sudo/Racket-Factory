"""Synthetic offline coverage for forward-only partial-coverage card admission.

No provider traffic, publication, grading, or mutation of repository localdata.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from racketfactory.quote_enrichment import collect_prediction_comparisons
from racketfactory.sources import betexplorer
from scripts import auto_tickets
from scripts.mine_edges import warehouse_selected_quote

DAY = datetime.now(auto_tickets.TZ).date().isoformat()


def _schedule(event_id, home, away, observed, start, url):
    return {
        "status": "candidate_unverified_contract",
        "provider": "BetExplorer", "event_id": event_id, "match_url": url,
        "player_home": home, "player_away": away, "sport": "Tennis",
        "event_status": "https://schema.org/EventScheduled",
        "contradictory_signals": [], "commence_time": start.isoformat(),
        "observed_at": observed.isoformat(), "response_sha256": "a" * 64,
        "contract_validation": "not_authenticated",
    }


def _pick_from_offer(offer, target, selected="home"):
    home, away = offer["player_home"], offer["player_away"]
    side = "player_a" if selected == "home" else "player_b"
    player = home if selected == "home" else away
    odds = offer["odds_home"] if selected == "home" else offer["odds_away"]
    meta = {key: offer.get(key) for key in (
        "source", "observed_at", "market", "period", "line", "player_home", "player_away",
        "match_date", "match_time", "commence_time", "bookmaker", "price_type", "schedule_evidence",
        "match_url", "response_sha256", "bookmaker_id", "event_id", "odds_home_bookmaker",
        "odds_away_bookmaker")}
    meta["reversed"] = False
    pick = {
        "match": f"{home} vs {away}", "player_home": home, "player_away": away,
        "selected_player": player, "selected_side": side, "date": target,
        "match_date": target, "kickoff": offer["match_time"], "bucket": "WATCHLIST",
        "confidence": 0.75, "odds": odds, "odds_source": offer["source"],
        "odds_bookmaker": offer["bookmaker"], "_is_paper": False,
    }
    pick["selected_quote"] = warehouse_selected_quote(
        pd.Series({"_quote_meta": json.dumps(meta)}), pick, odds,
        offer["source"], offer["bookmaker"])
    return pick


def _offer(event_id, home, away, now, start, *, source="BetExplorer", named=True):
    url = f"/tennis/atp/test/match-{event_id}/{event_id}/"
    bookmaker = "Book One" if named else "BetExplorer Consensus"
    return {
        "event_id": event_id, "match_url": url, "match_date": DAY,
        "match_time": start.strftime("%H:%M"), "commence_time": start.isoformat(),
        "player_home": home, "player_away": away,
        "odds_home": 1.60, "odds_away": 2.25,
        "bookmaker": bookmaker, "bookmaker_id": "book-1" if named else "",
        "odds_home_bookmaker": bookmaker, "odds_away_bookmaker": bookmaker,
        "source": source, "market": "match_winner", "period": "match", "line": None,
        "price_type": "bookmaker" if named else "consensus",
        "observed_at": now.isoformat(), "schedule_evidence": _schedule(
            event_id, home, away, now - timedelta(seconds=10), start, url),
    }


def test_partial_listing_admits_matched_fixture_and_keeps_global_status_partial(monkeypatch):
    now = datetime.now(auto_tickets.TZ)
    first_time = (now + timedelta(hours=3)).strftime("%H:%M")
    second_time = (now + timedelta(hours=4)).strftime("%H:%M")
    monkeypatch.setattr("racketfactory.quote_enrichment._collection_now", lambda: now)
    rows = [
        {"event_id": "one", "match_url": "/tennis/atp/test/match-one/one/", "match_date": DAY,
         "match_time": first_time, "listing_schedule_status": "unverified",
         "player_home": "Alice Adams", "player_away": "Beth Brown"},
        {"event_id": "two", "match_url": "/tennis/atp/event/two/", "match_date": DAY,
         "match_time": second_time, "listing_schedule_status": "unverified",
         "player_home": "Clara Clark", "player_away": "Diana Davies"},
    ]
    predictions = [
        {"match_date": DAY, "match_time": first_time, "player_home": "Alice Adams", "player_away": "Beth Brown"},
        {"match_date": DAY, "match_time": second_time, "player_home": "Completely Other", "player_away": "Names Elsewhere"},
    ]
    observed = now - timedelta(minutes=1)
    first = _offer("one", "Alice Adams", "Beth Brown", observed, now + timedelta(hours=3))
    calls = []
    def details(fixture, **kwargs):
        calls.append(fixture["event_id"])
        return [first]
    monkeypatch.setattr(betexplorer, "fetch_fixture_comparison", details)
    offers, counts, partial = collect_prediction_comparisons(
        DAY, predictions, rows, "partial", {"listing_recognition_status": "partial",
                                               "listing_coverage_status": "partial"})
    assert calls == ["one"]
    assert offers == [first]
    assert counts["matched_fixtures"] == 1
    assert counts["unmatched_or_ambiguous"] == 1
    assert counts["stage_statuses"]["required_source_readiness"] == "blocked"
    assert partial is True


def test_betexplorer_detail_path_can_pass_unverified_listing_schedule(monkeypatch):
    now = datetime.now(auto_tickets.TZ)
    fixture = {"event_id": "one", "match_url": "/tennis/atp/test/match-one/one/", "match_date": DAY,
               "match_time": "12:00", "listing_date": DAY, "listing_schedule_status": "unverified",
               "player_home": "Alice Adams", "player_away": "Beth Brown"}
    calls = []
    pages = iter([("<html>detail</html>", now.isoformat()),
                  (json.dumps({"odds": "<table></table>"}), (now + timedelta(seconds=1)).isoformat())])
    monkeypatch.setattr(betexplorer, "cached_fetch", lambda key, fn, **kwargs: fn())
    monkeypatch.setattr(betexplorer, "_fetch_comparison_document", lambda *a, **kw: next(pages))
    monkeypatch.setattr(betexplorer, "_detail_scheduled_start", lambda *a, **kw: (
        DAY, "12:00", (now + timedelta(hours=2)).isoformat()))
    monkeypatch.setattr(betexplorer, "_comparison_endpoint_candidates", lambda *a, **kw: [{"url": "https://www.betexplorer.com/api"}])
    def parse(payload, updated_fixture, endpoint, observed):
        calls.append(updated_fixture["schedule_evidence"]["status"])
        return ([{**updated_fixture, "odds_home": 1.6, "odds_away": 2.2,
                  "source": "BetExplorer", "bookmaker": "Book One", "price_type": "bookmaker",
                  "market": "match_winner", "period": "match", "line": None,
                  "observed_at": observed}], {"bookmaker_rows": 1})
    monkeypatch.setattr(betexplorer, "_comparison_response_rows", parse)
    rows = betexplorer.fetch_fixture_comparison(fixture)
    assert calls == ["candidate_unverified_contract"]
    assert len(rows) == 1
    assert rows[0]["schedule_evidence"]["contract_validation"] == "not_authenticated"


def test_indicative_and_named_bookmaker_prices_are_distinctly_admissible(monkeypatch):
    now = datetime.now(auto_tickets.TZ)
    monkeypatch.setattr(auto_tickets, "now_local", lambda: now)
    future = now + timedelta(hours=2)
    indicative = _offer("one", "Alice Adams", "Beth Brown", now - timedelta(minutes=1), future, named=False)
    pick = _pick_from_offer(indicative, DAY)
    assert auto_tickets.selected_quote_problem(pick, require_provenance=True) == ""
    assert pick["selected_quote"]["price_type"] == "consensus"
    assert pick["selected_quote"]["bookmaker"] == "BetExplorer Consensus"
    named = _offer("two", "Clara Clark", "Diana Davies", now - timedelta(minutes=1), future)
    named_pick = _pick_from_offer(named, DAY)
    assert auto_tickets.selected_quote_problem(named_pick, require_provenance=True) == ""
    assert named_pick["selected_quote"]["bookmaker"] == "Book One"


def test_wrong_side_stale_invalid_kickoff_started_and_conflicting_schedule_block(monkeypatch):
    now = datetime.now(auto_tickets.TZ)
    monkeypatch.setattr(auto_tickets, "now_local", lambda: now)
    future = now + timedelta(hours=2)
    base_offer = _offer("one", "Alice Adams", "Beth Brown", now - timedelta(minutes=1), future)
    base = _pick_from_offer(base_offer, DAY)
    assert auto_tickets.selected_quote_problem(base, require_provenance=True) == ""

    wrong_side = json.loads(json.dumps(base))
    wrong_side["selected_quote"]["provider_side"] = "2"
    assert "side mismatch" in auto_tickets.selected_quote_problem(wrong_side, require_provenance=True)

    stale = json.loads(json.dumps(base))
    stale["selected_quote"]["observed_at"] = (now - timedelta(minutes=31)).isoformat()
    stale["selected_quote"]["schedule_evidence"]["observed_at"] = (now - timedelta(minutes=32)).isoformat()
    assert "stale" in auto_tickets.selected_quote_problem(stale, require_provenance=True)

    invalid = json.loads(json.dumps(base))
    invalid["odds"] = 1.29
    invalid["selected_quote"]["odds"] = 1.29
    assert not auto_tickets.is_playable(invalid, require_provenance=True)

    unknown = json.loads(json.dumps(base))
    unknown["kickoff"] = ""
    assert auto_tickets.fresh_ticket_problem([{"legs": [unknown, base]}], DAY, now) == "kickoff passed or unknown"

    started = json.loads(json.dumps(base))
    started["kickoff"] = "06:59"
    started["odds_source"] = started["selected_quote"]["source"] = "TheOddsAPI"
    started["selected_quote"].pop("commence_time", None)
    started["selected_quote"].pop("match_time", None)
    started["selected_quote"].pop("schedule_evidence", None)
    assert auto_tickets.fresh_ticket_problem([{"legs": [started, base]}], DAY, now) == "kickoff passed or unknown"

    conflict = json.loads(json.dumps(base))
    conflict["selected_quote"]["schedule_evidence"]["contradictory_signals"] = ["live"]
    assert "conflicting" in auto_tickets.selected_quote_problem(conflict, require_provenance=True)

    time_conflict = json.loads(json.dumps(base))
    later = future + timedelta(hours=1)
    time_conflict["selected_quote"]["commence_time"] = later.isoformat()
    time_conflict["selected_quote"]["schedule_evidence"]["commence_time"] = later.isoformat()
    assert "kickoff conflicts" in auto_tickets.selected_quote_problem(time_conflict, require_provenance=True)


def _write_run_inputs(tmp_path, picks, now, *, collection_status="partial", existing=None):
    run = now.isoformat()
    (tmp_path / f"picks_{DAY}.json").write_text(json.dumps(picks))
    (tmp_path / f".pick_run_status_{DAY}.json").write_text(json.dumps({"run_as_of": run, "fresh_picks": len(picks)}))
    collection = {"run_as_of": run, "status": collection_status,
                  "oldest_offer_observed_at": (now - timedelta(minutes=1)).isoformat(),
                  "observed_offer_count": 2}
    (tmp_path / f"quote_collection_{DAY}.json").write_text(json.dumps(collection))
    state = {"bank": 100.0, "paper_bank": 100.0, "history": [], "open_slips": [], "events": []}
    (tmp_path / "auto_tickets_state.json").write_text(json.dumps(state))
    if existing is not None:
        (tmp_path / f"auto_tickets_{DAY}.json").write_text(json.dumps(existing))
    return run


def test_real_builder_and_ticket_write_entrypoint_saves_partial_coverage_card(monkeypatch, tmp_path):
    """Synthetic evidence through collection, existing bucket selector, and main()."""
    now = datetime.now(auto_tickets.TZ)
    monkeypatch.setattr(auto_tickets, "LOCALDATA", tmp_path)
    monkeypatch.setattr(auto_tickets, "STATE_FILE", tmp_path / "auto_tickets_state.json")
    monkeypatch.setattr(auto_tickets, "now_local", lambda: now)
    monkeypatch.setattr(auto_tickets, "_ML_AVAILABLE", False)
    run = _write_run_inputs(tmp_path, [], now)
    offers = [
        _offer("one", "Alice Adams", "Beth Brown", now - timedelta(minutes=1), now + timedelta(hours=2)),
        _offer("two", "Clara Clark", "Diana Davies", now - timedelta(minutes=1), now + timedelta(hours=3)),
    ]
    picks = [_pick_from_offer(row, DAY) for row in offers]
    # The normal official-pick reader consumes a fresh run-scoped export.
    (tmp_path / f"picks_{DAY}.json").write_text(json.dumps(picks))
    (tmp_path / f".pick_run_status_{DAY}.json").write_text(json.dumps({"run_as_of": run, "fresh_picks": 2}))
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", run)
    monkeypatch.setattr("sys.argv", ["auto_tickets.py", "--date", DAY])
    auto_tickets.main()
    saved = json.loads((tmp_path / f"auto_tickets_{DAY}.json").read_text())
    assert saved["admission_policy_version"] == "racket-new-card-v1"
    assert len(saved["accas"]) == 1
    assert all(leg["_admission_policy_version"] == "racket-new-card-v1" for leg in saved["accas"][0]["legs"])
    assert saved["accas"][0]["paper"] is False
    evaluation = json.loads((tmp_path / f"auto_tickets_evaluation_{DAY}.json").read_text())
    assert evaluation["collection_status"] == "partial"
    assert evaluation["evaluation_status"] == "valid_card"


def test_existing_bucket_and_clean_no_bet_vs_unavailable_input(monkeypatch, tmp_path):
    now = datetime.now(auto_tickets.TZ)
    monkeypatch.setattr(auto_tickets, "LOCALDATA", tmp_path)
    monkeypatch.setattr(auto_tickets, "STATE_FILE", tmp_path / "auto_tickets_state.json")
    monkeypatch.setattr(auto_tickets, "now_local", lambda: now)
    monkeypatch.setattr(auto_tickets, "_ML_AVAILABLE", False)
    offered = _offer("one", "Alice Adams", "Beth Brown", now - timedelta(minutes=1), now + timedelta(hours=2))
    bad_bucket = _pick_from_offer(offered, DAY)
    bad_bucket["bucket"] = "SKIPPED_DEAD_EDGE"
    run = _write_run_inputs(tmp_path, [bad_bucket], now)
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", run)
    monkeypatch.setattr("sys.argv", ["auto_tickets.py", "--date", DAY])
    auto_tickets.main()
    evaluation = json.loads((tmp_path / f"auto_tickets_evaluation_{DAY}.json").read_text())
    assert evaluation["evaluation_status"] == "clean_no_bet"
    saved = json.loads((tmp_path / f"auto_tickets_{DAY}.json").read_text())
    assert saved["accas"] == []

    # Missing run-scoped picks are input failure, not a clean no-bet that can
    # create/overwrite a card.
    tmp2 = tmp_path / "unavailable"
    tmp2.mkdir()
    monkeypatch.setattr(auto_tickets, "LOCALDATA", tmp2)
    monkeypatch.setattr(auto_tickets, "STATE_FILE", tmp2 / "auto_tickets_state.json")
    (tmp2 / "auto_tickets_state.json").write_text(json.dumps({"bank": 100, "paper_bank": 100, "history": [], "open_slips": [], "events": []}))
    auto_tickets.main()
    failed = json.loads((tmp2 / f"auto_tickets_evaluation_{DAY}.json").read_text())
    assert failed["evaluation_status"] == "input_failure"
    assert not (tmp2 / f"auto_tickets_{DAY}.json").exists()


def test_protected_card_no_overwrite_on_clean_no_bet(monkeypatch, tmp_path):
    now = datetime.now(auto_tickets.TZ)
    monkeypatch.setattr(auto_tickets, "LOCALDATA", tmp_path)
    monkeypatch.setattr(auto_tickets, "STATE_FILE", tmp_path / "auto_tickets_state.json")
    monkeypatch.setattr(auto_tickets, "now_local", lambda: now)
    monkeypatch.setattr(auto_tickets, "_ML_AVAILABLE", False)
    run = _write_run_inputs(tmp_path, [], now, existing={"date": DAY, "frozen": False,
        "accas": [{"legs": [{"selected_player": "Protected"}], "stake_pct": 1.0}]})
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", run)
    monkeypatch.setattr("sys.argv", ["auto_tickets.py", "--date", DAY])
    card = (tmp_path / f"auto_tickets_{DAY}.json").read_bytes()
    auto_tickets.main()
    assert (tmp_path / f"auto_tickets_{DAY}.json").read_bytes() == card
