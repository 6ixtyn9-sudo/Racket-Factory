"""Live-card odds matching: exact normalized date + single-side collapse.

Covers warehouse._match_api_odds_row (same date only, never dateless)
and collapse_live_card's single-side
fallback (one valid side preserved, both-long pairs rejected, strict
two-way pairs always win over singles).
"""
import pandas as pd

from racketfactory.warehouse import (
    _match_api_odds_row,
    collapse_live_card,
)


def _card_row(day, home="Alexander Zverev", away="Karen Khachanov"):
    return pd.Series({"match_date": day, "player_home": home, "player_away": away})


def _api_row(day, home="Alexander Zverev", away="Karen Khachanov"):
    return {"match_date": day, "player_home": home, "player_away": away,
            "odds_home": 1.5, "odds_away": 2.5}


def test_api_match_exact_date_preferred_over_drifted():
    card = _card_row("2026-09-12")
    drifted = _api_row("2026-09-11")
    exact = _api_row("2026-09-12")
    matched, reversed_order = _match_api_odds_row(card, [drifted, exact])
    assert matched is exact
    assert reversed_order is False


def test_api_match_rejects_unproven_minus_one_day_slam_drift():
    # A claimed timezone straddle does not prove identity without timestamps.
    card = _card_row("2026-09-12", "Frances Tiafoe", "Ben Shelton")
    api = _api_row("2026-09-11", "Frances Tiafoe", "Ben Shelton")
    matched, reversed_order = _match_api_odds_row(card, [api])
    assert matched is None
    assert reversed_order is False


def test_api_match_rejects_unproven_plus_one_day_even_reversed():
    card = _card_row("2026-09-12")
    api = _api_row("2026-09-13", "Karen Khachanov", "Alexander Zverev")
    matched, reversed_order = _match_api_odds_row(card, [api])
    assert matched is None
    assert reversed_order is False
    same_day = dict(api, match_date="2026-09-12")
    assert _match_api_odds_row(card, [same_day]) == (same_day, True)


def test_api_match_rejects_two_day_drift():
    card = _card_row("2026-09-12")
    assert _match_api_odds_row(card, [_api_row("2026-09-10")])[0] is None
    assert _match_api_odds_row(card, [_api_row("2026-09-14")])[0] is None


def test_api_match_rejects_rematch_weeks_later():
    # Same matchup meeting again must never inherit stale odds.
    card = _card_row("2026-09-12")
    assert _match_api_odds_row(card, [_api_row("2026-09-19")])[0] is None


def test_api_match_doubles_require_same_date():
    # Reordered team members are accepted, but not a different date.
    card = _card_row("2026-09-12", "Siniakova / Townsend", "Cascino / Feng")
    api = {"match_date": "2026-09-11", "player_home": "Townsend / Siniakova",
           "player_away": "Cascino / Feng", "odds_home": 1.5, "odds_away": 2.5}
    assert _match_api_odds_row(card, [api])[0] is None
    same_day = dict(api, match_date="2026-09-12")
    assert _match_api_odds_row(card, [same_day])[0] is same_day


def test_api_match_dateless_card_fails_closed():
    card = _card_row("")
    matched, _ = _match_api_odds_row(card, [_api_row("2026-09-12")])
    assert matched is None


def _live_row(home_odds, away_odds, **over):
    row = {"match_date": "2026-09-13", "match_time": "12:00",
           "tour": "ATP", "match_type": "Singles",
           "player_home": "Alexander Zverev", "player_away": "Karen Khachanov",
           "tournament": "US Open", "source": "BetClan",
           "predicted_winner": "player_a", "prob_home": 70, "prob_away": 30,
           "odds_home": home_odds, "odds_away": away_odds}
    row.update(over)
    return row


def test_collapse_keeps_single_side_odds():
    out = collapse_live_card(pd.DataFrame([_live_row(1.5, None)]))
    assert len(out) == 1
    assert float(out.iloc[0]["odds_home"]) == 1.5
    assert pd.isna(out.iloc[0]["odds_away"])


def test_collapse_rejects_both_long_pair():
    out = collapse_live_card(pd.DataFrame([_live_row(9.5, 9.7)]))
    assert len(out) == 1
    assert pd.isna(out.iloc[0]["odds_home"])
    assert pd.isna(out.iloc[0]["odds_away"])


def test_collapse_strict_pair_beats_single_side():
    pair = _live_row(1.5, 2.5, source="Forebet")
    single = _live_row(3.0, None, source="BetClan")
    out = collapse_live_card(pd.DataFrame([pair, single]))
    assert len(out) == 1
    # Strict two-way pair wins exclusively; the 3.0 single must not leak in.
    assert float(out.iloc[0]["odds_home"]) == 1.5
    assert float(out.iloc[0]["odds_away"]) == 2.5
