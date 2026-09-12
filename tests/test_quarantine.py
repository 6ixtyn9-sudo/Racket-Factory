"""Quarantine classification anchored in observed production garbage."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from quarantine_results import classify_row


def _row(**kw):
    base = {"match_date": "2026-09-11", "tour": "UNKNOWN", "tournament": "",
            "player_a": "A", "player_b": "B", "winner": "A",
            "score": "6-4 6-4", "source": "Challenger_results"}
    base.update(kw)
    return base


def test_tournament_as_player_quarantined():
    v, r = classify_row(_row(player_a="Cascino E / Feng S.",
                             player_b="Montreux WTA",
                             winner="Cascino E / Feng S.",
                             score="6-2 6-3"))
    assert v == "quarantine" and "tournament-like" in r


def test_tournament_as_winner_quarantined():
    v, _ = classify_row(_row(player_a="Gauff C.", player_b="US Open",
                             winner="US Open", score="6-4 6-4"))
    assert v == "quarantine"


def test_frankenstein_score_quarantined():
    v, r = classify_row(_row(player_a="Munar J.", player_b="Olivieri G.",
                             winner="Munar J.", score="2-1 2-1 6-7 6-3"))
    assert v == "quarantine"


def test_incomplete_final_set_quarantined():
    v, _ = classify_row(_row(winner="Alcala Gurri M.", score="6-4 5-4"))
    assert v == "quarantine"


def test_coherent_weak_row_is_legacy():
    v, _ = classify_row(_row(player_a="Alcala Gurri M.", player_b="Olivieri G.",
                             winner="Alcala Gurri M.", score="2-1 4-6 6-3 6-0"))
    assert v == "legacy"


def test_strong_row_kept():
    v, _ = classify_row(_row(tournament="Sevilla", _te_id="99",
                             player_a="Alcala Gurri M.", player_b="Olivieri G.",
                             winner="Alcala Gurri M.", score="2-1 4-6 6-3 6-0"))
    assert v == "keep"


def test_foretennis_concat_sets_migrated():
    row = _row(source="ForeTennis_results", tournament="Wimbledon",
               player_a="Hurkacz H.", player_b="Ofner S.",
               winner="Hurkacz H.", score="30",
               _score_perspective="player_a_sets-player_b_sets")
    v, _ = classify_row(row)
    assert v == "keep"
    assert (row["_sets_a"], row["_sets_b"]) == (3, 0)
    assert row["score"] == ""


def test_foretennis_single_digit_uses_winner_side():
    row = _row(source="ForeTennis_results", tournament="Wimbledon",
               player_a="Valentin Royer", player_b="Alexander Zverev",
               winner="Alexander Zverev", score="3",
               _score_perspective="player_a_sets-player_b_sets")
    v, _ = classify_row(row)
    assert v == "keep"
    assert (row["_sets_a"], row["_sets_b"]) == (0, 3)


def test_api_scores_dash_sets_migrated():
    row = _row(source="TheOddsAPI_scores", tournament="US Open",
               player_a="Karen Khachanov", player_b="Alexander Blockx",
               winner="Karen Khachanov", score="2-0")
    v, _ = classify_row(row)
    assert v == "keep"
    assert (row["_sets_a"], row["_sets_b"]) == (2, 0)
