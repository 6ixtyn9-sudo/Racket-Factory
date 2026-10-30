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
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup, NavigableString

from racketfactory.rebuild_diagnostics import directory

_HOST = 'www.betexplorer.com'
_ATTRIBUTES = {'data-url', 'data-endpoint', 'data-ajax-url', 'href', 'data-market', 'data-market-name',
               'data-period', 'data-scope', 'data-market-id', 'data-period-id', 'data-tab', 'data-tab-id',
               'data-pos', 'data-bookie', 'data-bookmaker-name', 'data-odd', 'data-created', 'data-home', 'data-away',
               'data-home-id', 'data-away-id', 'title', 'class'}
_URL_ATTRS = {'data-url', 'data-endpoint', 'data-ajax-url', 'href'}
_SECRET = re.compile(r'(?i)token|secret|password|api.?key|session|bearer|cookie|authoriz|account|credential')
_TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_. +/\'&-]{0,79}$')
_CALL = re.compile(r'\b([A-Za-z_$][\w$]*(?:match|odds?|market|tab)[\w$]*|(?:match|odds?|market|tab)[\w$]*)\s*\(([^()\n;]{0,250})\)\s*;?', re.I)
_ARG = re.compile(r'''\s*(?:true|false|\d{1,16}|['"][A-Za-z0-9 _.-]{1,32}['"])\s*(?:,\s*(?:true|false|\d{1,16}|['"][A-Za-z0-9 _.-]{1,32}['"])\s*)*''')


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


class CaptureResult(dict):
    """Expose separate facts; truthiness only means useful, not proven complete."""

    def __bool__(self) -> bool:
        return bool(self['file_written'] and self['discovery_material_retained'] and
                    self['material_sufficiency'] != 'insufficient')


def _safe_capture_url(raw: str, *, stage: str, target_date: str, event_id: str = '') -> str | None:
    """Return a public same-host request URL with only reviewed query keys."""
    try:
        from urllib.parse import parse_qsl
        parsed = urlsplit(raw)
        if parsed.scheme != 'https' or parsed.netloc.lower() != _HOST or parsed.username or parsed.password or parsed.fragment:
            return None
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else []
        values = dict(pairs)
        if len(values) != len(pairs):
            return None
        if stage == 'listing':
            if parsed.path == '/tennis/results/':
                if not {'year', 'month', 'day'} <= set(values) or set(values) - {'year', 'month', 'day', 'page', 'page_no'}:
                    return None
                y, m, d = target_date.split('-')
                if any(values.get(key) != value for key, value in
                       {'year': y, 'month': str(int(m)), 'day': str(int(d))}.items()):
                    return None
                if any(key in values and not re.fullmatch(r'\d{1,5}', values[key])
                       for key in ('page', 'page_no')):
                    return None
            elif parsed.path != '/tennis/' or values:
                return None
            allowed = {'year', 'month', 'day', 'page', 'page_no'}
        else:
            safe = _url(raw, event_id)
            if not safe or not any(token in parsed.path.lower() for token in ('tennis', 'match', 'odds', 'market', 'tab')):
                return None
            allowed = {'lang', 'event', 'event_id', 'match', 'match_id', 'tab', 'market', 'period'}
            if event_id and event_id not in parsed.path.split('/') and event_id not in values.values():
                return None
        if any(key not in allowed or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value)
               for key, value in pairs):
            return None
        query = urlencode(pairs)
        return 'https://' + _HOST + parsed.path + ('?' + query if query else '')
    except (ValueError, TypeError):
        return None


def _safe_response_headers(headers: dict) -> tuple[dict, bool]:
    """Keep only low-risk representation/date headers; never retain credentials."""
    safe = {}
    sensitive = False
    for raw_key, raw_value in headers.items():
        key = str(raw_key).strip().lower()
        value = str(raw_value).strip()
        if key in {'set-cookie', 'authorization', 'proxy-authorization', 'cookie', 'www-authenticate'}:
            sensitive = True
            continue
        if key == 'content-length' or key == 'age':
            if re.fullmatch(r'\d{1,12}', value):
                safe[key] = int(value)
        elif key in {'date', 'last-modified', 'expires', 'retry-after'}:
            from email.utils import parsedate_to_datetime
            if key == 'retry-after' and re.fullmatch(r'\d{1,8}', value):
                safe[key] = int(value)
                continue
            try:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is not None:
                    safe[key] = dt.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                continue
        elif key in {'content-type', 'content-encoding', 'content-language', 'etag'}:
            if re.fullmatch(r'[A-Za-z0-9/;=._+ -]{1,100}', value):
                safe[key] = value
        elif key in {'cache-control', 'vary'}:
            if re.fullmatch(r'[A-Za-z0-9=,;._* -]{1,160}', value):
                safe[key] = value
    return safe, sensitive


