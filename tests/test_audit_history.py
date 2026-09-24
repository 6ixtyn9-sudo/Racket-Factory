from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_recent_picks import merge_audit_history, write_markdown


def test_write_markdown_lists_every_pick(tmp_path, monkeypatch):
    rows = [
        {
            "date": "2026-09-01",
            "match": f"A{i} vs B{i}",
            "selected_player": "A",
            "winner": "A",
            "status": "won",
            "settle_source": "test",
            "settle_date": "2026-09-01",
            "settle_score": "6-0 6-0",
        }
        for i in range(120)
    ]
    report = {
        "start": "2026-08-01",
        "end": "2026-09-17",
        "archived_pick_rows": 120,
        "archived_pick_dates": ["2026-09-01"],
        "overall": {},
        "ledger_kind": "official",
        "include_same_day": True,
        "same_day_cutoff": "2026-09-17",
        "same_day_excluded": 0,
        "all_picks": rows,
        "by_tour": {},
        "by_series": {},
        "by_surface": {},
        "by_bucket": {},
        "by_source": {},
    }
    path = tmp_path / "audit.md"
    write_markdown(path, report)
    text = path.read_text()
    assert "A0 vs B0" in text
    assert "A119 vs B119" in text
    assert "and 20 more" not in text


def test_merge_history_keeps_settled_when_later_pending(tmp_path, monkeypatch):
    import scripts.audit_recent_picks as arp

    monkeypatch.setattr(arp, "HISTORY_PATH", tmp_path / "picks_audit_history.json")
    monkeypatch.setattr(arp, "LOCALDATA", tmp_path)
    arp.HISTORY_PATH.write_text(
        json.dumps(
            [
                {
                    "date": "2026-09-14",
                    "match": "A vs B",
                    "selected_player": "A",
                    "status": "won",
                    "winner": "A",
                }
            ]
        )
    )
    merged = arp.merge_audit_history(
        [
            {
                "date": "2026-09-14",
                "match": "A vs B",
                "selected_player": "A",
                "status": "pending_no_result",
            },
            {
                "date": "2026-09-15",
                "match": "C vs D",
                "selected_player": "C",
                "status": "lost",
                "winner": "D",
            },
        ]
    )
    by_match = {r["match"]: r for r in merged}
    assert by_match["A vs B"]["status"] == "won"
    assert by_match["C vs D"]["status"] == "lost"


def _setup_history(tmp_path, monkeypatch, rows):
    import scripts.audit_recent_picks as arp

    monkeypatch.setattr(arp, "HISTORY_PATH", tmp_path / "picks_audit_history.json")
    monkeypatch.setattr(arp, "LOCALDATA", tmp_path)
    arp.HISTORY_PATH.write_text(json.dumps(rows))
    return arp


def test_merge_prunes_stale_rows(tmp_path, monkeypatch):
    arp = _setup_history(
        tmp_path,
        monkeypatch,
        [
            {
                # Pick no longer exists in any archived ledger (its daily
                # file was rewritten) — must not survive into future audits.
                "date": "2026-09-17",
                "match": "Stale vs Pick",
                "selected_player": "Stale",
                "status": "pending_no_result",
            },
            {
                "date": "2026-09-14",
                "match": "Keep A vs B",
                "selected_player": "Keep A",
                "status": "won",
                "winner": "Keep A",
            },
        ],
    )
    valid = {
        arp._pick_key(
            {
                "ledger_kind": "official",
                "date": "2026-09-14",
                "match": "Keep A vs B",
                "selected_player": "Keep A",
            }
        )
    }
    merged = arp.merge_audit_history(
        [
            {
                "date": "2026-09-14",
                "match": "Keep A vs B",
                "selected_player": "Keep A",
                "status": "won",
                "winner": "Keep A",
                "ledger_kind": "official",
            }
        ],
        valid_keys=valid,
    )
    assert {r["match"] for r in merged} == {"Keep A vs B"}


