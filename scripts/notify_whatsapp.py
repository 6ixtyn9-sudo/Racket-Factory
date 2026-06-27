#!/usr/bin/env python3
"""Hoop Factory — WhatsApp operational heads-up (CallMeBot).

Edge-Factory parity, basketball-branded, and intentionally self-contained so it
adds no new runtime coupling to ``src/hoopfactory``.  Reads the full day archive
``localdata/picks_YYYY-MM-DD.json``, formats a mobile-friendly summary of the
certified / caution picks, dedupes against an already-sent ledger so the same
selection is never re-pinged within a day, and dispatches via the free CallMeBot
personal WhatsApp API.

It is a NO-OP (exit 0) unless CALLMEBOT_APIKEY and CALLMEBOT_PHONE are set, so it
is always safe to wire into ``daily.py`` / CI.

Environment
-----------
  CALLMEBOT_APIKEY   CallMeBot personal API key (required to actually send)
  CALLMEBOT_PHONE    Destination phone in international format (required)
  HOOP_FACTORY_NOTIFY_WATCHLIST=1   also notify WATCHLIST_* discoveries (optional)

Usage
-----
  PYTHONPATH=src python3 scripts/notify_whatsapp.py --date 2026-06-27
  python3 scripts/notify_whatsapp.py --picks localdata/picks_2026-06-27.json --force
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LOCALDATA = ROOT / "localdata"
DEFAULT_LOCAL_TZ = "Africa/Johannesburg"

BUCKET_CLEAN = "CERTIFIED_CLEAN"
BUCKET_CAUTION = "CAUTION"
BUCKET_WL_ODDS = "WATCHLIST_NO_ODDS"
BUCKET_WL_SIGNAL = "WATCHLIST_SIGNAL_ONLY"


# ----------------------------------------------------------------- helpers --
def _default_target_date() -> str:
    return datetime.now(ZoneInfo(DEFAULT_LOCAL_TZ)).strftime("%Y-%m-%d")


def _archive_file(target_date: str) -> Path:
    return LOCALDATA / f"picks_{target_date}.json"


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        logging.warning("⚠️ Source picks file does not exist: %s", path)
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logging.warning("⚠️ Could not read picks JSON %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)]


def _load_sent_ledger(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_sent_ledger(path: Path, keys: set[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(keys), indent=2))
    except Exception as exc:
        logging.warning("⚠️ Could not save sent ledger %s: %s", path, exc)


def _dedupe_key(pick: dict[str, Any], target_date: str) -> str:
    match = str(pick.get("match") or f"{pick.get('away')}@{pick.get('home')}")
    sel = str(pick.get("selected_team") or pick.get("pick") or "")
    day = str(pick.get("date") or pick.get("picked_for") or target_date)[:10]
    return f"{day}|{match}|{sel}".lower()


def _kickoff(pick: dict[str, Any]) -> str:
    for key in ("kickoff", "time", "start_time", "ko"):
        val = pick.get(key)
        if val not in (None, ""):
            return str(val)
    return "n/a"


def _odds_display(pick: dict[str, Any]) -> str:
    odds = pick.get("odds") or pick.get("decimal_odds")
    if odds is None:
        return "@n/a"
    try:
        return f"@{float(odds):.2f}"
    except (TypeError, ValueError):
        return f"@{odds}"


def _selection(pick: dict[str, Any]) -> str:
    return str(
        pick.get("selected_team")
        or pick.get("recommended_team")
        or pick.get("pick")
        or "?"
    ).upper()


def _prob(pick: dict[str, Any]) -> float:
    for key in ("confidence", "avg_p", "consensus_prob"):
        try:
            v = float(pick.get(key))
        except (TypeError, ValueError):
            continue
        if v:
            return v * 100 if v <= 1.0 else v
    return 0.0


# ------------------------------------------------------------- formatting --
def format_summary(target_date: str, picks: list[dict[str, Any]], *, late_slate: bool) -> str:
    lines: list[str] = []
    if late_slate:
        lines.append(f"🏀 *Hoop Factory Late-Slate Alert* 🏀\n📅 {target_date}\n⚡ Intraday discovery scan\n")
    else:
        lines.append(f"🏀 *Hoop Factory Official Picks* 🏀\n📅 {target_date}\n📊 Morning slate ledger\n")

    buckets: dict[str, list[dict[str, Any]]] = {}
    for p in picks:
        buckets.setdefault(str(p.get("bucket", "UNKNOWN")), []).append(p)

    clean = buckets.get(BUCKET_CLEAN, [])
    if clean:
        lines.append(f"✅ *CERTIFIED CLEAN* ({len(clean)})")
        for p in sorted(clean, key=lambda x: -_prob(x)):
            lines.append(f"• *{p.get('match', '?')}* ➡️ *{_selection(p)}* {_odds_display(p)}")
            lines.append(f"   └ [tip: {_kickoff(p)}] | Prob: {_prob(p):.0f}%\n")

    caution = buckets.get(BUCKET_CAUTION, [])
    if caution:
        lines.append(f"⚠️ *CAUTION* ({len(caution)})")
        for p in sorted(caution, key=lambda x: -_prob(x)):
            lines.append(f"• *{p.get('match', '?')}* ➡️ *{_selection(p)}* {_odds_display(p)}")
            lines.append(f"   └ [tip: {_kickoff(p)}] | Prob: {_prob(p):.0f}%\n")

    wl = [p for p in picks if str(p.get("bucket", "")).startswith("WATCHLIST")]
    if wl:
        lines.append(f"ℹ️ {len(wl)} watchlist item(s) evaluated (no certified odds/ROI yet).")

    lines.append("\n📲 Synced to Supabase / CI archives.")
    lines.append("⚠️ Flat stakes only. Bet only what you can afford to lose.")
    return "\n".join(lines)


# --------------------------------------------------------------- dispatch --
def send_callmebot(apikey: str, phone: str, message_text: str) -> str:
    clean_phone = "".join(ch for ch in str(phone) if ch.isdigit() or ch == "+")
    encoded = urllib.parse.quote(message_text)
    url = f"https://api.callmebot.com/whatsapp.php?phone={clean_phone}&text={encoded}&apikey={apikey}"
    req = urllib.request.Request(url, headers={"User-Agent": "HoopFactory/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Hoop Factory WhatsApp heads-up (CallMeBot).")
    ap.add_argument("--picks", default=None, help="Path to picks JSON (default: day archive).")
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today, local TZ).")
    ap.add_argument("--force", action="store_true", help="Ignore the sent ledger and transmit anyway.")
    args = ap.parse_args()

    target_date = args.date or _default_target_date()
    picks_file = Path(args.picks) if args.picks else _archive_file(target_date)

    apikey = os.environ.get("CALLMEBOT_APIKEY")
    phone = os.environ.get("CALLMEBOT_PHONE")
    if not (apikey and phone):
        logging.warning("⚠️ CALLMEBOT_APIKEY / CALLMEBOT_PHONE unset — skipping WhatsApp notification.")
        return 0

    raw = _load_json_list(picks_file)
    notify_buckets = {BUCKET_CLEAN, BUCKET_CAUTION}
    if os.environ.get("HOOP_FACTORY_NOTIFY_WATCHLIST", "").strip().lower() in {"1", "true", "yes", "on"}:
        notify_buckets |= {BUCKET_WL_ODDS, BUCKET_WL_SIGNAL}

    candidates = [p for p in raw if p.get("bucket") in notify_buckets]
    if not candidates:
        logging.info("[WhatsApp] No notifiable picks for %s. Staying silent.", target_date)
        return 0

    ledger_file = LOCALDATA / f"whatsapp_sent_ledger_{target_date}.json"
    sent = set() if args.force else _load_sent_ledger(ledger_file)
    late_slate = ledger_file.exists() and not args.force

    unsent = [p for p in candidates if _dedupe_key(p, target_date) not in sent]
    if not unsent:
        logging.info("[WhatsApp] All picks already notified earlier today. Staying silent.")
        return 0

    message = format_summary(target_date, unsent, late_slate=late_slate)
    print(message)
    print("\n" + "=" * 60)

    try:
        send_callmebot(apikey=apikey, phone=phone, message_text=message)
        logging.info("✅ CallMeBot dispatch success (%d picks).", len(unsent))
    except Exception as exc:
        logging.error("CallMeBot dispatch failed: %s", exc)
        return 0  # never fail the pipeline on a notification error

    for p in unsent:
        sent.add(_dedupe_key(p, target_date))
    _save_sent_ledger(ledger_file, sent)
    logging.info("Sent ledger updated: %d items in %s", len(sent), ledger_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
