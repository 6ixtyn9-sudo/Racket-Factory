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
  the sign is inverted vs the ISO notation). That assumption now has an
  explicit expiry (BETCLAN_TZ_REVIEW_DATE), a drift check
  (betclan_tz_status) and a config override (RACKET_FACTORY_BETCLAN_TZ)
  so the 2026-10-25 risk is monitored rather than merely documented.
- The Odds API commence_time is ISO UTC (e.g. 2026-09-25T08:30:00Z).
- Forebet: Playwright renders with timezone_id="UTC" and the Jina relay
  path renders server-side; both stored times are still unverified against
  a known reference, so no conversion is applied there yet.

Date and time are ALWAYS converted together so the day rolls over with the
clock: BetClan 23:30 (UTC+1) is 00:30 SAST the next day, and a 22:30Z
commence_time is 00:30 SAST the next day.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")

# Fixed UTC+1. IANA's POSIX-style sign inversion: "Etc/GMT-1" == UTC+1.
BETCLAN_TZ_DEFAULT = "Etc/GMT-1"


def _betclan_tz() -> ZoneInfo:
    """BetClan's assumed zone, overridable without a code change.

    BetClan is the single source of kickoff for essentially every staked
    leg, and its zone is an *assumption* measured on two days. When the
    2026-10-25 review (see BETCLAN_TZ_REVIEW_DATE) shows the offset has
    moved, the correction must be a one-line config change on a running
    system, not an emergency deploy. Unknown/invalid names fall back to the
    measured default rather than raising at import time.
    """
    name = str(os.getenv("RACKET_FACTORY_BETCLAN_TZ", "") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return ZoneInfo(BETCLAN_TZ_DEFAULT)


BETCLAN_TZ = _betclan_tz()

# The Odds API commence_time is always ISO UTC from the API.
THE_ODDS_API_TZ = timezone.utc

# --- BetClan timezone assumption: review contract --------------------------
# The UTC+1 reading was measured on 2026-09-24/25 only. If BetClan actually
# runs Europe/London rather than a fixed offset, Britain leaving BST on
# 2026-10-25 widens the gap to 2h — every kickoff would be read an hour
# early, and auto_tickets' kickoff_guard would stake matches already in
# progress. Nothing here changes behaviour; it makes the expiry date of the
# assumption a first-class, testable object instead of a docstring aside.
BETCLAN_TZ_REVIEW_DATE = date(2026, 10, 25)
BETCLAN_TZ_MEASURED_ON = (date(2026, 9, 24), date(2026, 9, 25))
# Offset (minutes) between a BetClan-derived SAST kickoff and a reference
# SAST kickoff for the same fixture. Both are already converted, so agreement
# means zero; anything at or beyond this is a broken assumption, not jitter.
BETCLAN_DRIFT_ALARM_MINUTES = 30

STATUS_OK = "OK"
STATUS_REVIEW_DUE = "REVIEW_DUE"
STATUS_DRIFT = "DRIFT"
STATUS_UNKNOWN = "UNKNOWN"


def betclan_tz_status(today: date | None = None,
                      measured_offset_minutes: float | None = None,
                      sample_size: int = 0) -> dict:
    """Health of the BetClan timezone assumption. Never raises.

    Fail-safe UNKNOWN: with no measurement the answer is "we do not know",
    which is louder than a green tick and honest. DRIFT beats REVIEW_DUE —
    evidence of breakage outranks a calendar reminder.
    """
    today = today or datetime.now(SAST).date()
    zone = str(getattr(_betclan_tz(), "key", BETCLAN_TZ_DEFAULT))
    out = {
        "assumed_tz": zone,
        "review_date": BETCLAN_TZ_REVIEW_DATE.isoformat(),
        "days_to_review": (BETCLAN_TZ_REVIEW_DATE - today).days,
        "sample_size": int(sample_size or 0),
        "measured_offset_minutes": measured_offset_minutes,
        "alarm_threshold_minutes": BETCLAN_DRIFT_ALARM_MINUTES,
        "status": STATUS_UNKNOWN,
        "detail": "",
    }
    if measured_offset_minutes is not None and sample_size > 0:
        if abs(float(measured_offset_minutes)) >= BETCLAN_DRIFT_ALARM_MINUTES:
            out["status"] = STATUS_DRIFT
            out["detail"] = (
                f"BetClan kickoffs sit {float(measured_offset_minutes):+.0f} min from the "
                f"reference feed over {sample_size} matched fixture(s). The {zone} "
                f"assumption is wrong; set RACKET_FACTORY_BETCLAN_TZ to correct it."
            )
            return out
        out["status"] = STATUS_OK
        out["detail"] = (
            f"offset {float(measured_offset_minutes):+.0f} min over {sample_size} "
            f"matched fixture(s) — within tolerance"
        )
        return out
    if today >= BETCLAN_TZ_REVIEW_DATE:
        out["status"] = STATUS_REVIEW_DUE
        out["detail"] = (
            f"{BETCLAN_TZ_REVIEW_DATE.isoformat()} has passed (Britain left BST) and no "
            f"cross-source kickoff measurement is available. The {zone} assumption is "
            f"unverified for the current date — treat every kickoff as suspect."
        )
        return out
    out["detail"] = (
        f"no cross-source measurement available; assumption {zone} unverified since "
        f"{BETCLAN_TZ_MEASURED_ON[-1].isoformat()}, review due in "
        f"{out['days_to_review']} day(s)"
    )
    return out


def minutes_between_wall_times(a: str, b: str) -> int | None:
    """Signed minutes from wall time ``b`` to wall time ``a`` ("HH:MM").

    Wrapped to (-720, 720] so a 23:50 vs 00:10 pair reads as -20 minutes
    rather than a spurious 23h40 gap.
    """
    def _mins(text: str) -> int | None:
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(text or ""))
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour * 60 + minute

    left, right = _mins(a), _mins(b)
    if left is None or right is None:
        return None
    delta = (left - right) % 1440
    if delta > 720:
        delta -= 1440
    return delta

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
