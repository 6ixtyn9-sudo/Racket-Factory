"""Synthetic-only provenance records for plumbing tests; never provider proof."""
from __future__ import annotations

import hashlib


SYNTHETIC_RULES = {"retirement": "advancer", "walkover": "void", "cancelled": "void"}


def attach_synthetic_settlement_evidence(quote: dict, *, jurisdiction: str = "TEST") -> dict:
    """Attach internally fabricated, clearly test-only bookmaker terms evidence."""
    bookmaker = str(quote.get("bookmaker") or "Synthetic Book")
    bookmaker_id = str(quote.get("bookmaker_id") or "synthetic-book-id")
    observed = str(quote.get("observed_at") or "2035-01-01T12:00:00+00:00")
    quote["bookmaker"] = bookmaker
    quote["bookmaker_id"] = bookmaker_id
    quote["operator_jurisdiction"] = jurisdiction
    quote["settlement_rules"] = dict(quote.get("settlement_rules") or SYNTHETIC_RULES)
    quote["settlement_evidence"] = {
        "document_kind": "official_bookmaker_terms",
        "document_url": "https://synthetic.invalid/terms/tennis",
        "document_sha256": hashlib.sha256(b"synthetic terms; not actual bookmaker policy").hexdigest(),
        "bookmaker": bookmaker,
        "bookmaker_id": bookmaker_id,
        "jurisdiction": jurisdiction,
        "market_scope": "tennis_match_winner",
        "effective_from": "2000-01-01",
        "effective_until": "2099-12-31",
        "retrieved_at": observed,
        "rules": dict(quote["settlement_rules"]),
        "test_only": True,
    }
    return quote


def attach_synthetic_schedule_evidence(quote: dict, *, observed_at: str | None = None) -> dict:
    """Attach synthetic schedule plumbing only; never a provider attestation."""
    from datetime import datetime, timezone

    observed = observed_at or str(quote.get("observed_at") or datetime.now(timezone.utc).isoformat())
    quote["observed_at"] = observed
    quote["schedule_evidence"] = {
        "status": "provider_verified",
        "provider": "BetExplorer",
        "sport": "Tennis",
        "event_status": "https://schema.org/EventScheduled",
        "event_id": quote.get("event_id"),
        "match_url": quote.get("match_url"),
        "player_home": quote.get("player_home"),
        "player_away": quote.get("player_away"),
        "commence_time": quote.get("commence_time"),
        "observed_at": observed,
        "response_sha256": hashlib.sha256(b"synthetic BetExplorer schedule; not provider evidence").hexdigest(),
        "contradictory_signals": [],
        "test_only": True,
    }
    return quote


def attach_synthetic_oddsportal_schedule_evidence(quote: dict) -> dict:
    """Attach test-only OddsPortal match-page schedule plumbing, not provider proof."""
    observed = str(quote.get("observed_at") or "2035-01-01T12:00:00+00:00")
    quote["schedule_evidence"] = {
        "status": "candidate_unverified_contract", "provider": "OddsPortal", "sport": "Tennis",
        "event_status": "EventScheduled", "event_id": quote.get("event_id"),
        "match_url": quote.get("match_url"), "player_home": quote.get("player_home"),
        "player_away": quote.get("player_away"), "commence_time": quote.get("commence_time"),
        "observed_at": observed,
        "response_sha256": hashlib.sha256(b"synthetic OddsPortal schedule; not provider evidence").hexdigest(),
        "contradictory_signals": [], "test_only": True,
    }
    return quote


def allow_synthetic_evidence_in_pipeline(monkeypatch) -> None:
    """Open only the evidence-authentication boundary in an offline plumbing test.

    Structural validation, coverage, ticket and publication code remain real.
    The override is test-local and accepts records only when explicitly marked
    test_only; it is never imported by production modules.
    """
    from racketfactory import execution

    settlement = execution.settlement_evidence_problem
    schedule = execution.betexplorer_schedule_evidence_problem
    oddsportal_schedule = execution.oddsportal_schedule_evidence_problem

    def synthetic_settlement(quote, *args, **kwargs):
        evidence = quote.get("settlement_evidence")
        if isinstance(evidence, dict) and evidence.get("test_only") is True:
            return ""
        return settlement(quote, *args, **kwargs)

    def synthetic_schedule(quote, *args, **kwargs):
        evidence = quote.get("schedule_evidence")
        if isinstance(evidence, dict) and evidence.get("test_only") is True:
            return ""
        return schedule(quote, *args, **kwargs)

    def synthetic_oddsportal_schedule(quote, *args, **kwargs):
        evidence = quote.get("schedule_evidence")
        if isinstance(evidence, dict) and evidence.get("test_only") is True:
            return ""
        return oddsportal_schedule(quote, *args, **kwargs)

    monkeypatch.setattr(execution, "settlement_evidence_problem", synthetic_settlement)
    monkeypatch.setattr(execution, "betexplorer_schedule_evidence_problem", synthetic_schedule)
    monkeypatch.setattr(execution, "oddsportal_schedule_evidence_problem", synthetic_oddsportal_schedule)
