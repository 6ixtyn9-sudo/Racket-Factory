#!/usr/bin/env python3
"""
Racket Factory Edge Miner (Ma Golide Enhanced)
Automated combinatorial discovery of Bankers and Robbers, including prediction signals.

WARNING: ROI is currently calculated using Market Closing Odds. AI predictions captured early in the day must be evaluated against Opening Odds before live capital is deployed.
"""
import pandas as pd
import argparse
import logging
import sys
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from itertools import combinations
import re

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.assay import assay_segment
from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.forebet import ForebetPredictor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("edge_miner")


def get_player_rank_band(rank: float) -> str:
    if pd.isna(rank): return "Unknown"
    if rank <= 10: return "Top 10"
    if rank <= 50: return "11-50"
    if rank <= 100: return "51-100"
    return "100+"


def get_selected_side_rank_band(row: pd.Series) -> str:
    """Pre-match rank band of the side we would actually back.

    Priority:
      1. primary prediction (`predicted_winner`)
      2. ForeTennis prediction
      3. market favorite as fallback
    Uses pre-match player rank columns only.
    """
    pick = row.get("predicted_winner")
    if pd.isna(pick) or pick == "":
        pick = row.get("predicted_winner_foretennis")

    if pd.notna(pick) and pick in {"player_a", "player_b"}:
        rank_col = "rank_a" if pick == "player_a" else "rank_b"
        if rank_col in row:
            return get_player_rank_band(row.get(rank_col))

    oa, ob = row.get("odds_a"), row.get("odds_b")
    if pd.notna(oa) and pd.notna(ob):
        fav_col = "rank_a" if oa <= ob else "rank_b"
        if fav_col in row:
            return get_player_rank_band(row.get(fav_col))

    return "Unknown"


def get_odds_band(odds: float) -> str:
    if pd.isna(odds): return "Unknown"
    if odds < 1.3: return "1.1-1.3"
    if odds < 1.6: return "1.3-1.6"
    if odds < 2.0: return "1.6-2.0"
    return "2.0+"


def get_confidence_band(prob: float) -> str:
    """Bucket prediction probability into confidence tiers."""
    if pd.isna(prob): return "Unknown"
    if prob >= 0.70: return "High"
    if prob >= 0.60: return "Medium"
    return "Low"


def get_cross_source_agree(row: pd.Series) -> str:
    """
    Compare Market baseline vs ForeTennis AI predictions.
    Returns one of: Both | Disagree | MarketOnly | ForeTennisOnly
    """
    mkt = row.get("predicted_winner_market")
    ft = row.get("predicted_winner_foretennis")
    has_mkt = pd.notna(mkt) and mkt != ""
    has_ft = pd.notna(ft) and ft != ""
    if has_mkt and has_ft:
        return "Both" if mkt == ft else "Disagree"
    if has_mkt:
        return "MarketOnly"
    if has_ft:
        return "ForeTennisOnly"
    return "Unknown"


