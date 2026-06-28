from racketfactory.assay import odds_band, roi_from_bets, score_rows, assay_segment
from racketfactory.sources.forebet import name_signature
import pandas as pd


def test_roi_from_bets():
    assert roi_from_bets([2.0, 1.5], [True, False]) == 0.0


def test_odds_band():
    assert odds_band(1.19) == "1.00-1.20"
    assert odds_band(2.25) == "2.00-2.50"


def test_score_rows():
    rows = [{"decimal_odds": 2.0, "won": True}, {"decimal_odds": 1.5, "won": False}]
    s = score_rows(rows)
    assert s["n"] == 2
    assert s["wins"] == 1
    assert s["roi"] == 0.0


def test_assay_segment_default_is_favorite():
    """Backwards compatibility: assay_segment without bet_side still uses favorites."""
    df = pd.DataFrame([
        {"player_a": "A1", "player_b": "B1", "winner": "A1", "odds_a": 1.20, "odds_b": 4.50},
        {"player_a": "A2", "player_b": "B2", "winner": "A2", "odds_a": 1.30, "odds_b": 3.50},
        {"player_a": "A3", "player_b": "B3", "winner": "B3", "odds_a": 1.10, "odds_b": 6.00},
    ])
    res = assay_segment(df)
    # 2/3 favourites won (the third match has favourite=A3 at 1.10 losing).
    assert res.n == 3
    assert abs(res.win_rate - 2 / 3) < 1e-9


def test_assay_segment_prediction_mode():
    """bet_side='prediction' should follow predicted_winner columns, not odds."""
    df = pd.DataFrame([
        # Prediction picks the underdog (player_b); favourite (player_a) loses.
        {"player_a": "A1", "player_b": "B1", "winner": "A1",
         "odds_a": 1.20, "odds_b": 4.50, "predicted_winner": "player_b"},
        # Prediction picks the underdog and underdog wins.
        {"player_a": "A2", "player_b": "B2", "winner": "B2",
         "odds_a": 1.30, "odds_b": 3.50, "predicted_winner": "player_b"},
        # Prediction picks favourite and favourite wins.
        {"player_a": "A3", "player_b": "B3", "winner": "A3",
         "odds_a": 1.10, "odds_b": 6.00, "predicted_winner": "player_a"},
    ])
    res = assay_segment(df, bet_side="prediction")
    assert res.n == 3
    # Two of three picks landed (B2 winning the underdog, A3 winning as fav).
    assert abs(res.win_rate - 2 / 3) < 1e-9


def test_assay_segment_prediction_mode_drops_unannotated_rows():
    """Rows without any predicted_winner should be skipped in prediction mode."""
    df = pd.DataFrame([
        {"player_a": "A1", "player_b": "B1", "winner": "A1",
         "odds_a": 1.20, "odds_b": 4.50, "predicted_winner": "player_b"},
        {"player_a": "A2", "player_b": "B2", "winner": "B2",
         "odds_a": 1.30, "odds_b": 3.50, "predicted_winner": None},
    ])
    res = assay_segment(df, bet_side="prediction")
    assert res.n == 1
    assert abs(res.win_rate - 0.0) < 1e-9  # the only annotated row lost


def test_name_signature_cross_format():
    """name_signature must collapse the two common player-name layouts
    ('Firstname Lastname' vs 'Lastname F.') so that BetClan and Forebet rows
    for the same player merge in the warehouse build.

    Regression test for the 2026-06-28 red-team finding where 72/78 BetClan
    archive rows had no odds because the merge key used an anagram-of-all-
    letters signature that did not match across formats.
    """
    cross_pairs = [
        ("Zizou Bergs", "Bergs Z."),
        ("Ugo Humbert", "Humbert U."),
        ("Karolina Muchova", "Muchova K."),
        ("Naomi Osaka", "Osaka N."),
        ("Tatjana Maria", "Maria T."),
        ("Madison Keys", "Keys M."),
        ("Ethan Quinn", "Quinn E."),
        ("Katerina Siniakova", "Siniakova K."),
        ("Alejandro Davidovich Fokina", "Davidovich Fokina A."),
    ]
    for betclan, forebet in cross_pairs:
        assert name_signature(betclan) == name_signature(forebet), (
            f"name_signature mismatch: {betclan!r} -> {name_signature(betclan)!r}"
            f" vs {forebet!r} -> {name_signature(forebet)!r}"
        )


def test_name_signature_distinct_players_dont_collide():
    """Surname-only matching must not merge different players whose surnames
    happen to share the canonical key.
    """
    assert name_signature("Zizou Bergs") != name_signature("Ugo Humbert")
    assert name_signature("Karolina Muchova") != name_signature("Madison Keys")
    assert name_signature("Naomi Osaka") != name_signature("Tatjana Maria")


def test_name_signature_pure_initial_fallback():
    """A name with only single-letter tokens should still produce a stable key."""
    sig_a = name_signature("A. B.")
    sig_b = name_signature("A. B.")
    assert sig_a == sig_b
    assert sig_a  # non-empty


