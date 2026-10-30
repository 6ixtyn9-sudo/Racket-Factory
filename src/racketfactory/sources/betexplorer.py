"""BetExplorer tennis odds adapter (Edge-Factory parity).

Transport (proven by Edge-Factory on the same runners): plain requests with
a simple UA first, 3s per-host throttle, dated results URLs for any date::

    /tennis/results/?year=YYYY&month=MM&day=DD

Listing parse is Edge-style primary (``tr.js-tournament`` headers +
``tr[data-dt]`` match rows + ``td.table-main__tt`` names) with the generic
row scanner as fallback. Listing prices are indicative consensus only;
they do not establish an executable single-bookmaker ticket quote.

Finished/retired/walkover rows are skipped: only actionable pre-match
prices are wanted. Disable with RACKET_FACTORY_DISABLE_BETEXPLORER=1.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from racketfactory.fetch_cache import cached_fetch
from racketfactory.sources import _page_odds as po
from racketfactory.sources._page_odds import fetch_page_html, parse_listing_page, PageFetchError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.betexplorer.com"
RESULTS_URL = BASE_URL + "/tennis/results/?year={y}&month={m}&day={d}"
TENNIS_URL = BASE_URL + "/tennis/"
SOURCE_NAME = "BetExplorer"
BOOK_LABEL = "BetExplorer consensus"
DISABLE_ENV = "RACKET_FACTORY_DISABLE_BETEXPLORER"

def _is_match_link(href: str) -> dict[str, Any] | None:
    """Match links are /tennis/<cat>/<tournament>/<slug>/<id>/ (5 segments)."""
    try:
        path = urlsplit(href).path
    except Exception:
        return None
    segs = [s for s in path.split("/") if s]
    if len(segs) != 5 or segs[0] != "tennis":
        return None
    return {"tour_hint": segs[1].replace("-", " "), "tournament": segs[2].replace("-", " ")}


def _structured_participants(anchors: list[Tag], *, rejection_reasons: list[str] | None = None) -> tuple[str, str] | None:
    """Extract explicit participant spans without inferring name boundaries.

    A single same-event anchor with two explicit participant spans is the
    retained BetExplorer shape. Multiple trailing initials, including
    ``J-L.``, are tokens inside the span, not a guessed separator between
    players. ``rejection_reasons`` receives bounded reason codes for the
    existing redacted evidence capture; it never receives page text.
    """
    from racketfactory.betexplorer_evidence import _url
    from racketfactory.settlement import players_match

    def reject(code: str):
        if rejection_reasons is not None and code not in rejection_reasons:
            rejection_reasons.append(code)
        return None

    if len(anchors) not in (1, 2):
        return reject('participant_anchor_count')
    paths = []
    for anchor in anchors:
        href = str(anchor.get('href') or '')
        path = _url(href, '')
        if not path or not _is_match_link(href):
            return reject('participant_link_invalid')
        if any(key in anchor.attrs for key in ('hidden', 'style', 'aria-hidden')):
            return reject('participant_anchor_hidden_signal')
        paths.append(path)
    if len(set(paths)) != 1:
        return reject('participant_anchor_event_conflict')
    if len(anchors) == 2:
        parent = anchors[0].parent
        if parent is not anchors[1].parent or not isinstance(parent, Tag) or parent.name not in {'td', 'div'}:
            return reject('participant_sibling_anchors_not_co_located')
        if any(key in parent.attrs for key in ('hidden', 'style', 'aria-hidden')):
            return reject('participant_container_hidden_signal')
        for child in parent.children:
            if isinstance(child, Comment):
                return reject('participant_container_comment')
            if isinstance(child, NavigableString):
                if str(child).strip().casefold() not in {'', '-', '–', '—', 'vs', 'vs.'}:
                    return reject('participant_container_unexpected_text')
            elif not isinstance(child, Tag) or not any(child is anchor for anchor in anchors):
                return reject('participant_container_extra_element')

    spans = []
    for anchor in anchors:
        direct = []
        for child in anchor.children:
            if isinstance(child, Comment):
                return reject('participant_anchor_comment')
            if isinstance(child, NavigableString):
                if str(child).strip():
                    return reject('participant_anchor_text_outside_span')
            elif isinstance(child, Tag) and child.name == 'span':
                direct.append(child)
            else:
                return reject('participant_anchor_child_tag')
        if len(direct) != (2 if len(anchors) == 1 else 1):
            return reject('participant_span_count')
        spans.extend(direct)

    def participant(span: Tag) -> str | None:
        for node in [span, *span.find_all(True)]:
            if node.name not in {'span', 'b', 'strong', 'i', 'em'}:
                return None
            if any(key in node.attrs for key in ('hidden', 'style', 'aria-hidden')):
                return None
            if any(re.search(r'(?i)(?:score|odds|status|ranking|seed|winner)', cls)
                   for cls in node.get('class', [])):
                return None
        if span.find(string=lambda value: isinstance(value, Comment)):
            return None
        text = span.get_text(' ', strip=True)
        if not text or len(text) > 100 or any(c.isdigit() for c in text):
            return None
        if any(not (c.isalpha() or c.isspace() or c in ".'’-/") for c in text):
            return None
        teams = [value.strip() for value in text.split('/')]
        if len(teams) not in (1, 2) or any(not value for value in teams):
            return None
        initial = re.compile(r'(?:[A-Z](?:-[A-Z])*)\.')
        for player in teams:
            words = player.split()
            if not 2 <= len(words) <= 8:
                return None
            end = len(words)
            while end and initial.fullmatch(words[end - 1]):
                end -= 1
            if end == 0 or end == len(words):
                return None
            if any(not any(c.isalpha() for c in word) or '.' in word for word in words[:end]):
                return None
            if any(word.casefold() in {'winner', 'finished', 'retired', 'walkover', 'cancelled', 'live', 'set'}
                   for word in words[:end]):
                return None
        return text

    participants = [participant(span) for span in spans]
    if not all(participants):
        return reject('participant_name_format')
    home, away = participants
    if (home.casefold() == away.casefold()
            or len(home.split('/')) != len(away.split('/'))):
        return reject('participant_pair_ambiguous')
    members = [part.strip() for team in (home, away) for part in team.split('/')]
    if any(players_match(a, b)[0] for i, a in enumerate(members) for b in members[i + 1:]):
        return reject('participant_pair_identity_conflict')
    return home, away


_STATUS_TOKEN = re.compile(
    r"(?i)\b(FINISHED|FIN|RETIRED|RET\.?|WALKOVER|W\.?\s?O\.?|AWARDED|"
    r"CANCELLED|CANCELED|LIVE|IN\s+PLAY|SCHEDULED|POSTPONED)\b"
)
_TERMINAL_STATUS = frozenset({'FIN', 'FINISHED', 'RETIRED', 'RET', 'WALKOVER', 'WO', 'AWARDED', 'CANCELLED', 'CANCELED'})
_NONTERMINAL_STATUS = frozenset({'LIVE', 'IN PLAY', 'SCHEDULED', 'POSTPONED'})


def _listing_status_state(tr: Tag) -> tuple[str, list[str]]:
    """Return an explicit, bounded row-status classification.

    Visible row text and status-bearing attributes/classes are evidence; an
    absent marker is not treated as scheduled. Contradictory terminal and
    nonterminal signals remain unresolved. No date/time is inferred here.
    """
    signals: set[str] = set()
    for match in _STATUS_TOKEN.finditer(tr.get_text(' ', strip=True)):
        token = re.sub(r'\s+', ' ', match.group(1).strip().upper()).rstrip('.')
        if token == 'W/O':
            token = 'WO'
        signals.add(token)
    attr_keys = ('data-status', 'data-state', 'data-live')
    for node in [tr, *tr.find_all(True)]:
        for key in attr_keys:
            if node.has_attr(key):
                value = str(node.get(key) or '').strip()
                found = {re.sub(r'\s+', ' ', item.group(1).strip().upper()).rstrip('.')
                         for item in _STATUS_TOKEN.finditer(value)}
                if found:
                    signals.update(found)
                else:
                    signals.add('UNRECOGNIZED_STATUS_ATTRIBUTE')
        for key in ('aria-label', 'title'):
            value = str(node.get(key) or '')
            signals.update(re.sub(r'\s+', ' ', item.group(1).strip().upper()).rstrip('.')
                           for item in _STATUS_TOKEN.finditer(value))
        for cls in node.get('class', []):
            signals.update(re.sub(r'\s+', ' ', item.group(1).strip().upper()).rstrip('.')
                           for item in _STATUS_TOKEN.finditer(str(cls).replace('-', ' ').replace('_', ' ')))
    signals.discard('')
    terminal = signals & _TERMINAL_STATUS
    nonterminal = signals & _NONTERMINAL_STATUS
    if 'UNRECOGNIZED_STATUS_ATTRIBUTE' in signals or (terminal and nonterminal):
        return 'conflicting_or_unknown', sorted(signals)[:12]
    if terminal:
        return 'terminal', sorted(signals)[:12]
    if 'LIVE' in nonterminal or 'IN PLAY' in nonterminal:
        return 'live', sorted(signals)[:12]
    if 'POSTPONED' in nonterminal:
        return 'postponed', sorted(signals)[:12]
    if 'SCHEDULED' in nonterminal:
        return 'scheduled', sorted(signals)[:12]
    return 'unmarked', sorted(signals)[:12]


def _finished_score_link_row(tr: Tag, *, rejection_reasons: list[str] | None = None) -> bool:
    """Recognize the retained terminal row shape without interpreting outcome.

    The participant anchor contains two explicit spans; a separate same-event
    anchor carries the match score. The optional third cell must be empty or a
    bounded, parenthesized sequence of one to three numeric set scores. These
    are exclusion cues only—not schedule or settlement evidence.
    """
    from racketfactory.betexplorer_evidence import _url

    def reject(code: str) -> bool:
        if rejection_reasons is not None and code not in rejection_reasons:
            rejection_reasons.append(code)
        return False

    cells = tr.find_all(['td', 'th'], recursive=False)
    if len(cells) != 3 or any(cell.name != 'td' for cell in cells):
        return reject('terminal_cell_count_or_type')
    if 'table-main__tt' not in (cells[0].get('class') or []):
        return reject('terminal_participant_cell_class')

    def safe(node: Tag) -> bool:
        return not any(key in node.attrs for key in ('hidden', 'style', 'aria-hidden'))

    if (not all(safe(node) for node in [tr, *tr.find_all(True)])
            or any(isinstance(node, Comment) for node in tr.descendants)):
        return reject('terminal_hidden_markup')
    status, signals = _listing_status_state(tr)
    if status != 'terminal' or not (set(signals) & _TERMINAL_STATUS):
        return reject('terminal_status_not_unambiguous')

    def children_without_whitespace(node: Tag):
        return [child for child in node.children
                if not isinstance(child, NavigableString) or isinstance(child, Comment) or str(child).strip()]

    if children_without_whitespace(tr) != cells:
        return reject('terminal_row_extra_children')
    participant_cell = children_without_whitespace(cells[0])
    score_cell = children_without_whitespace(cells[1])
    if (len(participant_cell) != 2 or not all(isinstance(node, Tag) for node in participant_cell)
            or participant_cell[0].name != 'span' or participant_cell[0].find(True)
            or str(participant_cell[0].get_text(' ', strip=True)) not in {
                'FIN', 'FINISHED', 'RET', 'RET.', 'RETIRED', 'W/O', 'W.O.',
                'WALKOVER', 'AWARDED', 'CANCELLED', 'CANCELED'}
            or participant_cell[1].name != 'a'
            or len(score_cell) != 1 or not isinstance(score_cell[0], Tag) or score_cell[0].name != 'a'):
        return reject('terminal_visible_structure_mismatch')

    participant_anchor, score_anchor = participant_cell[1], score_cell[0]
    participant_reasons: list[str] = []
    if _structured_participants([participant_anchor], rejection_reasons=participant_reasons) is None:
        if rejection_reasons is not None:
            rejection_reasons.extend(x for x in participant_reasons if x not in rejection_reasons)
        return reject('terminal_participant_structure_unverified')

    first = _url(str(participant_anchor.get('href') or ''), '')
    second = _url(str(score_anchor.get('href') or ''), '')
    match_links = [a for a in tr.find_all('a', href=True)
                   if _is_match_link(str(a.get('href') or ''))]
    if (not first or first != second or '?' in first or not _is_match_link(first)
            or not re.fullmatch(r'[A-Za-z0-9]{1,32}', first.rstrip('/').split('/')[-1])
            or len(match_links) != 2 or match_links != [participant_anchor, score_anchor]):
        return reject('terminal_event_links_not_same')

    if any(node.name not in {'strong', 'b', 'span', 'em', 'i'}
           for node in score_anchor.find_all(True)):
        return reject('terminal_score_markup_unsupported')
    if score_anchor.find(string=lambda value: isinstance(value, Comment)):
        return reject('terminal_score_comment')
    score_label = score_anchor.get_text(' ', strip=True)
    complete_score = re.fullmatch(r'(?:0:2|2:0|1:2|2:1)', score_label)
    interrupted_score = re.fullmatch(r'(?:0:0|1:0|0:1)\s+(?:RET\.?|INT\.?)',
                                     score_label, re.IGNORECASE)
    walkover_score = re.fullmatch(r'WO\.', score_label, re.IGNORECASE)
    if not (complete_score or interrupted_score or walkover_score):
        return reject('terminal_score_format')

    # BetExplorer's retained third-cell shape is either whitespace-only or
    # literal text such as "(7:6, 2:6, 7:6)". Do not accept arbitrary content,
    # nested markup, commentary, or unbounded score strings as a set-score cell.
    set_nodes = children_without_whitespace(cells[2])
    if set_nodes:
        if (len(set_nodes) != 1 or not isinstance(set_nodes[0], NavigableString)
                or isinstance(set_nodes[0], Comment)):
            return reject('terminal_score_format')
        set_text = str(set_nodes[0]).strip()
        set_match = re.fullmatch(
            r'\(\s*\d{1,2}\s*:\s*\d{1,2}'
            r'(?:\s*,\s*\d{1,2}\s*:\s*\d{1,2}){0,2}\s*\)', set_text)
        if not set_match:
            return reject('terminal_score_format')
        set_values = [int(value) for value in re.findall(r'\d{1,2}', set_text)]
        if len(set_values) not in {2, 4, 6} or any(value > 14 for value in set_values):
            return reject('terminal_score_format')
    return True


def _listing_dt_components(raw: object) -> tuple[list[int] | None, str]:
    """Check observed D,M,YYYY,H,MM shape only; never infer timezone/date.

    Historic provider material and a third-party client support this component
    order, but do not establish the live response's timezone or date-selection
    and rescheduling semantics. Even syntactically valid values remain unverified.
    """
    text = str(raw or '')
    if not re.fullmatch(r'\d{1,2},\d{1,2},\d{4},\d{1,2},\d{1,2}', text):
        return None, 'invalid'
    day, month, year, hour, minute = (int(part) for part in text.split(','))
    try:
        datetime(year, month, day, hour, minute)
    except ValueError:
        return None, 'invalid'
    return [day, month, year, hour, minute], 'valid_unverified'


def parse_results_page(html: str, page_date: str, *, metrics: dict | None = None, include_unpriced: bool = False) -> list[dict[str, Any]]:
    """Edge-style primary parse: js-tournament headers + data-dt rows."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    tournament = tour_hint = ""
    n_dt = n_finished = n_no_odds = n_bad = n_schedule_unverified = 0
    n_live = n_postponed = n_duplicate = n_recognized = n_conflicting_status = 0
    for tr in soup.find_all("tr"):
        classes = tr.get("class") or []
        if "js-tournament" in classes:
            text = tr.get_text(" ", strip=True)
            label = text.split(":")[-1].strip() if ":" in text else text.strip()
            tournament = label or tournament
            tour_hint = ""
            continue
        dt_raw = tr.get("data-dt")
        if not dt_raw:
            continue
        n_dt += 1
        # The listing date is request context, not evidence of the fixture's
        # betting-day date. The provider's data-dt ordering/timezone and status
        # semantics are not established; even a matching component is not proof.
        n_schedule_unverified += 1
        status_state, status_signals = _listing_status_state(tr)
        parser_reasons: list[str] = []
        if status_state == 'conflicting_or_unknown':
            n_bad += 1
            n_conflicting_status += 1
            parser_reasons.append('contradictory_or_unknown_status_signal')
            try:
                from racketfactory.rebuild_diagnostics import capture_unrecognized_listing_row
                capture_unrecognized_listing_row(tr, page_date, rejection_reasons=parser_reasons)
            except (OSError, ValueError, TypeError):
                logger.warning("Could not retain unrecognized BetExplorer row evidence")
            continue
        # Check the retained FIN + participant-link + same-event score-link
        # shape before treating the score anchor as a second participant. The
        # helper validates its optional set-score cell and records exclusion
        # only; terminal rows never enter the actionable quote path.
        if status_state == 'terminal':
            terminal_reasons: list[str] = []
            if _finished_score_link_row(tr, rejection_reasons=terminal_reasons):
                n_finished += 1
                continue

        # Collect all match-link anchors in this row
        candidates: list[tuple] = []
        match_anchors: list[Tag] = []
        for a in tr.find_all("a", href=True):
            href = str(a.get("href") or "")
            ctx = _is_match_link(href)
            if ctx is None:
                continue
            match_anchors.append(a)
            txt = a.get_text(" ", strip=True)
            if not txt or len(txt) < 2:
                parent_td = a.find_parent("td")
                if parent_td is not None:
                    txt = parent_td.get_text(" ", strip=True)
            if txt:
                candidates.append((a, txt, ctx, href))

        chosen = None
        chosen_home = chosen_away = ""
        chosen_ctx = None
        chosen_href = ""

        # When participant-like spans are present, never flatten an
        # ambiguous structure and hand it to the old fused-name heuristic.
        structured = [(a, txt, ctx, href) for a, txt, ctx, href in candidates
                      if a.find('span')]
        structure_ambiguous = False
        if structured:
            if len(candidates) != len(match_anchors) or len(match_anchors) not in (1, 2):
                structure_ambiguous = True
            else:
                pair = _structured_participants(match_anchors, rejection_reasons=parser_reasons)
                if pair is None:
                    structure_ambiguous = True
                else:
                    a, txt, ctx, href = candidates[0]
                    chosen, (chosen_home, chosen_away), chosen_ctx, chosen_href = a, pair, ctx, href

        # A plain-text match link needs one explicit separator. The shared
        # fused-name heuristic also guesses whitespace-only full-name splits;
        # those are not participant boundaries. Never split the whole row.
        if not structured and len(match_anchors) == len(candidates) == 1:
            from racketfactory.betexplorer_evidence import _url
            a, txt, ctx, href = candidates[0]
            if (_url(href, '') and len(po._NAME_SEP_RE.findall(txt)) == 1 and not a.find(True)
                    and not any(key in a.attrs for key in ('hidden', 'style', 'aria-hidden'))):
                h, aw = po.split_match_names(txt)
                from racketfactory.settlement import players_match
                members = [part.strip() for team in (h, aw) for part in team.split('/')]
                if (h and aw and not any(re.search(r'\d', v) or po._FINISHED_RE.search(v) for v in (h, aw))
                        and len(h.split('/')) == len(aw.split('/'))
                        and not any(players_match(x, y)[0] for i, x in enumerate(members) for y in members[i + 1:])):
                    chosen, chosen_home, chosen_away, chosen_ctx, chosen_href = a, h, aw, ctx, href

        # 2) Fallback: two separate anchors, each a single player / doubles team
        # Handles <a>Borisiouk M.</a> vs <a>Kim D. J.</a> or <a>Collins K.</a> vs <a>Yashina E.</a>
        # 2026-09-17: extended to full names without initials (e.g. Alcaraz Carlos) and loose check
        if chosen is None and not structure_ambiguous and not structured and len(match_anchors) == len(candidates) == 2:
            from racketfactory.betexplorer_evidence import _url
            a, b = match_anchors
            parent = a.parent
            public = [_url(str(link.get('href') or ''), '') for link in match_anchors]
            safe_pair = (all(public) and public[0] == public[1] and parent is b.parent
                         and isinstance(parent, Tag) and parent.name in {'td', 'div'}
                         and not any(key in parent.attrs for key in ('hidden', 'style', 'aria-hidden'))
                         and not any(any(key in link.attrs for key in ('hidden', 'style', 'aria-hidden'))
                                     or link.find(True) for link in match_anchors))
            if safe_pair:
                for child in parent.children:
                    if isinstance(child, Comment):
                        safe_pair = False
                    elif isinstance(child, NavigableString):
                        if str(child).strip().casefold() not in {'', '-', '–', '—', 'vs', 'vs.'}:
                            safe_pair = False
                    elif not isinstance(child, Tag) or not any(child is link for link in match_anchors):
                        safe_pair = False
            def _looks_like_player_or_team(t: str) -> bool:
                tt = t.strip()
                if not tt or len(tt) < 2 or re.search(r'\d', tt) or po._FINISHED_RE.search(tt):
                    return False
                if any(not (c.isalpha() or c.isspace() or c in ".'’-/") for c in tt):
                    return False
                if tt.lower() in {"winner", "odds", "result", "finished", "live"}:
                    return False
                try:
                    if po._looks_like_player(tt):
                        return True
                except Exception:
                    pass
                try:
                    if po._looks_like_player_loose(tt):
                        return True
                except Exception:
                    pass
                if "/" in tt:
                    try:
                        if po._looks_like_doubles_team(tt):
                            return True
                    except Exception:
                        pass
                    # Fallback: slash with dot or space
                    if "." in tt or " " in tt:
                        return len(tt) >= 5
                # Single player heuristic: allow full names (two caps words) or initial
                if len(tt) >= 3 and tt[0].isupper():
                    if "." in tt or " " in tt:
                        return True
                    # Single capitalized surname with len>=3 (rare but possible)
                    if len(tt) >= 3 and tt.isalpha():
                        return True
                return False

            player_cands = [(a, txt, ctx, href) for a, txt, ctx, href in candidates if _looks_like_player_or_team(txt)]
            # Deduplicate by text to avoid same anchor twice
            seen_txt = set()
            uniq_cands = []
            for cand in player_cands:
                txt = cand[1].strip()
                if txt.lower() not in seen_txt:
                    seen_txt.add(txt.lower())
                    uniq_cands.append(cand)
            player_cands = uniq_cands
            if safe_pair and len(player_cands) == 2:
                a1, txt1, ctx1, href1 = player_cands[0]
                a2, txt2, ctx2, href2 = player_cands[1]
                from racketfactory.settlement import players_match
                members = [part.strip() for team in (txt1, txt2) for part in team.split('/')]
                distinct = not any(players_match(x, y)[0] for i, x in enumerate(members) for y in members[i + 1:])
                if distinct and txt1 != txt2 and len(txt1) >= 2 and len(txt2) >= 2 and len(txt1.split('/')) == len(txt2.split('/')):
                    # Clean trailing odds if present
                    txt1_clean = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", txt1).strip()
                    txt2_clean = re.sub(r"\s+\d{1,2}\.\d{1,2}(?:\s+\d{1,2}\.\d{1,2})?\s*$", "", txt2).strip()
                    chosen = a1
                    chosen_home = txt1_clean or txt1
                    chosen_away = txt2_clean or txt2
                    chosen_ctx = ctx1 or ctx2
                    chosen_href = href1 or href2

        if chosen is None:
            # Retained tennis evidence shows a FIN marker next to a participant
            # link plus a separate same-event score link. Classify only this
            # narrow shape, without treating the score link as a second player.
            terminal_reasons: list[str] = []
            if _finished_score_link_row(tr, rejection_reasons=terminal_reasons):
                n_finished += 1
                continue
            n_bad += 1
            parser_reasons.extend(x for x in terminal_reasons if x not in parser_reasons)
            # Capture bounded reason codes and the smallest allowlisted DOM
            # facts needed to distinguish unsupported markup from omissions.
            try:
                from racketfactory.rebuild_diagnostics import capture_unrecognized_listing_row
                capture_unrecognized_listing_row(tr, page_date, rejection_reasons=parser_reasons)
            except (OSError, ValueError, TypeError):
                logger.warning("Could not retain unrecognized BetExplorer row evidence")
            continue
        row_text = tr.get_text(" ", strip=True)
        if status_state == 'terminal':
            if (any(key in node.attrs for node in [tr, *tr.find_all(True)]
                    for key in ('hidden', 'style', 'aria-hidden'))
                    or any(isinstance(node, Comment) for node in tr.descendants)):
                n_bad += 1
                n_conflicting_status += 1
                parser_reasons.append('contradictory_or_unknown_status_signal')
                try:
                    from racketfactory.rebuild_diagnostics import capture_unrecognized_listing_row
                    capture_unrecognized_listing_row(tr, page_date, rejection_reasons=parser_reasons)
                except (OSError, ValueError, TypeError):
                    logger.warning("Could not retain unrecognized BetExplorer row evidence")
                continue
            n_finished += 1
            continue
        if status_state == 'live':
            n_live += 1
            continue
        if status_state == 'postponed':
            n_postponed += 1
            continue
        key = chosen_href
        if key in seen:
            n_duplicate += 1
            continue
        seen.add(key)
        n_recognized += 1
        # Primary: shared extractor that checks visible text + data-odd attrs + odds cells
        decimals = po._extract_odds_from_tr(tr, chosen.get_text(" ", strip=True))
        if len(decimals) < 2:
            # Secondary: legacy row-decimal scan for backward compat
            decimals = po._row_decimals(row_text.replace(chosen.get_text(" ", strip=True), " "))
        if len(decimals) < 2:
            # Tertiary: conservative nearby search (parent tr only)
            try:
                decimals = po._find_odds_near(chosen, chosen.get_text(" ", strip=True), _is_match_link)
            except Exception:
                decimals = []
        if len(decimals) < 2:
            n_no_odds += 1
            if not include_unpriced:
                continue
            decimals = [None, None]
        # Components are a format check, not a schedule, timezone or status.
        components, component_status = _listing_dt_components(dt_raw)
        rows.append({
            "match_date": "",
            "match_time": "",
            "listing_date": page_date,
            "listing_data_dt_components": components,
            "listing_component_status": component_status,
            "listing_schedule_status": "unverified",
            "player_home": chosen_home,
            "player_away": chosen_away,
            "odds_home": decimals[0],
            "odds_away": decimals[1],
            "tournament": (chosen_ctx.get("tournament") if chosen_ctx else "") or tournament,
            "tour_hint": (chosen_ctx.get("tour_hint") if chosen_ctx else "") or tour_hint,
            "match_url": chosen_href,
        })
    if metrics is not None:
        accounting_total = n_finished + n_live + n_postponed + n_recognized + n_bad + n_duplicate
        metrics.update({"data_dt_rows": n_dt, "parsed_priced_rows": sum(r.get("odds_home") is not None for r in rows),
                        "discovered_rows": len(rows), "finished_rows": n_finished,
                        "live_excluded_rows": n_live, "postponed_excluded_rows": n_postponed,
                        "duplicate_rows": n_duplicate, "recognized_actionable_rows": n_recognized,
                        "no_odds_rows": n_no_odds, "unrecognized_rows": n_bad,
                        "conflicting_status_rows": n_conflicting_status,
                        "row_accounting_total": accounting_total,
                        "row_accounting_complete": accounting_total == n_dt,
                        "listing_recognition_status": "complete" if accounting_total == n_dt and n_bad == 0 else "partial",
                        # We account for rows in this response only. Page/pagination
                        # completeness and target-date semantics are not established.
                        "listing_coverage_status": "not_established",
                        "schedule_verification_status": "unverified" if n_recognized else "not_required_for_excluded_rows",
                        "actionable_schedule_unverified_rows": n_recognized,
                        "unverified_schedule_rows": n_schedule_unverified})
    logger.info("%s results parse %s: data-dt rows=%d parsed=%d finished=%d no_odds=%d bad=%d",
                SOURCE_NAME, page_date, n_dt, len(rows), n_finished, n_no_odds, n_bad)
    return rows


