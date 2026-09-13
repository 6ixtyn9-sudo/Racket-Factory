"""Tests for the BetExplorer x OddsPortal cross-check merger."""

import json

from racketfactory.odds_compare import (
    AGREE_TOLERANCE,
    STATUS_ENV,
    _write_status,
    fetch_comparison_rows,
    merge_comparison_rows,
)


def _row(home, away, h, a, source="BetExplorer"):
    return {
        "match_date": "2026-09-13",
        "player_home": home,
        "player_away": away,
        "odds_home": h,
        "odds_away": a,
        "source": source,
    }


def test_agree_uses_betexplorer_pair_and_flags():
    be = [_row("Steur J. L. S.", "Torner Sensano N.", 1.20, 4.25)]
    op = [_row("Steur J. L. S.", "Torner Sensano N.", 1.22, 4.20, "OddsPortal")]
    merged = merge_comparison_rows(be, op)
    assert len(merged) == 1
    row = merged[0]
    assert row["odds_home"] == 1.20  # consensus leg, not the optimistic leg
    assert row["odds_away"] == 4.25
    assert row["source"] == "BetExplorer"
    assert row["odds_cross_checked"] == "agree"
    assert row["odds_alt_source"] == "OddsPortal"
    assert row["odds_alt_home"] == 1.22


def test_disagree_still_prices_conservative_with_flag():
    be = [_row("Rouvroy M.", "Johnson S.", 3.61, 1.26)]
    op = [_row("Rouvroy M.", "Johnson S.", 4.20, 1.10, "OddsPortal")]
    merged = merge_comparison_rows(be, op)
    assert len(merged) == 1
    row = merged[0]
    assert (row["odds_home"], row["odds_away"]) == (3.61, 1.26)
    assert row["odds_cross_checked"] == "disagree"


def test_tolerance_boundary():
    be = [_row("Alpha B.", "Beta C.", 2.00, 2.00)]
    edge = 2.00 * (1 + AGREE_TOLERANCE)
    assert merge_comparison_rows(be, [_row("Alpha B.", "Beta C.", edge, 2.00, "OddsPortal")])[0][
        "odds_cross_checked"] == "agree"
    assert merge_comparison_rows(be, [_row("Alpha B.", "Beta C.", 2.30, 2.00, "OddsPortal")])[0][
        "odds_cross_checked"] == "disagree"


def test_reversed_order_matches_with_sides_swapped():
    be = [_row("Steur J. L. S.", "Torner Sensano N.", 1.20, 4.25)]
    op = [_row("Torner Sensano N.", "Steur J. L. S.", 4.20, 1.22, "OddsPortal")]
    merged = merge_comparison_rows(be, op)
    assert len(merged) == 1
    assert merged[0]["odds_cross_checked"] == "agree"
    assert merged[0]["odds_alt_home"] == 1.22  # swapped back to BE orientation
    assert merged[0]["odds_alt_away"] == 4.20


def test_single_leg_rows_pass_through_flagged():
    be = [_row("Only B.", "Here C.", 1.50, 2.50)]
    op = [_row("Solo D.", "Alone E.", 1.80, 2.00, "OddsPortal")]
    merged = merge_comparison_rows(be, op)
    assert len(merged) == 2
    by_home = {r["player_home"]: r for r in merged}
    assert by_home["Only B."]["odds_cross_checked"] == "single"
    assert by_home["Only B."]["source"] == "BetExplorer"
    assert by_home["Solo D."]["odds_cross_checked"] == "single"
    assert by_home["Solo D."]["source"] == "OddsPortal"


def test_junk_rows_dropped():
    be = [_row("", "Nope N.", 1.50, 2.50), _row("Bad B.", "Odds O.", None, 2.50)]
    op = [_row("Ghost G.", "Missing M.", 0.99, 2.00, "OddsPortal")]
    assert merge_comparison_rows(be, op) == []


def test_status_snapshot_written_per_date(tmp_path, monkeypatch):
    status = tmp_path / "odds_compare_status.json"
    monkeypatch.setenv(STATUS_ENV, str(status))
    # Out-of-range date: both legs short-circuit (no network), zeros recorded.
    assert fetch_comparison_rows("2000-01-01") == []
    payload = json.loads(status.read_text())
    assert payload["2000-01-01"]["merged_rows"] == 0
    assert payload["2000-01-01"]["errors"] == {}
    # A second date merges instead of overwriting.
    _write_status({"2000-01-02": {"merged_rows": 5}})
    payload = json.loads(status.read_text())
    assert set(payload) == {"2000-01-01", "2000-01-02"}


def test_status_keeps_freshest_7_dates(tmp_path, monkeypatch):
    status = tmp_path / "odds_compare_status.json"
    monkeypatch.setenv(STATUS_ENV, str(status))
    _write_status({f"2000-01-{d:02d}": {"merged_rows": d} for d in range(1, 10)})
    payload = json.loads(status.read_text())
    assert len(payload) == 7
    assert "2000-01-01" not in payload
    assert "2000-01-09" in payload