def test_merge_preserves_other_ledger_kind_history(tmp_path, monkeypatch):
    arp = _setup_history(
        tmp_path,
        monkeypatch,
        [
            {
                # Forecast history row must survive an official run even
                # though it is absent from the official archive.
                "date": "2026-09-19",
                "match": "Forecast X vs Y",
                "selected_player": "Forecast X",
                "status": "pending_no_result",
                "ledger_kind": "forecast",
            },
            {
                "date": "2026-09-17",
                "match": "Stale vs Pick",
                "selected_player": "Stale",
                "status": "pending_no_result",
                "ledger_kind": "official",
            },
        ],
    )
    # Only the current official row exists in the official archive; the
    # stale official row and the forecast row do not.
    valid = {
        arp._pick_key(
            {
                "ledger_kind": "official",
                "date": "2026-09-19",
                "match": "Live A vs B",
                "selected_player": "Live A",
            }
        )
    }
    merged = arp.merge_audit_history(
        [{"date": "2026-09-19", "match": "Live A vs B", "selected_player": "Live A",
          "status": "pending_no_result", "ledger_kind": "official"}],
        valid_keys=valid,
    )
    matches = {(r["match"], r.get("ledger_kind")) for r in merged}
    assert ("Forecast X vs Y", "forecast") in matches
    assert ("Live A vs B", "official") in matches
    assert not any(r["match"] == "Stale vs Pick" for r in merged)


def test_dedupe_collapses_carryover_repicks():
    import scripts.audit_recent_picks as arp

    rows = [
        {
            "date": "2026-09-16",
            "match": "Mikulskyte / Smith vs Strakhova / Tikhonova",
            "selected_player": "Strakhova / Tikhonova",
            "bucket": "SKIPPED_DEAD_EDGE",
            "status": "won",
            "winner": "Strakhova V. / Tikhonova A.",
        },
        {
            # Same match + same selection re-archived on match day.
            "date": "2026-09-17",
            "match": "Mikulskyte / Smith vs Strakhova / Tikhonova",
            "selected_player": "Strakhova / Tikhonova",
            "bucket": "SKIPPED_VETO",
            "status": "won",
            "winner": "Strakhova V. / Tikhonova A.",
        },
        {
            "date": "2026-09-15",
            "match": "Solo A vs Solo B",
            "selected_player": "Solo A",
            "bucket": "WATCHLIST",
            "status": "pending_no_result",
        },
    ]
    out = arp.dedupe_by_identity(rows)
    assert len(out) == 2
    miku = next(r for r in out if "Mikulskyte" in r["match"])
    assert miku["date"] == "2026-09-16"  # earliest = original pick
    assert miku["bucket"] == "SKIPPED_DEAD_EDGE"
    assert miku["duplicate_picks"] == ["2026-09-17 SKIPPED_VETO (won)"]


def test_dedupe_home_away_swap_and_settled_beats_pending():
    import scripts.audit_recent_picks as arp

    rows = [
        {
            "date": "2026-09-16",
            "match": "A One vs B Two",
            "selected_player": "B Two",
            "status": "pending_no_result",
        },
        {
            "date": "2026-09-17",
            "match": "B Two vs A One",  # home/away flipped on re-pick
            "selected_player": "B Two",
            "status": "lost",
            "winner": "A One",
        },
    ]
    out = arp.dedupe_by_identity(rows)
    assert len(out) == 1
    assert out[0]["status"] == "lost"  # settled row wins the merge
    assert out[0]["winner"] == "A One"


def test_summarize_by_covers_unsettled_groups():
    import scripts.audit_recent_picks as arp

    settled = [
        arp.SettledPick(
            date="2026-09-18", tour="ATP", series="ATP250", surface="Hard",
            bucket="WATCHLIST", source="Forebet", match="A vs B",
            selected_player="A", winner="A", won=True, odds=2.0, pnl=1.0,
        )
    ]
    all_rows = settled + [
        {
            "date": "2026-09-18",
            "match": "C vs D",
            "selected_player": "C",
            "status": "pending_no_result",
            "bucket": "WATCHLIST_NO_ODDS",
        }
    ]
    by = arp.summarize_by(settled, "bucket", all_rows)
    assert by["WATCHLIST"]["total_picks"] == 1
    assert by["WATCHLIST"]["settled_picks"] == 1
    # A bucket whose picks are all pending must still appear (full coverage).
    assert by["WATCHLIST_NO_ODDS"]["total_picks"] == 1
    assert by["WATCHLIST_NO_ODDS"]["settled_picks"] == 0
    assert by["WATCHLIST_NO_ODDS"]["pending_picks"] == 1
    assert by["WATCHLIST_NO_ODDS"]["roi"] is None