def _sanitize_listing_replay(html: str, target_date: str) -> tuple[str, dict]:
    """Retain parser-relevant listing rows while excluding scripts/forms/secrets."""
    soup = BeautifulSoup(html[:2_000_000], 'html.parser')
    output = BeautifulSoup('<table><tbody></tbody></table>', 'html.parser')
    tbody = output.tbody
    omissions = set()
    rows = [row for row in soup.find_all('tr')
            if row.has_attr('data-dt') or 'js-tournament' in (row.get('class') or [])]
    if len(rows) > 3000:
        omissions.add('listing_row_limit')
        rows = rows[:3000]
    nodes_seen = 0
    def clone(node, parent):
        nonlocal nodes_seen
        nodes_seen += 1
        if nodes_seen > 30000:
            omissions.add('listing_node_limit')
            return
        if isinstance(node, NavigableString):
            text = ' '.join(str(node).split())
            if not text:
                return
            if len(text) > 160 or _SECRET.search(text) or re.search(r'[A-Za-z0-9]{24,}', text):
                omissions.add('listing_text_omitted')
                return
            parent.append(text)
            return
        if not getattr(node, 'name', None):
            return
        if node.name not in {'tr', 'td', 'th', 'a', 'span', 'div', 'b', 'strong', 'i', 'em'}:
            omissions.add('listing_element_omitted')
            return
        tag = output.new_tag(node.name)
        for key, raw in node.attrs.items():
            value = ' '.join(raw) if isinstance(raw, list) else str(raw or '').strip()
            if key == 'data-dt' and node.name == 'tr' and re.fullmatch(r'\d{1,2},\d{1,2},\d{4},\d{1,2},\d{1,2}', value):
                tag[key] = value
            elif key in {'data-status', 'data-state', 'data-live', 'aria-label', 'title'}:
                if len(value) <= 80 and not _SECRET.search(value) and not re.search(r'[A-Za-z0-9]{24,}', value):
                    tag[key] = value
                else:
                    omissions.add('listing_status_attribute_omitted')
            elif key == 'href':
                public = _url(value, '')
                if public:
                    tag[key] = public
                else:
                    omissions.add('listing_link_omitted')
            elif key == 'data-odd' and re.fullmatch(r'\d{1,2}(?:\.\d{1,3})?', value):
                tag[key] = value
            elif key in {'data-pos', 'data-bookie'} and re.fullmatch(r'[A-Za-z0-9_-]{1,12}', value):
                tag[key] = value
            elif key == 'data-bookmaker-name' and _value(value):
                tag[key] = value
            elif key == 'class':
                keep = [part for part in value.split() if part in {'js-tournament', 'table-main__tt'}
                        or re.fullmatch(r'(?:live|scheduled|postponed|cancelled|canceled|retired|walkover|finished|fin)(?:[-_][a-z0-9]+){0,2}', part, re.I)]
                if keep:
                    tag['class'] = keep
            elif key in {'hidden', 'style', 'aria-hidden'}:
                # Parser needs only the signal's presence, not arbitrary CSS.
                tag[key] = ''
        parent.append(tag)
        for child in node.children:
            clone(child, tag)
    for row in rows:
        clone(row, tbody)
    replay = str(output)
    if len(replay.encode('utf-8')) > 1_000_000:
        return '', {'omissions': sorted(omissions | {'listing_output_size_limit'}),
                    'row_count': len(rows), 'material_sufficiency': 'insufficient'}
    sufficiency = 'parser_replay_retained' if not omissions else 'partial_parser_replay'
    return replay, {'omissions': sorted(omissions), 'row_count': len(rows),
                    'material_sufficiency': sufficiency,
                    'input_truncated': len(html) > 2_000_000}


def _listing_coverage_signals(html: str, target_date: str) -> dict:
    """Record visible page/pagination cues without treating them as completeness."""
    from racketfactory.sources.betexplorer import _listing_status_state, _is_match_link
    soup = BeautifulSoup(html[:2_000_000], 'html.parser')
    rows = soup.find_all('tr', attrs={'data-dt': True})
    states = {}
    ids = set()
    paths = set()
    for row in rows:
        state, _ = _listing_status_state(row)
        states[state] = states.get(state, 0) + 1
        for anchor in row.find_all('a', href=True):
            context = _is_match_link(str(anchor.get('href') or ''))
            if context:
                path = urlsplit(str(anchor.get('href') or '')).path
                paths.add(path)
                ids.add(path.rstrip('/').split('/')[-1])
    controls = []
    candidates = soup.select('a[rel], nav a, [class*="pagination"] a, [data-page], [aria-current]')
    for node in candidates[:50]:
        label = ' '.join(node.get_text(' ', strip=True).split())[:40]
        if _SECRET.search(label) or re.search(r'[A-Za-z0-9]{24,}', label):
            label = ''
        href = _safe_capture_url(urljoin('https://' + _HOST, str(node.get('href') or '')),
                                 stage='listing', target_date=target_date)
        controls.append({'label': label, 'path': href,
                         'rel': str(node.get('rel') or '')[:32],
                         'aria_current': str(node.get('aria-current') or '')[:16],
                         'disabled_signal': any(k in node.attrs for k in ('disabled', 'aria-disabled'))})
    text = soup.get_text(' ', strip=True)
    count_claims = [list(map(int, m.groups())) for m in re.finditer(r'\b(\d{1,5})\s*[-–]\s*(\d{1,5})\s+(?:of|/|from)\s+(\d{1,6})\b', text, re.I)][:10]
    page_position_claims = [list(map(int, m.groups())) for m in
                            re.finditer(r'\bpage\s+(\d{1,5})\s+(?:of|/)\s+(\d{1,5})\b', text, re.I)][:10]
    completion_phrases = []
    for marker, pattern in (('no_more_results', r'\bno\s+more\s+(?:results|matches)\b'),
                            ('end_of_results', r'\bend\s+of\s+(?:results|matches)\b'),
                            ('all_results_loaded', r'\ball\s+(?:results|matches)\s+loaded\b')):
        if re.search(pattern, text, re.I):
            completion_phrases.append(marker)
    return {'requested_date': target_date, 'provider_timezone': 'not stated/unknown',
            'data_dt_row_count': len(rows), 'row_status_state_counts': states,
            'event_id_count': len(ids), 'event_ids_from_public_match_paths': sorted(ids)[:1000],
            'match_path_count': len(paths), 'pagination_control_count': len(candidates),
            'pagination_controls': controls, 'visible_range_count_claims': count_claims,
            'visible_page_position_claims': page_position_claims,
            'visible_completion_claims': completion_phrases,
            'provider_page_count': None, 'provider_completion_marker': None,
            'coverage_status': 'not_established',
            'coverage_limitations': ['single rendered response only', 'pagination semantics not authenticated',
                                     'response rows do not prove requested-day completeness',
                                     'provider timezone/day-boundary semantics not stated']}