def infer_tour_and_series(text: str, row: pd.Series | None = None) -> tuple[str, str]:
    lower = str(text or "").lower()
    if any(x in lower for x in ["wimbledon", "roland garros", "us open", "australian open"]):
        if any(x in lower for x in ["women", "wta", "girls"]):
            return ("WTA", "Grand Slam")
        if any(x in lower for x in ["men", "atp", "boys"]):
            return ("ATP", "Grand Slam")
        if row is not None and str(row.get("source", "")) == "BetClan":
            tournament_text = str(row.get("tournament", "") or "").lower()
            event_text = str(row.get("event_text", "") or "").lower()
            category_text = str(row.get("category", "") or "").lower()
            if not any(x in f"{tournament_text} | {event_text} | {category_text}" for x in ["women", "wta", "girls"]):
                if not ("/" in str(row.get("player_home", "")) or "/" in str(row.get("player_away", ""))):
                    return ("ATP", "Grand Slam")
        return ("UNKNOWN", "Grand Slam")
    if any(x in lower for x in ["atp challenger", "challenger"]):
        return ("CHALLENGER", "Challenger")
    if "itf women" in lower:
        return ("ITF-W", "ITF")
    if "itf men" in lower or "itf m" in lower:
        return ("ITF-M", "ITF")
    if "wta 1000" in lower:
        return ("WTA", "WTA1000")
    if "wta 500" in lower or any(x in lower for x in ["wta eastbourne", "wta bad homburg"]):
        return ("WTA", "Premier")
    if "wta 250" in lower:
        return ("WTA", "WTA250")
    if "atp 500" in lower:
        return ("ATP", "ATP500")
    if "atp 250" in lower or any(x in lower for x in ["atp mallorca", "atp eastbourne"]):
        return ("ATP", "ATP250")
    if any(x in lower for x in ["women doubles", "wta doubles"]) or re.search(r"\bwd\b", lower):
        return ("WTA", "WTA")
    if any(x in lower for x in ["men doubles", "atp doubles"]) or re.search(r"\bmd\b", lower):
        return ("ATP", "ATP")
    if "wta" in lower:
        return ("WTA", "WTA")
    if "atp" in lower:
        return ("ATP", "ATP")
    if "utr" in lower:
        return ("UTR", "UTR")
    return ("UNKNOWN", "UNKNOWN")


def prob_to_odds_band(prob_pct: float) -> str:
    if pd.isna(prob_pct):
        return "Unknown"
    if prob_pct >= 75:
        return "1.1-1.3"
    if prob_pct >= 62:
        return "1.3-1.6"
    if prob_pct >= 50:
        return "1.6-2.0"
    return "2.0+"


def detect_match_type(row: pd.Series) -> str:
    if "/" in str(row.get("player_home", "")) or "/" in str(row.get("player_away", "")):
        return "Doubles"
    context = " | ".join(
        str(row.get(c, "") or "")
        for c in ["tournament", "event_text", "category", "match_label"]
        if c in row.index
    ).lower()
    if any(x in context for x in ["women doubles", "wta doubles", "men doubles", "atp doubles", "mixed doubles"]):
        return "Doubles"
    if re.search(r"\b(?:wd|md)\b", context):
        return "Doubles"
    return "Singles"