def test_write_markdown_full_coverage_group_lines(tmp_path, monkeypatch):
    import scripts.audit_recent_picks as arp

    rows = [
        {
            "date": "2026-09-18",
            "match": "A vs B",
            "selected_player": "A",
            "winner": "A",
            "status": "won",
            "settle_source": "test",
            "settle_date": "2026-09-18",
            "settle_score": "6-0 6-0",
            "bucket": "WATCHLIST",
        },
        {
            "date": "2026-09-18",
            "match": "C vs D",
            "selected_player": "C",
            "status": "pending_no_result",
            "reason": "no matching result rows found",
            "bucket": "WATCHLIST_NO_ODDS",
        },
    ]
    settled = [
        arp.SettledPick(
            date="2026-09-18", tour="ATP", series="ATP250", surface="Hard",
            bucket="WATCHLIST", source="Forebet", match="A vs B",
            selected_player="A", winner="A", won=True, odds=2.0, pnl=1.0,
        )
    ]
    report = {
        "start": "2026-09-01",
        "end": "2026-09-18",
        "archived_pick_rows": 2,
        "archived_pick_dates": ["2026-09-18"],
        "ledger_pick_rows": 2,
        "duplicates_merged": 0,
        "stale_rows_pruned": 0,
        "pending_reasons": {"pending_no_result: no matching result rows found": 1},
        "overall": {},
        "ledger_kind": "official",
        "include_same_day": True,
        "same_day_cutoff": "2026-09-18",
        "same_day_excluded": 0,
        "all_picks": rows,
        "by_tour": {},
        "by_series": {},
        "by_surface": {},
        "by_bucket": arp.summarize_by(settled, "bucket", rows),
        "by_source": {},
    }
    path = tmp_path / "audit.md"
    arp.write_markdown(path, report)
    text = path.read_text()
    assert "## Ledger reconciliation" in text
    assert "WATCHLIST_NO_ODDS" in text
    assert "total=1, settled=0, wins=0, hit_rate=None, ROI=None, pending=1" in text


def test_audit_rows_persist_edge_labels(tmp_path, monkeypatch):
    """edge_grade/edge_tier/edge_verdict ride along on every audit row path
    (settled, pending, same-day-excluded) into picks_audit_history.json, so
    tier cohorts survive retention pruning of the daily pick files. A pick
    without labels gets none (nothing invented, old rows need no backfill)."""
    import scripts.audit_recent_picks as arp

    monkeypatch.setattr(arp, "LOCALDATA", tmp_path)
    monkeypatch.setattr(arp, "HISTORY_PATH", tmp_path / "picks_audit_history.json")
    monkeypatch.setattr(arp, "local_today", lambda: "2026-09-21")

    def pick(day, home, away, **labels):
        return {"date": day, "match": f"{home} vs {away}", "player_home": home,
                "player_away": away, "selected_player": home, "tour": "ATP",
                "bucket": "WATCHLIST", "odds": 1.8, **labels}

    gold = {"edge_grade": "GOLD", "edge_tier": "BANKER", "edge_verdict": "EDGE CONFIRMED"}
    silver = {"edge_grade": "SILVER", "edge_tier": "WATCHLIST_ONLY", "edge_verdict": "WATCHLIST"}
    (tmp_path / "picks_2026-09-20.json").write_text(json.dumps([
        pick("2026-09-20", "Alpha One", "Beta Two", **gold),        # settles WON
        pick("2026-09-20", "Gamma Three", "Delta Four", **silver),  # no result
        pick("2026-09-20", "Eps Five", "Zeta Six", edge_grade=float("nan")),
    ]))
    (tmp_path / "picks_2026-09-21.json").write_text(json.dumps([
        pick("2026-09-21", "Eta Seven", "Theta Eight", **gold),     # same day
    ]))
    warehouse = tmp_path / "warehouse.csv"
    warehouse.write_text(
        "player_a,player_b,winner,score,match_date,source\n"
        "Alpha One,Beta Two,Alpha One,2-0 6-3 6-4,2026-09-20,test\n"
    )

    report = arp.build_report("2026-09-20", "2026-09-21", warehouse)

    fields = ("edge_grade", "edge_tier", "edge_verdict")
    history = json.loads(arp.HISTORY_PATH.read_text())
    for rows in (history, report["all_picks"]):
        by_match = {r["match"]: r for r in rows}
        assert by_match["Alpha One vs Beta Two"]["status"] == "won"
        assert {k: by_match["Alpha One vs Beta Two"][k] for k in fields} == gold
        assert by_match["Gamma Three vs Delta Four"]["status"] == "pending_no_result"
        assert {k: by_match["Gamma Three vs Delta Four"][k] for k in fields} == silver
        assert by_match["Eta Seven vs Theta Eight"]["status"] == "pending_same_day_excluded"
        assert by_match["Eta Seven vs Theta Eight"]["edge_tier"] == "BANKER"
        assert not set(fields) & set(by_match["Eps Five vs Zeta Six"])
