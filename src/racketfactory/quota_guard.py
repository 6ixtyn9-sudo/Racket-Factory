"""Quota guard for paid/rate-limited APIs.

Bzzoiro is 100 req/day free — not a paywall, but a small tank that burns
quickly if we re-crawl. Jina relay is also limited. This module tracks
daily spend via small JSON files under localdata/quota/, logs usage, and
blocks further calls when budget is exhausted or 402 is observed.

Design:
- Each service has a daily counter file: localdata/quota/<service>_<YYYY-MM-DD>.json
- Content: {"date": "...", "service": "...", "count": N, "blocked": bool, "last_402": iso|None}
- Env overrides: RACKET_FACTORY_QUOTA_BZZOIRO (default 100), RACKET_FACTORY_QUOTA_JINA (default 1000, count-only)
- When count >= limit, further calls are skipped (no HTTP).
- On 402, we mark blocked=True and log with current spend to diagnose account vs quota.

Usage:
    from racketfactory.quota_guard import check_and_spend, record_402, get_count, log_quota

    if not check_and_spend("bzzoiro", limit=100):
        # skip
    else:
        # do request, if 402 -> record_402("bzzoiro")

"""
from __future__ import annotations
import json
import os
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_QUOTA_DIR = ROOT / "localdata" / "quota"

def quota_dir() -> Path:
    p = Path(os.getenv("RACKET_FACTORY_QUOTA_DIR", str(DEFAULT_QUOTA_DIR)))
    p.mkdir(parents=True, exist_ok=True)
    return p

def _quota_path(service: str, day: Optional[str] = None) -> Path:
    d = (day or date.today().isoformat())[:10]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in service.lower())
    return quota_dir() / f"{safe}_{d}.json"

def _load(service: str, day: Optional[str] = None) -> dict:
    path = _quota_path(service, day)
    if not path.exists():
        return {"date": (day or date.today().isoformat())[:10], "service": service, "count": 0, "blocked": False, "last_402": None}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {"date": (day or date.today().isoformat())[:10], "service": service, "count": 0, "blocked": False, "last_402": None}
        return data
    except Exception:
        return {"date": (day or date.today().isoformat())[:10], "service": service, "count": 0, "blocked": False, "last_402": None}

def _save(service: str, payload: dict, day: Optional[str] = None) -> None:
    path = _quota_path(service, day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except Exception as exc:
        logger.debug("quota guard write failed for %s: %s", service, exc)

def get_count(service: str, day: Optional[str] = None) -> int:
    return int(_load(service, day).get("count", 0) or 0)

def is_blocked(service: str, day: Optional[str] = None) -> bool:
    return bool(_load(service, day).get("blocked", False))

def get_limit(service: str) -> int:
    service_l = service.lower()
    if "bzzoiro" in service_l:
        try:
            return int(os.getenv("RACKET_FACTORY_QUOTA_BZZOIRO", "100"))
        except Exception:
            return 100
    if "jina" in service_l:
        try:
            return int(os.getenv("RACKET_FACTORY_QUOTA_JINA", "1000"))
        except Exception:
            return 1000
    # generic
    try:
        return int(os.getenv(f"RACKET_FACTORY_QUOTA_{service_l.upper()}", "100"))
    except Exception:
        return 100

def check_and_spend(service: str, n: int = 1, day: Optional[str] = None) -> bool:
    """Return True if budget allows and increment, False if blocked/exhausted."""
    limit = get_limit(service)
    data = _load(service, day)
    count = int(data.get("count", 0) or 0)
    blocked = bool(data.get("blocked", False))
    if blocked:
        logger.warning("quota %s: blocked today (402), count %d/%d — skipping call", service, count, limit)
        return False
    if count + n > limit:
        logger.warning("quota %s: %d/%d today — budget exhausted, skipping call", service, count, limit)
        return False
    data["count"] = count + n
    _save(service, data, day)
    logger.info("quota %s: %d/%d today", service, data["count"], limit)
    return True

def record_402(service: str, day: Optional[str] = None) -> None:
    """Mark service as blocked for today after 402, preserving count for diagnostics."""
    from datetime import datetime, timezone
    data = _load(service, day)
    data["blocked"] = True
    data["last_402"] = datetime.now(timezone.utc).isoformat()
    _save(service, data, day)
    limit = get_limit(service)
    count = int(data.get("count", 0) or 0)
    logger.warning(
        "quota %s: 402 received — our spend today: %d/%d — if under budget, the token's free trial/plan state is the likely cause (check the account)",
        service, count, limit
    )

def log_quota(service: str, day: Optional[str] = None) -> None:
    data = _load(service, day)
    limit = get_limit(service)
    logger.info("quota %s: %d/%d today (blocked=%s)", service, data.get("count", 0), limit, data.get("blocked", False))

def reset_if_new_day(service: str) -> None:
    # No-op: file is per-day, so new day automatically resets. Kept for API symmetry.
    pass
