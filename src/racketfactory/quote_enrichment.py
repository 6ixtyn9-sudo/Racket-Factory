"""Shared, bounded prediction-targeted comparison discovery."""
from __future__ import annotations

import math
import os
import re


def _collection_now():
    from datetime import datetime
    from racketfactory.kickoff import SAST
    return datetime.now(SAST)


def _discovery_tour_tokens(value: object) -> set[str]:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    tokens = set(text.split())
    aliases = {
        "atp": {"atp"}, "wta": {"wta"}, "challenger": {"challenger"},
        "itf": {"itf", "men", "women", "m", "w"}, "utr": {"utr"},
    }
    found = set()
    for label, words in aliases.items():
        if tokens & words:
            found.add(label)
    return found


def _discovery_context_tokens(value: object) -> set[str]:
    ignored = {"atp", "wta", "itf", "utr", "challenger", "tennis", "men", "women",
               "singles", "doubles", "the", "open", "tour", "match"}
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
            if len(token) > 1 and token not in ignored}


def _listing_status_conflict(row: dict) -> str:
    """Fail closed on explicit non-prematch or unrecognized listing state."""
    for key in ("status", "event_status", "match_status", "fixture_status", "listing_status",
                "status_state", "listing_status_state", "live_status"):
        value = str(row.get(key) or "").strip().casefold()
        if not value:
            continue
        norm = re.sub(r"[^a-z0-9]+", " ", value).strip()
        if any(token in norm.split() for token in (
                "live", "inplay", "in", "progress", "started", "finished", "complete",
                "completed", "final", "retired", "walkover", "cancelled", "canceled",
                "postponed", "abandoned", "suspended")):
            return "terminal_or_live_status"
        allowed = {"scheduled", "event scheduled", "events scheduled", "eventschedule", "eventscheduled",
                   "upcoming", "prematch", "pre match", "not started", "unverified", "unknown"}
        if norm not in allowed and not norm.endswith((" event scheduled", " eventscheduled")):
            return "unrecognized_status"
    for key in ("data-live", "data_live", "is_live", "in_play"):
        raw = row.get(key)
        if raw is True or str(raw or "").strip().casefold() in {"1", "true", "yes", "live", "inplay", "in play"}:
            return "terminal_or_live_status"
    return ""