class EvidenceCapture:
    """One bounded evidence-only capture; hashes retained replay bodies, not just originals."""
    MAX_RESPONSES = 6  # two listing URLs, one detail, one comparison with existing transport fallback
    STAGE_LIMITS = {'listing': 4, 'detail': 1, 'comparison': 1}

    def __init__(self, target_date: str):
        path = directory()
        if path is None:
            raise RuntimeError('RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR must be set')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', target_date):
            raise ValueError('invalid evidence target date')
        from datetime import date as date_type
        date_type.fromisoformat(target_date)
        import uuid
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.target_date = target_date
        self.capture_id = uuid.uuid4().hex
        self.file = path / f'betexplorer_capture_{target_date}_{self.capture_id}.json'
        now = datetime.now(timezone.utc)
        self.data = {'schema_version': 2, 'capture_id': self.capture_id,
                     'purpose': 'bounded evidence only; no executable quotes',
                     'target_date': target_date, 'target_timezone': 'Africa/Johannesburg',
                     'target_local_offset': now.astimezone(ZoneInfo('Africa/Johannesburg')).utcoffset().total_seconds(),
                     'provider_schedule_timezone': 'unknown unless explicitly present in retained page material',
                     'provider_completeness': 'not_established', 'responses': [],
                     'selected_event': None,
                     'linkage': {'listing_response_id': None, 'listing_event_id': None,
                                 'detail_response_id': None, 'detail_event_id': None,
                                 'comparison_response_id': None, 'comparison_event_id': None,
                                 'listing_event_status': 'not_established',
                                 'detail_event_status': 'not_established',
                                 'comparison_event_status': 'not_established',
                                 'status': 'not_established'},
                     'notice': 'Bodies are parser-replay material with allowlisted redactions, not original raw bytes. Digests authenticate retained bytes only; they prove neither provider completeness nor quote applicability.'}
        self._stage_counts = {}
        self._write()

    def _write(self):
        import tempfile
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path, delete=False,
                                             encoding='utf-8') as stream:
                tmp = Path(stream.name)
                json.dump(self.data, stream, indent=2, sort_keys=True)
                stream.flush()
                import os
                os.fsync(stream.fileno())
            tmp.replace(self.file)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)

    def link_fixture(self, fixture: dict) -> dict:
        identity = _identity(fixture)
        if not identity:
            self.data['linkage']['status'] = 'fixture_identity_unverified'
            self._write()
            return {'status': 'fixture_identity_unverified'}
        listing = next((r for r in self.data['responses'] if r.get('stage') == 'listing'
                        and r.get('http_status') == 200), None)
        paths = set((listing or {}).get('coverage_signals', {}).get('match_paths', []))
        exact_path = identity['match_url'] in paths
        self.data['selected_event'] = identity
        self.data['linkage'].update(listing_response_id=(listing or {}).get('response_id'),
                                    listing_event_id=identity['event_id'] if exact_path else None,
                                    listing_event_status='listing_path_match' if exact_path else 'listing_path_not_observed',
                                    status='listing_path_match' if exact_path else 'listing_path_not_observed')
        self._write()
        return {'status': self.data['linkage']['status'], 'event_id': identity['event_id'],
                'match_url': identity['match_url']}

    def observe(self, stage: str, metadata: dict):
        if stage not in self.STAGE_LIMITS:
            return
        count = self._stage_counts.get(stage, 0)
        if count >= self.STAGE_LIMITS[stage] or len(self.data['responses']) >= self.MAX_RESPONSES:
            self.data.setdefault('capture_limits', []).append({'stage': stage, 'reason': 'response_observation_limit'})
            self._write()
            return
        self._stage_counts[stage] = count + 1
        event = self.data.get('selected_event') or {}
        requested = _safe_capture_url(str(metadata.get('requested_url') or ''), stage=stage,
                                      target_date=self.target_date, event_id=str(event.get('event_id') or ''))
        final = _safe_capture_url(str(metadata.get('final_url') or ''), stage=stage,
                                  target_date=self.target_date, event_id=str(event.get('event_id') or ''))
        headers, sensitive_headers = _safe_response_headers(metadata.get('headers') or {})
        body_bytes = bytes(metadata.get('body_bytes') or b'')
        body_text = str(metadata.get('body_text') or '')
        import hashlib
        record = {'response_id': f'{stage}-{count + 1}', 'stage': stage,
                  'requested_url': requested, 'requested_url_redacted': requested is None,
                  'final_url': final, 'final_url_redacted': bool(metadata.get('final_url')) and final is None,
                  'request_started_at': metadata.get('request_started_at'),
                  'response_received_at': metadata.get('response_received_at'),
                  'http_status': metadata.get('http_status'), 'safe_response_headers': headers,
                  'sensitive_response_headers_omitted': sensitive_headers,
                  'transport': metadata.get('transport') if metadata.get('transport') in {'requests', 'curl_cffi', 'playwright'} else 'unknown',
                  'response_body_bytes': len(body_bytes),
                  'response_body_sha256': hashlib.sha256(body_bytes).hexdigest() if body_bytes else None,
                  'parser_replay_body': None, 'retained_body_sha256': None,
                  'retained_body_bytes': 0, 'body_retention_status': 'not_retained',
                  'body_omissions': []}
        if metadata.get('http_status') == 200 and body_text and len(body_bytes) <= 2_000_000:
            if stage == 'listing':
                replay, facts = _sanitize_listing_replay(body_text, self.target_date)
                record['coverage_signals'] = _listing_coverage_signals(body_text, self.target_date)
                record['coverage_signals']['match_paths'] = sorted({
                    urlsplit(str(a.get('href') or '')).path
                    for tr in BeautifulSoup(body_text[:2_000_000], 'html.parser').find_all('tr', attrs={'data-dt': True})
                    for a in tr.find_all('a', href=True) if _is_public_match_path(str(a.get('href') or ''))})[:1000]
                record['body_omissions'] = facts['omissions']
                if facts['material_sufficiency'] != 'insufficient':
                    record['parser_replay_body'] = replay
                    record['body_retention_status'] = facts['material_sufficiency']
                record['retained_listing_row_count'] = facts['row_count']
            elif self.data.get('selected_event'):
                fixture = self.data['selected_event']
                if stage == 'comparison':
                    try:
                        decoded = json.loads(body_text)
                        comparison_html = decoded.get('odds', '') if isinstance(decoded, dict) else ''
                    except (TypeError, ValueError):
                        comparison_html = ''
                    if isinstance(comparison_html, str):
                        result = capture_replay(comparison_html, 'comparison', fixture,
                            observed_at=str(metadata.get('response_received_at') or ''),
                            response_body=body_text, request_url=str(metadata.get('requested_url') or ''))
                        replay_file = self.path / ('betexplorer_replay_' + fixture['event_id'] + '.json')
                        replay = json.loads(replay_file.read_text()).get('comparison', {}).get('replay_html', '') if replay_file.exists() else ''
                        if replay and result['file_written']:
                            record['parser_replay_body'] = json.dumps({'odds': replay}, separators=(',', ':'))
                            record['body_retention_status'] = result['material_sufficiency']
                            record['body_omissions'] = json.loads(replay_file.read_text()).get('comparison', {}).get('omission_categories', [])
                else:
                    result = capture_replay(body_text, 'detail', fixture,
                        observed_at=str(metadata.get('response_received_at') or ''),
                        request_url=str(metadata.get('requested_url') or ''))
                    replay_file = self.path / ('betexplorer_replay_' + fixture['event_id'] + '.json')
                    replay_data = json.loads(replay_file.read_text()) if replay_file.exists() else {}
                    replay = replay_data.get('detail', {}).get('replay_html', '')
                    if replay and result['file_written']:
                        record['parser_replay_body'] = replay
                        record['body_retention_status'] = result['material_sufficiency']
                        record['body_omissions'] = replay_data.get('detail', {}).get('omission_categories', [])
                    record['schedule_evidence'] = replay_data.get('detail', {}).get('schedule_evidence',
                        {'status': 'not_verified', 'reason': 'no safe candidate extracted'})
            if record['parser_replay_body'] is not None:
                retained = record['parser_replay_body'].encode('utf-8')
                record['retained_body_sha256'] = hashlib.sha256(retained).hexdigest()
                record['retained_body_bytes'] = len(retained)
        if stage == 'listing':
            self.data['linkage']['listing_response_id'] = record['response_id']
        elif stage == 'detail':
            self.data['linkage']['detail_response_id'] = record['response_id']
        elif stage == 'comparison':
            self.data['linkage']['comparison_response_id'] = record['response_id']
        self.data['responses'].append(record)
        if self.data.get('selected_event') and stage in {'detail', 'comparison'}:
            links = self.data['linkage']
            listing_linked = links.get('listing_event_status') == 'listing_path_match'
            linked = bool(requested and listing_linked)
            field = 'detail' if stage == 'detail' else 'comparison'
            links[f'{field}_event_id'] = self.data['selected_event']['event_id'] if linked else None
            links[f'{field}_event_status'] = ('linked_to_listing_event' if linked else
                'request_url_unverified' if not requested else 'listing_event_link_unverified')
            if not linked:
                links['status'] = links[f'{field}_event_status']
        self._write()


