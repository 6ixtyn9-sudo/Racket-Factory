"""Same-job disk cache for live prediction-site fetches.

Why: one daily job crawls BetClan / PredixSport / Forebet / Bzzoiro up to
five times (captures + three warehouse builds + miner fallback card). Those
redundant full-site crawls from a single runner IP invite rate limiting and
bot blocks, which starve the LATER builds of live rows even when the EARLY
fetches succeeded (observed 2026-09-13: captures + first warehouse build had
live rows, final build + miner fallback had zero -> zero picks).

The cache stores raw fetcher output (JSON-serializable list of dicts) under
``localdata/fetch_cache/<source>_<fetch-date>.json`` with a TTL
(``RACKET_FACTORY_FETCH_CACHE_TTL_MINUTES``, default 45). Later callers in
the same job reuse the first successful crawl instead of re-hitting the site.

Safety rules:
  * Only NON-EMPTY results are cached. A transient failure (empty list) is
    returned as-is without poisoning the cache, so the next caller retries
    the live site (previous behaviour preserved).
  * Corrupt/unparseable cache files are ignored and refetched.
  * ``ttl_minutes <= 0`` (or env ``<= 0``) bypasses the cache entirely.
  * Cache files are deliberately NOT committed to git and NOT part of the
    warehouse input globs (JSON, not ``*.csv.gz``).

Cross-run reuse (rapid manual dispatches) needs one extra line in the
workflow cache paths (``localdata/fetch_cache``); without it the cache
still collapses the ~5 same-job crawls down to ~1 per source.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = ROOT / "localdata" / "fetch_cache"
TTL_ENV_VAR = "RACKET_FACTORY_FETCH_CACHE_TTL_MINUTES"
DEFAULT_TTL_MINUTES = 45.0


def cache_ttl_minutes() -> float:
    try:
        return float(os.getenv(TTL_ENV_VAR, str(DEFAULT_TTL_MINUTES)))
    except ValueError:
        return DEFAULT_TTL_MINUTES


def _safe_key(source: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(source or "unknown")).strip("._") or "unknown"


def cache_path(source: str, fetch_day: str | None = None,
               cache_dir: Path | None = None) -> Path:
    day = (fetch_day or date.today().isoformat())[:10]
    base = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    return base / f"fetch_{_safe_key(source)}_{day}.json"


def _cache_is_fresh(path: Path, ttl_minutes: float) -> bool:
    if ttl_minutes <= 0 or not path.exists():
        return False
    try:
        age_min = (time.time() - path.stat().st_mtime) / 60.0
    except OSError:
        return False
    return age_min < ttl_minutes


def read_cached_rows(source: str, fetch_day: str | None = None,
                     ttl_minutes: float | None = None,
                     cache_dir: Path | None = None) -> list[dict[str, Any]] | None:
    """Return cached rows, or None on any miss/expiry/corruption."""
    ttl = DEFAULT_TTL_MINUTES if ttl_minutes is None else ttl_minutes
    path = cache_path(source, fetch_day, cache_dir)
    if not _cache_is_fresh(path, ttl):
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Fetch cache unreadable for %s (%s); refetching: %s",
                       source, path, exc)
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    try:
        age_min = (time.time() - path.stat().st_mtime) / 60.0
    except OSError:
        age_min = -1.0
    logger.info("Fetch cache hit for %s: %d rows (age %.1f min, ttl %.0f)",
                source, len(rows), age_min, ttl)
    return rows


def write_cached_rows(source: str, rows: list[dict[str, Any]],
                      fetch_day: str | None = None,
                      cache_dir: Path | None = None) -> None:
    """Persist NON-EMPTY fetcher output. Empty lists are never cached."""
    if not rows:
        return
    path = cache_path(source, fetch_day, cache_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": str(source),
            "fetch_day": (fetch_day or date.today().isoformat())[:10],
            "cached_at": time.time(),
            "row_count": len(rows),
            "rows": rows,
        }
        path.write_text(json.dumps(payload, default=str))
        logger.info("Fetch cache stored for %s: %d rows -> %s",
                    source, len(rows), path)
    except Exception as exc:
        logger.warning("Fetch cache write failed for %s: %s", source, exc)


def cached_fetch(source: str, fetch_fn: Callable[[], list[dict[str, Any]]],
                 *, fetch_day: str | None = None,
                 ttl_minutes: float | None = None,
                 cache_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return ``fetch_fn()`` rows, reusing a fresh cache entry when present.

    Fetch exceptions propagate (callers keep their existing try/except
    behaviour); only successful non-empty results populate the cache.
    """
    ttl = cache_ttl_minutes() if ttl_minutes is None else ttl_minutes
    if ttl > 0:
        cached = read_cached_rows(source, fetch_day, ttl, cache_dir)
        if cached is not None:
            return cached
    rows = fetch_fn()
    if ttl > 0 and rows:
        write_cached_rows(source, rows, fetch_day, cache_dir)
    return rows
