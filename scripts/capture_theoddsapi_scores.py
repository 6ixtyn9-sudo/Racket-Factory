#!/usr/bin/env python3
"""Capture completed tennis results from The Odds API scores endpoint.

Writes normalized warehouse-compatible result rows to:

    localdata/theoddsapi_scores_tennis_YYYY-MM.csv.gz

This is for result settlement, not betting advice. It never prints API keys.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
LOCALDATA = ROOT / "localdata"
BASE = "https://api.the-odds-api.com/v4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("capture_theoddsapi_scores")


def load_env() -> None:
    for p in (ROOT / ".env", LOCALDATA / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


def sports_from_env() -> list[str]:
    raw = (
        os.getenv("THE_ODDS_API_SCORE_SPORT_KEYS")
        or os.getenv("THE_ODDS_API_SPORT_KEYS")
        or os.getenv("THE_ODDS_API_SPORTS")
        or "tennis_atp,tennis_wta"
    )
    # The generic "tennis" key may work for odds but does not work for scores.
    return [s.strip() for s in raw.split(",") if s.strip() and s.strip() != "tennis"]


def local_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return text[:10]


def infer_tour_title(sport_key: str, sport_title: str) -> tuple[str, str]:
    text = f"{sport_key} {sport_title}".lower()
    tour = "WTA" if "wta" in text else ("ATP" if "atp" in text else "UNKNOWN")
    title = sport_title or sport_key
    if "wimbledon" in text:
        return tour, "Wimbledon"
    if "french" in text:
        return tour, "French Open"
    if "aus" in text or "australian" in text:
        return tour, "Australian Open"
    if "us_open" in text or "us open" in text:
        return tour, "US Open"
    return tour, title


def _scores_cache_path(sport: str, days_from: int, day: str) -> Path:
    safe_sport = "".join(c if c.isalnum() or c in "-_" else "_" for c in sport)
    return LOCALDATA / f"theoddsapi_scores_cache_{safe_sport}_{days_from}d_{day}.json"


def _scores_cache_ttl_minutes() -> int:
    try:
        return max(0, int(os.getenv("THE_ODDS_API_SCORES_CACHE_MINUTES", "60")))
    except ValueError:
        return 60


def _load_scores_cache(sport: str, days_from: int, day: str) -> list[dict[str, Any]] | None:
    ttl = _scores_cache_ttl_minutes()
    if ttl <= 0:
        return None
    path = _scores_cache_path(sport, days_from, day)
    if not path.exists():
        return None
    try:
        import time
        if time.time() - path.stat().st_mtime > ttl * 60:
            return None
        payload = json.loads(path.read_text())
        rows = payload.get("events", [])
        if isinstance(rows, list):
            logger.info("%s scores loaded from cache (%d events)", sport, len(rows))
            return rows
    except Exception as exc:
        logger.warning("Could not read scores cache %s: %s", path, exc)
    return None


def _write_scores_cache(sport: str, days_from: int, day: str, events: list[dict[str, Any]]) -> None:
    if _scores_cache_ttl_minutes() <= 0:
        return
    try:
        path = _scores_cache_path(sport, days_from, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sport": sport, "events": events}))
    except Exception as exc:
        logger.warning("Could not write scores cache for %s: %s", sport, exc)


def fetch_scores(sport: str, days_from: int, api_key: str) -> list[dict[str, Any]]:
    from datetime import date as _date
    cached = _load_scores_cache(sport, days_from, _date.today().isoformat())
    if cached is not None:
        return cached
    params = {
        "apiKey": api_key,
        "daysFrom": str(days_from),
        "dateFormat": "iso",
    }
    url = f"{BASE}/sports/{sport}/scores?" + urlencode(params)
    safe = f"{BASE}/sports/{sport}/scores?" + urlencode({**params, "apiKey": "***"})
    try:
        req = Request(url, headers={"User-Agent": "RacketFactoryScores/1.0"})
        with urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, list):
                logger.warning("%s returned non-list payload", sport)
                return []
            logger.info(
                "%s scores fetched: %d | remaining=%s used=%s last=%s",
                sport,
                len(payload),
                resp.headers.get("x-requests-remaining"),
                resp.headers.get("x-requests-used"),
                resp.headers.get("x-requests-last"),
            )
            try:
                sys.path.insert(0, str(ROOT / "src"))
                from racketfactory.warehouse import record_odds_api_usage
                record_odds_api_usage(api_key, resp.headers, endpoint=f"scores:{sport}")
            except Exception as exc:
                logger.warning("Usage ledger write failed: %s", exc)
            _write_scores_cache(sport, days_from, _date.today().isoformat(), payload)
            return payload
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        logger.warning("%s scores HTTP %s: %s | %s", sport, exc.code, body, safe)
        return []
    except (URLError, TimeoutError, Exception) as exc:
        logger.warning("%s scores fetch failed: %s | %s", sport, exc, safe)
        return []


def row_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if not event.get("completed"):
        return None

    home = str(event.get("home_team") or "").strip()
    away = str(event.get("away_team") or "").strip()
    if not home or not away:
        return None

    scores = event.get("scores") or []
    if not isinstance(scores, list) or len(scores) < 2:
        return None

    score_map: dict[str, float] = {}
    for item in scores:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        try:
            val = float(str(item.get("score")).strip())
        except Exception:
            continue
        if name:
            score_map[name] = val

    if home not in score_map or away not in score_map:
        return None

    hs = score_map[home]
    as_ = score_map[away]
    if hs == as_:
        return None

    winner = home if hs > as_ else away
    tour, tournament = infer_tour_title(str(event.get("sport_key") or ""), str(event.get("sport_title") or ""))

    return {
        "match_date": local_date(event.get("commence_time")),
        "tour": tour,
        "tournament": tournament,
        "round": "",
        "player_a": home,
        "player_b": away,
        "winner": winner,
        # Sets-only feed: no per-set strings exist, so score stays empty and
        # the S evidence travels in _sets_a/_sets_b (settlement finality).
        "score": "",
        "_sets_a": int(hs),
        "_sets_b": int(as_),
        "_result_status": "COMPLETED_FEED",
        "odds_a": pd.NA,
        "odds_b": pd.NA,
        "bookmaker": "",
        "source": "TheOddsAPI_scores",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "oddsportal_url": "",
        "_surface": "Grass" if "wimbledon" in str(event.get("sport_key", "")).lower() else "",
        "_court": "",
        "_series": "Grand Slam" if "wimbledon" in str(event.get("sport_key", "")).lower() else "",
        "_comment": "result_from_theoddsapi_scores",
        "_location": "",
        "_winner_rank": pd.NA,
        "_loser_rank": pd.NA,
        "_odds_source": "",
        "_is_live": False,
        "_api_event_id": event.get("id"),
        "_api_sport_key": event.get("sport_key"),
        "_api_last_update": event.get("last_update"),
    }


def write_monthly(rows: list[dict[str, Any]], output_dir: Path) -> list[Path]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["match_date"] = df["match_date"].astype(str).str[:10]
    written: list[Path] = []
    for month, group in df.groupby(df["match_date"].str[:7]):
        path = output_dir / f"theoddsapi_scores_tennis_{month}.csv.gz"
        if path.exists():
            old = pd.read_csv(path, low_memory=False)
            combined = pd.concat([old, group], ignore_index=True, sort=False)
        else:
            combined = group.copy()

        key_cols = ["match_date", "tour", "tournament", "player_a", "player_b"]
        if "_api_event_id" in combined.columns and combined["_api_event_id"].notna().any():
            key_cols = ["_api_event_id"]
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
        combined.to_csv(path, index=False, compression="gzip")
        written.append(path)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture completed tennis scores from The Odds API.")
    ap.add_argument("--days-from", type=int, default=3, help="Recent days to request from scores endpoint.")
    ap.add_argument("--output-dir", default=str(LOCALDATA), help="Output directory.")
    args = ap.parse_args()

    load_env()
    sys.path.insert(0, str(ROOT / "src"))
    from racketfactory.warehouse import (
        discover_active_tennis_sports,
        odds_api_budget_ok,
        the_odds_api_keys,
    )
    api_keys = the_odds_api_keys()
    if not api_keys:
        logger.warning("THE_ODDS_API_KEY missing; cannot fetch scores.")
        return 0
    if not odds_api_budget_ok(api_keys):
        return 0
    sports = sports_from_env()
    active = discover_active_tennis_sports(api_keys)
    if active is not None:
        active_set = set(active)
        logger.info("The Odds API active score keys discovered: %s", active)
        skipped = [s for s in sports if s not in active_set]
        sports = [s for s in sports if s in active_set]
        for sport in skipped:
            logger.warning("Scores sport key %r is not active; skipping", sport)
        if not sports:
            if active:
                logger.warning("No active configured score sport keys; falling back to discovered: %s", active)
                sports = active
            else:
                logger.warning("No active score sport keys; skipping scores fetch.")
                return 0

    all_rows: list[dict[str, Any]] = []
    for i, sport in enumerate(sports):
        events = fetch_scores(sport, args.days_from, api_keys[i % len(api_keys)])
        for event in events:
            row = row_from_event(event)
            if row:
                all_rows.append(row)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = write_monthly(all_rows, out_dir)

    logger.info("completed score rows normalized: %d", len(all_rows))
    for p in written:
        logger.info("wrote %s", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