def _provisional_discovery_match(prediction: dict, rows: list[dict], target_date: str):
    """Match an undated discovery row without weakening strict quote matching.

    Only participant identity, doubles structure, event URL/ID, available
    tournament/tour context, and explicit status are considered here. Neither
    listing_date nor any unverified match_date/time is used as fixture proof.
    Returns (row, prediction_order_reversed, reason_code).
    """
    from racketfactory.sources import betexplorer
    from racketfactory.warehouse import strict_quote_team_match

    ph, pa = str(prediction.get("player_home") or "").strip(), str(prediction.get("player_away") or "").strip()
    if not ph or not pa or strict_quote_team_match(ph, pa):
        return None, False, "prediction_participants_invalid"
    pred_doubles = "/" in ph or "/" in pa
    pred_context = " ".join(str(prediction.get(key) or "") for key in (
        "tournament", "tour", "tour_slug", "tournament_slug", "event_text", "match_label", "event", "competition"))
    pred_tours = _discovery_tour_tokens(pred_context)
    pred_tournament = _discovery_context_tokens(" ".join(str(prediction.get(key) or "") for key in (
        "tournament", "tournament_slug", "event_text", "match_label", "event", "competition")))

    candidates = []
    saw_bad_identity = False
    saw_participant_match = False
    saw_context_conflict = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        listing_date = str(row.get("listing_date") or "").strip()[:10]
        if listing_date and listing_date != target_date:
            continue
        if _listing_status_conflict(row):
            continue
        href = str(row.get("match_url") or "")
        path_context = betexplorer._is_match_link(href)
        eid = str(row.get("event_id") or "")
        path_id = href.rstrip("/").rsplit("/", 1)[-1] if href else ""
        if (not path_context or not re.fullmatch(r"[A-Za-z0-9]{1,32}", eid)
                or path_id != eid or "?" in href or "#" in href or "\\" in href):
            saw_bad_identity = True
            continue
        fh, fa = str(row.get("player_home") or "").strip(), str(row.get("player_away") or "").strip()
        if not fh or not fa or strict_quote_team_match(fh, fa):
            continue
        if pred_doubles != ("/" in fh or "/" in fa):
            continue
        direct = strict_quote_team_match(ph, fh) and strict_quote_team_match(pa, fa)
        reverse = strict_quote_team_match(ph, fa) and strict_quote_team_match(pa, fh)
        if direct == reverse:  # neither identity nor unique participant orientation
            continue
        saw_participant_match = True
        row_tours = _discovery_tour_tokens(" ".join(str(row.get(key) or "") for key in (
            "tour_hint", "tour", "tournament", "tournament_slug")))
        row_tournament = _discovery_context_tokens(" ".join(str(row.get(key) or "") for key in (
            "tournament", "tournament_slug", "event_text", "competition")))
        if pred_tours and row_tours and not pred_tours & row_tours:
            saw_context_conflict = True
            continue
        if pred_tournament and row_tournament and not pred_tournament & row_tournament:
            saw_context_conflict = True
            continue
        # Where both path and parsed row identify an event, they must agree.
        path_tournament = _discovery_context_tokens(path_context.get("tournament"))
        if row_tournament and path_tournament and not row_tournament & path_tournament:
            saw_context_conflict = True
            continue
        candidates.append((row, reverse, eid, href))

    if not candidates:
        if saw_context_conflict and saw_participant_match:
            reason = "participant_context_conflict"
        else:
            reason = "invalid_event_identity" if saw_bad_identity else "no_participant_context_match"
        return None, False, reason
    # Repeated links/IDs are ambiguous even if the participant text agrees.
    by_event = {}
    for candidate in candidates:
        by_event.setdefault(candidate[2], []).append(candidate)
    candidates = [c for event_rows in by_event.values() if len(event_rows) == 1 for c in event_rows]
    if not candidates:
        return None, False, "duplicate_event_identity"
    if len(candidates) > 1:
        # A context match does not silently defeat a second candidate whose
        # context is unknown; both remain plausible and the detail path is
        # intentionally not guessed.
        return None, False, "ambiguous_participant_context"
    row, reverse, _, _ = candidates[0]
    return row, reverse, ""


