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
