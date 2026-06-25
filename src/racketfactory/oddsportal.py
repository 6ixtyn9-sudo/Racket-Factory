from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
_DECIMAL_ODDS_RE = re.compile(r"(?<!\d)(\d+\.\d{2})(?!\d)")
_AMERICAN_ODDS_RE = re.compile(r"(?<!\w)([+-]\d{3,4})(?!\w)")
_SCORE_RE = re.compile(r"\b\d{1,2}\s*[-:–—]\s*\d{1,2}(?:\s+\d{1,2}\s*[-:–—]\s*\d{1,2})*\b")
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


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


def parse_oddsportal_date_header(text: object, *, default_year: int | None = None, today: date | None = None, default: str = "") -> str:
    """Parse rendered OddsPortal date headers such as `Today, 25 Jun - Singles`.

    OddsPortal rendered result pages often show relative labels for the current
    page (`Today`, `Yesterday`) and otherwise day/month without a year.  The
    caller can pass the tournament year inferred from the URL.
    """
    raw = " ".join(str(text or "").replace("\xa0", " ").split())
    if not raw:
        return default
    today = today or datetime.now().date()
    lowered = raw.lower()
    if lowered.startswith("today"):
        return today.isoformat()
    if lowered.startswith("yesterday"):
        return (today - timedelta(days=1)).isoformat()

    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(20\d{2}))?\b", raw)
    if not m:
        return parse_date(raw, default=default)
    day = int(m.group(1))
    month = _MONTHS.get(m.group(2).lower())
    year = int(m.group(3)) if m.group(3) else (default_year or today.year)
    if not month:
        return default
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return default


def to_decimal(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("−", "-")
    if not text:
        return None
    m = _AMERICAN_ODDS_RE.fullmatch(text)
    if m:
        american = int(m.group(1))
        if american > 0:
            f = 1.0 + american / 100.0
        elif american < 0:
            f = 1.0 + 100.0 / abs(american)
        else:
            return None
    else:
        try:
            f = float(text)
        except ValueError:
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
        ".participant-name", "[class*='participant']", "[class*='team']", "[data-testid*='participant']",
        "p[class*='participant']", "a[href*='/tennis/']",
    ]
    found: list[str] = []
    for sel in selectors:
        for p in node.select(sel):
            text = normalize_player(p.get("title") or p.get_text(" ", strip=True))
            if text and not _DECIMAL_ODDS_RE.fullmatch(text) and not _AMERICAN_ODDS_RE.fullmatch(text) and text not in found:
                found.append(text)
        if len(found) >= 2:
            return found[:2]
    return found[:2]


def _odds_from_node(node) -> list[str]:
    odds: list[str] = []
    for p in node.select("p[data-testid*='odd-container'], [data-testid*='odd-container'] p"):
        text = p.get_text(" ", strip=True).replace("−", "-")
        if to_decimal(text) is not None and text not in odds:
            odds.append(text)
    if len(odds) >= 2:
        return odds[-2:]
    text = node.get_text(" ", strip=True).replace("−", "-")
    found = _DECIMAL_ODDS_RE.findall(text) or _AMERICAN_ODDS_RE.findall(text)
    return found[-2:]


def _round_from_date_header(text: str) -> str:
    if " - " not in text:
        return ""
    parts = [p.strip() for p in text.split(" - ") if p.strip()]
    return " - ".join(parts[1:]) if len(parts) > 1 else ""


def _score_and_winner_from_event(node, player_a: str, player_b: str, status_text: str) -> tuple[str, str]:
    if re.search(r"\b(retired|ret\.)\b", status_text, flags=re.I):
        return "RET", ""

    participant_box = node.select_one('[data-testid="event-participants"]') or node
    center = participant_box.select_one(".relative")
    score_text = center.get_text(" ", strip=True) if center else participant_box.get_text(" ", strip=True)
    score_match = _SCORE_RE.search(score_text.replace(":", "-")) or _SCORE_RE.search(node.get_text(" ", strip=True).replace(":", "-"))
    score = ""
    winner = ""
    if score_match:
        score = re.sub(r"\s*[-:–—]\s*", "-", score_match.group(0)).strip()
        nums = [int(x) for x in re.findall(r"\d+", score)]
        if len(nums) >= 2 and re.search(r"\b(finished|fin)\b", status_text, flags=re.I):
            if nums[0] > nums[1]:
                winner = player_a
            elif nums[1] > nums[0]:
                winner = player_b
    return score, winner


def parse_rendered_html(
    html: str,
    *,
    default_date: str = "",
    tour: str = "UNKNOWN",
    tournament: str = "",
    oddsportal_url: str = "",
    default_year: int | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Parse saved/rendered OddsPortal tennis HTML.

    Supports the current rendered `.eventRow` layout, including American odds
    display, while keeping the older generic decimal parser as a fallback.
    """
    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select(".eventRow, [data-testid*='event'], .event-row, tr")
    rows: list[dict[str, Any]] = []
    current_date = default_date
    current_round = ""
    seen_row_ids: set[int] = set()

    for node in candidates:
        if id(node) in seen_row_ids:
            continue
        seen_row_ids.add(id(node))
        date_header = node.select_one('[data-testid="date-header"]')
        if date_header:
            header_text = date_header.get_text(" ", strip=True)
            current_date = parse_oddsportal_date_header(header_text, default_year=default_year, today=today, default=current_date)
            current_round = _round_from_date_header(header_text)

        text = node.get_text(" ", strip=True)
        if not text:
            continue
        odds = _odds_from_node(node)
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

        status = ""
        status_node = node.select_one('[data-testid="time-item"]')
        if status_node:
            status = status_node.get_text(" ", strip=True)
        score, inferred_winner = _score_and_winner_from_event(node, players[0], players[1], status)
        if not score:
            score_match = _SCORE_RE.search(text)
            score = score_match.group(0) if score_match else ""
        rows.append({
            "match_date": current_date or parse_date(text, default=default_date),
            "tour": tour,
            "tournament": tournament,
            "round": current_round,
            "player_a": players[0],
            "player_b": players[1],
            "odds_a": odds[-2],
            "odds_b": odds[-1],
            "winner": inferred_winner,
            "score": score,
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