def parse_tennis_page(html: str, page_date: str, *, require_odds: bool = True) -> list[dict[str, Any]]:
    """Day-page parse (generic scanner; fallback when results shape misses)."""
    rows = parse_listing_page(
        html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
        page_date=page_date, require_odds=require_odds,
    )
    for row in rows:
        row.update(listing_date=page_date, listing_schedule_status="unverified",
                   match_date="", match_time="", _collection_partial=True)
    return _finalize(rows)


def _finalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["bookmaker"] = BOOK_LABEL
        row["source"] = SOURCE_NAME
        url = str(row.get("match_url") or "")
        row["event_id"] = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return rows


def _results_url(target: str) -> str:
    y, m, d = target.split("-")
    return RESULTS_URL.format(y=y, m=m, d=d)


MAX_MATCH_PAGES = 60
# A detail plus comparison consumes two requests within the old 60-page budget.
MAX_COMPARISON_FIXTURES = MAX_MATCH_PAGES // 2


def _strip_fragment(href: str) -> str:
    return href.split("#", 1)[0]


def parse_match_page_odds(html: str) -> tuple[float | None, float | None]:
    """Extract consensus Home/Away from a BetExplorer match detail page.

    Strategy (tolerant, no JS):
    - Collect (1,2) pairs per table row where the row contains exactly 2
      plausible decimals (either via data-odd attributes or visible text) and
      at least one cell with an odds-like class.
    - Also scan generic data-odd / data-opening-odd attributes as fallback.
    - Average per side across pairs (consensus). Returns (None, None) when
      no complete pair is found or page is finished/retired.
    """
    if not html:
        return None, None
    if po._FINISHED_RE.search(html[:5000]):
        # Quick check: many finished pages contain FIN/RET markers in header
        pass
    soup = BeautifulSoup(html, "html.parser")
    pairs: list[tuple[float, float]] = []

    # Primary: rows with odds
    for tr in soup.find_all("tr"):
        # Skip header rows
        vals: list[float] = []
        # Prefer data-odd attributes inside row
        for el in tr.find_all(attrs={"data-odd": True}):
            try:
                v = float(str(el.get("data-odd") or "").strip())
                if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                    vals.append(v)
            except Exception:
                continue
        if len(vals) < 2:
            for el in tr.find_all(attrs={"data-opening-odd": True}):
                try:
                    v = float(str(el.get("data-opening-odd") or "").strip())
                    if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                        vals.append(v)
                except Exception:
                    continue
        if len(vals) < 2:
            # Fallback to visible odds cells
            has_odds_cell = False
            for td in tr.find_all("td"):
                cls = " ".join(td.get("class") or [])
                if "odd" in cls.lower():
                    has_odds_cell = True
                    break
            if has_odds_cell or tr.find(attrs={"data-odd": True}) is not None:
                txt = tr.get_text(" ", strip=True)
                vals = po._row_decimals(txt)
        if len(vals) == 2:
            pairs.append((vals[0], vals[1]))

    # Secondary: if no row pairs, collect all data-odd on page and try to pair
    if not pairs:
        all_odds: list[float] = []
        for attr in ("data-odd", "data-opening-odd", "data-closing-odd"):
            for el in soup.find_all(attrs={attr: True}):
                try:
                    v = float(str(el.get(attr) or "").strip())
                    if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                        all_odds.append(v)
                except Exception:
                    continue
        # Also scan td.table-main__odds
        for td in soup.find_all("td", class_=lambda c: c and "odd" in str(c).lower()):
            try:
                v = float(td.get_text(strip=True))
                if po.MIN_DECIMAL_ODDS <= v <= po.MAX_DECIMAL_ODDS:
                    all_odds.append(v)
            except Exception:
                continue
        # If we have at least 2, assume first two are representative average
        # (BetExplorer often renders average row first)
        if len(all_odds) >= 2:
            # If even number, average first half as home, second half as away is wrong.
            # Instead, take first two as pair (likely average row)
            pairs.append((all_odds[0], all_odds[1]))

    if not pairs:
        return None, None
    # Average across bookmakers for consensus (matches previous behavior)
    avg_home = sum(p[0] for p in pairs) / len(pairs)
    avg_away = sum(p[1] for p in pairs) / len(pairs)
    return avg_home, avg_away


