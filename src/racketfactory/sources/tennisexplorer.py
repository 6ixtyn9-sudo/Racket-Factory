"""Daily two-row tennis results and labelled H/A prices from TennisExplorer.

Results-page prices are retrospective reference prices, NEVER executable pick
prices. Only a blank-score fixture from a schedule page is eligible for pricing.
"""
from datetime import datetime, timezone
from pathlib import Path
import logging
import re
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE = 'https://www.tennisexplorer.com'
logger = logging.getLogger(__name__)


def daily_url(day, completed=True):
    return f"{BASE}/{'results' if completed else 'next'}/?type=all&year={day[:4]}&month={day[5:7]}&day={day[8:10]}"


def _player(row):
    link = row.select_one('a[href*="/player/"], a[href*="/doubles-team/"]')
    if not link:
        return None
    cell = link.find_parent('td')
    links = cell.select('a[href*="/player/"], a[href*="/doubles-team/"]')
    name = ' / '.join((a.get('title') or a.get_text(' ', strip=True)) for a in links)
    score_cell = cell.find_next_sibling('td')
    score = score_cell.get_text(' ', strip=True) if score_cell else ''
    return name, score


def parse_daily(html, day, *, completed=True):
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for table in soup.select('table.result')[:1]:
        tournament, tour, pending = '', 'UNKNOWN', None
        for tr in table.select('tr'):
            player = _player(tr)
            if not player:
                pending = None
                header = tr.select_one('td.t-name')
                if header:
                    tournament = header.get_text(' ', strip=True)
                    href = str(header.find('a').get('href', '')) if header.find('a') else ''
                    tour = ('CHALLENGER' if 'challenger' in tournament.lower() else
                            'WTA' if 'wta' in href.lower() else
                            'ATP' if 'atp' in href.lower() else 'UNKNOWN')
                continue
            detail = tr.select_one('a[href*="match-detail/"]')
            # First row owns time/odds/detail cells spanning both players.
            if detail is not None or tr.select_one('td[rowspan="2"]'):
                pending = (tr, player, detail)
                continue
            if pending is None:
                continue
            first, (a, sa), detail = pending
            pending = None
            b, sb = player
            if a == b or not a or not b:
                continue
            status_text = (first.get_text(' ', strip=True) + ' ' + tr.get_text(' ', strip=True)).lower()
            if re.search(r'\b(ret\.?|retired|w/o|walkover|postponed|cancelled|suspended|live)\b', status_text):
                continue
            winner, score = '', ''
            if completed:
                if not sa.isdigit() or not sb.isdigit():
                    continue
                x, y = int(sa), int(sb)
                # A best-of-three match cannot finish 2-2 or 2-3; best-of-five
                # is identified by winner's three sets, not a prediction field.
                if (x, y) not in {(2, 0), (2, 1), (0, 2), (1, 2), (3, 0), (3, 1), (3, 2), (0, 3), (1, 3), (2, 3)}:
                    continue
                winner, score = (a if x > y else b), f'{x}{y}'
            elif sa or sb:
                # Live or already scored: do not promote prices to pre-match.
                continue
            price_cells = first.select('td.course')
            prices = [float(c.get_text(strip=True)) if re.fullmatch(r'\d+\.\d+', c.get_text(strip=True)) else None for c in price_cells]
            oa, ob = prices[:2] if len(prices) == 2 else (None, None)
            source_url = urljoin(BASE, detail['href']) if detail else daily_url(day, completed)
            event_id = re.search(r'[?&]id=(\d+)', source_url)
            rows.append(dict(match_date=day, tour=tour, tournament=tournament,
                             player_a=a, player_b=b, winner=winner, score=score,
                             odds_a=oa, odds_b=ob, source='TennisExplorer',
                             source_url=source_url, source_event_id=event_id.group(1) if event_id else '',
                             status='finished' if completed else 'scheduled',
                             price_kind='result_reference' if completed else 'prematch',
                             captured_at=datetime.now(timezone.utc).isoformat(),
                             _score_perspective='player_a_sets-player_b_sets', _is_live=False))
    return rows


def fetch_daily(day, *, completed=True):
    url = daily_url(day, completed)
    response = requests.get(url, impersonate='chrome', timeout=30)
    response.raise_for_status()
    # Fail visibly on a layout change/block, rather than calling it no coverage.
    if 'table' not in response.text or 'Just a moment' in response.text:
        raise ValueError('TennisExplorer did not return a result table')
    visible = BeautifulSoup(response.text, 'html.parser').get_text(' ', strip=True)
    shown = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b', visible)
    if not shown or f"{shown[3]}-{int(shown[2]):02d}-{int(shown[1]):02d}" != day:
        raise ValueError('TennisExplorer returned an unverified or different page date')
    rows = parse_daily(response.text, day, completed=completed)
    logger.info('TennisExplorer %s: %s rows for %s', 'results' if completed else 'fixtures', len(rows), day)
    return rows


def load_prematch_odds(data_dir, day):
    path = Path(data_dir) / f'tennisexplorer_odds_{day}.json'
    if not path.exists():
        return []
    import json
    payload = json.loads(path.read_text())
    # Short-lived market snapshot. A failed refresh must not resurrect stale odds.
    now = datetime.now(timezone.utc)
    rows = []
    for r in payload:
        try:
            captured = datetime.fromisoformat(r['captured_at'])
            age = (now - captured).total_seconds()
        except (ValueError, KeyError, TypeError):
            continue
        if not (0 <= age <= 1800) or r.get('price_kind') != 'prematch' or r.get('winner') or r.get('match_date') != day:
            continue
        rows.append(dict(match_date=day, player_home=r['player_a'], player_away=r['player_b'],
                         odds_home=r.get('odds_a'), odds_away=r.get('odds_b'), source='TennisExplorer',
                         source_url=r.get('source_url'), event_id=r.get('source_event_id'),
                         odds_home_bookmaker='TennisExplorer average', odds_away_bookmaker='TennisExplorer average'))
    return rows
