"""Shared, bounded prediction-targeted comparison discovery."""
from __future__ import annotations

import os


def _collection_now():
    from datetime import datetime
    from racketfactory.kickoff import SAST
    return datetime.now(SAST)


def collect_prediction_comparisons(target_date: str, predictions: list[dict], be_rows: list[dict], listing_status: str) -> tuple[list[dict], dict, bool]:
    """Target detail requests to uniquely identified prediction fixtures.

    Discovery rows are not bookmaker offers. Return partial explicitly so
    transport/parser failures can never masquerade as a complete empty board.
    """
    from racketfactory.sources import betexplorer
    partial = False
    detail_offers = []
    detail_counts = {"predictions": 0, "matched_fixtures": 0, "unmatched_or_ambiguous": 0,
                     "attempted": 0, "offers": 0, "failures": {}, "limit_exceeded": 0,
                     "match_page_attempts": 0, "match_page_success": 0, "comparison_endpoint_discovered": 0,
                     "comparison_attempts": 0, "comparison_response_success": 0, "comparison_parsed": 0,
                     "concrete_bookmaker_rows": 0, "identified_offers": 0,
                     "settlement_compatible_offers": 0, "detail_cache_hits": 0,
                     "skipped_started_fixtures": 0, "skipped_conflicting_kickoff_fixtures": 0,
                     "excluded_started_fixtures": 0, "relevant_upcoming_fixtures": 0,
                     "unknown_or_conflicting_kickoff_fixtures": 0, "unexamined_fixtures": 0,
                     "unresolved_fixtures": 0, "fresh_settlement_compatible_fixtures": 0}
    # A zero prediction count here means none were evaluated, not an empty card.
    if listing_status != "complete":
        detail_counts["matching_skipped_reason"] = "listing_not_complete"
        detail_counts["listing_status"] = listing_status
        detail_counts["input_predictions"] = len(predictions)
        detail_counts["target_date_input_predictions"] = sum(
            str(p.get("match_date") or "")[:10] == target_date for p in predictions)
    if listing_status == "complete" and not be_rows:
        detail_counts["predictions"] = sum(str(p.get("match_date") or "")[:10] == target_date for p in predictions)
        detail_counts["unmatched_or_ambiguous"] = detail_counts["predictions"]
        partial = bool(detail_counts["predictions"])
    if listing_status == "complete" and be_rows:
        import pandas as pd
        from racketfactory.warehouse import _match_api_odds_row, names_match, strict_quote_team_match
        from racketfactory.sources._page_odds import PageFetchError
        matched = {}
        priorities = {}
        kickoff_states = {}
        kickoff_values = {}
        kickoff_unknown = set()
        identity_conflicts = set()
        from datetime import datetime
        from racketfactory.kickoff import SAST
        now = _collection_now()
        for pred in predictions:
            if str(pred.get("match_date") or "")[:10] != target_date:
                continue
            detail_counts["predictions"] += 1
            fixture, reversed_order = _match_api_odds_row(pd.Series(pred), be_rows)
            if not fixture:
                detail_counts["unmatched_or_ambiguous"] += 1
                continue
            ph, pa = str(pred.get("player_home") or ""), str(pred.get("player_away") or "")
            fh, fa = str(fixture.get("player_home") or ""), str(fixture.get("player_away") or "")
            direct = strict_quote_team_match(ph, fh) and strict_quote_team_match(pa, fa)
            reverse = strict_quote_team_match(ph, fa) and strict_quote_team_match(pa, fh)
            if names_match(ph, pa) or names_match(fh, fa) or direct == reverse:
                detail_counts["unmatched_or_ambiguous"] += 1
                continue
            eid = str(fixture.get("event_id") or "")
            if not eid:
                detail_counts["unmatched_or_ambiguous"] += 1
                continue
            previous = matched.get(eid)
            if previous and (not strict_quote_team_match(str(previous.get("player_home") or ""), fh)
                             or not strict_quote_team_match(str(previous.get("player_away") or ""), fa)
                             or str(previous.get("match_url") or "") != str(fixture.get("match_url") or "")):
                identity_conflicts.add(eid)
            else:
                matched[eid] = fixture
            try:
                # BetClan uses 00:00 as its missing-time fallback; it is not
                # evidence that the match really started at midnight.
                if str(pred.get("match_time") or "") == "00:00":
                    raise ValueError("ambiguous midnight fallback")
                start = datetime.strptime(f"{target_date} {pred.get('match_time')}", "%Y-%m-%d %H:%M").replace(tzinfo=SAST)
                priority = (0 if start > now else 2, start.isoformat())
                kickoff_states.setdefault(eid, set()).add(priority[0])
                kickoff_values.setdefault(eid, set()).add(start.isoformat())
            except (TypeError, ValueError):
                kickoff_unknown.add(eid)
                priority = (1, "")
            priorities[eid] = min(priorities.get(eid, priority), priority)
        detail_counts["matched_fixtures"] = len(matched)
        # A started fixture cannot yield an executable prematch quote. If
        # duplicate predictions disagree across kickoff, fail closed rather
        # than letting the earlier priority choose a purported future time.
        ordered = []
        excluded = set()
        conflicting = set(identity_conflicts)
        attempted_ids = set()
        for eid in sorted(matched, key=lambda e: (priorities[e], e)):
            states = kickoff_states.get(eid, set())
            if eid in identity_conflicts:
                continue
            if len(kickoff_values.get(eid, set())) > 1:
                detail_counts["skipped_conflicting_kickoff_fixtures"] += 1
                conflicting.add(eid)
            elif 2 in states and eid not in kickoff_unknown:
                detail_counts["skipped_started_fixtures"] += 1
                excluded.add(eid)
            else:
                ordered.append(eid)
        detail_counts["limit_exceeded"] = max(0, len(ordered) - betexplorer.MAX_COMPARISON_FIXTURES)
        for eid in ordered:
            if detail_counts["attempted"] >= betexplorer.MAX_COMPARISON_FIXTURES:
                break
            # A match can start while earlier detail requests are in flight.
            if priorities[eid][0] == 0 and datetime.fromisoformat(priorities[eid][1]) <= _collection_now():
                # An unknown duplicate kickoff is unresolved, not an
                # authoritative basis for exclusion.
                if eid not in kickoff_unknown:
                    detail_counts["skipped_started_fixtures"] += 1
                    excluded.add(eid)
                    continue
            fixture = matched[eid]
            detail_counts["attempted"] += 1
            attempted_ids.add(eid)
            try:
                # Capture real response structure for up to two fixtures;
                # all other fixtures can reuse their original cached observation.
                if os.getenv("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR") and detail_counts["attempted"] <= 2:
                    offers = betexplorer.fetch_fixture_comparison(fixture, refresh_evidence=True, stage_counts=detail_counts)
                else:
                    offers = betexplorer.fetch_fixture_comparison(fixture, stage_counts=detail_counts)
                detail_offers.extend(offers)
            except Exception as exc:
                kind = exc.kind if isinstance(exc, PageFetchError) else "unexpected"
                stage = getattr(exc, "stage", "unknown")
                failure = f"{stage}:{kind}"
                detail_counts["failures"][failure] = detail_counts["failures"].get(failure, 0) + 1
                if kind in {"challenge", "rate_limit", "auth"}:
                    break
        detail_counts["offers"] = len(detail_offers)
        # A fetched page is not complete quote coverage when no offer proves
        # its market, age and settlement. Never infer missing bookmaker rules.
        from datetime import datetime, timezone
        from math import isfinite
        from racketfactory.execution import rebuild_quote_execution_problem
        verified = set()
        for row in detail_offers:
            try:
                observed = datetime.fromisoformat(str(row.get("observed_at") or "").replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - observed).total_seconds() if observed.tzinfo else -1
                prices = [float(row.get(k)) for k in ("odds_home", "odds_away")]
                if (not 0 <= age <= 30 * 60 or not all(isfinite(p) and p > 1 for p in prices) or
                    str(row.get("match_date") or "")[:10] != target_date or
                    str(row.get("market") or "").lower() not in {"h2h", "1x2", "match_winner", "match winner"} or
                    str(row.get("period") or "").lower() not in {"match", "full match", "full_match", "full", "0"} or
                    (row.get("line") is not None and str(row.get("line")).strip()) or
                    rebuild_quote_execution_problem(row)):
                    continue
                verified.add(str(row.get("event_id") or ""))
            except (TypeError, ValueError, OverflowError):
                continue
        matched_ids = set(matched)
        unknown = (kickoff_unknown | conflicting) - excluded
        relevant = matched_ids - excluded - unknown
        unexamined = matched_ids - excluded - conflicting - attempted_ids
        # An expired known fixture is removed only after a reliable kickoff;
        # unknown/conflicting kickoff and unexamined relevant fixtures remain
        # unresolved even if some quotes happened to be obtained.
        verified_relevant = verified & relevant
        unresolved = unknown | (relevant - verified_relevant)
        detail_counts["limit_exceeded"] = (len(unexamined) if detail_counts["attempted"] >= betexplorer.MAX_COMPARISON_FIXTURES else 0)
        detail_counts.update({"excluded_started_fixtures": len(excluded),
                              "relevant_upcoming_fixtures": len(relevant),
                              "unknown_or_conflicting_kickoff_fixtures": len((kickoff_unknown | (conflicting - identity_conflicts)) - excluded),
                              "ambiguous_identity_fixtures": len(identity_conflicts),
                              "unexamined_fixtures": len(unexamined),
                              "unresolved_fixtures": len(unresolved),
                              "fresh_settlement_compatible_fixtures": len(verified_relevant)})
        if (detail_counts["failures"] or detail_counts["unmatched_or_ambiguous"] or unresolved or unexamined):
            partial = True
    return detail_offers, detail_counts, partial