def _fetch_live(target: str, *, with_status: bool = False, metrics: dict | None = None,
                response_observer=None):
    def done(rows, status):
        return (rows, status) if with_status else rows

    # 1) Try dated results page with odds (historical shape)
    try:
        listing_url = _results_url(target)
        observer_kwargs = ({"response_observer": lambda response: response_observer("listing", response)}
                           if response_observer is not None else {})
        html = fetch_page_html(listing_url, SOURCE_NAME, prefer_plain=True, **observer_kwargs)
        observed_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        if isinstance(exc, PageFetchError):
            raise
        logger.warning("%s dated fetch failed for %s: %s", SOURCE_NAME, target, exc)
        html = ""
    if html:
        from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
        capture_betexplorer_fragment(html, "listing")
    parse_metrics: dict[str, int] = {}
    rows = parse_results_page(html, target, metrics=parse_metrics, include_unpriced=with_status) if html else []
    if metrics is not None:
        metrics.update(parse_metrics)
    if rows:
        if parse_metrics.get("actionable_schedule_unverified_rows") and not with_status:
            raise PageFetchError("parser", "BetExplorer listing schedule is unverified")
        for row in rows:
            row["observed_at"] = observed_at
        logger.info("%s: dated results shape hit for %s (%d rows)", SOURCE_NAME, target, len(rows))
        partial = bool(parse_metrics.get("unrecognized_rows") or parse_metrics.get("actionable_schedule_unverified_rows")
                       or (not with_status and parse_metrics.get("no_odds_rows")))
        for row in rows:
            row["_collection_partial"] = partial
        return done(_finalize(rows), "partial" if partial else "complete")

    # An unrecognized dated row remains unresolved even when no other rows
    # parsed. Neither a generic fallback nor an empty-board marker can prove
    # that this row is non-actionable or make the dated listing complete.
    if parse_metrics.get("unrecognized_rows") or parse_metrics.get("actionable_schedule_unverified_rows"):
        if with_status:
            return done([], "partial")
        if parse_metrics.get("unrecognized_rows"):
            raise PageFetchError("parser", "BetExplorer dated listing contains unrecognized rows")
        raise PageFetchError("parser", "BetExplorer listing schedule is unverified")

    # Row accounting is not target-date listing coverage. Even when every
    # observed row is a narrow terminal exclusion, the response's completeness
    # and date-selection semantics remain unestablished.
    if (parse_metrics.get("data_dt_rows", 0) > 0
            and parse_metrics.get("row_accounting_complete") is True
            and parse_metrics.get("listing_recognition_status") == "complete"
            and parse_metrics.get("actionable_schedule_unverified_rows", 0) == 0):
        if with_status:
            return done([], "partial")
        raise PageFetchError("parser", "BetExplorer row accounting does not establish requested-day listing coverage")

    # 2) For any date, if results page missed, try to collect links without odds
    #    and price via match pages (new fallback for JS-rendered listings)
    link_rows: list[dict[str, Any]] = []
    if html:
        # Re-parse results page without requiring odds to get candidate links
        soup = BeautifulSoup(html, "html.parser")
        # Use generic scanner with require_odds=False on results html
        link_rows = parse_listing_page(
            html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
            page_date=target, require_odds=False,
        )

    from racketfactory.kickoff import SAST
    if target == datetime.now(SAST).date().isoformat():
        # Also try day page for today
        if not link_rows:
            logger.info("%s: dated shape missed for %s; trying day page", SOURCE_NAME, target)
            day_html = fetch_page_html(TENNIS_URL, SOURCE_NAME, prefer_plain=True, **observer_kwargs)
            if day_html:
                link_rows = parse_listing_page(
                    day_html, source_label=SOURCE_NAME, is_match_link=_is_match_link,
                    page_date=target, require_odds=False,
                )
                if not link_rows:
                    html = day_html  # preserve verified empty-board evidence


    if not link_rows:
        if not html:
            raise PageFetchError("transport", "BetExplorer listings unavailable")
        if not any(marker in html.lower() for marker in ("no matches", "no events")):
            raise PageFetchError("parser", "BetExplorer listing has no recognised fixtures or empty-board marker")
        if metrics is not None:
            metrics.update({"empty_board_marker_observed": True,
                            "empty_board_marker_contract": "unverified",
                            "listing_coverage_status": "not_established",
                            "listing_recognition_status": "unknown",
                            "row_accounting_complete": False})
        if with_status:
            return done([], "partial")
        raise PageFetchError("parser", "BetExplorer empty-board marker is not authenticated for requested-day coverage")

    # The generic fallback has no validated provider date/time evidence either.
    # Preserve discovery as partial without assigning the requested day; never
    # crawl detail pages as executable prices from an unverified schedule.
    generic_schedule_status = "unverified"
    if generic_schedule_status != "verified":
        if with_status:
            for row in link_rows:
                row["observed_at"] = observed_at
                row["listing_date"] = target
                row["listing_schedule_status"] = generic_schedule_status
                row["match_date"] = ""
                row["match_time"] = ""
                row["_collection_partial"] = True
            return done(_finalize(link_rows), "partial")
        raise PageFetchError("parser", "BetExplorer listing schedule is unverified")

    # 3) Price each link via its match page (bounded)
    priced: list[dict[str, Any]] = []
    n_live = n_failed = 0
    for link in link_rows[:MAX_MATCH_PAGES]:
        href = str(link.get("match_url") or "")
        if not href:
            continue
        full_url = href if href.startswith("http") else (BASE_URL + href if href.startswith("/") else href)
        mhtml = fetch_page_html(full_url, SOURCE_NAME, prefer_plain=True)
        observed_at = datetime.now(timezone.utc).isoformat()
        if mhtml:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
            capture_betexplorer_fragment(mhtml, "detail")
        if not mhtml:
            n_failed += 1
            continue
        # Skip finished pages
        if po._FINISHED_RE.search(mhtml[:8000]):
            n_live += 1
            continue
        best = parse_match_page_odds(mhtml)
        if best[0] is None or best[1] is None:
            n_failed += 1
            continue
        link["odds_home"] = best[0]
        link["odds_away"] = best[1]
        link["observed_at"] = observed_at
        priced.append(link)

    logger.info("%s match pages %s: candidates=%d priced=%d live_skipped=%d failed=%d",
                SOURCE_NAME, target, min(len(link_rows), MAX_MATCH_PAGES), len(priced), n_live, n_failed)
    if priced:
        partial = bool(n_failed or len(link_rows) > MAX_MATCH_PAGES)
        for row in priced:
            row["_collection_partial"] = partial
        return done(_finalize(priced), "partial" if partial else "complete")

    if n_failed:
        raise PageFetchError("parser", "BetExplorer fixture pages contained no usable paired prices")
    return done([], "unknown")


