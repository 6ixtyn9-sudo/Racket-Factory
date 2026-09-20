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
import hashlib
from datetime import datetime, date, timedelta
from pathlib import Path
from itertools import combinations
import re

# Ensure src is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.assay import assay_segment
from racketfactory.fetch_cache import cached_fetch
from racketfactory.warehouse import (
    align_odds_to_probabilities,
    coerce_decimal_odds,
    fetch_the_odds_api_rows,
    live_player_key,
    names_match,
    odds_suspicious_for_probability,
    valid_two_way_decimal_pair,
)
from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.forebet import ForebetPredictor, forebet_cache_key
from racketfactory.ml import ml_filter_picks, build_context_registry, load_audit_rolling, should_veto_slice, get_min_ev_real
from racketfactory.regime import REGIME_ID
from racketfactory.odds_compare import fetch_comparison_rows

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
        if pd.notna(val) and str(val).strip() not in {"", "nan", "<NA>", "None"}:
            pick = str(val).strip()
            break

    if pd.notna(pick) and pick in {"player_a", "player_b", "1", "2"}:
        rank_col = "rank_a" if pick in {"player_a", "1"} else "rank_b"
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
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return "Unknown"
    if p > 1.0:
        p /= 100.0
    if p >= 0.70: return "High"
    if p >= 0.60: return "Medium"
    return "Low"


def _normalize_winner_for_agree(val, row: pd.Series | None = None) -> str | None:
    """Normalize legacy 1/2 codes and player names to player_a/player_b for agree check.

    - "1"/"player_a"/"a"/"home" -> player_a
    - "2"/"player_b"/"b"/"away" -> player_b
    - If val is an actual player name, map to player_a/b via row's player_a/b
    """
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() in {"", "nan", "<na>", "none"}:
        return None
    low = s.lower()
    if low in {"1", "player_a", "a", "home", "player_home"}:
        return "player_a"
    if low in {"2", "player_b", "b", "away", "player_away"}:
        return "player_b"
    # Try to map actual player name to player_a/b
    if row is not None:
        try:
            pa = str(row.get("player_a") or "").strip()
            pb = str(row.get("player_b") or "").strip()
            if pa and pb:
                if low == pa.lower() or pa.lower() in low or low in pa.lower():
                    return "player_a"
                if low == pb.lower() or pb.lower() in low or low in pb.lower():
                    return "player_b"
        except Exception:
            pass
    if low in ("player_a", "player_b"):
        return low
    return low


def get_cross_source_agree(row: pd.Series, pred_cols: list[str]) -> str:
    """
    Compare all prediction sources for consensus.
    Returns one of: Both | Disagree | MarketOnly | ForeTennisOnly | Unknown

    FIXES APPLIED:
    - Normalizes legacy 1/2 codes (BetClan/PredixSport) to player_a/b
    - Implements majority vote: 3-vs-1 consensus => Both, not Disagree
    - Derives real market baseline from odds in warehouse.py (was ghost column)
    - Previously: market vs foretennis special case ignored other sources;
      now all predicted_winner_* columns participate in vote
    """
    from collections import Counter

    # Collect all normalized picks across all prediction columns
    picks = []
    picks_by_col = {}
    for col in pred_cols:
        val = row.get(col)
        norm = _normalize_winner_for_agree(val, row)
        if norm is not None:
            picks.append(norm)
            picks_by_col[col] = norm

    if not picks:
        return "Unknown"

    unique_picks = set(picks)
    sources_count = len(picks)

    # Single source cases - preserve backward compat labels
    if len(unique_picks) == 1 and sources_count == 1:
        # Determine which single source it is for specific label
        sole_col = list(picks_by_col.keys())[0] if picks_by_col else ""
        if "foretennis" in sole_col.lower():
            return "ForeTennisOnly"
        if "market" in sole_col.lower():
            return "MarketOnly"
        # Generic single source (BetClan, Bzzoiro, etc) - historically labeled MarketOnly
        return "MarketOnly"

    if len(unique_picks) == 1 and sources_count > 1:
        return "Both"

    # Multiple unique picks - majority vote logic
    # Previously: any disagreement => Disagree (dead row for all 9 exportable slices)
    # Now: if clear majority exists, treat as Both (consensus)
    counter = Counter(picks)
    most_common_pick, most_common_count = counter.most_common(1)[0]

    # Majority: >50% and at least 2 votes agree
    if most_common_count > len(picks) / 2 and most_common_count >= 2:
        # e.g., 3 vs 1, or 2 vs 1 - consensus exists
        return "Both"

    # No majority (e.g., 2 vs 2 tie, or 1 vs 1 vs 1) => genuine Disagree
    return "Disagree"


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
    if target == today:
        return "today"
    if target == today + timedelta(days=1):
        return "tomorrow"
    # Off-window targets use Forebet calendar URLs (YYYY-MM-DD).
    return target.isoformat()


