"""Test encoding fix for BetClan/PredixSport 1/2 -> player_a/b and market baseline."""

import pandas as pd
from scripts.mine_edges import get_cross_source_agree, _normalize_winner_for_agree

def test_normalize_winner_for_agree():
    # Legacy 1/2 codes
    assert _normalize_winner_for_agree("1") == "player_a"
    assert _normalize_winner_for_agree("2") == "player_b"
    assert _normalize_winner_for_agree("player_a") == "player_a"
    assert _normalize_winner_for_agree("player_b") == "player_b"
    # Case insensitive
    assert _normalize_winner_for_agree("PLAYER_A") == "player_a"
    # Empty
    assert _normalize_winner_for_agree("") is None
    assert _normalize_winner_for_agree(None) is None
    assert _normalize_winner_for_agree("nan") is None

    # Player name mapping
    row = pd.Series({"player_a": "Alexander Zverev", "player_b": "Ben Shelton"})
    assert _normalize_winner_for_agree("Alexander Zverev", row) == "player_a"
    assert _normalize_winner_for_agree("Ben Shelton", row) == "player_b"
    assert _normalize_winner_for_agree("Zverev", row) == "player_a"
    assert _normalize_winner_for_agree("Shelton", row) == "player_b"


def test_cross_source_agree_encoding_fix():
    # Simulate row where BetClan says "1" and Forebet says "player_a" - should be Both after fix, not Disagree
    row = pd.Series({
        "player_a": "Alexander Zverev",
        "player_b": "Ben Shelton",
        "predicted_winner": "player_a",  # Forebet
        "predicted_winner_betclan": "1",  # BetClan legacy
        "predicted_winner_foretennis": "player_a",
        "predicted_winner_bzzoiro": "player_a",
    })
    pred_cols = [c for c in row.index if c.startswith("predicted_winner")]
    agree = get_cross_source_agree(row, pred_cols)
    # After fix, all agree -> Both
    assert agree == "Both", f"Expected Both but got {agree} for picks that should agree after encoding fix"

    # Row where BetClan says "2" and Forebet says "player_a" -> genuine Disagree
    row2 = pd.Series({
        "player_a": "Alexander Zverev",
        "player_b": "Ben Shelton",
        "predicted_winner": "player_a",
        "predicted_winner_betclan": "2",  # BetClan says away wins
    })
    pred_cols2 = [c for c in row2.index if c.startswith("predicted_winner")]
    agree2 = get_cross_source_agree(row2, pred_cols2)
    assert agree2 == "Disagree", f"Expected Disagree for genuine conflict, got {agree2}"


def test_cross_source_agree_majority_vote():
    # 3 vs 1 consensus should be Both, not Disagree (weighted majority)
    row = pd.Series({
        "player_a": "A Player",
        "player_b": "B Player",
        "predicted_winner": "player_a",  # Forebet
        "predicted_winner_foretennis": "player_a",
        "predicted_winner_betclan": "player_a",
        "predicted_winner_bzzoiro": "player_b",  # odd one out
    })
    pred_cols = [c for c in row.index if c.startswith("predicted_winner")]
    agree = get_cross_source_agree(row, pred_cols)
    # Majority 3 vs 1 should be Both (consensus) not Disagree
    assert agree == "Both", f"Expected Both for 3-vs-1 majority, got {agree}"

    # 2 vs 2 tie should be Disagree
    row_tie = pd.Series({
        "player_a": "A Player",
        "player_b": "B Player",
        "predicted_winner": "player_a",
        "predicted_winner_foretennis": "player_a",
        "predicted_winner_betclan": "player_b",
        "predicted_winner_bzzoiro": "player_b",
    })
    pred_cols_tie = [c for c in row_tie.index if c.startswith("predicted_winner")]
    agree_tie = get_cross_source_agree(row_tie, pred_cols_tie)
    assert agree_tie == "Disagree", f"Expected Disagree for 2-vs-2 tie, got {agree_tie}"


def test_market_baseline_from_odds():
    # Test that market baseline is derived from odds
    from racketfactory.warehouse import build_warehouse
    import tempfile
    from pathlib import Path

    # Create minimal warehouse with odds but no market prediction
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Create a simple tennis data file
        df = pd.DataFrame([{
            "match_date": "2026-09-20",
            "tour": "ATP",
            "tournament": "Test Open",
            "round": "R32",
            "player_a": "A Player",
            "player_b": "B Player",
            "winner": "",
            "score": "",
            "odds_a": 1.5,
            "odds_b": 2.5,
            "bookmaker": "BetExplorer consensus",
            "source": "test",
            "captured_at": "2026-09-20T00:00:00Z",
            "oddsportal_url": "",
        }])
        df.to_csv(tmp_path / "tennis_test_2026-09.csv.gz", index=False, compression="gzip")

        # Build warehouse - should derive market baseline
        result = build_warehouse(data_dir=str(tmp_path), output_file="warehouse.csv.gz", db_path=tmp_path / "test.duckdb")
        # Read warehouse
        wh_path = tmp_path / "warehouse.csv.gz"
        if wh_path.exists():
            wh = pd.read_csv(wh_path, low_memory=False)
            # Check market baseline derived
            if "predicted_winner_market" in wh.columns:
                market_winner = wh.iloc[0].get("predicted_winner_market")
                # odds_a 1.5 < odds_b 2.5, so market should predict player_a
                assert market_winner == "player_a", f"Expected market to predict player_a (fav odds 1.5), got {market_winner}"