def fetch_betexplorer_rows(target_date: str) -> list[dict[str, Any]]:
    """Priced upcoming rows for target_date (any date), TheOddsAPI-shaped."""
    if os.getenv(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return []
    target = str(target_date)[:10]
    try:
        parts = target.split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return []
    except Exception:
        return []
    fetched_live = False
    def fetch_live_rows():
        nonlocal fetched_live
        fetched_live = True
        return _fetch_live(target)

    rows = cached_fetch(f"betexplorer_v6_{target}", fetch_live_rows)
    # Old cached entries cannot establish independently verified schedules.
    # Quarantine without deleting, refreshing, or making additional requests.
    if rows and not fetched_live:
        raise PageFetchError("parser", "BetExplorer cached schedule evidence is unverified")
    return [dict(row) for row in rows]


def _comparison_endpoint_candidates(html: str, event_id: str, *, metrics: dict | None = None) -> list[dict]:
    """Discover *literal* tennis comparison URLs and explicit tab metadata.

    No football endpoint or missing market/scope is inferred. Only same-host,
    HTTPS URLs for this event, with an innocuous language parameter, are used.
    """
    from urllib.parse import urljoin, urlparse, parse_qsl
    soup = BeautifulSoup(html, "html.parser")
    found = []
    contexts = {}
    def record(key):
        if metrics is not None:
            metrics[key] = metrics.get(key, 0) + 1
    for node in soup.select('[data-url], [data-endpoint], [data-ajax-url], a[href]'):
        record("endpoint_nodes_scanned")
        raw = next((str(node.get(k) or "") for k in ("data-url", "data-endpoint", "data-ajax-url", "href") if node.get(k)), "")
        url = urljoin(BASE_URL, raw)
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "www.betexplorer.com" or parsed.username or parsed.password
                or parsed.fragment or not parsed.path.startswith("/match-odds/")
                or event_id not in parsed.path.split("/")
                or any(k != "lang" or v != "en" for k, v in parse_qsl(parsed.query, keep_blank_values=True))):
            record("endpoint_url_rejected")
            continue
        market = str(node.get("data-market") or node.get("data-market-name") or "").strip().lower()
        period = str(node.get("data-period") or node.get("data-scope") or "").strip().lower()
        market_id = str(node.get("data-market-id") or "")
        # Compare explicit contexts for the *same validated endpoint* before
        # rejecting unsupported markets/periods. Plain links with no context
        # are not assertions about scope. Only the aliases already accepted by
        # this parser are normalized; unsupported labels remain distinct.
        market_key = "match winner" if market in {"match winner", "match_winner", "match-winner", "h2h"} else market
        period_key = "match" if period in {"match", "full match", "full_match", "full-match"} else period
        context = contexts.setdefault(url, {"market": set(), "period": set(), "market_id": set()})
        for key, value in (("market", market_key), ("period", period_key), ("market_id", market_id)):
            if value:
                context[key].add(value)
        if market not in {"match winner", "match_winner", "match-winner", "h2h"}:
            record("endpoint_market_rejected")
            continue
        if period not in {"match", "full match", "full_match", "full-match"}:
            record("endpoint_period_rejected")
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{0,32}", market_id):
            record("endpoint_market_id_rejected")
            continue
        found.append({"url": url, "raw_market": market, "raw_period": period, "market_id": market_id})
    # A page with competing explicit contexts is ambiguous, not a first hit.
    if any(any(len(values) > 1 for values in contexts[item["url"]].values()) for item in found):
        record("endpoint_conflicting_context")
        return []
    unique = {}
    for item in found:
        previous = unique.get(item["url"])
        if previous is None:
            unique[item["url"]] = item
    if len(unique) > 1:
        record("endpoint_ambiguous_urls")
    return list(unique.values()) if len(unique) == 1 else []


