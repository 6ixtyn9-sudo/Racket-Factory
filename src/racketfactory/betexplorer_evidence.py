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

    def __init__(self, fixture: dict):
        identity = _identity(fixture)
        if not identity:
            raise ValueError('fixture identity is not established')
        self.fixture = fixture
        self.identity = identity
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



def observe_in_browser(fixture: dict) -> dict:
    """One bounded, non-interactive browser navigation, never a quote source."""
    observer = BrowserObservation(fixture)
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