def _is_public_match_path(raw: str) -> bool:
    try:
        path = urlsplit(urljoin('https://' + _HOST, raw)).path
        from racketfactory.sources.betexplorer import _is_match_link
        return _is_match_link(path) is not None and '?' not in raw and '#' not in raw
    except Exception:
        return False


def capture_replay(html: str, kind: str, fixture: dict, *, observed_at: str,
                   response_body: str | None = None, request_url: str = '') -> CaptureResult:
    """Write metadata even for empty extracts; return distinct capture facts.

    A retained fragment is never proof that all material for a new parser was
    captured. No response headers or unverified opaque IDs are retained.
    """
    path = directory()
    identity = _identity(fixture)
    unavailable = CaptureResult(file_written=False, discovery_material_retained=False,
                                material_sufficiency='insufficient')
    if path is None or identity is None or kind not in {'detail', 'comparison'}:
        return unavailable
    path.mkdir(parents=True, exist_ok=True)
    file = path / ('betexplorer_replay_' + identity['event_id'] + '.json')
    try:
        data = json.loads(file.read_text()) if file.exists() else {'version': 1, 'fixture': identity}
    except (OSError, ValueError):
        return unavailable
    if data.get('fixture') != identity:
        return unavailable
    source = html[:2_000_000]
    soup = BeautifulSoup(source, 'html.parser')
    out = BeautifulSoup('<div></div>', 'html.parser')
    root = out.div
    dropped = False
    omissions = set()
    material_count = 0
    request_or_initializer_count = 0
    count = 0
    # Only IDs explicitly bound to both selected listing participants on the
    # same page element can be retained as public participant references. This
    # records the provider's claimed relationship; it does not verify it.
    participant_ids = {}
    id_conflict = False
    for node in soup.select('[data-home][data-away][data-home-id][data-away-id]')[:100]:
        if (_person(node.get('data-home')) != identity['player_home'] or
                _person(node.get('data-away')) != identity['player_away']):
            continue
        home_id, away_id = str(node.get('data-home-id') or ''), str(node.get('data-away-id') or '')
        if (home_id == away_id or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,12}', v)
                or _SECRET.search(v) for v in (home_id, away_id))):
            continue
        mapping = {'home': home_id, 'away': away_id}
        if participant_ids and participant_ids != mapping:
            id_conflict = True
            break
        participant_ids = mapping
    if id_conflict:
        participant_ids = {}
        dropped = True
        omissions.add('conflicting_participant_identifiers')
    # Preserve relevant elements in document order; ancestor nesting is not
    # needed by the current endpoint parser, but tables/rows are retained for
    # comparison response orientation. Values are allowlisted per attribute.
    if kind == 'detail':
        nodes = soup.select('[data-url], [data-endpoint], [data-ajax-url], a[href], [data-tab], [data-market], [data-period], [data-home], [data-away], h1, h2')
    else:
        nodes = soup.select('table, div.oddsComparisonAll__rowBookie')
    def clean(node, parent):
        nonlocal count, dropped, material_count, request_or_initializer_count
        if count >= 250:
            dropped = True
            omissions.add('node_limit')
            return
        count += 1
        tag = out.new_tag(node.name)
        for k, v in node.attrs.items():
            if k not in _ATTRIBUTES:
                if k.startswith('data-') and any(w in k for w in ('odds', 'market', 'period', 'tab', 'event', 'endpoint', 'url')):
                    dropped = True
                    omissions.add('unsupported_relevant_attribute')
                continue
            if k == 'title' and (kind != 'comparison' or node.name not in {'img', 'a'}):
                continue
            if k == 'class':
                allowed = [x for x in (v if isinstance(v, list) else str(v).split())
                           if x in {'average', 'oddsComparisonAll__rowBookie'}]
                if allowed:
                    tag[k] = ' '.join(allowed)
                continue
            if k in {'data-home-id', 'data-away-id'}:
                side = 'home' if k == 'data-home-id' else 'away'
                value = str(v) if participant_ids and str(v) == participant_ids.get(side) and _person(node.get('data-home')) == identity['player_home'] and _person(node.get('data-away')) == identity['player_away'] else None
            elif k in {'data-home', 'data-away'}:
                person = _person(v)
                value = person if person in {identity['player_home'], identity['player_away']} else None
            else:
                value = _url(str(v), identity['event_id']) if k in _URL_ATTRS else _value(v)
            if value is None:
                dropped = True
                omissions.add('untrusted_or_sensitive_attribute')
            else:
                tag[k] = value
                if kind == 'detail':
                    if k in {'data-tab', 'data-tab-id', 'data-market', 'data-market-name', 'data-market-id', 'data-period', 'data-period-id', 'data-scope'}:
                        material_count += 1
                    elif k in _URL_ATTRS and any(word in value.lower() for word in ('odds', 'market', 'tab')):
                        material_count += 1
                        request_or_initializer_count += 1
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
                omissions.add('unallowlisted_page_text')
        if kind == 'comparison':
            for child in node.children:
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text and (_person(text) in {identity['player_home'], identity['player_away']} or
                                 text.lower() in {'bookmaker', 'average', 'avg', '1', '2'} or re.fullmatch(r'\d{1,2}\.\d{1,3}', text)):
                        tag.append(text)
                    elif text:
                        dropped = True
                        omissions.add('unallowlisted_page_text')
                elif getattr(child, 'name', '') in {'thead', 'tbody', 'tr', 'th', 'td', 'img', 'a', 'span', 'div'}:
                    clean(child, tag)
    for node in nodes[:250]:
        clean(node, root)
    if len(nodes) > 250:
        dropped = True
        omissions.add('node_limit')
    calls = []
    if kind == 'detail':
        scripts = soup.find_all('script')
        if len(scripts) > 100:
            dropped = True
            omissions.add('script_limit')
        for script in scripts[:100]:
            text = script.string or ''
            if len(text) > 100_000:
                dropped = True
                omissions.add('script_size_limit')
            # Scripts are never copied wholesale. Even retained calls may not
            # describe external or dynamic JavaScript required by the page.
            if text.strip() or script.get('src'):
                dropped = True
                if text.strip():
                    omissions.add('arbitrary_script_content_omitted')
                if script.get('src'):
                    omissions.add('external_script_omitted')
            source_script = text[:100_000]
            matches = list(_CALL.finditer(source_script))
            # A nested, multiline or otherwise unrecognised initializer is not
            # silently presented as reconstructable by the allowlisted calls.
            matched_positions = {m.start(1) for m in matches}
            for possible in re.finditer(r'\b[A-Za-z_$][\w$]*\s*\(', source_script):
                if (re.search(r'(?i)match|odds?|market|tab', possible.group())
                        and possible.start() not in matched_positions):
                    omissions.add('initializer_shape_not_recognized')
                    dropped = True
                    break
            for m in matches:
                call = m.group(0).strip()
                args = re.findall(r"['\"]([^'\"]+)['\"]", m.group(2))
                # Arbitrary string literals could be credentials. Retain only
                # the event identity and short, public market/tab vocabulary.
                public = {identity['event_id'].casefold(), 't', 'en', 'tennis', 'match',
                          'odds', 'market', 'tab', 'h2h', '1x2', 'winner', 'full'}
                public.update(v.casefold() for v in participant_ids.values())
                if (_ARG.fullmatch(m.group(2)) and not _SECRET.search(call)
                        and all(arg.casefold() in public for arg in args)):
                    calls.append(call)
                    material_count += 1
                    request_or_initializer_count += 1
                else:
                    dropped = True
                    omissions.add('initializer_arguments_or_syntax_not_allowlisted')
                if len(calls) == 30:
                    dropped = True
                    omissions.add('initializer_limit')
                    break
            if len(calls) == 30:
                break
        for call in calls:
            tag = out.new_tag('script')
            tag.string = call
            root.append(tag)
    if kind == 'comparison':
        # A table wrapper alone is not bookmaker/column evidence.
        material_count = int(bool(root.select('[data-odd]')) and bool(root.select('[data-bookie], [data-bookmaker-name], img[title], a[title]')))
    evidence = str(root)
    if len(evidence.encode()) > 64_000:
        evidence = ''  # never publish invalid, silently truncated replay HTML
        dropped = True
        material_count = 0
        omissions.add('output_size_limit')
    if len(html) > 2_000_000:
        omissions.add('input_size_limit')
    if not material_count:
        omissions.add('no_relevant_discovery_material' if kind == 'detail' else 'no_bookmaker_price_structure')
    if kind == 'detail' and not request_or_initializer_count:
        omissions.add('no_retained_request_or_initializer')
    insufficient = bool(omissions & {'no_relevant_discovery_material', 'no_bookmaker_price_structure',
                                     'initializer_arguments_or_syntax_not_allowlisted',
                                     'conflicting_participant_identifiers', 'node_limit', 'initializer_limit',
                                     'output_size_limit', 'input_size_limit', 'script_limit', 'script_size_limit',
                                     'initializer_shape_not_recognized', 'no_retained_request_or_initializer',
                                     'unsupported_relevant_attribute', 'untrusted_or_sensitive_attribute'})
    payload = html if response_body is None else response_body
    data[kind] = {'http_status': 200, 'observed_at': observed_at,
                  'response_sha256': hashlib.sha256(payload.encode()).hexdigest(),
                  'hash_basis': 'UTF-8 encoding of decoded response text, before sanitisation',
                  'observed_request_path': _url(request_url, identity['event_id']) if request_url else None,
                  'replay_html': evidence, 'input_truncated': len(html) > 2_000_000,
                  'redactions_or_limits': dropped or len(html) > 2_000_000,
                  'file_written': True, 'discovery_material_retained': material_count > 0,
                  'material_sufficiency': 'insufficient' if insufficient else 'not_established',
                  'omission_categories': sorted(omissions),
                  'redaction_policy': 'Only allowlisted DOM attributes, public same-host paths with selected query keys, and simple initializer calls with public arguments. All other page text, headers, cookies, form data, scripts and arguments omitted.',
                  'notice': 'Allowlisted evidence only; unretained DOM/scripts may be essential. Never execute this HTML.'}
    if kind == 'detail' and fixture.get('listing_schedule_status') == 'unverified':
        # Persist only strictly matched, bounded public fields. A failure
        # is deliberately not described as absence of JSON-LD or proof that
        # the fixture is started; unallowlisted raw scripts are never saved.
        from racketfactory.sources.betexplorer import _detail_scheduled_start
        schedule_reasons = []
        schedule = _detail_scheduled_start(html, fixture, observed_at=observed_at,
                                           rejection_reasons=schedule_reasons)
        schedule_record = {'status': 'not_verified', 'listing_coverage': 'not_established',
                           'rejection_reasons': schedule_reasons[:12]}
        if schedule:
            schedule_record.update(status='candidate_offset_sportsevent', normalised_fields=True,
                                   contract_validation='not_authenticated',
                                   response_sha256=hashlib.sha256(html.encode()).hexdigest(),
                                   observed_at=observed_at, start_date=schedule[2],
                                   match_date_sast=schedule[0], match_time_sast=schedule[1],
                                   event_url=identity['match_url'], player_home=identity['player_home'],
                                   player_away=identity['player_away'],
                                   event_status='https://schema.org/EventScheduled', sport='Tennis')
        data[kind]['schedule_evidence'] = schedule_record
    file.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding='utf-8')
    return CaptureResult(file_written=True, discovery_material_retained=material_count > 0,
                         material_sufficiency=data[kind]['material_sufficiency'])


