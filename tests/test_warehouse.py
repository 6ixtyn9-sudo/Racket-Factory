import pandas as pd

from racketfactory.oddsportal import COLUMNS
from racketfactory.warehouse import build_warehouse


def test_build_warehouse_counts(tmp_path):
    df = pd.DataFrame([{
        "match_date": "2026-06-24",
        "tour": "ATP",
        "tournament": "Test Open",
        "round": "R32",
        "player_a": "A Player",
        "player_b": "B Player",
        "winner": "A Player",
        "score": "6-4 6-4",
        "odds_a": 1.8,
        "odds_b": 2.1,
        "bookmaker": "fixture",
        "source": "fixture",
        "captured_at": "2026-06-24T00:00:00Z",
        "oddsportal_url": "",
    }], columns=COLUMNS)
    df.to_csv(tmp_path / "oddsportal_tennis_2026-06.csv.gz", index=False, compression="gzip")
    counts = build_warehouse(data_dir=tmp_path, db_path=tmp_path / "warehouse.duckdb")
    assert counts["oddsportal_rows"] == 1
    assert counts["settled_matches"] == 1
    assert counts["market_sides"] == 2
