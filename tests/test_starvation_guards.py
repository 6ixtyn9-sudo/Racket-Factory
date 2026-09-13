"""Guards against the 2026-09-13 no-picks outage class.

Covers:
  * same-job live-fetch cache (racketfactory.fetch_cache)
  * no-clobber rules for official picks + ticket ledgers
  * Forebet Jina markdown US-date parsing (MM/DD first)
  * doctor live-row visibility (warehouse:live_rows)
"""
import gzip
import json
from pathlib import Path

import pytest

from racketfactory import doctor
from racketfactory.fetch_cache import cached_fetch, read_cached_rows
from racketfactory.sources.forebet import ForebetPredictor
from scripts import auto_tickets, mine_edges


# ------------------------------------------------ fetch cache --


def _rows(*names):
    return [{"player_home": n, "player_away": "Opp", "match_date": "2026-09-13"}
            for n in names]


def test_fetch_cache_hit_avoids_second_fetch(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return _rows("A", "B")

    first = cached_fetch("betclan", fetch, ttl_minutes=45, cache_dir=tmp_path)
    second = cached_fetch("betclan", fetch, ttl_minutes=45, cache_dir=tmp_path)
    assert [r["player_home"] for r in first] == ["A", "B"]
    assert second == first
    assert len(calls) == 1  # second call served from disk


def test_fetch_cache_empty_results_are_never_cached(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return []

    assert cached_fetch("betclan", fetch, ttl_minutes=45, cache_dir=tmp_path) == []
    assert cached_fetch("betclan", fetch, ttl_minutes=45, cache_dir=tmp_path) == []
    assert len(calls) == 2  # failures keep retrying the live site
    assert list(tmp_path.iterdir()) == []


def test_fetch_cache_expired_entry_refetches(tmp_path):
    import os
    import time

    cached_fetch("predixsport", lambda: _rows("A"), ttl_minutes=45,
                 cache_dir=tmp_path)
    only = next(tmp_path.iterdir())
    stale = time.time() - 3600
    os.utime(only, (stale, stale))

    calls = []

    def fetch():
        calls.append(1)
        return _rows("B")

    rows = cached_fetch("predixsport", fetch, ttl_minutes=45, cache_dir=tmp_path)
    assert [r["player_home"] for r in rows] == ["B"]
    assert len(calls) == 1


def test_fetch_cache_corrupt_file_refetches(tmp_path):
    from racketfactory.fetch_cache import cache_path

    path = cache_path("betclan", "2026-09-13", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    rows = cached_fetch("betclan", lambda: _rows("A"), fetch_day="2026-09-13",
                        ttl_minutes=45, cache_dir=tmp_path)
    assert [r["player_home"] for r in rows] == ["A"]


def test_fetch_cache_ttl_zero_bypasses(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return _rows("A")

    cached_fetch("betclan", fetch, ttl_minutes=0, cache_dir=tmp_path)
    cached_fetch("betclan", fetch, ttl_minutes=0, cache_dir=tmp_path)
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_fetch_cache_read_miss_returns_none(tmp_path):
    assert read_cached_rows("betclan", ttl_minutes=45, cache_dir=tmp_path) is None


# ------------------------------------------------ picks no-clobber --


def test_picks_no_clobber_truth_table():
    assert mine_edges.should_write_pick_outputs([{"a": 1}], [{"old": 1}]) is True
    assert mine_edges.should_write_pick_outputs([{"a": 1}], None) is True
    assert mine_edges.should_write_pick_outputs([], None) is True
    assert mine_edges.should_write_pick_outputs([], []) is True
    # The outage rule: empty mine must never blank a good ledger.
    assert mine_edges.should_write_pick_outputs([], [{"old": 1}]) is False


def test_write_official_pick_outputs_keeps_good_ledger_on_empty_mine(tmp_path, monkeypatch):
    monkeypatch.setattr(mine_edges, "ROOT", tmp_path)
    out_dir = tmp_path / "localdata"
    out_dir.mkdir()
    good = [{"match": "A vs B", "bucket": "WATCHLIST_NO_ODDS"}]
    (out_dir / "picks_2026-09-13.json").write_text(json.dumps(good))
    (out_dir / "picks_today.json").write_text(json.dumps(good))

    mine_edges.write_official_pick_outputs("2026-09-13", [])

    assert json.loads((out_dir / "picks_2026-09-13.json").read_text()) == good
    assert json.loads((out_dir / "picks_today.json").read_text()) == good


def test_write_official_pick_outputs_writes_first_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(mine_edges, "ROOT", tmp_path)
    mine_edges.write_official_pick_outputs("2026-09-13", [])
    out_dir = tmp_path / "localdata"
    assert json.loads((out_dir / "picks_2026-09-13.json").read_text()) == []
    assert json.loads((out_dir / "picks_today.json").read_text()) == []


def test_write_official_pick_outputs_overwrites_with_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(mine_edges, "ROOT", tmp_path)
    out_dir = tmp_path / "localdata"
    out_dir.mkdir()
    (out_dir / "picks_2026-09-13.json").write_text(json.dumps([{"old": 1}]))
    new = [{"match": "C vs D", "bucket": "WATCHLIST"}]
    mine_edges.write_official_pick_outputs("2026-09-13", new)
    assert json.loads((out_dir / "picks_2026-09-13.json").read_text()) == new


# ------------------------------------------------ tickets no-clobber --


def test_tickets_no_clobber_truth_table():
    frozen = {"frozen": True, "accas": [{"legs": []}]}
    booked = {"frozen": False, "accas": [{"legs": []}]}
    empty = {"frozen": False, "accas": []}
    assert auto_tickets.should_write_ticket_files(None, 0) is True
    assert auto_tickets.should_write_ticket_files(empty, 0) is True
    assert auto_tickets.should_write_ticket_files(booked, 2) is True
    assert auto_tickets.should_write_ticket_files(booked, 0) is False
    assert auto_tickets.should_write_ticket_files(frozen, 2) is False
    assert auto_tickets.should_write_ticket_files(frozen, 0) is False
    # Ops escape hatch.
    assert auto_tickets.should_write_ticket_files(frozen, 0, force=True) is True
    assert auto_tickets.should_write_ticket_files(booked, 0, force=True) is True


# ------------------------------------------------ Forebet Jina US dates --


JINA_SEPT_MD = """\
Tennis predictions for today
ATP US Open - Semi-finals
[F. Tiafoe B. Shelton 09/11/2026 01:45](https://www.forebet.com/en/tennis/matches/atp-singles/us-open/12345/)
41 59
2 1-3
ATP Challenger Tulln - Quarter-finals
[J. Reis Da Silva A. Barrena 09/12/2026 12:00](https://www.forebet.com/en/tennis/matches/challenger-singles/tulln/67890/)
44 56
2 1-2
"""


def test_jina_markdown_september_dates_stay_in_september():
    rows = ForebetPredictor().parse_jina_markdown(JINA_SEPT_MD)
    assert len(rows) == 2
    by_home = {r["player_home"]: r for r in rows}
    # Regression: DD/MM-first misread these as Nov 9 / Dec 9.
    assert by_home["F. Tiafoe"]["match_date"] == "2026-09-11"
    assert by_home["J. Reis Da Silva"]["match_date"] == "2026-09-12"
    assert by_home["F. Tiafoe"]["tour_slug"] == "atp-singles"
    assert by_home["F. Tiafoe"]["predicted_winner"] == "2"


# ------------------------------------------------ doctor live rows --


def _write_gz(path: Path, header: str, lines: list[str]) -> None:
    with gzip.open(path, "wt", newline="") as f:
        f.write(header + "\n")
        for line in lines:
            f.write(line + "\n")


def test_doctor_count_live_rows(tmp_path):
    wh = tmp_path / "warehouse.csv.gz"
    _write_gz(wh, "match_date,player_a,player_b,_is_live",
              ["2026-09-13,A,B,True", "2026-09-12,C,D,False",
               "2026-09-12,E,F,"])
    assert doctor.count_live_rows(wh) == 1


def test_doctor_count_live_rows_missing_col_or_file(tmp_path):
    assert doctor.count_live_rows(tmp_path / "nope.csv.gz") is None
    wh = tmp_path / "w.csv.gz"
    _write_gz(wh, "match_date,player_a", ["2026-09-12,A"])
    assert doctor.count_live_rows(wh) is None


def test_doctor_reports_empty_picks_export(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "LOCALDATA", tmp_path)
    monkeypatch.setenv("RACKET_FACTORY_RUN_AS_OF", "2026-09-13")
    (tmp_path / "picks_2026-09-13.json").write_text("[]")
    checks = doctor.run_health_checks(as_of="2026-09-13")
    assert checks["picks:priced_share"]["status"] == "WARN"
    assert "0 rows" in checks["picks:priced_share"]["detail"]
    assert "warehouse:live_rows" in checks


# ------------------------------------------------ miner candidate coverage --


def _settled_row(i, *, day="2026-09-01", won=True):
    a, b = f"Hist A{i}", f"Hist B{i}"
    return {"match_date": day, "tour": "ATP", "tournament": "US Open",
            "_series": "Grand Slam", "player_a": a, "player_b": b,
            "winner": a if won else b, "odds_a": 1.5, "odds_b": 2.5,
            "rank_a": 5, "rank_b": 40, "predicted_winner": "player_a",
            "predicted_winner_market": "player_a",
            "predicted_winner_foretennis": "player_a",
            "prediction_prob": 0.75, "_is_live": False, "_comment": ""}


def _run_mine(tmp_path, monkeypatch, rows, target):
    import pandas as pd

    monkeypatch.setattr(mine_edges, "ROOT", tmp_path)
    wh = tmp_path / "warehouse.csv.gz"
    pd.DataFrame(rows).to_csv(wh, index=False, compression="gzip")
    monkeypatch.setattr(
        mine_edges.sys, "argv",
        ["mine_edges", "--warehouse", str(wh), "--date", target,
         "--bet-side", "favorite"],
    )
    rc = mine_edges.main()
    assert rc == 0
    return json.loads((tmp_path / "localdata" / f"picks_{target}.json").read_text())


def test_mine_mines_unsettled_nonlive_row(tmp_path, monkeypatch):
    target = "2026-09-13"
    rows = [_settled_row(i) for i in range(5)]  # too few for any slice
    rows.append({"match_date": target, "tour": "ATP", "tournament": "US Open",
                 "_series": "Grand Slam", "player_a": "Today A",
                 "player_b": "Today B", "winner": "", "odds_a": 1.5,
                 "odds_b": 2.5, "rank_a": 5, "rank_b": 40,
                 "predicted_winner": "player_a", "prediction_prob": 0.7,
                 "_is_live": False, "_comment": ""})
    picks = _run_mine(tmp_path, monkeypatch, rows, target)
    assert len(picks) == 1
    assert picks[0]["match"] == "Today A vs Today B"
    assert picks[0]["bucket"] == "WATCHLIST"


def test_mine_logs_today_rows_matching_no_slice(tmp_path, monkeypatch, caplog):
    target = "2026-09-13"
    # 60 settled rows, one dominant slice (GOLD: 55/60 @1.5, N>50).
    rows = [_settled_row(i, won=(i % 12 != 0)) for i in range(60)]
    # Live today row whose every dim differs from history: matches nothing.
    rows.append({"match_date": target, "tour": "UTR", "tournament": "Madrid",
                 "_series": "ITF", "player_a": "Today A", "player_b": "Today B",
                 "winner": "", "odds_a": 2.5, "odds_b": 3.0, "rank_a": 150,
                 "rank_b": 160, "predicted_winner_market": "player_a",
                 "prediction_prob": 0.5, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    with caplog.at_level("WARNING", logger="edge_miner"):
        picks = _run_mine(tmp_path, monkeypatch, rows, target)
    assert picks == []
    assert "matched no exportable historical slice" in caplog.text


# ------------------------------------------------ forebet writer dedup --


def test_forebet_writer_dedups_fresh_and_stripped(tmp_path):
    from scripts import backfill_forebet

    base = {"match_date": "2026-09-12", "tour": "", "tournament": "",
            "player_a": "J. Reis Da Silva", "player_b": "A. Barrena",
            "predicted_winner": "player_b", "prediction_prob": 0.56,
            "source": "Forebet"}
    dup = dict(base, player_b="A. Barrena  ")  # stray whitespace
    other = dict(base, player_a="P. Kotov", player_b="M. Sharipov")
    backfill_forebet._write_predictions([base, dup, other], tmp_path)
    # Second identical write exercises the append path.
    backfill_forebet._write_predictions([base, dup, other], tmp_path)

    import pandas as pd

    df = pd.read_csv(tmp_path / "predictions_forebet_2026-09.csv.gz")
    assert len(df) == 2
    assert (df["player_b"] == "A. Barrena").sum() == 1