def _comparison_response_rows(payload: str, fixture: dict, context: dict, observed_at: str) -> tuple[list[dict], dict]:
    """Parse two explicitly labelled tennis columns and individual book rows.

    The format covered here is synthetic until actual tennis fragments validate
    the provider contract. A comparison average is never a bookmaker offer.
    """
    import hashlib
    import json
    import math
    from racketfactory.warehouse import names_match, strict_quote_team_match
    if (context.get("raw_market") not in {"match winner", "match_winner", "match-winner", "h2h"}
            or context.get("raw_period") not in {"match", "full match", "full_match", "full-match"}):
        raise PageFetchError("parser", "comparison market or scope not established")
    metrics = {"bookmaker_rows": 0, "offers": 0, "average_rows": 0,
               "missing_bookmaker": 0, "invalid_price": 0, "duplicate_rows": 0,
               "ambiguous_columns": 0, "ambiguous_bookmaker_rows": 0, "average_prices": []}
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        raise PageFetchError("parser", "comparison response is not JSON")
    html = data.get("odds") if isinstance(data, dict) else None
    if not isinstance(html, str):
        raise PageFetchError("parser", "comparison response has no odds HTML")
    soup = BeautifulSoup(html, "html.parser")
    home, away = str(fixture.get("player_home") or ""), str(fixture.get("player_away") or "")
    if not home or not away or names_match(home, away):
        raise PageFetchError("parser", "ambiguous fixture participants")
    digest = hashlib.sha256(payload.encode()).hexdigest()
    offers, seen, conflicts = [], {}, set()
    def unique_book(book_id, book, prices):
        key = book_id or book.casefold()
        value = (book.casefold(), tuple(prices))
        if key in seen:
            if seen[key] == value:
                metrics["duplicate_rows"] += 1
            else:
                conflicts.add(key)
                metrics["ambiguous_bookmaker_rows"] += 1
            return False
        seen[key] = value
        return True
    for table in soup.find_all("table"):
        thead = table.find("thead")
        headers = thead.find_all("th") if thead else []
        labels = [x.get_text(" ", strip=True) for x in headers]
        hidx = [i for i, text in enumerate(labels) if strict_quote_team_match(home, text)]
        aidx = [i for i, text in enumerate(labels) if strict_quote_team_match(away, text)]
        extra_outcome = any(i not in hidx + aidx and (n.get("data-pos") or labels[i].strip().lower() in {"1", "2", "x", "draw", "tie"})
                            for i, n in enumerate(headers))
        if len(hidx) != 1 or len(aidx) != 1 or hidx[0] == aidx[0] or extra_outcome:
            metrics["ambiguous_columns"] += 1
            continue
        for row in table.select("tbody tr"):
            classes = " ".join(row.get("class") or []).lower()
            if "average" in classes or (row.find("td") and row.find("td").get_text(" ", strip=True).lower() in {"average", "avg"}):
                metrics["average_rows"] += 1
                avg_cells = row.find_all("td", recursive=False)
                if max(hidx[0], aidx[0]) < len(avg_cells):
                    try:
                        avg_nodes = [avg_cells[i].find(attrs={"data-odd": True}) or avg_cells[i] for i in (hidx[0], aidx[0])]
                        avg = [float(n.get("data-odd") or n.get_text(" ", strip=True)) for n in avg_nodes]
                        if all(math.isfinite(v) and v > 1 for v in avg) and len(metrics["average_prices"]) < 2:
                            metrics["average_prices"].append(avg)
                    except (TypeError, ValueError, OverflowError):
                        pass
                continue
            cells = row.find_all("td", recursive=False)
            if max(hidx[0], aidx[0]) >= len(cells):
                continue
            if any((c.has_attr("data-odd") or c.find(attrs={"data-odd": True}))
                   for i, c in enumerate(cells) if i not in (hidx[0], aidx[0])):
                metrics["ambiguous_columns"] += 1
                continue
            metrics["bookmaker_rows"] += 1
            logo = row.select_one('img[title], a[title]')
            bookmaker = str(row.get("data-bookmaker-name") or (logo.get("title") if logo else "") or "").strip()
            book_id = str(row.get("data-bookie") or "").strip()
            if not bookmaker or bookmaker.lower() in {"betexplorer_best", "betexplorer best"}:
                metrics["missing_bookmaker"] += 1
                continue
            try:
                price_nodes = [cells[i].find(attrs={"data-odd": True}) or cells[i] for i in (hidx[0], aidx[0])]
                for i, node in zip((hidx[0], aidx[0]), price_nodes):
                    if headers[i].get("data-pos") and node.get("data-pos") and str(headers[i].get("data-pos")) != str(node.get("data-pos")):
                        raise ValueError("conflicting position")
                prices = [float(n.get("data-odd")) for n in price_nodes]
                if not all(math.isfinite(v) and v > 1 for v in prices):
                    raise ValueError("invalid decimal")
            except (TypeError, ValueError, OverflowError):
                metrics["invalid_price"] += 1
                continue
            if not unique_book(book_id, bookmaker, prices):
                continue
            offers.append({**{k: fixture.get(k) for k in ("event_id", "match_url", "match_date", "match_time", "commence_time", "player_home", "player_away", "tournament", "tour_hint", "schedule_evidence")},
                "odds_home": prices[0], "odds_away": prices[1], "bookmaker": bookmaker, "bookmaker_id": book_id, "odds_home_bookmaker": bookmaker, "odds_away_bookmaker": bookmaker,
                "source": SOURCE_NAME, "market": "match_winner", "period": "match", "line": None,
                "raw_market": context["raw_market"], "raw_period": context["raw_period"], "market_id": context.get("market_id"),
                "observed_at": observed_at, "retrieved_at": observed_at,
                "provider_price_updated_at": [n.get("data-created") for n in price_nodes],
                "provider_positions": [n.get("data-pos") for n in price_nodes],
                "response_sha256": digest, "price_type": "bookmaker", "settlement_rules": None})
    # Hidden all-bookmaker rows are accepted only when one table explicitly
    # binds participant headings to data-pos tokens used by the hidden cells.
    header_maps = []
    for table in soup.find_all("table"):
        head = table.find("thead")
        heads = head.find_all("th") if head else []
        hm = [str(n.get("data-pos") or "") for n in heads if strict_quote_team_match(home, n.get_text(" ", strip=True))]
        am = [str(n.get("data-pos") or "") for n in heads if strict_quote_team_match(away, n.get_text(" ", strip=True))]
        if len(hm) == len(am) == 1 and hm[0] and am[0] and hm[0] != am[0] and sum(bool(n.get("data-pos")) for n in heads) == 2:
            header_maps.append((hm[0], am[0]))
    if len(header_maps) == 1:
        hpos, apos = header_maps[0]
        for row in soup.find_all("div", class_=lambda c: c and "oddsComparisonAll__rowBookie" in str(c)):
            if "average" in " ".join(row.get("class") or []).lower():
                metrics["average_rows"] += 1
                continue
            metrics["bookmaker_rows"] += 1
            logo = row.select_one('img[title], a[title]')
            book = str(row.get("data-bookmaker-name") or (logo.get("title") if logo else "") or "").strip()
            book_id = str(row.get("data-bookie") or "").strip()
            if not book or book.lower() in {"betexplorer_best", "betexplorer best"}:
                metrics["missing_bookmaker"] += 1
                continue
            nodes = row.find_all(attrs={"data-odd": True})
            h = [n for n in nodes if str(n.get("data-pos") or "") == hpos]
            a = [n for n in nodes if str(n.get("data-pos") or "") == apos]
            try:
                if len(h) != 1 or len(a) != 1 or len(nodes) != 2: raise ValueError("ambiguous position")
                prices = [float(h[0].get("data-odd")), float(a[0].get("data-odd"))]
                if not all(math.isfinite(v) and v > 1 for v in prices): raise ValueError("invalid price")
            except (TypeError, ValueError, OverflowError):
                metrics["invalid_price"] += 1
                continue
            if not unique_book(book_id, book, prices):
                continue
            offers.append({**{k: fixture.get(k) for k in ("event_id", "match_url", "match_date", "match_time", "commence_time", "player_home", "player_away", "tournament", "tour_hint", "schedule_evidence")},
                "odds_home": prices[0], "odds_away": prices[1], "bookmaker": book, "bookmaker_id": book_id, "odds_home_bookmaker": book, "odds_away_bookmaker": book,
                "source": SOURCE_NAME, "market": "match_winner", "period": "match", "line": None,
                "raw_market": context["raw_market"], "raw_period": context["raw_period"], "market_id": context.get("market_id"),
                "observed_at": observed_at, "retrieved_at": observed_at,
                "provider_price_updated_at": [h[0].get("data-created"), a[0].get("data-created")],
                "provider_positions": [h[0].get("data-pos"), a[0].get("data-pos")],
                "response_sha256": digest, "price_type": "bookmaker", "settlement_rules": None})
    offers = [row for row in offers if (row.get("bookmaker_id") or row["bookmaker"].casefold()) not in conflicts]
    metrics["offers"] = len(offers)
    return offers, metrics