def build_upcoming_fallback_card(target_date: str) -> pd.DataFrame:
    rows = []
    for source_name, predictor, fetcher in [
        ("PredixSport", PredixSportPredictor(), lambda p: p.fetch_daily()),
        ("BetClan", BetClanPredictor(), lambda p: p.fetch_daily()),
        ("Forebet", ForebetPredictor(), lambda p: p.fetch_daily_predictions("today")),
    ]:
        try:
            preds = fetcher(predictor)
        except Exception as e:
            logger.warning("Upcoming fallback source %s failed: %s", source_name, e)
            preds = []
        for row in preds:
            row = dict(row)
            row["source"] = source_name
            rows.append(row)
    if not rows:
        return pd.DataFrame()

    card = pd.DataFrame(rows)
    if "match_date" in card.columns:
        card = card[card["match_date"].astype(str) == str(target_date)].copy()
    if card.empty:
        return card

    card["match_type"] = card.apply(detect_match_type, axis=1)
    context_cols = [c for c in ["tournament", "event_level", "event_text", "category", "tour_slug", "tournament_slug"] if c in card.columns]
    card["context_used"] = card.apply(
        lambda r: " | ".join([str(r.get(c, "") or "") for c in context_cols if str(r.get(c, "") or "").strip()]),
        axis=1,
    )
    inferred = card.apply(lambda r: infer_tour_and_series(r.get("context_used", ""), row=r), axis=1)
    card["tour"] = inferred.apply(lambda x: x[0])
    card["_series"] = inferred.apply(lambda x: x[1])
    card["_surface"] = card.get("surface", pd.Series(index=card.index, dtype=object)).astype(str).str.strip().str.title()
    card.loc[card["_surface"].isin(["", "Nan", "None"]), "_surface"] = "Unknown"
    card["pred_confidence"] = card.apply(
        lambda r: "High" if max(pd.to_numeric(r.get("prob_home"), errors="coerce") or 0,
                                 pd.to_numeric(r.get("prob_away"), errors="coerce") or 0) >= 70
        else ("Medium" if max(pd.to_numeric(r.get("prob_home"), errors="coerce") or 0,
                              pd.to_numeric(r.get("prob_away"), errors="coerce") or 0) >= 60 else "Low"),
        axis=1,
    )
    card["pair_key"] = card.apply(
        lambda r: "|".join(sorted([str(r.get("player_home", "")).strip().lower(), str(r.get("player_away", "")).strip().lower()])),
        axis=1,
    )
    card = card[card["match_type"] == "Singles"]
    card = card[card["tour"].isin(["ATP", "WTA", "CHALLENGER", "ITF-M", "ITF-W", "UTR"])]
    if card.empty:
        return card.reset_index(drop=True)

    grouped_rows = []
    for (_, pair_key), g in card.groupby(["match_date", "pair_key"], dropna=False):
        first = g.iloc[0]
        winners = []
        for _, rr in g.iterrows():
            pick = str(rr.get("predicted_winner", "") or "")
            if pick == "1":
                winners.append("player_a")
            elif pick == "2":
                winners.append("player_b")
        unique_winners = sorted(set(winners))
        if len(unique_winners) > 1:
            cross_source_agree = "Disagree"
        elif len(unique_winners) == 1 and len(g["source"].unique()) > 1:
            cross_source_agree = "Both"
        elif len(unique_winners) == 1:
            cross_source_agree = "MarketOnly"
        else:
            cross_source_agree = "Unknown"

        selected_pick = unique_winners[0] if unique_winners else ("player_a" if str(first.get("predicted_winner", "")) == "1" else "player_b")
        source_count = int(g["source"].nunique())
        home_probs = pd.to_numeric(g.get("prob_home", pd.Series(dtype=float)), errors="coerce")
        away_probs = pd.to_numeric(g.get("prob_away", pd.Series(dtype=float)), errors="coerce")
        max_home = home_probs.max() if not home_probs.empty else None
        max_away = away_probs.max() if not away_probs.empty else None
        probs = [p for p in [max_home, max_away] if pd.notna(p)]
        max_prob = max(probs) if probs else None
        fav_odds_band = prob_to_odds_band(max_prob)
        series_value = first.get("_series")
        if (pd.isna(series_value) or str(series_value).strip() in {"", "ATP", "WTA", "UNKNOWN"}) and str(first.get("context_used", "")).strip():
            _, series_value = infer_tour_and_series(first.get("context_used", ""), row=first)

        grouped_rows.append({
            "match_date": first.get("match_date"),
            "player_home": first.get("player_home"),
            "player_away": first.get("player_away"),
            "player_a": first.get("player_home"),
            "player_b": first.get("player_away"),
            "tour": first.get("tour"),
            "_series": series_value,
            "_surface": first.get("_surface"),
            "fav_odds_band": fav_odds_band,
            "tournament": first.get("tournament"),
            "context_used": first.get("context_used"),
            "predicted_winner": selected_pick,
            "predicted_winner_foretennis": selected_pick,
            "cross_source_agree": cross_source_agree,
            "pred_confidence": "High" if (max_prob is not None and max_prob >= 70) else ("Medium" if (max_prob is not None and max_prob >= 60) else "Low"),
            "source": ", ".join(sorted(set(map(str, g["source"])))),
            "source_count": source_count,
        })
    return pd.DataFrame(grouped_rows).reset_index(drop=True)


def classify_bucket(best_pick: dict) -> str:
    verdict = str(best_pick.get("Verdict", ""))
    if verdict == "EDGE CONFIRMED":
        return "CERTIFIED_CLEAN"
    if verdict == "WATCHLIST":
        return "WATCHLIST"
    if verdict == "FADE THIS SIGNAL":
        return "CAUTION"
    return "WATCHLIST"