def targeted_comparison_rows(target_date: str, predictions: list[dict]) -> list[dict]:
    """Use the guarded detail path during normal warehouse collection too.

    The same-run cache stores the *status* along with offers: a failed or
    partial collection is not turned into a successful empty response. Quote
    timestamps remain the original provider observation times.
    """
    import hashlib
    import json
    import logging
    from datetime import datetime, timezone
    from pathlib import Path
    from racketfactory.fetch_cache import cached_fetch
    from racketfactory.sources import betclan, betexplorer, oddsportal_upcoming
    from racketfactory.odds_compare import merge_comparison_rows
    from racketfactory.execution import rebuild_quote_execution_problem

    log = logging.getLogger(__name__)
    identities = sorted((str(p.get('match_date') or ''), str(p.get('player_home') or ''),
                         str(p.get('player_away') or '')) for p in predictions)
    run = os.environ.get('RACKET_FACTORY_RUN_AS_OF', '')
    fingerprint = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
    key = hashlib.sha256(json.dumps([run, target_date]).encode()).hexdigest()[:24]

    def collect():
        metrics = {}
        try:
            if os.getenv(betexplorer.DISABLE_ENV, '').strip().lower() in {'1','true','yes','on'}:
                return [{'status': 'disabled', 'counts': {}, 'offers': []}]
            fixtures, status = betexplorer._fetch_live(target_date, with_status=True, metrics=metrics)
            offers, counts, partial = collect_prediction_comparisons(target_date, predictions, fixtures, status)
            if predictions and not fixtures:
                partial = True
            return [{'status': 'partial' if partial else status, 'listing_rows': len(fixtures),
                     'listing_counts': metrics, 'counts': counts, 'offers': offers}]
        except Exception as exc:
            return [{'status': 'failed', 'error_type': type(exc).__name__,
                     'failure_kind': getattr(exc, 'kind', 'unexpected'), 'offers': []}]

    # Cache only within the identified run, and never relabel old observations.
    def collect_for_run():
        result = collect()
        result[0]['prediction_fingerprint'] = fingerprint
        return result
    result = (cached_fetch('targeted_quotes_' + key, collect_for_run, fetch_day=target_date,
                           ttl_minutes=24 * 60)
              if run else collect_for_run())[0]
    if result.get('prediction_fingerprint') != fingerprint:
        # Do not silently treat a different fixture set as covered or exceed
        # the per-run detail request budget by recrawling it.
        result['status'] = 'partial'
        result['fixture_set_changed'] = True
    betclan_status = betclan.current_run_status(run)
    result['betclan_status'] = betclan_status
    if betclan_status != 'complete' and result.get('status') == 'complete':
        result['status'] = 'partial'
    result['run_as_of'] = run
    now = datetime.now(timezone.utc)
    observed_times = []
    for offer in result.get('offers', []):
        try:
            observed = datetime.fromisoformat(str(offer.get('observed_at') or '').replace('Z', '+00:00'))
            if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= 30 * 60:
                result['status'] = 'partial'
            else:
                observed_times.append(observed.astimezone(timezone.utc))
        except (ValueError, TypeError, OverflowError):
            result['status'] = 'partial'
    result['oldest_offer_observed_at'] = min(observed_times).isoformat() if observed_times else None
    result['evaluated_at'] = now.isoformat()
    # Persist bounded stage counts, not page bodies, credentials or full offers.
    status = {k: v for k, v in result.items() if k != 'offers'}
    try:
        (Path(__file__).resolve().parents[2] / "localdata" / f'quote_collection_{target_date}.json').write_text(json.dumps(status, indent=2, sort_keys=True))
    except (OSError, TypeError) as exc:
        log.warning('Could not write quote collection status: %s', type(exc).__name__)
    try:
        from racketfactory.rebuild_diagnostics import directory
        diagnostic_dir = directory()
        if diagnostic_dir is not None:
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            (diagnostic_dir / 'quote_collection.json').write_text(json.dumps(status, indent=2, sort_keys=True))
    except (OSError, TypeError) as exc:
        log.warning('Could not retain quote collection diagnostics: %s', type(exc).__name__)
    log.info('Targeted quote collection %s: %s', target_date, status)
    # Multiple rows for the same fixture are not multiple identities. Prefer a
    # settlement-compatible single bookmaker, never the highest price.
    chosen = {}
    for row in sorted(result.get('offers', []), key=lambda r: (str(r.get('event_id') or ''),
                       bool(rebuild_quote_execution_problem(r)), str(r.get('bookmaker') or '').casefold(),
                       str(r.get('bookmaker_id') or ''))):
        key = (str(row.get('event_id') or ''), str(row.get('match_date') or ''),
               str(row.get('player_home') or ''), str(row.get('player_away') or ''))
        chosen.setdefault(key, row)
    try:
        optional = oddsportal_upcoming.fetch_oddsportal_upcoming_rows(target_date)
    except Exception as exc:
        log.warning('OddsPortal comparison unavailable: %s', type(exc).__name__)
        optional = []
    return merge_comparison_rows(list(chosen.values()), optional)
