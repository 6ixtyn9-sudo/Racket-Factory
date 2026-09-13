"""Priced/paper ticket tracks: no fabricated odds, unpriced legs never staked."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import auto_tickets as at


def _pick(match, sel, odds=None, src=None, conf=70, verdict="BOOST",
          bucket="WATCHLIST", late=False):
    p = {"match": match, "selected_player": sel, "odds": odds,
         "odds_source": src, "confidence": conf, "ml_verdict": verdict,
         "bucket": bucket, "source": "T", "source_count": 1,
         "prediction_prob": 0.65}
    if late:
        p["_late_start"] = True
    return p


def test_real_odds_gate():
    assert at._leg_real_odds({"odds": 1.9, "odds_source": "TheOddsAPI"}) == 1.9
    assert at._leg_real_odds({"odds": 2.05, "odds_source": "nan"}) is None
    assert at._leg_real_odds({"odds": 2.05, "odds_source": "ML_Estimated"}) is None
    assert at._leg_real_odds({"odds": 1.9, "odds_source": "Bzzoiro",
                              "odds_reject_reason": "x"}) is None
    assert at._leg_real_odds({"odds": None, "odds_source": "X"}) is None
    assert at._leg_real_odds({"odds": 1.0, "odds_source": "X"}) is None


def test_no_fabrication_entrypoint():
    assert not hasattr(at, "estimate_odds_from_confidence")


def test_priced_and_paper_tracks_split():
    pool = [
        _pick("A1 vs B1", "A1", odds=1.9, src="TheOddsAPI"),
        _pick("A2 vs B2", "A2", odds=2.1, src="Bzzoiro"),
        _pick("C1 vs D1", "C1", bucket="WATCHLIST_NO_ODDS"),
        _pick("C2 vs D2", "C2", bucket="WATCHLIST_NO_ODDS"),
    ]
    priced, paper, _ = at.build_accas(pool)
    assert priced and all(not a["paper"] for a in priced)
    assert paper and all(a["paper"] for a in paper)
    assert all(a["odds"] is None for a in paper)
    for a in priced:
        for leg in a["legs"]:
            assert at._leg_real_odds(leg) is not None


def test_late_start_forces_paper():
    pool = [_pick("A1 vs B1", "A1", odds=1.9, src="TheOddsAPI", late=True),
            _pick("A2 vs B2", "A2", odds=2.1, src="TheOddsAPI", late=True)]
    priced, paper, _ = at.build_accas(pool)
    assert priced == []
    assert len(paper) == 1 and paper[0]["legs"][0]["_late_start"]


def test_short_odds_excluded_from_priced():
    # Super-short <1.10 should be excluded even for BOOST unless calib prob >=85%
    # Use 1.05 with low prob 0.65 -> should be excluded
    pool = [_pick("A1 vs B1", "A1", odds=1.05, src="TheOddsAPI", conf=60, verdict="ALLOW"),
            _pick("A2 vs B2", "A2", odds=2.1, src="TheOddsAPI"),
            _pick("A3 vs B3", "A3", odds=1.05, src="TheOddsAPI", conf=60, verdict="ALLOW")]
    priced, paper, _ = at.build_accas(pool)
    # Only one priced-eligible leg (2.1) -> no priced acca possible (needs 2 legs)
    assert priced == []


def test_scraped_fallback_never_real_odds():
    assert at._leg_real_odds({"odds": 1.78, "odds_source": "ScrapedFallback"}) is None
    assert at._leg_real_odds({"odds": 1.78, "odds_source": "TheOddsAPI",
                              "_is_paper": True}) is None
    assert at._leg_real_odds({"odds": 1.78, "odds_source": "OddsPortal"}) == 1.78
    assert at._leg_real_odds({"odds": 1.78, "odds_source": "Bzzoiro"}) == 1.78


def test_scraped_leg_forced_to_paper_track():
    pool = [_pick("A1 vs B1", "A1", odds=1.78, src="ScrapedFallback"),
            _pick("A2 vs B2", "A2", odds=2.10, src="ScrapedFallback")]
    priced, paper, _ = at.build_accas(pool)
    assert priced == []
    assert paper and all(a["paper"] for a in paper)
    assert all(a["odds"] is None for a in paper)
