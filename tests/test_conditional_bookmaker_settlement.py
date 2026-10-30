"""Test-only synthetic rows for conditional bookmaker settlement semantics.

These cases exercise the generic policy consumer, not live provider contracts.
Official terms, jurisdiction and quote binding remain mandatory in production.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from racketfactory.execution import (  # noqa: E402
    settlement_evidence_problem,
    settlement_rules_problem,
)
from racketfactory.settlement import (  # noqa: E402
    completed_sets_from_row,
    row_finality,
    settle_selection,
    termination_kind,
)


SET_THRESHOLD_RULES_TEST_ONLY = {
    "retirement": {
        "action": "settle_winner_if_completed_sets",
        "minimum_completed_sets": 1,
        "otherwise": "void",
    },
    "disqualification": {
        "action": "settle_winner_if_completed_sets",
        "minimum_completed_sets": 1,
        "otherwise": "void",
    },
    "walkover": {"action": "void"},
    "cancelled": {"action": "void"},
}

SIMPLE_TERMINATION_RULES_TEST_ONLY = {
    "retirement": {"action": "void"},
    "disqualification": {"action": "settle_winner"},
    "walkover": {"action": "void"},
    "cancelled": {"action": "void"},
}


def result(score, *, status="", winner="A Player"):
    return {
        "player_a": "A Player",
        "player_b": "B Player",
        "winner": winner,
        "score": score,
        "_result_status": status,
        "match_date": "2026-10-30",
        "source": "test-only synthetic result",
    }


def grade(row, selected="A Player", rules=SET_THRESHOLD_RULES_TEST_ONLY):
    return settle_selection(
        "A Player vs B Player", selected, [row], "2026-10-30",
        settlement_rules=rules,
    )


def test_completed_set_count_ignores_partial_final_set_and_status_suffix():
    assert completed_sets_from_row(result("6:1 3:0 RET.")) == 1
    assert completed_sets_from_row(result("6:1 6:4 2:1 RET.")) == 2
    # Test-only transcription of the official historical page's visible score
    # format; this is not a retained raw provider response or contract.
    assert completed_sets_from_row(result("1:0 RET. (6:1, 3:0)")) == 1
    assert completed_sets_from_row(result("0:0 RET.")) == 0
    assert completed_sets_from_row(result("not a score RET.")) is None


def test_retirement_conditional_rule_requires_completed_set():
    at_least_one = grade(result("6:1 3:0 RET.", winner="B Player"), selected="B Player")
    no_sets = grade(result("0:0 RET.", winner="B Player"), selected="B Player")
    unknown_sets = grade(result("RET.", winner="B Player"), selected="B Player")

    assert at_least_one.outcome == "WON"
    assert at_least_one.basis["completed_sets"] == 1
    assert no_sets.outcome == "VOID"
    assert unknown_sets.outcome == "PENDING"


def test_bet365_style_retirement_and_disqualification_are_distinct():
    retirement = grade(
        result("6:1 3:0 RET.", winner="B Player"),
        selected="B Player", rules=SIMPLE_TERMINATION_RULES_TEST_ONLY,
    )
    disqualification = grade(
        result("3:0 DISQ.", status="disqualified", winner="B Player"),
        selected="B Player", rules=SIMPLE_TERMINATION_RULES_TEST_ONLY,
    )

    assert termination_kind(result("6:1 3:0 RET.")) == "retirement"
    assert termination_kind(result("3:0 DISQ.", status="disqualified")) == "disqualification"
    assert retirement.outcome == "VOID"
    assert disqualification.outcome == "WON"


def test_disqualification_threshold_finality_is_terms_scoped():
    row = result("0:0 DISQ.", status="disqualified", winner="B Player")
    assert not row_finality(row).final  # legacy result policy is unchanged
    assert row_finality(row, settlement_rules=SET_THRESHOLD_RULES_TEST_ONLY).final
    assert grade(row, selected="B Player").outcome == "VOID"


def test_defaulted_forfeit_and_ambiguous_termination_do_not_inherit_retirement_rule():
    defaulted = grade(result("6:1 DEF.", status="defaulted", winner="B Player"))
    forfeited = grade(result("6:1 forfeited", status="forfeit", winner="B Player"))
    contradictory = grade(result("6:1 RET. DISQ.", status="disqualified", winner="B Player"))

    assert defaulted.outcome == "PENDING"
    assert "defaulted" in defaulted.reason
    assert forfeited.outcome == "PENDING"
    assert "forfeit" in forfeited.reason
    assert contradictory.outcome == "CONFLICT"


def test_legacy_retirement_semantics_are_unchanged_without_terms():
    row = result("6:1 3:0 RET.", winner="B Player")
    legacy = settle_selection("A Player vs B Player", "B Player", [row], "2026-10-30")
    assert legacy.outcome == "WON"


def test_rule_schema_is_structural_and_does_not_authenticate_synthetic_evidence():
    # Synthetic policy data is marked test_only and is used only to exercise
    # this schema helper. It is not a provider/bookmaker contract.
    assert settlement_rules_problem(SET_THRESHOLD_RULES_TEST_ONLY) == ""
    assert settlement_rules_problem(SIMPLE_TERMINATION_RULES_TEST_ONLY) == ""
    timeout_rules = {key: dict(value) for key, value in SET_THRESHOLD_RULES_TEST_ONLY.items()}
    timeout_rules["cancelled"] = {
        "action": "void_after_scheduled_hours", "hours": 12, "otherwise": "pending",
    }
    assert settlement_rules_problem(timeout_rules) == ""
    assert settlement_rules_problem({
        "retirement": "advancer",
        "disqualification": "advancer",
        "walkover": "void",
        "cancelled": "void",
    })


def test_ticket_grader_uses_only_validated_stored_terms(monkeypatch):
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import auto_tickets_grade as grader

    rules = {key: dict(value) for key, value in SET_THRESHOLD_RULES_TEST_ONLY.items()}
    quote = {
        "bookmaker": "Test book (synthetic)",
        "bookmaker_id": "test-only-id",
        "settlement_rules": rules,
        "settlement_evidence": {
            "test_only": True,
            "bookmaker": "Test book (synthetic)",
            "bookmaker_id": "test-only-id",
            "jurisdiction": "test-only",
            "document_url": "https://example.invalid/test-only",
            "document_sha256": "0" * 64,
            "rules": rules,
        },
    }
    assert settlement_evidence_problem(quote) == "synthetic settlement evidence is not provider proof"
    # The test bypasses only the external-document validator so a clearly
    # test-only quote can exercise the grader's terms-routing branch.
    monkeypatch.setattr(
        grader, "settlement_evidence_problem",
        lambda candidate: "" if candidate["settlement_evidence"].get("test_only") else "rejected",
    )

    def leg():
        return {
            "match": "A Player vs B Player",
            "selected_player": "B Player",
            "date": "2026-10-30",
            "selected_quote": quote,
        }

    frame = pd.DataFrame([{
        "player_a": "A Player", "player_b": "B Player", "winner": "B Player",
        "score": "6:1 3:0 RET.", "_result_status": "retired",
        "match_date": "2026-10-30", "source": "synthetic test row",
    }])
    priced = leg()
    assert grader.settle_leg(priced, frame, target_date="2026-10-30") == "WON"
    terms = priced["_settle_basis"]["settlement_terms"]
    assert terms["bookmaker_id"] == "test-only-id"
    assert terms["rule_for"] == "retirement"

    unplayed = pd.DataFrame([{
        "player_a": "A Player", "player_b": "B Player", "winner": "B Player",
        "score": "0:0 RET.", "_result_status": "retired",
        "match_date": "2026-10-30", "source": "synthetic test row",
    }])
    refunded = leg()
    assert grader.settle_leg(refunded, unplayed, target_date="2026-10-30") == "VOID"

    # A historical quote carrying only the old unbound rules field is not
    # reinterpreted under the new terms contract.
    legacy = {
        "match": "A Player vs B Player",
        "selected_player": "B Player",
        "date": "2026-10-30",
        "selected_quote": {"settlement_rules": {
            "retirement": "advancer", "walkover": "void", "cancelled": "void",
        }},
    }
    assert grader.settle_leg(legacy, frame, target_date="2026-10-30") == "WON"


def test_cancelled_fixture_respects_documented_no_start_timeout():
    rules = {key: dict(value) for key, value in SET_THRESHOLD_RULES_TEST_ONLY.items()}
    rules["cancelled"] = {
        "action": "void_after_scheduled_hours", "hours": 12, "otherwise": "pending",
    }
    row = result("cancelled", status="cancelled", winner="")
    before_deadline = settle_selection(
        "A Player vs B Player", "A Player", [row], "2026-10-30",
        settlement_rules=rules,
        settlement_context={
            "commence_time": "2026-10-30T10:00:00+00:00",
            "graded_at": "2026-10-30T21:59:59+00:00",
        },
    )
    after_deadline = settle_selection(
        "A Player vs B Player", "A Player", [row], "2026-10-30",
        settlement_rules=rules,
        settlement_context={
            "commence_time": "2026-10-30T10:00:00+00:00",
            "graded_at": "2026-10-30T22:00:00+00:00",
        },
    )
    missing_start = settle_selection(
        "A Player vs B Player", "A Player", [row], "2026-10-30",
        settlement_rules=rules,
        settlement_context={"graded_at": "2026-10-30T22:00:00+00:00"},
    )

    assert before_deadline.outcome == "PENDING"
    assert after_deadline.outcome == "VOID"
    assert missing_start.outcome == "PENDING"


def test_walkover_remains_void_and_never_uses_set_threshold():
    row = result("W.O.", winner="B Player")
    assert termination_kind(row) == "walkover"
    assert grade(row, selected="B Player").outcome == "VOID"