def _fetch_comparison_document(url: str, referer: str | None = None, *,
                               response_observer=None) -> tuple[str, str]:
    """Single throttled request, no alternate route on a restriction.

    The optional observer is only supplied by the bounded evidence-only CLI;
    ordinary collection retains its previous response handling and no body.
    """
    import requests
    from racketfactory.quota_guard import cooldown_status, record_cooldown
    host = urlsplit(url).netloc.lower()
    if cooldown_status("betexplorer", "host:" + host):
        raise PageFetchError("rate_limit", "BetExplorer cooldown active")
    po._throttle(host)
    request_started_at = datetime.now(timezone.utc).isoformat()
    headers = {"User-Agent": po.EDGE_SIMPLE_UA, "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    try:
        response = requests.get(url, timeout=30, headers=headers, allow_redirects=False)
    except requests.RequestException as exc:
        raise PageFetchError("transport", type(exc).__name__) from exc
    when = datetime.now(timezone.utc).isoformat()
    if response_observer is not None:
        try:
            response_observer({
                "requested_url": url,
                "final_url": str(getattr(response, "url", "") or ""),
                "request_started_at": request_started_at,
                "response_received_at": when,
                "http_status": response.status_code,
                "headers": dict(response.headers),
                "body_bytes": bytes(response.content or b""),
                "body_text": response.text or "",
                "transport": "requests",
            })
        except Exception:
            logger.warning("BetExplorer response evidence callback failed")
    if response.status_code == 429:
        from email.utils import parsedate_to_datetime
        raw = str(response.headers.get("Retry-After") or "")
        try:
            wait = float(raw)
        except ValueError:
            try:
                wait = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                wait = 900
        try:
            record_cooldown("betexplorer", "host:" + host, "rate_limit", max(1, wait))
        except OSError:
            logger.warning("BetExplorer cooldown could not be persisted")
    body = response.text or ""
    def refusal(kind: str, detail: str):
        import hashlib
        exc = PageFetchError(kind, detail)
        exc.http_status, exc.observed_at = response.status_code, when
        exc.response_sha256 = hashlib.sha256(body.encode()).hexdigest()
        raise exc
    if response.status_code in (401, 403, 429):
        refusal("challenge" if response.status_code in (401, 403) else "rate_limit", f"HTTP {response.status_code}")
    if response.status_code != 200:
        refusal("http", f"HTTP {response.status_code}")
    if any(x in body[:6000].lower() for x in ("cf-chl", "just a moment", "attention required", "verify you are human")):
        refusal("challenge", "verification page")
    return body, when


def _detail_scheduled_start(page: str, fixture: dict, *, observed_at: str,
                            rejection_reasons: list[str] | None = None) -> tuple[str, str, str] | None:
    """Extract a strictly matched offset-bearing SportsEvent candidate.

    The schema is observed only on a saved football page. Listing component
    and other page-status conflicts are unresolved, so this is diagnostic
    evidence, not a verified executable kickoff or complete listing.
    """
    import json
    from racketfactory.betexplorer_evidence import _url
    from racketfactory.kickoff import SAST
    from racketfactory.warehouse import strict_quote_team_match

    def reject(code: str):
        if rejection_reasons is not None and code not in rejection_reasons:
            rejection_reasons.append(code)
        return None

    try:
        observed = datetime.fromisoformat(str(observed_at).replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        if observed.tzinfo is None or not 0 <= (now - observed.astimezone(timezone.utc)).total_seconds() <= 1800:
            return reject('schedule_observation_invalid_or_stale')
        target = date.fromisoformat(str(fixture.get('listing_date') or ''))
    except (TypeError, ValueError, OverflowError):
        return reject('schedule_listing_date_invalid')
    path = _url(str(fixture.get('match_url') or ''), '')
    eid = str(fixture.get('event_id') or '')
    if (not path or not path.startswith('/tennis/') or '?' in path
            or path.rstrip('/').split('/')[-1] != eid or not _is_match_link(path)):
        return reject('schedule_event_identity_invalid')
    components = fixture.get('listing_data_dt_components')
    if isinstance(components, list) and len(components) == 5:
        try:
            listed_date = date(int(components[2]), int(components[1]), int(components[0]))
            if listed_date != target:
                return reject('schedule_listing_components_conflict_with_request_date')
        except (TypeError, ValueError, OverflowError):
            # Invalid listing components stay unresolved; they do not become
            # permission to infer a date from the detail document.
            return reject('schedule_listing_components_invalid')
    soup = BeautifulSoup(page[:2_000_000], 'html.parser')
    # The JSON-LD candidate must not coexist with a visible or attribute-bound
    # started/terminal/postponed signal. This is intentionally conservative:
    # unrelated status-bearing DOM may require authentic provider review.
    conflict_tokens = {'live', 'in play', 'finished', 'fin', 'ret', 'retired', 'walkover',
                       'w/o', 'cancelled', 'canceled', 'postponed', 'abandoned', 'started'}
    for node in soup.select('[data-status], [data-state], [data-live], [aria-label], [title], [class]')[:500]:
        values = [str(node.get(key) or '') for key in ('data-status', 'data-state', 'data-live', 'aria-label', 'title')]
        values.extend(str(value) for value in (node.get('class') or []))
        if node.name not in {'script', 'style'}:
            values.append(node.get_text(' ', strip=True))
        normalized = re.sub(r'[^a-z0-9/ ]+', ' ', ' '.join(values).casefold())
        words = set(normalized.split())
        if words & conflict_tokens:
            return reject('schedule_conflicting_status_signal')
        for key in ('data-status', 'data-state', 'data-live'):
            raw_status = str(node.get(key) or '').strip().casefold()
            if raw_status and not any(token in raw_status for token in {'scheduled', 'live', 'finished', 'fin', 'ret', 'walkover', 'cancel', 'postpon', 'abandon', 'started'}):
                return reject('schedule_unrecognized_status_signal')
    scripts = soup.find_all('script', attrs={'type': 'application/ld+json'}, limit=21)
    if len(scripts) > 20 or len(page) > 2_000_000:
        return reject('schedule_jsonld_size_or_count_limit')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    candidates = []
    for script in scripts:
        try:
            obj = json.loads(script.string or '', object_pairs_hook=unique_object)
        except (TypeError, ValueError):
            return reject('schedule_jsonld_invalid_or_duplicate_keys')
        if not isinstance(obj, dict) or obj.get('@type') != 'SportsEvent':
            continue
        candidates.append(obj)
    if len(candidates) != 1:
        return reject('schedule_sportsevent_count_unresolved')
    event = candidates[0]
    if (str(event.get('sport') or '').casefold() != 'tennis'
            or _url(str(event.get('url') or ''), '') != path
            or event.get('eventStatus') not in ('EventScheduled', 'https://schema.org/EventScheduled')):
        return reject('schedule_sport_url_or_status_mismatch')
    home = event.get('homeTeam')
    away = event.get('awayTeam')
    if not isinstance(home, dict) or not isinstance(away, dict):
        return reject('schedule_participant_fields_missing')
    fh, fa = str(fixture.get('player_home') or ''), str(fixture.get('player_away') or '')
    ph, pa = str(home.get('name') or ''), str(away.get('name') or '')
    direct = strict_quote_team_match(fh, ph) and strict_quote_team_match(fa, pa)
    reverse = strict_quote_team_match(fh, pa) and strict_quote_team_match(fa, ph)
    if not direct or reverse:
        return reject('schedule_participant_identity_or_orientation_mismatch')
    try:
        raw_start = str(event.get('startDate') or '')
        # RFC 3339 uses -00:00 for an unknown offset, not known UTC.
        if raw_start.endswith('-00:00'):
            return reject('schedule_start_offset_unknown')
        start = datetime.fromisoformat(raw_start.replace('Z', '+00:00'))
        if (start.tzinfo is None or start.utcoffset() is None
                or abs(start.utcoffset().total_seconds()) > 14 * 3600):
            return reject('schedule_start_timezone_invalid')
        if start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            return reject('schedule_event_started_or_not_future')
        local = start.astimezone(SAST)
        if local.date() != target:
            return reject('schedule_event_target_date_mismatch')
    except (TypeError, ValueError, OverflowError):
        return reject('schedule_start_timestamp_invalid')
    return local.date().isoformat(), local.strftime('%H:%M'), start.isoformat()


def fetch_fixture_comparison(fixture: dict, *, refresh_evidence: bool = False, stage_counts: dict | None = None,
                             capture_replay: bool = False, response_observer=None) -> list[dict]:
    """Fetch and cache one prediction-matched fixture, retaining timestamps."""
    from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
    fixture = dict(fixture)
    evidence_fixture = dict(fixture)
    needs_schedule = fixture.get('listing_schedule_status') == 'unverified'
    if needs_schedule and not capture_replay:
        raise PageFetchError('parser', 'unverified listing schedule is diagnostic only')
    event_id = str(fixture.get("event_id") or "")
    href = str(fixture.get("match_url") or "")
    if (not re.fullmatch(r"[A-Za-z0-9]{1,32}", event_id) or not href.startswith("/tennis/")
            or "\\" in href or urlsplit(href).query or urlsplit(href).fragment
            or not _is_match_link(href) or href.rstrip('/').split('/')[-1] != event_id):
        raise PageFetchError("parser", "fixture URL or event identity invalid")
    fetched = False
    def count(key, amount=1):
        if stage_counts is not None:
            stage_counts[key] = stage_counts.get(key, 0) + amount
    def fetch():
        nonlocal fetched
        fetched = True
        count("match_page_attempts")
        # Per-fixture variables are reset on every invocation, including errors.
        match_url = BASE_URL + href
        try:
            detail_kwargs = ({"response_observer": lambda response: response_observer("detail", response)}
                             if response_observer is not None else {})
            page, page_at = _fetch_comparison_document(match_url, **detail_kwargs)
        except PageFetchError as exc:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "detail", exc.kind, status=getattr(exc, "http_status", None), observed_at=getattr(exc, "observed_at", ""), response_sha256=getattr(exc, "response_sha256", ""))
            exc.stage = "match_page"
            raise
        count("match_page_success")
        capture_betexplorer_fragment(page, "detail", event_id=event_id, observed_at=page_at, status=200)
        if capture_replay:
            from racketfactory.betexplorer_evidence import capture_replay as save_replay
            capture_result = save_replay(page, "detail", evidence_fixture, observed_at=page_at, request_url=match_url)
            if capture_result["file_written"]:
                count("replay_detail_file_written")
            if capture_result["discovery_material_retained"]:
                count("replay_detail_material_retained")
            if capture_result["material_sufficiency"] == "insufficient":
                count("replay_detail_insufficient")
        if needs_schedule:
            schedule = _detail_scheduled_start(page, fixture, observed_at=page_at)
            if schedule is None:
                count('schedule_unverified')
                error = PageFetchError('parser', 'match page schedule or fixture identity unverified')
                error.stage = 'match_schedule'
                raise error
            fixture['match_date'], fixture['match_time'], fixture['commence_time'] = schedule
            import hashlib
            fixture['schedule_evidence'] = {
                'status': 'candidate_unverified_contract', 'provider': SOURCE_NAME,
                'event_id': event_id, 'match_url': href,
                'player_home': fixture.get('player_home'), 'player_away': fixture.get('player_away'),
                'sport': 'Tennis', 'event_status': 'https://schema.org/EventScheduled',
                'commence_time': schedule[2], 'observed_at': page_at,
                'response_sha256': hashlib.sha256(page.encode()).hexdigest(),
                'contract_validation': 'not_authenticated',
            }
            count('schedule_candidate_from_match_page')
            count('schedule_contract_unverified')
        discovery_metrics = {}
        endpoints = _comparison_endpoint_candidates(page, event_id, metrics=discovery_metrics)
        if capture_replay and stage_counts is not None and stage_counts.get("replay_detail_file_written"):
            # Compare against the captured DOM, without implying that an empty
            # result proves the omitted JavaScript is sufficient for a new parser.
            import json
            from racketfactory.rebuild_diagnostics import directory
            replay_file = directory() / ("betexplorer_replay_" + event_id + ".json")
            try:
                evidence = json.loads(replay_file.read_text())
                replay = _comparison_endpoint_candidates(evidence["detail"]["replay_html"], event_id)
                agrees = replay == endpoints
                replay_status = ("match_nonempty" if agrees and endpoints else
                                 "match_empty_inconclusive" if agrees else "mismatch")
                evidence["detail"]["current_parser_replay_agreement"] = replay_status
                evidence["detail"]["same_current_discovery_result"] = agrees
                evidence["detail"]["empty_discovery_does_not_prove_future_parser_fidelity"] = not bool(endpoints)
                if not agrees:
                    evidence["detail"]["material_sufficiency"] = "insufficient"
                    categories = evidence["detail"].setdefault("omission_categories", [])
                    if "current_parser_replay_mismatch" not in categories:
                        categories.append("current_parser_replay_mismatch")
                    count("replay_detail_insufficient")
                replay_file.write_text(json.dumps(evidence, indent=2, ensure_ascii=True), encoding="utf-8")
                if agrees and endpoints:
                    count("replay_discovery_agrees_nonempty")
                elif agrees:
                    count("replay_discovery_empty_inconclusive")
                else:
                    count("replay_discovery_mismatch")
            except (OSError, ValueError, KeyError, TypeError):
                count("replay_comparison_failed")
                count("replay_detail_insufficient")
        for key, amount in discovery_metrics.items():
            count(key, amount)
        if not endpoints:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "detail", "parser")
            error = PageFetchError("parser", "tennis match-winner endpoint or scope not explicitly established")
            error.stage = "endpoint_discovery"
            raise error
        count("comparison_endpoint_discovered")
        count("comparison_attempts")
        try:
            comparison_kwargs = ({"response_observer": lambda response: response_observer("comparison", response)}
                                 if response_observer is not None else {})
            payload, observed_at = _fetch_comparison_document(endpoints[0]["url"], referer=match_url,
                                                               **comparison_kwargs)
        except PageFetchError as exc:
            if getattr(exc, "http_status", None) is not None:
                count("comparison_http_response_observed")
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "comparison", exc.kind, status=getattr(exc, "http_status", None), observed_at=getattr(exc, "observed_at", ""), response_sha256=getattr(exc, "response_sha256", ""))
            exc.stage = "comparison_request"
            raise
        count("comparison_response_success")
        count("comparison_http_response_observed")
        try:
            import json
            html = json.loads(payload).get("odds", "")
        except (ValueError, AttributeError):
            html = ""
        if not isinstance(html, str):
            html = ""
        capture_betexplorer_fragment(html or "<div></div>", "comparison", event_id=event_id, observed_at=observed_at, status=200, response_body=payload)
        if capture_replay:
            from racketfactory.betexplorer_evidence import capture_replay as save_replay
            result = save_replay(html, "comparison", evidence_fixture, observed_at=observed_at,
                                 response_body=payload, request_url=endpoints[0]["url"])
            if result["file_written"]:
                count("replay_comparison_file_written")
            if result["discovery_material_retained"]:
                count("replay_comparison_material_retained")
            if result["material_sufficiency"] == "insufficient":
                count("replay_comparison_insufficient")
        try:
            rows, metrics = _comparison_response_rows(payload, fixture, endpoints[0], observed_at)
        except PageFetchError as exc:
            from racketfactory.rebuild_diagnostics import capture_betexplorer_failure
            capture_betexplorer_failure(event_id, "comparison", exc.kind, status=200, observed_at=observed_at)
            exc.stage = "comparison_parse"
            raise
        count("comparison_parsed")
        count("concrete_bookmaker_rows", metrics.get("bookmaker_rows", 0))
        count("identified_offers", len(rows))
        if not capture_replay:
            from racketfactory.execution import rebuild_quote_execution_problem
            count("settlement_compatible_offers", sum(not rebuild_quote_execution_problem(r) for r in rows))
        from racketfactory.rebuild_diagnostics import capture_comparison_offers
        capture_comparison_offers(event_id, rows, metrics)
        logger.info("BetExplorer comparison parse counts: %s", metrics)
        if not rows:
            error = PageFetchError("parser", "no uniquely oriented named-bookmaker quotes")
            error.stage = "bookmaker_rows"
            raise error
        if needs_schedule:
            # The comparison response was captured for review only. A single
            # SportsEvent cannot establish listing coverage or rule out all
            # conflicting schedule/status signals, so never cache or return
            # its quotes as a result of this unverified listing.
            error = PageFetchError('parser', 'listing schedule and coverage remain unverified')
            error.stage = 'listing_coverage'
            raise error
        return rows
    rows = cached_fetch("betexplorer_detail_" + event_id, fetch, fetch_day=str(fixture.get("match_date") or "")[:10], ttl_minutes=0 if refresh_evidence else 30)
    if not fetched:
        count("detail_cache_hits")
        if needs_schedule:
            # Cached offers do not retain independently re-verifiable match
            # page schedule evidence. Never relabel or refetch them here.
            raise PageFetchError('parser', 'cached match schedule evidence unverified')
    from racketfactory.warehouse import strict_quote_team_match
    if any(str(row.get("event_id") or "") != event_id or row.get("match_url") != href
           or not strict_quote_team_match(str(row.get("player_home") or ""), str(fixture.get("player_home") or ""))
           or not strict_quote_team_match(str(row.get("player_away") or ""), str(fixture.get("player_away") or "")) for row in rows):
        raise PageFetchError("parser", "cached fixture identity mismatch")
    return rows
