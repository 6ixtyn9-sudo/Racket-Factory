"""Offline production-function regressions using a reduced listing fixture.

This is synthetic/reduced evidence, not an authentic BetExplorer capture and
not evidence that a live provider route is available.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pandas as pd

from racketfactory import quote_enrichment, warehouse
from racketfactory.kickoff import SAST
from racketfactory.sources import betexplorer

from tests.test_betexplorer_structured_listing import listing


def test_parser_produced_undated_listing_reaches_guarded_detail_and_returns_bound_quote(monkeypatch):
    target = (datetime.now(SAST) + timedelta(hours=2)).date().isoformat()
    start = datetime.now(SAST) + timedelta(hours=3)
    html = listing(
        '<span>van Wyk K.</span><span>Marmousez L.</span>',
        dt=f"{target[8:10]},{target[5:7]},{target[:4]},11,0",
    )
    monkeypatch.setattr(betexplorer, "fetch_page_html", lambda *args, **kwargs: html)
    rows, listing_status = betexplorer._fetch_live(target, with_status=True)
    assert listing_status == "partial"
    assert len(rows) == 1
    discovery = rows[0]
    assert discovery["match_date"] == discovery["match_time"] == ""
    assert discovery["listing_date"] == target
    prediction = {
        "match_date": target, "match_time": start.strftime("%H:%M"),
        "player_home": "van Wyk K.", "player_away": "Marmousez L.",
        "tournament": "M15 Monastir 37", "tour": "ITF-M",
    }
    # The unchanged strict quote matcher still rejects the undated listing.
    assert warehouse._match_api_odds_row(pd.Series(prediction), rows) == (None, False)

    calls = []
    observed = datetime.now(timezone.utc).isoformat()
    commence = start.astimezone(timezone.utc).isoformat()

    def detail(fixture, **kwargs):
        calls.append(dict(fixture))
        assert fixture["match_date"] == fixture["match_time"] == ""
        evidence = {
            "status": "candidate_unverified_contract", "provider": "BetExplorer", "sport": "Tennis",
            "event_status": "EventScheduled", "event_id": fixture["event_id"],
            "match_url": fixture["match_url"], "player_home": fixture["player_home"],
            "player_away": fixture["player_away"], "commence_time": commence,
            "observed_at": observed, "response_sha256": hashlib.sha256(b"synthetic reduced detail").hexdigest(),
            "contradictory_signals": [],
        }
        return [{
            "event_id": fixture["event_id"], "match_url": fixture["match_url"],
            "player_home": fixture["player_home"], "player_away": fixture["player_away"],
            "match_date": target, "match_time": start.strftime("%H:%M"), "commence_time": commence,
            "tournament": fixture["tournament"], "tour_hint": fixture["tour_hint"],
            "schedule_evidence": evidence, "observed_at": observed, "retrieved_at": observed,
            "source": "BetExplorer", "bookmaker": "Synthetic Book", "price_type": "bookmaker",
            "market": "match_winner", "period": "match", "odds_home": 1.55, "odds_away": 2.60,
        }]

    monkeypatch.setattr(betexplorer, "fetch_fixture_comparison", detail)
    offers, counts, partial = quote_enrichment.collect_prediction_comparisons(
        target, [prediction], rows, listing_status,
    )
    assert len(calls) == 1
    assert len(offers) == 1
    assert offers[0]["event_id"] == discovery["event_id"]
    assert offers[0]["match_date"] == target
    assert counts["matched_fixtures"] == counts["attempted"] == 1
    assert counts["stage_statuses"]["identity_matching"] == "complete"
    assert counts["stage_statuses"]["schedule_verification"] == "candidate_verified_uncontracted"
    assert counts["stage_statuses"]["detail_discovery"] == "complete"
    assert counts["stage_statuses"]["quote_resolution"] == "complete"
    # The incomplete listing remains diagnostically partial; discovery does
    # not upgrade whole-board completeness.
    assert partial is True


def test_undated_or_unidentified_discovery_does_not_claim_later_stages_complete():
    prediction = {"match_date": "2026-10-31", "player_home": "Alice Adams", "player_away": "Beth Brown"}
    offers, counts, partial = quote_enrichment.collect_prediction_comparisons(
        "2026-10-31", [prediction], [], "complete",
    )
    assert offers == [] and partial is True
    assert counts["stage_statuses"]["identity_matching"] == "failed"
    assert counts["stage_statuses"]["schedule_verification"] == "not_attempted"
    assert counts["stage_statuses"]["detail_discovery"] == "not_attempted"
    assert counts["stage_statuses"]["quote_resolution"] == "not_attempted"


def test_conflicting_tournament_context_blocks_same_participant_fixture(monkeypatch):
    target = "2026-10-31"
    row = {
        "listing_date": target, "listing_schedule_status": "unverified",
        "player_home": "van Wyk K.", "player_away": "Marmousez L.",
        "event_id": "E7omKkv8",
        "match_url": "/tennis/itf-men-singles/m15-monastir-37/van-wyk-marmousez/E7omKkv8/",
        "tour_hint": "itf men singles", "tournament": "m15 monastir 37",
        "match_date": "", "match_time": "",
    }
    valid_context = {
        "match_date": target, "player_home": "Kris van Wyk", "player_away": "Lilian Marmousez",
        "tour": "ITF-M", "tournament": "M15 Monastir 37",
    }
    conflicting_context = dict(valid_context, tour="WTA", tournament="WTA Beijing")
    monkeypatch.setattr(betexplorer, "fetch_fixture_comparison",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("conflicting identity must not fetch")))
    offers, counts, partial = quote_enrichment.collect_prediction_comparisons(
        target, [valid_context, conflicting_context], [row], "complete",
    )
    assert offers == [] and partial is True
    assert counts["ambiguous_identity_fixtures"] == 1
    assert counts["identity_rejections"]["participant_context_conflict"] == 1
    assert counts["stage_statuses"]["identity_matching"] == "partial"
    assert counts["stage_statuses"]["detail_discovery"] == "not_attempted"


def test_provisional_identity_requires_unique_full_participant_context_and_status():
    target = "2026-10-31"
    path = "/tennis/itf-men-singles/m15-monastir-37/van-wyk-marmousez/E7omKkv8/"
    row = {
        "listing_date": target, "match_date": "", "match_time": "",
        "listing_schedule_status": "unverified", "player_home": "van Wyk K.",
        "player_away": "Marmousez L.", "event_id": "E7omKkv8", "match_url": path,
        "tour_hint": "itf men singles", "tournament": "m15 monastir 37",
    }
    prediction = {"match_date": target, "player_home": "Kris van Wyk", "player_away": "Lilian Marmousez",
                  "tour": "ITF-M", "tournament": "M15 Monastir 37"}
    matched, reversed_order, reason = quote_enrichment._provisional_discovery_match(prediction, [row], target)
    assert matched is row and reversed_order is False and reason == ""

    reversed_row = dict(row, player_home="Marmousez L.", player_away="van Wyk K.")
    matched, reversed_order, reason = quote_enrichment._provisional_discovery_match(
        prediction, [reversed_row], target,
    )
    assert matched is reversed_row and reversed_order is True and reason == ""

    other_event = dict(row, event_id="Other123",
                       match_url="/tennis/itf-men-singles/m15-monastir-37/other-match/Other123/")
    assert quote_enrichment._provisional_discovery_match(prediction, [row, other_event], target)[0] is None
    assert quote_enrichment._provisional_discovery_match(prediction, [row, dict(row)], target)[2] == "duplicate_event_identity"
    assert quote_enrichment._provisional_discovery_match(prediction, [dict(row, status="Live")], target)[0] is None
    assert quote_enrichment._provisional_discovery_match(prediction, [dict(row, **{"data-live": "true"})], target)[0] is None
    assert quote_enrichment._provisional_discovery_match(
        dict(prediction, tournament="WTA Beijing"), [row], target,
    )[0] is None
    assert quote_enrichment._provisional_discovery_match(
        dict(prediction, player_home="van Wyk / Marmousez", player_away="Lilian Marmousez"), [row], target,
    )[0] is None
