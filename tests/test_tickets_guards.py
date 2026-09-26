"""No-clobber, kickoff fail-closed, staking and ledger-replay guards.

Each test below names the production failure it pins down. Several of these
would have failed against the code as it shipped on 2026-09-26.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import auto_tickets as at  # noqa: E402

SAST = ZoneInfo("Africa/Johannesburg")


# =========================================================== no-clobber ====

BOOKED = {"frozen": False, "date": "2026-09-26", "accas": [{"legs": []}]}
FROZEN_BOOKED = {"frozen": True, "date": "2026-09-26", "accas": [{"legs": []}]}
EMPTY = {"frozen": False, "date": "2026-09-26", "accas": []}


def test_generation_window_may_not_blank_a_booked_ledger():
    """THE 2026-09-12 BUG, and how it was silently reintroduced.

    main() used to set effective_force=True for every 06:00-09:00 run, and
    force short-circuits every other rule — so a starved pick feed producing
    0 accas wiped a ledger that already held booked bets. The window
    exemption is now its own argument and cannot reach past the empty check.
    """
    assert at.should_write_ticket_files(
        BOOKED, 0, in_generation_window=True, target_date="2026-09-26") is False
    assert at.should_write_ticket_files(
        FROZEN_BOOKED, 0, in_generation_window=True, target_date="2026-09-26") is False
    # ...and the old blanket force would have said yes to both:
    assert at.should_write_ticket_files(BOOKED, 0, force=True) is True


def test_generation_window_still_refreshes_a_frozen_ledger():
    """The behaviour the blanket force was introduced for must survive."""
    assert at.should_write_ticket_files(
        FROZEN_BOOKED, 3, in_generation_window=True, target_date="2026-09-26") is True
    # Only for TODAY's ledger, and only inside the window.
    assert at.should_write_ticket_files(
        FROZEN_BOOKED, 3, in_generation_window=True, target_date="2026-09-27") is False
    assert at.should_write_ticket_files(
        FROZEN_BOOKED, 3, in_generation_window=False, target_date="2026-09-26") is False


def test_no_clobber_truth_table_unchanged_for_legacy_callers():
    assert at.should_write_ticket_files(None, 0) is True
    assert at.should_write_ticket_files(EMPTY, 0) is True
    assert at.should_write_ticket_files(BOOKED, 2) is True
    assert at.should_write_ticket_files(BOOKED, 0) is False
    assert at.should_write_ticket_files(FROZEN_BOOKED, 2) is False
    assert at.should_write_ticket_files(FROZEN_BOOKED, 0) is False
    assert at.should_write_ticket_files(FROZEN_BOOKED, 0, force=True) is True
    assert at.should_write_ticket_files(BOOKED, 0, force=True) is True


def test_post_window_lock_still_holds():
    assert at.should_write_ticket_files(
        BOOKED, 5, is_frozen_now=True, target_date="2026-09-26") is False


# ======================================================= kickoff guard ====

NOW = datetime(2026, 9, 26, 8, 5, tzinfo=SAST)


def _pick(name, kickoff):
    return {"match": f"{name} vs Opponent", "selected_player": name,
            "kickoff": kickoff, "odds": 1.5, "odds_source": "BetExplorer"}


def test_unreadable_kickoff_goes_to_paper_not_to_the_bank(monkeypatch):
    """FAIL CLOSED. `n/a` kickoffs were staked: 22 of them over 3 days.

    The old guard returned the pick to the staked pool whenever the kickoff
    could not be parsed, i.e. "we cannot tell if this match has started" was
    treated as "safe to bet".
    """
    monkeypatch.delenv("RACKET_FACTORY_KICKOFF_FAIL_OPEN", raising=False)
    pool = [_pick("Ana", "n/a"), _pick("Bea", "23:30"), _pick("Cyd", "")]
    kept, skipped = at.kickoff_guard(pool, "2026-09-26", NOW)
    assert len(kept) == 3, "unprovable legs are demoted, never dropped"
    assert pool[0]["_kickoff_unknown"] is True
    assert pool[2]["_kickoff_unknown"] is True
    assert "_kickoff_unknown" not in pool[1]
    assert not at.leg_execution_safe(pool[0])
    assert at.leg_execution_safe(pool[1])
    assert any("kickoff unreadable" in reason for _, reason in skipped)


def test_started_match_is_still_dropped(monkeypatch):
    monkeypatch.delenv("RACKET_FACTORY_KICKOFF_FAIL_OPEN", raising=False)
    pool = [_pick("Ana", "07:00")]
    kept, skipped = at.kickoff_guard(pool, "2026-09-26", NOW)
    assert kept == [] and skipped[0][1] == "already started"


def test_fail_open_escape_hatch_restores_old_behaviour(monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_KICKOFF_FAIL_OPEN", "1")
    pool = [_pick("Ana", "n/a")]
    kept, skipped = at.kickoff_guard(pool, "2026-09-26", NOW)
    assert kept == pool and skipped == []
    assert "_kickoff_unknown" not in pool[0]


def test_flag_is_cleared_when_a_kickoff_becomes_readable(monkeypatch):
    monkeypatch.delenv("RACKET_FACTORY_KICKOFF_FAIL_OPEN", raising=False)
    pick = _pick("Ana", "n/a")
    at.kickoff_guard([pick], "2026-09-26", NOW)
    assert pick["_kickoff_unknown"] is True
    pick["kickoff"] = "23:30"
    at.kickoff_guard([pick], "2026-09-26", NOW)
    assert "_kickoff_unknown" not in pick


def test_future_dates_are_not_second_guessed(monkeypatch):
    """Tomorrow's card cannot have started; don't demote it."""
    monkeypatch.delenv("RACKET_FACTORY_KICKOFF_FAIL_OPEN", raising=False)
    pick = _pick("Ana", "n/a")
    kept, skipped = at.kickoff_guard([pick], "2026-09-27", NOW)
    assert kept == [pick] and "_kickoff_unknown" not in pick


# ============================================================== staking ====

def test_free_bank_excludes_capital_already_at_risk():
    """Stakes were sized off total bank while slips were still open."""
    state = {"bank": 173.6, "open_slips": [
        {"accas": [{"stake_pct": 20.0, "legs": [
            {"odds": 1.5, "odds_source": "BetExplorer"}]}]},
        {"accas": [{"stake_pct": 25.0, "paper": True, "legs": [
            {"odds": 1.5, "odds_source": "BetExplorer"}]}]},
    ]}
    assert at.free_bank(state) == 153.6
    assert at.free_bank({"bank": 100.0}) == 100.0
    assert at.free_bank({}) == 100.0


def test_free_bank_never_goes_negative():
    state = {"bank": 10.0, "open_slips": [
        {"accas": [{"stake_pct": 50.0, "legs": [
            {"odds": 1.5, "odds_source": "BetExplorer"}]}]}]}
    assert at.free_bank(state) == 0.0


def test_free_bank_survives_a_null_bank():
    assert at.free_bank({"bank": None, "open_slips": []}) == 0.0


def test_stake_allocation_never_exceeds_the_day_cap():
    """The live weights are 0.283/0.283/0.283/0.15 — they do not sum to 1."""
    for n_two_leg, n_three_leg in [(1, 0), (2, 0), (3, 1), (4, 0), (0, 2), (8, 3)]:
        accas = ([{"type": "value_2leg_mutual"}] * n_two_leg
                 + [{"type": "high_strength_3leg_mutual"}] * n_three_leg)
        if not accas:
            continue
        stakes = at.allocate_stakes(20.0, accas)
        assert len(stakes) == len(accas)
        assert all(s >= 0 for s in stakes)
        assert round(sum(stakes), 4) <= 20.0 + 1e-9
        assert round(sum(stakes), 4) == pytest.approx(20.0, abs=1e-4)


def test_stake_allocation_degenerate_inputs():
    assert at.allocate_stakes(20.0, []) == []
    assert at.allocate_stakes(0.0, [{"type": "x"}]) == [0.0]
    assert at.allocate_stakes(-5.0, [{"type": "x"}]) == [0.0]


def test_rounding_crumbs_are_clawed_back_not_added():
    """4dp rounding of each share must not inflate the book."""
    accas = [{"type": "value_2leg_mutual"}] * 3
    stakes = at.allocate_stakes(10.0 / 3.0, accas)
    assert round(sum(stakes), 6) <= round(10.0 / 3.0, 4) + 1e-9


def test_stake_frac_default_is_twenty_percent(monkeypatch):
    monkeypatch.delenv("RACKET_FACTORY_STAKE_FRAC", raising=False)
    assert at.stake_frac() == 0.20


@pytest.mark.parametrize("raw,expected", [
    ("0.25", 0.25), ("1.0", 1.0), ("0", 0.20), ("-1", 0.20),
    ("2", 0.20), ("", 0.20), ("abc", 0.20),
])
def test_stake_frac_override_is_validated(monkeypatch, raw, expected):
    monkeypatch.setenv("RACKET_FACTORY_STAKE_FRAC", raw)
    assert at.stake_frac() == expected


# ======================================================= ledger replay ====

def test_reconstruction_cannot_reimport_a_paper_acca_as_real(tmp_path, monkeypatch):
    """THE 2026-09-20 REPLAY: paper acca came back holding stake_pct 25.0."""
    monkeypatch.setattr(at, "LOCALDATA", tmp_path)
    monkeypatch.setattr(at, "STATE_FILE", tmp_path / "auto_tickets_state.json")
    ledger = {
        "date": "2026-09-20", "generated_at": "2026-09-20T08:00:00",
        "accas": [
            {"paper": True, "odds": None, "stake_pct": 25.0, "type": "value_2leg_mutual_paper",
             "legs": [{"match": "A vs B", "selected_player": "A", "odds": 1.22,
                       "odds_source": "BetExplorer", "_late_start": True}]},
            {"paper": False, "odds": 2.2, "stake_pct": 10.0, "type": "value_2leg_mutual",
             "legs": [{"match": "C vs D", "selected_player": "C", "odds": 2.2,
                       "odds_source": "BetExplorer"}]},
        ],
        "staked_pct": 35.0, "stake_per_acca_pct": 17.5,
    }
    (tmp_path / "auto_tickets_2026-09-20.json").write_text(json.dumps(ledger))
    (tmp_path / "picks_2026-09-26.json").write_text("[]")
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", "2026-09-26T08:05:00+02:00")

    monkeypatch.setattr(sys, "argv", ["auto_tickets.py", "--date", "2026-09-26"])
    at.main()

    state = json.loads((tmp_path / "auto_tickets_state.json").read_text())
    replayed = [s for s in state["open_slips"] if s["date"] == "2026-09-20"]
    assert replayed, "the historical slip should still be replayed for settlement"
    accas = replayed[0]["accas"]
    paper = [a for a in accas if a.get("paper")]
    # The 25.0 on the 2026-09-20 acca is a PAPER notional (paper_bank * frac),
    # not a real stake, so it is preserved for paper-bank accounting — but it
    # is now provably unable to reach the real bank, and it is annotated so
    # nobody has to re-derive that by reading the builder.
    assert paper and paper[0]["stake_pct"] == 25.0
    assert paper[0]["_replay_block"], "a demoted acca must say why"
    assert replayed[0]["staked_pct"] == 10.0, "only the executable acca counts as exposure"
    assert at.committed_stake(state["open_slips"]) == 10.0, \
        "the paper acca must commit nothing against the free bank"


def test_reconstruction_zeroes_a_real_stake_on_a_non_executable_acca(tmp_path, monkeypatch):
    """A ledger that claims REAL on an unstakeable acca is claiming fiction."""
    monkeypatch.setattr(at, "LOCALDATA", tmp_path)
    monkeypatch.setattr(at, "STATE_FILE", tmp_path / "auto_tickets_state.json")
    (tmp_path / "auto_tickets_2026-09-21.json").write_text(json.dumps({
        "date": "2026-09-21", "generated_at": "2026-09-21T08:00:00",
        "accas": [{"paper": False, "odds": 1.44, "stake_pct": 25.0,
                   "type": "value_2leg_mutual",
                   "legs": [{"match": "A vs B", "selected_player": "A", "odds": 1.44,
                             "odds_source": "BetExplorer", "_late_start": True}]}],
        "staked_pct": 25.0, "stake_per_acca_pct": 25.0,
    }))
    (tmp_path / "picks_2026-09-26.json").write_text("[]")
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", "2026-09-26T08:05:00+02:00")
    monkeypatch.setattr(sys, "argv", ["auto_tickets.py", "--date", "2026-09-26"])
    at.main()

    state = json.loads((tmp_path / "auto_tickets_state.json").read_text())
    acca = [s for s in state["open_slips"] if s["date"] == "2026-09-21"][0]["accas"][0]
    assert acca["paper"] is True and acca["stake_pct"] == 0.0
    assert at.committed_stake(state["open_slips"]) == 0.0


# ------------------------------------------------- skip list is readable --

def test_skip_records_are_compact_not_whole_picks():
    """The fail-closed kickoff rule routes up to 15 picks/day here. Embedding
    the full dict is ~1 KB apiece of ledger nobody reads."""
    pick = {"match": "A vs B", "selected_player": "A", "kickoff": "n/a",
            "bucket": "WATCHLIST", "ml_verdict": "BOOST", "odds": 1.4,
            "junk": "x" * 5000, "more_junk": list(range(500))}
    record = at.skip_record(pick)
    assert record["match"] == "A vs B" and record["selected_player"] == "A"
    assert "junk" not in record and "more_junk" not in record
    assert len(json.dumps(record)) < 200


def test_no_bet_day_explains_itself_grouped_by_reason():
    skipped = [
        (at.skip_record({"match": "A vs B", "selected_player": "A",
                         "kickoff": "n/a"}), "kickoff unreadable (n/a) -> paper"),
        (at.skip_record({"match": "C vs D", "selected_player": "C",
                         "kickoff": "n/a"}), "kickoff unreadable (n/a) -> paper"),
        (at.skip_record({"match": "E vs F", "selected_player": "E",
                         "kickoff": "07:45"}), "already started"),
    ]
    lines = at.format_skips(skipped)
    assert lines[0] == "Skipped 3 pick(s):"
    assert "  2x kickoff unreadable (n/a) -> paper" in lines
    assert "  1x already started" in lines
    assert any("A vs B [A] ko n/a" in line for line in lines)
    # worst offender first
    assert lines.index("  2x kickoff unreadable (n/a) -> paper") < \
        lines.index("  1x already started")


def test_format_skips_survives_a_malformed_ledger_entry():
    assert at.format_skips(["just a string"])[0].startswith("Skipped 1")
    assert at.format_skips([({"match": None}, "r")])
    assert "none" in at.format_skips([])[0]


def test_skipped_appears_in_the_no_bet_ticket_text():
    state = {"bank_pct": 100.0, "paper_bank_pct": 100.0, "open_slips": [],
             "history": []}
    skipped = [(at.skip_record({"match": "A vs B", "kickoff": "n/a"}),
                "kickoff unreadable (n/a) -> paper")]
    text = at.format_tickets_txt("2026-09-26", [], state, skipped)
    assert "NO BET" in text
    assert "1x kickoff unreadable" in text
    assert "A vs B" in text
