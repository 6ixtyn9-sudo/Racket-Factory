"""Single source of truth for "may this leg move the real bank?".

WHY THIS MODULE EXISTS
----------------------
The ticket builder (``scripts/auto_tickets.py``) and the grader
(``scripts/auto_tickets_grade.py``) used to answer that question with two
independent, *different* rules:

    builder  : paper if (no trusted market price) OR (_late_start)
    grader   : paper if (odds_source blank) OR (odds_reject_reason)

The grader never looked at ``_late_start``/``_is_paper``, so a leg that was
late at generation time but carried a BetExplorer price was PAPER to the
builder and REAL to the grader. Observed live on the 2026-09-20 open slip
(both legs late + BetExplorer priced, acca ``paper: true``, ``odds: null``,
``stake_pct: 25.0``). Consequence, traced branch by branch through
``settle_open_slips``:

    both legs win  -> acca_odds is None -> "never pays on fiction" -> the
                      slip is held open FOREVER (it had sat open 6 days)
    one leg loses  -> booked as a loss

i.e. the disagreement was strictly one-directional: a mismatched acca could
be booked as a LOSS but never as a WIN.

The fix is not "make the grader match the builder" — that just re-creates
the same class of bug next time someone edits one side. Both sides now call
the functions below, and the verdict is *stamped onto the leg at build time*
so a later grading run can detect drift instead of silently disagreeing.

FAIL-CLOSED CONTRACT
--------------------
Every rule here answers "can we PROVE this leg is executable at the recorded
price?". Anything unproven is blocked (paper track: hit-rate only, never
touches the bank). Blocking is cheap; a fabricated bank curve is not.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

# Only the trusted price categories below can move the real bank. Legacy
# ScrapedFallback, ML estimates and blanks remain paper-only; ScrapedPickOdds
# is a separate source-published category gated by strict provenance checks.
# BetExplorer legs are indicative consensus, not a confirmed single-book fill.
TRUSTED_MARKET_SOURCES = frozenset({"theoddsapi", "oddsportal", "bzzoiro", "betexplorer", "scrapedpickodds"})

# Stamped onto every leg the builder emits, so the grader can cross-check.
STAMP_SAFE = "_execution_safe"
STAMP_BLOCK = "_execution_block"

# Reason strings are part of the ticket .txt output contract; keep them stable.
BLOCK_LATE = "already started at generation time (stale price)"
BLOCK_NO_PRICE = "no trusted market price"
BLOCK_KICKOFF_UNKNOWN = "kickoff unknown (cannot prove match had not started)"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>", "nat", "null"}:
        return ""
    return text


def leg_market_odds(leg: Mapping[str, Any]) -> float | None:
    """The tradable market price for a leg, or None when there isn't one.

    NEVER estimated. A leg without a trusted, finite, >1.0 market price goes
    to the paper track instead of being staked on a fabricated number.
    """
    if _clean(leg.get("odds_reject_reason")):
        return None
    if leg.get("_is_paper"):
        return None
    source = _clean(leg.get("odds_source")).lower()
    if source not in TRUSTED_MARKET_SOURCES:
        return None
    if source == "scrapedpickodds":
        quote = leg.get("selected_quote")
        if not isinstance(quote, Mapping):
            return None
        try:
            if rebuild_quote_execution_problem(quote, pick=leg):
                return None
        except Exception:
            return None
    try:
        odds = float(leg.get("odds"))
    except (TypeError, ValueError):
        return None
    # NaN fails every comparison, inf is not a real price.
    if not (odds > 1.0) or odds == float("inf"):
        return None
    return odds


def leg_execution_block(leg: Mapping[str, Any]) -> str:
    """"" when the leg may be staked, else the human reason it may not.

    Order is chosen for message quality, not logic: a late leg that is also
    unpriced reads better as "already started".
    """
    if leg.get("_late_start"):
        return BLOCK_LATE
    if leg.get("_kickoff_unknown"):
        return BLOCK_KICKOFF_UNKNOWN
    # _is_paper is deliberately NOT a separate branch: leg_market_odds()
    # already rejects it, so such legs report "no trusted market price" —
    # the wording the ticket .txt has always used for them.
    if leg_market_odds(leg) is None:
        return BLOCK_NO_PRICE
    return ""


def leg_execution_safe(leg: Mapping[str, Any]) -> bool:
    return leg_execution_block(leg) == ""


def stamp_leg(leg: dict) -> dict:
    """Record the build-time verdict on the leg (mutates and returns it)."""
    block = leg_execution_block(leg)
    leg[STAMP_SAFE] = block == ""
    leg[STAMP_BLOCK] = block
    # Legacy field kept so existing ticket .txt rendering and any downstream
    # reader that predates the stamp keeps working. Only written when there
    # IS a reason: an executable leg carries no paper reason, same as before.
    if block:
        leg["_paper_reason"] = block
    else:
        leg.pop("_paper_reason", None)
    return leg


def stamp_acca(acca: dict) -> dict:
    """Tag an acca with the execution regime that built it.

    Acca-level P&L is only comparable within one execution regime: the
    2026-09-26 boundary closed the ungated fallback, moved staking to
    free-bank sizing at 0.20 and unified the builder/grader paper split.
    Untagged accas pre-date the boundary.
    """
    from racketfactory.regime import EXECUTION_REGIME_ID
    acca["_exec_regime"] = EXECUTION_REGIME_ID
    return acca


def leg_settlement_safe(leg: Mapping[str, Any]) -> bool:
    """Grader-side verdict: AND of the build-time stamp and a fresh recompute.

    Taking the AND (rather than trusting either side) means the two can only
    ever disagree in the safe direction. A leg stamped safe that no longer
    recomputes as safe — the exact drift this module exists to prevent — is
    demoted to paper rather than silently staked.
    """
    if not leg_execution_safe(leg):
        return False
    if STAMP_SAFE in leg and not leg.get(STAMP_SAFE):
        return False
    return True


def acca_execution_safe(acca: Mapping[str, Any]) -> bool:
    """An acca is real only if every leg is real AND nobody flagged it paper.

    ``acca["paper"]`` is the builder's own verdict and is honoured as a hard
    floor. The grader used to ignore it entirely and recompute from legs,
    which is how a slip the builder had explicitly marked ``paper: true``
    ended up on the real-money branch.
    """
    if acca.get("paper") is True:
        return False
    legs = acca.get("legs") or []
    if not legs:
        return False
    return all(leg_settlement_safe(leg) for leg in legs)


def acca_block_reasons(acca: Mapping[str, Any]) -> list[str]:
    """Distinct reasons this acca cannot be staked (empty == executable)."""
    reasons: list[str] = []
    if acca.get("paper") is True:
        reasons.append("acca flagged paper by builder")
    for leg in acca.get("legs") or []:
        block = leg_execution_block(leg)
        if not block and STAMP_SAFE in leg and not leg.get(STAMP_SAFE):
            block = _clean(leg.get(STAMP_BLOCK)) or "stamped unsafe at build time"
        if block and block not in reasons:
            reasons.append(block)
    if not (acca.get("legs") or []):
        reasons.append("acca has no legs")
    return reasons


def committed_stake(open_slips: Iterable[Mapping[str, Any]]) -> float:
    """Real bank already committed to unsettled slips, in bank-%.

    Paper accas commit nothing. Used to size new bets off FREE bank rather
    than total bank: ``state["bank"]`` only moves at settlement, so with
    slips open the old sizing was staking capital that was already at risk.
    """
    total = 0.0
    for slip in open_slips or []:
        if not isinstance(slip, Mapping):
            continue
        for acca in slip.get("accas") or []:
            if not isinstance(acca, Mapping):
                continue
            if not acca_execution_safe(acca):
                continue
            try:
                total += float(acca.get("stake_pct") or 0.0)
            except (TypeError, ValueError):
                continue
    return round(total, 4)


def sanitise_acca_for_replay(acca: Mapping[str, Any]) -> dict:
    """Copy of an acca safe to re-import into ``open_slips``.

    Historical ticket ledgers are replayed back into state when a slip went
    missing. The loop used to re-import them verbatim, which carried paper
    accas back in still holding their *real* ``stake_pct`` (2026-09-20 came
    back with 25.0). Anything not provably executable is re-imported as
    paper with the stake zeroed: it still settles for hit-rate, it can never
    move the bank.
    """
    out = dict(acca)
    if acca_execution_safe(acca):
        return out
    was_paper = acca.get("paper") is True
    out["paper"] = True
    out["_replay_block"] = acca_block_reasons(acca)
    if not was_paper:
        # The ledger claimed this was a real bet and it is not executable, so
        # its stake_pct is fiction: zero it. An acca the ledger ALREADY marked
        # paper keeps its number, which is a paper-track notional and is what
        # the paper bank is supposed to be accounted against.
        out["stake_pct"] = 0.0
    return out


def settlement_rules_problem(rules: object) -> str:
    """Validate the narrow rule grammar consumed by new-ticket grading.

    This checks structure only. It does not authenticate the terms or bind
    them to a bookmaker, product, jurisdiction, market or quote.
    """
    if not isinstance(rules, Mapping):
        return "bookmaker settlement rules unknown or incompatible"
    required_rules = {"retirement", "disqualification", "walkover", "cancelled"}
    if set(rules) != required_rules:
        return "bookmaker settlement rules incomplete or contain unknown statuses"

    def valid_rule(status: str, rule: object) -> bool:
        if not isinstance(rule, Mapping):
            return False
        value = dict(rule)
        if status == "walkover":
            return value == {"action": "void"}
        if status == "cancelled":
            return value == {"action": "void"} or (
                set(value) == {"action", "hours", "otherwise"}
                and value.get("action") == "void_after_scheduled_hours"
                and type(value.get("hours")) is int
                and 1 <= value["hours"] <= 168
                and value.get("otherwise") == "pending"
            )
        if value == {"action": "void"}:
            return True
        if value == {"action": "settle_winner"}:
            return True
        return (
            set(value) == {"action", "minimum_completed_sets", "otherwise"}
            and value.get("action") == "settle_winner_if_completed_sets"
            and type(value.get("minimum_completed_sets")) is int
            and 1 <= value["minimum_completed_sets"] <= 5
            and value.get("otherwise") == "void"
        )

    if any(not valid_rule(status, rules.get(status)) for status in required_rules):
        return "bookmaker settlement rules unknown or incompatible"
    return ""


def settlement_evidence_problem(quote: Mapping[str, Any]) -> str:
    """Validate the binding of bookmaker terms evidence to one quote.

    This is a structural/provenance check, not authentication of an external
    legal document. Live acceptance still requires the retained official terms
    document to be reviewed and its jurisdiction/effective scope established.
    """
    from datetime import date, datetime
    from urllib.parse import urlsplit
    import re

    evidence = quote.get("settlement_evidence")
    if not isinstance(evidence, Mapping):
        return "bookmaker settlement rules evidence missing"
    if evidence.get("test_only") is True:
        return "synthetic settlement evidence is not provider proof"
    bookmaker = str(quote.get("bookmaker") or "").strip()
    bookmaker_id = str(quote.get("bookmaker_id") or "").strip()
    if not bookmaker or str(evidence.get("bookmaker") or "").strip().casefold() != bookmaker.casefold():
        return "settlement evidence bookmaker mismatch"
    evidence_id = str(evidence.get("bookmaker_id") or "").strip()
    if bookmaker_id and evidence_id != bookmaker_id:
        return "settlement evidence bookmaker id mismatch"
    if not bookmaker_id and not evidence_id:
        return "settlement evidence bookmaker id missing"
    if str(evidence.get("document_kind") or "") != "official_bookmaker_terms":
        return "settlement evidence source is not official bookmaker terms"
    if evidence.get("evidence_status") != "verified_official_terms":
        return "official bookmaker settlement terms have not been authenticated"
    if not str(evidence.get("reviewed_by") or "").strip():
        return "bookmaker settlement terms review provenance missing"
    url = str(evidence.get("document_url") or "")
    official_domain = str(evidence.get("official_domain") or "").strip().casefold().lstrip(".")
    try:
        parsed_url = urlsplit(url)
        host = (parsed_url.hostname or "").casefold().rstrip(".")
        domain_valid = bool(re.fullmatch(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", official_domain))
        if (parsed_url.scheme != "https" or not host or parsed_url.username or parsed_url.password
                or host.endswith(".invalid") or not domain_valid
                or not (host == official_domain or host.endswith("." + official_domain))):
            return "settlement evidence document URL or official domain binding invalid"
    except ValueError:
        return "settlement evidence document URL invalid"
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("document_sha256") or "")):
        return "settlement evidence document digest missing"
    jurisdiction = str(evidence.get("jurisdiction") or "").strip().casefold()
    operator_jurisdiction = str(quote.get("operator_jurisdiction") or "").strip().casefold()
    if not jurisdiction or not operator_jurisdiction or jurisdiction != operator_jurisdiction:
        return "settlement evidence jurisdiction unknown or mismatched"
    if str(evidence.get("market_scope") or "") != "tennis_match_winner":
        return "settlement evidence market scope mismatch"
    market = str(quote.get("market") or "").strip().casefold()
    period = str(quote.get("period") or "").strip().casefold()
    if market not in {"h2h", "1x2", "match_winner", "match winner"}:
        return "settlement evidence quote market is missing or incompatible"
    if period not in {"match", "full", "full_match", "full match", "0"}:
        return "settlement evidence quote period is missing or incompatible"
    if quote.get("line") is not None and str(quote.get("line")).strip():
        return "settlement evidence quote has an incompatible line"
    try:
        retrieved = datetime.fromisoformat(str(evidence.get("retrieved_at") or "").replace("Z", "+00:00"))
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            return "settlement evidence retrieval timezone unknown"
        effective_from = date.fromisoformat(str(evidence.get("effective_from") or ""))
        effective_until_raw = str(evidence.get("effective_until") or "").strip()
        effective_until = date.fromisoformat(effective_until_raw) if effective_until_raw else None
        reviewed = datetime.fromisoformat(str(evidence.get("reviewed_at") or "").replace("Z", "+00:00"))
        if reviewed.tzinfo is None or reviewed.utcoffset() is None:
            return "settlement evidence review timezone unknown"
        observed = datetime.fromisoformat(str(quote.get("observed_at") or "").replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return "settlement evidence quote observation timezone unknown"
        observed_date = observed.date()
        if effective_from > observed_date or (effective_until and observed_date > effective_until):
            return "settlement evidence not effective at quote observation"
        if retrieved.astimezone(observed.tzinfo) > observed:
            return "settlement evidence retrieved after quote"
        if reviewed.astimezone(observed.tzinfo) > observed or reviewed.astimezone(retrieved.tzinfo) < retrieved:
            return "settlement evidence review timestamp is out of order"
    except (TypeError, ValueError, OverflowError):
        return "settlement evidence dates invalid"
    rules = evidence.get("rules")
    rules_problem = settlement_rules_problem(rules)
    if rules_problem:
        return rules_problem
    if rules["cancelled"].get("action") == "void_after_scheduled_hours":
        try:
            scheduled = datetime.fromisoformat(str(quote.get("commence_time") or "").replace("Z", "+00:00"))
            if scheduled.tzinfo is None or scheduled.utcoffset() is None:
                return "cancellation settlement timeout requires an offset-bearing scheduled start"
        except (TypeError, ValueError, OverflowError):
            return "cancellation settlement timeout scheduled start invalid"
    quote_rules = quote.get("settlement_rules")
    if not isinstance(quote_rules, Mapping) or dict(quote_rules) != dict(rules):
        return "settlement evidence rules not bound to quote"
    return ""



def betexplorer_schedule_evidence_problem(quote: Mapping[str, Any], *, now=None) -> str:
    """Check fixture-bound schedule observations without claiming contract proof.

    Forward admission accepts the current provider-verified form or the
    explicitly labelled detail-page candidate when its identity, timing,
    freshness and status checks pass. The candidate remains unverified as a
    provider-wide schedule contract.
    """
    from datetime import datetime, timezone
    import re
    from racketfactory.kickoff import SAST

    evidence = quote.get("schedule_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("status") not in {
            "provider_verified", "candidate_unverified_contract"}:
        return "BetExplorer schedule evidence missing or unsupported"
    if evidence.get("test_only") is True:
        return "synthetic BetExplorer schedule evidence is not provider proof"
    if (evidence.get("provider") != "BetExplorer" or evidence.get("sport") != "Tennis"
            or evidence.get("event_status") not in {"EventScheduled", "https://schema.org/EventScheduled"}):
        return "BetExplorer schedule evidence event state mismatch"
    if evidence.get("event_id") != quote.get("event_id") or evidence.get("match_url") != quote.get("match_url"):
        return "BetExplorer schedule evidence event identity mismatch"
    for key, fallback in (("player_home", "provider_home"), ("player_away", "provider_away")):
        if str(evidence.get(key) or "").strip() != str(quote.get(key) or quote.get(fallback) or "").strip():
            return "BetExplorer schedule evidence participant mismatch"
    if evidence.get("contradictory_signals") != []:
        return "BetExplorer schedule evidence has conflicting or unexamined status signals"
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("response_sha256") or "")):
        return "BetExplorer schedule evidence response digest missing"
    try:
        observed = datetime.fromisoformat(str(evidence.get("observed_at") or "").replace("Z", "+00:00"))
        quote_observed = datetime.fromisoformat(str(quote.get("observed_at") or "").replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(evidence.get("commence_time") or "").replace("Z", "+00:00"))
        quote_start = datetime.fromisoformat(str(quote.get("commence_time") or "").replace("Z", "+00:00"))
        if any(value.tzinfo is None or value.utcoffset() is None for value in (observed, quote_observed, start, quote_start)):
            return "BetExplorer schedule evidence timezone unknown"
        if start.astimezone(timezone.utc) != quote_start.astimezone(timezone.utc):
            return "BetExplorer schedule evidence kickoff does not match quote"
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            return "BetExplorer schedule evidence validation clock is naive"
        if not 0 <= (clock.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() <= 30 * 60:
            return "BetExplorer schedule evidence stale or future"
        if observed.astimezone(timezone.utc) > quote_observed.astimezone(timezone.utc):
            return "BetExplorer schedule evidence postdates quote"
        if start.astimezone(timezone.utc) <= clock.astimezone(timezone.utc):
            return "BetExplorer event has started"
        if start.astimezone(SAST).date().isoformat() != str(quote.get("match_date") or "")[:10]:
            return "BetExplorer schedule evidence date mismatch"
    except (TypeError, ValueError, OverflowError):
        return "BetExplorer schedule evidence timestamps invalid"
    return ""

def oddsportal_schedule_evidence_problem(quote: Mapping[str, Any], *, now=None) -> str:
    """Validate an explicit OddsPortal match-page schedule candidate.

    The match-page JSON-LD can establish a dated event candidate for this
    quote, but it does not establish an OddsPortal-wide schedule contract.
    Listing-page dates alone never satisfy this check.
    """
    from datetime import datetime, timezone
    import re
    from urllib.parse import urlsplit
    from racketfactory.kickoff import SAST

    evidence = quote.get("schedule_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("status") != "candidate_unverified_contract":
        return "OddsPortal match-page schedule evidence missing or unsupported"
    if evidence.get("test_only") is True:
        return "synthetic OddsPortal schedule evidence is not provider proof"
    if (evidence.get("provider") != "OddsPortal" or evidence.get("sport") != "Tennis"
            or evidence.get("event_status") != "EventScheduled"):
        return "OddsPortal schedule evidence event state mismatch"
    if (evidence.get("event_id") != quote.get("event_id")
            or evidence.get("match_url") != quote.get("match_url")):
        return "OddsPortal schedule evidence event identity mismatch"
    path = urlsplit(str(quote.get("match_url") or ""))
    path_id = path.path.rstrip("/").rsplit("/", 1)[-1]
    if path.scheme or path.netloc or not path.path.startswith("/tennis/") or path_id != str(quote.get("event_id") or ""):
        return "OddsPortal public match path and event ID mismatch"
    for key, fallback in (("player_home", "provider_home"), ("player_away", "provider_away")):
        if str(evidence.get(key) or "").strip() != str(quote.get(key) or quote.get(fallback) or "").strip():
            return "OddsPortal schedule evidence participant mismatch"
    if evidence.get("contradictory_signals") != []:
        return "OddsPortal schedule evidence has conflicting or unexamined status signals"
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("response_sha256") or "")):
        return "OddsPortal schedule evidence response digest missing"
    try:
        observed = datetime.fromisoformat(str(evidence.get("observed_at") or "").replace("Z", "+00:00"))
        quote_observed = datetime.fromisoformat(str(quote.get("observed_at") or "").replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(evidence.get("commence_time") or "").replace("Z", "+00:00"))
        quote_start = datetime.fromisoformat(str(quote.get("commence_time") or "").replace("Z", "+00:00"))
        if any(value.tzinfo is None or value.utcoffset() is None for value in (observed, quote_observed, start, quote_start)):
            return "OddsPortal schedule evidence timezone unknown"
        if start.astimezone(timezone.utc) != quote_start.astimezone(timezone.utc):
            return "OddsPortal schedule evidence kickoff does not match quote"
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            return "OddsPortal schedule evidence validation clock is naive"
        if not 0 <= (clock.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() <= 30 * 60:
            return "OddsPortal schedule evidence stale or future"
        if observed.astimezone(timezone.utc) != quote_observed.astimezone(timezone.utc):
            return "OddsPortal schedule and quote observations are not the same retrieval"
        if start.astimezone(timezone.utc) <= clock.astimezone(timezone.utc):
            return "OddsPortal event has started"
        local_start = start.astimezone(SAST)
        if local_start.date().isoformat() != str(quote.get("match_date") or "")[:10]:
            return "OddsPortal schedule evidence date mismatch"
        if local_start.strftime("%H:%M") != str(quote.get("match_time") or "")[:5]:
            return "OddsPortal schedule evidence time mismatch"
    except (TypeError, ValueError, OverflowError):
        return "OddsPortal schedule evidence timestamps invalid"
    return ""


def scraped_pick_odds_execution_problem(quote: Mapping[str, Any], *, now=None) -> str:
    """Fail-closed checks for provenance-bearing source-pick odds.

    These are source-published prices, not bookmaker offers or confirmed fills.
    Admission is limited to a recent BetClan match-page observation whose
    odds container binds exactly two prices to the page's named home/away
    participants. Best-effort global/table guesses remain paper-only.
    """
    from datetime import datetime, timezone
    from urllib.parse import urlsplit
    import re

    if str(quote.get("price_type") or "").strip().casefold() != "scraped_pick_odds":
        return "scraped quote price type missing or incompatible"
    if str(quote.get("market") or "").strip().casefold() != "source_pick_odds":
        return "scraped quote market missing or incompatible"
    if str(quote.get("period") or "").strip().casefold() not in {"match", "full", "full_match", "full match", "0"}:
        return "scraped quote period missing or incompatible"
    if str(quote.get("bookmaker") or "").strip():
        return "scraped pick odds cannot claim a bookmaker"

    origin = str(quote.get("origin_source") or "").strip()
    kind = str(quote.get("evidence_kind") or "").strip()
    if origin != "BetClan" or kind not in {"betclan_match_detail", "betclan_odds_subpage"}:
        return "scraped quote source evidence unsupported"
    if quote.get("market_evidence") != "betclan_named_home_away_odds":
        return "scraped quote market evidence missing or incompatible"
    if quote.get("kickoff_evidence") != "betclan_event_timestamp":
        return "scraped quote kickoff evidence missing or incompatible"
    if not re.fullmatch(r"[0-9a-f]{64}", str(quote.get("response_sha256") or "")):
        return "scraped quote response digest missing or invalid"

    def checked_url(value):
        try:
            parsed = urlsplit(str(value or ""))
            return (parsed.scheme == "https" and parsed.hostname == "www.betclan.com" and parsed.port in (None, 443)
                    and not parsed.username and not parsed.password and not parsed.query
                    and not parsed.fragment and bool(parsed.path))
        except (TypeError, ValueError):
            return False

    source_url = str(quote.get("source_url") or "")
    event_url = str(quote.get("event_url") or "")
    if not checked_url(source_url) or not checked_url(event_url):
        return "scraped quote source URL invalid"
    source_path, event_path = urlsplit(source_url).path, urlsplit(event_url).path
    event_match = re.fullmatch(r"/tennis/predictionsdetails/([A-Za-z0-9_-]+)/?", event_path)
    if not event_match:
        return "scraped quote event URL mismatch"
    event_slug = event_match.group(1)
    if kind == "betclan_match_detail":
        if source_path.rstrip("/") != event_path.rstrip("/"):
            return "scraped quote event URL mismatch"
    else:
        source_parts = source_path.strip("/").split("/")
        if (len(source_parts) < 3 or source_parts[-1] != event_slug
                or not any(token in "/".join(source_parts[1:-1]).casefold()
                           for token in ("odds", "bookmaker", "market", "prices"))):
            return "scraped quote event URL mismatch"

    fixture_date = str(quote.get("fixture_date") or "")[:10]
    match_time = str(quote.get("match_time") or "")[:5]
    try:
        from datetime import date
        date.fromisoformat(fixture_date)
    except (TypeError, ValueError):
        return "scraped quote fixture date missing or invalid"
    if not re.fullmatch(r"\d{2}:\d{2}", match_time):
        return "scraped quote kickoff missing or invalid"

    home, away = str(quote.get("provider_home") or "").strip(), str(quote.get("provider_away") or "").strip()
    if not home or not away or home.casefold() == away.casefold():
        return "scraped quote provider orientation missing"
    if quote.get("reversed") is True:
        return "scraped quote provider orientation changed"

    try:
        from racketfactory.warehouse import strict_quote_team_match
        source_home = str(quote.get("source_player_home") or "").strip()
        source_away = str(quote.get("source_player_away") or "").strip()
        source_reversed = quote.get("source_reversed")
        if type(source_reversed) is not bool or not source_home or not source_away:
            return "scraped quote source orientation missing"
        expected_source_home, expected_source_away = (away, home) if source_reversed else (home, away)
        if (not strict_quote_team_match(source_home, expected_source_home)
                or not strict_quote_team_match(source_away, expected_source_away)):
            return "scraped quote source orientation mismatch"
    except (ImportError, TypeError, ValueError):
        return "scraped quote source orientation invalid"

    try:
        from math import isfinite
        home_odds, away_odds = float(quote.get("odds_home")), float(quote.get("odds_away"))
        source_home_odds = float(quote.get("source_odds_home"))
        source_away_odds = float(quote.get("source_odds_away"))
        selected = float(quote.get("odds"))
        if not all(isfinite(value) and 1.0 < value <= 51.0 for value in (home_odds, away_odds, selected, source_home_odds, source_away_odds)):
            return "invalid observed quote pair"
        expected_source_prices = (away_odds, home_odds) if source_reversed else (home_odds, away_odds)
        if (source_home_odds, source_away_odds) != expected_source_prices:
            return "scraped quote source prices do not match oriented prices"
        side = str(quote.get("provider_side") or "")
        expected = home_odds if side == "1" else away_odds if side == "2" else None
        if expected is None or selected != expected:
            return "scraped quote selected side price mismatch"
        from racketfactory.warehouse import valid_two_way_decimal_pair
        if not valid_two_way_decimal_pair(home_odds, away_odds):
            return "invalid observed quote pair"
    except (TypeError, ValueError, OverflowError):
        return "invalid observed quote pair"

    try:
        observed = datetime.fromisoformat(str(quote.get("observed_at") or "").replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return "scraped quote timestamp missing timezone"
        age = ((now or datetime.now(timezone.utc)) - observed.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= 30 * 60:
            return "scraped quote stale or future"
    except (TypeError, ValueError, OverflowError):
        return "scraped quote timestamp invalid"
    return ""


def rebuild_quote_execution_problem(quote: Mapping[str, Any], *, now=None, pick: Mapping[str, Any] | None = None) -> str:
    """Validate a fresh observed price for forward-only new-card admission.

    Named-bookmaker observations and supported indicative listing/consensus
    prices are admitted as *observations*, not asserted bookmaker fills. Missing
    documentary settlement terms remain a warning at the collection layer;
    they are not inferred here. Historical grading does not call this policy.
    """
    from math import isfinite
    source = str(quote.get("source") or "").strip().casefold()
    if source == "scrapedpickodds":
        problem = scraped_pick_odds_execution_problem(quote, now=now)
        if problem:
            return problem
        if pick is None:
            return "scraped quote pick context missing"
        if pick.get("odds") is not None:
            try:
                if float(quote.get("odds")) != float(pick.get("odds")):
                    return "scraped quote price changed"
            except (TypeError, ValueError, OverflowError):
                return "invalid selected quote price"
        selected = str(pick.get("selected_player") or "").strip()
        side = str(pick.get("selected_side") or "")
        expected_side = "1" if side in {"player_a", "1"} else "2" if side in {"player_b", "2"} else ""
        if not selected or quote.get("selection") != selected or expected_side != quote.get("provider_side"):
            return "scraped quote selected side does not match pick"
        try:
            from racketfactory.warehouse import strict_quote_team_match
            home, away = str(quote.get("provider_home") or ""), str(quote.get("provider_away") or "")
            expected, opponent = (home, away) if expected_side == "1" else (away, home)
            if not strict_quote_team_match(selected, expected) or strict_quote_team_match(selected, opponent):
                return "scraped quote selected side does not match pick"
        except (ImportError, TypeError, ValueError):
            return "scraped quote selected side cannot be validated"
        pick_date = str(pick.get("date") or pick.get("match_date") or "")[:10]
        if not pick_date or pick_date != str(quote.get("fixture_date") or "")[:10]:
            return "scraped quote fixture date does not match pick"
        pick_time = str(pick.get("kickoff") or pick.get("match_time") or "")[:5]
        if not pick_time or pick_time != str(quote.get("match_time") or "")[:5]:
            return "scraped quote kickoff does not match pick"
        return ""
    if source not in {"betexplorer", "oddsportal", "theoddsapi", "bzzoiro"}:
        return "unsupported quote source"
    market = str(quote.get("market") or "").strip().casefold()
    period = str(quote.get("period") or "").strip().casefold()
    if market not in {"h2h", "1x2", "match_winner", "match winner"}:
        return "quote market missing or incompatible"
    if period not in {"match", "full", "full_match", "full match", "0"}:
        return "quote period missing or incompatible"
    if quote.get("line") is not None and str(quote.get("line")).strip().lower() not in {"", "none", "nan"}:
        return "quote line incompatible"
    if quote.get("odds_home") is not None or quote.get("odds_away") is not None:
        try:
            pair = (float(quote.get("odds_home")), float(quote.get("odds_away")))
        except (TypeError, ValueError, OverflowError):
            return "invalid observed quote pair"
        if not all(isfinite(value) and value > 1 for value in pair):
            return "invalid observed quote pair"
    price_type = str(quote.get("price_type") or "").strip().casefold()
    bookmaker = str(quote.get("bookmaker") or "").strip()
    generic_book_labels = ("consensus", "best odds", "average", "best price", "oddsportal", "betexplorer")
    named_bookmaker = (price_type in {"", "bookmaker"} and bool(bookmaker)
                       and not any(word in bookmaker.casefold() for word in generic_book_labels))
    indicative = (source in {"betexplorer", "oddsportal"}
                  and price_type in {"", "consensus", "indicative", "best_across_books", "best across books"})
    if not (named_bookmaker or indicative):
        return "quote is neither a named bookmaker offer nor a supported indicative observation"
    if source == "betexplorer":
        schedule_problem = betexplorer_schedule_evidence_problem(quote)
        if schedule_problem:
            return schedule_problem
    elif source == "oddsportal":
        schedule_problem = oddsportal_schedule_evidence_problem(quote)
        if schedule_problem:
            return schedule_problem
    return ""