class BrowserObservation:
    """Intercept only requests initiated by one selected page, within its old budget.

    The original probe allowed one detail and one comparison fetch after its
    listing. The observer substitutes for those requests, not in addition to
    them. External resources and external scripts are blocked; consequently a
    page may not work and an empty observation is never proof of absence.
    """
    MAX_REQUESTS = 2
    MAX_BODY_BYTES = 512_000

    def __init__(self, fixture: dict, evidence_capture=None):
        identity = _identity(fixture)
        if not identity:
            raise ValueError('fixture identity is not established')
        self.fixture = fixture
        self.identity = identity
        self.evidence_capture = evidence_capture
        self._request_started = {}
        self.document_url = 'https://' + _HOST + identity['match_url']
        self.allowed = 0
        self.document_seen = False
        self.pending = False
        self.stopped = False
        self.seen = set()
        self._responses = {}
        self.result = {'requests_allowed': 0, 'requests_blocked': {}, 'responses': [],
                       'restriction': None, 'detail_file_written': False,
                       'detail_material_retained': False, 'detail_material_sufficiency': 'not_evaluated',
                       'comparison_file_written': False, 'comparison_material_retained': False,
                       'identity_confirmation': 'listing_only_page_initiation_not_independent_identity',
                       'quote_eligibility': 'not_evaluated', 'budget': self.MAX_REQUESTS,
                       'notice': 'Only page-initiated same-host GETs; scripts and other resources blocked. No settlement or market identity established.'}

    def _block(self, route, reason):
        counts = self.result['requests_blocked']
        counts[reason] = counts.get(reason, 0) + 1
        route.abort()

    def handle(self, route, request):
        from racketfactory.sources._page_odds import _throttle
        if self.stopped:
            return self._block(route, 'stopped')
        if self.pending:
            return self._block(route, 'prior_request_in_flight')
        if request.method != 'GET' or request.redirected_from is not None:
            return self._block(route, 'method_or_redirect')
        kind = request.resource_type
        raw_url = request.url
        if len(raw_url) > 2048:
            return self._block(route, 'url_too_long')
        if kind == 'document' and not self.document_seen and raw_url == self.document_url:
            safe_path = self.identity['match_url']
        elif kind in {'xhr', 'fetch'} and self.document_seen:
            safe_path = _url(raw_url, self.identity['event_id'])
            parsed = urlsplit(raw_url)
            event_id = self.identity['event_id']
            identifiers = [value for key, value in parse_qsl(parsed.query)
                           if key in {'event', 'event_id', 'match', 'match_id'}]
            identity_bound = event_id in parsed.path.split('/') or event_id in identifiers
            if (not safe_path or not identity_bound or any(value != event_id for value in identifiers)
                    or not any(x in parsed.path.lower() for x in ('odds', 'market', 'tab'))):
                return self._block(route, 'unverified_request')
        else:
            return self._block(route, 'resource_not_allowed')
        if raw_url in self.seen:
            return self._block(route, 'duplicate')
        if self.allowed >= self.MAX_REQUESTS:
            return self._block(route, 'budget')
        self.seen.add(raw_url)
        self._request_started[raw_url] = datetime.now(timezone.utc).isoformat()
        self.allowed += 1
        self.pending = True
        self.result['requests_allowed'] = self.allowed
        if kind == 'document':
            self.document_seen = True
        try:
            # The browser performs the exact request it initiated; no replay,
            # route guessing, redirects, or retries are added by the observer.
            _throttle(_HOST)
            route.continue_()
        except Exception:
            self.stopped = True
            self._block(route, 'transport_failure')

    def on_response(self, response):
        request = response.request
        if request.url not in self.seen:
            return
        kind = request.resource_type
        safe_path = (self.identity['match_url'] if kind == 'document'
                     else _url(request.url, self.identity['event_id']))
        status = response.status
        entry = {'resource': kind, 'path': safe_path, 'http_status': status, 'body_captured': False}
        self.result['responses'].append(entry)
        self._responses[request.url] = (response, entry)
        if status in (401, 403, 429):
            self.stopped = True
            self.result['restriction'] = 'rate_limit' if status == 429 else 'access_restricted'
            if status == 429:
                from racketfactory.quota_guard import record_cooldown
                try:
                    record_cooldown('betexplorer', 'host:' + _HOST, 'rate_limit', 900)
                except OSError:
                    pass

    def on_request_failed(self, request):
        if request.url in self.seen:
            self.stopped = True
            self.pending = False
            self.result['response_error'] = 'request_failed'

    def on_request_finished(self, request):
        pair = self._responses.get(request.url)
        if not pair:
            return
        response, entry = pair
        kind = request.resource_type
        try:
            if self.stopped or response.status != 200:
                return
            length = response.headers.get('content-length', '')
            if not length.isdigit() or int(length) > self.MAX_BODY_BYTES:
                self.stopped = True
                entry['capture_failure'] = 'body_length_unknown_or_large'
                return
            body = response.body()
            if len(body) > self.MAX_BODY_BYTES:
                self.stopped = True
                entry['capture_failure'] = 'body_too_large'
                return
            text = body.decode('utf-8', errors='replace')
            if any(marker in text[:6000].lower() for marker in
                   ('cf-chl', 'just a moment', 'attention required', 'verify you are human')):
                self.stopped = True
                self.result['restriction'] = 'challenge'
                return
            now = datetime.now(timezone.utc).isoformat()
            if self.evidence_capture is not None:
                self.evidence_capture.observe('detail' if kind == 'document' else 'comparison', {
                    'requested_url': request.url, 'final_url': response.url,
                    'request_started_at': self._request_started.get(request.url),
                    'response_received_at': now, 'http_status': response.status,
                    'headers': dict(response.headers), 'body_bytes': body,
                    'body_text': text, 'transport': 'playwright'})
            if kind == 'document':
                result = capture_replay(text, 'detail', self.fixture, observed_at=now, request_url=request.url)
                self.result['detail_file_written'] = result['file_written']
                self.result['detail_material_retained'] = result['discovery_material_retained']
                self.result['detail_material_sufficiency'] = result['material_sufficiency']
            else:
                try:
                    decoded = json.loads(text)
                    html = decoded.get('odds', '') if isinstance(decoded, dict) else ''
                except ValueError:
                    html = text if text.lstrip().startswith('<') else ''
                if not isinstance(html, str):
                    html = ''
                result = capture_replay(html, 'comparison', self.fixture, observed_at=now,
                                        request_url=request.url, response_body=text)
                self.result['comparison_file_written'] = result['file_written']
                self.result['comparison_material_retained'] = result['discovery_material_retained']
            entry['body_captured'] = result['file_written']
        except Exception:
            self.stopped = True
            self.result['response_error'] = 'unavailable_or_capture_failed'
        finally:
            self.pending = False



def observe_in_browser(fixture: dict, *, evidence_capture=None) -> dict:
    """One bounded, non-interactive browser navigation, never a quote source."""
    observer = BrowserObservation(fixture, evidence_capture=evidence_capture)
    from racketfactory.quota_guard import cooldown_status
    if cooldown_status('betexplorer', 'host:' + _HOST):
        observer.result['restriction'] = 'existing_cooldown'
        return observer.result
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers='block', accept_downloads=False)
                context.route('**/*', observer.handle)
                context.on('response', observer.on_response)
                context.on('requestfinished', observer.on_request_finished)
                context.on('requestfailed', observer.on_request_failed)
                page = context.new_page()
                try:
                    page.goto(observer.document_url, wait_until='domcontentloaded', timeout=15000)
                    page.wait_for_timeout(2000)
                except Exception:
                    observer.result['navigation'] = 'incomplete'
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception:
        observer.result['navigation'] = 'browser_unavailable_or_failed'
    return observer.result
