"""ForeTennis actual_result orientation: digits flip with warehouse order.

Root cause of quarantined match 1324: matched rows took warehouse
(player_a, player_b) order but feed-order actual_result digits, naming the
wrong winner whenever the orders disagreed.
"""
from racketfactory.sources.foretennis import (
    ForeTennisPredictor,
    flip_actual_result,
)


def test_flip_swaps_two_digits():
    assert flip_actual_result("12") == "21"
    assert flip_actual_result("30") == "03"
    assert flip_actual_result("02") == "20"


def test_flip_passes_through_unknown_formats():
    assert flip_actual_result("") == ""
    assert flip_actual_result(None) == ""
    assert flip_actual_result("2-1 6-1 5-7") == "2-1 6-1 5-7"
    assert flip_actual_result("1") == "1"


def _pred(home, away):
    return {"player_home": home, "player_away": away,
            "predicted_winner": "1", "prob_home": 51.0, "prob_away": 49.0}


def test_mapper_reports_orientation():
    ft = ForeTennisPredictor()
    straight = ft.map_prediction_to_player(_pred("Coco Gauff", "Elena Rybakina"),
                                           "Coco Gauff", "Elena Rybakina")
    assert straight is not None and straight["home_is_a"] is True
    swapped = ft.map_prediction_to_player(_pred("Coco Gauff", "Elena Rybakina"),
                                          "Elena Rybakina", "Coco Gauff")
    assert swapped is not None and swapped["home_is_a"] is False
    # predicted side follows the orientation too
    assert straight["predicted_winner"] == "player_a"
    assert swapped["predicted_winner"] == "player_b"


def test_1324_replay_names_rybakina():
    """Feed order (Gauff, Rybakina) + actual '12' against warehouse order
    (Rybakina, Gauff) must flip to '21' so player_a (Rybakina) wins."""
    import sys
    sys.path.insert(0, "scripts")
    from backfill_foretennis import _winner_from_actual_result
    ft = ForeTennisPredictor()
    mapped = ft.map_prediction_to_player(
        _pred("Gauff C.", "Rybakina E."), "Rybakina E.", "Gauff C.")
    assert mapped is not None and mapped["home_is_a"] is False
    actual = "12"  # feed order: Gauff 1, Rybakina 2
    if not mapped.get("home_is_a", True):
        actual = flip_actual_result(actual)
    assert actual == "21"
    # warehouse order: player_a Rybakina wins 2-1
    assert _winner_from_actual_result(actual) == "player_a"
