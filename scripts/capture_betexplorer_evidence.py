"""Bounded evidence-only BetExplorer probe; never evaluates or publishes bets."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from racketfactory.kickoff import SAST
from racketfactory.rebuild_diagnostics import directory
from racketfactory.sources import betexplorer
from racketfactory.sources._page_odds import PageFetchError


def capture(target: str) -> dict:
    out = {"target_date": target, "purpose": "evidence_only", "publication_attempted": False,
           "listing_attempted": False, "fixtures_attempted": 0, "stage_counts": {}, "failures": []}
    try:
        out["listing_attempted"] = True
        rows, status = betexplorer._fetch_live(target, with_status=True)
        out["listing_status"] = status
        if status != "complete":
            return out
        seen = set()
        for row in rows:
            eid = str(row.get("event_id") or "")
            if not eid or eid in seen or not row.get("match_url"):
                continue
            seen.add(eid)
            out["fixtures_attempted"] += 1
            try:
                betexplorer.fetch_fixture_comparison(row, refresh_evidence=True, stage_counts=out["stage_counts"])
            except Exception as exc:
                kind = exc.kind if isinstance(exc, PageFetchError) else "unexpected"
                out["failures"].append({"kind": kind, "stage": getattr(exc, "stage", "unknown")})
                if kind in {"rate_limit", "challenge", "auth"}:
                    break
            if out["fixtures_attempted"] == 2:
                break
    except Exception as exc:
        out["failures"].append({"kind": exc.kind if isinstance(exc, PageFetchError) else "unexpected", "stage": "listing"})
    return out


def main() -> int:
    path = directory()
    if path is None:
        raise RuntimeError("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR must be set")
    path.mkdir(parents=True, exist_ok=True)
    result = capture(datetime.now(SAST).date().isoformat())
    (path / "evidence_collection.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    # Even a successful HTTP request does not prove the parser or policy.
    return 1 if (result["failures"] or result.get("listing_status") != "complete" or
                 not result["stage_counts"].get("comparison_response_success")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
