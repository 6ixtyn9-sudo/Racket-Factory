"""Tennis-data adapter: player_a-first scores, no-odds retention, cache age."""
import gzip
import os
import time

import pandas as pd
import pytest

from racketfactory.sources.tennisdata import (
    _cache_is_fresh,
    flip_score_perspective,
    format_score,
    normalize_row,
    read_yearly_excel,
    repair_monthly_scores,
)


def _td_row(**kw):
    row = {"Tournament": "Brisbane", "Date": "2026-01-04", "Series": "ATP250",
           "Court": "Outdoor", "Surface": "Hard", "Round": "1st Round",
           "Winner": "Halys Q.", "Loser": "Popyrin A.",
           "W1": 5, "L1": 7, "W2": 6, "L2": 3, "W3": 6, "L3": 4,
           "WRank": 87, "LRank": 30, "Comment": "Completed",
           "PSW": 2.49, "PSL": 1.60, "B365W": None, "B365L": None,
           "AvgW": None, "AvgL": None, "Location": "Brisbane"}
    row.update(kw)
    return row


def test_upset_score_flips_to_player_a_first():
    out = normalize_row(_td_row(), tour="ATP")
    assert out is not None
    assert (out["player_a"], out["player_b"]) == ("Popyrin A.", "Halys Q.")
    assert out["winner"] == "Halys Q."
    assert out["score"] == "7-5 3-6 4-6"
    assert out["_score_perspective"] == "player_a_games-player_b_games"


def test_favourite_win_keeps_winner_first_order():
    out = normalize_row(_td_row(PSW=1.60, PSL=2.49), tour="ATP")
    assert out is not None
    assert (out["player_a"], out["player_b"]) == ("Halys Q.", "Popyrin A.")
    assert out["score"] == "5-7 6-3 6-4"


def test_no_odds_rows_kept_alphabetical():
    out = normalize_row(_td_row(PSW=None, PSL=None), tour="ATP")
    assert out is not None
    assert out["odds_a"] is None and out["odds_b"] is None
    assert out["bookmaker"] == "" and out["_odds_source"] == ""
    # alphabetical by canonical key: halys < popyrin
    assert (out["player_a"], out["player_b"]) == ("Halys Q.", "Popyrin A.")
    assert out["winner"] == "Halys Q."
    assert out["score"] == "5-7 6-3 6-4"


def test_flip_skips_annotated_tokens():
    fixed, skipped = flip_score_perspective("6-3 7-68 RET")
    assert fixed == "3-6 7-68 RET"
    assert skipped == 2


def test_repair_monthly_file(tmp_path):
    path = tmp_path / "tennisdata_tennis_2026-01.csv.gz"
    pd.DataFrame([
        {"match_date": "2026-01-04", "player_a": "Popyrin A.",
         "player_b": "Halys Q.", "winner": "Halys Q.",
         "score": "5-7 6-3 6-4"},
        {"match_date": "2026-01-04", "player_a": "Tiafoe F.",
         "player_b": "Vukic A.", "winner": "Tiafoe F.", "score": "6-2 6-2"},
    ]).to_csv(path, index=False, compression="gzip")
    stats = repair_monthly_scores(path)
    assert stats["rows"] == 2 and stats["flipped"] == 1
    assert stats["skipped_tokens"] == 0 and stats["stripped_00"] == 0
    with gzip.open(path, "rt") as f:
        rows = list(__import__("csv").DictReader(f))
    assert rows[0]["score"] == "7-5 3-6 4-6"
    assert rows[1]["score"] == "6-2 6-2"
    assert rows[0]["_score_perspective"] == "player_a_games-player_b_games"
    # idempotent: second run flips nothing
    stats2 = repair_monthly_scores(path)
    assert stats2["flipped"] == 0 and stats2["already_labeled"] == 2


def test_zero_zero_sets_dropped():
    row = {"W1": 6, "L1": 4, "W2": 6, "L2": 2, "W3": 0, "L3": 0}
    assert format_score(row) == "6-4 6-2"


def test_cache_age_rules(tmp_path):
    from datetime import datetime
    f = tmp_path / "2026.xlsx"
    f.write_text("x")
    # past year: always fresh
    assert _cache_is_fresh(f, 2020, 24.0) is True
    # current year, just written: fresh
    assert _cache_is_fresh(f, datetime.now().year, 24.0) is True
    # current year, old mtime: stale
    old = time.time() - 25 * 3600
    os.utime(f, (old, old))
    assert _cache_is_fresh(f, datetime.now().year, 24.0) is False
    # missing: not fresh
    assert _cache_is_fresh(tmp_path / "nope.xlsx", 2020, 24.0) is False


def test_yearly_excel_roundtrip(tmp_path):
    pytest.importorskip("openpyxl")
    xlsx = tmp_path / "2026.xlsx"
    pd.DataFrame([_td_row(), _td_row(Winner="Tiafoe F.", Loser="Vukic A.",
                                     W1=6, L1=2, W2=6, L2=3, W3=None, L3=None,
                                     PSW=1.52, PSL=2.70)]).to_excel(xlsx, index=False)
    rows = read_yearly_excel(xlsx, tour="ATP")
    assert len(rows) == 2
    assert rows[0]["score"] == "7-5 3-6 4-6"
    assert rows[1]["score"] == "6-2 6-3"