def select_player_from_row(row: pd.Series, target_date: str) -> dict:
    home_name = row.get("player_a", row.get("player_home", "A"))
    away_name = row.get("player_b", row.get("player_away", "B"))
    selected_pick = row.get("predicted_winner")
    selected_player = None
    if selected_pick in {"player_a", "player_b"}:
        selected_player = home_name if selected_pick == "player_a" else away_name
    elif selected_pick in {"1", "2"}:
        selected_player = home_name if selected_pick == "1" else away_name
    else:
        selected_pick = row.get("predicted_winner_foretennis")
        if selected_pick in {"player_a", "player_b"}:
            selected_player = home_name if selected_pick == "player_a" else away_name
        elif pd.isna(selected_pick) or selected_pick == "":
            oa, ob = row.get("odds_a"), row.get("odds_b")
            if pd.notna(oa) and pd.notna(ob):
                selected_pick = "player_a" if oa <= ob else "player_b"
                selected_player = home_name if selected_pick == "player_a" else away_name

        if selected_pick in {"1", "2"}:
            selected_player = home_name if selected_pick == "1" else away_name

    return {
        "match": f"{home_name} vs {away_name}",
        "date": str(row.get("match_date", target_date)),
        "selected_side": selected_pick,
        "selected_player": selected_player,
        "tournament": row.get("tournament"),
        "source": row.get("source"),
        "source_count": row.get("source_count"),
        "tour": row.get("tour"),
        "series": row.get("_series"),
        "surface": row.get("_surface"),
        "pred_confidence": row.get("pred_confidence"),
        "cross_source_agree": row.get("cross_source_agree"),
        "context_used": row.get("context_used"),
        "player_home": home_name,
        "player_away": away_name,
    }


