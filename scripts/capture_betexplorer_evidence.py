"""Bounded evidence-only BetExplorer probe; never evaluates or publishes bets."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from racketfactory.kickoff import SAST
from racketfactory.rebuild_diagnostics import directory
from racketfactory.sources import betexplorer
from racketfactory.sources._page_odds import PageFetchError


def capture(target: str, *, browser_observer: bool = False, event_id: str | None = None) -> dict:
    out = {"target_date": target, "purpose": "evidence_only", "publication_attempted": False,
           "listing_attempted": False, "fixtures_attempted": 0, "stage_counts": {}, "failures": [],
           "capture_status": "not_attempted", "parser_status": "not_attempted", "quote_eligibility": "not_evaluated",
           "detail_response_fetched": False, "evidence_file_written": False,
           "discovery_material_retained": False, "material_sufficiency": "not_evaluated",
           "current_parser_replay_agreement": "not_compared", "comparison_request": "not_attempted"}
    try:
        out["listing_attempted"] = True
        rows, status = betexplorer._fetch_live(target, with_status=True)
        out["listing_status"] = status
        from racketfactory.betexplorer_evidence import _identity
        from racketfactory.warehouse import names_match
        if event_id is not None:
            # An explicitly selected listing event is diagnostic only. The
            # listing day is not its scheduled date and its status is unknown.
            # Never select an arbitrary URL or auto-crawl a partial listing.
            if status not in {"complete", "partial"}:
                return out
            matching = [row for row in rows if str(row.get('event_id') or '') == event_id]
            if len(matching) != 1:
                out['capture_status'] = 'selected_event_not_unique'
                return out
            fixture = matching[0]
            identity = _identity(fixture)
            if (not identity or str(fixture.get('listing_date') or '')[:10] != target
                    or names_match(identity['player_home'], identity['player_away'])):
                out['capture_status'] = 'selected_event_identity_unverified'
                return out
            out['selection'] = 'explicit_listing_event_schedule_unknown'
        else:
            if status != "complete":
                return out
            now = datetime.now(SAST)
            candidates = []
            for row in rows:
                if str(row.get('match_date') or '')[:10] != target or not _identity(row):
                    continue
                if names_match(str(row.get('player_home') or ''), str(row.get('player_away') or '')):
                    continue
                try:
                    stamp = str(row.get('match_time') or '')
                    if stamp == '00:00':
                        continue
                    kickoff = datetime.strptime(f'{target} {stamp}', '%Y-%m-%d %H:%M').replace(tzinfo=SAST)
                    if kickoff <= now:
                        continue
                except (ValueError, TypeError):
                    continue
                candidates.append((kickoff, str(row['event_id']), row))
            # Conflicting IDs or duplicate player pairs cannot establish a unique
            # fixture; do not arbitrarily select the first listing row.
            from collections import Counter
            ids = Counter(item[1] for item in candidates)
            pairs = Counter(tuple(sorted((str(item[2]['player_home']).casefold(),
                                          str(item[2]['player_away']).casefold()))) for item in candidates)
            candidates = [item for item in candidates if ids[item[1]] == 1 and
                          pairs[tuple(sorted((str(item[2]['player_home']).casefold(),
                                              str(item[2]['player_away']).casefold())))] == 1]
            out['upcoming_candidates'] = len(candidates)
            if not candidates:
                out['capture_status'] = 'no_eligible_fixture'
                return out
            _, _, fixture = min(candidates, key=lambda x: (x[0], x[1]))
            out['selection'] = 'upcoming_from_complete_listing'
        out['fixtures_attempted'] = 1
        if browser_observer:
            from racketfactory.betexplorer_evidence import observe_in_browser
            observed = observe_in_browser(fixture)
            out['browser_observer'] = observed
            out['detail_response_fetched'] = any(r.get('resource') == 'document' and r.get('http_status') == 200
                                                 for r in observed['responses'])
            out['evidence_file_written'] = observed['detail_file_written']
            out['discovery_material_retained'] = observed['detail_material_retained']
            out['material_sufficiency'] = observed['detail_material_sufficiency']
            out['comparison_request'] = ('response_observed' if any(r.get('resource') in {'xhr', 'fetch'}
                                                                      for r in observed['responses'])
                                         else 'not_attempted')
            out['capture_status'] = ('browser_comparison_saved' if observed['comparison_material_retained']
                                     else 'browser_observation_inconclusive')
            return out
        try:
            betexplorer.fetch_fixture_comparison(fixture, refresh_evidence=True, capture_replay=True,
                                                  stage_counts=out['stage_counts'])
            out['parser_status'] = 'parsed'
        except Exception as exc:
            kind = exc.kind if isinstance(exc, PageFetchError) else 'unexpected'
            stage = getattr(exc, 'stage', 'unknown')
            out['failures'].append({'kind': kind, 'stage': stage})
            out['parser_status'] = 'failed_' + stage
        counts = out['stage_counts']
        out['detail_response_fetched'] = bool(counts.get('match_page_success'))
        out['evidence_file_written'] = bool(counts.get('replay_detail_file_written'))
        out['discovery_material_retained'] = bool(counts.get('replay_detail_material_retained'))
        if out['evidence_file_written']:
            out['material_sufficiency'] = 'insufficient' if counts.get('replay_detail_insufficient') else 'not_established'
            if not out['discovery_material_retained']:
                out['capture_status'] = 'detail_insufficient'
            elif counts.get('replay_detail_insufficient'):
                out['capture_status'] = 'detail_saved_incomplete'
            else:
                out['capture_status'] = 'detail_saved'
        else:
            out['capture_status'] = 'not_saved'
        if counts.get('replay_discovery_agrees_nonempty'):
            out['current_parser_replay_agreement'] = 'match_nonempty'
        elif counts.get('replay_discovery_empty_inconclusive'):
            out['current_parser_replay_agreement'] = 'match_empty_inconclusive'
        elif counts.get('replay_discovery_mismatch'):
            out['current_parser_replay_agreement'] = 'mismatch'
        elif counts.get('replay_comparison_failed'):
            out['current_parser_replay_agreement'] = 'comparison_failed'
        if counts.get('comparison_http_response_observed'):
            out['comparison_request'] = 'response_observed'
        elif counts.get('comparison_attempts'):
            # A cooldown or transport error can occur before a request is sent.
            out['comparison_request'] = 'attempted_not_confirmed'
    except Exception as exc:
        out["failures"].append({"kind": exc.kind if isinstance(exc, PageFetchError) else "unexpected", "stage": "listing"})
    return out


def main(argv=()) -> int:
    parser = argparse.ArgumentParser(description='Bounded evidence-only BetExplorer capture')
    parser.add_argument('--browser-observer', action='store_true',
                        help='replace direct detail/comparison fetches with a bounded browser observation')
    parser.add_argument('--event-id', help='select one event ID present in the requested listing; schedule remains unknown')
    args = parser.parse_args(argv)
    path = directory()
    if path is None:
        raise RuntimeError("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR must be set")
    path.mkdir(parents=True, exist_ok=True)
    result = capture(datetime.now(SAST).date().isoformat(), browser_observer=args.browser_observer, event_id=args.event_id)
    (path / "evidence_collection.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    # Capture can succeed even if the production parser fails. It is not a bet.
    return 0 if result['capture_status'] in {'detail_saved', 'browser_comparison_saved'} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
