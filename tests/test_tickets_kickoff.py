"""kickoff_guard keeps correct-SAST kickoffs: the 08:01 morning run must not
drop picks that have not started.

Regression anchor 2026-09-24/25: BetClan kickoffs were stored UTC+1, so an
08:30 SAST pick was stored as 07:30 and dropped as "already started" by the
08:01 run. Ingestion now stores SAST; the guard itself is unchanged and a
pick at 08:30 SAST survives while a genuinely-started 07:45 pick is skipped.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import auto_tickets as at

NOW_0801 = datetime(2026, 9, 25, 8, 1, 53, tzinfo=at.TZ)


def _pick(kickoff):
    return {
        "match": "A vs B",
        "selected_player": "A",
        "kickoff": kickoff,
        "bucket": "WATCHLIST",
    }


def test_pick_at_0830_sast_survives_0801_guard():
    kept, skipped = at.kickoff_guard([_pick("08:30")], "2026-09-25", NOW_0801)
    assert len(kept) == 1 and skipped == []


def test_started_picks_still_dropped():
    kept, skipped = at.kickoff_guard(
        [_pick("07:30"), _pick("08:00")], "2026-09-25", NOW_0801
    )
    assert kept == []
    assert [reason for _, reason in skipped] == ["already started", "already started"]


def test_rollover_kickoff_on_later_day_kept():
    # Ingestion rolled a 23:30 stamp to the next SAST day (00:30): not today,
    # so the guard keeps it.
    kept, skipped = at.kickoff_guard([_pick("00:30")], "2026-09-26", NOW_0801)
    assert len(kept) == 1 and skipped == []


def test_parse_kickoff_attaches_sast():
    ko = at.parse_kickoff(_pick("08:30"), "2026-09-25")
    assert ko is not None and ko.tzinfo is at.TZ
    assert (ko.hour, ko.minute) == (8, 30)
