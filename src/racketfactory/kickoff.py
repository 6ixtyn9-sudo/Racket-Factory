"""Kickoff timezone normalisation — every source's kickoff becomes SAST at ingestion.

The pipeline runs on SAST (Africa/Johannesburg, UTC+2): auto_tickets,
mine_edges, the WhatsApp digest and the warehouse all read match_date /
match_time as SAST wall time. Sources do not agree on a zone, so adapters
must convert AT INGESTION rather than letting every consumer guess:

- BetClan publishes kickoff stamps with no explicit offset; measured
  2026-09-24/25 they were exactly UTC+1 (Betway SAST kickoff - 1h on every
  singles checked). Whether BetClan runs fixed UTC+1 (e.g. WAT) or
  Europe/London is unverified: London leaves BST on 2026-10-25 which would
  widen the gap to 2h. Until that can be observed we assume the measured
  fixed UTC+1 via the IANA name Etc/GMT-1 (NOTE: Etc/GMT-1 means UTC+1;
  the sign is inverted vs the ISO notation).
- The Odds API commence_time is ISO UTC (e.g. 2026-09-25T08:30:00Z).
- Forebet: Playwright renders with timezone_id="UTC" and the Jina relay
  path renders server-side; both stored times are still unverified against
  a known reference, so no conversion is applied there yet.

Date and time are ALWAYS converted together so the day rolls over with the
clock: BetClan 23:30 (UTC+1) is 00:30 SAST the next day, and a 22:30Z
commence_time is 00:30 SAST the next day.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")

# Fixed UTC+1. IANA's POSIX-style sign inversion: "Etc/GMT-1" == UTC+1.
BETCLAN_TZ = ZoneInfo("Etc/GMT-1")

# The Odds API commence_time is always ISO UTC from the API.
THE_ODDS_API_TZ = timezone.utc

_KICKOFF_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_source_timestamp(raw: str, default_tz=BETCLAN_TZ) -> datetime | None:
    """Parse a source kickoff stamp into an aware datetime.

    Honours an explicit UTC offset when the stamp carries one ("Z" or
    "+HH:MM"); naive stamps get ``default_tz``. Returns None when the text
    is not a parseable date+time.
    """
    raw = str(raw or "").strip()
    if not raw:
        return None
    m = _KICKOFF_RE.match(raw)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute = int(m.group(4)), int(m.group(5))
    second = int(m.group(6) or 0)
    offset_text = m.group(7)
    try:
        dt = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    if offset_text:
        if offset_text == "Z":
            return dt.replace(tzinfo=timezone.utc)
        sign = 1 if offset_text[0] == "+" else -1
        digits = offset_text[1:].replace(":", "")
        try:
            delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
        except (ValueError, IndexError):
            return None
        return dt.replace(tzinfo=timezone(sign * delta))
    return dt.replace(tzinfo=default_tz)


def to_sast_parts(dt: datetime) -> tuple[str, str]:
    """Aware/naive datetime -> (YYYY-MM-DD, HH:MM) SAST wall time.

    Naive input is assumed to already be UTC. Date rolls with the clock:
    22:30Z converts to 00:30 on the next SAST day.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=THE_ODDS_API_TZ)
    local = dt.astimezone(SAST)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def convert_kickoff(date_str: str, time_str: str, source_tz) -> tuple[str, str]:
    """Convert a source (date, time) pair to SAST, date included.

    The pair is combined BEFORE converting so late-evening kickoffs roll
    into the next SAST day (BetClan 23:30 -> 2026-09-26 00:30) and early
    ones into the previous day. Returns ("", "") when either part is
    unparseable.
    """
    raw = f"{str(date_str or '').strip()}T{str(time_str or '').strip()}"
    dt = parse_source_timestamp(raw, default_tz=source_tz)
    if dt is None:
        return "", ""
    return to_sast_parts(dt)


def utc_commence_to_sast(commence_time: str) -> tuple[str, str]:
    """The Odds API ISO commence_time (UTC) -> (SAST date, SAST time)."""
    dt = parse_source_timestamp(commence_time, default_tz=THE_ODDS_API_TZ)
    if dt is None:
        return "", ""
    return to_sast_parts(dt)