def collect_prediction_comparisons(target_date: str, predictions: list[dict], be_rows: list[dict], listing_status: str,
                                   listing_metrics: dict | None = None) -> tuple[list[dict], dict, bool]:
    """Resolve undated listing candidates, then use the bounded detail path.

    Provisional discovery identity is intentionally separate from the strict,
    dated quote matcher used downstream. Detail evidence must still establish
    the SAST betting day, future kickoff, status, orientation, freshness,
    provenance, and market before a returned offer is counted as resolved.
    """
    from racketfactory.sources import betexplorer
    from racketfactory.sources._page_odds import PageFetchError
    from racketfactory.warehouse import strict_quote_team_match
    from datetime import datetime
    from racketfactory.kickoff import SAST

    partial = listing_status != "complete"
    detail_offers: list[dict] = []
    target_predictions = [p for p in predictions if str(p.get("match_date") or "")[:10] == target_date]
    detail_counts = {
        "predictions": len(target_predictions), "matched_fixtures": 0, "unmatched_or_ambiguous": 0,
        "attempted": 0, "offers": 0, "failures": {}, "limit_exceeded": 0,
        "match_page_attempts": 0, "match_page_success": 0, "comparison_endpoint_discovered": 0,
        "comparison_attempts": 0, "comparison_response_success": 0, "comparison_parsed": 0,
        "concrete_bookmaker_rows": 0, "identified_offers": 0, "settlement_compatible_offers": 0,
        "detail_cache_hits": 0, "skipped_started_fixtures": 0, "skipped_conflicting_kickoff_fixtures": 0,
        "excluded_started_fixtures": 0, "relevant_upcoming_fixtures": 0,
        "unknown_or_conflicting_kickoff_fixtures": 0, "unexamined_fixtures": 0,
        "unresolved_fixtures": 0, "fresh_settlement_compatible_fixtures": 0,
        "fresh_quote_admissible_fixtures": 0, "ambiguous_identity_fixtures": 0,
        "identity_rejections": {},
        "stage_statuses": {
            "listing_recognition": "unknown", "listing_row_accounting": "unknown",
            "listing_coverage": "unknown", "listing_schedule_verification": "unknown",
            "identity_matching": "not_attempted", "schedule_verification": "not_attempted",
            "detail_discovery": "not_attempted", "quote_availability": "not_attempted",
            "quote_resolution": "not_attempted", "unexamined_fixtures": "unknown",
            "prediction_set": "captured_snapshot", "required_source_readiness": "blocked",
        },
        "readiness_blockers": [],
    }
    import hashlib
    import json
    target_prediction_keys = sorted((str(p.get("match_date") or "")[:10],
                                      str(p.get("player_home") or "").strip().casefold(),
                                      str(p.get("player_away") or "").strip().casefold())
                                     for p in target_predictions)
    detail_counts["prediction_set_fingerprint"] = hashlib.sha256(
        json.dumps(target_prediction_keys, separators=(",", ":")).encode()).hexdigest()
    detail_counts["prediction_set_input_rows"] = len(target_prediction_keys)
    if isinstance(listing_metrics, dict):
        recognition = listing_metrics.get("listing_recognition_status")
        if recognition in {"complete", "partial"}:
            detail_counts["stage_statuses"]["listing_recognition"] = recognition
        detail_counts["stage_statuses"]["listing_row_accounting"] = (
            "complete" if listing_metrics.get("row_accounting_complete") is True else "incomplete")
        coverage = listing_metrics.get("listing_coverage_status")
        if coverage in {"complete", "not_established", "partial"}:
            detail_counts["stage_statuses"]["listing_coverage"] = coverage
        schedule = listing_metrics.get("schedule_verification_status")
        if schedule in {"unverified", "verified", "not_required_for_excluded_rows"}:
            detail_counts["stage_statuses"]["listing_schedule_verification"] = schedule
    if listing_status != "complete":
        detail_counts["stage_statuses"]["listing_recognition"] = (
            detail_counts["stage_statuses"]["listing_recognition"] if listing_metrics else listing_status)
        detail_counts["readiness_blockers"].append("listing_not_complete")
        detail_counts["listing_status"] = listing_status
    if not target_predictions:
        for stage in ("identity_matching", "schedule_verification", "detail_discovery", "quote_availability", "quote_resolution"):
            detail_counts["stage_statuses"][stage] = "not_required"
        detail_counts["stage_statuses"]["unexamined_fixtures"] = "none"
        detail_counts["stage_statuses"]["prediction_set"] = "not_required"
        detail_counts["stage_statuses"]["required_source_readiness"] = "blocked"
        detail_counts["readiness_blockers"].append("prediction_set_unavailable")
        # An empty prediction set is not proof of a clean no-bet day.
        return [], detail_counts, True

    if detail_counts["stage_statuses"]["listing_recognition"] == "unknown" and be_rows:
        detail_counts["stage_statuses"]["listing_recognition"] = "complete"

    matched: dict[str, dict] = {}
    kickoff_states: dict[str, set[int]] = {}
    kickoff_values: dict[str, set[str]] = {}
    kickoff_unknown: set[str] = set()
    identity_conflicts: set[str] = set()
    context_conflict_predictions: list[dict] = []
    priorities: dict[str, tuple[int, str]] = {}
    now = _collection_now()

    for pred in target_predictions:
        fixture, _reversed_order, reason = _provisional_discovery_match(pred, be_rows, target_date)
        if not fixture:
            detail_counts["unmatched_or_ambiguous"] += 1
            detail_counts["identity_rejections"][reason] = detail_counts["identity_rejections"].get(reason, 0) + 1
            if reason == "participant_context_conflict":
                context_conflict_predictions.append(pred)
            continue
        eid = str(fixture.get("event_id") or "")
        fh, fa = str(fixture.get("player_home") or ""), str(fixture.get("player_away") or "")
        previous = matched.get(eid)
        if previous and (str(previous.get("match_url") or "") != str(fixture.get("match_url") or "")
                         or not (str(previous.get("player_home") or "") == fh and str(previous.get("player_away") or "") == fa)):
            identity_conflicts.add(eid)
        else:
            matched[eid] = fixture
        # Different prediction sources may publish opposite participant order;
        # the provider fixture orientation remains authoritative for later side
        # mapping, so a reversed prediction alone is not an identity conflict.
        try:
            # Unknown prediction kickoff remains unresolved but does not prevent
            # detail discovery from independently verifying the schedule.
            if str(pred.get("match_time") or "") in {"", "00:00", "n/a", "unknown"}:
                raise ValueError("prediction kickoff unavailable or fallback")
            start = datetime.strptime(f"{target_date} {pred.get('match_time')}", "%Y-%m-%d %H:%M").replace(tzinfo=SAST)
            priority = (0 if start > now else 2, start.isoformat())
            kickoff_states.setdefault(eid, set()).add(priority[0])
            kickoff_values.setdefault(eid, set()).add(start.isoformat())
        except (TypeError, ValueError):
            kickoff_unknown.add(eid)
            priority = (1, "")
        priorities[eid] = min(priorities.get(eid, priority), priority)

    for conflict_pred in context_conflict_predictions:
        ph, pa = str(conflict_pred.get("player_home") or ""), str(conflict_pred.get("player_away") or "")
        for eid, fixture in matched.items():
            fh, fa = str(fixture.get("player_home") or ""), str(fixture.get("player_away") or "")
            direct = strict_quote_team_match(ph, fh) and strict_quote_team_match(pa, fa)
            reverse = strict_quote_team_match(ph, fa) and strict_quote_team_match(pa, fh)
            if direct != reverse:
                identity_conflicts.add(eid)

    detail_counts["matched_fixtures"] = len(matched)
    detail_counts["stage_statuses"]["identity_matching"] = (
        "failed" if not matched else
        "complete" if not detail_counts["unmatched_or_ambiguous"] and not identity_conflicts else "partial")
    if identity_conflicts:
        detail_counts["ambiguous_identity_fixtures"] = len(identity_conflicts)
    if not matched:
        # An absent/failed listing means identity work was not attempted; a
        # recognized empty listing with predictions is an actual no-match.
        if not be_rows and listing_status != "complete":
            detail_counts["stage_statuses"]["identity_matching"] = "not_attempted"
        # Do not label schedule, detail, or quote stages complete when no
        # candidate was resolved and therefore no work could be attempted.
        detail_counts["stage_statuses"].update({
            "schedule_verification": "not_attempted", "detail_discovery": "not_attempted",
            "quote_availability": "not_attempted", "quote_resolution": "not_attempted",
            "unexamined_fixtures": "none",
        })
        detail_counts["stage_statuses"]["prediction_set"] = "unmatched_or_ambiguous"
        detail_counts["readiness_blockers"].append("predictions_unmatched_to_listing")
        return [], detail_counts, True

    ordered: list[str] = []
    excluded: set[str] = set()
    conflicting: set[str] = set(identity_conflicts)
    attempted_ids: set[str] = set()
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
        # Preserve the previous second-pass future kickoff check and unknown
        # duplicate protections while the bounded request queue runs.
        if priorities[eid][0] == 0 and datetime.fromisoformat(priorities[eid][1]) <= _collection_now():
            if eid not in kickoff_unknown:
                detail_counts["skipped_started_fixtures"] += 1
                excluded.add(eid)
                continue
        fixture = matched[eid]
        detail_counts["attempted"] += 1
        attempted_ids.add(eid)
        try:
            local_stages: dict[str, int] = {}
            if os.getenv("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR") and detail_counts["attempted"] <= 2:
                offers = betexplorer.fetch_fixture_comparison(fixture, refresh_evidence=True, stage_counts=local_stages)
            else:
                offers = betexplorer.fetch_fixture_comparison(fixture, stage_counts=local_stages)
            for key, value in local_stages.items():
                detail_counts[key] = detail_counts.get(key, 0) + value
            detail_offers.extend(offers)
        except Exception as exc:
            kind = exc.kind if isinstance(exc, PageFetchError) else "unexpected"
            stage = getattr(exc, "stage", "unknown")
            failure_key = f"{stage}:{kind}"
            detail_counts["failures"][failure_key] = detail_counts["failures"].get(failure_key, 0) + 1
            for key, value in local_stages.items():
                detail_counts[key] = detail_counts.get(key, 0) + value
            if kind in {"challenge", "rate_limit", "auth"}:
                break

    detail_counts["offers"] = len(detail_offers)
    from datetime import timezone
    from math import isfinite
    from racketfactory.execution import rebuild_quote_execution_problem, betexplorer_schedule_evidence_problem
    quote_verified: set[str] = set()
    schedule_candidates: set[str] = set()
    for row in detail_offers:
        eid = str(row.get("event_id") or "")
        schedule_problem = betexplorer_schedule_evidence_problem(row)
        if not schedule_problem:
            schedule_candidates.add(eid)
        try:
            observed = datetime.fromisoformat(str(row.get("observed_at") or "").replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - observed).total_seconds() if observed.tzinfo else -1
            prices = [float(row.get(key)) for key in ("odds_home", "odds_away")]
            if (not 0 <= age <= 30 * 60 or not all(isfinite(price) and price > 1 for price in prices)
                    or str(row.get("match_date") or "")[:10] != target_date
                    or str(row.get("market") or "").lower() not in {"h2h", "1x2", "match_winner", "match winner"}
                    or str(row.get("period") or "").lower() not in {"match", "full match", "full_match", "full", "0"}
                    or (row.get("line") is not None and str(row.get("line")).strip())
                    or schedule_problem or rebuild_quote_execution_problem(row)):
                continue
            quote_verified.add(eid)
        except (TypeError, ValueError, OverflowError):
            continue

    matched_ids = set(matched)
    unknown = (kickoff_unknown | conflicting) - excluded
    relevant = matched_ids - excluded - unknown
    unexamined = matched_ids - excluded - conflicting - attempted_ids
    verified_relevant = quote_verified & relevant
    unresolved = unknown | (relevant - verified_relevant)
    detail_counts["limit_exceeded"] = len(unexamined) if detail_counts["attempted"] >= betexplorer.MAX_COMPARISON_FIXTURES else 0
    detail_counts.update({
        "excluded_started_fixtures": len(excluded), "relevant_upcoming_fixtures": len(relevant),
        "unknown_or_conflicting_kickoff_fixtures": len((kickoff_unknown | (conflicting - identity_conflicts)) - excluded),
        "ambiguous_identity_fixtures": len(identity_conflicts), "unexamined_fixtures": len(unexamined),
        "unresolved_fixtures": len(unresolved), "fresh_settlement_compatible_fixtures": len(verified_relevant),
        "fresh_quote_admissible_fixtures": len(verified_relevant),
    })
    attempted = detail_counts["attempted"]
    successful = attempted - len(detail_counts["failures"])
    all_relevant_excluded = bool(matched_ids) and matched_ids <= excluded and not unknown
    if attempted == 0 and all_relevant_excluded:
        detail_status = "not_required"
    elif attempted == 0:
        detail_status = "not_attempted"
    elif not detail_counts["failures"] and successful == len(attempted_ids) and not unexamined:
        detail_status = "complete"
    elif successful == 0:
        detail_status = "failed"
    else:
        detail_status = "partial"
    if not matched_ids:
        schedule_status = quote_status = "not_attempted"
    elif all_relevant_excluded and not attempted_ids:
        schedule_status = quote_status = "not_required"
    elif not attempted_ids:
        schedule_status = quote_status = "not_attempted"
    elif relevant and relevant <= schedule_candidates and not unknown:
        schedule_status = "candidate_verified_uncontracted"
    elif schedule_candidates:
        schedule_status = "partial"
    elif unexamined:
        schedule_status = "partial"
    else:
        schedule_status = "failed"
    if all_relevant_excluded and not attempted_ids:
        quote_status = "not_required"
    elif verified_relevant == relevant and not unknown and not unexamined:
        quote_status = "complete"
    elif verified_relevant or unexamined:
        quote_status = "partial"
    elif attempted_ids:
        quote_status = "failed"
    detail_counts["stage_statuses"].update({
        "schedule_verification": schedule_status,
        "detail_discovery": detail_status,
        "quote_availability": ("offers_observed" if detail_offers else
                               "not_required" if all_relevant_excluded and not attempted_ids else
                               "no_offers_observed" if attempted_ids else "not_attempted"),
        "quote_resolution": quote_status,
        "unexamined_fixtures": "none" if not unexamined else "present",
    })
    if detail_counts["unmatched_or_ambiguous"]:
        detail_counts["stage_statuses"]["prediction_set"] = "unmatched_or_ambiguous"
    for condition, blocker in (
        (detail_counts["stage_statuses"]["identity_matching"] != "complete", "identity_matching_incomplete"),
        (schedule_status not in {"candidate_verified_uncontracted", "not_required"}, "schedule_unverified"),
        (detail_status not in {"complete", "not_required"}, "detail_discovery_incomplete"),
        (quote_status not in {"complete", "not_required"}, "quote_resolution_incomplete"),
    ):
        if condition:
            detail_counts["readiness_blockers"].append(blocker)
    if detail_counts["failures"] or detail_counts["unmatched_or_ambiguous"] or unresolved or unexamined:
        partial = True
    source_ready = (
        listing_status == "complete"
        and detail_counts["stage_statuses"]["listing_recognition"] in {"complete", "unknown"}
        and detail_counts["stage_statuses"]["listing_row_accounting"] in {"complete", "unknown"}
        and detail_counts["stage_statuses"]["listing_coverage"] in {"complete", "unknown"}
        and detail_counts["stage_statuses"]["listing_schedule_verification"] in {"verified", "not_required_for_excluded_rows", "unknown"}
        and detail_counts["stage_statuses"]["identity_matching"] in {"complete", "not_applicable"}
        and detail_counts["stage_statuses"]["schedule_verification"] in {"complete", "candidate_verified_uncontracted", "not_required"}
        and detail_counts["stage_statuses"]["detail_discovery"] in {"complete", "not_required"}
        and detail_counts["stage_statuses"]["quote_resolution"] in {"complete", "not_required"}
        and not detail_counts["readiness_blockers"]
        and detail_counts["stage_statuses"]["unexamined_fixtures"] in {"none", "unknown"}
    )
    detail_counts["stage_statuses"]["required_source_readiness"] = "ready" if source_ready else "blocked"
    if not source_ready:
        partial = True
    return detail_offers, detail_counts, partial

