"""Bounded, allowlisted, evidence-only replay material; never executable quotes.

Only explicitly selected DOM attributes and simple tab/market initializer calls
are retained. Headers, cookies, forms, arbitrary text, scripts and query keys
outside the allowlist are never copied. Unsupported material is flagged, not
silently represented as complete. HTML is evidence, never to be executed.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup, NavigableString

from racketfactory.rebuild_diagnostics import directory

_HOST = 'www.betexplorer.com'
_ATTRIBUTES = {'data-url', 'data-endpoint', 'data-ajax-url', 'href', 'data-market', 'data-market-name',
               'data-period', 'data-scope', 'data-market-id', 'data-period-id', 'data-tab', 'data-tab-id',
               'data-pos', 'data-bookie', 'data-bookmaker-name', 'data-odd', 'data-created', 'data-home', 'data-away', 'title', 'class'}
_URL_ATTRS = {'data-url', 'data-endpoint', 'data-ajax-url', 'href'}
_SECRET = re.compile(r'(?i)token|secret|password|api.?key|session|bearer|cookie|authoriz|account|credential')
_TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_. +/\'&-]{0,79}$')
_CALL = re.compile(r'\b([A-Za-z_$][\w$]*(?:match|odds?|market|tab)[\w$]*|(?:match|odds?|market|tab)[\w$]*)\s*\(([^()\n;]{0,250})\)\s*;?', re.I)
_ARG = re.compile(r'''\s*(?:\d{1,16}|['"][A-Za-z0-9 _.-]{1,32}['"])\s*(?:,\s*(?:\d{1,16}|['"][A-Za-z0-9 _.-]{1,32}['"])\s*)*''')


def _value(raw: object) -> str | None:
    value = ' '.join(raw) if isinstance(raw, list) else str(raw or '').strip()
    if not _TOKEN.fullmatch(value) or _SECRET.search(value) or re.search(r'[A-Za-z0-9]{24,}', value):
        return None
    return value


def _person(raw: object) -> str | None:
    value = str(raw or '').strip()
    if (not re.fullmatch(r"[^\W\d_][\w .&'’/-]{0,79}", value, re.UNICODE)
            or _SECRET.search(value) or re.search(r'[A-Za-z0-9]{24,}', value)):
        return None
    return value


def _url(raw: str, event_id: str) -> str | None:
    try:
        u = urlsplit(urljoin('https://' + _HOST, raw))
        if u.scheme != 'https' or u.netloc.lower() != _HOST or u.username or u.password or u.fragment:
            return None
        if (not re.fullmatch(r'/[A-Za-z0-9_./-]{1,200}', u.path) or '..' in u.path.split('/')
                or _SECRET.search(u.path) or re.search(r'[A-Za-z0-9]{24,}', u.path)):
            return None
        # A public provider route may be relevant without embedding the event
        # id; no network request is made from this recorded value.
        if not any(word in u.path.lower() for word in ('match', 'odds', 'tennis', 'tab')):
            return None
        pairs = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True) if u.query else []
        if any(k not in {'lang', 'event', 'event_id', 'match', 'match_id', 'tab', 'market', 'period'}
               or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', v) or _SECRET.search(v) for k, v in pairs):
            return None
        return u.path + ('?' + urlencode(pairs) if pairs else '')
    except (ValueError, TypeError):
        return None


def _identity(fixture: dict) -> dict | None:
    eid = str(fixture.get('event_id') or '')
    home, away = _person(fixture.get('player_home')), _person(fixture.get('player_away'))
    href = _url(str(fixture.get('match_url') or ''), eid)
    if not re.fullmatch(r'[A-Za-z0-9]{1,32}', eid) or not home or not away or not href:
        return None
    if not href.startswith('/tennis/') or href.rstrip('/').split('/')[-1] != eid:
        return None
    return {'event_id': eid, 'match_url': href, 'player_home': home, 'player_away': away,
            'match_date': str(fixture.get('match_date') or '')[:10],
            'identity_source': 'listing, not independently verified against match page'}


def capture_replay(html: str, kind: str, fixture: dict, *, observed_at: str,
                   response_body: str = '', request_url: str = '') -> bool:
    path = directory()
    identity = _identity(fixture)
    if path is None or identity is None or kind not in {'detail', 'comparison'}:
        return False
    path.mkdir(parents=True, exist_ok=True)
    file = path / ('betexplorer_replay_' + identity['event_id'] + '.json')
    try:
        data = json.loads(file.read_text()) if file.exists() else {'version': 1, 'fixture': identity}
    except (OSError, ValueError):
        return False
    if data.get('fixture') != identity:
        return False
    source = html[:2_000_000]
    soup = BeautifulSoup(source, 'html.parser')
    out = BeautifulSoup('<div></div>', 'html.parser')
    root = out.div
    dropped = False
    count = 0
    # Preserve relevant elements in document order; ancestor nesting is not
    # needed by the current endpoint parser, but tables/rows are retained for
    # comparison response orientation. Values are allowlisted per attribute.
    if kind == 'detail':
        nodes = soup.select('[data-url], [data-endpoint], [data-ajax-url], a[href], [data-tab], [data-market], [data-period], [data-home], [data-away], h1, h2')
    else:
        nodes = soup.select('table, div.oddsComparisonAll__rowBookie')
    def clean(node, parent):
        nonlocal count, dropped
        if count >= 250:
            dropped = True
            return
        count += 1
        tag = out.new_tag(node.name)
        for k, v in node.attrs.items():
            if k not in _ATTRIBUTES:
                if k.startswith('data-') and any(w in k for w in ('odds', 'market', 'period', 'tab', 'event', 'endpoint', 'url')):
                    dropped = True
                continue
            if k == 'title' and (kind != 'comparison' or node.name not in {'img', 'a'}):
                continue
            if k == 'class':
                allowed = [x for x in (v if isinstance(v, list) else str(v).split())
                           if x in {'average', 'oddsComparisonAll__rowBookie'}]
                if allowed:
                    tag[k] = ' '.join(allowed)
                continue
            if k in {'data-home', 'data-away'}:
                person = _person(v)
                value = person if person in {identity['player_home'], identity['player_away']} else None
            else:
                value = _url(str(v), identity['event_id']) if k in _URL_ATTRS else _value(v)
            if value is None:
                dropped = True
            else:
                tag[k] = value
        parent.append(tag)
        if kind == 'detail':
            from racketfactory.rebuild_diagnostics import _safe_label
            text = node.get_text(' ', strip=True)
            allowed_names = {identity['player_home'], identity['player_away'],
                             identity['player_home'] + ' - ' + identity['player_away'],
                             identity['player_home'] + ' vs ' + identity['player_away'],
                             identity['player_away'] + ' - ' + identity['player_home'],
                             identity['player_away'] + ' vs ' + identity['player_home']}
            if text in allowed_names or _safe_label(text):
                tag.append(text)
            elif text:
                dropped = True
        if kind == 'comparison':
            for child in node.children:
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text and (_person(text) in {identity['player_home'], identity['player_away']} or
                                 text.lower() in {'bookmaker', 'average', 'avg', '1', '2'} or re.fullmatch(r'\d{1,2}\.\d{1,3}', text)):
                        tag.append(text)
                    elif text:
                        dropped = True
                elif getattr(child, 'name', '') in {'thead', 'tbody', 'tr', 'th', 'td', 'img', 'a', 'span', 'div'}:
                    clean(child, tag)
    for node in nodes[:250]:
        clean(node, root)
    if len(nodes) > 250:
        dropped = True
    calls = []
    if kind == 'detail':
        for script in soup.find_all('script')[:100]:
            text = script.string or ''
            # Scripts are never copied wholesale. Even retained calls may not
            # describe external or dynamic JavaScript required by the page.
            if text.strip() or script.get('src'):
                dropped = True
            for m in _CALL.finditer(text[:100_000]):
                call = m.group(0).strip()
                args = re.findall(r"['\"]([^'\"]+)['\"]", m.group(2))
                # Arbitrary string literals could be credentials. Retain only
                # the event identity and short, public market/tab vocabulary.
                public = {identity['event_id'].casefold(), 't', 'en', 'tennis', 'match',
                          'odds', 'market', 'tab', 'h2h', '1x2', 'winner', 'full'}
                if (_ARG.fullmatch(m.group(2)) and not _SECRET.search(call)
                        and all(arg.casefold() in public for arg in args)):
                    calls.append(call)
                else:
                    dropped = True
                if len(calls) == 30:
                    dropped = True
                    break
            if len(calls) == 30:
                break
        for call in calls:
            tag = out.new_tag('script')
            tag.string = call
            root.append(tag)
    evidence = str(root)
    if len(evidence.encode()) > 64_000:
        evidence = ''  # never publish invalid, silently truncated replay HTML
        dropped = True
    payload = response_body or html
    data[kind] = {'http_status': 200, 'observed_at': observed_at,
                  'response_sha256': hashlib.sha256(payload.encode()).hexdigest(),
                  'hash_basis': 'UTF-8 encoding of decoded response text, before sanitisation',
                  'observed_request_path': _url(request_url, identity['event_id']) if request_url else None,
                  'replay_html': evidence, 'input_truncated': len(html) > 2_000_000,
                  'redactions_or_limits': dropped,
                  'redaction_policy': 'Only allowlisted DOM attributes, public same-host paths with selected query keys, and simple initializer calls with public arguments. All other page text, headers, cookies, form data, scripts and arguments omitted.',
                  'notice': 'Allowlisted evidence only; unretained DOM/scripts may be essential. Never execute this HTML.'}
    file.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding='utf-8')
    return bool(evidence)
