from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from racketfactory.oddsportal import COLUMNS

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "localdata"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "warehouse.duckdb"


def connect(db_path: str | Path = DEFAULT_DB_PATH):
    import duckdb
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def load_oddsportal_rows(data_dir: str | Path = DEFAULT_DATA_DIR) -> pd.DataFrame:
    paths = sorted(Path(data_dir).glob("oddsportal_tennis_*.csv.gz"))
    frames = [pd.read_csv(p, low_memory=False) for p in paths]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[COLUMNS]


def build_warehouse(*, data_dir: str | Path = DEFAULT_DATA_DIR, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    df = load_oddsportal_rows(data_dir)
    con = connect(db_path)
    con.register("oddsportal_df", df)
    con.execute("CREATE OR REPLACE TABLE oddsportal_matches AS SELECT * FROM oddsportal_df")
    con.execute("""
        CREATE OR REPLACE VIEW settled_matches AS
        SELECT *,
               CASE
                 WHEN winner = player_a THEN 'A'
                 WHEN winner = player_b THEN 'B'
                 ELSE NULL
               END AS winner_side
        FROM oddsportal_matches
        WHERE winner IS NOT NULL AND winner != ''
          AND odds_a IS NOT NULL AND odds_b IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE VIEW market_sides AS
        SELECT match_date, tour, tournament, round, player_a AS player, player_b AS opponent,
               winner, score, 'A' AS side, odds_a AS decimal_odds,
               odds_a <= odds_b AS is_favorite,
               winner = player_a AS won,
               bookmaker, source, oddsportal_url
        FROM settled_matches
        UNION ALL
        SELECT match_date, tour, tournament, round, player_b AS player, player_a AS opponent,
               winner, score, 'B' AS side, odds_b AS decimal_odds,
               odds_b < odds_a AS is_favorite,
               winner = player_b AS won,
               bookmaker, source, oddsportal_url
        FROM settled_matches
    """)
    counts = {
        "oddsportal_rows": int(con.execute("SELECT count(*) FROM oddsportal_matches").fetchone()[0]),
        "settled_matches": int(con.execute("SELECT count(*) FROM settled_matches").fetchone()[0]),
        "market_sides": int(con.execute("SELECT count(*) FROM market_sides").fetchone()[0]),
        "db_path": str(db_path),
    }
    con.close()
    return counts
