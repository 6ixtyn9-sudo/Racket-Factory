"""Live-card odds matching: bounded date tolerance + single-side collapse.

Covers warehouse._match_api_odds_row (exact date first, then ±1 day for
singles only — never dateless) and collapse_live_card's single-side
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


def test_api_match_allows_minus_one_day_slam_drift():
    # Tiafoe/Shelton replay: card 09-12, API commence 09-11 (UTC straddle).
    card = _card_row("2026-09-12", "Frances Tiafoe", "Ben Shelton")
    api = _api_row("2026-09-11", "Frances Tiafoe", "Ben Shelton")
    matched, reversed_order = _match_api_odds_row(card, [api])
    assert matched is api
    assert reversed_order is False


def test_api_match_allows_plus_one_day_with_reversed_flag():
    card = _card_row("2026-09-12")
    api = _api_row("2026-09-13", "Karen Khachanov", "Alexander Zverev")
    matched, reversed_order = _match_api_odds_row(card, [api])
    assert matched is api
    assert reversed_order is True


def test_api_match_rejects_two_day_drift():
    card = _card_row("2026-09-12")
    assert _match_api_odds_row(card, [_api_row("2026-09-10")])[0] is None
    assert _match_api_odds_row(card, [_api_row("2026-09-14")])[0] is None


def test_api_match_rejects_rematch_weeks_later():
    # Same matchup meeting again must never inherit stale odds.
    card = _card_row("2026-09-12")
    assert _match_api_odds_row(card, [_api_row("2026-09-19")])[0] is None


def test_api_match_second_pass_allows_doubles():
    # Updated: second pass now allows doubles too to push comparison 9->30+
    card = _card_row("2026-09-12", "A / B", "C / D")
    api = {"match_date": "2026-09-11", "player_home": "A / B",
           "player_away": "C / D", "odds_home": 1.5, "odds_away": 2.5}
    assert _match_api_odds_row(card, [api])[0] is not None


def test_api_match_dateless_card_stays_fail_open():
    card = _card_row("")
    matched, _ = _match_api_odds_row(card, [_api_row("2026-09-12")])
    assert matched is not None


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
