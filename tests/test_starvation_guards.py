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
    # Also patch ml.LOCALDATA so audit registry doesn't leak from real localdata (Hard VETO n=41)
    from racketfactory import ml as ml_module
    monkeypatch.setattr(ml_module, "LOCALDATA", tmp_path / "localdata")
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
    # This test exercises the starvation/live-only fallback, not the EV gate:
    # pin the post-RED-DAY 1% floor so the fixture pick (1-2% calibrated EV
    # band) is not vetoed by the 2% default (prompt 2026-09-18).
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
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


def _no_slice_fixture_rows(target):
    """60 settled rows (one dominant GOLD slice) + a live row matching nothing.

    Mirrors run 35402614701: exportable slices exist, today's slate matches
    none of them, and the day must still be diagnosable/exportable.
    """
    rows = [_settled_row(i, won=(i % 12 != 0)) for i in range(60)]
    rows.append({"match_date": target, "tour": "UTR", "tournament": "Madrid",
                 "_series": "ITF", "player_a": "Today A", "player_b": "Today B",
                 "winner": "", "odds_a": 1.6, "odds_b": 2.4, "rank_a": 150,
                 "rank_b": 160, "predicted_winner_market": "player_a",
                 "prediction_prob": 0.66, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    return rows


def test_no_slice_diagnostics_dumped_on_empty_day(tmp_path, monkeypatch):
    # Run 35402614701: 81 candidates matched no exportable slice and the only
    # trace was one log line. With opt-in OFF (default) the picks file stays
    # empty — but the dropped rows must land in picks_unmatched_<date>.json
    # with dims + nearest slice + missing dims.
    monkeypatch.delenv("RACKET_FACTORY_EXPORT_NO_SLICE", raising=False)
    target = "2026-09-13"
    picks = _run_mine(tmp_path, monkeypatch, _no_slice_fixture_rows(target), target)
    assert picks == []
    dump = tmp_path / "localdata" / f"picks_unmatched_{target}.json"
    assert dump.exists()
    rows = json.loads(dump.read_text())
    assert len(rows) == 1
    row = rows[0]
    assert row["match"] == "Today A vs Today B"
    assert row["tour"] == "UTR"
    assert row["closest_slice"]
    assert row["missing_dims"], "nearest slice must say which dims failed"
    assert row["odds"] == pytest.approx(1.6)


def test_no_slice_diagnostics_stale_dump_removed_when_picks_exist(tmp_path, monkeypatch):
    # A later run on the same date that DOES produce picks must clear the
    # stale diagnostic dump (the picks .txt renders it).
    monkeypatch.delenv("RACKET_FACTORY_EXPORT_NO_SLICE", raising=False)
    target = "2026-09-13"
    (tmp_path / "localdata").mkdir(parents=True, exist_ok=True)
    dump = tmp_path / "localdata" / f"picks_unmatched_{target}.json"
    dump.write_text(json.dumps([{"match": "stale", "missing_dims": ["x"]}]))
    # Dominant slice that the today-row DOES match (ATP/Grand Slam @1.5).
    rows = [_settled_row(i, won=(i % 12 != 0)) for i in range(60)]
    rows.append({"match_date": target, "tour": "ATP", "tournament": "US Open",
                 "_series": "Grand Slam", "player_a": "Today A",
                 "player_b": "Today B", "winner": "", "odds_a": 1.5,
                 "odds_b": 2.5, "rank_a": 5, "rank_b": 40,
                 "predicted_winner": "player_a",
                 "predicted_winner_market": "player_a",
                 "predicted_winner_foretennis": "player_a",
                 "prediction_prob": 0.7, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    picks = _run_mine(tmp_path, monkeypatch, rows, target)
    assert len(picks) >= 1
    assert not dump.exists()


def test_no_slice_export_opt_in_exports_watchlist(tmp_path, monkeypatch, caplog):
    # Opt-in dial: RACKET_FACTORY_EXPORT_NO_SLICE=1 routes would-be dropped
    # candidates through the same watchlist exporter (EV gate applied).
    # Fires only on would-be empty days and must not write the diagnostic dump
    # (the rows are in the picks file instead).
    monkeypatch.setenv("RACKET_FACTORY_EXPORT_NO_SLICE", "1")
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
    target = "2026-09-13"
    with caplog.at_level("WARNING", logger="edge_miner"):
        picks = _run_mine(tmp_path, monkeypatch, _no_slice_fixture_rows(target), target)
    assert "No-slice export enabled" in caplog.text
    assert len(picks) == 1
    pick = picks[0]
    assert pick["match"] == "Today A vs Today B"
    assert pick["bucket"] == "WATCHLIST"
    assert pick["slice_matched"] == "no_slice_export"
    assert pick["expected_value"] == pytest.approx(0.056)  # 0.66*0.6 - 0.34
    assert not (tmp_path / "localdata" / f"picks_unmatched_{target}.json").exists()


def _low_ev_fallback_fixture(target):
    """20 settled rows (no actionable slice -> live-only fallback) + a today
    row with EV = 0.635*0.6 - 0.365 = +1.6% — inside the 1–2% band that
    separates the post-RED-DAY tuning from the +2% policy floor."""
    rows = [_settled_row(i) for i in range(20)]
    rows.append({"match_date": target, "tour": "UTR", "tournament": "Madrid",
                 "_series": "ITF", "player_a": "Today A", "player_b": "Today B",
                 "winner": "", "odds_a": 1.6, "odds_b": 2.4, "rank_a": 150,
                 "rank_b": 160, "predicted_winner": "player_a",
                 "prediction_prob": 0.635, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    return rows


def test_mine_default_min_ev_matches_ml_two_percent_policy(tmp_path, monkeypatch):
    # Gap closed (2026-09-19): mine_edges' export gate previously defaulted to
    # 0.0 while ml.py enforced the +2% AGENT_PROMPT policy. A +1.6% EV row must
    # now be SKIPPED_DEAD_EDGE by default (same floor as ml.get_min_ev_real).
    monkeypatch.delenv("RACKET_FACTORY_MIN_EV", raising=False)
    target = "2026-09-13"
    picks = _run_mine(tmp_path, monkeypatch, _low_ev_fallback_fixture(target), target)
    assert len(picks) == 1
    assert picks[0]["bucket"] == "SKIPPED_DEAD_EDGE"
    assert picks[0]["expected_value"] == pytest.approx(0.016)
    assert "0.016 < 0.020" in picks[0]["skip_reason"]


def test_mine_min_ev_env_var_restores_red_day_tuning(tmp_path, monkeypatch):
    # The same env var ml.py reads (RACKET_FACTORY_MIN_EV) now governs the
    # export gate too: 1% restores the post-RED-DAY tuning and the +1.6% row
    # exports as WATCHLIST.
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
    target = "2026-09-13"
    picks = _run_mine(tmp_path, monkeypatch, _low_ev_fallback_fixture(target), target)
    assert len(picks) == 1
    assert picks[0]["bucket"] == "WATCHLIST"
    assert picks[0]["expected_value"] == pytest.approx(0.016)


def test_mine_min_ev_cli_flag_beats_env_and_default(tmp_path, monkeypatch):
    # Explicit --min-ev still wins over env/default (diagnostics escape hatch).
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
    import pandas as pd

    monkeypatch.setattr(mine_edges, "ROOT", tmp_path)
    from racketfactory import ml as ml_module
    monkeypatch.setattr(ml_module, "LOCALDATA", tmp_path / "localdata")
    wh = tmp_path / "warehouse.csv.gz"
    target = "2026-09-13"
    pd.DataFrame(_low_ev_fallback_fixture(target)).to_csv(wh, index=False, compression="gzip")
    monkeypatch.setattr(
        mine_edges.sys, "argv",
        ["mine_edges", "--warehouse", str(wh), "--date", target,
         "--bet-side", "favorite", "--min-ev", "0.05"],
    )
    assert mine_edges.main() == 0
    picks = json.loads((tmp_path / "localdata" / f"picks_{target}.json").read_text())
    assert len(picks) == 1
    assert picks[0]["bucket"] == "SKIPPED_DEAD_EDGE"  # 0.016 < 0.05 explicit floor


def test_no_slice_export_opt_in_never_dilutes_matched_days(tmp_path, monkeypatch):
    # If the slice pass already exported a pick, the opt-in must NOT also
    # export the unmatched rows (no gate dilution on days that have picks).
    monkeypatch.setenv("RACKET_FACTORY_EXPORT_NO_SLICE", "1")
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
    target = "2026-09-13"
    rows = [_settled_row(i, won=(i % 12 != 0)) for i in range(60)]
    # One row that matches the dominant slice...
    rows.append({"match_date": target, "tour": "ATP", "tournament": "US Open",
                 "_series": "Grand Slam", "player_a": "Match A",
                 "player_b": "Match B", "winner": "", "odds_a": 1.5,
                 "odds_b": 2.5, "rank_a": 5, "rank_b": 40,
                 "predicted_winner": "player_a",
                 "predicted_winner_market": "player_a",
                 "predicted_winner_foretennis": "player_a",
                 "prediction_prob": 0.7, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    # ...and one that matches nothing.
    rows.append({"match_date": target, "tour": "UTR", "tournament": "Madrid",
                 "_series": "ITF", "player_a": "Today A", "player_b": "Today B",
                 "winner": "", "odds_a": 1.6, "odds_b": 2.4, "rank_a": 150,
                 "rank_b": 160, "predicted_winner_market": "player_a",
                 "prediction_prob": 0.66, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    picks = _run_mine(tmp_path, monkeypatch, rows, target)
    matches = [p for p in picks if p.get("slice_matched") == "no_slice_export"]
    assert matches == [], "opt-in must not fire on days that have slice-matched picks"
    assert any(p["match"] == "Match A vs Match B" for p in picks)


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


# ------------------------------------------------ run #206 repairs --


JINA_DDMM_MD = """\
Tennis predictions for yesterday
ATP US Open - Semi-finals
[F. Tiafoe B. Shelton 12/09/2026 01:45](https://www.forebet.com/en/tennis/matches/atp-singles/us-open/12345/)
41 59
2 1-3
"""


def test_jina_expected_day_disambiguates_ddmm():
    # predictions-yesterday rendered DD/MM; without the page day this token
    # misfiles as Dec 9 (run #206: 28 rows under predictions_forebet_2026-12).
    rows = ForebetPredictor().parse_jina_markdown(JINA_DDMM_MD, "2026-09-12")
    assert len(rows) == 1
    assert rows[0]["match_date"] == "2026-09-12"


def test_jina_expected_day_keeps_mmdd():
    rows = ForebetPredictor().parse_jina_markdown(JINA_SEPT_MD, "2026-09-11")
    by_home = {r["player_home"]: r for r in rows}
    assert by_home["F. Tiafoe"]["match_date"] == "2026-09-11"
    # 09/12 offers Sep 12 / Dec 9; expected Sep 11 matches neither, so the
    # MM/DD-first default applies.
    assert by_home["J. Reis Da Silva"]["match_date"] == "2026-09-12"


def test_jina_no_expected_day_keeps_mmdd_first_default():
    rows = ForebetPredictor().parse_jina_markdown(JINA_DDMM_MD)
    assert rows[0]["match_date"] == "2026-12-09"


def test_jina_unexpected_date_falls_back_to_mmdd():
    rows = ForebetPredictor().parse_jina_markdown(JINA_SEPT_MD, "2026-09-12")
    by_home = {r["player_home"]: r for r in rows}
    assert by_home["F. Tiafoe"]["match_date"] == "2026-09-11"
    assert by_home["J. Reis Da Silva"]["match_date"] == "2026-09-12"


JINA_LOOSE_MD = """\
Tennis predictions for tomorrow
ATP Test Open - Final
[J. Smith A. Jones 9/14/2026 6:15 AM](https://www.forebet.com/en/tennis/matches/atp-singles/test-open/2/)
50 50
1 2-0
[C. Day D. Foe 14/09/2026](https://www.forebet.com/en/tennis/matches/atp-singles/test-open/3/)
55 45
1 2-0
[A. Player B. Opp Tomorrow](https://www.forebet.com/en/tennis/matches/wta-singles/test-open/4/)
60 40
1 2-1
"""


def test_jina_relaxed_time_and_dateless_links():
    rows = ForebetPredictor().parse_jina_markdown(JINA_LOOSE_MD)
    assert len(rows) == 3
    by_home = {r["player_home"]: r for r in rows}
    assert by_home["J. Smith"]["match_date"] == "2026-09-14"
    assert by_home["J. Smith"]["match_time"] == "06:15"
    assert by_home["C. Day"]["match_date"] == "2026-09-14"
    # Dateless link: no date, but the trailing day-word must not pollute names.
    assert by_home["A. Player"]["match_date"] is None
    assert by_home["A. Player"]["player_away"] == "B. Opp"


def test_expected_iso_for_day():
    from datetime import date, timedelta
    from racketfactory.sources.forebet import _expected_iso_for_day

    today = date.today()
    assert _expected_iso_for_day("today") == today.isoformat()
    assert _expected_iso_for_day("yesterday") == (today - timedelta(days=1)).isoformat()
    assert _expected_iso_for_day("tomorrow") == (today + timedelta(days=1)).isoformat()
    assert _expected_iso_for_day("2026-09-12") == "2026-09-12"
    assert _expected_iso_for_day("bogus") is None
    assert _expected_iso_for_day("") is None


def test_fetch_daily_backfills_page_day_for_dateless_rows(monkeypatch):
    from datetime import date, timedelta

    exp = (date.today() + timedelta(days=1)).isoformat()
    md = """\
Tennis predictions for tomorrow
ATP Test Open - Final
[A. Player B. Opp](https://www.forebet.com/en/tennis/matches/atp-singles/test-open/4/)
60 40
1 2-1
[C. Dated D. Foe 09/14/2026 18:00](https://www.forebet.com/en/tennis/matches/atp-singles/test-open/5/)
55 45
1 2-0
"""
    monkeypatch.setattr(ForebetPredictor, "_fetch_daily_page",
                        lambda self, day="today": md)
    rows = ForebetPredictor().fetch_daily_predictions("tomorrow")
    assert len(rows) == 2
    by_home = {r["player_home"]: r for r in rows}
    assert by_home["A. Player"]["match_date"] == exp
    assert by_home["C. Dated"]["match_date"] == "2026-09-14"


def test_actionable_slices_gate_mirrors_run_206():
    results = [
        {"Slice": "tour:UNKNOWN | x | y", "Verdict": "NO STAT SIG", "Exportable": False},
        {"Slice": "tour:UNKNOWN | x | z", "Verdict": "WATCHLIST", "Exportable": False},
        {"Slice": "tour:ATP | a | b", "Verdict": "EDGE CONFIRMED", "Exportable": True},
        {"Slice": "tour:ATP | a | c", "Verdict": "FADE THIS SIGNAL", "Exportable": False},
        {"Slice": "tour:ATP | a | d", "Verdict": "WATCHLIST", "Exportable": True},
    ]
    got = mine_edges._actionable_slices(results)
    assert [r["Slice"] for r in got] == ["tour:ATP | a | b", "tour:ATP | a | d"]
    assert mine_edges._actionable_slices([]) == []
    assert mine_edges._actionable_slices(None) == []


def test_placeholder_dim_values_exclude_unknown_variants():
    import pandas as pd

    assert mine_edges._is_placeholder_dim_value("Unknown")
    assert mine_edges._is_placeholder_dim_value("UNKNOWN")
    assert mine_edges._is_placeholder_dim_value("")
    assert mine_edges._is_placeholder_dim_value(None)
    assert mine_edges._is_placeholder_dim_value(float("nan"))
    assert mine_edges._is_placeholder_dim_value(pd.NA)
    assert not mine_edges._is_placeholder_dim_value("ATP")
    assert not mine_edges._is_placeholder_dim_value("Challenger")


def test_mine_unactionable_slices_still_exports_live_only(tmp_path, monkeypatch, caplog):
    # Starvation-fallback test: pin the 1% EV floor (see sibling test).
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
    target = "2026-09-13"
    # 20 settled rows: slices mine at N=20 (min-n 15) but stay unexportable
    # (min-export-n 50) however the verdict lands. Mirrors run #206, where 5
    # unexportable slices yielded 0 picks despite 26 today candidates.
    rows = [_settled_row(i) for i in range(20)]
    rows.append({"match_date": target, "tour": "UTR", "tournament": "Madrid",
                 "_series": "ITF", "player_a": "Today A", "player_b": "Today B",
                 "winner": "", "odds_a": 1.5, "odds_b": 2.5, "rank_a": 5,
                 "rank_b": 40, "predicted_winner": "player_a",
                 "prediction_prob": 0.7, "_is_live": True,
                 "_comment": "live_upcoming_injected",
                 "_odds_source": "TheOddsAPI"})
    with caplog.at_level("WARNING", logger="edge_miner"):
        picks = _run_mine(tmp_path, monkeypatch, rows, target)
    assert "none are actionable" in caplog.text  # proves slices were mined
    assert "matched no exportable historical slice" not in caplog.text
    assert len(picks) == 1
    assert picks[0]["match"] == "Today A vs Today B"
    assert picks[0]["bucket"] == "WATCHLIST"
    assert picks[0]["slice_matched"] == "live_only_fallback"


def test_drop_future_garbage_month_files(tmp_path):
    from datetime import date
    from scripts import backfill_forebet

    header = "match_date,tour"
    _write_gz(tmp_path / "predictions_forebet_2026-12.csv.gz", header,
              ["2026-12-09,ATP"])  # rule 1: month >1 ahead
    _write_gz(tmp_path / "forebet_results_tennis_2026-11.csv.gz", header,
              ["2026-11-05,WTA"])  # rule 1
    _write_gz(tmp_path / "predictions_forebet_2026-10.csv.gz", header,
              ["2026-10-30,ATP"])  # rule 2: all rows >14d future
    _write_gz(tmp_path / "forebet_results_tennis_2026-10.csv.gz", header,
              ["2026-10-30,ATP", "2026-09-13,ATP"])  # mixed: kept
    _write_gz(tmp_path / "predictions_forebet_2026-09.csv.gz", header,
              ["2026-09-12,ATP"])  # current: kept
    _write_gz(tmp_path / "predictions_forebet_2026-08.csv.gz", header,
              ["2026-08-20,ATP"])  # history: kept
    (tmp_path / "warehouse.csv.gz").write_bytes(b"untouched")

    dropped = backfill_forebet._drop_future_garbage_month_files(
        tmp_path, date(2026, 9, 13))
    assert set(dropped) == {"predictions_forebet_2026-12.csv.gz",
                            "forebet_results_tennis_2026-11.csv.gz",
                            "predictions_forebet_2026-10.csv.gz"}
    assert not (tmp_path / "predictions_forebet_2026-12.csv.gz").exists()
    assert not (tmp_path / "forebet_results_tennis_2026-11.csv.gz").exists()
    assert not (tmp_path / "predictions_forebet_2026-10.csv.gz").exists()
    assert (tmp_path / "forebet_results_tennis_2026-10.csv.gz").exists()
    assert (tmp_path / "predictions_forebet_2026-09.csv.gz").exists()
    assert (tmp_path / "predictions_forebet_2026-08.csv.gz").exists()
    assert (tmp_path / "warehouse.csv.gz").exists()


def test_mode_daily_counts_dateless_rows(tmp_path, monkeypatch, caplog):
    import pandas as pd
    from types import SimpleNamespace
    from scripts import backfill_forebet

    wh = tmp_path / "warehouse.csv.gz"
    pd.DataFrame([{"match_date": "2026-09-13", "tour": "ATP",
                   "tournament": "US Open", "player_a": "N. Djokovic",
                   "player_b": "C. Alcaraz"}]).to_csv(wh, index=False,
                                                       compression="gzip")

    def pred(home, away, dt, winner="1"):
        return {"match_date": dt, "match_time": "", "player_home": home,
                "player_away": away, "prob_home": 70, "prob_away": 30,
                "odds_home": 1.5, "odds_away": 2.5, "predicted_winner": winner,
                "tournament": "US Open", "result_status": None,
                "result_score": None, "result_winner": None,
                "result_winner_name": None, "result_sets_home": None,
                "result_sets_away": None}

    canned = [pred("Novak Djokovic", "Carlos Alcaraz", "2026-09-13"),
              pred("Ghost A", "Ghost B", None),
              pred("Unknown One", "Unknown Two", "2026-09-13", winner="2")]
    monkeypatch.setattr(backfill_forebet, "cached_fetch",
                        lambda key, thunk: canned)
    args = SimpleNamespace(days=["today"], warehouse=str(wh),
                           output_dir=str(tmp_path / "out"), delay=0)
    with caplog.at_level("INFO", logger="backfill_forebet"):
        assert backfill_forebet.mode_daily(args) == 0
    assert ("1 matched to warehouse, 1 stored as new upcoming matches, "
            "1 skipped (no date)") in caplog.text


# ------------------------------------------------ forebet Jina ground truth --


def test_jina_fused_names_split():
    from racketfactory.sources.forebet import _split_jina_players

    # Jina fuses home/away without spaces; players carry multiple initials.
    assert _split_jina_players("A. ZverevB. Shelton") == ("A. Zverev", "B. Shelton")
    assert _split_jina_players("J. D. Hara FriendM. Basing") == ("J. D. Hara Friend", "M. Basing")
    assert _split_jina_players("L. S. SteurJ. N. Torner Sensano") == ("L. S. Steur", "J. N. Torner Sensano")
    assert _split_jina_players("G. Garcia-PerezA. Arseneault") == ("G. Garcia-Perez", "A. Arseneault")
    assert _split_jina_players("D. SpiteriM. E. Reasco Gonzalez") == ("D. Spiteri", "M. E. Reasco Gonzalez")
    # Spaced links keep working (regression).
    assert _split_jina_players("F. Tiafoe B. Shelton") == ("F. Tiafoe", "B. Shelton")
    assert _split_jina_players("") == ("", "")


JINA_GROUND_TRUTH_MD = """\
Tennis predictions for Today
ATP US Open - Final
[A. ZverevB. Shelton13/09/2026 20:00](https://www.forebet.com/en/tennis/matches/atp-singles/us-open/358444)
![Image 4](https://www.forebet.com/images/fc/us.png)
61 39
1 3-1
**3**
1
10.4
-152
WTA Guadalajara - 1/16-finals
[A. ParksM. Sherif13/09/2026 20:00](https://www.forebet.com/en/tennis/matches/wta-singles/guadalajara/a-parks-m-sherif/358449)
![Image 5](https://www.forebet.com/images/fc/mx.png)
35 65
2 0-2
0
**2**
9.3
+220
[P. UdvardyL. Boisson13/09/2026 22:30](https://www.forebet.com/en/tennis/matches/wta-singles/guadalajara/x/358451)
46 54
2 0-2
0
**2**
9.2
-149
[O. SterniczukI. Gakhov13/09/2026 13:30](https://www.forebet.com/en/tennis/matches/challenger-men/szczecin/x/358475)
27 73
2 0-2
0
**2**
8.3
-5000
[I. IvashkaM. Sharipov13/09/2026 10:00](https://www.forebet.com/en/tennis/matches/challenger-men/shanghai/x/358455)
58 42
1 2-0
**2**
0
11.2
-
"""


def _ground_truth_rows():
    return {r["player_home"]: r
            for r in ForebetPredictor().parse_jina_markdown(JINA_GROUND_TRUTH_MD, "2026-09-13")}


def test_jina_ground_truth_names_probs_pred():
    by_home = _ground_truth_rows()
    assert len(by_home) == 5
    assert by_home["A. Zverev"]["player_away"] == "B. Shelton"
    assert by_home["A. Zverev"]["match_date"] == "2026-09-13"
    assert (by_home["A. Zverev"]["prob_home"], by_home["A. Zverev"]["prob_away"]) == (61, 39)
    assert by_home["A. Zverev"]["predicted_winner"] == "1"
    assert by_home["A. Parks"]["predicted_winner"] == "2"


def test_jina_positional_coef_one_sided():
    by_home = _ground_truth_rows()
    # American coef attributed to the matching side; avg-games never a price.
    assert by_home["A. Zverev"]["odds_home"] == pytest.approx(1.6579, abs=1e-4)
    assert by_home["A. Zverev"]["odds_away"] is None
    assert by_home["A. Parks"]["odds_home"] == pytest.approx(3.2)
    assert by_home["A. Parks"]["odds_away"] is None
    # Closest-side attribution (away here).
    assert by_home["P. Udvardy"]["odds_home"] is None
    assert by_home["P. Udvardy"]["odds_away"] == pytest.approx(1.6711, abs=1e-4)


def test_jina_coef_rejects_avg_games_pollution():
    rows = ForebetPredictor().parse_jina_markdown(
        "Tennis predictions for Today\n"
        "[I. SimakinM. Purcell12/09/2026 10:00](https://www.forebet.com/en/tennis/matches/x/y/1/)\n"
        "54 46\n1 2-0\n**2**\n0\n10.4\n+110\n", "2026-09-12")
    assert len(rows) == 1
    # Old code stored [10.4, 2.10]; avg-games must never be a price again.
    assert rows[0]["odds_home"] is None
    assert rows[0]["odds_away"] == pytest.approx(2.10)


def test_jina_coef_stale_and_missing_discarded():
    by_home = _ground_truth_rows()
    # -5000 (1.02) on a 27/73 match matches neither side: stale garbage.
    assert by_home["O. Sterniczuk"]["odds_home"] is None
    assert by_home["O. Sterniczuk"]["odds_away"] is None
    # "-" means no market; the avg line (11.2) must not become a price.
    assert by_home["I. Ivashka"]["odds_home"] is None
    assert by_home["I. Ivashka"]["odds_away"] is None


def test_jina_decimal_coef_still_parses():
    rows = ForebetPredictor().parse_jina_markdown(
        "Tennis predictions for Today\n"
        "[A. ZverevB. Shelton13/09/2026 20:00](https://www.forebet.com/en/tennis/matches/x/y/1/)\n"
        "61 39\n1 3-1\n**3**\n1\n10.4\n1.66\n", "2026-09-13")
    assert rows[0]["odds_home"] == pytest.approx(1.66)
    assert rows[0]["odds_away"] is None


def test_fetch_via_relay_sends_slumdog_headers_and_rejects_stubs(monkeypatch, caplog):
    import urllib.error
    import urllib.request

    calls = []
    stub = "Tennis predictions for Today\n" + "nav " * 800  # >2000 chars, no match links
    full = ("<html><head><title>Tennis predictions for Today | Forebet</title></head><body>"
            '<a class="tnmscn" href="/en/tennis/matches/atp-singles/us-open/x/">x</a>'
            + "rows " * 800 + "</body></html>")
    md_full = ("Title: Tennis predictions\nURL Source: https://www.forebet.com/en/tennis/x\n"
               "Markdown Content:\n[Zverev](https://www.forebet.com/en/tennis/matches/x/)\n"
               + "rows " * 800)

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._body

    def sent_headers(request):
        return {k.lower(): v for k, v in request.header_items()}

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return FakeResponse(full.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    got = ForebetPredictor()._fetch_via_relay(
        "https://www.forebet.com/en/tennis/predictions/2026-09-13")
    assert got is not None and "/tennis/matches/" in got
    # Slumdog header set on the relay request — including the exact UAs
    # Slumdog's capture receipts prove work from GitHub Actions (its own
    # "RacketFactory/1.0" UA was CF-challenged 2026-09-18).
    assert len(calls) == 1
    assert calls[0].full_url.startswith("https://r.jina.ai/https://www.forebet.com/")
    sent = sent_headers(calls[0])
    assert sent.get("x-no-cache") == "true"
    assert sent.get("x-return-format") == "html"
    assert sent.get("user-agent") == "Slumdog"

    # Stub in both flavors -> None after exactly one reader-mode retry (no
    # ?fb= cache-buster: X-No-Cache replaces that workaround), and the
    # discard logs the snapshot head for forensics.
    calls.clear()

    def fake_stub_urlopen(request, timeout=None):
        calls.append(request)
        return FakeResponse(stub.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_stub_urlopen)
    with caplog.at_level("WARNING", logger="racketfactory.sources.forebet"):
        assert ForebetPredictor()._fetch_via_relay("https://www.forebet.com/en/tennis/x") is None
    assert len(calls) == 2
    assert sent_headers(calls[0]).get("x-return-format") == "html"
    assert "x-return-format" not in sent_headers(calls[1])
    assert all("?fb=" not in c.full_url for c in calls)
    assert "head=" in caplog.text

    # Html stub + markdown board -> the markdown body is returned.
    calls.clear()

    def fake_mode_urlopen(request, timeout=None):
        calls.append(request)
        body = stub if sent_headers(request).get("x-return-format") == "html" else md_full
        return FakeResponse(body.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_mode_urlopen)
    got = ForebetPredictor()._fetch_via_relay("https://www.forebet.com/en/tennis/x")
    assert got is not None and "Markdown Content:" in got
    assert len(calls) == 2
    # Reader-mode retry uses Slumdog's EdgeFactory-validated UA, no html format.
    assert sent_headers(calls[1]).get("user-agent") == "EdgeFactory/1.0"

    # Relay 403 -> None with a single attempt: transport failures get no
    # reader-mode retry.
    calls.clear()

    def fake_403_urlopen(request, timeout=None):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_403_urlopen)
    assert ForebetPredictor()._fetch_via_relay("https://www.forebet.com/en/tennis/x") is None
    assert len(calls) == 1


def test_fetch_via_relay_skips_reader_retry_on_forebet_404(monkeypatch):
    import urllib.request

    calls = []
    not_found = ("<html><head><title>404 - Error: 404</title></head><body>"
                 "<h1>Forebet 404 Error</h1>Not what you were looking for?</body></html>")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return not_found.encode()

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert ForebetPredictor()._fetch_via_relay("https://www.forebet.com/en/tennis/x") is None
    assert len(calls) == 1


# Run 35311356382: every deep tournament page came back as a ~7.9KB
# Cloudflare "Just a moment..." shell. The shell echoes the requested URL
# (which contains /tennis/), so it PASSED the sport-label check and the
# parsers silently returned 0 predictions for all 50 pages.


def _cf_shell(url: str) -> str:
    """Challenge stub shaped like the 7.9KB shells from run 35311356382:
    contains the requested URL (=> 'tennis' present) but no board content."""
    return (
        '<html lang="en-US"><head><title>Just a moment...</title>'
        "<meta http-equiv='Content-Type' content='text/html; charset=UTF-8'>"
        '<meta name="robots" content="noindex,nofollow"></head><body>'
        f'<div id="challenge-running"><a href="{url}">Verifying...</a></div>'
        + "cf padding " * 400
        + "</body></html>"
    )


def test_relay_challenge_shell_discarded_and_fails_fast(monkeypatch, caplog):
    import urllib.request

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=None):
        return FakeResponse(_cf_shell(request.full_url.replace("https://r.jina.ai/", "")).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    p = ForebetPredictor()
    with caplog.at_level("WARNING", logger="racketfactory.sources.forebet"):
        # expect_matches=False is the deep tournament path (looser check).
        for _ in range(3):
            assert p._fetch_via_relay("https://www.forebet.com/en/tennis/atp-singles/bastad",
                                      expect_matches=False) is None
    assert p.relay_blocked is True
    assert "Cloudflare challenge page" in caplog.text
    assert "failing fast" in caplog.text

    # Once latched, the deep fetch short-circuits WITHOUT another relay call.
    fetched = []
    monkeypatch.setattr(p, "_fetch_tournament_page",
                        lambda *a, **k: fetched.append(1) or "body")
    assert p.fetch_tournament_predictions("ATP", "Bastad") == []
    assert fetched == []


def test_relay_challenge_counter_resets_on_real_board(monkeypatch):
    import urllib.request

    board = ("<html><head><title>Tennis predictions | Forebet</title></head><body>"
             "atp-singles bastad board" + "rows " * 800 + "</body></html>")

    # Per-URL bodies: both relay flavors (html + reader retry) for a given
    # URL return the same body, so each challenged URL records 2 challenge
    # snapshots and the streak of 3 latches mid-URL-2.
    bodies = {
        "u1": _cf_shell("https://www.forebet.com/en/tennis/atp-singles/t1"),
        "u2": _cf_shell("https://www.forebet.com/en/tennis/atp-singles/t2"),
        "u3": board,  # relay recovers mid-run
        "u4": _cf_shell("https://www.forebet.com/en/tennis/atp-singles/t3"),
        "u5": _cf_shell("https://www.forebet.com/en/tennis/atp-singles/t4"),
    }

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=None):
        # request.full_url is the relay-wrapped URL: RELAY_BASE + url
        url = request.full_url.rsplit("/", 1)[-1]
        return FakeResponse(bodies[url].encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    p = ForebetPredictor()
    assert p._fetch_via_relay("u1", expect_matches=False) is None
    assert p.relay_blocked is False  # streak of 2: below 3
    assert p._fetch_via_relay("u2", expect_matches=False) is None
    assert p.relay_blocked is True   # streak hits 3 mid-URL
    got = p._fetch_via_relay("u3", expect_matches=False)
    assert got is not None           # real board accepted
    assert p.relay_blocked is False  # latch released
    assert p._challenge_hits == 0    # streak reset by the good board
    assert p._fetch_via_relay("u4", expect_matches=False) is None
    assert p.relay_blocked is False  # fresh streak of 2: no latch
    assert p._fetch_via_relay("u5", expect_matches=False) is None
    assert p.relay_blocked is True   # new streak reaches 3


def test_relay_forebet_404_streak_fails_fast(monkeypatch, caplog):
    """Run 35399503550: with the Jina key the relay reached Forebet, so dead
    warehouse slugs surface as real 404 content pages (15/15 dead). Each one
    burns a relay call for a guaranteed empty board, so fail fast after 3."""
    import urllib.request

    not_found = ("<html><head><title>404 - Error: 404</title></head><body>"
                 "<h1>Forebet 404 Error</h1>Not what you were looking for?"
                 "</body></html>")

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return not_found.encode()

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    p = ForebetPredictor()
    with caplog.at_level("WARNING", logger="racketfactory.sources.forebet"):
        # A real 404 is authoritative (no reader retry), so 1 call per URL.
        for i in range(3):
            assert p._fetch_via_relay(
                f"https://www.forebet.com/en/tennis/atp-singles/dead-{i}",
                expect_matches=False) is None
    assert len(calls) == 3
    assert p.notfound_blocked is True
    assert "failing fast" in caplog.text

    # Once latched, the deep fetch short-circuits WITHOUT another relay call.
    calls.clear()
    assert p.fetch_tournament_predictions("ATP", "Bastad") == []
    assert calls == []


def test_relay_forebet_404_counter_resets_on_real_board(monkeypatch):
    """Streak of 404s is broken by a real board (a live tournament page),
    so a single dead slug among live ones never latches the run."""
    import urllib.request

    not_found = ("<html><head><title>404 - Error: 404</title></head><body>"
                 "<h1>Forebet 404 Error</h1>Not what you were looking for?"
                 "</body></html>")
    board = ("<html><head><title>Tennis predictions | Forebet</title></head>"
             "<body>atp-singles live board" + "rows " * 800 + "</body></html>")

    bodies = {
        "u1": not_found,
        "u2": not_found,
        "u3": board,          # live page mid-run resets the streak
        "u4": not_found,
        "u5": not_found,      # fresh streak of 2: still below 3
        "u6": not_found,      # new streak reaches 3
    }

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=None):
        url = request.full_url.rsplit("/", 1)[-1]
        return FakeResponse(bodies[url].encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    p = ForebetPredictor()
    assert p._fetch_via_relay("u1", expect_matches=False) is None
    assert p.notfound_blocked is False
    assert p._fetch_via_relay("u2", expect_matches=False) is None
    assert p.notfound_blocked is False  # streak of 2: below 3
    got = p._fetch_via_relay("u3", expect_matches=False)
    assert got is not None
    assert p.notfound_blocked is False  # latch released
    assert p._notfound_hits == 0        # streak reset by the good board
    assert p._fetch_via_relay("u4", expect_matches=False) is None
    assert p.notfound_blocked is False  # fresh streak of 1: no latch
    assert p._fetch_via_relay("u5", expect_matches=False) is None
    assert p.notfound_blocked is False  # fresh streak of 2: below 3
    assert p._fetch_via_relay("u6", expect_matches=False) is None
    assert p.notfound_blocked is True   # new streak reaches 3


def test_relay_empty_board_still_accepted(monkeypatch):
    """Regression: with expect_matches=False a legitimate board WITHOUT match
    links (empty tournament) must still pass — only challenge shells are new
    rejects."""
    import urllib.request

    empty_board = ("<html><head><title>Tennis predictions | Forebet</title></head>"
                   "<body>atp-singles bastad, no matches today" + "nav " * 800
                   + "</body></html>")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return empty_board.encode()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=None: FakeResponse())
    assert ForebetPredictor()._fetch_via_relay(
        "https://www.forebet.com/en/tennis/atp-singles/bastad",
        expect_matches=False) is not None


def test_relay_status_marker_roundtrip(tmp_path):
    from racketfactory.sources.forebet import read_relay_status, record_relay_status

    day = "2026-09-18"
    assert read_relay_status(day, tmp_path) is None  # absent -> None
    record_relay_status("challenged", day, tmp_path)
    assert read_relay_status(day, tmp_path) == "challenged"
    # A different day's marker must not leak into today's gate.
    assert read_relay_status("2026-09-19", tmp_path) is None
    # Corrupt marker fails soft to None (never raises into the pipeline).
    bad = tmp_path / "fetch_cache" / f"forebet_relay_status_{day}.json"
    bad.write_text("not json")
    assert read_relay_status(day, tmp_path) is None


def test_tournament_mode_skips_when_relay_challenged(tmp_path, monkeypatch):
    from scripts import backfill_forebet

    class Args:
        output_dir = str(tmp_path)
        warehouse = str(tmp_path / "does-not-exist.csv.gz")
        limit = 50
        delay = 2

    backfill_forebet.record_relay_status(
        "challenged", _today_iso(), str(tmp_path))
    # Gate fires BEFORE the warehouse load, so a missing warehouse file is
    # fine — a non-gated run would have returned 1 with a load error.
    assert backfill_forebet.mode_tournament(Args()) == 0
    # No marker -> gate open (the per-run latch is the remaining guard).
    assert backfill_forebet.read_relay_status(_today_iso(), str(tmp_path / "else")) is None


def _stub_tournament_fetch(monkeypatch, captured: list, boards: dict):
    """Replace ForebetPredictor.fetch_tournament_predictions inside the
    backfill script; record the (tour_slug, tournament_slug) requested and
    return canned raw predictions from ``boards`` keyed by the slug pair."""
    from scripts import backfill_forebet
    real = backfill_forebet.ForebetPredictor

    class FakePredictor(real):
        def fetch_tournament_predictions(self, tour, tournament,
                                         tour_slug=None, tournament_slug=None):
            captured.append((tour_slug, tournament_slug))
            return boards.get((tour_slug, tournament_slug), [])

    monkeypatch.setattr(backfill_forebet, "ForebetPredictor", FakePredictor)


def _warehouse_with(tmp_path, rows) -> str:
    import pandas as pd
    wh = pd.DataFrame(rows)
    path = tmp_path / "warehouse.csv.gz"
    wh.to_csv(path, index=False, compression="gzip")
    return str(path)


def _today_row(match_date, tour, tournament, a, b):
    return {"match_date": match_date, "tour": tour, "tournament": tournament,
            "player_a": a, "player_b": b}


def test_mode_tournament_prefers_live_slugs_ranked_by_count(tmp_path, monkeypatch):
    """Run 35399503550 fix: pages are fetched by the live slugs observed in
    daily-board match links (challenger-men/rennes), NOT the name-derived
    slugs ('Challenger Rennes Prediction' -> challenger-rennes-prediction,
    which is a 404), and the limit keeps the busiest tournaments."""
    import pandas as pd
    from datetime import date
    from scripts import backfill_forebet

    today = date.today().isoformat()
    wh_path = _warehouse_with(tmp_path, [
        _today_row(today, "CHALLENGER", "Challenger Rennes Prediction", "A. Player", "B. Opp"),
        _today_row(today, "CHALLENGER", "Challenger Rennes Prediction", "C. Day", "D. Foe"),
        _today_row(today, "WTA", "WTA Guadalajara Prediction", "E. Vega", "F. Cruz"),
    ])
    # Daily-board capture carrying the live slugs from the match links.
    month = date.today().strftime("%Y-%m")
    preds = pd.DataFrame([
        {"match_date": today, "tour": "CHALLENGER", "tournament": "Challenger Rennes Prediction",
         "player_a": "A. Player", "player_b": "B. Opp", "predicted_winner": "player_a",
         "prediction_prob": 0.64, "odds_a": 1.55, "odds_b": 2.4, "source": "Forebet",
         "tour_slug": "challenger-men", "tournament_slug": "rennes"},
        {"match_date": today, "tour": "CHALLENGER", "tournament": "Challenger Rennes Prediction",
         "player_a": "C. Day", "player_b": "D. Foe", "predicted_winner": "player_b",
         "prediction_prob": 0.57, "odds_a": 1.8, "odds_b": 1.95, "source": "Forebet",
         "tour_slug": "challenger-men", "tournament_slug": "rennes"},
        {"match_date": today, "tour": "WTA", "tournament": "WTA Guadalajara Prediction",
         "player_a": "E. Vega", "player_b": "F. Cruz", "predicted_winner": "player_a",
         "prediction_prob": 0.61, "odds_a": 1.6, "odds_b": 2.3, "source": "Forebet",
         "tour_slug": "wta-singles", "tournament_slug": "guadalajara"},
    ])
    preds.to_csv(tmp_path / f"predictions_forebet_{month}.csv.gz", index=False, compression="gzip")

    boards = {
        ("challenger-men", "rennes"): [
            {"match_date": today, "player_home": "A. Player", "player_away": "B. Opp",
             "predicted_winner": "1", "prob_home": 64, "prob_away": 36,
             "odds_home": 1.55, "odds_away": 2.4, "tournament": "Rennes"},
            {"match_date": today, "player_home": "C. Day", "player_away": "D. Foe",
             "predicted_winner": "2", "prob_home": 43, "prob_away": 57,
             "odds_home": 1.8, "odds_away": 1.95, "tournament": "Rennes"},
        ],
        ("wta-singles", "guadalajara"): [
            {"match_date": today, "player_home": "E. Vega", "player_away": "F. Cruz",
             "predicted_winner": "1", "prob_home": 61, "prob_away": 39,
             "odds_home": 1.6, "odds_away": 2.3, "tournament": "Guadalajara"},
        ],
    }
    captured: list = []
    _stub_tournament_fetch(monkeypatch, captured, boards)

    class Args:
        output_dir = str(tmp_path)
        warehouse = wh_path
        limit = 1
        delay = 0

    assert backfill_forebet.mode_tournament(Args()) == 0
    # Live slug of the busiest tournament — NOT the name-derived slugs, and
    # NOT the name-derived slugs of the smaller one (limit keeps the top).
    assert captured == [("challenger-men", "rennes")]

    out = pd.read_csv(tmp_path / f"predictions_forebet_{month}.csv.gz")
    rennes = out[out["player_a"].astype(str).str.contains("Player|Day")]
    assert len(rennes) == 2
    assert rennes["predicted_winner"].notna().all()


def test_mode_tournament_falls_back_to_name_slugs_without_slug_data(tmp_path, monkeypatch):
    """Cold start: monthly captures predate slug persistence (no tour_slug
    column) — the legacy name-derived path must still run, not crash."""
    from datetime import date
    from scripts import backfill_forebet

    today = date.today().isoformat()
    wh_path = _warehouse_with(tmp_path, [
        _today_row(today, "ATP", "Bastad", "A. Player", "B. Opp"),
    ])
    import pandas as pd
    month = date.today().strftime("%Y-%m")
    pd.DataFrame([
        {"match_date": today, "tour": "ATP", "tournament": "Bastad",
         "player_a": "A. Player", "player_b": "B. Opp", "predicted_winner": "player_a",
         "prediction_prob": 0.6, "odds_a": 1.5, "odds_b": 2.5, "source": "Forebet"},
    ]).to_csv(tmp_path / f"predictions_forebet_{month}.csv.gz", index=False, compression="gzip")

    captured: list = []
    _stub_tournament_fetch(monkeypatch, captured, {})

    class Args:
        output_dir = str(tmp_path)
        warehouse = wh_path
        limit = 5
        delay = 0

    assert backfill_forebet.mode_tournament(Args()) == 0
    assert captured == [("atp-singles", "bastad")]  # legacy name-derived slugs


def test_mode_tournament_skips_unresolvable_slug_groups(tmp_path, monkeypatch):
    """Rows with empty tour/tournament names build empty slugs — they must
    be skipped (BASE_URL// is not a page), not fetched, and not crash the
    run (the old groupby dropped NaN keys implicitly; the "" fallbacks
    do not, so the filter is explicit now)."""
    from datetime import date
    from scripts import backfill_forebet

    today = date.today().isoformat()
    wh_path = _warehouse_with(tmp_path, [
        _today_row(today, "", "", "A. One", "B. Two"),
        _today_row(today, "ATP", "Bastad", "A. Player", "B. Opp"),
    ])

    captured: list = []
    _stub_tournament_fetch(monkeypatch, captured, {})

    class Args:
        output_dir = str(tmp_path)
        warehouse = wh_path
        limit = 5
        delay = 0

    assert backfill_forebet.mode_tournament(Args()) == 0
    assert captured == [("atp-singles", "bastad")]


def test_load_forebet_prediction_slugs_reads_recent_months_only(tmp_path):
    """Slug source = current + previous month only; empty-slug rows and old
    monthly files are excluded."""
    from datetime import date, timedelta
    from scripts import backfill_forebet

    today = date.today()
    this_month = today.strftime("%Y-%m")
    prev_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    older_month = (today.replace(day=1) - timedelta(days=62)).strftime("%Y-%m")

    def month_file(month, rows):
        import pandas as pd
        pd.DataFrame(rows).to_csv(
            tmp_path / f"predictions_forebet_{month}.csv.gz", index=False, compression="gzip")

    day = today.isoformat()
    base = lambda sl_t, sl_tt, mdate=None: {  # noqa: E731
        "match_date": mdate or day, "player_a": "A. One", "player_b": "B. Two",
        "tour_slug": sl_t, "tournament_slug": sl_tt}
    stale_day = (today - timedelta(days=30)).isoformat()  # beyond the 14d window
    month_file(this_month, [base("atp-singles", "us-open"),
                            base("", "stale-empty"),
                            base("wta-singles", "guadalajara"),
                            base("atp-singles", "long-finished", stale_day)])
    month_file(prev_month, [base("challenger-men", "rennes")])
    month_file(older_month, [base("atp-singles", "should-be-ignored")])

    slugs = backfill_forebet._load_forebet_prediction_slugs(tmp_path)
    got = {(r["tour_slug"], r["tournament_slug"]) for _, r in slugs.iterrows()}
    assert got == {("atp-singles", "us-open"), ("wta-singles", "guadalajara"),
                   ("challenger-men", "rennes")}
    # the 30-day-old row is excluded even though it is in the current month
    assert "long-finished" not in got


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


def test_relay_get_retries_transient_statuses_only(monkeypatch):
    import time
    import urllib.error
    import urllib.request

    calls = []

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._body

    outcomes = [
        urllib.error.HTTPError("https://r.jina.ai/x", 503, "Unavailable", {}, None),
        FakeResponse(b"recovered"),
    ]

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    from racketfactory.sources.forebet import relay_get

    assert relay_get("https://r.jina.ai/x", max_retries=3) == b"recovered"
    assert len(calls) == 2


# ------------------------------------------------ run #208 repairs --


def test_forebet_cache_key_is_versioned():
    from racketfactory.sources.forebet import FOREBET_CACHE_VERSION, forebet_cache_key

    assert forebet_cache_key("today") == f"forebet_today_{FOREBET_CACHE_VERSION}"
    assert forebet_cache_key("2026-09-13") == f"forebet_2026-09-13_{FOREBET_CACHE_VERSION}"


def test_mode_daily_uses_versioned_forebet_cache_key(tmp_path, monkeypatch):
    import pandas as pd
    from types import SimpleNamespace
    from scripts import backfill_forebet
    from racketfactory.sources.forebet import forebet_cache_key

    wh = tmp_path / "warehouse.csv.gz"
    pd.DataFrame([{"match_date": "2026-09-13", "tour": "ATP",
                   "tournament": "US Open", "player_a": "N. Djokovic",
                   "player_b": "C. Alcaraz"}]).to_csv(wh, index=False,
                                                       compression="gzip")
    keys = []
    canned = [{"match_date": "2026-09-13", "match_time": "",
               "player_home": "Novak Djokovic", "player_away": "Carlos Alcaraz",
               "prob_home": 70, "prob_away": 30,
               "odds_home": 1.5, "odds_away": 2.5, "predicted_winner": "1",
               "tournament": "US Open", "result_status": None,
               "result_score": None, "result_winner": None,
               "result_winner_name": None, "result_sets_home": None,
               "result_sets_away": None}]

    def fake_cached_fetch(key, thunk):
        keys.append(key)
        return canned

    monkeypatch.setattr(backfill_forebet, "cached_fetch", fake_cached_fetch)
    args = SimpleNamespace(days=["today", "tomorrow"], warehouse=str(wh),
                           output_dir=str(tmp_path / "out"), delay=0)
    assert backfill_forebet.mode_daily(args) == 0
    assert keys == [forebet_cache_key("today"), forebet_cache_key("tomorrow")]


def test_write_predictions_collapses_identity_twins(tmp_path):
    import pandas as pd
    from scripts import backfill_forebet

    # Pass 1 (no warehouse): tour-less, Jina spelling. Pass 2 (warehouse
    # matched): filled tour, warehouse spelling. Same match -> one row.
    unattributed = {"match_date": "2026-09-12", "tour": "", "tournament": "Unknown",
                    "player_a": "A. Sabalenka", "player_b": "E. Rybakina",
                    "predicted_winner": "player_b", "prediction_prob": 0.54,
                    "source": "Forebet"}
    attributed = {"match_date": "2026-09-12", "tour": "WTA", "tournament": "US Open",
                  "player_a": "Aryna Sabalenka", "player_b": "Elena Rybakina",
                  "predicted_winner": "player_b", "prediction_prob": 0.54,
                  "source": "Forebet"}
    backfill_forebet._write_predictions([unattributed], tmp_path)
    backfill_forebet._write_predictions([attributed], tmp_path)

    path = tmp_path / "predictions_forebet_2026-09.csv.gz"
    df = pd.read_csv(path)
    assert len(df) == 1
    assert df.iloc[0]["tour"] == "WTA"

    # Reverse insertion order: the attributed twin must still win.
    backfill_forebet._write_predictions([unattributed], tmp_path)
    df2 = pd.read_csv(path)
    assert len(df2) == 1
    assert df2.iloc[0]["tour"] == "WTA"


def test_fetch_daily_fails_fast_on_runner_skips_direct(monkeypatch):
    relay_calls = []
    direct_calls = []

    def fake_relay(self, url, expect_matches=True):
        relay_calls.append(url)
        return None

    def fake_direct(self, url):
        direct_calls.append(url)
        return None

    monkeypatch.setattr(ForebetPredictor, "_fetch_via_relay", fake_relay)
    monkeypatch.setattr(ForebetPredictor, "_fetch_direct", fake_direct)

    # On a GitHub runner: relay failure -> [] with NO direct attempt.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert ForebetPredictor().fetch_daily_predictions("today") == []
    assert len(relay_calls) == 1 and "/predictions/" in relay_calls[0]
    assert direct_calls == []

    # Off-runner: relay failure -> the direct chain is tried.
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert ForebetPredictor().fetch_daily_predictions("today") == []
    assert len(direct_calls) == 1
