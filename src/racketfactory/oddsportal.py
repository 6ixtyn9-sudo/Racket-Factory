from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

import pandas as pd
from bs4 import BeautifulSoup

from racketfactory.entities import normalize_player, normalize_tour

COLUMNS = [
    "match_date", "tour", "tournament", "round", "player_a", "player_b",
    "winner", "score", "odds_a", "odds_b", "bookmaker", "source", "captured_at",
    "oddsportal_url",
]

_DATE_RE = re.compile(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2})\b")
_ODDS_RE = re.compile(r"(?<!\d)(\d+\.\d{2})(?!\d)")
_SCORE_RE = re.compile(r"\b\d{1,2}[-:]\d{1,2}(?:\s+\d{1,2}[-:]\d{1,2})*\b")


def parse_date(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    m = _DATE_RE.search(text)
    candidate = m.group(1) if m else text[:10]
    candidate = candidate.replace("/", "-").replace(".", "-")
    parts = candidate.split("-")
    try:
        if len(parts[0]) == 4:
            y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            d, mo, y = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return default


def to_decimal(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f <= 1.0 or f > 1000:
        return None
    return round(f, 6)


def clean_winner(value: object, player_a: str, player_b: str) -> str:
    raw = normalize_player(value)
    low = raw.lower()
    if not raw:
        return ""
    if low in {"a", "1", "home", player_a.lower()}:
        return player_a
    if low in {"b", "2", "away", player_b.lower()}:
        return player_b
    return raw if raw in {player_a, player_b} else ""


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    captured_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        player_a = normalize_player(row.get("player_a") or row.get("home") or row.get("player1"))
        player_b = normalize_player(row.get("player_b") or row.get("away") or row.get("player2"))
        odds_a = to_decimal(row.get("odds_a") or row.get("home_odds") or row.get("odds1"))
        odds_b = to_decimal(row.get("odds_b") or row.get("away_odds") or row.get("odds2"))
        match_date = parse_date(row.get("match_date") or row.get("date"), default="")
        if not match_date or not player_a or not player_b or odds_a is None or odds_b is None:
            continue
        winner = clean_winner(row.get("winner") or row.get("result") or "", player_a, player_b)
        out.append({
            "match_date": match_date,
            "tour": normalize_tour(row.get("tour") or row.get("league") or "UNKNOWN"),
            "tournament": str(row.get("tournament") or "").strip(),
            "round": str(row.get("round") or "").strip(),
            "player_a": player_a,
            "player_b": player_b,
            "winner": winner,
            "score": str(row.get("score") or "").strip(),
            "odds_a": odds_a,
            "odds_b": odds_b,
            "bookmaker": str(row.get("bookmaker") or "OddsPortal Rendered").strip(),
            "source": str(row.get("source") or "OddsPortal Rendered").strip(),
            "captured_at": str(row.get("captured_at") or captured_at),
            "oddsportal_url": str(row.get("oddsportal_url") or row.get("url") or "").strip(),
        })
    return out


def read_export_csv(path: str | Path) -> list[dict[str, Any]]:
    df = pd.read_csv(path, low_memory=False)
    return normalize_rows(df.to_dict("records"))


def _players_from_node(node) -> list[str]:
    selectors = [
        "[class*='participant']", "[class*='team']", "[data-testid*='participant']",
        "p[class*='participant']", "a[href*='/tennis/']",
    ]
    found: list[str] = []
    for sel in selectors:
        for p in node.select(sel):
            text = normalize_player(p.get_text(" ", strip=True))
            if text and not _ODDS_RE.fullmatch(text) and text not in found:
                found.append(text)
        if len(found) >= 2:
            return found[:2]
    return found[:2]


def parse_rendered_html(
    html: str,
    *,
    default_date: str = "",
    tour: str = "UNKNOWN",
    tournament: str = "",
    oddsportal_url: str = "",
) -> list[dict[str, Any]]:
    """Parse saved/rendered OddsPortal tennis HTML.

    This intentionally supports several rendered row shapes and avoids making
    certification claims. Route-specific hardening should be driven by saved
    fixtures from real pages.
    """
    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select("[data-testid*='event'], .eventRow, .event-row, tr")
    rows: list[dict[str, Any]] = []
    for node in candidates:
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        odds = _ODDS_RE.findall(text)
        if len(odds) < 2:
            continue
        players = _players_from_node(node)
        if len(players) < 2:
            before_odds = text.split(odds[0], 1)[0]
            parts = [normalize_player(x) for x in re.split(r"\s+[-–—]\s+|\s+v(?:s\.)?\s+", before_odds, flags=re.I)]
            parts = [p for p in parts if p and not parse_date(p)]
            players = parts[:2]
        if len(players) < 2:
            continue
        score_match = _SCORE_RE.search(text)
        rows.append({
            "match_date": parse_date(text, default=default_date),
            "tour": tour,
            "tournament": tournament,
            "player_a": players[0],
            "player_b": players[1],
            "odds_a": odds[-2],
            "odds_b": odds[-1],
            "winner": "",
            "score": score_match.group(0) if score_match else "",
            "source": "OddsPortal Rendered",
            "bookmaker": "OddsPortal Rendered",
            "oddsportal_url": oddsportal_url,
        })
    return normalize_rows(rows)


def parse_embedded_json(html: str, *, tour: str = "UNKNOWN", tournament: str = "", oddsportal_url: str = "") -> list[dict[str, Any]]:
    """Best-effort extraction from JSON script blobs.

    OddsPortal frequently changes DOM structure. This function is a low-risk
    fallback that looks for obvious participant/odds pairs in embedded JSON.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if "odds" not in text.lower() or "participant" not in text.lower():
            continue
        try:
            blobs = re.findall(r"\{[^{}]*(?:participant|home|away|odds)[^{}]*\}", text, flags=re.I)
        except Exception:
            blobs = []
        for blob in blobs:
            try:
                data = json.loads(blob)
            except Exception:
                continue
            rows.extend(normalize_rows([{**data, "tour": tour, "tournament": tournament, "oddsportal_url": oddsportal_url}]))
    return rows


def write_monthly_csv(rows: list[dict[str, Any]], data_dir: str | Path = "localdata") -> list[Path]:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_month.setdefault(str(row["match_date"])[:7], []).append(row)
    written: list[Path] = []
    for month, month_rows in sorted(by_month.items()):
        path = data_dir / f"oddsportal_tennis_{month}.csv.gz"
        df = pd.DataFrame(month_rows, columns=COLUMNS)
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["match_date", "tour", "tournament", "player_a", "player_b", "bookmaker"], keep="last")
        df.to_csv(path, index=False, compression="gzip")
        written.append(path)
    return written