def targeted_comparison_rows(target_date: str, predictions: list[dict]) -> list[dict]:
    """Use the guarded detail path during normal warehouse collection too.

    Identified-run attempts are bounded independently of the positive-result
    cache: failures remain failures, and later builds do not recrawl the source.
    Quote timestamps remain the original provider observation times.
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
        if os.getenv(betexplorer.DISABLE_ENV, '').strip().lower() in {'1','true','yes','on'}:
            return [{'status': 'disabled', 'counts': {}, 'offers': []}]
        fixtures, status = betexplorer._fetch_live(target_date, with_status=True, metrics=metrics)
        offers, counts, partial = collect_prediction_comparisons(target_date, predictions, fixtures, status,
                                                                    listing_metrics=metrics)
        if predictions and not fixtures:
            partial = True
        return [{'status': 'partial' if partial else status, 'listing_rows': len(fixtures),
                 'listing_counts': metrics, 'counts': counts, 'offers': offers}]

    # The run-scoped attempt marker is not a positive-result cache or a
    # provider quota authority. It prevents retries by later processes in this
    # job even if a fetch, cache write or process crashes. It cannot coordinate
    # separate runners. Old positive-cache failures remain failures without a
    # re-fetch; no cache or cooldown is deleted.
    collected_live = False
    def failure(exc):
        kind = getattr(exc, 'kind', 'unexpected')
        if not isinstance(kind, str) or kind not in {'transport', 'http', 'auth', 'challenge', 'rate_limit', 'parser'}:
            kind = 'unexpected'
        result = {'status': 'failed', 'error_type': type(exc).__name__,
                  'failure_kind': kind, 'offers': [], 'prediction_fingerprint': fingerprint}
        if kind == 'rate_limit':
            from racketfactory.quota_guard import cooldown_status
            cooldown = cooldown_status('betexplorer', 'host:www.betexplorer.com')
            if cooldown and cooldown.get('kind') == 'rate_limit':
                result['cooldown_state'] = 'active_in_current_runner'
                result['cooldown_until'] = cooldown.get('until')
            else:
                result['cooldown_state'] = 'not_established_in_current_runner'
        return result

    if run:
        import fcntl
        import tempfile
        from racketfactory.fetch_cache import cache_path
        marker_path = cache_path('targeted_attempt_' + key, fetch_day=target_date)
        lock_path = marker_path.with_suffix('.lock')
        def write_marker(payload):
            tmp = None
            try:
                with tempfile.NamedTemporaryFile('w', dir=marker_path.parent, delete=False, encoding='utf-8') as stream:
                    tmp = Path(stream.name)
                    json.dump(payload, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, marker_path)
                fd = os.open(marker_path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                if tmp:
                    tmp.unlink(missing_ok=True)
        try:
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open('a') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                marker = None
                if marker_path.exists():
                    marker = json.loads(marker_path.read_text(encoding='utf-8'))
                    if (not isinstance(marker, dict) or marker.get('version') != 1
                            or marker.get('key') != key or marker.get('state') not in {'started', 'failed', 'completed'}
                            or not isinstance(marker.get('prediction_fingerprint'), str)
                            or not re.fullmatch(r'[0-9a-f]{64}', marker['prediction_fingerprint'])
                            or (marker.get('state') == 'failed' and
                                (marker.get('failure_kind') not in {'transport', 'http', 'auth', 'challenge', 'rate_limit', 'parser', 'unexpected'}
                                 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', str(marker.get('error_type') or ''))))):
                        raise ValueError('invalid attempt marker')
                if marker and marker['state'] in {'started', 'failed'}:
                    result = {'status': 'failed',
                              'error_type': marker['error_type'] if marker['state'] == 'failed' else 'PriorAttemptIncomplete',
                              'failure_kind': marker['failure_kind'] if marker['state'] == 'failed' else 'attempt_incomplete', 'offers': [],
                              'prediction_fingerprint': marker['prediction_fingerprint'],
                              'attempt_guard': 'previous_attempt'}
                    if result['failure_kind'] == 'rate_limit':
                        from racketfactory.quota_guard import cooldown_status
                        cooldown = cooldown_status('betexplorer', 'host:www.betexplorer.com')
                        if cooldown and cooldown.get('kind') == 'rate_limit':
                            result['cooldown_state'] = 'active_in_current_runner'
                            result['cooldown_until'] = cooldown.get('until')
                        else:
                            result['cooldown_state'] = 'not_established_in_current_runner'
                else:
                    def collect_for_run():
                        nonlocal collected_live
                        # A prior completed attempt with a missing/corrupt cache
                        # must not create a second request sequence either.
                        if marker:
                            raise RuntimeError('prior attempt result unavailable')
                        write_marker({'version': 1, 'key': key, 'state': 'started',
                                      'prediction_fingerprint': fingerprint})
                        collected_live = True
                        rows = collect()  # exceptions never reach the positive cache
                        rows[0]['prediction_fingerprint'] = fingerprint
                        return rows
                    try:
                        cached = cached_fetch('targeted_quotes_' + key, collect_for_run,
                                              fetch_day=target_date, ttl_minutes=24 * 60)
                        if (not isinstance(cached, list) or len(cached) != 1 or not isinstance(cached[0], dict)
                                or cached[0].get('status') not in {'complete', 'partial', 'failed', 'disabled', 'unknown'}
                                or not isinstance(cached[0].get('offers'), list)):
                            raise ValueError('invalid targeted collection cache')
                        result = cached[0]
                        if collected_live:
                            write_marker({'version': 1, 'key': key, 'state': 'completed',
                                          'prediction_fingerprint': fingerprint})
                    except Exception as exc:
                        result = failure(exc)
                        # The started marker already blocks retry if this write fails.
                        if collected_live:
                            write_marker({'version': 1, 'key': key, 'state': 'failed',
                                          'prediction_fingerprint': fingerprint,
                                          'error_type': result['error_type'], 'failure_kind': result['failure_kind']})
                        elif marker:
                            result['failure_kind'] = 'prior_result_unavailable'
                            result['prediction_fingerprint'] = marker['prediction_fingerprint']
                        else:
                            result['failure_kind'] = 'attempt_guard_unavailable'
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            # Missing/unwritable/corrupt local attempt state is not permission
            # to run the request sequence again.
            result = failure(exc)
            result['failure_kind'] = 'attempt_guard_unavailable'
    else:
        # Without a run identity there is no safe cross-process same-run key.
        try:
            collected_live = True
            result = collect()[0]
            result['prediction_fingerprint'] = fingerprint
        except Exception as exc:
            result = failure(exc)
    # Cache reuse is allowed only when the complete cached snapshot carries
    # independently revalidated, still-fresh quote evidence and exactly the
    # same prediction set. Never stamp a cached observation as new.
    def cached_snapshot_is_proven() -> bool:
        if (not isinstance(result, dict) or result.get('status') not in {'complete', 'partial'}
                or result.get('prediction_fingerprint') != fingerprint
                or not isinstance(result.get('offers'), list) or not result.get('offers')):
            return False
        for offer in result['offers']:
            if not isinstance(offer, dict) or rebuild_quote_execution_problem(offer):
                return False
            try:
                observed = datetime.fromisoformat(str(offer.get('observed_at') or '').replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - observed).total_seconds()
                prices = [float(offer.get(key)) for key in ('odds_home', 'odds_away')]
                if (observed.tzinfo is None or observed.utcoffset() is None or not 0 <= age <= 30 * 60
                        or str(offer.get('match_date') or '')[:10] != target_date
                        or not all(math.isfinite(price) and price > 1 for price in prices)
                        or not offer.get('event_id') or not offer.get('player_home') or not offer.get('player_away')):
                    return False
            except (TypeError, ValueError, OverflowError):
                return False
        return True

    cached_without_proof = (not collected_live and bool(result.get('offers') or result.get('listing_rows'))
                            and not cached_snapshot_is_proven())
    if cached_without_proof:
        if result.get('status') not in {'failed', 'disabled'}:
            result['status'] = 'partial'
        result['listing_schedule_status'] = 'unverified'
        result['schedule_guard_reason'] = 'cached_schedule_evidence_unavailable'
        result['offers'] = []
    result['prediction_set_status'] = ('changed' if result.get('prediction_fingerprint') != fingerprint
                                       else 'captured' if collected_live else 'unchanged')
    if result.get('prediction_fingerprint') != fingerprint:
        # Do not silently treat a different fixture set as covered or exceed
        # the per-run detail request budget by recrawling it.
        if result.get('status') == 'complete':
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
    status['observed_offer_count'] = len(result.get('offers', []))
    status['listing_row_count'] = int(result.get('listing_rows') or 0)
    status['admission_policy_version'] = 'racket-new-card-v1'
    status['indicative_observations_are_not_bookmaker_offers_or_fills'] = True
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
    # Invalid/stale BetExplorer offers must not occupy a fixture key and hide
    # an independently valid OddsPortal indicative row in the merge. Keep only
    # current, dated, schedule-bound offers for this routing decision; missing
    # documentary settlement terms are not a rejection under forward-only PR#36.
    current_offers = []
    for row in result.get('offers', []):
        try:
            observed = datetime.fromisoformat(str(row.get('observed_at') or '').replace('Z', '+00:00'))
            if observed.tzinfo is None or observed.utcoffset() is None:
                continue
            age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
            prices = [float(row.get(key)) for key in ('odds_home', 'odds_away')]
            if (not 0 <= age <= 30 * 60
                    or str(row.get('match_date') or '')[:10] != target_date
                    or not all(math.isfinite(price) and price > 1 for price in prices)
                    or not row.get('event_id') or not row.get('player_home') or not row.get('player_away')
                    or rebuild_quote_execution_problem(row)):
                continue
            current_offers.append(row)
        except (TypeError, ValueError, OverflowError):
            continue
    # Multiple current rows for one fixture are not multiple identities.
    # Prefer a valid named bookmaker deterministically, never the highest price.
    chosen = {}
    for row in sorted(current_offers, key=lambda r: (str(r.get('event_id') or ''),
                       str(r.get('bookmaker') or '').casefold(), str(r.get('bookmaker_id') or ''))):
        key = (str(row.get('event_id') or ''), str(row.get('match_date') or ''),
               str(row.get('player_home') or ''), str(row.get('player_away') or ''))
        chosen.setdefault(key, row)
    try:
        optional = oddsportal_upcoming.fetch_oddsportal_upcoming_rows(target_date)
    except Exception as exc:
        log.warning('OddsPortal comparison unavailable: %s', type(exc).__name__)
        optional = []
    return merge_comparison_rows(list(chosen.values()), optional)
