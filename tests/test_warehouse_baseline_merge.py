"""Warehouse baseline merge + mining-time slice table artifact.

Incident under test: 2026-09-20 — the mining-time warehouse (built with
runtime cache enrichment) derived `_surface:Hard | fav_odds_band:1.1-1.3 |
pred_confidence:High` as FADE THIS SIGNAL while the post-run rebuild
derived it as EDGE CONFIRMED; all 20 candidates fell through to
no_slice_export. Fixes: (1) the assembled warehouse is committed and the
next run re-anchors onto it so historical dimension columns are pinned;
(2) the mining-time slice table + warehouse fingerprint is committed so
every match decision is auditable.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd


from scripts.merge_warehouse_baseline import merge_baselines
from scripts.mine_edges import dump_slice_table


def _row(**kw):
    base = {
        "match_date": "2026-09-18",
        "tour": "WTA",
        "tournament": "Sofia Open",
        "player_a": "Ana Ivanova",
        "player_b": "Bela Kovacs",
        "winner": "",
        "odds_a": 1.20,
        "odds_b": 4.10,
        "predicted_winner": "Ana Ivanova",
        "prediction_prob": 0.80,
        "_is_live": False,
    }
    base.update(kw)
    return base


def _df(rows):
    return pd.DataFrame(rows)


def test_passthrough_without_baseline():
    fresh = _df([_row()])
    merged, report = merge_baselines(None, fresh)
    assert report["status"] == "passthrough"
    assert len(merged) == 1
    assert float(merged.iloc[0]["odds_a"]) == 1.20


def test_dimension_columns_pinned_to_baseline_and_drift_measured():
    baseline = _df([_row(odds_a=1.20, odds_b=4.10, predicted_winner="Ana Ivanova")])
    fresh = _df([_row(odds_a=1.45, odds_b=2.80, predicted_winner="Bela Kovacs")])
    merged, report = merge_baselines(baseline, fresh)
    assert len(merged) == 1
    row = merged.iloc[0]
    # dimension columns keep the baseline values (no silent re-derivation)
    assert float(row["odds_a"]) == 1.20
    assert float(row["odds_b"]) == 4.10
    assert row["predicted_winner"] == "Ana Ivanova"
    # ...and the drift is measured, not hidden
    assert report["dim_drift_rows"] == 1
    assert report["shared_keys"] == 1
    assert any(e["col"] in {"odds_a", "odds_b", "predicted_winner"} for e in report["dim_drift_examples"])


def test_settlement_flows_from_fresh_and_adopts_settled_odds():
    baseline = _df([_row(winner="")])
    fresh = _df(
        [
            _row(
                winner="Ana Ivanova",
                score="6-3 6-4",
                odds_a=1.21,
                _ref_odds_a=1.18,
            )
        ]
    )
    merged, report = merge_baselines(baseline, fresh)
    row = merged.iloc[0]
    assert row["winner"] == "Ana Ivanova"
    assert row["score"] == "6-3 6-4"
    assert float(row["_ref_odds_a"]) == 1.18
    assert float(row["odds_a"]) == 1.21  # settled odds from the fresh build
    assert report["settlement_updates"] == 1


def test_new_matches_from_fresh_are_appended():
    baseline = _df([_row()])
    fresh = _df(
        [
            _row(),
            _row(match_date="2026-09-19", player_a="Cara Dumas", player_b="Eva Fontaine",
                 odds_a=1.55, odds_b=2.45),
        ]
    )
    merged, report = merge_baselines(baseline, fresh)
    assert len(merged) == 2
    assert report["new_from_fresh"] == 1
    assert report["baseline_only"] == 0


def test_live_row_from_fresh_replaces_stale_baseline_row():
    baseline = _df([_row(odds_a=1.20, _is_live=True)])
    fresh = _df([_row(odds_a=1.28, odds_b=3.90, _is_live=True)])
    merged, report = merge_baselines(baseline, fresh)
    row = merged.iloc[0]
    assert float(row["odds_a"]) == 1.28
    assert report["live_rows_from_fresh"] == 1


def test_baseline_only_rows_are_kept():
    baseline = _df([_row(match_date="2026-09-17", player_a="Gina Rossi", player_b="Hana Silva")])
    fresh = _df([_row()])
    merged, report = merge_baselines(baseline, fresh)
    assert len(merged) == 2
    assert report["baseline_only"] == 1
    assert report["shared_keys"] == 0


def test_cli_writes_merged_warehouse_and_report(tmp_path):
    from scripts.merge_warehouse_baseline import main

    baseline = _df([_row(winner="")])
    fresh = _df([_row(winner="Ana Ivanova", score="6-1 6-2")])
    b_path = tmp_path / "baseline.csv.gz"
    f_path = tmp_path / "warehouse.csv.gz"
    r_path = tmp_path / "report.json"
    baseline.to_csv(b_path, index=False, compression="gzip")
    fresh.to_csv(f_path, index=False, compression="gzip")

    import sys

    argv = sys.argv
    sys.argv = [
        "merge_warehouse_baseline.py",
        "--baseline", str(b_path),
        "--fresh", str(f_path),
        "--output", str(f_path),
        "--report", str(r_path),
    ]
    try:
        rc = main()
    finally:
        sys.argv = argv

    assert rc == 0
    out = pd.read_csv(f_path)
    assert len(out) == 1
    assert out.iloc[0]["winner"] == "Ana Ivanova"
    report = json.loads(r_path.read_text())
    assert report["status"] == "merged"
    assert report["merged_sha256"] == hashlib.sha256(Path(f_path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# slice table artifact
# ---------------------------------------------------------------------------

def _results():
    return [
        {
            "Slice": "_surface:Hard | fav_odds_band:1.1-1.3 | pred_confidence:High",
            "Side": "prediction",
            "Combo_Dict": {"_surface": "Hard", "fav_odds_band": "1.1-1.3", "pred_confidence": "High"},
            "Dims": 3,
            "N": 245,
            "WinRate": "80.41%",
            "Shrunk": "79.92%",
            "ROI": "39.89%",
            "Grade": "GOLD",
            "Tier": "BANKER",
            "Verdict": "EDGE CONFIRMED",
            "Exportable": True,
            "ROI_num": 39.89,
        },
        {
            "Slice": "_surface:Hard | fav_odds_band:1.1-1.3 | pred_confidence:High",
            "Side": "fade",
            "Combo_Dict": {"_surface": "Hard", "fav_odds_band": "1.1-1.3", "pred_confidence": "High"},
            "Dims": 3,
            "N": 243,
            "WinRate": "23.46%",
            "Shrunk": "23.89%",
            "ROI": "-41.66%",
            "Grade": "BRONZE",
            "Tier": "ROBBER",
            "Verdict": "FADE THIS SIGNAL",
            "Exportable": False,
            "ROI_num": -41.66,
        },
    ]


def test_dump_slice_table_writes_auditable_artifact(tmp_path):
    wh = tmp_path / "warehouse.csv.gz"
    wh.write_bytes(b"fake-warehouse-bytes")
    df = _df([_row()])
    df["cross_source_agree"] = ["Disagree"]
    out = tmp_path / "slice_table_2026-09-20.json"

    result = dump_slice_table("2026-09-20", _results(), df, wh, out)

    assert result == out
    payload = json.loads(out.read_text())
    assert payload["date"] == "2026-09-20"
    assert payload["warehouse_sha256"] == hashlib.sha256(b"fake-warehouse-bytes").hexdigest()
    assert payload["n_slices"] == 2
    assert payload["n_model_slices"] == 1
    assert payload["n_fade_slices"] == 1
    assert payload["n_exportable_model"] == 1
    assert payload["n_exportable_fade"] == 0
    assert payload["cross_source_agree"] == {"Disagree": 1}
    assert payload["slices"][0]["Slice"].startswith("_surface:Hard")


def test_dump_slice_table_handles_empty_results_and_missing_warehouse(tmp_path):
    out = tmp_path / "slice_table_2026-09-20.json"
    result = dump_slice_table("2026-09-20", [], None, tmp_path / "nope.csv.gz", out)
    assert result == out
    payload = json.loads(out.read_text())
    assert payload["n_slices"] == 0
    assert payload["slices"] == []
    assert payload["warehouse_sha256"] == ""


def test_dump_slice_table_failure_is_non_fatal(tmp_path):
    # Parent path is an existing FILE -> mkdir fails; the miner must not
    # die over an artifact, and must return None.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = blocker / "slice_table.json"
    assert dump_slice_table("2026-09-20", [], None, tmp_path / "x.csv.gz", bad) is None
