#!/usr/bin/env python3
"""
Racket Factory — CLV & Confidence Calibration Audit

Since odds are tough for Challenger/ITF (TheOddsAPI only Slams/1000/500),
CLV focuses on strengths:

- Confidence calibration: does 70% prob actually win 70%?
- Source reliability: which predictor's 70% is actually 70%?
- Odds movement: when odds available, did we beat closing?
- ROI by confidence band, source, tour, surface

Outputs:
- localdata/clv_rolling.json
- localdata/clv_*.md

This feeds the ML feedback loop in mine_edges.py
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from collections import defaultdict
from datetime import date, timedelta
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LOCALDATA = ROOT / "localdata"

def wilson_lb(wins: int, n: int) -> float:
    if n == 0:
        return 0.0
    z = 1.959963984540054
    p = wins / n
    denom = 1 + z*z/n
    centre = p + z*z/(2*n)
    spread = z * math.sqrt((p*(1-p) + z*z/(4*n))/n)
    return (centre - spread) / denom

def load_warehouse():
    path = LOCALDATA / "warehouse.csv.gz"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()

def load_picks_archive(days: int = 60):
    """Load last N days of picks_*.json"""
    end = date.today()
    start = end - timedelta(days=days)
    all_picks = []
    d = start
    while d <= end:
        p = LOCALDATA / f"picks_{d.isoformat()}.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, list):
                    for row in data:
                        if isinstance(row, dict):
                            row["archive_date"] = d.isoformat()
                            all_picks.append(row)
            except Exception:
                pass
        d += timedelta(days=1)
    return all_picks

def audit_clv(days: int = 30):
    warehouse = load_warehouse()
    if warehouse.empty:
        print("warehouse empty")
        return {}

    # For calibration, we need settled rows with winner and predicted prob
    # Use warehouse itself for calibration: predicted prob vs actual outcome
    # This is more direct than picks audit since warehouse has all historical

    # Build calibration by confidence band and source
    # We need to parse prediction prob columns
    pred_cols = [c for c in warehouse.columns if c.startswith("prediction_prob")]
    prob_col = "prediction_prob_foretennis"
    if prob_col not in warehouse.columns and pred_cols:
        prob_col = pred_cols[0]

    calibration = defaultdict(list)
    source_calib = defaultdict(list)
    tour_calib = defaultdict(list)

    for _, row in warehouse.iterrows():
        winner = str(row.get("winner") or "").strip()
        if not winner:
            continue
        player_a = str(row.get("player_a") or "")
        player_b = str(row.get("player_b") or "")
        # Determine if favorite won or prediction won?
        # For calibration, check predicted_winner vs actual winner
        pred_winner = None
        pred_prob = None
        for col in [c for c in warehouse.columns if c.startswith("predicted_winner")]:
            val = row.get(col)
            if pd.notna(val) and str(val).strip() not in {"", "nan", "<NA>", "None"}:
                pred_winner = str(val).strip()
                # find matching prob
                prob_col_match = col.replace("predicted_winner", "prediction_prob")
                if prob_col_match in warehouse.columns:
                    pred_prob = row.get(prob_col_match)
                break

        if not pred_winner or pred_prob is None:
            continue
        try:
            prob = float(pred_prob)
            if prob <= 1.0:
                prob *= 100
        except:
            continue

        # Map pred_winner to player_a/b
        if pred_winner in {"player_a", "1"}:
            pred_player = player_a
        elif pred_winner in {"player_b", "2"}:
            pred_player = player_b
        else:
            # direct name
            pred_player = pred_winner

        # Check if prediction correct
        # Simple name matching
        won = winner.lower() in pred_player.lower() or pred_player.lower() in winner.lower()

        # Band
        if prob >= 70:
            band = "High (70%+)"
        elif prob >= 60:
            band = "Medium (60-70%)"
        else:
            band = "Low (<60%)"

        calibration[band].append(1 if won else 0)
        tour = str(row.get("tour") or "UNKNOWN")
        tour_calib[tour].append(1 if won else 0)

        # Source
        src = str(row.get("source") or row.get("tournament") or "UNKNOWN")[:30]
        # Use predicted_winner column source
        for col in [c for c in warehouse.columns if c.startswith("predicted_winner")]:
            if pd.notna(row.get(col)):
                src = col.replace("predicted_winner_", "").replace("predicted_winner", "market")
                break
        source_calib[src].append(1 if won else 0)

    # Summarize
    def summarize(arr):
        n = len(arr)
        wins = sum(arr)
        return {
            "n": n,
            "wins": wins,
            "hit_rate": round(wins/n, 4) if n else None,
            "wilson_lb": round(wilson_lb(wins, n), 4) if n else 0.0,
            "expected": None,  # will fill
        }

    calib_summary = {}
    for band, arr in calibration.items():
        s = summarize(arr)
        # Expected hit rate from band name
        if "High" in band:
            s["expected"] = 0.70
        elif "Medium" in band:
            s["expected"] = 0.60
        else:
            s["expected"] = 0.50
        s["calibration_error"] = round((s["hit_rate"] or 0) - s["expected"], 4) if s["hit_rate"] is not None else None
        calib_summary[band] = s

    # ROI by tour from audit_recent_picks rolling if exists
    audit_path = LOCALDATA / "picks_audit_rolling.json"
    audit_data = {}
    if audit_path.exists():
        try:
            audit_data = json.loads(audit_path.read_text())
        except:
            pass

    # CLV: odds movement when available
    # Check if we have odds_a/b and closing odds? For now use TheOddsAPI vs scraped
    clv_stats = {
        "total_settled": len(warehouse[warehouse["winner"].notna()]),
        "calibration_by_confidence": calib_summary,
        "calibration_by_tour": {k: summarize(v) for k, v in tour_calib.items()},
        "calibration_by_source": {k: summarize(v) for k, v in source_calib.items()},
        "audit_roi_by_tour": audit_data.get("by_tour", {}),
        "audit_roi_by_surface": audit_data.get("by_surface", {}),
        "audit_roi_by_source": audit_data.get("by_source", {}),
    }

    # Write rolling
    out_path = LOCALDATA / "clv_rolling.json"
    out_path.write_text(json.dumps(clv_stats, indent=2, sort_keys=True))
    print(f"Wrote {out_path} with {len(calib_summary)} confidence bands")

    # Write markdown
    md_path = LOCALDATA / "clv_rolling.md"
    lines = ["# Racket Factory — CLV & Calibration Rolling", "", f"Generated {date.today().isoformat()}", ""]
    lines.append("## Confidence Calibration (prob vs actual win rate)")
    lines.append("| Band | N | Wins | Hit Rate | Expected | Error | Wilson LB |")
    lines.append("|---|---|---|---|---|---|---|")
    for band, s in sorted(calib_summary.items()):
        lines.append(f"| {band} | {s['n']} | {s['wins']} | {s['hit_rate']} | {s['expected']} | {s['calibration_error']} | {s['wilson_lb']} |")
    lines.append("")
    lines.append("## ROI by Tour (from audit)")
    lines.append("| Tour | N | ROI | Hit Rate |")
    lines.append("|---|---|---|---|")
    for tour, stats in sorted(audit_data.get("by_tour", {}).items()):
        lines.append(f"| {tour} | {stats.get('settled_picks')} | {stats.get('roi')} | {stats.get('hit_rate')} |")
    md_path.write_text("\n".join(lines))
    print(f"Wrote {md_path}")
    return clv_stats

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    audit_clv(days=args.days)
