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


def _listing_row_evidence(row) -> dict:
    """Describe a row without serializing its HTML or arbitrary attributes.

    Name-like text is retained only in a cell containing a validated match
    link. It is unverified page text, not a participant identity or an
    orientation. Other cells contribute only exact allowlisted status text.
    The ordered tree and omission counters make lost material visible.
    """
    from bs4 import NavigableString, Tag, Comment
    from racketfactory.betexplorer_evidence import _SECRET, _url
    from racketfactory.sources.betexplorer import _is_match_link

    statuses = {'finished', 'live', 'scheduled', 'cancelled', 'canceled',
                'postponed', 'abandoned', 'retired', 'ret.', 'walkover', 'w/o'}
    blocked = {'script', 'style', 'noscript', 'template', 'form', 'input',
               'textarea', 'select', 'button', 'iframe', 'svg'}
    allowed_tags = {'td', 'th', 'a', 'span', 'div', 'b', 'strong', 'i', 'em', 'br'}
    allowed_classes = {'table-main__tt'}
    remaining = 80
    omissions = {'text': 0, 'elements': 0, 'bounded': False}

    def public_name_text(value: str) -> str | None:
        value = ' '.join(value.split())
        if not value or len(value) > 100 or _SECRET.search(value):
            return None
        # Separators must survive as separate DOM text nodes for offline replay.
        if value in {'-', '–', '—', '/', 'vs', 'vs.'}:
            return value
        if not any(c.isalpha() for c in value):
            return None
        if any(not (c.isalpha() or c.isspace() or c in ".'’-/&–—") for c in value):
            return None
        words = re.findall(r'[^\W\d_]+', value, re.UNICODE)
        if not words or len(words) > 12 or any(len(w) > 22 or (len(w) > 5 and w.isupper()) for w in words):
            return None
        return value

    def tree(node, name_cell: bool, depth: int):
        nonlocal remaining
        if remaining <= 0 or depth > 6:
            omissions['bounded'] = True
            return None
        remaining -= 1
        if isinstance(node, Comment):
            omissions['elements'] += 1
            return None
        if isinstance(node, NavigableString):
            raw_text = str(node)
            if len(raw_text) > 200:
                omissions['text'] += 1
                return None
            text = ' '.join(raw_text.split())
            if not text:
                return None
            if text.casefold() in statuses:
                return {'status_text': text}
            safe = public_name_text(text) if name_cell else None
            if safe is not None:
                return {'name_like_text': safe}
            omissions['text'] += 1
            return None
        if not isinstance(node, Tag) or node.name not in allowed_tags or node.name in blocked:
            omissions['elements'] += 1
            return None
        item = {'tag': node.name}
        classes = sorted(set(node.get('class') or []) & allowed_classes)
        if classes:
            item['classes'] = classes
        if node.name == 'a':
            href = _url(str(node.get('href') or ''), '')
            if href and _is_match_link(href):
                item['public_match_path'] = href.split('?', 1)[0]
        children = []
        for child in node.children:
            if remaining <= 0:
                omissions['bounded'] = True
                break
            entry = tree(child, name_cell, depth + 1)
            if entry is not None:
                children.append(entry)
        if children:
            item['children'] = children
        return item

    cells = []
    for cell in row.find_all(['td', 'th'], recursive=False, limit=13):
        if len(cells) == 12:
            omissions['bounded'] = True
            break
        name_cell = False
        for anchor in cell.find_all('a', href=True, limit=20):
            href = _url(str(anchor.get('href') or ''), '')
            if href and _is_match_link(href):
                name_cell = True
                break
        cells.append(tree(cell, name_cell, 0))
    dt = str(row.get('data-dt') or '')
    # Preserve only the exact five numeric components, without interpreting
    # their ordering, timezone, or relationship to the listing date.
    dt_components = [int(x) for x in dt.split(',')] if re.fullmatch(r'\d{1,4}(?:,\d{1,4}){4}', dt) else None
    return {'data_dt_components': dt_components, 'cells': cells, 'omissions': omissions,
            'interpretation': 'Unverified row evidence; participant identity, status and schedule not established.'}


def capture_unrecognized_listing_row(row, target_date: str) -> None:
    """Retain bounded public row evidence without classifying unknown markup.

    Only name-like visible text in a validated match-link cell, exact status
    labels, numeric date components, a restricted tag tree and public paths
    are retained. Other attributes, scripts, forms and arbitrary text are not.
    """
    import hashlib
    from racketfactory.betexplorer_evidence import _url
    path = directory()
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)
    file = path / "betexplorer_unrecognized_rows.json"
    try:
        data = json.loads(file.read_text()) if file.exists() else []
        if not isinstance(data, list):
            data = []
    except (OSError, ValueError):
        data = []
    if len(data) >= 5:
        return
    raw = str(row)
    links = []
    for a in row.find_all('a', href=True, limit=20):
        safe = _url(str(a.get('href') or ''), '')
        links.append({'public_path': safe.split('?', 1)[0] if safe else None,
                      'text_label': _safe_label(a.get_text(' ', strip=True))})
    cells = []
    for cell in row.find_all(['td', 'th'], recursive=False, limit=12):
        cells.append({'tag': cell.name, 'text_label': _safe_label(cell.get_text(' ', strip=True)),
                      'link_count': len(cell.find_all('a', href=True, limit=21)),
                      'attribute_names': sorted(k for k in cell.attrs if k in {'class', 'data-dt', 'colspan', 'rowspan'})})
    data.append({'target_date': target_date if re.fullmatch(r'\d{4}-\d{2}-\d{2}', target_date) else '',
                 'classification': 'unresolved', 'row_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                 'cells': cells, 'links': links, 'sample_limited': len(raw) > 64000,
                 'row_evidence': _listing_row_evidence(row),
                 'notice': 'Redacted structural sample; omitted markup may be necessary to classify this row.'})
    file.write_text(json.dumps(data, indent=2), encoding='utf-8')
