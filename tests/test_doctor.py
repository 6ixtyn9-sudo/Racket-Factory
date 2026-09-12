"""Doctor health checks against a fixture localdata."""
import csv
import gzip
import json

import racketfactory.doctor as doc


def _write_results(path, rows):
    with gzip.open(path, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["match_date", "player_a", "player_b",
                                          "winner", "score"])
        w.writeheader()
        w.writerows(rows)


def test_critical_when_no_result_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "LOCALDATA", tmp_path)
    res = doc.run_health_checks(as_of="2026-09-12")
    assert res["results:any_rows"]["status"] == "CRITICAL"
    assert res["warehouse"]["status"] == "WARN"
    assert doc.main(["--as-of", "2026-09-12"]) == 1


def test_fresh_results_pass_and_stale_warn(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "LOCALDATA", tmp_path)
    _write_results(tmp_path / "challenger_results_tennis_2026-09.csv.gz", [
        {"match_date": "2026-09-12", "player_a": "A", "player_b": "B",
         "winner": "A", "score": "6-0 6-0"}])
    _write_results(tmp_path / "tennisdata_tennis_2026-01.csv.gz", [
        {"match_date": "2026-01-04", "player_a": "A", "player_b": "B",
         "winner": "A", "score": "6-0 6-0"}])
    res = doc.run_health_checks(as_of="2026-09-12")
    assert res["results:challenger_results"]["status"] == "OK"
    assert res["results:tennisdata"]["status"] == "WARN"
    assert res["results:any_rows"]["status"] == "OK"


def test_picks_priced_share(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "LOCALDATA", tmp_path)
    (tmp_path / "picks_2026-09-12.json").write_text(json.dumps([
        {"bucket": "WATCHLIST", "odds": 1.9, "odds_source": "TheOddsAPI"},
        {"bucket": "WATCHLIST_NO_ODDS", "odds": None, "odds_source": "nan",
         "odds_reject_reason": "missing selected-side odds"},
        {"bucket": "SKIPPED_DEAD_EDGE", "odds": None, "odds_source": ""},
    ]))
    res = doc.run_health_checks(as_of="2026-09-12")
    assert res["picks:priced_share"]["status"] == "OK"
    assert res["picks:priced_share"]["detail"].startswith("picks_2026-09-12.json: 1/2")


def test_expect_warehouse_is_critical(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "LOCALDATA", tmp_path)
    res = doc.run_health_checks(expect_warehouse=True, as_of="2026-09-12")
    assert res["warehouse"]["status"] == "CRITICAL"
