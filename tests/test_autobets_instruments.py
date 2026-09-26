"""The measuring instruments must run, be wired in, and refuse to over-claim."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import autobets_forensics as forensics  # noqa: E402


def _run(*args):
    env = {"PYTHONPATH": "src", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp"}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)


def test_forensics_runs_clean_on_the_live_ledger(tmp_path):
    out = tmp_path / "forensics.json"
    proc = _run("scripts/autobets_forensics.py", "--bootstrap", "500",
                "--json", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    if not out.exists():
        pytest.skip("no settled ledger in this checkout")
    payload = json.loads(out.read_text())
    assert payload["bet_days"] >= 1
    assert "null_test" in payload and "preregistration" in payload


def test_forensics_never_reports_a_battery_without_a_null_test():
    """The whole point: a variant table is not a recommendation."""
    source = (ROOT / "scripts" / "autobets_forensics.py").read_text()
    assert "SEARCH-WINNER NULL TEST" in source
    assert "NOT ADOPTABLE" in source


def test_kickoff_audit_runs_clean():
    proc = _run("scripts/kickoff_tz_audit.py", "--days", "2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BetClan timezone assumption" in proc.stdout


# ------------------------------------------------------- pre-registration --

def test_every_hypothesis_has_a_bar_and_both_outcomes():
    assert forensics.PREREGISTRATION
    ids = set()
    for item in forensics.PREREGISTRATION:
        for field in ("id", "claim", "as_of_2026_09_26", "test", "bar",
                      "action_if_passed", "action_if_failed"):
            assert item.get(field), f"{item.get('id')} missing {field}"
        assert item["id"] not in ids, "duplicate hypothesis id"
        ids.add(item["id"])


def test_the_findings_that_were_NOT_acted_on_are_all_registered():
    """Anything measured but left alone must be a written-down hypothesis,
    not a dangling observation that gets "remembered" selectively later."""
    registered = {item["id"] for item in forensics.PREREGISTRATION}
    assert {"H1-boost-privileges", "H2-doubles-ev-surcharge",
            "H3-leg-price-floor", "H4-acca-slots",
            "H5-calibration-band"} <= registered


def test_preregistration_cli_exits_without_touching_state():
    proc = _run("scripts/autobets_forensics.py", "--preregistration")
    assert proc.returncode == 0
    assert "registered 2026-09-26, before the data" in proc.stdout
    assert "A change to any constant above without a passing bar is a guess." in proc.stdout


# --------------------------------------------------------------- wiring --

@pytest.mark.parametrize("script", [
    "scripts/kickoff_tz_audit.py",
    "scripts/auto_tickets.py",
    "scripts/auto_tickets_grade.py",
    "scripts/autobets_forensics.py",
])
def test_daily_pipeline_invokes_the_instruments(script):
    daily = (ROOT / "scripts" / "daily.py").read_text()
    assert script in daily, f"{script} is not wired into the daily run"
    assert (ROOT / script).exists()
