"""Controls on the reporting layer itself.

Every test here exists because a number in a report was, or could be, read as
saying more than the data supports. These are not tests of the betting logic;
they are tests that the engine describes itself truthfully.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from racketfactory import tripwire as tw  # noqa: E402
import kickoff_tz_audit as tza  # noqa: E402
import autobets_forensics as fx  # noqa: E402


# ------------------------------------------------ rule-breach accounting --

def _acca(odds, stake, ret, *, paper=False, won=True, type_="value_2leg_mutual"):
    return {"odds": odds, "stake_pct": stake, "return_pct": ret,
            "paper": paper, "won": won, "type": type_, "legs": []}


def _history(*accas, day="2026-09-17"):
    return [{"date": day, "accas": list(accas)}]


def test_an_acca_above_the_hard_ceiling_is_a_breach():
    assert tw.acca_rule_breach(_acca(4.08, 28.66, 116.92))
    assert "4.08" in tw.acca_rule_breach(_acca(4.08, 28.66, 116.92))


def test_an_acca_inside_the_ceiling_is_not_a_breach():
    assert tw.acca_rule_breach(_acca(4.00, 10, 40)) is None
    assert tw.acca_rule_breach(_acca(1.66, 10, 16)) is None


def test_a_paper_acca_is_never_a_breach():
    """Paper never touched the bank, so it cannot contaminate the record."""
    assert tw.acca_rule_breach(_acca(9.99, 10, 0, paper=True)) is None


def test_the_winning_breach_is_stripped_from_the_record():
    """The live case: +88.26 from a bet the rules barred turns +30 into -58."""
    adj = tw.adjusted_pnl(_history(
        _acca(4.08, 28.656, 116.92, type_="fallback_2leg_mutual"),
        _acca(1.66, 20.0, 0.0, won=False),
    ))
    assert adj["booked_pnl_pct"] == pytest.approx(68.26, abs=0.01)
    assert adj["adjusted_pnl_pct"] == pytest.approx(-20.0, abs=0.01)
    assert adj["breach_pnl_pct"] == pytest.approx(88.26, abs=0.01)
    assert len(adj["breaches"]) == 1


def test_a_losing_breach_is_stripped_too_even_though_it_flatters_us():
    """Removing only the breaches that helped would be its own dishonesty."""
    adj = tw.adjusted_pnl(_history(_acca(5.0, 10.0, 0.0, won=False)))
    assert adj["booked_pnl_pct"] == pytest.approx(-10.0)
    assert adj["adjusted_pnl_pct"] == pytest.approx(0.0)


def test_a_clean_record_renders_no_adjustment_block():
    adj = tw.adjusted_pnl(_history(_acca(2.0, 10.0, 20.0)))
    assert adj["breaches"] == []
    assert tw.render_adjusted_pnl(adj) == []


def test_the_adjusted_record_is_rendered_where_pnl_is_quoted():
    report = tw.build_report(_history(
        _acca(4.08, 28.656, 116.92, type_="fallback_2leg_mutual")))
    text = "\n".join(tw.render_report(report))
    assert "RECORD ADJUSTED FOR RULE BREACHES" in text
    assert "entitled to place" in text


def test_the_live_ledger_still_shows_the_known_contamination():
    """Regression guard: if this stops reporting -58.09 the detector broke."""
    state = ROOT / "localdata" / "auto_tickets_state.json"
    if not state.exists():
        pytest.skip("no live ledger in this checkout")
    adj = tw.adjusted_pnl(json.loads(state.read_text()).get("history", []))
    assert adj["booked_pnl_pct"] == pytest.approx(30.17, abs=0.05)
    assert adj["adjusted_pnl_pct"] == pytest.approx(-58.09, abs=0.05)


# ------------------------------------------- kickoff evidence accrual --

def _cache(tmp, day, fixtures):
    (tmp / f"theoddsapi_odds_cache_{day}.json").write_text(json.dumps(
        {"rows": [{"match_date": day, "match_time": t,
                   "player_home": a, "player_away": b}
                  for a, b, t in fixtures]}))


def _picks(tmp, day, fixtures):
    (tmp / f"picks_{day}.json").write_text(json.dumps(
        [{"match": f"{a} vs {b}", "kickoff": t, "match_date": day,
          "source": "BetClan"}
         for a, b, t in fixtures]))


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tza, "LOCALDATA", tmp_path)
    monkeypatch.setattr(tza, "OUT_FILE", tmp_path / "audit.json")
    return tmp_path


FIX = [("Alpha Aaa", "Beta Bbb", "12:00"), ("Gamma Ccc", "Delta Ddd", "14:30"),
       ("Epsi Eee", "Zeta Fff", "16:45")]


def test_with_no_evidence_the_verdict_is_unknown_never_green(audit_dir):
    report = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert report["status"] == "UNKNOWN"
    assert report["matched_fixtures"] == 0


def test_evidence_accrues_and_survives_the_cache_disappearing(audit_dir):
    """The whole point: the reference cache is ephemeral and uncommitted, so
    without a durable ledger the audit can never leave UNKNOWN."""
    _picks(audit_dir, "2026-09-25", FIX)
    _cache(audit_dir, "2026-09-25", FIX)
    first = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert first["status"] == "OK"
    assert first["new_samples_recorded"] == 3

    (audit_dir / "theoddsapi_odds_cache_2026-09-25.json").unlink()
    second = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert second["matched_this_run"] == 0
    assert second["remembered_samples"] == 3
    assert second["status"] == "OK"


def test_samples_are_not_double_counted_across_runs(audit_dir):
    _picks(audit_dir, "2026-09-25", FIX)
    _cache(audit_dir, "2026-09-25", FIX)
    tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    again = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert again["new_samples_recorded"] == 0
    assert again["matched_fixtures"] == 3


def test_a_one_hour_drift_is_caught(audit_dir):
    _picks(audit_dir, "2026-09-25", FIX)
    _cache(audit_dir, "2026-09-25",
           [(a, b, f"{int(t[:2]) + 1:02d}{t[2:]}") for a, b, t in FIX])
    report = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert report["status"] == tza.STATUS_DRIFT
    assert report["median_offset_minutes"] == pytest.approx(-60)


def test_stale_evidence_is_not_evidence_about_today(audit_dir):
    _picks(audit_dir, "2026-01-01", FIX)
    _cache(audit_dir, "2026-01-01", FIX)
    tza.audit(["2026-01-01"], today=date(2026, 1, 2))
    later = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert later["matched_fixtures"] == 0
    assert later["status"] == "UNKNOWN"


def test_a_corrupt_evidence_line_cannot_take_the_audit_down(audit_dir):
    (audit_dir / tza.EVIDENCE_NAME).write_text(
        'not json\n{"date": "2026-09-25", "fixture": "x", "delta_minutes": 0}\n'
        '{"no_delta": 1}\n')
    report = tza.audit(["2026-09-25"], today=date(2026, 9, 26))
    assert report["matched_fixtures"] == 1


def test_the_evidence_file_is_bounded(audit_dir, monkeypatch):
    monkeypatch.setattr(tza, "EVIDENCE_MAX_ROWS", 10)
    rows = [{"date": "2026-09-25", "fixture": f"f{i}", "delta_minutes": 0}
            for i in range(40)]
    tza.record_evidence(rows, existing=[])
    assert len((audit_dir / tza.EVIDENCE_NAME).read_text().strip().splitlines()) == 10


def test_the_evidence_path_follows_a_redirected_localdata(tmp_path, monkeypatch):
    """Regression: a module-level constant froze the real repo path at import
    and the first run of these very tests wrote into the live localdata."""
    monkeypatch.setattr(tza, "LOCALDATA", tmp_path)
    assert tza.evidence_file().parent == tmp_path


def test_display_path_never_raises_outside_the_repo():
    assert tza._display_path(Path("/tmp/elsewhere/x.jsonl")) == "/tmp/elsewhere/x.jsonl"


def test_audit_can_be_run_without_persisting(audit_dir):
    _picks(audit_dir, "2026-09-25", FIX)
    _cache(audit_dir, "2026-09-25", FIX)
    tza.audit(["2026-09-25"], persist=False, today=date(2026, 9, 26))
    assert not (audit_dir / tza.EVIDENCE_NAME).exists()


# -------------------------------------------------- staking honesty --

def _days(gross_by_day):
    """Synthesise settled accas whose gross return multiple is `g`."""
    days = {}
    for day, g in gross_by_day.items():
        days[day] = [{"odds": 2.0, "paper": False, "stake_pct": 10.0,
                      "won": g > 0, "return_pct": 10.0 * g,
                      "legs": [{"odds": g if g > 0 else 2.0,
                                "_settle_outcome": "WON" if g > 0 else "LOST",
                                "match": "A vs B"}]}]
    return days


def test_stake_dominance_separates_growth_from_drawdown():
    """The original error was collapsing two different questions into one."""
    import random
    days = _days({"2026-09-0%d" % i: g for i, g in
                  enumerate([1.8, 0.0, 1.3, 0.0, 2.1, 0.4, 1.1, 0.0, 1.9], 1)})
    dom = fx.stake_dominance(days, sorted(days), random.Random(1), 2000)
    assert set(dom) >= {"p_growth_not_worse", "p_drawdown_not_worse",
                        "p_dominates"}
    # a lower fraction can never have a WORSE drawdown on the same card
    assert dom["p_drawdown_not_worse"] == 1.0
    assert dom["p_dominates"] <= dom["p_drawdown_not_worse"]


def test_stake_dominance_reports_growth_at_enough_precision_to_see_a_gap():
    """0.0278686 vs 0.0278694 rounds to a tie at 4dp — that tie was the bug."""
    import random
    days = _days({"2026-09-0%d" % i: g for i, g in
                  enumerate([1.795, 1.298, 0.37, 4.08, 0.0, 0.41, 1.31,
                             2.18, 0.0], 1)})
    dom = fx.stake_dominance(days, sorted(days), random.Random(1), 200)
    assert dom["point_lo_log_day"] != dom["point_hi_log_day"]


def test_stake_dominance_can_exclude_the_rule_breaching_day():
    import random
    days = _days({"2026-09-0%d" % i: g for i, g in
                  enumerate([1.8, 0.0, 1.3, 4.08, 0.0], 1)})
    order = sorted(days)
    full = fx.stake_dominance(days, order, random.Random(1), 200)
    less = fx.stake_dominance(days, order, random.Random(1), 200,
                              exclude=(order[3],))
    assert full["bet_days"] == 5 and less["bet_days"] == 4


# ------------------------------------- pre-registration self-evaluation --

def test_every_registered_hypothesis_is_scored():
    import random
    state = ROOT / "localdata" / "auto_tickets_state.json"
    if not state.exists():
        pytest.skip("no live ledger in this checkout")
    days = fx.load_days(json.loads(state.read_text()))
    legs = fx.load_legs(days)
    board = fx.evaluate_preregistration(legs, days, sorted(days),
                                        random.Random(1), 200)
    assert {r["id"] for r in board} == {i["id"] for i in fx.PREREGISTRATION}
    for row in board:
        assert row["bar_n"] > 0 and "status" in row


def test_nothing_is_ready_to_decide_at_the_current_sample_size():
    """If this ever fails, the bar was met — decide it, do not delete it."""
    import random
    state = ROOT / "localdata" / "auto_tickets_state.json"
    if not state.exists():
        pytest.skip("no live ledger in this checkout")
    days = fx.load_days(json.loads(state.read_text()))
    legs = fx.load_legs(days)
    board = fx.evaluate_preregistration(legs, days, sorted(days),
                                        random.Random(1), 200)
    ready = [r["id"] for r in board if r["met"]]
    assert ready == [], f"bar met for {ready} — apply the registered action"


def test_a_hypothesis_fires_once_its_bar_is_actually_met():
    """Proves the board is capable of saying yes, not just permanently no."""
    import random
    legs = ([{"day": "d", "match": "A/B vs C/D", "odds": 3.0, "won": True,
              "bucket": "x", "verdict": "ALLOW", "cp": 0.8, "doubles": True}] * 40
            + [{"day": "d", "match": "A vs B", "odds": 2.0, "won": False,
                "bucket": "x", "verdict": "ALLOW", "cp": 0.8,
                "doubles": False}] * 40)
    board = fx.evaluate_preregistration(legs, {}, [], random.Random(1), 400)
    row = next(r for r in board if r["id"] == "H2-doubles-ev-surcharge")
    assert row["n"] == 40 and row["met"], row
    assert row["status"] == "READY TO DECIDE"


def test_the_board_appears_in_the_report_and_names_the_shortfall():
    proc = subprocess.run(
        [sys.executable, "scripts/autobets_forensics.py", "--bootstrap", "200"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    assert proc.returncode == 0, proc.stderr
    assert "PRE-REGISTERED HYPOTHESES" in proc.stdout
    assert "no constant may be changed on this evidence" in proc.stdout
    assert "risk trade, NOT dominance" in proc.stdout
