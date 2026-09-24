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


def test_performance_merges_partial_history_passes(tmp_path, monkeypatch):
    """A bet-day settled across several grading passes shows every acca.

    Regression: 2026-09-23 settled in two passes — the @2.62 acca banked into
    a "partial" history entry, the @1.88 acca settled the next run — and the
    report kept only the newest history entry per date, so the day showed a
    lone @1.88L and hid the @2.62W (while the bank already included it).
    """
    import auto_tickets_grade as g
    monkeypatch.setattr(g, "LOCALDATA", tmp_path)
    state = {
        "bank": 120.0, "base_pct": 100.0, "cycle_base": 100.0,
        "paper_bank": 100.0, "open_slips": [], "events": [],
        "history": [
            {"date": "2026-09-22", "bank_pct": 110.0, "accas": [
                {"odds": 1.9, "won": True, "stake_pct": 10.0, "paper": False, "legs": []},
            ]},
            # 2026-09-23 settles over two passes: partial first, final later
            {"date": "2026-09-23", "bank_pct": 134.0, "partial": True,
             "pending_accas": 1, "accas": [
                 {"odds": 2.62, "won": True, "stake_pct": 15.55, "paper": False, "legs": []},
             ]},
            {"date": "2026-09-23", "bank_pct": 120.0, "accas": [
                {"odds": 1.88, "won": False, "stake_pct": 15.55, "paper": False, "legs": []},
            ]},
        ],
    }
    g.write_performance(state)
    txt = (tmp_path / "auto_tickets_performance.txt").read_text()
    day_lines = [ln for ln in txt.splitlines() if "2026-09-23" in ln]
    assert len(day_lines) == 1                 # one line per bet-day
    assert "@2.62W @1.88L" in day_lines[0]     # both passes on that line
    assert "120.0%" in day_lines[0]            # end-of-day bank, not the partial 134.0
    assert "134.0" not in txt                  # intermediate pass snapshot dropped
    assert "REAL accas 2W/1L" in txt           # tallies still count every pass once

