#!/usr/bin/env python3
"""Fail closed on stale newly changed ticket cards immediately before Git push.

Pass the Git baseline that preceded the publication commit. Unchanged saved
cards are historical records and must not be relabelled as fresh or regraded.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.auto_tickets import fresh_ticket_problem, now_local


def _git_file(ref: str, path: str) -> bytes | None:
    result = subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return result.stdout if result.returncode == 0 else None


def check_card(current: bytes | None, previous: bytes | None, target_date: str) -> str:
    if current == previous:
        return ''
    if current is None:
        return 'new card missing'
    try:
        card = json.loads(current)
        if not isinstance(card, dict) or card.get('date') != target_date or not isinstance(card.get('accas'), list):
            return 'changed card malformed or date mismatch'
        now = now_local()
        observed = datetime.fromisoformat(str(card.get('collection_observed_at') or '').replace('Z', '+00:00'))
        if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= 30 * 60:
            return 'collection observation stale or future'
        return fresh_ticket_problem(card['accas'], target_date, now)
    except (ValueError, TypeError, AttributeError):
        return 'changed card invalid'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--baseline', default='HEAD^')
    args = parser.parse_args()
    paths = (f'localdata/auto_tickets_{args.date}.json', 'localdata/auto_tickets_today.json')
    for path in paths:
        problem = check_card(_git_file('HEAD', path), _git_file(args.baseline, path), args.date)
        if problem:
            print('Git publication refused:', path, problem, file=sys.stderr)
            return 1
    print('Git publication check: unchanged historical card or fresh changed card')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
