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



def test_performance_price_band_ledger(tmp_path, monkeypatch):
    """Staked legs bucketed <1.30 / 1.30-1.59 / 1.60+ with hit% vs breakeven%.

    Paper accas, VOID/unsettled legs, and legs without numeric odds > 1.0
    are excluded. Partial passes of one bet-day each carry distinct accas,
    so every leg counts once. Band lower bounds are inclusive.
    """
    import json
    import auto_tickets_grade as g
    monkeypatch.setattr(g, "LOCALDATA", tmp_path)

    def leg(odds, outcome):
        out = {"match": "A vs B", "selected_player": "A", "odds": odds}
        if outcome:
            out["_settle_outcome"] = outcome
        return out

    state = {
        "bank": 110.0, "base_pct": 100.0, "cycle_base": 100.0,
        "paper_bank": 100.0, "open_slips": [], "events": [],
        "history": [
            {"date": "2026-09-20", "bank_pct": 105.0, "accas": [
                {"odds": 3.3, "won": False, "paper": False, "legs": [
                    leg(1.25, "WON"), leg(1.29, "LOST"),
                    leg(1.30, "WON"), leg(1.59, "LOST")]},
            ]},
            {"date": "2026-09-21", "bank_pct": 115.0, "partial": True, "accas": [
                {"odds": 2.3, "won": True, "paper": False, "legs": [
                    leg("1.45", "WON"), leg(1.60, "WON"), leg(1.80, "VOID")]},
            ]},
            {"date": "2026-09-21", "bank_pct": 110.0, "accas": [
                {"odds": 2.2, "won": False, "paper": False, "legs": [
                    leg(2.20, "LOST"), leg(None, "WON"), leg("nan", "WON"),
                    leg(1.0, "WON"), leg(1.50, None)]},
            ]},
            {"date": "2026-09-22", "bank_pct": 110.0, "accas": [
                {"odds": 1.7, "won": True, "paper": True, "legs": [
                    leg(1.40, "WON"), leg(1.20, "WON")]},
            ]},
        ],
    }
    g.write_performance(state)
    txt = (tmp_path / "auto_tickets_performance.txt").read_text()
    section = txt[txt.index("--- STAKED legs by price band"):].splitlines()
    rows = {ln.split()[0]: " ".join(ln.split()) for ln in section[2:] if ln.strip()}
    #       band      n  W-L  hit%   avg    breakeven spread
    assert rows["<1.30"] == "<1.30 2 1-1 50.0% 1.270 78.7% -28.7pp"
    assert rows["1.30-1.59"] == "1.30-1.59 3 2-1 66.7% 1.447 69.1% -2.4pp"
    assert rows["1.60+"] == "1.60+ 2 1-1 50.0% 1.900 52.6% -2.6pp"
    assert rows[">=1.30"] == ">=1.30 5 3-2 60.0% 1.628 61.4% -1.4pp"
    assert rows["all"] == "all 7 4-3 57.1% 1.526 65.5% -8.4pp"

    bands = json.loads((tmp_path / "auto_tickets_performance.json").read_text())["price_bands"]
    assert [(b["band"], b["n"], b["wins"], b["losses"]) for b in bands["bands"]] == [
        ("<1.30", 2, 1, 1), ("1.30-1.59", 3, 2, 1), ("1.60+", 2, 1, 1)]
    assert bands[">=1.30"]["n"] == 5 and bands["all"]["n"] == 7
    assert bands["bands"][0]["breakeven_pct"] == 78.7  # 100 / avg odds 1.27

    # No settled staked legs yet: bands render as n=0 with dashes, no crash.
    state["history"] = []
    g.write_performance(state)
    empty = (tmp_path / "auto_tickets_performance.txt").read_text()
    assert " ".join(empty.splitlines()[-3].split()) == "1.60+ 0 0-0 — — — —"
    assert json.loads((tmp_path / "auto_tickets_performance.json").read_text())[
        "price_bands"]["all"] == {"band": "all", "n": 0, "wins": 0, "losses": 0,
                                  "hit_pct": None, "avg_odds": None,
                                  "breakeven_pct": None, "spread_pp": None}
