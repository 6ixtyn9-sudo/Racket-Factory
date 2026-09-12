"""Scores-feed rows must carry sets-only evidence that settles."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from capture_theoddsapi_scores import row_from_event
from racketfactory.settlement import row_finality, settle_selection


def _event(home="Jaume Munar", away="Nicolas Alcala", hs=2, as_=1):
    return {
        "id": "evt1",
        "completed": True,
        "commence_time": "2026-09-11T18:10:00Z",
        "home_team": home,
        "away_team": away,
        "scores": [{"name": home, "score": str(hs)}, {"name": away, "score": str(as_)}],
        "sport_key": "tennis_atp_sevilla",
        "sport_title": "Sevilla",
        "last_update": "2026-09-11T20:00:00Z",
    }


def test_row_from_event_sets_only():
    row = row_from_event(_event())
    assert row is not None
    assert row["winner"] == "Jaume Munar"
    assert row["score"] == ""
    assert (row["_sets_a"], row["_sets_b"]) == (2, 1)
    assert row["_api_event_id"] == "evt1"


def test_sets_only_row_settles():
    row = row_from_event(_event())
    assert row_finality(row).final
    s = settle_selection("Jaume Munar vs Nicolas Alcala", "Jaume Munar",
                         [row], "2026-09-11")
    assert s.outcome == "WON"


def test_incomplete_events_ignored():
    ev = _event()
    ev["completed"] = False
    assert row_from_event(ev) is None
