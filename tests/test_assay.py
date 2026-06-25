from racketfactory.assay import odds_band, roi_from_bets, score_rows


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
