#!/usr/bin/env python3
"""Probe bzzoiro odds API capability without printing secrets or full payloads.

This is a diagnostic tool, not an ingest path. It intentionally prints compact
metadata only: endpoint status, top-level JSON shape, list counts, sample keys,
and discovered event ids. It never prints BZZOIRO_TOKEN or full response bodies.

Usage:
    PYTHONPATH=src python scripts/probe_bzzoiro_odds.py
    PYTHONPATH=src python scripts/probe_bzzoiro_odds.py --date 2026-06-16 --days 7
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - requirements includes python-dotenv
    load_dotenv = None

if load_dotenv:
    load_dotenv()

TOKEN = os.environ.get("BZZOIRO_TOKEN")
BASE_V2 = "https://sports.bzzoiro.com/tennis/api/v2"
BASE_V1 = "https://sports.bzzoiro.com/tennis/api"
MARKETS = ("1x2", "over_under_25", "btts")


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str


def _qs(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(params)


def build_probe_endpoints(day: str, days: int) -> list[Endpoint]:
    """Build the static endpoint set to probe for one date window."""
    target = date.fromisoformat(day)
    end = (target + timedelta(days=days)).isoformat()
    endpoints = [
        Endpoint("v2 bookmakers", f"{BASE_V2}/bookmakers/?limit=5"),
        Endpoint("v2 odds best default", f"{BASE_V2}/odds/best/?limit=5"),
        Endpoint(
            "v2 predictions sample",
            f"{BASE_V2}/predictions/?{_qs({'limit': 3})}",
        ),
        Endpoint(
            "v2 events date-window sample",
            f"{BASE_V2}/events/?{_qs({'date_from': day, 'date_to': end, 'limit': 3})}",
        ),
    ]
    for market in MARKETS:
        endpoints.append(
            Endpoint(
                f"v2 odds best market={market}",
                f"{BASE_V2}/odds/best/?{_qs({'market': market, 'date_from': day, 'date_to': end, 'limit': 5})}",
            )
        )
    for market in MARKETS:
        endpoints.append(
            Endpoint(
                f"v1 odds best market={market}",
                f"{BASE_V1}/odds/best/?{_qs({'market': market, 'days': days})}",
            )
        )
    return endpoints


def request_json(url: str, token: str | None, timeout: int) -> dict[str, Any]:
    """Return compact request result with parsed JSON or sanitized error."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(750_000).decode("utf-8", "replace")
            try:
                data = json.loads(body) if body else None
                parse_error = None
            except json.JSONDecodeError as exc:
                data = None
                parse_error = f"JSONDecodeError: {exc}"
            return {
                "ok": 200 <= resp.status < 400,
                "status": resp.status,
                "content_type": resp.headers.get("content-type", ""),
                "data": data,
                "parse_error": parse_error,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        # Keep only a tiny, token-free body snippet for diagnostics.
        snippet = ""
        try:
            snippet = exc.read(300).decode("utf-8", "replace").strip()
        except Exception:
            pass
        return {
            "ok": False,
            "status": exc.code,
            "content_type": exc.headers.get("content-type", "") if exc.headers else "",
            "data": None,
            "parse_error": None,
            "error": f"HTTPError: {exc.code} {exc.reason}",
            "body_snippet": snippet[:300],
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "content_type": "",
            "data": None,
            "parse_error": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _short_keys(obj: Any, limit: int = 18) -> list[str]:
    if not isinstance(obj, dict):
        return []
    return [str(k) for k in list(obj.keys())[:limit]]


def summarize_payload(data: Any) -> dict[str, Any]:
    """Summarize JSON shape without exposing full payload content."""
    if data is None:
        return {"json_type": "none"}

    if isinstance(data, list):
        first = data[0] if data and isinstance(data[0], dict) else None
        summary: dict[str, Any] = {
            "json_type": "list",
            "list_len": len(data),
            "sample_keys": _short_keys(first),
        }
        _add_sample_nested_summary(summary, first)
        return summary

    if isinstance(data, dict):
        summary = {
            "json_type": "dict",
            "top_keys": _short_keys(data),
            "list_counts": {
                str(k): len(v) for k, v in data.items() if isinstance(v, list)
            },
            "dict_keys": {
                str(k): _short_keys(v) for k, v in data.items() if isinstance(v, dict)
            },
        }
        sample = _first_sample_item(data)
        summary["sample_keys"] = _short_keys(sample)
        _add_sample_nested_summary(summary, sample)
        return summary

    return {"json_type": type(data).__name__}


def _first_sample_item(data: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("results", "events", "data", "odds", "bookmakers"):
        value = data.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return None


def _add_sample_nested_summary(summary: dict[str, Any], sample: dict[str, Any] | None) -> None:
    if not isinstance(sample, dict):
        return
    if isinstance(sample.get("event"), dict):
        summary["sample_event_keys"] = _short_keys(sample["event"])
    if isinstance(sample.get("comparison"), dict):
        summary["sample_comparison_keys"] = _short_keys(sample["comparison"])
    if isinstance(sample.get("best_odds"), list):
        summary["sample_best_odds_len"] = len(sample["best_odds"])
        if sample["best_odds"] and isinstance(sample["best_odds"][0], dict):
            summary["sample_best_odds_keys"] = _short_keys(sample["best_odds"][0])
    if isinstance(sample.get("markets"), dict):
        summary["sample_markets_keys"] = _short_keys(sample["markets"])


def extract_event_ids(data: Any, limit: int = 5) -> list[str]:
    """Extract event ids from common response shapes for subresource probing."""
    ids: list[str] = []

    def add(value: Any) -> None:
        if value is not None and str(value) not in ids:
            ids.append(str(value))

    def visit(obj: Any) -> None:
        if len(ids) >= limit:
            return
        if isinstance(obj, dict):
            if "event_id" in obj:
                add(obj.get("event_id"))
            if "id" in obj and any(k in obj for k in ("home_team", "away_team", "event_date")):
                add(obj.get("id"))
            ev = obj.get("event")
            if isinstance(ev, dict):
                add(ev.get("id"))
            for key in ("results", "events", "data", "odds"):
                if isinstance(obj.get(key), list):
                    for item in obj[key][:limit]:
                        visit(item)
        elif isinstance(obj, list):
            for item in obj[:limit]:
                visit(item)

    visit(data)
    return ids[:limit]


def probe_endpoint(
    endpoint: Endpoint,
    token: str | None,
    timeout: int,
    requester: Callable[[str, str | None, int], dict[str, Any]] = request_json,
) -> dict[str, Any]:
    result = requester(endpoint.url, token, timeout)
    data = result.get("data")
    return {
        "name": endpoint.name,
        "url": endpoint.url,
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "content_type": result.get("content_type", ""),
        "error": result.get("error"),
        "parse_error": result.get("parse_error"),
        "body_snippet": result.get("body_snippet"),
        "summary": summarize_payload(data),
        "event_ids": extract_event_ids(data),
        "_data": data,  # internal only; removed before JSON output
    }


def format_probe(result: dict[str, Any]) -> str:
    status = result.get("status")
    mark = "OK" if result.get("ok") else "ERR"
    lines = [f"[{mark}] {result['name']}  status={status}", f"  url={result['url']}"]
    if result.get("error"):
        lines.append(f"  error={result['error']}")
    if result.get("parse_error"):
        lines.append(f"  parse_error={result['parse_error']}")
    if result.get("body_snippet"):
        lines.append(f"  body_snippet={result['body_snippet']}")
    summary = result.get("summary", {})
    lines.append(f"  json_type={summary.get('json_type')}")
    if summary.get("top_keys"):
        lines.append(f"  top_keys={summary['top_keys']}")
    if summary.get("list_counts"):
        lines.append(f"  list_counts={summary['list_counts']}")
    if summary.get("list_len") is not None:
        lines.append(f"  list_len={summary['list_len']}")
    if summary.get("sample_keys"):
        lines.append(f"  sample_keys={summary['sample_keys']}")
    for key in ("sample_event_keys", "sample_comparison_keys", "sample_markets_keys"):
        if summary.get(key):
            lines.append(f"  {key}={summary[key]}")
    if summary.get("sample_best_odds_len") is not None:
        lines.append(f"  sample_best_odds_len={summary['sample_best_odds_len']}")
    if summary.get("sample_best_odds_keys"):
        lines.append(f"  sample_best_odds_keys={summary['sample_best_odds_keys']}")
    if result.get("event_ids"):
        lines.append(f"  event_ids={result['event_ids']}")
    return "\n".join(lines)


def _subresource_endpoints(event_ids: list[str]) -> list[Endpoint]:
    out = []
    for eid in event_ids[:3]:
        out.append(Endpoint(f"v2 event {eid} odds comparison", f"{BASE_V2}/events/{eid}/odds/comparison/"))
        out.append(Endpoint(f"v2 event {eid} polymarket", f"{BASE_V2}/events/{eid}/polymarket/"))
    return out


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Drop internal raw payload before optional JSON output."""
    return {k: v for k, v in result.items() if k != "_data"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe bzzoiro odds API capability")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD start date")
    parser.add_argument("--days", type=int, default=7, help="date window in days / v1 days parameter")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument("--json", action="store_true", help="emit compact JSON instead of text")
    args = parser.parse_args()

    print(f"BZZOIRO_TOKEN present: {'yes' if TOKEN else 'no'}", file=sys.stderr if args.json else sys.stdout)
    print(f"Probe date={args.date} days={args.days}", file=sys.stderr if args.json else sys.stdout)

    results = []
    event_ids: list[str] = []
    for endpoint in build_probe_endpoints(args.date, args.days):
        result = probe_endpoint(endpoint, TOKEN, args.timeout)
        results.append(result)
        for eid in result.get("event_ids", []):
            if eid not in event_ids:
                event_ids.append(eid)

    for endpoint in _subresource_endpoints(event_ids):
        results.append(probe_endpoint(endpoint, TOKEN, args.timeout))

    if args.json:
        print(json.dumps([public_result(r) for r in results], indent=2, sort_keys=True))
    else:
        for result in results:
            print(format_probe(result))
            print("-" * 72)
        if not event_ids:
            print("No event ids discovered for subresource probes.")
        print("Done. No token or full payload was printed.")

    # Diagnostic-only: endpoint failures are reported but do not fail CI/nightly.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
