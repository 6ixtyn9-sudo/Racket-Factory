"""Synthetic offline diagnostics tests; not a recorded provider fixture."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from racketfactory import rebuild_diagnostics as diagnostics
from racketfactory import doctor
from racketfactory.sources import betexplorer
from scripts import daily


def test_capture_is_bounded_redacted_and_never_replaces_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv(diagnostics.ENV, str(tmp_path))
    sample = '''<html><script>APIKEY_SUPERSECRET</script><h2>Match Winner</h2>
    <div data-market-id="42" data-period-id="0">Full Match</div>
    <table><tr><th>Match Winner</th><th>1</th><th>2</th></tr>
    <tr><td>Private Person <a href="https://bad/?token=APIKEY_SUPERSECRET">X</a></td>
    <td data-odd="1.52">1.52</td><td>2.56</td></tr></table>
    <input value="APIKEY_SUPERSECRET"></html>'''
    for _ in range(4):
        diagnostics.capture_betexplorer_fragment(sample, "listing")
        diagnostics.capture_betexplorer_fragment(sample, "detail")
    result = (tmp_path / "betexplorer_fragments.json").read_text()
    parsed = json.loads(result)
    assert len(parsed["fragments"]) == 3
    assert 'match winner' in result and 'data-market-id' in result
    for sensitive in ("APIKEY_SUPERSECRET", "Private Person", "https:", "1.52"):
        assert sensitive not in result


def test_betexplorer_observation_evidence_uses_only_bounded_discovered_pages(tmp_path, monkeypatch):
    monkeypatch.setenv(diagnostics.ENV, str(tmp_path))
    calls = []
    listing = '''<table><tr data-dt="29,10,2026,12,00"><td>12:00</td><td>
    <a href="/tennis/atp/tour/alice-beth/id123/">Alice Adams - Beth Brown</a></td>
    <td>1.50</td><td>2.50</td></tr></table>'''
    def fetch(url, *a, **kw):
        calls.append(url)
        return listing if "results/?" in url else "<h2>Match Winner</h2>"
    monkeypatch.setattr(betexplorer, "fetch_page_html", fetch)
    rows, status = betexplorer._fetch_live("2026-10-29", with_status=True)
    assert status == "partial" and len(rows) == 1 and len(calls) == 1
    assert rows[0]["listing_schedule_status"] == "unverified"
    assert rows[0]["match_date"] == rows[0]["match_time"] == ""
    assert "market" not in rows[0] and "period" not in rows[0]
    captured = json.loads((tmp_path / "betexplorer_fragments.json").read_text())
    assert [f["kind"] for f in captured["fragments"]] == ["listing"]


def test_unusable_quotes_record_each_reason_without_verified_no_bet(tmp_path):
    day = datetime.now(timezone.utc).date().isoformat()
    row = {"match_date": day, "player_home": "Alice", "player_away": "Beth", "source": "BetExplorer",
           "bookmaker": "BetExplorer consensus", "odds_home": 1.5, "odds_away": 2.5,
           "observed_at": datetime.now(timezone.utc).isoformat()}
    collection = {"status": "complete", "date": day, "observed_at": datetime.now(timezone.utc).isoformat(),
                  "predictions": [{"match_date": day, "player_home": "Alice", "player_away": "Beth"}],
                  "raw_quotes": {"betexplorer": [row], "oddsportal": []}}
    with pytest.raises(RuntimeError, match="no usable fresh fixture quote observations"):
        daily.stage_fresh_rebuild_picks(day, collection, tmp_path / "stage")
    result = collection["quote_diagnostics"]
    assert result["sources"]["betexplorer"]["fixture_rows"] == 1
    assert result["sources"]["betexplorer"]["quote_pairs"] == 1
    assert result["sources"]["betexplorer"]["eligible_rows"] == 0
    reasons = result["sources"]["betexplorer"]["rejection_reasons"]
    assert reasons["market_missing"] == reasons["period_missing"] == 1
    assert reasons["execution:quote market missing or incompatible"] == 1
    assert result["warehouse_matched_rows"] is None


def test_rebuild_status_failed_overrides_healthy_historical_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "LOCALDATA", tmp_path)
    monkeypatch.setenv(diagnostics.ENV, str(tmp_path / "diagnostics"))
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    (tmp_path / "auto_tickets_2026-10-29.json").write_text(json.dumps({"date": "2026-10-29", "generated_at": "old", "diagnostics": {"playable": 0}, "accas": []}))
    diagnostics.write_status({"github_run_id": "42", "github_run_attempt": "1", "status": "failed",
        "stage": "validating_and_staging_quotes", "ticket_generation": "not_completed"})
    checks = doctor.run_health_checks(as_of="2026-10-29", expect_rebuild=True)
    assert checks["tickets:stages"]["status"] == "OK" and "old" in checks["tickets:stages"]["detail"]
    assert checks["rebuild:current"]["status"] == "CRITICAL"
    assert "not_completed" in checks["rebuild:current"]["detail"]
    assert doctor.main(["--as-of", "2026-10-29", "--expect-rebuild"]) == 1
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert doctor.run_health_checks(as_of="2026-10-29", expect_rebuild=True)["rebuild:current"]["status"] == "CRITICAL"


def test_rebuild_quote_execution_rejects_consensus_and_mismatched_rules():
    from racketfactory.execution import rebuild_quote_execution_problem
    q = {"source": "TheOddsAPI", "bookmaker": "example", "price_type": "bookmaker",
         "market": "h2h", "period": "match",
         "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}}
    assert rebuild_quote_execution_problem(q) == ""  # missing authenticated terms are documentary warnings
    assert rebuild_quote_execution_problem(dict(q, bookmaker="BetExplorer consensus"))
    assert rebuild_quote_execution_problem(dict(q, settlement_rules={"retirement": "void", "walkover": "void", "cancelled": "void"})) == ""
    assert rebuild_quote_execution_problem(dict(q, settlement_rules=None)) == ""


def test_synthetic_rule_and_schedule_fixtures_never_become_production_proof():
    from tests._synthetic_evidence import (attach_synthetic_schedule_evidence,
                                           attach_synthetic_settlement_evidence)
    from racketfactory.execution import (betexplorer_schedule_evidence_problem,
                                         rebuild_quote_execution_problem,
                                         settlement_evidence_problem)
    now = datetime.now(timezone.utc)
    quote = {"source": "BetExplorer", "price_type": "bookmaker", "bookmaker": "Synthetic Book",
             "bookmaker_id": "synthetic-id", "operator_jurisdiction": "TEST",
             "match_date": (now.date()).isoformat(), "commence_time": (now + timedelta(hours=2)).isoformat(),
             "observed_at": now.isoformat(), "event_id": "synthetic-event",
             "match_url": "/tennis/atp/event/synthetic-event/", "player_home": "Alice Adams",
             "player_away": "Beth Brown", "market": "h2h", "period": "match",
             "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"}}
    attach_synthetic_settlement_evidence(quote)
    attach_synthetic_schedule_evidence(quote)
    assert settlement_evidence_problem(quote) == "synthetic settlement evidence is not provider proof"
    assert betexplorer_schedule_evidence_problem(quote) == "synthetic BetExplorer schedule evidence is not provider proof"
    assert rebuild_quote_execution_problem(quote) == "synthetic BetExplorer schedule evidence is not provider proof"
    # A supported candidate may satisfy the fixture-level gate without
    # authenticating a provider-wide schedule contract.
    schedule = dict(quote["schedule_evidence"], test_only=False,
                    status="candidate_unverified_contract")
    assert betexplorer_schedule_evidence_problem(dict(quote, schedule_evidence=schedule)) == ""
    # Incomplete contradictory-signal accounting remains a hard block.
    schedule.update(status="provider_verified", contradictory_signals=None)
    assert "conflicting or unexamined" in betexplorer_schedule_evidence_problem(dict(quote, schedule_evidence=schedule))


def test_optional_failure_does_not_hide_verified_primary_quotes(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    import pandas as pd
    from racketfactory import warehouse
    day = datetime.now(timezone.utc).date().isoformat()
    from tests._synthetic_evidence import (allow_synthetic_evidence_in_pipeline,
                                           attach_synthetic_schedule_evidence,
                                           attach_synthetic_settlement_evidence)
    row = {"match_date": day, "player_home": "Alice", "player_away": "Beth", "source": "BetExplorer",
           "bookmaker": "actual-book", "odds_home": 1.5, "odds_away": 2.5,
           "observed_at": datetime.now(timezone.utc).isoformat(), "market": "h2h", "period": "match",
           "price_type": "bookmaker", "settlement_rules": {"retirement": "advancer", "walkover": "void", "cancelled": "void"},
           "event_id": "synthetic-event", "match_url": "/tennis/atp/synthetic-event/",
           "commence_time": datetime.now(timezone.utc).isoformat()}
    attach_synthetic_settlement_evidence(row)
    attach_synthetic_schedule_evidence(row)
    allow_synthetic_evidence_in_pipeline(monkeypatch)
    local = tmp_path / "data"; local.mkdir()
    pd.DataFrame([{"match_date": "2025-01-01", "winner": "Alice"}]).to_csv(local / "warehouse.csv.gz", index=False)
    monkeypatch.setattr(daily, "LOCALDATA", local)
    captured = []
    def build(**kwargs):
        captured.extend(kwargs["supplied_comparisons"])
        return pd.DataFrame()
    monkeypatch.setattr(warehouse, "build_live_rows", build)
    def miner(cmd, **kwargs):
        (Path(cmd[cmd.index("--output-dir") + 1]) / f"picks_{day}.json").write_text("[]")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess, "run", miner)
    collection = {"status": "complete", "date": day, "observed_at": datetime.now(timezone.utc).isoformat(),
                  "predictions": [{"match_date": day, "player_home": "Alice", "player_away": "Beth"}],
                  "outcomes": {"oddsportal": {"status": "failed"}},
                  "raw_quotes": {"betexplorer": [row], "oddsportal": []}}
    assert daily.stage_fresh_rebuild_picks(day, collection, tmp_path / "stage") == []
    assert captured and captured[0]["market"] == "h2h"
    assert collection["quote_diagnostics"]["sources"]["betexplorer"]["eligible_rows"] == 1


def test_incompatible_market_period_and_settlement_are_counted(tmp_path):
    day = datetime.now(timezone.utc).date().isoformat()
    base = {"match_date": day, "player_home": "Alice", "player_away": "Beth", "source": "BetExplorer",
            "bookmaker": "book", "odds_home": 1.5, "odds_away": 2.5,
            "observed_at": datetime.now(timezone.utc).isoformat(), "market": "set_winner", "period": "set1",
            "price_type": "bookmaker", "settlement_rules": {"retirement": "void", "walkover": "void", "cancelled": "void"}}
    collection = {"status": "complete", "date": day, "observed_at": datetime.now(timezone.utc).isoformat(),
                  "predictions": [{"match_date": day, "player_home": "Alice", "player_away": "Beth"}],
                  "raw_quotes": {"betexplorer": [base], "oddsportal": []}}
    with pytest.raises(RuntimeError):
        daily.stage_fresh_rebuild_picks(day, collection, tmp_path / "stage")
    reasons = collection["quote_diagnostics"]["sources"]["betexplorer"]["rejection_reasons"]
    assert reasons["market_incompatible"] == reasons["period_incompatible"] == 1
    assert reasons["execution:quote market missing or incompatible"] == 1


def test_failed_rebuild_writes_run_status_and_leaves_card_unchanged(tmp_path, monkeypatch):
    from scripts import auto_tickets
    day = daily._now_local().date().isoformat()
    monkeypatch.setattr(daily, "LOCALDATA", tmp_path)
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_SERIALIZED", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "555")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv(diagnostics.ENV, str(tmp_path / "diag"))
    card = tmp_path / f"auto_tickets_{day}.json"
    card.write_text(json.dumps({"date": day, "frozen": True, "accas": [], "staked_pct": 0, "paper_staked_pct": 0}))
    state = tmp_path / "auto_tickets_state.json"
    state.write_text(json.dumps({"bank": 100, "paper_bank": 100, "open_slips": [], "history": []}))
    before = card.read_bytes(), state.read_bytes()
    row = {"match_date": day, "player_home": "Alice", "player_away": "Beth", "source": "BetExplorer",
           "bookmaker": "consensus", "odds_home": 1.5, "odds_away": 2.5,
           "observed_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(daily, "collect_rebuild_sources", lambda d, **kwargs: {"status": "complete", "date": d,
        "observed_at": datetime.now(timezone.utc).isoformat(), "outcomes": {"betexplorer": {"status": "complete", "rows": 1}},
        "predictions": [{"match_date": d, "player_home": "Alice", "player_away": "Beth"}],
        "raw_quotes": {"betexplorer": [row], "oddsportal": []}})
    with pytest.raises(RuntimeError, match="no usable fresh fixture quote observations"):
        daily.run_guarded_empty_rebuild(day)
    status = json.loads((tmp_path / "diag/rebuild_status.json").read_text())
    assert status["status"] == "failed" and status["stage"] == "validating_and_staging_quotes"
    assert status["ticket_generation"] == "not_completed"
    assert status["quote_stages"]["sources"]["betexplorer"]["rejection_reasons"]["market_missing"] == 1
    assert (card.read_bytes(), state.read_bytes()) == before


def test_known_oddsportal_route_cooldown_is_bounded(monkeypatch):
    from racketfactory.sources import oddsportal_upcoming as op
    real_datetime = datetime
    class Clock(real_datetime):
        now_value = real_datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None): return cls.now_value
    monkeypatch.setattr(op, "datetime", Clock)
    calls = []
    monkeypatch.setattr(op, "fetch_page_html", lambda url, *a, **k: calls.append(url) or "<html>no matches</html>")
    op._fetch_live("/tennis/", "2026-10-29", extra_paths=["/tennis/next/"])
    assert len(calls) == 1
    Clock.now_value = real_datetime(2026, 10, 29, 22, 16, 20, tzinfo=timezone.utc)
    op._fetch_live("/tennis/", "2026-10-29", extra_paths=["/tennis/next/"])
    assert len(calls) == 3


def test_synthetic_oddsportal_indicative_schedule_is_not_provider_proof():
    from datetime import datetime, timedelta, timezone
    from tests._synthetic_evidence import attach_synthetic_oddsportal_schedule_evidence
    from racketfactory.execution import oddsportal_schedule_evidence_problem, rebuild_quote_execution_problem
    from racketfactory.kickoff import SAST

    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=2)
    quote = {
        "source": "OddsPortal", "bookmaker": "OddsPortal best odds", "price_type": "best_across_books",
        "market": "h2h", "period": "match", "odds_home": 1.7, "odds_away": 2.3,
        "observed_at": now.isoformat(), "event_id": "synthetic-event",
        "match_url": "/tennis/h2h/a-b/synthetic-event/", "player_home": "Alice Adams",
        "player_away": "Beth Brown", "match_date": start.astimezone(SAST).date().isoformat(),
        "commence_time": start.isoformat(),
    }
    attach_synthetic_oddsportal_schedule_evidence(quote)
    assert oddsportal_schedule_evidence_problem(quote) == "synthetic OddsPortal schedule evidence is not provider proof"
    assert rebuild_quote_execution_problem(quote) == "synthetic OddsPortal schedule evidence is not provider proof"
    assert rebuild_quote_execution_problem({key: value for key, value in quote.items() if key != "schedule_evidence"})
