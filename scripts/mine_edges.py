#!/usr/bin/env python3
"""
Racket Factory Edge Miner (Ma Golide Enhanced)
Automated combinatorial discovery of Bankers and Robbers, including prediction signals.

WARNING: ROI is currently calculated using Market Closing Odds. AI predictions captured early in the day must be evaluated against Opening Odds before live capital is deployed.
"""
import os
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
from racketfactory.warehouse import (
    coerce_decimal_odds,
    fetch_the_odds_api_rows,
    live_player_key,
    names_match,
    odds_suspicious_for_probability,
    valid_two_way_decimal_pair,
)
from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.forebet import ForebetPredictor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("edge_miner")


def get_player_rank_band(rank: float | None) -> str:
    if rank is None or pd.isna(rank): return "Unknown"
    try:
        rank_float = float(rank)
    except (ValueError, TypeError):
        return "Unknown"
        
    if pd.isna(rank_float): return "Unknown"
    if rank_float <= 10: return "Top 10"
    if rank_float <= 50: return "11-50"
    if rank_float <= 100: return "51-100"
    return "100+"


def get_selected_side_rank_band(row: pd.Series, pred_cols: list[str]) -> str:
    """Pre-match rank band of the side we would actually back.

    Priority:
      1. primary predictions (`predicted_winner*`)
      2. market favorite as fallback
    Uses pre-match player rank columns only.
    """
    pick = None
    for col in pred_cols:
        val = row.get(col)
        if str(val).strip() not in {"", "nan", "<NA>", "None"}:
            pick = str(val).strip()
            break

    if pick is not None and pick in {"player_a", "player_b", "1", "2"}:
        rank_col = "rank_a" if pick in {"player_a", "1"} else "rank_b"
        if rank_col in row:
            return get_player_rank_band(row.get(rank_col))

    oa, ob = row.get("odds_a"), row.get("odds_b")
    if isinstance(oa, (int, float)) and isinstance(ob, (int, float)) and not pd.isna(oa) and not pd.isna(ob):
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
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return "Unknown"
    if p > 1.0:
        p /= 100.0
    if p >= 0.70: return "High"
    if p >= 0.60: return "Medium"
    return "Low"


def get_cross_source_agree(row: pd.Series, pred_cols: list[str]) -> str:
    """
    Compare Market baseline vs ForeTennis AI predictions and other sources.
    Returns one of: Both | Disagree | MarketOnly | ForeTennisOnly
    """
    mkt = row.get("predicted_winner_market")
    ft = row.get("predicted_winner_foretennis")
    has_mkt = str(mkt).strip() not in {"", "nan", "<NA>", "None"}
    has_ft = str(ft).strip() not in {"", "nan", "<NA>", "None"}
    if has_mkt and has_ft:
        return "Both" if mkt == ft else "Disagree"
    if has_mkt:
        return "MarketOnly"
    if has_ft:
        return "ForeTennisOnly"

    # Check all available predicted_winner columns for live upcoming matches
    picks = set()
    sources_count = 0
    for col in pred_cols:
        val = row.get(col)
        if str(val).strip() not in {"", "nan", "<NA>", "None"}:
            picks.add(str(val).strip())
            sources_count += 1

    if len(picks) > 1:
        return "Disagree"
    elif len(picks) == 1 and sources_count > 1:
        return "Both"
    elif len(picks) == 1:
        return "MarketOnly"

    return "Unknown"