def write_official_pick_outputs(target_date: str, picks: list[dict]) -> None:
    out_dir = ROOT / "localdata"
    out_dir.mkdir(parents=True, exist_ok=True)
    today_path = out_dir / "picks_today.json"
    archive_path = out_dir / f"picks_{target_date}.json"
    payload = json.dumps(picks, indent=2)
    today_path.write_text(payload)
    archive_path.write_text(payload)
    logger.info("Wrote %d official picks to %s and %s", len(picks), today_path, archive_path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine the warehouse for automated edges")
    ap.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse")
    ap.add_argument("--min-n", type=int, default=15,
                    help="Minimum matches per slice (default 15)")
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD to extract specific picks (default: today)")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.warehouse, low_memory=False)
    except Exception as e:
        logger.error("Could not load warehouse: %s", e)
        return 1

    df['selected_rank_band'] = df.apply(get_selected_side_rank_band, axis=1)
    df['fav_odds'] = df.apply(
        lambda r: r['odds_a'] if pd.notna(r.get('odds_a')) and pd.notna(r.get('odds_b'))
                  and r['odds_a'] < r['odds_b'] else r.get('odds_b'), axis=1
    )
    df['fav_odds_band'] = df['fav_odds'].apply(get_odds_band)

    if 'prediction_prob' in df.columns:
        df['pred_confidence'] = df['prediction_prob'].apply(get_confidence_band)

    if 'predicted_winner_foretennis' in df.columns:
        df['cross_source_agree'] = df.apply(get_cross_source_agree, axis=1)
        logger.info("Cross-source agree distribution: %s",
                    df['cross_source_agree'].value_counts().to_dict())

    dimensions = {
        "tour": df['tour'].unique(),
        "_surface": df['_surface'].unique(),
        "fav_odds_band": df['fav_odds_band'].unique(),
        "selected_rank_band": df['selected_rank_band'].unique(),
        "_series": df['_series'].unique(),
    }

    if 'predicted_winner_foretennis' in df.columns:
        dimensions['predicted_winner_foretennis'] = df['predicted_winner_foretennis'].unique()
    if 'pred_confidence' in df.columns:
        dimensions['pred_confidence'] = df['pred_confidence'].unique()
    if 'cross_source_agree' in df.columns:
        dimensions['cross_source_agree'] = df['cross_source_agree'].unique()

    for k, v in dimensions.items():
        dimensions[k] = [x for x in v if pd.notna(x) and x != "Unknown" and x != ""]

    logger.info("Mining for Bankers and Robbers across %d dimensions...", len(dimensions))

    results = []
    dim_names = list(dimensions.keys())

    min_dims = 3
    max_dims = min(5, len(dim_names))
    logger.info("Evaluating dimension combinations from %dD to %dD...", min_dims, max_dims)

    seen_signatures = set()
    for r in range(min_dims, max_dims + 1):
        for subset in combinations(dim_names, r):
            subset = list(subset)
            subset_df = df.copy()
            for d in subset:
                subset_df = subset_df[~subset_df[d].isin(["Unknown", ""])]
                subset_df = subset_df.dropna(subset=[d])

            if subset_df.empty:
                continue

            for combo, slice_df in subset_df.groupby(subset):
                if not isinstance(combo, tuple):
                    combo = (combo,)
                if len(slice_df) < args.min_n:
                    continue

                res = assay_segment(slice_df)
                if res.grade not in ["GOLD", "PLATINUM", "SILVER"] and res.tier != "ROBBER":
                    continue

                combo_dict = dict(zip(subset, combo))
                signature = tuple(sorted(combo_dict.items()))
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)

                results.append({
                    "Slice": " | ".join([f"{n}:{v}" for n, v in combo_dict.items()]),
                    "Combo_Dict": combo_dict,
                    "Dims": len(combo_dict),
                    "N": res.n,
                    "WinRate": f"{res.win_rate:.2%}",
                    "Shrunk": f"{res.shrunk_rate:.2%}",
                    "ROI": f"{res.roi:.2%}",
                    "Grade": res.grade,
                    "Tier": res.tier,
                    "Verdict": res.verdict
                })

    if not results:
        logger.info("No high-conviction edges found.")
        report = []
    else:
        report = pd.DataFrame(results)
        report["ROI_num"] = report["ROI"].str.rstrip('%').astype(float)
        report = report.sort_values(["Verdict", "ROI_num", "N", "Dims"], ascending=[True, False, False, True])

        print("\n" + "="*120)
        print("🚀 RACKET FACTORY EDGE MINER: SIGNAL INTELLIGENCE MODE")
        print("="*120)
        print(report.drop(columns=["Combo_Dict", "ROI_num"]).to_string(index=False))
        print("="*120 + "\n")
        results = report.to_dict("records")

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")
    today_all = df[df["match_date"] == target_date].copy()

    today_df = today_all.copy()
    if "winner" in today_df.columns:
        today_df = today_df[today_df["winner"].isna() | (today_df["winner"].astype(str).str.strip() == "")]
    if "selected_rank_band" in today_df.columns:
        if "_comment" in today_df.columns:
            live_injected_mask = today_df["_comment"].astype(str).str.strip().eq("live_upcoming_injected")
            today_df = today_df[(today_df["selected_rank_band"] != "Unknown") | live_injected_mask]
        else:
            today_df = today_df[today_df["selected_rank_band"] != "Unknown"]

    if today_df.empty:
        fallback = today_all.copy()
        pred_mask = False
        for col in ["predicted_winner", "predicted_winner_foretennis", "predicted_winner_market"]:
            if col in fallback.columns:
                mask = fallback[col].notna() & (fallback[col].astype(str).str.strip() != "")
                pred_mask = mask if isinstance(pred_mask, bool) else (pred_mask | mask)
        if not isinstance(pred_mask, bool):
            fallback = fallback[pred_mask]
        if "selected_rank_band" in fallback.columns:
            if "_comment" in fallback.columns:
                live_injected_mask = fallback["_comment"].astype(str).str.strip().eq("live_upcoming_injected")
                fallback = fallback[(fallback["selected_rank_band"] != "Unknown") | live_injected_mask]
            else:
                fallback = fallback[fallback["selected_rank_band"] != "Unknown"]
        if "fav_odds_band" in fallback.columns:
            fallback = fallback[fallback["fav_odds_band"] != "Unknown"]
        if "tour" in fallback.columns:
            fallback = fallback[fallback["tour"].notna() & (fallback["tour"].astype(str).str.strip() != "")]
        if "_series" in fallback.columns:
            fallback = fallback[fallback["_series"].notna() & (fallback["_series"].astype(str).str.strip() != "")]
        today_df = fallback
        logger.info("Today candidate rows after live filtering: 0; fallback prediction-bearing rows: %d", len(today_df))
        if today_df.empty:
            upcoming_df = build_upcoming_fallback_card(target_date)
            logger.info("Upcoming-card fallback rows: %d", len(upcoming_df))
            if not upcoming_df.empty:
                today_df = upcoming_df
    else:
        logger.info("Today candidate rows after live filtering: %d", len(today_df))

    picks_to_export = []
    if not today_df.empty and results:
        for _, row in today_df.iterrows():
            best_pick = None
            best_roi = -999.0

            for res in results:
                combo = res["Combo_Dict"]
                match_all = True
                for dim_name, dim_val in combo.items():
                    if row.get(dim_name) != dim_val:
                        match_all = False
                        break

                if match_all:
                    slice_roi = float(str(res["ROI"]).strip('%')) / 100.0
                    verdict_rank = {"EDGE CONFIRMED": 3, "WATCHLIST": 2, "FADE THIS SIGNAL": 1}.get(res["Verdict"], 0)
                    best_verdict_rank = -1 if best_pick is None else {"EDGE CONFIRMED": 3, "WATCHLIST": 2, "FADE THIS SIGNAL": 1}.get(best_pick["Verdict"], 0)
                    best_dims = -1 if best_pick is None else int(best_pick.get("Dims", 0))
                    cur_dims = int(res.get("Dims", 0))
                    if (
                        best_pick is None
                        or verdict_rank > best_verdict_rank
                        or (verdict_rank == best_verdict_rank and cur_dims > best_dims)
                        or (verdict_rank == best_verdict_rank and cur_dims == best_dims and slice_roi > best_roi)
                    ):
                        best_roi = slice_roi
                        best_pick = res

            if best_pick and best_pick["Verdict"] in {"EDGE CONFIRMED", "WATCHLIST", "FADE THIS SIGNAL"}:
                base = select_player_from_row(row, target_date)
                prob = row.get("prediction_prob")
                if pd.isna(prob):
                    prob = row.get("pred_confidence")
                if pd.isna(prob):
                    prob = None
                base.update({
                    "bucket": classify_bucket(best_pick),
                    "pick": best_pick["Verdict"],
                    "odds": row.get("fav_odds"),
                    "confidence": prob,
                    "slice_matched": best_pick["Slice"],
                    "edge_dims": best_pick.get("Dims"),
                    "edge_n": best_pick.get("N"),
                    "edge_grade": best_pick.get("Grade"),
                    "edge_tier": best_pick.get("Tier"),
                    "edge_verdict": best_pick.get("Verdict"),
                    "roi_estimate": best_pick.get("ROI"),
                })
                picks_to_export.append(base)

    picks_to_export = sorted(
        picks_to_export,
        key=lambda p: (
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "CAUTION": 2}.get(str(p.get("bucket")), 9),
            -int(p.get("source_count") or 0),
            str(p.get("match")),
        )
    )

    write_official_pick_outputs(target_date, picks_to_export)
    logger.info("Exported %d actionable picks for %s", len(picks_to_export), target_date)
    return 0


if __name__ == "__main__":
    main()
