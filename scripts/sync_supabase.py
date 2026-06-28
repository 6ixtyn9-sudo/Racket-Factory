#!/usr/bin/env python3
"""Sync certified edges + daily picks to Supabase for Racket Factory.

This script promotes the current edge slices and explicit tennis picks ledger
into Supabase for dashboards / app read models. It supports authoritative
replace-for-date syncing so stale same-day rows cannot survive downstream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.db import delete_picks_for_date, get_client, upsert_edges, upsert_picks  # noqa: E402

DEFAULT_PICKS = ROOT / "localdata" / "picks_today.json"

SPORT_ID = 2  # sports.key='tennis'
SOURCE_ID = 2  # base source for tennis
EVENT_SOURCE_KEY = "racketfactory_picks"


def _response_data(resp) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def load_picks_raw(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def load_edges_from_picks(picks: list[dict]) -> list[dict]:
    edges = {}
    for p in picks:
        slice_name = p.get("slice_matched")
        if not slice_name:
            continue
        if slice_name not in edges:
            edges[slice_name] = {
                "name": slice_name,
                "sport_id": SPORT_ID,
                "source_id": SOURCE_ID,
                "rule": {
                    "slice": slice_name,
                    "dims": p.get("edge_dims"),
                    "grade": p.get("edge_grade"),
                    "tier": p.get("edge_tier"),
                    "verdict": p.get("edge_verdict"),
                    "roi_estimate": p.get("roi_estimate"),
                },
                "status": "certified",
                "train_stats": {"n": p.get("edge_n"), "roi": p.get("roi_estimate")},
                "valid_stats": {},
                "decay_verdict": "unknown",
            }
    return list(edges.values())


def infer_target_date(picks: list[dict], fallback: str | None = None) -> str | None:
    if fallback:
        return fallback
    for p in picks:
        value = str(p.get("picked_for") or p.get("date") or "")[:10]
        if value:
            return value
    return None


def event_source_ref(pick: dict) -> str:
    sport = pick.get("sport") or "tennis"
    day = str(pick.get("date") or date.today().isoformat())[:10]
    home = str(pick.get("player_home") or "").strip().lower()
    away = str(pick.get("player_away") or "").strip().lower()
    if not home or not away:
        match_str = str(pick.get("match") or "").strip().lower()
        if " vs " in match_str:
            parts = match_str.split(" vs ", 1)
            home, away = parts[0], parts[1]
        else:
            digest = hashlib.sha1(json.dumps(pick, sort_keys=True).encode()).hexdigest()[:16]
            home, away = "unknown", digest
    return f"{sport}|{day}|{home}|{away}"


def event_row_from_pick(pick: dict) -> dict:
    day = str(pick.get("date") or date.today().isoformat())[:10]
    return {
        "sport_id": SPORT_ID,
        "start_time": f"{day}T12:00:00+00:00",
        "source_key": EVENT_SOURCE_KEY,
        "source_ref": event_source_ref(pick),
        "status": "scheduled",
    }


def fetch_edge_ids(client, edge_names: list[str]) -> dict[str, str]:
    if not edge_names:
        return {}
    resp = (
        client.table("edges")
        .select("id,name")
        .eq("sport_id", SPORT_ID)
        .eq("source_id", SOURCE_ID)
        .in_("name", sorted(set(edge_names)))
        .execute()
    )
    return {r["name"]: r["id"] for r in _response_data(resp) if r.get("name") and r.get("id")}


def upsert_events(client, picks: list[dict]) -> dict[str, str]:
    if not picks:
        return {}
    by_ref = {event_source_ref(p): event_row_from_pick(p) for p in picks}
    rows = list(by_ref.values())
    client.table("events").upsert(rows, on_conflict="source_key,source_ref").execute()
    resp = (
        client.table("events")
        .select("id,source_ref")
        .eq("source_key", EVENT_SOURCE_KEY)
        .in_("source_ref", sorted(by_ref))
        .execute()
    )
    return {
        r["source_ref"]: r["id"]
        for r in _response_data(resp)
        if r.get("source_ref") and r.get("id")
    }


def _sync_meta(target_date: str, picks_path: Path) -> dict[str, Any]:
    return {
        "producer": "racketfactory",
        "target_date": target_date,
        "sync_mode": "authoritative_replace",
        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_file": str(picks_path),
    }


def build_pick_rows(
    picks: list[dict],
    edge_ids: dict[str, str],
    event_ids: dict[str, str],
    *,
    target_date: str,
    picks_path: Path,
) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    skipped: list[dict] = []
    sync_meta = _sync_meta(target_date, picks_path)
    for p in picks:
        edge_name = p.get("slice_matched")
        event_ref = event_source_ref(p)
        edge_id = edge_ids.get(edge_name or "")
        event_id = event_ids.get(event_ref)
        if not edge_id or not event_id:
            skipped.append({"pick": p, "edge_name": edge_name, "event_ref": event_ref})
            continue
        bucket = p.get("bucket") or "UNKNOWN"
        try:
            prob_val = p.get("confidence") or p.get("pred_confidence")
            if prob_val == "High": probability = 0.75
            elif prob_val == "Medium": probability = 0.65
            elif prob_val == "Low": probability = 0.50
            else: probability = round(float(prob_val) / 100.0, 4) if prob_val else None
        except Exception:
            probability = None
        payload = dict(p)
        payload["_sync_meta"] = sync_meta
        rows.append({
            "edge_id": edge_id,
            "event_id": event_id,
            "market": "1x2",
            "selection": p.get("selected_player") or p.get("selected_side"),
            "probability": probability,
            "odds": p.get("odds"),
            "status": "skipped" if str(bucket).startswith("SKIPPED") else "open",
            "bucket": bucket,
            "context": {
                "tour": p.get("tour"),
                "series": p.get("series"),
                "surface": p.get("surface"),
                "tournament": p.get("tournament"),
                "source": p.get("source"),
                "cross_source_agree": p.get("cross_source_agree"),
                "_sync_meta": sync_meta
            },
            "rule": edge_name,
            "match_name": p.get("match"),
            "picked_for": (p.get("date") or target_date)[:10],
            "market_type": "1x2",
            "odds_tier": p.get("fav_odds_band"),
            "source_payload": payload,
        })
    return rows, skipped


def write_sync_manifest(*, target_date: str, picks_path: Path, raw_text: str, pick_rows: list[dict], replace_date: bool) -> Path:
    manifest = {
        "target_date": target_date,
        "picks_path": str(picks_path),
        "row_count": len(pick_rows),
        "sha1": hashlib.sha1(raw_text.encode()).hexdigest(),
        "sync_mode": "authoritative_replace" if replace_date else "upsert_only",
        "written_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    out = ROOT / "localdata" / f"supabase_sync_manifest_{target_date}.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Sync certified edges and an explicit picks ledger to Supabase for Racket Factory")
    p.add_argument("--picks", default=str(DEFAULT_PICKS), help="Path to source picks JSON.")
    p.add_argument("--target-date", default=None, help="Authoritative target date (YYYY-MM-DD).")
    p.add_argument("--replace-date", action="store_true", help="Delete existing rows for target date before upserting.")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    picks_path = Path(args.picks)
    raw_text = picks_path.read_text() if picks_path.exists() else "[]"
    raw_picks = load_picks_raw(picks_path)
    edges = load_edges_from_picks(raw_picks)
    target_date = infer_target_date(raw_picks, args.target_date)

    print(f"Certified edges to sync: {len(edges)}")
    print(f"Daily picks to sync: {len(raw_picks)}")
    print(f"Sync source file: {picks_path}")
    print(f"Target date: {target_date}")
    print(f"Replace date mode: {args.replace_date}")

    if args.dry_run:
        print("DRY RUN")
        return

    if args.replace_date and not target_date:
        print("Sync failed: --replace-date requires --target-date or picks with a date field")
        sys.exit(1)

    try:
        client = get_client()
        if edges:
            upsert_edges(client, edges)
        if args.replace_date and target_date:
            delete_picks_for_date(client, target_date)
        edge_ids = fetch_edge_ids(client, [e["name"] for e in edges])
        event_ids = upsert_events(client, raw_picks)
        pick_rows, skipped = build_pick_rows(
            raw_picks,
            edge_ids,
            event_ids,
            target_date=target_date or date.today().isoformat(),
            picks_path=picks_path,
        )
        if skipped:
            print(f"Skipped picks without edge/event id: {len(skipped)}")
        if pick_rows:
            upsert_picks(client, pick_rows)
        manifest = write_sync_manifest(
            target_date=target_date or date.today().isoformat(),
            picks_path=picks_path,
            raw_text=raw_text,
            pick_rows=pick_rows,
            replace_date=args.replace_date,
        )
        print(f"Sync manifest written: {manifest}")
        print("Supabase sync done.")
    except Exception as e:
        print("Sync failed:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