def enrich_fallback_card_with_api_odds(card: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """Attach forecast-card prices, preferring API with guarded scrape fallback.

    Tomorrow/future cards are built directly from prediction sources rather than
    warehouse live injection.  Keep The Odds API as first choice, then the
    cross-checked comparison leg (BetExplorer x OddsPortal upcoming) for the
    Challenger/ITF tours the API does not cover, and only then the source
    scrape pair — every leg under the same side-alignment and two-way sanity
    checks.  A lone honest side (Forebet coef on its predicted side) promotes
    single-side for paper-track use.
    """
    if card.empty:
        return card

    out = card.copy()
    out["debug_scraped_odds_home"] = out.get("odds_home", pd.Series(index=out.index, dtype=object))
    out["debug_scraped_odds_away"] = out.get("odds_away", pd.Series(index=out.index, dtype=object))
    if "odds_home" not in out.columns:
        out["odds_home"] = pd.NA
    if "odds_away" not in out.columns:
        out["odds_away"] = pd.NA
    if "odds_a" not in out.columns:
        out["odds_a"] = pd.NA
    if "odds_b" not in out.columns:
        out["odds_b"] = pd.NA
    if "_odds_source" not in out.columns:
        out["_odds_source"] = ""
    if "odds_cross_checked" not in out.columns:
        out["odds_cross_checked"] = ""

    odds_rows = fetch_the_odds_api_rows(target_date)
    if not odds_rows:
        logger.info("No The Odds API odds available for fallback card %s; trying comparison odds, then validated scraped fallback odds.", target_date)
    compare_rows = fetch_comparison_rows(target_date)
    priced_rows = list(odds_rows) + list(compare_rows)

    api_matched = 0
    compare_matched = 0

    for idx, row in out.iterrows():
        home = str(row.get("player_home") or row.get("player_a") or "").strip()
        away = str(row.get("player_away") or row.get("player_b") or "").strip()
        if not home or not away or not priced_rows:
            continue

        for odds_row in priced_rows:
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

            odds_home, odds_away = align_odds_to_probabilities(
                row.get("prob_home"), row.get("prob_away"), odds_home, odds_away
            )
            if not valid_two_way_decimal_pair(odds_home, odds_away):
                continue

            src_label = str(odds_row.get("source") or "TheOddsAPI")
            out.at[idx, "odds_home"] = odds_home
            out.at[idx, "odds_away"] = odds_away
            out.at[idx, "odds_a"] = odds_home
            out.at[idx, "odds_b"] = odds_away
            out.at[idx, "_odds_source"] = src_label
            out.at[idx, "odds_cross_checked"] = str(odds_row.get("odds_cross_checked") or "")
            out.at[idx, "_is_live"] = True
            out.at[idx, "_comment"] = (
                "forecast_upcoming_api_priced" if src_label == "TheOddsAPI"
                else "forecast_upcoming_compare_priced"
            )
            if src_label == "TheOddsAPI":
                api_matched += 1
            else:
                compare_matched += 1
            break

    fallback_matched = 0
    for idx, row in out.iterrows():
        if str(row.get("_odds_source", "") or ""):
            continue

        scraped_home, scraped_away = align_odds_to_probabilities(
            row.get("prob_home"),
            row.get("prob_away"),
            row.get("debug_scraped_odds_home"),
            row.get("debug_scraped_odds_away"),
        )
        if not valid_two_way_decimal_pair(scraped_home, scraped_away):
            ch = coerce_decimal_odds(scraped_home)
            ca = coerce_decimal_odds(scraped_away)
            # Both sides present but incoherent -> reject (e.g. 9.50/9.70).
            if ch is not None and ca is not None:
                continue
            # Nothing usable -> reject.
            if ch is None and ca is None:
                continue
            # One honest side (Forebet coef on its predicted side): promote
            # single-side. Paper track downstream; API stays strict-pair.
            if ch is None:
                scraped_home = pd.NA
            if ca is None:
                scraped_away = pd.NA

        out.at[idx, "odds_home"] = scraped_home
        out.at[idx, "odds_away"] = scraped_away
        out.at[idx, "odds_a"] = scraped_home
        out.at[idx, "odds_b"] = scraped_away
        out.at[idx, "_odds_source"] = "ScrapedFallback"
        out.at[idx, "_is_live"] = True
        out.at[idx, "_comment"] = "forecast_upcoming_scraped_fallback_priced"
        fallback_matched += 1

    logger.info(
        "Matched fallback forecast odds for %d/%d rows on %s: %d The Odds API, %d comparison, %d ScrapedFallback",
        api_matched + compare_matched + fallback_matched,
        len(out),
        target_date,
        api_matched,
        compare_matched,
        fallback_matched,
    )
    return out

def build_upcoming_fallback_card(target_date: str) -> pd.DataFrame:
    rows = []
    forebet_day = forebet_day_for_target(target_date)
    # Same-job fetch cache: reuse the site crawls already done by the capture
    # steps / warehouse builds instead of re-hitting the sites (rate-limit
    # protection). Empty results are never cached, so failures keep retrying.
    for source_name, cache_key, predictor, fetcher in [
        ("PredixSport", "predixsport", PredixSportPredictor(), lambda p: p.fetch_daily()),
        ("BetClan", "betclan", BetClanPredictor(), lambda p: p.fetch_daily()),
        ("Forebet", forebet_cache_key(forebet_day), ForebetPredictor(), lambda p: p.fetch_daily_predictions(forebet_day)),
    ]:
        try:
            preds = cached_fetch(cache_key, lambda _p=predictor, _f=fetcher: _f(_p))
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
    card["surface"] = card.get("surface", pd.Series(index=card.index, dtype=object)).astype(str).str.strip().str.title()
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
    # Removed Singles-only filter to enable pricing for Doubles matches
    card = card[card["tour"].isin(["ATP", "WTA", "CHALLENGER", "ITF-M", "ITF-W", "UTR"])]
    if card.empty:
        return card.reset_index(drop=True)

    grouped_rows = []
    for (_, pair_key), g in card.groupby(["match_date", "pair_key"], dropna=False):
        first = g.iloc[0]
        base_home = str(first.get("player_home", "") or "").strip()
        base_away = str(first.get("player_away", "") or "").strip()

        oriented_rows = []
        for _, rr in g.iterrows():
            row_home = str(rr.get("player_home", "") or "").strip()
            row_away = str(rr.get("player_away", "") or "").strip()
            normal = names_match(row_home, base_home) and names_match(row_away, base_away)
            reverse = names_match(row_home, base_away) and names_match(row_away, base_home)
            swapped = reverse and not normal

            if swapped:
                prob_home = rr.get("prob_away")
                prob_away = rr.get("prob_home")
                odds_home = rr.get("odds_away")
                odds_away = rr.get("odds_home")
                raw_pick = str(rr.get("predicted_winner", "") or "").strip()
                if raw_pick in {"1", "player_a"}:
                    pick = "player_b"
                elif raw_pick in {"2", "player_b"}:
                    pick = "player_a"
                else:
                    pick = ""
            else:
                prob_home = rr.get("prob_home")
                prob_away = rr.get("prob_away")
                odds_home = rr.get("odds_home")
                odds_away = rr.get("odds_away")
                raw_pick = str(rr.get("predicted_winner", "") or "").strip()
                if raw_pick in {"1", "player_a"}:
                    pick = "player_a"
                elif raw_pick in {"2", "player_b"}:
                    pick = "player_b"
                else:
                    pick = ""

            prob_home_num = pd.to_numeric(prob_home, errors="coerce")
            prob_away_num = pd.to_numeric(prob_away, errors="coerce")
            odds_home_num, odds_away_num = align_odds_to_probabilities(
                prob_home_num, prob_away_num, odds_home, odds_away
            )
            oriented_rows.append({
                "source": rr.get("source"),
                "pick": pick,
                "prob_home": prob_home_num,
                "prob_away": prob_away_num,
                "odds_home": odds_home_num,
                "odds_away": odds_away_num,
            })

        winners = [rr["pick"] for rr in oriented_rows if rr.get("pick")]
        unique_winners = sorted(set(winners))
        if len(unique_winners) > 1:
            cross_source_agree = "Disagree"
        elif len(unique_winners) == 1 and len(g["source"].unique()) > 1:
            cross_source_agree = "Both"
        elif len(unique_winners) == 1:
            cross_source_agree = "MarketOnly"
        else:
            cross_source_agree = "Unknown"

        raw_first_pick = str(first.get("predicted_winner", "") or "").strip()
        selected_pick = unique_winners[0] if unique_winners else ("player_a" if raw_first_pick in {"1", "player_a"} else "player_b")
        source_count = int(g["source"].nunique())
        home_probs = pd.Series([rr.get("prob_home") for rr in oriented_rows], dtype="float64")
        away_probs = pd.Series([rr.get("prob_away") for rr in oriented_rows], dtype="float64")
        max_home = home_probs.max() if not home_probs.empty else None
        max_away = away_probs.max() if not away_probs.empty else None
        probs = [p for p in [max_home, max_away] if pd.notna(p)]
        max_prob = max(probs) if probs else None
        selected_prob = max_home if selected_pick == "player_a" else max_away
        if pd.isna(selected_prob):
            selected_prob = max_prob

        selected_odds_key = "odds_home" if selected_pick == "player_a" else "odds_away"
        # A row is usable when it carries a valid pair OR a valid selected-side
        # price (Forebet publishes a single honest coef on its predicted side;
        # EV/pricing downstream consume the selected side only).
        usable_odds_rows = [
            rr for rr in oriented_rows
            if valid_two_way_decimal_pair(rr.get("odds_home"), rr.get("odds_away"))
            or coerce_decimal_odds(rr.get(selected_odds_key)) is not None
        ]
        if usable_odds_rows:
            best_odds_row = max(
                usable_odds_rows,
                key=lambda rr: coerce_decimal_odds(rr.get(selected_odds_key)) or 0.0,
            )
            scraped_odds_home = best_odds_row.get("odds_home")
            scraped_odds_away = best_odds_row.get("odds_away")
        else:
            scraped_odds_home = pd.NA
            scraped_odds_away = pd.NA

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
            "odds_home": scraped_odds_home,
            "odds_away": scraped_odds_away,
            "odds_a": scraped_odds_home,
            "odds_b": scraped_odds_away,
            "cross_source_agree": cross_source_agree,
            "pred_confidence": "High" if (max_prob is not None and max_prob >= 70) else ("Medium" if (max_prob is not None and max_prob >= 60) else "Low"),
            "_comment": "forecast_upcoming_fallback",
            "_is_live": True,
            "source": ", ".join(sorted(set(map(str, g["source"])))),
            "source_count": source_count,
            "debug_scraped_odds_home": scraped_odds_home,
            "debug_scraped_odds_away": scraped_odds_away,
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


# Dim values that carry no signal: mining slices on them (e.g. tour:UNKNOWN)
# can only match other unknown-context rows, which are never actionable.
PLACEHOLDER_DIM_VALUES = ("Unknown", "UNKNOWN", "")

# Slice verdicts that may promote a today-row to a pick. Exportable neutral
# slices (NO STAT SIG / NEUTRAL) are diagnostics-only.
ACTIONABLE_VERDICTS = ("EDGE CONFIRMED", "WATCHLIST")


def _is_placeholder_dim_value(x: object) -> bool:
    """True for dim values that must be excluded from slice mining."""
    return bool(pd.isna(x)) or x in PLACEHOLDER_DIM_VALUES


def _actionable_slices(results: list[dict] | None) -> list[dict]:
    """Slices that may promote a today-row to a pick.

    Mining emits report-only slices too (NO STAT SIG, N < min-export-n,
    ROBBER, FADE); those must not block the live-only fallback (run #206:
    5 unexportable tour:UNKNOWN slices yielded 0 picks despite 26 today
    candidates). Single source of truth for the export gate.
    """
    return [
        res for res in (results or [])
        if res.get("Exportable", False)
        and str(res.get("Verdict", "")).strip() in ACTIONABLE_VERDICTS
    ]


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
    """Return selected-side odds. NEVER repairs side inversions.

    A model disagreeing with a labeled market price is evidence of model
    error or market news, never proof of an inverted market. The old
    "correction" swapped sides on probability disagreement and converted
    79%-favorites @9.50 into fake value. Disagreement now rejects the row.
    """
    odds_val = odds_for_selected_side(row, selected_side)
    if odds_val is None:
        return None, "missing selected-side odds"

    if row_has_live_flag(row):
        odds_source = str(row.get("_odds_source", "") or "")
        usable_live_sources = {"TheOddsAPI", "OddsPortal", "BetExplorer", "ScrapedFallback"}
        if odds_source not in usable_live_sources:
            return None, "missing usable live odds"
        pair_ok = valid_two_way_decimal_pair(row.get("odds_a"), row.get("odds_b"))
        if not pair_ok and odds_source != "ScrapedFallback":
            return None, f"incomplete/invalid {odds_source} live odds pair"
        if not pair_ok:
            # Single-side scraped fallback (Forebet coef): usable for the
            # selected side only, and only when the other side is absent (a
            # present-but-incoherent other side means a corrupt pair).
            # Paper track downstream; API prices stay strict-pair.
            side_tok = normalize_side_token(selected_side)
            other_raw = row.get("odds_b" if side_tok == "player_a" else "odds_a")
            if coerce_decimal_odds(other_raw) is not None:
                return None, f"incomplete/invalid {odds_source} live odds pair"
        side = normalize_side_token(selected_side)
        other_odds = coerce_decimal_odds(row.get("odds_b" if side == "player_a" else "odds_a"))
        if (
            other_odds is not None
            and odds_suspicious_for_probability(probability, odds_val)
            and not odds_suspicious_for_probability(probability, other_odds)
        ):
            return None, (
                f"prob/odds side disagreement on {odds_source} live odds "
                f"(selected {odds_val} vs alt {other_odds}) — not usable"
            )

    return odds_val, None


LOCAL_ODDSPORTAL_ODDS_CACHE: dict[str, list[dict]] = {}


def _split_match_text_for_odds(match: object) -> tuple[str, str]:
    text = str(match or "").strip()
    parts = re.split(r"\s+v(?:s\.?)?\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


def load_local_oddsportal_odds(target_date: str) -> list[dict]:
    """Load locally captured OddsPortal odds for a target date.

    This is an automated odds source, not manual ingestion. It reads the
    existing normalized OddsPortal monthly CSV produced by capture_oddsportal.py.

    Useful for doubles where The Odds API has no coverage but OddsPortal has
    fixture prices.
    """
    target = str(target_date)[:10]
    if target in LOCAL_ODDSPORTAL_ODDS_CACHE:
        return LOCAL_ODDSPORTAL_ODDS_CACHE[target]

    path = ROOT / "localdata" / f"oddsportal_tennis_{target[:7]}.csv.gz"
    if not path.exists():
        LOCAL_ODDSPORTAL_ODDS_CACHE[target] = []
        return []

    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        logger.warning("Could not read local OddsPortal odds %s: %s", path, exc)
        LOCAL_ODDSPORTAL_ODDS_CACHE[target] = []
        return []

    required = {"match_date", "player_a", "player_b", "odds_a", "odds_b"}
    if df.empty or not required.issubset(set(df.columns)):
        LOCAL_ODDSPORTAL_ODDS_CACHE[target] = []
        return []

    df = df[df["match_date"].astype(str).str[:10] == target].copy()

    rows: list[dict] = []
    for _, r in df.iterrows():
        odds_a = coerce_decimal_odds(r.get("odds_a"))
        odds_b = coerce_decimal_odds(r.get("odds_b"))
        if not valid_two_way_decimal_pair(odds_a, odds_b):
            continue

        a = str(r.get("player_a") or "").strip()
        b = str(r.get("player_b") or "").strip()
        if not a or not b:
            continue

        rows.append({
            "player_a": a,
            "player_b": b,
            "odds_a": odds_a,
            "odds_b": odds_b,
            "bookmaker": str(r.get("bookmaker") or "OddsPortal").strip() or "OddsPortal",
            "source": str(r.get("source") or "OddsPortal").strip() or "OddsPortal",
        })

    LOCAL_ODDSPORTAL_ODDS_CACHE[target] = rows
    if rows:
        logger.info("Loaded %d local OddsPortal odds rows for %s from %s", len(rows), target, path)
    return rows


BZZOIRO_ODDS_CACHE: dict[str, list[dict] | None] = {}


def _bzzoiro_cache_path(target_date: str) -> Path:
    return ROOT / "localdata" / f"bzzoiro_odds_{target_date}.json"


def _bzzoiro_date_payload(target_date: str) -> list[dict] | None:
    """Paid v2 odds/best payload, read-through static disk cache.

    One paid fetch per date ever: the payload is persisted to
    ``localdata/bzzoiro_odds_<date>.json`` and reused by every later run
    (CI reruns, audits, replays) without spending quota. A corrupt cache
    file is ignored and refetched when a token is available.
    """
    import os
    if target_date in BZZOIRO_ODDS_CACHE:
        return BZZOIRO_ODDS_CACHE[target_date]
    try:
        cached = json.loads(_bzzoiro_cache_path(target_date).read_text())
        if isinstance(cached, dict) and isinstance(cached.get("results"), list):
            BZZOIRO_ODDS_CACHE[target_date] = cached["results"]
            logger.info("Bzzoiro odds/best: %d matches for %s (static cache)",
                        len(cached["results"]), target_date)
            return cached["results"]
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Bzzoiro static cache unreadable for %s: %s", target_date, exc)
    token = os.getenv("BZZOIRO_TOKEN")
    if not token:
        BZZOIRO_ODDS_CACHE[target_date] = None
        return None
    try:
        import requests
        url = (f"https://sports.bzzoiro.com/tennis/api/v2/odds/best/"
               f"?date_from={target_date}&date_to={target_date}&limit=100")
        headers = {"Authorization": f"Token {token}"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning("Bzzoiro odds/best returned %s for %s",
                           resp.status_code, target_date)
            BZZOIRO_ODDS_CACHE[target_date] = None
            return None
        data = resp.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        BZZOIRO_ODDS_CACHE[target_date] = results
        try:
            _bzzoiro_cache_path(target_date).write_text(json.dumps(
                {"date": target_date, "results": results}))
        except Exception as exc:
            logger.warning("Bzzoiro static cache write failed for %s: %s", target_date, exc)
        logger.info("Bzzoiro odds/best: %d matches for %s", len(results), target_date)
        return results
    except Exception as exc:
        logger.warning("Bzzoiro odds/best fetch failed for %s: %s", target_date, exc)
        BZZOIRO_ODDS_CACHE[target_date] = None
        return None


def lookup_bzzoiro_selected_odds(target_date: str, pick: dict) -> dict | None:
    """Try Bzzoiro odds best API for selected side — Challenger/ITF coverage.

    Best-price across bookmakers for the SELECTED side only (no Frankenstein
    pair is built). Labeled ``Bzzoiro`` with the quoting bookmaker.
    """
    try:
        results = _bzzoiro_date_payload(target_date)
        if not results:
            return None
        home = str(pick.get("player_home") or "").strip()
        away = str(pick.get("player_away") or "").strip()
        selected = str(pick.get("selected_player") or "").strip()
        if not home or not away or not selected:
            return None
        for item in results:
            match = item.get("match", {}) or {}
            p1 = match.get("player1", {}).get("name", "")
            p2 = match.get("player2", {}).get("name", "")
            if not p1 or not p2:
                continue
            # Name matching
            from racketfactory.sources.forebet import name_signature
            def sig(s): return name_signature(s)
            home_sig, away_sig = sig(home), sig(away)
            p1_sig, p2_sig = sig(p1), sig(p2)
            normal = (home_sig == p1_sig and away_sig == p2_sig)
            reverse = (home_sig == p2_sig and away_sig == p1_sig)
            if not (normal or reverse):
                continue
            # Find best odds for 1x2 market
            best_odds = item.get("best_odds", []) or []
            for bo in best_odds:
                market = str(bo.get("market") or "").lower()
                if "1x2" not in market and "h2h" not in market:
                    continue
                outcomes = bo.get("outcomes", []) or []
                for oc in outcomes:
                    outcome_name = str(oc.get("outcome") or oc.get("name") or "").strip()
                    # Bzzoiro outcome 1 = player1, 2 = player2
                    if outcome_name == "1" and sig(selected) == p1_sig:
                        odds = oc.get("odd") or oc.get("odds")
                        try:
                            odds_f = float(odds)
                            if odds_f > 1.01:
                                return {"odds": odds_f, "bookmaker": str(bo.get("bookmaker") or "Bzzoiro"), "source": "Bzzoiro", "matched_market": f"{p1} vs {p2}"}
                        except:
                            pass
                    if outcome_name == "2" and sig(selected) == p2_sig:
                        odds = oc.get("odd") or oc.get("odds")
                        try:
                            odds_f = float(odds)
                            if odds_f > 1.01:
                                return {"odds": odds_f, "bookmaker": str(bo.get("bookmaker") or "Bzzoiro"), "source": "Bzzoiro", "matched_market": f"{p1} vs {p2}"}
                        except:
                            pass
        return None
    except Exception as e:
        # Soft fail
        try:
            import logging
            logging.getLogger(__name__).debug(f"Bzzoiro odds lookup failed: {e}")
        except:
            pass
        return None

def lookup_local_oddsportal_selected_odds(target_date: str, pick: dict) -> dict | None:
    """Find selected-side odds from locally captured OddsPortal rows."""
    rows = load_local_oddsportal_odds(target_date)
    if not rows:
        return None

    home = str(pick.get("player_home") or pick.get("player_a") or "").strip()
    away = str(pick.get("player_away") or pick.get("player_b") or "").strip()
    if not home or not away:
        home, away = _split_match_text_for_odds(pick.get("match"))

    selected = str(pick.get("selected_player") or "").strip()

    if not home or not away or not selected:
        return None

    for row in rows:
        op_a = str(row.get("player_a") or "").strip()
        op_b = str(row.get("player_b") or "").strip()

        normal = names_match(home, op_a) and names_match(away, op_b)
        reverse = names_match(home, op_b) and names_match(away, op_a)

        if not (normal or reverse):
            continue

        if normal:
            side_a_name, side_b_name = op_a, op_b
            side_a_odds, side_b_odds = row.get("odds_a"), row.get("odds_b")
        else:
            side_a_name, side_b_name = op_b, op_a
            side_a_odds, side_b_odds = row.get("odds_b"), row.get("odds_a")

        if names_match(selected, side_a_name):
            return {
                "odds": side_a_odds,
                "bookmaker": row.get("bookmaker") or "OddsPortal",
                "source": row.get("source") or "OddsPortal",
                "matched_market": f"{op_a} vs {op_b}",
            }

        if names_match(selected, side_b_name):
            return {
                "odds": side_b_odds,
                "bookmaker": row.get("bookmaker") or "OddsPortal",
                "source": row.get("source") or "OddsPortal",
                "matched_market": f"{op_a} vs {op_b}",
            }

    return None



def select_player_from_row(row: pd.Series, target_date: str) -> dict:
    home_name = row.get("player_a", row.get("player_home", "A"))
    away_name = row.get("player_b", row.get("player_away", "B"))

    selected_pick = None
    selected_player = None

    pred_cols = [c for c in row.index if c.startswith("predicted_winner")]
    for col in pred_cols:
        val = row.get(col)
        if pd.notna(val) and str(val).strip() not in {"", "nan", "<NA>", "None"}:
            selected_pick = str(val).strip()
            break

    selection_basis = "prediction"
    if selected_pick in {"player_a", "player_b"}:
        selected_player = home_name if selected_pick == "player_a" else away_name
    elif selected_pick in {"1", "2"}:
        selected_player = home_name if selected_pick == "1" else away_name
    else:
        oa, ob = row.get("odds_a"), row.get("odds_b")
        if pd.notna(oa) and pd.notna(ob):
            selected_pick = "player_a" if oa <= ob else "player_b"
            selected_player = home_name if selected_pick == "player_a" else away_name
            selection_basis = "odds_favorite"
        else:
            selected_pick = "player_a"
            selected_player = home_name
            selection_basis = "arbitrary_default"

    source_val = row.get("source", "")
    source_count = row.get("source_count")
    if pd.isna(source_count) or source_count is None:
        source_count = len([c for c in pred_cols if pd.notna(row.get(c)) and str(row.get(c)).strip() not in {"", "nan", "<NA>", "None"}])

    ctx_val = row.get("context_used")
    if pd.isna(ctx_val) or str(ctx_val).strip() in {"nan", "<NA>", "None", ""}:
        ctx_val = row.get("tournament", "")
        
    time_val = row.get("match_time")
    if pd.isna(time_val) or str(time_val).strip() in {"nan", "<NA>", "None", ""}:
        time_val = "n/a"

    return {
        "match": f"{home_name} vs {away_name}",
        "date": str(row.get("match_date", target_date)),
        "match_time": str(time_val),
        "kickoff": str(time_val),
        "selected_side": selected_pick,
        "selected_player": selected_player,
        "_regime": REGIME_ID,
        "_selection_basis": selection_basis,
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


def market_basis_for_pick(odds_source: str, odds_val: object) -> tuple[str, bool]:
    """Return (market_basis, is_paper) for an emitted pick.

    ``api`` = executable bookmaker price (TheOddsAPI/OddsPortal/Bzzoiro/BetExplorer).
    ``scraped_fallback`` = label-only scrape pair, paper only.
    ``none`` = unpriced.
    """
    if odds_val is None:
        return "none", True
    if str(odds_source or "").strip() in {"TheOddsAPI", "OddsPortal", "Bzzoiro", "BetExplorer"}:
        return "api", False
    if str(odds_source or "").strip() == "ScrapedFallback":
        return "scraped_fallback", True
    return "none", True


def _existing_pick_rows(path: Path) -> list | None:
    """Read an existing picks ledger; None when missing/unreadable."""
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return None


def should_write_pick_outputs(picks: list[dict], existing: list | None) -> bool:
    """No-clobber rule: an empty mine must never blank a non-empty ledger.

    Late-day re-runs (or live-fetch outages) legitimately mine zero rows;
    overwriting the day's good ledger with ``[]`` destroys the betting record
    and the audit trail (observed 2026-09-12 evening: 15 rows -> []).
    """
    if picks:
        return True
    if existing:
        return False
    return True


def write_official_pick_outputs(target_date: str, picks: list[dict]) -> None:
    out_dir = ROOT / "localdata"
    out_dir.mkdir(parents=True, exist_ok=True)
    today_path = out_dir / "picks_today.json"
    archive_path = out_dir / f"picks_{target_date}.json"
    payload = json.dumps(picks, indent=2)
    for path in (today_path, archive_path):
        existing = _existing_pick_rows(path)
        if not should_write_pick_outputs(picks, existing):
            logger.error(
                "REFUSING to overwrite non-empty %s (%d rows) with 0 pick rows "
                "(live-fetch starvation guard; keeping existing ledger).",
                path.name, len(existing or []),
            )
            continue
        path.write_text(payload)
    bucket_counts = {}
    for pick in picks:
        bucket = str(pick.get("bucket") or "UNKNOWN")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    bettable_count = sum(bucket_counts.get(bucket, 0) for bucket in ("CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "FADE"))
    logger.info(
        "Wrote %d pick rows (%d bettable/review rows) to %s and %s; buckets=%s",
        len(picks),
        bettable_count,
        today_path,
        archive_path,
        bucket_counts,
    )



def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(__import__("os").environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = str(__import__("os").environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = str(__import__("os").environ.get(name, "")).strip()
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
    try:
        if value is None:
            return None
        value = float(value)
        if pd.isna(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def _is_same_day_target(target_date: str) -> bool:
    try:
        target = datetime.strptime(str(target_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return target == datetime.now().date()


def _pick_sources(pick: dict) -> set[str]:
    raw = str(pick.get("source") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _pick_has_missing_kickoff(pick: dict) -> bool:
    for key in ("kickoff", "match_time", "time", "start_time", "ko"):
        value = str(pick.get(key) or "").strip().lower()
        if value and value not in {"n/a", "na", "nan", "<na>", "none", "null"}:
            return False
    return True


def apply_same_day_source_hygiene(target_date: str, picks: list[dict]) -> list[dict]:
    """Drop same-day rows whose source date is not trustworthy enough.

    PredixSport tennis exposes prediction slate/tournament dates, not confirmed
    actual play dates.  If a row is PredixSport-only, has no selected-side odds,
    and has no kickoff time, it is an early/unconfirmed prediction rather than
    an actionable same-day pick.
    """
    if not _is_same_day_target(target_date):
        return picks

    kept: list[dict] = []
    dropped = 0

    for pick in picks:
        bucket = str(pick.get("bucket") or "")
        sources = _pick_sources(pick)
        odds = _pick_float(pick, "odds")
        date_confidence = str(pick.get("date_confidence") or "").upper()
        schedule_source = str(pick.get("scheduled_date_source") or "")

        unsafe_predix_only = (
            sources == {"PredixSport"}
            and bucket in {"WATCHLIST_NO_ODDS", "WATCHLIST_UNKNOWN_CTX"}
            and odds is None
            and _pick_has_missing_kickoff(pick)
            and (date_confidence in {"", "LOW"} or schedule_source.startswith("PredixSport"))
        )

        if unsafe_predix_only:
            dropped += 1
            logger.info(
                "Same-day hygiene dropped PredixSport-only date-uncertain row: %s -> %s",
                pick.get("match"),
                pick.get("selected_player"),
            )
            continue

        kept.append(pick)

    if dropped:
        logger.info("Same-day source hygiene for %s: kept %d, dropped %d", target_date, len(kept), dropped)

    return kept


def _pick_players_for_dedupe(pick: dict) -> tuple[str, str]:
    home = str(pick.get("player_home") or pick.get("player_a") or "").strip()
    away = str(pick.get("player_away") or pick.get("player_b") or "").strip()
    if home and away:
        return home, away

    match = str(pick.get("match") or "").strip()
    parts = re.split(r"\s+v(?:s\.?)?\s+", match, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()

    return home, away


def _dedupe_name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9/]+", " ", str(name or "").lower())
    return [tok for tok in cleaned.split() if tok and tok != "/"]


def _dedupe_names_match(a: str, b: str) -> bool:
    if names_match(a, b):
        return True

    ta = _dedupe_name_tokens(a)
    tb = _dedupe_name_tokens(b)
    if not ta or not tb or ta[-1] != tb[-1]:
        return False

    pre_a = ta[:-1]
    pre_b = tb[:-1]

    # Full pre-surname token overlap handles "A. De Minaur" vs "Alex de Minaur".
    if {x for x in pre_a if len(x) > 1} & {x for x in pre_b if len(x) > 1}:
        return True

    initials_a = [x for x in pre_a if len(x) == 1]
    initials_b = [x for x in pre_b if len(x) == 1]

    # Initials handle "R. A. Burruchaga" vs "Roman Andres Burruchaga".
    if initials_a and all(any(y.startswith(x) for y in pre_b) for x in initials_a):
        return True
    if initials_b and all(any(y.startswith(x) for y in pre_a) for x in initials_b):
        return True

    return False


def _same_pick_match(a: dict, b: dict) -> bool:
    ah, aa = _pick_players_for_dedupe(a)
    bh, ba = _pick_players_for_dedupe(b)
    if not ah or not aa or not bh or not ba:
        return False

    return (
        _dedupe_names_match(ah, bh) and _dedupe_names_match(aa, ba)
    ) or (
        _dedupe_names_match(ah, ba) and _dedupe_names_match(aa, bh)
    )


def _same_selected_player(a: dict, b: dict) -> bool:
    ap = str(a.get("selected_player") or "").strip()
    bp = str(b.get("selected_player") or "").strip()
    return bool(ap and bp and _dedupe_names_match(ap, bp))


def dedupe_same_match_picks(picks: list[dict]) -> list[dict]:
    """Drop duplicate rows for the same match and same selected side.

    The input list is already sorted by bucket, EV, source_count, and match, so
    the first row kept is the preferred/export row. Opposing-side conflicts are
    deliberately not merged or hidden.
    """
    kept: list[dict] = []
    dropped = 0

    for pick in picks:
        is_duplicate = False
        for existing in kept:
            if _same_pick_match(existing, pick) and _same_selected_player(existing, pick):
                dropped += 1
                is_duplicate = True
                logger.info(
                    "Deduped same-match pick row: kept %s -> %s; dropped %s -> %s",
                    existing.get("match"),
                    existing.get("selected_player"),
                    pick.get("match"),
                    pick.get("selected_player"),
                )
                break

        if not is_duplicate:
            kept.append(pick)

    if dropped:
        logger.info("Same-match pick dedupe: kept %d, dropped %d", len(kept), dropped)

    return kept


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

    actionable_buckets = {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "FADE"}
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
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "FADE": 1, "CAUTION": 2}.get(str(p.get("bucket")), 9),
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

def _dim_mismatches_slice(row: pd.Series, dim_name: str, dim_val) -> bool:
    """True when a today-row's dimension value does NOT satisfy a slice's
    dimension value.

    Exact, case/space-insensitive equality — the same rule the slice-match
    loop has always used — with the historical series equivalences
    (International <-> WTA250, Premier <-> WTA500) applied to ``_series``.
    Centralised so the match loop and the no-slice diagnostics below can't
    drift apart (run 35402614701: 81 candidates matched no slice and the
    only trace was a single log line — diagnostics must reuse one rule).
    """
    r_val = row.get(dim_name)
    if str(r_val).replace(" ", "").lower() == str(dim_val).replace(" ", "").lower():
        return False
    if dim_name == "_series":
        d_clean = str(dim_val).replace(" ", "").lower()
        r_clean = str(r_val).replace(" ", "").lower()
        if (d_clean, r_clean) in {
            ("international", "wta250"), ("wta250", "international"),
            ("premier", "wta500"), ("wta500", "premier"),
        }:
            return False
    return True


def dump_slice_table(
    target_date: str,
    results: list[dict],
    df: pd.DataFrame | None,
    warehouse_path: str | Path,
    out_path: str | Path,
) -> Path | None:
    """Write the mining-time slice table + warehouse fingerprint to JSON.

    The match loop below decides on THIS table, and the table is not
    reproducible from committed localdata alone (the CI build merges
    runtime caches — 2026-09-20: Hard|1.1-1.3|High derived FADE here and
    EDGE CONFIRMED in the offline rebuild). Committing the exact table +
    warehouse hash makes every match/no-match decision auditable from the
    repo without re-deriving anything. Non-fatal on failure.
    """
    try:
        wh = Path(warehouse_path)
        agree_dist: dict[str, int] = {}
        n_rows = 0
        if df is not None and "cross_source_agree" in df.columns:
            agree_dist = {
                str(k): int(v)
                for k, v in df["cross_source_agree"].astype(str).value_counts().items()
            }
        if df is not None:
            n_rows = int(len(df))
        model_rows = [s for s in (results or []) if s.get("Side") == "prediction"]
        fade_rows = [s for s in (results or []) if s.get("Side") == "fade"]
        payload = {
            "date": str(target_date),
            "warehouse": str(wh),
            "warehouse_sha256": (
                hashlib.sha256(wh.read_bytes()).hexdigest() if wh.exists() else ""
            ),
            "warehouse_rows": n_rows,
            "cross_source_agree": agree_dist,
            "n_slices": len(results or []),
            "n_model_slices": len(model_rows),
            "n_fade_slices": len(fade_rows),
            "n_exportable_model": sum(1 for s in model_rows if s.get("Exportable")),
            "n_exportable_fade": sum(1 for s in fade_rows if s.get("Exportable")),
            "slices": results or [],
        }
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, default=str))
        logger.info(
            "Wrote slice table artifact %s (%d slices: %d model / %d fade, "
            "%d exportable model, %d exportable fade)",
            out.name,
            payload["n_slices"],
            payload["n_model_slices"],
            payload["n_fade_slices"],
            payload["n_exportable_model"],
            payload["n_exportable_fade"],
        )
        return out
    except Exception as exc:
        logger.warning("slice table dump failed (non-fatal): %s", exc)
        return None


def _nearest_slice_gap(row: pd.Series, res: dict) -> list[str]:
    """Human-readable list of the dims where ``row`` fails ``res``'s slice."""
    combo = res.get("Combo_Dict") or {}
    gaps = []
    for dim_name, dim_val in combo.items():
        if _dim_mismatches_slice(row, dim_name, dim_val):
            gaps.append(f"{dim_name}={str(row.get(dim_name)).strip()}!={str(dim_val).strip()}")
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine the warehouse for automated edges")
    ap.add_argument("--warehouse", default="localdata/warehouse.csv.gz", help="Path to warehouse")
    ap.add_argument("--min-n", type=int, default=15,
                    help="Minimum matches per slice (default 15)")
    ap.add_argument("--min-export-n", type=int, default=30,
                    help="REDTEAM Finding #5: minimum historical N required for a slice to be "
                         "considered exportable as a live pick. Default 30 (was 50 too strict: "
                         "2026-09-17 only 1 slice n=54 exportable -> 1 bettable -> NO BET, 66 rows "
                         "no exportable slice). Anything smaller is kept for transparency but cannot "
                         "become a pick. User rule n<30 fluke, so 30 is minimum meaningful.")
    ap.add_argument("--min-ev", type=float, default=None,
                    help="REDTEAM Finding #4: minimum per-bet expected value (decimal) required "
                         "for a pick to be exported. EV = conf*(odds-1) - (1-conf). Default None "
                         "resolves to ml.get_min_ev_real() (RACKET_FACTORY_MIN_EV, +2% policy — "
                         "the same floor the ML capital-protection gate enforces on stakeable "
                         "legs). Set explicitly (e.g. 0.0 or a negative value) to override.")
    ap.add_argument("--bet-side", choices=["favorite", "prediction"], default="favorite",
                    help="REDTEAM Finding #3: what side does the slice assay test? 'favorite' "
                         "(default, historical behaviour) or 'prediction' (follow predicted_winner*).")
    ap.add_argument("--date", default=None, help="Target date YYYY-MM-DD to extract specific picks (default: today)")
    args = ap.parse_args()

    # Close the policy gap: when --min-ev is not passed on the CLI, resolve it
    # through the SAME source the ML capital-protection gate uses (ml.py),
    # i.e. RACKET_FACTORY_MIN_EV with a +2% default. Previously the export
    # gate defaulted to 0.0 while ml.py enforced 2%, so a 0.5%-EV pick could
    # be exported by mine_edges but the policy floor was +2%. One env var now
    # governs both; an explicit --min-ev still wins for diagnostics.
    if args.min_ev is None:
        args.min_ev = get_min_ev_real()

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
        try: oa = float(oa) if pd.notna(oa) and str(oa).strip() not in {"", "nan", "<NA>", "None"} else None
        except: oa = None
        try: ob = float(ob) if pd.notna(ob) and str(ob).strip() not in {"", "nan", "<NA>", "None"} else None
        except: ob = None
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
            if pd.notna(p_val) and str(p_val).strip() not in {"nan", "<NA>", "None"}:
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
            if pd.notna(p_val) and str(p_val).strip() not in {"nan", "<NA>", "None"}:
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

    # Candidate selection below needs the PRE-SPLIT frame: unsettled,
    # non-live rows (upcoming fixtures carried by results feeds) are excluded
    # from slice mining but must still be minable as same-day candidates.
    df_candidates = df
    df = df_hist  # downstream slice mining operates on the settled cohort only
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
        dimensions[k] = [x for x in v if not _is_placeholder_dim_value(x)]

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
                subset_df = subset_df[~subset_df[d].isin(PLACEHOLDER_DIM_VALUES)]
                subset_df = subset_df.dropna(subset=[d])

            if subset_df.empty:
                continue

            # ROI feedback: load registry once per outer loop (cached)
            try:
                _audit = load_audit_rolling()
                _registry = build_context_registry(_audit)
            except Exception:
                _registry = {}
            for combo, slice_df in subset_df.groupby(subset):
                if not isinstance(combo, tuple):
                    combo = (combo,)
                if len(slice_df) < args.min_n:
                    continue
                # ROI feedback veto for historical slices
                combo_dict = dict(zip(subset, combo))
                # ROI-feedback veto is model-direction (the registry is built
                # from model-side audit ROI), so it applies to the model side
                # only. Fade sides are vetted by their own assay gates.
                model_vetoed = False
                try:
                    veto, reason = should_veto_slice(combo_dict, _registry)
                    if veto:
                        logger.debug("Slice vetoed by ROI feedback: %s (%s)", combo_dict, reason)
                        model_vetoed = True
                except Exception:
                    pass
                res = assay_segment(slice_df, bet_side=args.bet_side)
                side_results = [(args.bet_side, res)]
                if args.bet_side == "prediction":
                    # Fade lane: the opposite side is a different bet at a
                    # different price, so it gets its own assay under the
                    # identical gates (a model-side ROBBER is a candidate
                    # fade, not a dead end).
                    side_results.append(("fade", assay_segment(slice_df, bet_side="fade")))
                for side, r in side_results:
                    if side != "fade" and model_vetoed:
                        continue
                    # REDTEAM Finding #1: ROBBER/FADE slices are still mined (so
                    # they can be shown in the slice report) but they cannot
                    # promote a live row to a pick. We continue to the slice
                    # collection but skip the export path later.
                    if r.grade not in ["GOLD", "PLATINUM", "SILVER"] and r.tier != "ROBBER":
                        continue

                    signature = (side, tuple(sorted(combo_dict.items())))
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)

                    slice_label = " | ".join([f"{n}:{v}" for n, v in combo_dict.items()])
                    results.append({
                        "Slice": f"FADE {slice_label}" if side == "fade" else slice_label,
                        "Side": side,
                        "Combo_Dict": combo_dict,
                        "Dims": len(combo_dict),
                        "N": r.n,
                        "WinRate": f"{r.win_rate:.2%}",
                        "Shrunk": f"{r.shrunk_rate:.2%}",
                        "ROI": f"{r.roi:.2%}",
                        "Grade": r.grade,
                        "Tier": r.tier,
                        "Verdict": r.verdict,
                        # REDTEAM Finding #5: mark exportability. Only slices that
                        # survive the min-N gate AND are not ROBBER/FADE can be
                        # used as actionable picks. The flag is consumed by the
                        # pick export loop below.
                        "Exportable": (
                            r.tier != "ROBBER"
                            and r.verdict != "FADE THIS SIGNAL"
                            and r.n >= args.min_export_n
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

    # Mining-time slice table artifact (committed): the exact table this run
    # matched candidates against, with the warehouse fingerprint. See
    # dump_slice_table for why (snapshot drift flips boundary verdicts).
    dump_slice_table(
        target_date,
        results,
        df,
        args.warehouse,
        ROOT / "localdata" / f"slice_table_{target_date}.json",
    )

    # REDTEAM Finding #2 follow-up: today's pick candidates need BOTH
    # unsettled historical rows for `target_date` AND live-injected rows for
    # the same date. We excluded live rows from `df` (the slice-mining
    # cohort), but they still have to participate in pick matching so the
    # operator sees actionable matches for today's slate.
    if not df_candidates.empty and "match_date" in df_candidates.columns:
        _unsettled_hist = (
            (~live_mask) & (~settled_mask)
            & (df_candidates["match_date"] == target_date)
        )
        today_hist = df_candidates[_unsettled_hist].copy()
    else:
        today_hist = df_candidates.copy()
    today_live = (
        df_live_only[df_live_only["match_date"] == target_date].copy()
        if not df_live_only.empty else df_live_only.copy()
    )
    if today_hist.empty:
        today_all = today_live.copy()
    elif today_live.empty:
        today_all = today_hist.copy()
    else:
        today_all = pd.concat([today_hist, today_live], ignore_index=True, sort=False)

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
    # Actionable slices: only exportable EDGE CONFIRMED / WATCHLIST slices can
    # promote a today-row to a pick. Report-only slices (NO STAT SIG, N <
    # min-export-n, ROBBER, FADE) must not block the live-only fallback below
    # (run #206: 5 unexportable slices yielded 0 picks despite 26 candidates).
    actionable_results = _actionable_slices(results)
    if results and not actionable_results:
        logger.warning("Mined %d slices but none are actionable (all NO STAT SIG / "
                       "N<%d / ROBBER / FADE) — live-only fallback will export today "
                       "candidates as WATCHLIST", len(results), args.min_export_n)
    # Fallback exporter shared by both recovery paths:
    #   (1) live-only fallback (no actionable slices at all) — original path,
    #   (2) opt-in no-slice export (RACKET_FACTORY_EXPORT_NO_SLICE) — added
    #       after run 35402614701, where 81 candidates matched no exportable
    #       slice and the day went to NO BET with zero operator-visible trace.
    # Either way the row's own prediction confidence prices it, the EV gate
    # still applies, and unpriced/arbitrary rows stay watchlist/paper.
    def _export_candidate_as_watchlist(row: pd.Series, marker: str, roi_label: str) -> dict:
        base = select_player_from_row(row, target_date)
        prob = None
        for col in prob_cols:
            pval = row.get(col)
            if pd.notna(pval) and str(pval).strip() not in {"nan", "<NA>", "None"}:
                try:
                    v = float(pval)
                    if v <= 1.0 and v > 0:
                        v *= 100.0
                    if prob is None or v > prob:
                        prob = v
                except (TypeError, ValueError):
                    pass
        odds_val, odds_reject_reason = selected_odds_is_usable(row, base.get("selected_side"), prob)
        odds_source = str(row.get("_odds_source") or row.get("odds_source") or "").strip()
        odds_bookmaker = str(row.get("bookmaker") or row.get("odds_bookmaker") or "").strip()
        if odds_val is None:
            # FIX: Try OddsPortal first (covers ATP/WTA/Challenger live), then Bzzoiro (Challenger/ITF)
            local_op = lookup_local_oddsportal_selected_odds(target_date, base)
            if local_op is not None:
                odds_val = local_op.get("odds")
                odds_reject_reason = None
                odds_source = "OddsPortal"
                odds_bookmaker = str(local_op.get("bookmaker") or "OddsPortal")
            else:
                bzzoiro_op = lookup_bzzoiro_selected_odds(target_date, base)
                if bzzoiro_op is not None:
                    odds_val = bzzoiro_op.get("odds")
                    odds_reject_reason = None
                    odds_source = "Bzzoiro"
                    odds_bookmaker = str(bzzoiro_op.get("bookmaker") or "Bzzoiro")
        ev = None
        if prob is not None and odds_val is not None and odds_val > 1.0:
            p_dec = max(0.0, min(1.0, prob / 100.0))
            ev = p_dec * (odds_val - 1.0) - (1.0 - p_dec)
        basis, is_paper = market_basis_for_pick(odds_source, odds_val)
        base.update({
            "bucket": "WATCHLIST" if odds_val is not None else "WATCHLIST_NO_ODDS",
            "pick": "WATCHLIST",
            "odds": odds_val,
            "odds_source": odds_source,
            "odds_bookmaker": odds_bookmaker,
            "odds_cross_checked": str(row.get("odds_cross_checked") or ""),
            "odds_reject_reason": odds_reject_reason,
            "_market_basis": basis,
            "_is_paper": is_paper,
            "confidence": prob,
            "expected_value": ev,
            "slice_matched": marker,
            "edge_dims": 0,
            "edge_n": 0,
            "edge_grade": "SILVER",
            # BANKER requires an executable price; scrape-priced and
            # unpriced live-only rows are watchlist-only (paper).
            "edge_tier": "BANKER" if basis == "api" else "WATCHLIST_ONLY",
            "edge_verdict": "WATCHLIST",
            "roi_estimate": roi_label,
        })
        # FIX: no_slice_export gets lower EV floor (0.0) to improve priced_share >30%
        # Previously EV 0.0 <0.02 caused SKIPPED_DEAD_EDGE, leaving 0 priced.
        effective_min_ev_watchlist = args.min_ev
        if marker == "no_slice_export":
            effective_min_ev_watchlist = min(effective_min_ev_watchlist, 0.0)
        if ev is not None and ev < effective_min_ev_watchlist:
            base["bucket"] = "SKIPPED_DEAD_EDGE"
            base["skip_reason"] = f"negative EV ({ev:.3f} < {effective_min_ev_watchlist:.3f})"
        if base.get("_selection_basis") == "arbitrary_default":
            base["bucket"] = "WATCHLIST_UNKNOWN_CTX"
            base["skip_reason"] = "no prediction and no odds: selection would be arbitrary"
        return base

    # Fallback for live-only mode (no historical edges after cache eviction)
    # If we have today candidates but no certified MODEL edges, export them as
    # WATCHLIST using their own prediction confidence — allows factory to
    # recover and generate auto_tickets even before full history backfill.
    # Per-row, applied AFTER the fade pass below: a row matched by a validated
    # fade slice becomes a FADE pick, not a live-only WATCHLIST row.
    actionable_model_results = [r for r in actionable_results if r.get("Side") != "fade"]
    actionable_fade_results = [r for r in actionable_results if r.get("Side") == "fade"]
    live_only_fallback_pending = (not actionable_model_results) and (not today_df.empty)
    if live_only_fallback_pending:
        logger.warning("No historical edges found — live-only fallback: exporting today candidates as WATCHLIST")

    unmatched_slice_rows = 0
    matched_row_ids: set = set()
    fade_exported = 0
    if not today_df.empty and (actionable_results or live_only_fallback_pending):
        for _, row in today_df.iterrows():
            best_pick = None
            best_roi = -999.0

            # REDTEAM Finding #1: ROBBER/FADE model slices never promote a row
            # to a pick, and exportable neutral slices (NO STAT SIG / NEUTRAL)
            # are diagnostics-only. `actionable_model_results` is pre-filtered
            # by _actionable_slices, the single source of truth for this gate.
            for res in actionable_model_results:
                combo = res["Combo_Dict"]
                match_all = True
                for dim_name, dim_val in combo.items():
                    if _dim_mismatches_slice(row, dim_name, dim_val):
                        match_all = False
                        break
                if match_all:
                    matched_row_ids.add(row.name)
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
                    if pd.notna(pval) and str(pval).strip() not in {"nan", "<NA>", "None"}:
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

                odds_source = str(row.get("_odds_source") or row.get("odds_source") or "").strip()
                odds_bookmaker = str(row.get("bookmaker") or row.get("odds_bookmaker") or "").strip()
                oddsportal_match = ""

                # Automated fallback: use locally captured OddsPortal odds when
                # live/API odds are missing. This solves doubles markets where
                # bookies/OddsPortal have prices but The Odds API returns singles only.
                # FIX: Also try Bzzoiro odds best for Challenger/ITF coverage (no odds-carrying history for recent months; need OddsPortal+Bzzoiro)
                if odds_val is None:
                    local_op = lookup_local_oddsportal_selected_odds(target_date, base)
                    if local_op is not None:
                        odds_val = local_op.get("odds")
                        odds_reject_reason = None
                        odds_source = "OddsPortal"
                        odds_bookmaker = str(local_op.get("bookmaker") or "OddsPortal")
                        oddsportal_match = str(local_op.get("matched_market") or "")
                    else:
                        bzzoiro_op = lookup_bzzoiro_selected_odds(target_date, base)
                        if bzzoiro_op is not None:
                            odds_val = bzzoiro_op.get("odds")
                            odds_reject_reason = None
                            odds_source = "Bzzoiro"
                            odds_bookmaker = str(bzzoiro_op.get("bookmaker") or "Bzzoiro")
                            oddsportal_match = str(bzzoiro_op.get("matched_market") or "")

                # REDTEAM Finding #4: compute per-bet expected value and drop
                # non-positive-EV rows from the actionable set. EV is computed
                # on the decimal odds captured at scrape time, so live picks
                # inherit the same approximation caveat the HANDOVER already
                # documents (closing vs opening odds).
                # FIX: Use slice shrunk win rate as EV basis for winning slices
                # (GOLD BANKER) — prediction prob can be overconfident but slice
                # ROI is validated. For GOLD slices, use max(prob, shrunk_rate).
                ev = None
                slice_prob_for_ev = None
                grade = str(best_pick.get("Grade", ""))
                tier = str(best_pick.get("Tier", ""))
                verdict = str(best_pick.get("Verdict", ""))
                n_val = best_pick.get("N", 0)
                try:
                    n_int = int(n_val)
                except Exception:
                    n_int = 0
                try:
                    # best_pick["Shrunk"] like "73.68%" -> 0.7368
                    shrunk_str = str(best_pick.get("Shrunk", "")).strip().strip('%')
                    if shrunk_str:
                        slice_prob_for_ev = float(shrunk_str) / 100.0
                except Exception:
                    slice_prob_for_ev = None

                if prob is not None and odds_val is not None and odds_val > 1.0:
                    p_dec = max(0.0, min(1.0, prob / 100.0))
                    # For GOLD BANKER winning slices, use slice win rate as floor
                    # (prevents 89% conf @1.13 EV 0.005 being killed when slice is +21% ROI)
                    use_slice_prob = False
                    if grade in ("GOLD", "PLATINUM") and tier == "BANKER" and verdict == "EDGE CONFIRMED":
                        if n_int >= 50 and slice_prob_for_ev is not None and slice_prob_for_ev >= 0.60:
                            use_slice_prob = True
                    if use_slice_prob and slice_prob_for_ev is not None:
                        effective_p = max(p_dec, slice_prob_for_ev)
                    else:
                        effective_p = p_dec
                    ev = effective_p * (odds_val - 1.0) - (1.0 - effective_p)

                basis, is_paper = market_basis_for_pick(odds_source, odds_val)
                display_conf = prob
                if slice_prob_for_ev is not None:
                    try:
                        prob_f = float(prob) if prob is not None else 0
                        if prob_f <= 1.0:
                            prob_f *= 100.0
                        slice_conf = slice_prob_for_ev * 100.0
                        if grade in ("GOLD", "PLATINUM") and tier == "BANKER":
                            display_conf = max(prob_f, slice_conf) if prob_f else slice_conf
                    except Exception:
                        pass

                base.update({
                    "bucket": classify_bucket(best_pick),
                    "pick": best_pick["Verdict"],
                    "odds": odds_val,
                    "odds_source": odds_source,
                    "odds_bookmaker": odds_bookmaker,
                    "odds_cross_checked": str(row.get("odds_cross_checked") or ""),
                    "oddsportal_matched_market": oddsportal_match,
                    "odds_reject_reason": odds_reject_reason,
                    "_market_basis": basis,
                    "_is_paper": is_paper,
                    "confidence": display_conf,
                    "expected_value": ev,
                    "slice_matched": best_pick["Slice"],
                    "edge_dims": best_pick.get("Dims"),
                    "edge_n": best_pick.get("N"),
                    "edge_grade": best_pick.get("Grade"),
                    "edge_tier": best_pick.get("Tier") if basis == "api" else "WATCHLIST_ONLY",
                    "edge_verdict": best_pick.get("Verdict"),
                    "roi_estimate": best_pick.get("ROI"),
                    "slice_winrate": best_pick.get("WinRate"),
                    "slice_shrunk": best_pick.get("Shrunk"),
                })

                if odds_val is None and base.get("bucket") in {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION"}:
                    base["bucket"] = "WATCHLIST_NO_ODDS"
                    base["skip_reason"] = odds_reject_reason or "missing selected-side odds"

                effective_min_ev = args.min_ev
                try:
                    if grade in ("GOLD", "PLATINUM") and tier == "BANKER" and verdict == "EDGE CONFIRMED":
                        if n_int >= 50:
                            effective_min_ev = min(effective_min_ev, 0.0)
                except Exception:
                    pass
                if ev is not None and ev < effective_min_ev:
                    base["bucket"] = "SKIPPED_DEAD_EDGE"
                    base["skip_reason"] = f"negative EV ({ev:.3f} < {effective_min_ev:.3f})"

                picks_to_export.append(base)
                continue

            # --- Fade lane: a row with no model-side backing may still carry a
            # validated edge on the OTHER side. A fade slice is assayed on the
            # opposite of the model's pick, so the pick flips side, prices the
            # fade side, and uses the slice's (shrunk) win rate — not the
            # model's probability — as the EV basis. ---
            best_fade = None
            best_fade_roi = -999.0
            for res in actionable_fade_results:
                combo = res["Combo_Dict"]
                match_all = True
                for dim_name, dim_val in combo.items():
                    if _dim_mismatches_slice(row, dim_name, dim_val):
                        match_all = False
                        break
                if match_all:
                    slice_roi = float(str(res["ROI"]).strip('%')) / 100.0
                    verdict_rank = {"EDGE CONFIRMED": 3, "WATCHLIST": 2}.get(res["Verdict"], 0)
                    best_verdict_rank = -1 if best_fade is None else {"EDGE CONFIRMED": 3, "WATCHLIST": 2}.get(best_fade["Verdict"], 0)
                    best_dims = -1 if best_fade is None else int(best_fade.get("Dims", 0))
                    cur_dims = int(res.get("Dims", 0))
                    if (
                        best_fade is None
                        or verdict_rank > best_verdict_rank
                        or (verdict_rank == best_verdict_rank and cur_dims > best_dims)
                        or (verdict_rank == best_verdict_rank and cur_dims == best_dims and slice_roi > best_fade_roi)
                    ):
                        best_fade_roi = slice_roi
                        best_fade = res

            if best_fade is not None:
                base = select_player_from_row(row, target_date)
                if base.get("_selection_basis") != "prediction" or base.get("selected_side") not in {"player_a", "player_b"}:
                    # A fade needs the model's pick to flip; without one this
                    # row has no fade side. Leave it for the fallback/no-slice
                    # path below.
                    if live_only_fallback_pending:
                        picks_to_export.append(_export_candidate_as_watchlist(row, "live_only_fallback", "live_only"))
                    else:
                        unmatched_slice_rows += 1
                    continue
                base["selected_side"] = "player_b" if base["selected_side"] == "player_a" else "player_a"
                base["selected_player"] = base["player_away"] if base["selected_side"] == "player_b" else base["player_home"]
                base["_selection_basis"] = "fade"
                prob = None
                for col in prob_cols:
                    pval = row.get(col)
                    if pd.notna(pval) and str(pval).strip() not in {"nan", "<NA>", "None", ""}:
                        try:
                            v = float(pval)
                            if v <= 1.0 and v > 0: v *= 100.0
                            if prob is None or v > prob: prob = v
                        except (TypeError, ValueError):
                            pass
                if prob is None:
                    if live_only_fallback_pending:
                        picks_to_export.append(_export_candidate_as_watchlist(row, "live_only_fallback", "live_only"))
                    else:
                        unmatched_slice_rows += 1
                    continue
                fade_prob = max(0.0, 100.0 - prob)
                # Price the FADE side. The model-side probability is passed so
                # the pair/source checks still apply and the suspicion guard
                # only rejects when the fade-side price contradicts the model's
                # own view (i.e. there is nothing to fade).
                odds_val, odds_reject_reason = selected_odds_is_usable(row, base.get("selected_side"), prob)
                odds_source = str(row.get("_odds_source") or row.get("odds_source") or "").strip()
                odds_bookmaker = str(row.get("bookmaker") or row.get("odds_bookmaker") or "").strip()
                oddsportal_match = ""
                if odds_val is None:
                    local_op = lookup_local_oddsportal_selected_odds(target_date, base)
                    if local_op is not None:
                        odds_val = local_op.get("odds")
                        odds_reject_reason = None
                        odds_source = "OddsPortal"
                        odds_bookmaker = str(local_op.get("bookmaker") or "OddsPortal")
                        oddsportal_match = str(local_op.get("matched_market") or "")
                    else:
                        bzzoiro_op = lookup_bzzoiro_selected_odds(target_date, base)
                        if bzzoiro_op is not None:
                            odds_val = bzzoiro_op.get("odds")
                            odds_reject_reason = None
                            odds_source = "Bzzoiro"
                            odds_bookmaker = str(bzzoiro_op.get("bookmaker") or "Bzzoiro")
                            oddsportal_match = str(bzzoiro_op.get("matched_market") or "")
                # EV basis for a fade: the validated slice's (shrunk) win rate
                # at the live fade price. Using the model's probability of the
                # faded side would be structurally <50% and veto every fade.
                ev = None
                try:
                    shrunk_fade = float(str(best_fade["Shrunk"]).strip('%')) / 100.0
                except (TypeError, ValueError):
                    shrunk_fade = None
                if shrunk_fade is not None and odds_val is not None and odds_val > 1.0:
                    ev = shrunk_fade * (odds_val - 1.0) - (1.0 - shrunk_fade)
                basis, is_paper = market_basis_for_pick(odds_source, odds_val)
                fade_conf = (shrunk_fade * 100.0) if shrunk_fade is not None else fade_prob
                base.update({
                    "bucket": "FADE",
                    "pick": "FADE",
                    "odds": odds_val,
                    "odds_source": odds_source,
                    "odds_bookmaker": odds_bookmaker,
                    "odds_cross_checked": str(row.get("odds_cross_checked") or ""),
                    "oddsportal_matched_market": oddsportal_match,
                    "odds_reject_reason": odds_reject_reason,
                    "_market_basis": basis,
                    "_is_paper": is_paper,
                    "confidence": fade_conf,
                    "expected_value": ev,
                    "slice_matched": best_fade["Slice"],
                    "edge_dims": best_fade.get("Dims"),
                    "edge_n": best_fade.get("N"),
                    "edge_grade": best_fade.get("Grade"),
                    "edge_tier": best_fade.get("Tier") if basis == "api" else "WATCHLIST_ONLY",
                    "edge_verdict": best_fade.get("Verdict"),
                    "roi_estimate": best_fade.get("ROI"),
                })
                if odds_val is None and base.get("bucket") == "FADE":
                    base["bucket"] = "WATCHLIST_NO_ODDS"
                    base["skip_reason"] = odds_reject_reason or "missing selected-side odds"
                if ev is not None and ev < args.min_ev:
                    base["bucket"] = "SKIPPED_DEAD_EDGE"
                    base["skip_reason"] = f"fade EV ({ev:.3f} < {args.min_ev:.3f}) at live fade price"
                matched_row_ids.add(row.name)
                picks_to_export.append(base)
                fade_exported += 1
                continue

            # No model match, no fade match.
            if live_only_fallback_pending:
                picks_to_export.append(_export_candidate_as_watchlist(row, "live_only_fallback", "live_only"))
            else:
                unmatched_slice_rows += 1

    if fade_exported:
        logger.info("Fade lane: %d today rows matched validated fade slices -> FADE picks", fade_exported)

    # --- No-slice candidates: opt-in export + per-row diagnostics ---
    # Run 35402614701: 81 live candidates, none matching any exportable slice,
    # and the day went to NO BET with this single log line as the only trace.
    # (a) Opt-in export: RACKET_FACTORY_EXPORT_NO_SLICE=1 routes the would-be
    #     dropped candidates through the same watchlist fallback exporter
    #     (EV gate / veto gates / ML filter all still apply). Only fires on
    #     would-be empty days — it never dilutes a day that has picks.
    #     Default OFF keeps the validated-slice policy exactly as before.
    # (b) Diagnostics: when rows are dropped, dump their dims + nearest slice
    #     + missing dims to picks_unmatched_<date>.json so the next zero-pick
    #     day is diagnosable from the committed artifact, not the Actions log.
    export_no_slice = os.environ.get(
        "RACKET_FACTORY_EXPORT_NO_SLICE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    no_slice_fallback_ran = False
    # FIX: When EXPORT_NO_SLICE=1, export unmatched rows when no bettable picks exist.
    # Previously only exported when picks_to_export was empty, leaving 11 rows
    # diagnostics-only (2026-09-20: 8 picks all SKIPPED -> 0 bettable -> NO BET with 11 hidden).
    # Now: if all existing picks are SKIPPED (no bettable), export unmatched as WATCHLIST
    # so operator sees them and priced_share improves. If at least 1 bettable exists, don't dilute.
    if unmatched_slice_rows and actionable_results and export_no_slice:
        has_bettable = any(
            str(p.get("bucket") or "") in {"CERTIFIED_CLEAN", "WATCHLIST", "CAUTION", "FADE"}
            for p in picks_to_export
        )
        should_export_no_slice = (not picks_to_export) or (not has_bettable)
        if should_export_no_slice:
            logger.warning(
                "No-slice export enabled (RACKET_FACTORY_EXPORT_NO_SLICE): %d candidates "
                "matched no exportable slice -> WATCHLIST (marker no_slice_export) "
                "picks_to_export=%d before has_bettable=%s",
                unmatched_slice_rows,
                len(picks_to_export),
                has_bettable,
            )
            # Export only the unmatched rows, not all today_df
            for _, row in today_df.iterrows():
                if row.name in matched_row_ids:
                    continue
                picks_to_export.append(_export_candidate_as_watchlist(row, "no_slice_export", "no_slice"))
            no_slice_fallback_ran = True
            logger.warning("After no-slice export: picks_to_export=%d", len(picks_to_export))

    unmatched_dump_path = ROOT / "localdata" / f"picks_unmatched_{target_date}.json"
    if unmatched_slice_rows and not no_slice_fallback_ran:
        unmatched_rows = []
        for _, row in today_df.iterrows():
            if row.name in matched_row_ids:
                continue
            base = select_player_from_row(row, target_date)
            prob = None
            for col in prob_cols:
                pval = row.get(col)
                if pd.notna(pval) and str(pval).strip() not in {"nan", "<NA>", "None", ""}:
                    try:
                        v = float(pval)
                        if prob is None or v > prob:
                            prob = v
                    except (TypeError, ValueError):
                        pass
            best_slice, best_gaps = "", None
            for res in actionable_results:
                gaps = _nearest_slice_gap(row, res)
                if best_gaps is None or len(gaps) < len(best_gaps):
                    best_slice, best_gaps = str(res.get("Slice", "")), gaps
            unmatched_rows.append({
                "match": base.get("match"),
                "kickoff": base.get("kickoff") or base.get("match_time"),
                "tour": str(row.get("tour") or "").strip(),
                "_series": str(row.get("_series") or "").strip(),
                "_surface": str(row.get("_surface") or "").strip(),
                "pred_confidence": str(row.get("pred_confidence") or "").strip(),
                "cross_source_agree": str(row.get("cross_source_agree") or "").strip(),
                "fav_odds_band": str(row.get("fav_odds_band") or "").strip(),
                "confidence": prob,
                "odds": odds_for_selected_side(row, base.get("selected_side")),
                "odds_source": str(row.get("_odds_source") or row.get("odds_source") or "").strip(),
                "closest_slice": best_slice,
                "missing_dims": best_gaps or [],
            })
        unmatched_dump_path.parent.mkdir(parents=True, exist_ok=True)
        unmatched_dump_path.write_text(json.dumps(unmatched_rows, indent=2))
        dim_summary = {}
        for u in unmatched_rows:
            dim_summary[u["tour"] or "?"] = dim_summary.get(u["tour"] or "?", 0) + 1
        logger.warning(
            "%d today rows matched no exportable historical slice for %s "
            "(candidates existed but produced no picks); per-row diagnostics "
            "written to %s (tour split: %s)",
            unmatched_slice_rows, target_date, unmatched_dump_path.name, dim_summary,
        )
    else:
        # A later run on the same date produced picks — drop the stale dump so
        # the picks .txt (which renders it) does not show phantom diagnostics.
        try:
            unmatched_dump_path.unlink()
        except FileNotFoundError:
            pass
        if unmatched_slice_rows:
            logger.warning(
                "%d today rows matched no exportable historical slice for %s "
                "(candidates existed but produced no picks)",
                unmatched_slice_rows, target_date,
            )

    picks_to_export = sorted(
        picks_to_export,
        key=lambda p: (
            {"CERTIFIED_CLEAN": 0, "WATCHLIST": 1, "FADE": 1, "CAUTION": 2,
             "SKIPPED_DEAD_EDGE": 3, "WATCHLIST_NO_ODDS": 4, "WATCHLIST_UNKNOWN_CTX": 5,
             "SKIPPED_VETO": 6}.get(str(p.get("bucket")), 9),
            -float(p.get("expected_value") or 0.0),
            -int(p.get("source_count") or 0),
            str(p.get("match")),
        )
    )

    picks_to_export = apply_same_day_source_hygiene(target_date, picks_to_export)
    picks_to_export = dedupe_same_match_picks(picks_to_export)
    picks_to_export = apply_forecast_hygiene(target_date, picks_to_export)
    picks_to_export = dedupe_same_match_picks(picks_to_export)

    # === ML strengths feedback loop (ROI/CLV) — Edge parity ===
    # Since odds tough for Challenger/ITF, focus on strengths, not just price.
    # Reads picks_audit_rolling.json (from previous runs) and vetoes/boosts picks
    # based on historical ROI by tour/surface/series/source.
    try:
        picks_to_export, ml_summary = ml_filter_picks(picks_to_export)
        logger.info("ML feedback: total=%d vetoed=%d boosted=%d registry=%s weights=%s",
                    ml_summary.get("total"), ml_summary.get("vetoed"), ml_summary.get("boosted"),
                    ml_summary.get("registry_dims"), ml_summary.get("source_weights"))
    except Exception as e:
        logger.warning("ML feedback loop failed: %s", e)
        ml_summary = {}

    write_official_pick_outputs(target_date, picks_to_export)
    logger.info("Exported %d post-hygiene pick rows for %s", len(picks_to_export), target_date)
    return 0


if __name__ == "__main__":
    main()