"""TennisExplorer results adapter (table ``result`` on ``/results/`` pages).

Rewritten 2026-09-12 after the paired-row heuristic was proven to fabricate
matches in production data (tournament headers paired as opponents, e.g.
``Cascino E / Feng S. vs Montreux WTA``; frankenstein rows such as
``Munar J. vs Olivieri G.``; the S (sets-won) column parsed as a set score).

Parser contract:
  * Tournament context comes from the tournament link preceding each table
    (``/<slug>/<year>/<tour-segment>/``); tour is mapped from the segment,
    never from substring guessing on display text.
  * Match pairs are anchored on the time cell: a row WITH a ``HH:MM`` time
    starts a match, the immediately following row WITHOUT a time continues
    it. Header rows (S/1/2/..) and orphan rows are skipped, never paired.
  * Player names prefer the link ``title`` attribute (full names) over the
    visible text, which TennisExplorer truncates for doubles teams
    (``Brancacci / Papamicha`` vs title ``Brancaccio N. / Papamichail D.``).
  * The winner comes from the S column first, markup second, set counts
    last. Scores stay in ROW order (row1-row2 per set); the ``winner`` field
    carries the outcome. The S column is never emitted as a set score.
  * Reference H/A odds, the match-detail id, per-row status markers
    (ret./w.o.) and the page's visible date are captured for settlement and
    audit. Fused tiebreak digits (``61`` = 6 games + 1 point) are split.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tennisexplorer.com"
RESULTS_URL = BASE_URL + "/results/?type=all&year={year}&month={month:02d}&day={day:02d}"

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_TOURNAMENT_HREF_RE = re.compile(r"/([^/]+)/(\d{4})/([a-z0-9-]+)/")
_MATCH_ID_RE = re.compile(r"match-detail/\?id=(\d+)")
_SEED_RE = re.compile(r"\s*\(\d+\)\s*")
_RET_RE = re.compile(r"\b(ret\.?|retired|abd\.?|abandoned|def\.?|defaulted)\b", re.IGNORECASE)
_WO_RE = re.compile(r"\b(w\.?o\.?|walkover)\b", re.IGNORECASE)
_VISIBLE_DATE_RES = (
    re.compile(r"(\d{1,2})[.](\d{1,2})[.](\d{4})"),   # 11.09.2026 (TE is D.M.Y)
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),       # 09/11/2026 fallback
)

RESULT_COLUMNS = [
    "match_date", "tour", "tournament", "round", "player_a", "player_b",
    "winner", "score", "odds_a", "odds_b", "bookmaker", "source",
    "captured_at", "oddsportal_url", "_surface", "_court", "_series",
    "_comment", "_location", "_winner_rank", "_loser_rank", "_odds_source",
    "_is_live", "_score_perspective", "_te_id", "_sets_a", "_sets_b",
    "_ref_odds_a", "_ref_odds_b", "_result_status", "_match_type",
    "_page_date", "_row_quality",
]


def tour_from_segment(segment: str, slug: str = "") -> str:
    seg = (segment or "").lower()
    slug_l = (slug or "").lower()
    base = ""
    if seg.startswith("atp"):
        base = "ATP"
    elif seg.startswith("wta"):
        base = "WTA"
    if "davis" in slug_l or "davis" in seg:
        return "Davis Cup"
    if "bjk" in slug_l or "billie" in slug_l:
        return "BJK Cup"
    if "utr" in slug_l or seg == "utr":
        return "UTR"
    if "exhibition" in slug_l or "exo" in seg:
        return "Exhibition"
    if "itf" in slug_l:
        if base == "WTA":
            return "ITF-W"
        if base == "ATP":
            return "ITF-M"
        return "ITF"
    if "challenger" in slug_l:
        return "CHALLENGER"
    return base or "UNKNOWN"


@dataclass
class ParsedRow:
    time: str = ""
    player: str = ""
    sets_won: int | None = None
    sets: list[int | None] = field(default_factory=list)
    ref_odds: float | None = None
    match_id: str = ""
    status: str = ""
    is_doubles: bool = False


def _cell_text(cell) -> str:
    return cell.get_text(separator=" ", strip=True) if cell is not None else ""


def _parse_games(text: str, partner: int | None) -> int | None:
    """Games from a set cell, splitting fused tiebreak digits (``61``->6)."""
    toks = str(text or "").split()
    if not toks or not toks[0].isdigit():
        return None
    value = int(toks[0])
    if value >= 60 and partner in (6, 7):
        head = str(value)[0]
        if head in ("6", "7"):
            return int(head)
    if value > 30:
        return None
    return value


def _parse_float(text: str) -> float | None:
    try:
        value = float(str(text or "").strip())
    except (TypeError, ValueError):
        return None
    if 1.01 <= value <= 100.0:
        return value
    return None


def _player_from_cell(cell) -> tuple[str, bool]:
    """Return (display name, is_doubles) preferring title attributes."""
    if cell is None:
        return "", False
    link = cell.find("a", href=re.compile(r"/(player|doubles-team)/"))
    if link is None:
        return "", False
    href = str(link.get("href") or "")
    title = str(link.get("title") or "").strip()
    text = link.get_text(separator=" ", strip=True)
    name = title or text
    name = _SEED_RE.sub(" ", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name, "/doubles-team/" in href


def _is_header_row(cells: list[str]) -> bool:
    lowered = {c.strip().lower() for c in cells}
    if "s" in lowered and "1" in lowered:
        return True
    if lowered >= {"h", "a"} and len(cells) <= 12:
        # bare H/A header (only when it cannot be a match row: match rows
        # always carry a player link, which is checked before this).
        return True
    return False


def _row_status(row_text: str, sets: list[int | None], sets_won: int | None) -> str:
    if _WO_RE.search(row_text):
        return "WALKOVER"
    if _RET_RE.search(row_text):
        return "RETIRED"
    played = [s for s in sets if s is not None]
    if sets_won is not None and sets_won > 0 and not played:
        # Winner recorded (S column) but no set cells: walkover/bye.
        return "WALKOVER"
    return ""


def _parse_match_row(cells, *, is_second: bool) -> ParsedRow | None:
    texts = [_cell_text(c) for c in cells]
    if not cells or _is_header_row(texts):
        return None
    idx = 0
    time = ""
    if texts and _TIME_RE.match(texts[0]):
        if is_second:
            return None  # a timed row never continues a match
        time = texts[0]
        idx = 1
    elif not is_second:
        return None  # opener rows must carry a time cell
    # Continuation rows start with an empty time cell; skip padding.
    while idx < len(cells) and not texts[idx].strip():
        idx += 1
    if idx >= len(cells):
        return None
    name, is_doubles = _player_from_cell(cells[idx])
    if not name:
        return None
    idx += 1
    # S column: first purely-numeric short cell after the player.
    sets_won: int | None = None
    if idx < len(texts) and texts[idx].isdigit() and int(texts[idx]) <= 5:
        sets_won = int(texts[idx])
        idx += 1
    rest = texts[idx:]
    # Info/match-id link may sit in any trailing cell.
    match_id = ""
    for cell in cells[idx:]:
        link = cell.find("a", href=_MATCH_ID_RE) if hasattr(cell, "find") else None
        if link is not None:
            m = _MATCH_ID_RE.search(str(link.get("href") or ""))
            if m:
                match_id = m.group(1)
                break
    # Set cells: leading numeric-ish cells; H/A reference odds are the last
    # two float cells before the info cell.
    numeric_cells = [t for t in rest if t and (t[0].isdigit() or t in ("RET", "W/O", "DEF", "w.o.", "ret."))]
    set_texts = numeric_cells[:5]
    sets: list[int | None] = []
    for tok in set_texts:
        if tok.upper().startswith(("RET", "W/O", "DEF", "W.O")):
            sets.append(None)
        elif tok.split() and tok.split()[0].isdigit():
            sets.append(int(tok.split()[0]) if int(tok.split()[0]) <= 30 else None)
        else:
            sets.append(None)
    ref = None
    # Reference odds always carry decimals ("2.30"); integer cells are sets.
    floats = [_parse_float(t) for t in rest if "." in str(t)]
    floats = [f for f in floats if f is not None]
    if floats:
        # H belongs to row1, A to row2; opener takes the first float only
        # when two are present (H/A), closer takes the second.
        pass
    row_text = " ".join(texts)
    status = _row_status(row_text, sets, sets_won)
    parsed = ParsedRow(time=time, player=name, sets_won=sets_won, sets=sets,
                       match_id=match_id, status=status,
                       is_doubles=is_doubles)
    parsed.ref_odds = None  # resolved at pair level (H/A order)
    parsed._floats = floats  # type: ignore[attr-defined]
    return parsed


def _pair_scores(first: ParsedRow, second: ParsedRow) -> tuple[str, int, int]:
    """Row-order set scores (``row1-row2`` per set) + computed set counts."""
    pairs: list[str] = []
    won_a = won_b = 0
    width = max(len(first.sets), len(second.sets))
    for i in range(width):
        raw_a = first.sets[i] if i < len(first.sets) else None
        raw_b = second.sets[i] if i < len(second.sets) else None
        g1 = _parse_games(str(raw_a) if raw_a is not None else "", raw_b)
        g2 = _parse_games(str(raw_b) if raw_b is not None else "", raw_a)
        if g1 is None or g2 is None:
            continue
        pairs.append(f"{g1}-{g2}")
        if g1 > g2:
            won_a += 1
        elif g2 > g1:
            won_b += 1
    return " ".join(pairs), won_a, won_b


def _tournament_context(table) -> tuple[str, str, str, bool]:
    """Return (name, tour, slug, is_doubles) from the preceding tourney link."""
    link = table.find_previous("a", href=_TOURNAMENT_HREF_RE)
    if link is None:
        return "", "UNKNOWN", "", False
    href = str(link.get("href") or "")
    m = _TOURNAMENT_HREF_RE.search(href)
    slug, segment = (m.group(1), m.group(3)) if m else ("", "")
    name = link.get_text(separator=" ", strip=True)
    return name, tour_from_segment(segment, slug), slug, "type=double" in href


def parse_visible_date(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("h1")
    probe = head.get_text(separator=" ", strip=True) if head else ""
    if not probe:
        title = soup.find("title")
        probe = title.get_text(strip=True) if title else ""
    for i, pattern in enumerate(_VISIBLE_DATE_RES):
        m = pattern.search(probe)
        if m:
            a, b, year = int(m.group(1)), int(m.group(2)), m.group(3)
            if i == 0 or a > 12:
                day, month = a, b  # D.M.Y
            else:
                day, month = b, a  # M/D/Y fallback
            try:
                return f"{int(year):04d}-{month:02d}-{day:02d}"
            except ValueError:
                continue
    return ""


@dataclass
class ParsedPage:
    rows: list[dict[str, Any]] = field(default_factory=list)
    visible_date: str = ""
    stats: dict[str, int] = field(default_factory=dict)


def parse_results_page(html: str, requested_day: str, *, source_url: str = "") -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    stats = {"tables": 0, "pairs": 0, "orphans": 0, "skipped_no_winner": 0,
             "skipped_header": 0, "s_mismatch": 0}
    rows: list[dict[str, Any]] = []
    tables = soup.find_all("table", class_="result")
    stats["tables"] = len(tables)
    for table in tables:
        tour_name, tour, _slug, table_doubles = _tournament_context(table)
        trs = [tr for tr in table.find_all("tr")
               if tr.find_parent("table") is table]
        i = 0
        while i < len(trs):
            cells = trs[i].find_all(["td", "th"], recursive=False)
            if not cells:
                cells = trs[i].find_all("td")
            texts = [_cell_text(c) for c in cells]
            if any(c.name == "th" for c in cells) or _is_header_row(texts):
                stats["skipped_header"] += 1
                i += 1
                continue
            first = _parse_match_row(cells, is_second=False)
            if first is None:
                i += 1
                continue
            second = None
            if i + 1 < len(trs):
                next_cells = trs[i + 1].find_all(["td", "th"], recursive=False)
                if not next_cells:
                    next_cells = trs[i + 1].find_all("td")
                second = _parse_match_row(next_cells, is_second=True)
            if second is None:
                stats["orphans"] += 1
                i += 1
                continue
            winner = ""
            if first.sets_won is not None and second.sets_won is not None:
                if first.sets_won > second.sets_won:
                    winner = first.player
                elif second.sets_won > first.sets_won:
                    winner = second.player
            score, won_a, won_b = _pair_scores(first, second)
            if not winner:
                if won_a > won_b:
                    winner = first.player
                elif won_b > won_a:
                    winner = second.player
            if not winner:
                # Markup fallback: winner class / strong emphasis.
                for row, player in ((trs[i], first.player),
                                    (trs[i + 1], second.player)):
                    cls = " ".join(row.get("class") or []).lower()
                    td_cls = " ".join(" ".join(c.get("class") or [])
                                      for c in row.find_all("td")).lower()
                    if "winner" in cls or "winner" in td_cls:
                        winner = player
                        break
            if not winner:
                stats["skipped_no_winner"] += 1
                i += 2
                continue
            quality = "ok"
            if (first.sets_won is not None and second.sets_won is not None
                    and score and (won_a, won_b) != (first.sets_won, second.sets_won)):
                stats["s_mismatch"] += 1
                quality = "s_mismatch"
            if not tour_name:
                quality = (quality + "+no_tournament").strip("+")
            floats_first: list[float] = getattr(first, "_floats", [])
            floats_second: list[float] = getattr(second, "_floats", [])
            ref_a = floats_first[0] if len(floats_first) >= 2 else None
            ref_b = floats_first[1] if len(floats_first) >= 2 else None
            if ref_a is None and floats_second:
                # Some layouts repeat H/A on the second row only.
                ref_b = floats_second[-1]
            is_doubles = table_doubles or first.is_doubles or second.is_doubles
            status = first.status or second.status
            if status == "WALKOVER" and not score:
                quality = (quality + "+walkover_noscore").strip("+") \
                    if quality != "ok" else "ok"
            rows.append({
                "match_date": requested_day,
                "tour": tour,
                "tournament": tour_name,
                "round": "",
                "player_a": first.player,
                "player_b": second.player,
                "winner": winner,
                "score": score,
                "_te_id": first.match_id or second.match_id,
                "_sets_a": first.sets_won,
                "_sets_b": second.sets_won,
                "_ref_odds_a": ref_a,
                "_ref_odds_b": ref_b,
                "_result_status": status,
                "_match_type": "Doubles" if is_doubles else "Singles",
                "_page_date": "",
                "_row_quality": quality,
            })
            stats["pairs"] += 1
            i += 2
    visible = parse_visible_date(html)
    for row in rows:
        row["_page_date"] = visible
    return ParsedPage(rows=rows, visible_date=visible, stats=stats)


def fetch_results_page(day: str, *, timeout: int = 25) -> tuple[str, int, str]:
    """Fetch one TE results day. Returns (html, status_code, final_url)."""
    url = RESULTS_URL.format(year=int(day[:4]), month=int(day[5:7]),
                             day=int(day[8:10]))
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/126.0 Safari/537.36"}
    try:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(url, headers=headers, timeout=timeout,
                                 impersonate="chrome")
        return resp.text or "", int(getattr(resp, "status_code", 0)), url
    except Exception as exc:  # noqa: BLE001 - fallback chain
        logger.warning("curl_cffi fetch failed for %s: %s", day, exc)
    try:
        import requests as plain_requests
        resp = plain_requests.get(url, headers=headers, timeout=timeout)
        return resp.text or "", int(resp.status_code), url
    except Exception as exc:  # noqa: BLE001 - terminal fallback
        logger.warning("plain fetch failed for %s: %s", day, exc)
        return "", 0, url


def normalize_row(row: dict[str, Any], *, captured_at: str | None = None) -> dict[str, Any]:
    out = {col: row.get(col) for col in RESULT_COLUMNS}
    out["match_date"] = str(row.get("match_date") or "")[:10]
    out["odds_a"] = row.get("odds_a")
    out["odds_b"] = row.get("odds_b")
    out["source"] = "Challenger_results"
    out["captured_at"] = captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["_comment"] = "result_from_challenger_backfill"
    out["_is_live"] = False
    out["_score_perspective"] = "player_a_games-player_b_games"
    if out.get("_odds_source") is None:
        out["_odds_source"] = ""
    return out


def fetch_day(day: str) -> ParsedPage:
    html, status, url = fetch_results_page(day)
    if status != 200 or not html:
        logger.warning("TennisExplorer returned status=%s for %s", status, day)
        return ParsedPage(rows=[], visible_date="", stats={"http_status": status})
    page = parse_results_page(html, day, source_url=url)
    if page.visible_date and page.visible_date != day:
        logger.warning("TE visible date %s != requested %s (%s)",
                       page.visible_date, day, url)
    logger.info("TE %s: %d pairs (%d tables, %d orphans, %d no-winner, "
                "%d s_mismatch)", day, page.stats.get("pairs", 0),
                page.stats.get("tables", 0), page.stats.get("orphans", 0),
                page.stats.get("skipped_no_winner", 0),
                page.stats.get("s_mismatch", 0))
    return page


def _dedup_key(row: dict[str, Any]) -> tuple:
    from racketfactory.entities import player_key as _pk
    pair = tuple(sorted([_pk(row.get("player_a")), _pk(row.get("player_b"))]))
    te_id = str(row.get("_te_id") or "").strip()
    if te_id:
        return ("id", te_id)
    return ("legacy", str(row.get("match_date") or "")[:10],
            str(row.get("tour") or ""), str(row.get("tournament") or ""),
            pair, str(row.get("winner") or ""), str(row.get("score") or ""))


def merge_rows(existing: list[dict[str, Any]],
               new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge keeping the earliest dated row per match id (TE repeats adjacent
    days: the same id may arrive under two requested dates)."""
    best: dict[tuple, dict[str, Any]] = {}
    for row in list(existing) + list(new):
        key = _dedup_key(row)
        if key not in best:
            best[key] = row
            continue
        cur = best[key]
        if str(row.get("match_date") or "") < str(cur.get("match_date") or ""):
            best[key] = row
        elif str(row.get("match_date") or "") == str(cur.get("match_date") or ""):
            # Same date: prefer rows with richer evidence.
            def _rich(r: dict[str, Any]) -> int:
                return sum(bool(str(r.get(k) or "").strip()) for k in
                           ("_te_id", "tournament", "score", "winner"))
            if _rich(row) > _rich(cur):
                best[key] = row
    return list(best.values())


def write_result_rows(rows: list[dict[str, Any]], data_dir: str | Path,
                      *, prefix: str = "challenger_results") -> list[Path]:
    import pandas as pd

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if not rows:
        return []
    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    normalized = [normalize_row(r, captured_at=captured) for r in rows]
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in normalized:
        by_month.setdefault(str(row["match_date"])[:7], []).append(row)
    written: list[Path] = []
    for month, month_rows in sorted(by_month.items()):
        path = data_dir / f"{prefix}_tennis_{month}.csv.gz"
        existing: list[dict[str, Any]] = []
        if path.exists():
            old = pd.read_csv(path, low_memory=False)
            existing = old.to_dict(orient="records")
        merged = merge_rows(existing, month_rows)
        df = pd.DataFrame(merged)
        for col in RESULT_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[RESULT_COLUMNS]
        df.to_csv(path, index=False, compression="gzip")
        written.append(path)
        logger.info("Wrote %d merged rows (%d new) to %s",
                    len(df), len(month_rows), path)
    return written
