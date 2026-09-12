"""Grader settlement via the shared settlement module (real/paper/void)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from auto_tickets_grade import _leg_odds_trusted, settle_leg


def _df(rows):
    return pd.DataFrame(rows)


def _leg(match, sel, odds=1.5, src="TheOddsAPI", rej=None):
    return {"match": match, "selected_player": sel, "odds": odds,
            "odds_source": src, "odds_reject_reason": rej, "date": "2026-09-11"}


def test_won_leg_records_evidence():
    leg = _leg("Alexander Zverev vs Karen Khachanov", "Alexander Zverev")
    df = _df([{"player_a": "Zverev A.", "player_b": "Khachanov K.",
               "winner": "Zverev A.", "score": "3-0 6-3 7-6 7-6",
               "match_date": "2026-09-11", "source": "T"}])
    assert settle_leg(leg, df, None, target_date="2026-09-11") == "WON"
    assert "Zverev" in leg["_settle_reason"] or "Challenger" in leg["_settle_reason"] or "settled" in leg["_settle_reason"]
    assert leg["_settle_basis"]["winner"] == "Zverev A."


def test_truncated_doubles_names_settle():
    leg = _leg("Brancaccio / Papamichail vs Cascino / Feng", "Cascino / Feng",
               odds=2.05, src="nan", rej="missing selected-side odds")
    df = _df([{"player_a": "Cascino E / Feng S.", "player_b": "Brancacci / Papamicha",
               "winner": "Cascino E / Feng S.", "score": "2-0 6-3 6-2",
               "match_date": "2026-09-11", "source": "Challenger_results"}])
    assert settle_leg(leg, df, None, target_date="2026-09-11") == "WON"
    assert not _leg_odds_trusted(leg)


def test_phantom_match_stays_pending():
    leg = _leg("Elsa Jacquemot vs Anna Blinkova", "Anna Blinkova",
               odds=2.25, src="nan", rej="missing selected-side odds")
    df = _df([{"player_a": "Blinkova A.", "player_b": "Riera J.",
               "winner": "Blinkova A.", "score": "2-0 6-2 6-2",
               "match_date": "2026-09-11", "source": "T"}])
    assert settle_leg(leg, df, None, target_date="2026-09-11") == "PENDING"


def test_walkover_is_void():
    leg = _leg("Strakhova / Tikhonova vs Jacquemot / Quevedo", "Strakhova / Tikhonova")
    df = _df([{"player_a": "Strakhova / Tikhonova", "player_b": "Jacquemot / Quevedo",
               "winner": "Strakhova / Tikhonova", "score": "W.O.",
               "match_date": "2026-09-11", "source": "T"}])
    assert settle_leg(leg, df, None, target_date="2026-09-11") == "VOID"


def test_conflicting_rows_hold_acca_open():
    leg = _leg("Coco Gauff vs Elena Rybakina", "Elena Rybakina")
    df = _df([
        {"player_a": "Coco Gauff", "player_b": "Elena Rybakina",
         "winner": "Elena Rybakina", "score": "", "_sets_a": 1, "_sets_b": 2,
         "match_date": "2026-09-11", "source": "A"},
        {"player_a": "Rybakina E.", "player_b": "Gauff C.", "winner": "Gauff C.",
         "score": "", "_sets_a": 1, "_sets_b": 2,
         "match_date": "2026-09-11", "source": "B"},
    ])
    assert settle_leg(leg, df, None, target_date="2026-09-11") == "CONFLICT"


def test_odds_trust_rules():
    assert _leg_odds_trusted({"odds": 1.24, "odds_source": "TheOddsAPI"})
    assert not _leg_odds_trusted({"odds": 2.05, "odds_source": "nan",
                                  "odds_reject_reason": "missing selected-side odds"})
    assert not _leg_odds_trusted({"odds": 2.05, "odds_source": ""})
    assert not _leg_odds_trusted({"odds": 1.0, "odds_source": "X"})