def infer_tour_and_series(text: str, row: pd.Series | None = None) -> tuple[str, str]:
    lower = str(text or "").lower()
    
    tour = "UNKNOWN"
    if any(x in lower for x in ["wta", "women", "girls"]): tour = "WTA"
    elif any(x in lower for x in ["atp", "men", "boys"]): tour = "ATP"
    elif any(x in lower for x in ["challenger"]): tour = "CHALLENGER"
    elif "itf" in lower: tour = "ITF-M" if any(w in lower for w in ["men", " m ", "-m"]) else "ITF-W"
    elif "utr" in lower: tour = "UTR"
    
    if row is not None and tour == "UNKNOWN":
        tour_val = str(row.get("tour", "")).upper()
        if tour_val in ("ATP", "WTA", "CHALLENGER", "ITF-M", "ITF-W", "UTR"):
            tour = tour_val

    if any(x in lower for x in ["wimbledon", "roland garros", "us open", "australian open", "grand slam"]):
        return (tour if tour != "UNKNOWN" else "ATP", "Grand Slam")
        
    if any(x in lower for x in ["challenger", "piracicaba", "targu mures"]):
        return ("CHALLENGER", "Challenger")
    if "itf" in lower:
        return (tour if tour != "UNKNOWN" else "ITF-M", "ITF")
    if "utr" in lower:
        return ("UTR", "UTR")
        
    if tour == "WTA":
        if any(x in lower for x in ["wta 1000", "madrid", "rome", "miami", "indian wells", "beijing", "wuhan", "cincinnati", "toronto", "montreal", "doha", "dubai"]):
            return ("WTA", "WTA1000")
        if any(x in lower for x in ["wta 500", "premier", "eastbourne", "bad homburg", "stuttgart", "berlin", "charleston", "san diego", "abudhabi", "abu dhabi", "brisbane", "adelaide", "tokyo", "zhengzhou", "ningbo", "monterrey", "strasbourg"]):
            return ("WTA", "Premier")
        if any(x in lower for x in ["wta 250", "international", "mallorca", "birmingham", "nottingham", "s-hertogenbosch", "hertogenbosch", "palermo", "budapest", "prague", "warsaw", "hamburg", "cluj", "monastir", "jiujiang", "linz", "rouen", "rabat", "bogota", "austin", "hobart", "auckland", "hua hin", "merida", "guangzhou"]):
            return ("WTA", "International")
        return ("WTA", "International")
        
    if tour == "ATP":
        if any(x in lower for x in ["masters 1000", "atp 1000", "madrid", "rome", "miami", "indian wells", "monte carlo", "monte-carlo", "cincinnati", "toronto", "montreal", "shanghai", "paris"]):
            return ("ATP", "Masters 1000")
        if any(x in lower for x in ["atp 500", "halle", "queens", "queen's", "hamburg", "washington", "beijing", "tokyo", "basel", "vienna", "acapulco", "dubai", "rotterdam", "rio", "barcelona"]):
            return ("ATP", "ATP500")
        if any(x in lower for x in ["atp 250", "eastbourne", "mallorca", "mallorca championships", "s-hertogenbosch", "hertogenbosch", "stuttgart", "geneva", "lyon", "estoril", "marrakech", "houston", "munich", "bucharest", "båstad", "bastad", "gstaad", "newport", "umag", "atlanta", "kitzbühel", "kitzbuhel", "los cabos", "winston-salem", "chengdu", "zhuhai", "astana", "almaty", "antwerp", "stockholm", "metz", "sofia", "brisbane", "adelaide", "auckland", "cordoba", "buenos aires", "delray beach", "santiago", "marseille", "doha"]):
            return ("ATP", "ATP250")
        return ("ATP", "ATP250")

    return ("UNKNOWN", "UNKNOWN")


def infer_surface(text: str, current_surface: str = "") -> str:
    if str(current_surface).strip() in ("Hard", "Clay", "Grass"):
        return str(current_surface).strip()
    lower = str(text or "").lower()
    if any(x in lower for x in ["wimbledon", "eastbourne", "mallorca", "bad homburg", "s-hertogenbosch", "hertogenbosch", "queens", "queen's", "halle", "birmingham", "nottingham", "berlin", "newport", "grass"]):
        return "Grass"
    if any(x in lower for x in ["french open", "roland garros", "madrid", "rome", "monte carlo", "monte-carlo", "barcelona", "estoril", "munich", "geneva", "lyon", "båstad", "bastad", "gstaad", "umag", "kitzbühel", "kitzbuhel", "hamburg", "palermo", "budapest", "prague", "bogota", "rabat", "marrakech", "santiago", "cordoba", "buenos aires", "iasi", "brasov", "clay"]):
        return "Clay"
    if any(x in lower for x in ["australian open", "us open", "indian wells", "miami", "cincinnati", "toronto", "montreal", "shanghai", "paris", "beijing", "tokyo", "doha", "dubai", "acapulco", "rotterdam", "basel", "vienna", "washington", "winston-salem", "los cabos", "atlanta", "chengdu", "zhuhai", "astana", "almaty", "stockholm", "antwerp", "metz", "sofia", "brisbane", "adelaide", "auckland", "delray beach", "dallas", "marseille", "montpellier", "monastir", "ningbo", "seoul", "hong kong", "cluj", "jiujiang", "linz", "rouen", "austin", "hobart", "hua hin", "merida", "guangzhou", "finals", "hard"]):
        return "Hard"
    return "Hard"


def prob_to_odds_band(prob_pct: float) -> str:
    if pd.isna(prob_pct):
        return "Unknown"
    if prob_pct <= 1.0 and prob_pct > 0:
        prob_pct *= 100.0
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



