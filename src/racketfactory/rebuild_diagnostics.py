"""Bounded, non-financial diagnostics for a single guarded workflow attempt."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ENV = "RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR"


def directory() -> Path | None:
    value = os.getenv(ENV, "").strip()
    return Path(value) if value else None


def write_status(data: dict, path: Path | None = None) -> None:
    path = path or directory()
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path, delete=False, encoding="utf-8") as stream:
            tmp = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path / "rebuild_status.json")
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


# Only allow actual market/period labels composed entirely of known vocabulary.
# No URLs, player names, arbitrary page text, scripts, cookies, or input values.
_LABEL_WORDS = frozenset('match winner moneyline full time result results 1x2 1 2 vs set sets game games handicap total totals over under first second third fourth fifth period including excluding retirement retired retires retirements walkover void cancelled cancellation abandoned completed completion bookmaker book odds win draw home away tennis regular best of three five if a player all bets bet settled are is the was will be in case as at least one to finish outcome stake returned rules settlement for on'.split())


def _safe_label(text: str) -> str | None:
    value = re.sub(r"\s+", " ", text.strip().lower())
    words = re.findall(r"[a-z0-9]+", value)
    if not words or len(value) > 100 or len(words) > 12:
        return None
    return value if all(word in _LABEL_WORDS for word in words) else None


def capture_betexplorer_fragment(html: str, kind: str, *, event_id: str = "", observed_at: str = "", status: int | None = None, response_body: str = "") -> None:
    """Record a tiny redacted DOM description of responses already fetched.

    This is evidence collection, not an assertion about market semantics. No
    additional network calls are made. At most one listing and two details are
    retained. DOM text and attributes are allowlisted and never copied wholesale.
    """
    path = directory()
    if path is None or kind not in {"listing", "detail", "comparison"} or not html:
        return
    from bs4 import BeautifulSoup
    path.mkdir(parents=True, exist_ok=True)
    file = path / "betexplorer_fragments.json"
    try:
        data = json.loads(file.read_text()) if file.exists() else {"version": 1, "fragments": []}
    except (OSError, ValueError):
        data = {"version": 1, "fragments": []}
    fragments = data.get("fragments", [])
    limit = 1 if kind == "listing" else 2
    if sum(item.get("kind") == kind for item in fragments) >= limit:
        return
    if kind == "comparison" and event_id and event_id not in {item.get("event_id") for item in fragments if item.get("kind") == "detail"}:
        return
    soup = BeautifulSoup(html[:2_000_000], "html.parser")
    labels = []
    for tag in soup.select('th, h1, h2, h3, h4, [role="tab"], [data-market], [data-market-id], [data-period], [data-period-id]')[:300]:
        label = _safe_label(tag.get_text(" ", strip=True))
        attrs = {}
        for key in ("data-market", "data-market-id", "data-period", "data-period-id"):
            raw = str(tag.get(key) or "")
            val = _safe_label(raw)
            if val or re.fullmatch(r"[0-9]{1,6}", raw):
                attrs[key] = val or raw
        if label or attrs:
            labels.append({"tag": tag.name, "text": label, "attributes": attrs})
        if len(labels) == 25:
            break
    # Bookmaker labels are public provider identifiers, not policy proof.
    # Capture only short, restricted labels from bookmaker-specific attributes;
    # other page text, URLs and long token-like values remain redacted.
    books = []
    for row in soup.select('table tbody tr, tr[data-bookie], tr[data-bookmaker-name], div.oddsComparisonAll__rowBookie')[:200]:
        logo = row.select_one('img[title], a[title]')
        label = str(row.get("data-bookmaker-name") or (logo.get("title") if logo else "") or "").strip()
        if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .&+'-]{0,39}", label)
                and not re.search(r"(?i)token|secret|password|api.?key|session|bearer", label)
                and not re.search(r"[A-Za-z0-9]{24}", label) and label not in books):
            books.append(label)
        if len(books) == 20:
            break
    tables = []
    for table in soup.find_all("table", limit=3):
        sample = []
        for tr in table.find_all("tr", limit=8):
            cells = []
            for cell in tr.find_all(["th", "td"], recursive=False, limit=12):
                text = cell.get_text(" ", strip=True)
                label = _safe_label(text)
                numeric = bool(re.fullmatch(r"\d{1,2}\.\d{1,2}", text))
                # Attribute names indicate structure; values may contain secrets.
                attrs = sorted(k for k in cell.attrs if k in {"data-odd", "data-opening-odd", "data-closing-odd", "data-market", "data-period"})
                match_link = any(re.match(r"^/tennis/[^?#]+/[^?#]+/[^?#]+/[^?#]+/?$", str(a.get("href") or "")) for a in cell.find_all("a", href=True))
                cells.append({"tag": cell.name, "label": label, "kind": "label" if label else "decimal" if numeric else "redacted", "attribute_names": attrs, "has_match_link": match_link})
            if cells:
                sample.append(cells)
        tables.append(sample)
    import hashlib
    safe_event = event_id if re.fullmatch(r"[A-Za-z0-9]{1,32}", event_id) else ""
    body = response_body or html
    fragments.append({"kind": kind, "captured_at": datetime.now(timezone.utc).isoformat(),
                      "event_id": safe_event, "retrieved_at": observed_at, "http_status": status,
                      "response_sha256": hashlib.sha256(body.encode()).hexdigest(),
                      "input_truncated": len(html) > 2_000_000,
                      "raw_data_dt_rows_in_sample": len(soup.find_all("tr", attrs={"data-dt": True})),
                      "bookmaker_labels": books if kind == "comparison" else [],
                      "labels": labels, "tables": tables})
    file.write_text(json.dumps({"version": 1, "fragments": fragments}, indent=2), encoding="utf-8")


def capture_comparison_offers(event_id: str, offers: list[dict], metrics: dict) -> None:
    """Persist bounded pseudonymised per-bookmaker comparison evidence."""
    import hashlib
    path = directory()
    if path is None or not re.fullmatch(r"[A-Za-z0-9]{1,32}", event_id):
        return
    path.mkdir(parents=True, exist_ok=True)
    file = path / "betexplorer_comparison_summary.json"
    try:
        data = json.loads(file.read_text()) if file.exists() else []
        if not isinstance(data, list): data = []
    except (OSError, ValueError):
        data = []
    if len(data) >= 2 or any(row.get("event_id") == event_id for row in data):
        return
    samples = []
    for offer in offers[:20]:
        book = str(offer.get("bookmaker") or "")
        updates = [str(v) if v is not None and re.fullmatch(r"[0-9]{1,16}", str(v)) else None
                   for v in (offer.get("provider_price_updated_at") or [])[:2]]
        samples.append({"bookmaker_sha256": hashlib.sha256(book.encode()).hexdigest(),
                        "odds_home": offer.get("odds_home"), "odds_away": offer.get("odds_away"),
                        "observed_at": offer.get("observed_at"), "provider_price_updated_at_raw": updates,
                        "response_sha256": offer.get("response_sha256"), "market": offer.get("market"),
                        "period": offer.get("period")})
    data.append({"event_id": event_id, "parser_counts": metrics, "offers": samples})
    file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def capture_betexplorer_failure(event_id: str, stage: str, kind: str, *, status: int | None = None, observed_at: str = "", response_sha256: str = "") -> None:
    """Record bounded failure categories without response bodies or URLs."""
    path = directory()
    if path is None or not re.fullmatch(r"[A-Za-z0-9]{1,32}", event_id):
        return
    path.mkdir(parents=True, exist_ok=True)
    file = path / "betexplorer_detail_failures.json"
    try:
        items = json.loads(file.read_text()) if file.exists() else []
        if not isinstance(items, list): items = []
    except (OSError, ValueError):
        items = []
    if len(items) < 2:
        items.append({"event_id": event_id, "stage": stage if stage in {"detail", "comparison"} else "detail",
                      "kind": kind if kind in {"transport", "http", "auth", "challenge", "rate_limit", "parser"} else "unexpected",
                      "recorded_at": datetime.now(timezone.utc).isoformat(), "retrieved_at": observed_at,
                      "http_status": status if isinstance(status, int) else None,
                      "response_sha256": response_sha256 if re.fullmatch(r"[0-9a-f]{64}", response_sha256) else ""})
        file.write_text(json.dumps(items, indent=2), encoding="utf-8")
