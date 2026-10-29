"""Conservative daily request accounting for rate-limited providers.

RACKET_FACTORY_QUOTA_DIR must point at an operator-managed durable directory,
shared by all writers. Each service/day file must already contain an
independently reconciled count. Missing or corrupt state never means zero.
A local lock and atomic, fsynced replacement reserve each request before HTTP;
they do not make an ephemeral runner or Actions cache durable.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
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
    d = (day or date.today().isoformat())[:10]
    unknown = {"date": d, "service": service, "count": 0, "blocked": True,
               "authority_missing": True, "last_402": None}
    configured = os.getenv("RACKET_FACTORY_QUOTA_DIR", "").strip()
    if not configured:
        return unknown
    try:
        # Git-tracked state and evictable localdata caches are not authority.
        if Path(configured).resolve().is_relative_to((ROOT / "localdata").resolve()):
            return unknown
        data = json.loads(_quota_path(service, day).read_text())
        if (not isinstance(data, dict) or data.get("date") != d or
                str(data.get("service", "")).lower() != service.lower() or
                type(data.get("count")) is not int or data["count"] < 0 or
                type(data.get("blocked")) is not bool):
            return unknown
        return data
    except (OSError, ValueError, TypeError):
        return unknown


def _save(service: str, payload: dict, day: Optional[str] = None) -> None:
    path = _quota_path(service, day)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            json.dump(payload, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # Persist the directory entry before allowing a request.
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def _locked(service: str, day: Optional[str] = None):
    path = _quota_path(service, day)
    with open(str(path) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def get_count(service: str, day: Optional[str] = None) -> int:
    return int(_load(service, day).get("count", 0))


def is_blocked(service: str, day: Optional[str] = None) -> bool:
    return bool(_load(service, day).get("blocked", True))


def get_limit(service: str) -> int:
    service_l = service.lower()
    default = 1000 if "jina" in service_l else 100
    key = "JINA" if "jina" in service_l else "BZZOIRO" if "bzzoiro" in service_l else service_l.upper()
    try:
        return int(os.getenv(f"RACKET_FACTORY_QUOTA_{key}", str(default)))
    except ValueError:
        return default


def check_and_spend(service: str, n: int = 1, day: Optional[str] = None) -> bool:
    """Reserve n requests before HTTP; deny absent, corrupt or unwritable state."""
    if type(n) is not int or n <= 0:
        return False
    if not os.getenv("RACKET_FACTORY_QUOTA_DIR", "").strip():
        logger.error("quota %s: no durable accounting directory configured; request denied", service)
        return False
    try:
        with _locked(service, day):
            data = _load(service, day)
            if data.get("authority_missing"):
                logger.error("quota %s: durable accounting missing or corrupt; request denied", service)
                return False
            count, limit = data["count"], get_limit(service)
            if data["blocked"] or count + n > limit:
                logger.warning("quota %s: blocked or exhausted (%d/%d); request denied", service, count, limit)
                return False
            data["count"] = count + n
            _save(service, data, day)
            logger.info("quota %s: %d/%d today", service, data["count"], limit)
            return True
    except (OSError, ValueError, TypeError) as exc:
        logger.error("quota %s: reservation could not be persisted; request denied: %s", service, exc)
        return False


def record_402(service: str, day: Optional[str] = None) -> None:
    if not os.getenv("RACKET_FACTORY_QUOTA_DIR", "").strip():
        raise RuntimeError("durable quota directory not configured")
    with _locked(service, day):
        data = _load(service, day)
        if data.get("authority_missing"):
            raise RuntimeError(f"quota {service}: cannot persist 402 without authority")
        data["blocked"] = True
        data["last_402"] = datetime.now(timezone.utc).isoformat()
        _save(service, data, day)
    logger.warning("quota %s: 402 received — our spend today: %d/%d; check provider account and entitlement", service, data["count"], get_limit(service))


def log_quota(service: str, day: Optional[str] = None) -> None:
    data = _load(service, day)
    logger.info("quota %s: %d/%d today (blocked=%s, authority_missing=%s)", service, data.get("count", 0), get_limit(service), data.get("blocked"), data.get("authority_missing", False))


def reset_if_new_day(service: str) -> None:
    # A missing new-day ledger is not proof that nothing has been spent.
    pass


def _failure_path(service: str, credential: str) -> Path:
    import hashlib
    fingerprint = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    safe = "".join(c for c in service.lower() if c.isalnum() or c in "-_")
    # OddsPortal cooldown markers are non-financial and may survive in the
    # existing disposable marker cache; quota records never derive from them.
    ext = "marker" if safe == "oddsportal" else "json"
    return quota_dir() / f"{safe}_failure_{fingerprint}.{ext}"


def cooldown_status(service: str, credential: str) -> dict | None:
    """An active credential-scoped failure, or None once it expires."""
    try:
        path = _failure_path(service, credential)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        until = float(data["until"])
        if data.get("kind") not in ("auth", "rate_limit", "entitlement", "route_invalid"):
            raise ValueError("unknown failure type")
        return data if until > datetime.now(timezone.utc).timestamp() else None
    except (OSError, ValueError, TypeError, KeyError):
        return {"kind": "invalid_failure_state"}


def record_cooldown(service: str, credential: str, kind: str, seconds: float) -> None:
    """Persist a bounded failure without ever storing the credential."""
    import math
    if kind not in ("auth", "rate_limit", "entitlement", "route_invalid") or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid cooldown")
    path = _failure_path(service, credential)
    with _locked(service):
        until = datetime.now(timezone.utc).timestamp() + seconds
        previous = cooldown_status(service, credential)
        if previous and previous.get("kind") == "invalid_failure_state":
            raise RuntimeError("invalid persisted cooldown; repair required")
        if previous and previous.get("until", 0) > until:
            return  # never shorten a provider's already active cooldown
        payload = {"kind": kind, "until": until}
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
                tmp = Path(f.name)
                json.dump(payload, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)


def retry_after_seconds(headers, *, default: int = 900) -> float:
    """Respect either Retry-After seconds or an HTTP-date; never cap early."""
    from email.utils import parsedate_to_datetime
    raw = str(headers.get("Retry-After", "") or "").strip() if headers else ""
    try:
        import math
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("invalid Retry-After")
        return max(1.0, value)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(1.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return float(default)


def _odds_credit_identity(api_key: str) -> tuple[str, str]:
    import hashlib
    fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"theoddsapi_{fingerprint}", datetime.now(timezone.utc).strftime("%Y-%m-01")


def _odds_max_cost(min_cost: int) -> int | None:
    """An operator-verified maximum cost is required; never assume a budget."""
    try:
        cap = int(os.environ["THE_ODDS_API_MAX_CREDITS_PER_CALL"])
        return cap if cap >= min_cost and cap > 0 else None
    except (KeyError, ValueError):
        return None


def odds_credit_available(api_key: str, *, min_cost: int = 1, reserve: int = 25) -> bool:
    cap = _odds_max_cost(min_cost)
    if not api_key or cap is None or not os.getenv("RACKET_FACTORY_QUOTA_DIR", "").strip():
        return False
    if cooldown_status("theoddsapi", api_key):
        return False
    service, day = _odds_credit_identity(api_key)
    try:
        data = _load(service, day)
        remaining = data.get("remaining_credits")
        return (not data.get("authority_missing") and not data.get("blocked") and
                type(remaining) is int and remaining - cap >= reserve)
    except (OSError, ValueError, TypeError):
        return False


def reserve_odds_credits(api_key: str, *, min_cost: int = 1, reserve: int = 25) -> bool:
    """Pessimistically debit the verified per-call maximum before any HTTP.

    The external ledger must be seeded from independently verified provider
    remaining credits. A crash consumes the whole reservation, never refunds
    it. This is conservative local accounting, not a claim of actual spend.
    """
    cap = _odds_max_cost(min_cost)
    if not api_key or cap is None or not os.getenv("RACKET_FACTORY_QUOTA_DIR", "").strip():
        logger.error("The Odds API: missing credit authority or inadequate verified per-call bound")
        return False
    if cooldown_status("theoddsapi", api_key):
        logger.warning("The Odds API: provider cooldown active; request denied")
        return False
    service, day = _odds_credit_identity(api_key)
    try:
        with _locked(service, day):
            data = _load(service, day)
            remaining = data.get("remaining_credits")
            if (data.get("authority_missing") or data.get("blocked") or type(remaining) is not int
                    or remaining - cap < reserve):
                logger.error("The Odds API: durable credit record absent, invalid, blocked or below reserve")
                return False
            data["remaining_credits"] = remaining - cap
            data["count"] += 1
            _save(service, data, day)
            return True
    except (OSError, ValueError, TypeError) as exc:
        logger.error("The Odds API: credit reservation not persisted; request denied: %s", exc)
        return False


def reconcile_odds_credits(api_key: str, headers) -> None:
    """Only lower the conservative balance; halt if actual cost exceeds the bound."""
    try:
        remaining_raw = headers.get("x-requests-remaining")
        last_raw = headers.get("x-requests-last")
        remaining = int(remaining_raw) if remaining_raw is not None else None
        last = int(last_raw) if last_raw is not None else None
        service, day = _odds_credit_identity(api_key)
        with _locked(service, day):
            data = _load(service, day)
            old = data.get("remaining_credits")
            if data.get("authority_missing") or type(old) is not int:
                return
            changed = False
            if remaining is not None and 0 <= remaining < old:
                data["remaining_credits"] = remaining
                changed = True
            cap = _odds_max_cost(1)
            if last is not None and cap is not None and last > cap:
                data["blocked"] = True
                data["block_reason"] = "provider_cost_exceeded_verified_bound"
                changed = True
                logger.error("The Odds API: reported request cost exceeded the configured bound; further requests blocked")
            if changed:
                _save(service, data, day)
    except (OSError, ValueError, TypeError, AttributeError):
        # Reservation already accounts for the maximum even when headers or
        # subsequent persistence are unavailable.
        logger.warning("The Odds API: provider credit reconciliation unavailable")