def forebet_day_for_target(target_date: str) -> str:
    """Map a target date to Forebet daily page name."""
    try:
        target = datetime.strptime(str(target_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return "today"
    today = datetime.now().date()
    if target == today - timedelta(days=1):
        return "yesterday"
    if target == today + timedelta(days=1):
        return "tomorrow"
    return "today"


def enrich_fallback_card_with_api_odds(card: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """Attach The Odds API prices to fallback forecast card rows.

    This makes tomorrow/future forecast rows priceable without needing them to
    exist as live-injected warehouse rows first.
    """
    if card.empty:
        return card

    odds_rows = fetch_the_odds_api_rows(target_date)
    if not odds_rows:
        logger.info("No The Odds API odds available for fallback card %s", target_date)
        return card

    out = card.copy()
    matched = 0

    for idx, row in out.iterrows():
        home = str(row.get("player_home") or row.get("player_a") or "").strip()
        away = str(row.get("player_away") or row.get("player_b") or "").strip()
        if not home or not away:
            continue

        for odds_row in odds_rows:
            api_home = str(odds_row.get("player_home") or "").strip()
            api_away = str(odds_row.get("player_away") or "").strip()

            normal = names_match(home, api_home) and names_match(away, api_away)
            reverse = names_match(home, api_away) and names_match(away, api_home)
            if not (normal or reverse):
                continue

            if normal:
                odds_home = odds_row.get("odds_home")
                odds_away = odds_row.get("odds_away")
            else:
                odds_home = odds_row.get("odds_away")
                odds_away = odds_row.get("odds_home")

            out.loc[idx, "odds_home"] = odds_home
            out.loc[idx, "odds_away"] = odds_away
            out.loc[idx, "odds_a"] = odds_home
            out.loc[idx, "odds_b"] = odds_away
            out.loc[idx, "_odds_source"] = "TheOddsAPI"
            out.loc[idx, "_is_live"] = True
            out.loc[idx, "_comment"] = "forecast_upcoming_api_priced"
            matched += 1
            break

    logger.info("Matched The Odds API odds for %d/%d fallback forecast rows on %s", matched, len(out), target_date)
    return out

def build_upcoming_fallback_card(target_date: str) -> pd.DataFrame:
    rows = []
    forebet_day = forebet_day_for_target(target_date)
    for source_name, predictor, fetcher in [
        ("PredixSport", PredixSportPredictor(), lambda p: p.fetch_daily()),
        ("BetClan", BetClanPredictor(), lambda p: p.fetch_daily()),
        ("Forebet", ForebetPredictor(), lambda p: p.fetch_daily_predictions(forebet_day)),
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
    card["surface"] = card.get("surface", pd.Series(index=card.index, dtype=object)).astype(str).str.strip().str.title() # type: ignore
    card.loc[card["surface"].isin(["", "Nan", "None"]), "surface"] = ""
    card["_surface"] = card.apply(lambda r: infer_surface(r.get("context_used", "") or r.get("tournament", ""), r.get("surface", "")), axis=1)
    card["pred_confidence"] = card.apply(
        lambda r: "High" if max(pd.to_numeric(r.get("prob_home"), errors="coerce") or 0,
                                 pd.to_numeric(r.get("prob_away"), errors="coerce") or 0) >= 70
        else ("Medium" if max(pd.to_numeric(r.get("prob_home"), errors="coerce") or 0,
                              pd.to_numeric(r.get("prob_away"), errors="coerce") or 0) >= 60 else "Low"),
        axis=1,
    )
    card["pair_key"] = card.apply(
        lambda r: "|".join(sorted([live_player_key(r.get("player_home", "")), live_player_key(r.get("player_away", ""))])),
        axis=1,
    )
    card = card[card["match_type"] == "Singles"]
    card = card[card["tour"].isin(["ATP", "WTA", "CHALLENGER", "ITF-M", "ITF-W", "UTR"])]
    if card.empty:
        return card.reset_index(drop=True)

    grouped_rows = []
    for (_, pair_key), g in card.groupby(["match_date", "pair_key"], dropna=False): # type: ignore
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
        max_home = float(home_probs.max()) if hasattr(home_probs, 'empty') and not home_probs.empty else None # type: ignore
        max_away = float(away_probs.max()) if hasattr(away_probs, 'empty') and not away_probs.empty else None # type: ignore
        probs = [p for p in [max_home, max_away] if p is not None and not pd.isna(p)]
        max_prob = max(probs) if probs else None
        selected_prob = max_home if selected_pick == "player_a" else max_away
        if selected_prob is None or str(selected_prob).strip() in {"nan", "<NA>", "None"}:
            selected_prob = max_prob
        fav_odds_band = prob_to_odds_band(max_prob)
        series_value = first.get("_series")
        if (pd.isna(series_value) or str(series_value).strip() in {"", "ATP", "WTA", "UNKNOWN"}) and str(first.get("context_used", "")).strip():
            _, series_value = infer_tour_and_series(first.get("context_used", ""), row=first)

        grouped_rows.append({
            "match_date": first.get("match_date"),
            "match_time": first.get("match_time", ""),
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
            "prediction_prob": selected_prob,
            "prediction_prob_foretennis": selected_prob,
            "prob_home": max_home,
            "prob_away": max_away,
            "cross_source_agree": cross_source_agree,
            "pred_confidence": "High" if (max_prob is not None and max_prob >= 70) else ("Medium" if (max_prob is not None and max_prob >= 60) else "Low"),
            "_comment": "forecast_upcoming_fallback",
            "_is_live": True,
            "source": ", ".join(sorted(set(map(str, g["source"])))),
            "source_count": source_count,
        })
    out_df = pd.DataFrame(grouped_rows).reset_index(drop=True)
    out_df = enrich_fallback_card_with_api_odds(out_df, target_date)
    return out_df


def classify_bucket(best_pick: dict) -> str:
    verdict = str(best_pick.get("Verdict", ""))
    if verdict == "EDGE CONFIRMED":
        return "CERTIFIED_CLEAN"
    if verdict == "WATCHLIST":
        return "WATCHLIST"
    # REDTEAM Finding #1: ROBBER/FADE verdicts must NEVER be exported as a
    # pick. The old mapping ("FADE THIS SIGNAL" -> "CAUTION") was the root
    # cause of the 2026-06-28 run shipping 48 negatively-biased picks.
    if verdict == "FADE THIS SIGNAL":
        return "SKIPPED_DEAD_EDGE"
    # NO STAT SIG / NEUTRAL / unknown verdicts are not actionable.
    return "WATCHLIST_UNKNOWN_CTX"


def normalize_side_token(value: object) -> str | None:
    text = str(value or "").strip()
    if text in {"player_a", "1"}:
        return "player_a"
    if text in {"player_b", "2"}:
        return "player_b"
    return None


def odds_for_selected_side(row: pd.Series, selected_side: object) -> float | None:
    side = normalize_side_token(selected_side)
    if side == "player_a":
        return coerce_decimal_odds(row.get("odds_a"))
    if side == "player_b":
        return coerce_decimal_odds(row.get("odds_b"))
    return None


def row_has_live_flag(row: pd.Series) -> bool:
    value = row.get("_is_live")
    try:
        if str(value).strip().lower() in {"true", "1", "yes"}:
            return True
    except Exception:
        pass
    return str(row.get("_comment", "")).strip() == "live_upcoming_injected"


def selected_odds_is_usable(row: pd.Series, selected_side: object, probability: object) -> tuple[float | None, str | None]:
    """Return selected-side odds, repairing likely live side inversions."""
    odds_val = odds_for_selected_side(row, selected_side)
    if odds_val is None:
        return None, "missing selected-side odds"

    if row_has_live_flag(row):
        odds_source = str(row.get("_odds_source", "") or "")
        usable_live_sources = {"TheOddsAPI", "ScrapedFallback"}
        if odds_source not in usable_live_sources:
            return None, "missing usable live odds"
        if not valid_two_way_decimal_pair(row.get("odds_a"), row.get("odds_b")):
            return None, f"incomplete/invalid {odds_source} live odds pair"
        side = normalize_side_token(selected_side)
        other_odds = coerce_decimal_odds(row.get("odds_b" if side == "player_a" else "odds_a"))
        if (
            other_odds is not None
            and odds_suspicious_for_probability(probability, odds_val)
            and not odds_suspicious_for_probability(probability, other_odds)
        ):
            return other_odds, f"corrected likely side-inverted {odds_source} live odds"

    return odds_val, None


def select_player_from_row(row: pd.Series, target_date: str) -> dict:
    home_name = row.get("player_a", row.get("player_home", "A"))
    away_name = row.get("player_b", row.get("player_away", "B"))

    selected_pick = None
    selected_player = None

    pred_cols = [c for c in row.index if c.startswith("predicted_winner")]
    for col in pred_cols:
        val = row.get(col)
        if str(val).strip() not in {"", "nan", "<NA>", "None"}:
            selected_pick = str(val).strip()
            break

    if selected_pick in {"player_a", "player_b"}:
        selected_player = home_name if selected_pick == "player_a" else away_name
    elif selected_pick in {"1", "2"}:
        selected_player = home_name if selected_pick == "1" else away_name
    else:
        oa, ob = row.get("odds_a"), row.get("odds_b")
        if isinstance(oa, (int, float)) and isinstance(ob, (int, float)) and not pd.isna(oa) and not pd.isna(ob):
            selected_pick = "player_a" if oa <= ob else "player_b"
            selected_player = home_name if selected_pick == "player_a" else away_name
        else:
            selected_pick = "player_a"
            selected_player = home_name

    source_val = row.get("source", "")
    source_count = row.get("source_count")
    if source_count is None or str(source_count).strip() in {"nan", "<NA>", "None"}:
        source_count = len([c for c in pred_cols if str(row.get(c)).strip() not in {"", "nan", "<NA>", "None"}])

    ctx_val = row.get("context_used")
    if ctx_val is None or str(ctx_val).strip() in {"nan", "<NA>", "None", ""}:
        ctx_val = row.get("tournament", "")
        
    time_val = row.get("match_time")
    if time_val is None or str(time_val).strip() in {"nan", "<NA>", "None", ""}:
        time_val = "n/a"

    return {
        "match": f"{home_name} vs {away_name}",
        "date": str(row.get("match_date", target_date)),
        "match_time": str(time_val),
        "kickoff": str(time_val),
        "selected_side": selected_pick,
        "selected_player": selected_player,
        "tournament": row.get("tournament"),
        "source": source_val,
        "source_count": source_count,
        "tour": row.get("tour"),
        "series": row.get("_series"),
        "surface": row.get("_surface"),
        "pred_confidence": row.get("pred_confidence"),
        "cross_source_agree": row.get("cross_source_agree"),
        "context_used": ctx_val,
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



def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _is_future_target(target_date: str) -> bool:
    try:
        target = datetime.strptime(str(target_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return target > datetime.now().date()


def _pick_float(pick: dict, key: str) -> float | None:
    value = pick.get(key)
    if value is None:
        return None
    try:
        result = float(str(value))
        if pd.isna(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def apply_forecast_hygiene(target_date: str, picks: list[dict]) -> list[dict]:
    """Reduce future fallback ledgers to a priced, positive-EV review list.

    This only applies to future dates. Same-day picks are untouched.

    Forecast rows are inherently weaker than same-day live rows because they can
    come from sparse fallback source cards. The goal is to avoid dumping every
    priced prediction into tomorrow's report while keeping the strongest
    positive-EV items for operator review.
    """
    if not _is_future_target(target_date):
        return picks

    min_conf = _env_float("RACKET_FACTORY_FORECAST_MIN_CONF", 60.0)
    min_ev = _env_float("RACKET_FACTORY_FORECAST_MIN_EV", 0.05)
    max_rows = max(1, _env_int("RACKET_FACTORY_FORECAST_MAX_ROWS", 20))
    include_no_odds = _env_bool("RACKET_FACTORY_FORECAST_INCLUDE_NO_ODDS", False)
    include_skipped = _env_bool("RACKET_FACTORY_FORECAST_INCLUDE_SKIPPED", False)

    actionable_buckets = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}
    no_odds_buckets = {"WATCHLIST_NO_ODDS", "WATCHLIST_UNKNOWN_CTX"}
    skipped_buckets = {"SKIPPED_DEAD_EDGE", "SKIPPED_VETO"}

    kept: list[dict] = []
    dropped = 0

    for pick in picks:
        bucket = str(pick.get("bucket") or "")
        ev = _pick_float(pick, "expected_value")
        conf = _pick_float(pick, "confidence")
        odds = _pick_float(pick, "odds")

        if conf is not None and 0 < conf <= 1.0:
            conf *= 100.0

        keep = True
        reason = ""

        if bucket in skipped_buckets and not include_skipped:
            keep = False
            reason = "forecast skipped bucket hidden"
        elif bucket in no_odds_buckets and not include_no_odds:
            keep = False
            reason = "forecast no-odds bucket hidden"
        elif bucket in actionable_buckets:
            if odds is None:
                keep = False
                reason = "forecast missing odds"
            elif ev is None:
                keep = False
                reason = "forecast missing EV"
            elif ev < min_ev:
                keep = False
                reason = f"forecast EV {ev:.3f} < {min_ev:.3f}"
            elif conf is None:
                keep = False
                reason = "forecast missing confidence"
            elif conf < min_conf:
                keep = False
                reason = f"forecast confidence {conf:.1f}% < {min_conf:.1f}%"

        if keep:
            pick = dict(pick)
            pick["ledger_kind"] = "forecast"
            pick["forecast_hygiene"] = {
                "min_conf": min_conf,
                "min_ev": min_ev,
                "max_rows": max_rows,
                "include_no_odds": include_no_odds,
                "include_skipped": include_skipped,
            }
            kept.append(pick)
        else:
            dropped += 1
            logger.debug("Forecast hygiene dropped %s -> %s (%s)", pick.get("match"), pick.get("selected_player"), reason)

    kept = sorted(
        kept,
        key=lambda p: (
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "CAUTION": 2}.get(str(p.get("bucket")), 9),
            -float(p.get("expected_value") or 0.0),
            -float(p.get("confidence") or 0.0),
            str(p.get("match") or ""),
        ),
    )

    if len(kept) > max_rows:
        dropped += len(kept) - max_rows
        kept = kept[:max_rows]

    logger.info(
        "Forecast hygiene for %s: kept %d, dropped %d (min_conf=%.1f, min_ev=%.3f, max_rows=%d)",
        target_date,
        len(kept),
        dropped,
        min_conf,
        min_ev,
        max_rows,
    )
    return kept

def main() -> int:
    ap = argparse.ArgumentParser(description="Mine the warehouse for automated edges")
    ap.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse")
    ap.add_argument("--min-n", type=int, default=15,
                    help="Minimum matches per slice (default 15)")
    ap.add_argument("--min-export-n", type=int, default=50,
                    help="REDTEAM Finding #5: minimum historical N required for a slice to be "
                         "considered exportable as a live pick. Default 50 — anything smaller is "
                         "kept for transparency in the slice report but cannot become a pick.")
    ap.add_argument("--min-ev", type=float, default=0.0,
                    help="REDTEAM Finding #4: minimum per-bet expected value (decimal) required "
                         "for a pick to be exported. EV = conf*(odds-1) - (1-conf). Default 0.0 "
                         "(drop non-positive-EV bets). Set to negative to disable.")
    ap.add_argument("--bet-side", choices=["favorite", "prediction"], default="favorite",
                    help="REDTEAM Finding #3: what side does the slice assay test? 'favorite' "
                         "(default, historical behaviour) or 'prediction' (follow predicted_winner*).")
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD to extract specific picks (default: today)")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.warehouse, low_memory=False)
    except Exception as e:
        logger.error("Could not load warehouse: %s", e)
        return 1

    # REDTEAM Finding #2 (pre-step): identify live-injected rows so we can
    # (a) exclude them from historical slice mining (they have empty winner
    #     and would otherwise count as guaranteed losses), and
    # (b) still include them in today's pick matching so the operator sees
    #     the same-day slate.
    live_mask = pd.Series(False, index=df.index)
    if "_is_live" in df.columns:
        try:
            live_mask = df["_is_live"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
        except Exception:
            live_mask = pd.Series(False, index=df.index)
    if "_comment" in df.columns and not live_mask.any():
        live_mask = df["_comment"].astype(str).str.strip() == "live_upcoming_injected"

    pred_cols = [c for c in df.columns if c.startswith("predicted_winner")]
    prob_cols = [c for c in df.columns if c.startswith("prediction_prob")]

    # REDTEAM Finding #2 (pre-step 2): apply the dimensional derivations to
    # the WHOLE warehouse first, then split into historical and live cohorts.
    # This keeps `fav_odds`, `pred_confidence`, `cross_source_agree`, etc.
    # populated on live rows so the export loop can compute EV correctly.
    df['selected_rank_band'] = df.apply(lambda r: get_selected_side_rank_band(r, pred_cols), axis=1)

    def get_fav_odds(r):
        oa, ob = r.get('odds_a'), r.get('odds_b')
        try: oa = float(str(oa)) if str(oa).strip() not in {"", "nan", "<NA>", "None"} else None
        except (TypeError, ValueError, AttributeError): oa = None
        try: ob = float(str(ob)) if str(ob).strip() not in {"", "nan", "<NA>", "None"} else None
        except (TypeError, ValueError, AttributeError): ob = None
        if oa is not None and ob is not None: return min(oa, ob)
        if oa is not None: return oa
        if ob is not None: return ob
        return None

    df['fav_odds'] = df.apply(get_fav_odds, axis=1)

    def calc_odds_band(row):
        val = row.get('fav_odds')
        band = get_odds_band(val)
        if band != 'Unknown':
            return band
        max_p = None
        for col in prob_cols:
            p_val = row.get(col)
            if str(p_val).strip() not in {"nan", "<NA>", "None"}:
                try:
                    v = float(p_val)
                    if max_p is None or v > max_p: max_p = v
                except (TypeError, ValueError):
                    pass
        if max_p is not None:
            return prob_to_odds_band(max_p)
        return 'Unknown'

    df['fav_odds_band'] = df.apply(calc_odds_band, axis=1)

    def calc_pred_confidence(row):
        max_p = None
        for col in prob_cols:
            p_val = row.get(col)
            if str(p_val).strip() not in {"nan", "<NA>", "None"}:
                try:
                    v = float(p_val)
                    if max_p is None or v > max_p: max_p = v
                except (TypeError, ValueError):
                    pass
        if max_p is not None:
            if max_p > 1.0: max_p /= 100.0
            if max_p >= 0.70: return "High"
            if max_p >= 0.60: return "Medium"
            return "Low"
        return "Unknown"

    df['pred_confidence'] = df.apply(calc_pred_confidence, axis=1)

    df['cross_source_agree'] = df.apply(lambda r: get_cross_source_agree(r, pred_cols), axis=1)
    df['_surface'] = df.apply(lambda r: infer_surface(r.get("tournament", "") or r.get("context_used", ""), r.get("_surface", "")), axis=1)

    if live_mask.any():
        logger.info(
            "REDTEAM guard: %d live-injected rows tagged for pick matching only "
            "(excluded from historical slice mining).",
            int(live_mask.sum()),
        )
    df_live_only = df[live_mask].copy()

    # Now restrict the slice-mining cohort to settled, non-live rows.
    settled_mask = pd.Series(True, index=df.index)
    if "winner" in df.columns:
        settled_mask = (
            df["winner"].notna()
            & (~df["winner"].astype(str).str.strip().isin(["", "nan", "<NA>", "None"]))
        )
    df_hist = df[~live_mask & settled_mask].copy()
    n_uns = int((~settled_mask & ~live_mask).sum())
    if n_uns:
        logger.info(
            "REDTEAM guard: %d historical rows excluded from slice mining "
            "(empty/unsettled `winner`).",
            n_uns,
        )

    df = df_hist  # downstream code operates on the historical cohort only
    # The dimensional derivations (selected_rank_band, fav_odds, fav_odds_band,
    # pred_confidence, cross_source_agree, _surface) were applied to the FULL
    # warehouse above before splitting. Reusing them here on df_hist would
    # be redundant; we only need to keep them in sync when the schema is
    # missing on a row.

    logger.info("Cross-source agree distribution: %s", df['cross_source_agree'].value_counts().to_dict())

    dimensions = {
        "tour": df['tour'].unique(),
        "_surface": df['_surface'].unique(),
        "fav_odds_band": df['fav_odds_band'].unique(),
        "selected_rank_band": df['selected_rank_band'].unique(),
        "_series": df['_series'].unique(),
        "pred_confidence": df['pred_confidence'].unique(),
        "cross_source_agree": df['cross_source_agree'].unique(),
    }

    for k, v in dimensions.items():
        dimensions[k] = [x for x in v if str(x).strip() not in {"nan", "<NA>", "None", "", "Unknown"}]

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
                subset_df = subset_df.dropna(subset=[d]) # type: ignore

            if subset_df.empty:
                continue

            for combo, slice_df in subset_df.groupby(subset):
                if not isinstance(combo, tuple):
                    combo = (combo,)
                if len(slice_df) < args.min_n:
                    continue

                res = assay_segment(slice_df, bet_side=args.bet_side)
                # REDTEAM Finding #1: ROBBER/FADE slices are still mined (so
                # they can be shown in the slice report) but they cannot
                # promote a live row to a pick. We continue to the slice
                # collection but skip the export path later.
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
                    "Verdict": res.verdict,
                    # REDTEAM Finding #5: mark exportability. Only slices that
                    # survive the min-N gate AND are not ROBBER/FADE can be
                    # used as actionable picks. The flag is consumed by the
                    # pick export loop below.
                    "Exportable": (
                        res.tier != "ROBBER"
                        and res.verdict != "FADE THIS SIGNAL"
                        and res.n >= args.min_export_n
                    ),
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
    # REDTEAM Finding #2 follow-up: today's pick candidates need BOTH
    # unsettled historical rows for `target_date` AND live-injected rows for
    # the same date. We excluded live rows from `df` (the slice-mining
    # cohort), but they still have to participate in pick matching so the
    # operator sees actionable matches for today's slate.
    today_hist = df[df["match_date"] == target_date].copy() if not df.empty else df.copy()
    today_live = (
        df_live_only[df_live_only["match_date"] == target_date].copy()
        if not df_live_only.empty else df_live_only.copy()
    )
    if today_hist.empty:
        today_all = today_live.copy()
    elif today_live.empty:
        today_all = today_hist.copy()
    else:
        today_all = pd.concat([today_hist, today_live], ignore_index=True, sort=False) # type: ignore

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
        for col in pred_cols:
            if col in fallback.columns:
                mask = fallback[col].notna() & (~fallback[col].astype(str).str.strip().isin(["", "nan", "<NA>", "None"]))
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
            fallback = fallback[fallback["tour"].notna() & (~fallback["tour"].astype(str).str.strip().isin(["", "nan", "<NA>", "None"]))]
        if "_series" in fallback.columns:
            fallback = fallback[fallback["_series"].notna() & (~fallback["_series"].astype(str).str.strip().isin(["", "nan", "<NA>", "None"]))]
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
                # REDTEAM Finding #1: ROBBER/FADE slices never promote a row
                # to a pick. They are still emitted as SKIPPED_DEAD_EDGE
                # rows further down so the slice is visible, but they cannot
                # be the basis of an actionable bet.
                if not res.get("Exportable", False):
                    continue

                # Only historically actionable verdicts may become picks.
                # Exportable neutral slices are useful for diagnostics, but
                # NO STAT SIG / NEUTRAL must never become WATCHLIST rows.
                if str(res.get("Verdict", "")).strip() not in {"EDGE CONFIRMED", "WATCHLIST"}:
                    continue

                combo = res["Combo_Dict"]
                match_all = True
                for dim_name, dim_val in combo.items():
                    r_val = row.get(dim_name)
                    if str(r_val).replace(" ", "").lower() != str(dim_val).replace(" ", "").lower():
                        # Allow historical series equivalences (International <-> WTA250, Premier <-> WTA500)
                        if dim_name == "_series":
                            d_clean = str(dim_val).replace(" ", "").lower()
                            r_clean = str(r_val).replace(" ", "").lower()
                            if d_clean == "international" and r_clean == "wta250": continue
                            if d_clean == "wta250" and r_clean == "international": continue
                            if d_clean == "premier" and r_clean == "wta500": continue
                            if d_clean == "wta500" and r_clean == "premier": continue
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

            if best_pick is not None:
                base = select_player_from_row(row, target_date)
                prob = None
                for col in prob_cols:
                    pval = row.get(col)
                    if str(pval).strip() not in {"nan", "<NA>", "None"}:
                        try:
                            v = float(pval)
                            if v <= 1.0 and v > 0: v *= 100.0
                            if prob is None or v > prob: prob = v
                        except (TypeError, ValueError):
                            pass

                # Price the actual selected side, not blindly the market favourite.
                # The previous code used `fav_odds` for every exported row, which
                # could misprice underdog predictions and also let bad live scrapes
                # (e.g. @9.50 attached to a 79% favourite) create comedy EV.
                odds_val, odds_reject_reason = selected_odds_is_usable(
                    row, base.get("selected_side"), prob
                )

                # REDTEAM Finding #4: compute per-bet expected value and drop
                # non-positive-EV rows from the actionable set. EV is computed
                # on the decimal odds captured at scrape time, so live picks
                # inherit the same approximation caveat the HANDOVER already
                # documents (closing vs opening odds).
                ev = None
                if prob is not None and odds_val is not None and odds_val > 1.0:
                    p_dec = max(0.0, min(1.0, prob / 100.0))
                    ev = p_dec * (odds_val - 1.0) - (1.0 - p_dec)

                base.update({
                    "bucket": classify_bucket(best_pick),
                    "pick": best_pick["Verdict"],
                    "odds": odds_val,
                    "odds_reject_reason": odds_reject_reason,
                    "confidence": prob,
                    "expected_value": ev,
                    "slice_matched": best_pick["Slice"],
                    "edge_dims": best_pick.get("Dims"),
                    "edge_n": best_pick.get("N"),
                    "edge_grade": best_pick.get("Grade"),
                    "edge_tier": best_pick.get("Tier"),
                    "edge_verdict": best_pick.get("Verdict"),
                    "roi_estimate": best_pick.get("ROI"),
                })

                if odds_val is None and base.get("bucket") in {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}:
                    base["bucket"] = "WATCHLIST_NO_ODDS"
                    base["skip_reason"] = odds_reject_reason or "missing selected-side odds"

                # REDTEAM Finding #4 gate: by default we DO NOT export
                # negative-EV picks. Set --min-ev to a negative number to
                # disable the gate (useful for diagnostics only).
                if ev is not None and ev < args.min_ev:
                    base["bucket"] = "SKIPPED_DEAD_EDGE"
                    base["skip_reason"] = f"negative EV ({ev:.3f} < {args.min_ev:.3f})"

                picks_to_export.append(base)

    picks_to_export = sorted(
        picks_to_export,
        key=lambda p: (
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "CAUTION": 2,
             "SKIPPED_DEAD_EDGE": 3, "WATCHLIST_NO_ODDS": 4, "WATCHLIST_UNKNOWN_CTX": 5,
             "SKIPPED_VETO": 6}.get(str(p.get("bucket")), 9),
            -float(p.get("expected_value") or 0.0),
            -int(p.get("source_count") or 0),
            str(p.get("match")),
        )
    )

    picks_to_export = apply_forecast_hygiene(target_date, picks_to_export)

    write_official_pick_outputs(target_date, picks_to_export)
    logger.info("Exported %d actionable picks for %s", len(picks_to_export), target_date)
    return 0


if __name__ == "__main__":
    main()