"""
Racket Factory Warehouse
Handles the merging and deduplication of various tennis data sources.
"""
import json
import time
import pandas as pd
from pathlib import Path
import logging
import os
import requests
from bs4 import BeautifulSoup
import re
from typing import Optional
from datetime import date, timedelta
from racketfactory.entities import player_key
from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.forebet import ForebetPredictor, name_signature

logger = logging.getLogger(__name__)


def load_warehouse_env() -> None:
    """Load repo-local .env files before live odds/API enrichment.

    Existing process env wins. Secrets are never printed.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    root = Path(__file__).resolve().parents[2]
    for env_path in (root / ".env", root / "localdata" / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)


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


# Live prediction pages are not bookmaker APIs. They can contain decimal-looking
# scores, rankings, widget values, or alternate-market prices. Keep the sanity
# guard here in the warehouse because this is where live source rows are merged
# into the odds-first data contract.
MIN_DECIMAL_ODDS = 1.01
MAX_DECIMAL_ODDS = 51.0
MIN_TWO_WAY_IMPLIED_SUM = 0.98
MAX_TWO_WAY_IMPLIED_SUM = 1.35
MAX_PROBABILITY_ODDS_EV = 0.75
MAX_FAIR_ODDS_RATIO_FOR_STRONG_PROB = 1.75
STRONG_PROBABILITY = 0.60


def coerce_decimal_odds(value: object) -> float | None:
    if value is None:
        return None
    try:
        odd = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(odd) or odd < MIN_DECIMAL_ODDS or odd > MAX_DECIMAL_ODDS:
        return None
    return odd


def normalize_probability(value: object) -> float | None:
    if value is None:
        return None
    try:
        prob = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(prob) or prob <= 0:
        return None
    if prob > 1.0:
        prob /= 100.0
    if prob <= 0 or prob > 1.0:
        return None
    return prob


def valid_two_way_decimal_pair(odds_a: object, odds_b: object) -> bool:
    oa = coerce_decimal_odds(odds_a)
    ob = coerce_decimal_odds(odds_b)
    if oa is None or ob is None:
        return False
    implied_sum = (1.0 / oa) + (1.0 / ob)
    return MIN_TWO_WAY_IMPLIED_SUM <= implied_sum <= MAX_TWO_WAY_IMPLIED_SUM


def odds_suspicious_for_probability(probability: object, odds: object) -> bool:
    """Flag prices that are more likely side-inverted than genuine value.

    This is not a bet veto by itself.  It is used to repair live scraper rows
    where the page exposed a valid two-way pair, but the pair landed on the
    wrong home/away side during extraction or source aggregation.
    """
    p = normalize_probability(probability)
    o = coerce_decimal_odds(odds)
    if p is None or o is None:
        return False
    if (p * o - 1.0) > MAX_PROBABILITY_ODDS_EV:
        return True
    if p >= STRONG_PROBABILITY and o > (1.0 / p) * MAX_FAIR_ODDS_RATIO_FOR_STRONG_PROB:
        return True
    return False


def align_odds_to_probabilities(
    prob_home: object,
    prob_away: object,
    odds_home: object,
    odds_away: object,
) -> tuple[float | None, float | None]:
    """Repair likely home/away odds inversions using the source probabilities.

    Example from the bad report class: Ostapenko 79% got @9.50 while Dart had
    @1.02.  The two-way pair itself is coherent; the problem is side assignment.
    In that case we swap the pair instead of rejecting it.
    """
    oh = coerce_decimal_odds(odds_home)
    oa = coerce_decimal_odds(odds_away)
    if oh is None or oa is None:
        return oh, oa
    if not valid_two_way_decimal_pair(oh, oa):
        return oh, oa

    ph = normalize_probability(prob_home)
    pa = normalize_probability(prob_away)
    home_looks_inverted = (
        ph is not None
        and ph >= STRONG_PROBABILITY
        and odds_suspicious_for_probability(ph, oh)
        and not odds_suspicious_for_probability(ph, oa)
    )
    away_looks_inverted = (
        pa is not None
        and pa >= STRONG_PROBABILITY
        and odds_suspicious_for_probability(pa, oa)
        and not odds_suspicious_for_probability(pa, oh)
    )
    if home_looks_inverted or away_looks_inverted:
        return oa, oh
    return oh, oa


def normalize_person_name(name: str) -> str:
    name = " ".join(str(name or "").replace(".", " ").replace("-", " ").replace("/", " / ").split()).strip().lower()
    if not name:
        return ""
    parts = name.split()
    if "/" in parts:
        return " ".join(parts)
    if len(parts) >= 2 and len(parts[0]) == 1:
        parts = parts[1:]
    return " ".join(parts)


def surname_tokens(name: str) -> tuple[str, ...]:
    parts = [p for p in normalize_person_name(name).split() if p != "/"]
    if not parts:
        return tuple()
    if len(parts) == 1:
        return (parts[-1],)
    return tuple(parts[-2:])


def live_player_key(name: str) -> str:
    normalized = normalize_person_name(name)
    if "/" in normalized:
        parts = [part.strip() for part in normalized.split("/")]
        member_keys = []
        for part in parts:
            tail = " ".join(surname_tokens(part))
            member_keys.append(tail or normalize_person_name(part) or player_key(part))
        return " / ".join(member_keys)
    tail = " ".join(surname_tokens(name))
    return tail or normalized or player_key(name)


def canonical_display_name(name: str) -> str:
    raw = " ".join(str(name or "").split()).strip()
    if not raw:
        return ""
    if "/" in raw:
        return " / ".join(part.strip() for part in raw.split("/"))
    return raw


def choose_display_name(values: pd.Series) -> str:
    vals = [canonical_display_name(v) for v in values if str(v or "").strip()]
    if not vals:
        return ""
    vals = sorted(set(vals), key=lambda x: (-len(x), x))
    return vals[0]


def names_match(name_a: str, name_b: str) -> bool:
    norm_a = normalize_person_name(name_a)
    norm_b = normalize_person_name(name_b)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    if surname_tokens(name_a) == surname_tokens(name_b):
        return True
    toks_a = tuple(p for p in norm_a.split() if p != "/")
    toks_b = tuple(p for p in norm_b.split() if p != "/")
    if len(toks_a) == len(toks_b):
        shared = sum(1 for x, y in zip(toks_a, toks_b) if x == y)
        if shared >= max(1, len(toks_a) - 1):
            return True
    set_a = set(toks_a)
    set_b = set(toks_b)
    overlap = set_a & set_b
    return len(overlap) >= min(len(set_a), len(set_b)) and len(overlap) >= 1


def rows_refer_to_same_match(a: pd.Series, b: pd.Series) -> bool:
    if str(a.get("match_date", "")) != str(b.get("match_date", "")):
        return False
    if str(a.get("tour", "")) != str(b.get("tour", "")):
        return False
    if str(a.get("match_type", "")) != str(b.get("match_type", "")):
        return False
    return (
        (names_match(a.get("player_home", ""), b.get("player_home", "")) and names_match(a.get("player_away", ""), b.get("player_away", "")))
        or
        (names_match(a.get("player_home", ""), b.get("player_away", "")) and names_match(a.get("player_away", ""), b.get("player_home", "")))
    )


def _first_present(values: pd.Series, *, reject: set[str] | None = None) -> str:
    reject = reject or {"", "nan", "None", "<NA>", "NA"}
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text and text not in reject:
            return value
    return ""


def _swap_prediction_side(value: object) -> object:
    text = str(value or "").strip()
    if text in {"1", "player_a"}:
        return "2" if text == "1" else "player_b"
    if text in {"2", "player_b"}:
        return "1" if text == "2" else "player_a"
    return value


def _orient_live_source_row(row: pd.Series, base_home: str, base_away: str) -> dict:
    """Orient one source row to the canonical home/away display names."""
    home = canonical_display_name(row.get("player_home", ""))
    away = canonical_display_name(row.get("player_away", ""))
    swapped = (
        names_match(home, base_away)
        and names_match(away, base_home)
        and not (names_match(home, base_home) and names_match(away, base_away))
    )

    if swapped:
        prob_home = row.get("prob_away")
        prob_away = row.get("prob_home")
        odds_home = row.get("odds_away")
        odds_away = row.get("odds_home")
        predicted_winner = _swap_prediction_side(row.get("predicted_winner"))
    else:
        prob_home = row.get("prob_home")
        prob_away = row.get("prob_away")
        odds_home = row.get("odds_home")
        odds_away = row.get("odds_away")
        predicted_winner = row.get("predicted_winner")

    prob_home_num = pd.to_numeric(prob_home, errors="coerce")
    prob_away_num = pd.to_numeric(prob_away, errors="coerce")
    odds_home_num, odds_away_num = align_odds_to_probabilities(
        prob_home_num, prob_away_num, odds_home, odds_away
    )

    return {
        "match_date": row.get("match_date"),
        "match_time": row.get("match_time", ""),
        "tour": row.get("tour"),
        "match_type": row.get("match_type"),
        "player_home": base_home,
        "player_away": base_away,
        "tournament": row.get("tournament", ""),
        "country": row.get("country", ""),
        "surface": row.get("surface", ""),
        "context_used": row.get("context_used", ""),
        "_series": row.get("_series", ""),
        "source": row.get("source", ""),
        "predicted_winner": predicted_winner,
        "prob_home": prob_home_num,
        "prob_away": prob_away_num,
        "odds_home": odds_home_num,
        "odds_away": odds_away_num,
    }


def _live_row_selected_prob(row: dict) -> object:
    pick = str(row.get("predicted_winner", "") or "").strip()
    if pick in {"1", "player_a"}:
        return row.get("prob_home")
    if pick in {"2", "player_b"}:
        return row.get("prob_away")
    return None


def _live_row_selected_odds(row: dict) -> object:
    pick = str(row.get("predicted_winner", "") or "").strip()
    if pick in {"1", "player_a"}:
        return row.get("odds_home")
    if pick in {"2", "player_b"}:
        return row.get("odds_away")
    return None


def _live_odds_pair_is_usable(row: dict) -> bool:
    """Only coherent two-way pairs may feed best-price aggregation."""
    return valid_two_way_decimal_pair(row.get("odds_home"), row.get("odds_away"))


def collapse_live_card(card: pd.DataFrame) -> pd.DataFrame:
    if card.empty:
        return card
    ordered = card.copy()
    ordered["name_score"] = ordered.get("player_home", "").astype(str).str.len() + ordered.get("player_away", "").astype(str).str.len()
    ordered = ordered.sort_values(by=[c for c in ["match_date", "tour", "match_type", "name_score"] if c in ordered.columns], ascending=[True, True, True, False]).reset_index(drop=True)
    rows = []
    used = set()
    for i, row in ordered.iterrows():
        if i in used:
            continue
        group = [i]
        used.add(i)
        for j in range(i + 1, len(ordered)):
            if j in used:
                continue
            other = ordered.iloc[j]
            if rows_refer_to_same_match(row, other):
                group.append(j)
                used.add(j)

        g = ordered.iloc[group].copy()
        g["name_score"] = g.get("player_home", "").astype(str).str.len() + g.get("player_away", "").astype(str).str.len()
        g = g.sort_values("name_score", ascending=False)
        base = g.iloc[0]
        base_home = canonical_display_name(base.get("player_home", ""))
        base_away = canonical_display_name(base.get("player_away", ""))

        oriented = [_orient_live_source_row(rr, base_home, base_away) for _, rr in g.iterrows()]
        odds_rows = [rr for rr in oriented if _live_odds_pair_is_usable(rr)]

        def _max_numeric(key: str, source_rows: list[dict]) -> object:
            vals = [pd.to_numeric(rr.get(key), errors="coerce") for rr in source_rows]
            vals = [float(v) for v in vals if pd.notna(v)]
            return max(vals) if vals else pd.NA

        # Use the best usable price among sources, but only after each source's
        # two-way pair passed sanity checks.  This prevents one poisoned scrape
        # (e.g. @9.50 parsed from page text) from inflating EV for everyone.
        odds_home = _max_numeric("odds_home", odds_rows)
        odds_away = _max_numeric("odds_away", odds_rows)

        rows.append({
            "match_date": oriented[0].get("match_date"),
            "match_time": _first_present(pd.Series([rr.get("match_time", "") for rr in oriented])),
            "tour": oriented[0].get("tour"),
            "match_type": oriented[0].get("match_type"),
            "player_home": base_home,
            "player_away": base_away,
            "tournament": _first_present(pd.Series([rr.get("tournament", "") for rr in oriented])),
            "country": _first_present(pd.Series([rr.get("country", "") for rr in oriented])),
            "surface": _first_present(pd.Series([rr.get("surface", "") for rr in oriented])),
            "context_used": _first_present(pd.Series([rr.get("context_used", "") for rr in oriented])),
            "_series": _first_present(pd.Series([rr.get("_series", "") for rr in oriented])),
            "source": ", ".join(sorted(set(str(rr.get("source", "")) for rr in oriented if str(rr.get("source", "")).strip()))),
            "predicted_winner": _first_present(pd.Series([rr.get("predicted_winner", "") for rr in oriented])),
            "prob_home": _max_numeric("prob_home", oriented),
            "prob_away": _max_numeric("prob_away", oriented),
            "odds_home": odds_home,
            "odds_away": odds_away,
        })
    return pd.DataFrame(rows)



def _match_api_odds_row(card_row: pd.Series, odds_rows: list[dict]) -> tuple[dict | None, bool]:
    """Return matching API odds row and whether it was reversed vs card row."""
    card_date = str(card_row.get("match_date", "") or "")[:10]
    home = card_row.get("player_home", "")
    away = card_row.get("player_away", "")
    for odds_row in odds_rows:
        odds_date = str(odds_row.get("match_date", "") or "")[:10]
        if card_date and odds_date and card_date != odds_date:
            continue
        api_home = odds_row.get("player_home", "")
        api_away = odds_row.get("player_away", "")
        if names_match(home, api_home) and names_match(away, api_away):
            return odds_row, False
        if names_match(home, api_away) and names_match(away, api_home):
            return odds_row, True
    return None, False


THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
DEFAULT_THE_ODDS_API_SPORTS = "tennis_atp_wimbledon,tennis_wta_wimbledon,tennis"


def the_odds_api_sports() -> tuple[str, ...]:
    """Return configured The Odds API sport keys.

    The Odds API does not expose generic `tennis_atp` / `tennis_wta` sport
    keys. Tennis is tournament-keyed, e.g. `tennis_atp_wimbledon`.
    We read .env at call time so daily/systemd runs pick up local config.
    """
    load_warehouse_env()
    raw = (
        os.getenv("THE_ODDS_API_SPORT_KEYS")
        or os.getenv("THE_ODDS_API_SPORTS")
        or DEFAULT_THE_ODDS_API_SPORTS
    )
    sports = tuple(x.strip() for x in str(raw).split(",") if x.strip())
    return sports or tuple(DEFAULT_THE_ODDS_API_SPORTS.split(","))



def the_odds_api_keys() -> tuple[str, ...]:
    """Return configured The Odds API keys without printing secrets.

    Supports:
      THE_ODDS_API_KEYS=key1,key2,key3
      THE_ODDS_API_KEY=key1
    """
    load_warehouse_env()
    keys: list[str] = []

    multi = os.getenv("THE_ODDS_API_KEYS", "")
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())

    single = os.getenv("THE_ODDS_API_KEY", "")
    if single and single.strip():
        keys.append(single.strip())

    # De-dupe while preserving order.
    seen = set()
    out = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def _redacted_key_label(index: int, total: int) -> str:
    return f"key {index + 1}/{total}"


def _local_date_from_api_time(value: object) -> str:
    """Extract YYYY-MM-DD from The Odds API ISO commence_time.

    Keep this deliberately simple: The Odds API returns ISO UTC strings such
    as 2026-06-29T10:00:00Z. For tennis schedule matching we only need the
    date part, and we never print secrets here.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "T" in raw:
        return raw.split("T", 1)[0]
    return raw[:10]


def _the_odds_api_cache_file(target_date: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    cache_dir = root / "localdata"
    return cache_dir / f"theoddsapi_odds_cache_{str(target_date)[:10]}.json"


def _the_odds_api_cache_ttl_seconds() -> int:
    try:
        minutes = float(os.getenv("THE_ODDS_API_CACHE_TTL_MINUTES", "30"))
    except ValueError:
        minutes = 30.0
    return max(0, int(minutes * 60))


def _the_odds_api_cache_disabled() -> bool:
    return os.getenv("THE_ODDS_API_DISABLE_CACHE", "").strip().lower() in {"1", "true", "yes", "on"}


def _load_the_odds_api_cache(target_date: str, sports: tuple[str, ...], regions: str, bookmakers: str) -> list[dict] | None:
    """Load cached The Odds API rows for this date/config if fresh.

    The cache stores parsed rows, never API keys. It prevents duplicate quota
    burn across repeated warehouse builds in one daily run.
    """
    if _the_odds_api_cache_disabled():
        return None
    ttl = _the_odds_api_cache_ttl_seconds()
    if ttl <= 0:
        return None

    path = _the_odds_api_cache_file(target_date)
    if not path.exists():
        return None

    try:
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None

        payload = json.loads(path.read_text())
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}

        if tuple(meta.get("sports", [])) != tuple(sports):
            return None
        if str(meta.get("regions", "")) != str(regions):
            return None
        if str(meta.get("bookmakers", "")) != str(bookmakers):
            return None

        rows = payload.get("rows", [])
        if isinstance(rows, list):
            logger.info("Loaded %d The Odds API tennis rows for %s from cache %s", len(rows), target_date, path)
            return rows
    except Exception as exc:
        logger.warning("Could not read The Odds API cache %s: %s", path, exc)

    return None


def _write_the_odds_api_cache(target_date: str, sports: tuple[str, ...], regions: str, bookmakers: str, rows: list[dict]) -> None:
    """Persist parsed The Odds API rows without storing secrets."""
    if _the_odds_api_cache_disabled():
        return

    try:
        path = _the_odds_api_cache_file(target_date)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "meta": {
                "target_date": str(target_date)[:10],
                "sports": list(sports),
                "regions": str(regions),
                "bookmakers": str(bookmakers),
                "cached_at": int(time.time()),
            },
            "rows": rows,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        logger.info("Cached %d The Odds API tennis rows for %s to %s", len(rows), target_date, path)
    except Exception as exc:
        logger.warning("Could not write The Odds API cache for %s: %s", target_date, exc)

def fetch_the_odds_api_rows(target_date: str) -> list[dict]:
    """Fetch The Odds API H2H tennis prices for the target date.

    Rotates across THE_ODDS_API_KEYS / THE_ODDS_API_KEY without printing
    secrets. Prediction-site scraped prices are kept only as debug/fallback
    visibility and must not drive live EV.
    """
    load_warehouse_env()
    sports = the_odds_api_sports()

    try:
        start = date.fromisoformat(str(target_date)[:10])
    except ValueError:
        start = date.today()
    end = start + timedelta(days=1)

    regions = os.getenv("THE_ODDS_API_REGIONS", "eu,uk,us")
    bookmakers = os.getenv("THE_ODDS_API_BOOKMAKERS", "").strip()

    cached_rows = _load_the_odds_api_cache(str(target_date)[:10], sports, regions, bookmakers)
    if cached_rows is not None:
        return cached_rows

    api_keys = the_odds_api_keys()
    if not api_keys:
        logger.warning("THE_ODDS_API_KEY missing; live API odds unavailable.")
        return []

    rows: list[dict] = []

    key_index = 0
    exhausted_keys: set[int] = set()

    for sport in sports:
        params_base = {
            "regions": regions,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "commenceTimeFrom": f"{start.isoformat()}T00:00:00Z",
            "commenceTimeTo": f"{end.isoformat()}T00:00:00Z",
        }
        if bookmakers:
            params_base["bookmakers"] = bookmakers
            params_base.pop("regions", None)

        resp = None
        events = None

        for _attempt in range(len(api_keys)):
            if len(exhausted_keys) >= len(api_keys):
                logger.warning("The Odds API: all configured keys exhausted/unavailable.")
                break

            # Advance to a non-exhausted key.
            while key_index in exhausted_keys:
                key_index = (key_index + 1) % len(api_keys)

            params = dict(params_base)
            params["apiKey"] = api_keys[key_index]
            key_label = _redacted_key_label(key_index, len(api_keys))

            try:
                resp = requests.get(
                    f"{THE_ODDS_API_BASE}/sports/{sport}/odds/",
                    params=params,
                    timeout=20,
                )

                remaining_raw = resp.headers.get("x-requests-remaining")
                used_raw = resp.headers.get("x-requests-used")
                last_raw = resp.headers.get("x-requests-last")

                if resp.status_code == 200:
                    try:
                        remaining = int(remaining_raw) if remaining_raw is not None else None
                    except ValueError:
                        remaining = None

                    if remaining is not None and remaining <= 0:
                        exhausted_keys.add(key_index)

                    logger.info(
                        "The Odds API %s %s: remaining=%s used=%s last=%s",
                        sport,
                        key_label,
                        remaining_raw,
                        used_raw,
                        last_raw,
                    )
                    events = resp.json()
                    break

                if resp.status_code in {401, 403, 429}:
                    logger.warning(
                        "The Odds API %s failed with %s on %s; rotating key.",
                        sport,
                        resp.status_code,
                        key_label,
                    )
                    exhausted_keys.add(key_index)
                    key_index = (key_index + 1) % len(api_keys)
                    continue

                logger.warning("The Odds API failed for %s: HTTP %s", sport, resp.status_code)
                break

            except Exception as e:
                logger.warning("The Odds API request failed for %s on %s: %s", sport, key_label, e)
                exhausted_keys.add(key_index)
                key_index = (key_index + 1) % len(api_keys)
                continue

        if not isinstance(events, list):
            continue

        for event in events:
            if not isinstance(event, dict):
                continue
            home = str(event.get("home_team") or "").strip()
            away = str(event.get("away_team") or "").strip()
            if not home or not away:
                continue

            best_home = best_away = None
            best_home_book = best_away_book = ""
            for book in event.get("bookmakers") or []:
                if not isinstance(book, dict):
                    continue
                book_name = str(book.get("title") or book.get("key") or "")
                for market in book.get("markets") or []:
                    if not isinstance(market, dict) or market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes") or []:
                        if not isinstance(outcome, dict):
                            continue
                        name = str(outcome.get("name") or "").strip()
                        try:
                            price = float(outcome.get("price"))
                        except (TypeError, ValueError):
                            continue
                        if names_match(name, home):
                            if best_home is None or price > best_home:
                                best_home = price
                                best_home_book = book_name
                        elif names_match(name, away):
                            if best_away is None or price > best_away:
                                best_away = price
                                best_away_book = book_name

            if best_home is None and best_away is None:
                continue

            rows.append({
                "match_date": _local_date_from_api_time(event.get("commence_time")) or str(target_date)[:10],
                "match_time": str(event.get("commence_time") or "")[11:16],
                "player_home": home,
                "player_away": away,
                "odds_home": best_home,
                "odds_away": best_away,
                "odds_home_bookmaker": best_home_book,
                "odds_away_bookmaker": best_away_book,
                "source": "TheOddsAPI",
                "sport_key": sport,
                "event_id": event.get("id"),
            })

        # Move to next non-exhausted key for the next sport to spread usage
        # when multiple keys are configured.
        if api_keys:
            key_index = (key_index + 1) % len(api_keys)

    logger.info("Fetched %d The Odds API tennis rows for %s", len(rows), target_date)
    _write_the_odds_api_cache(str(target_date)[:10], sports, regions, bookmakers, rows)
    return rows

def enrich_live_card_with_api_odds(card: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """Attach The Odds API prices as the primary live pricing source.

    Scraped BetClan/Forebet prices are preserved as debug columns only. If API
    odds are unavailable, odds_home/odds_away are left empty so mine_edges puts
    the row in WATCHLIST_NO_ODDS instead of computing EV from scrape noise.
    """
    if card.empty:
        return card

    out = card.copy()
    out["scraped_odds_home"] = out.get("odds_home", pd.Series(index=out.index, dtype=object))
    out["scraped_odds_away"] = out.get("odds_away", pd.Series(index=out.index, dtype=object))
    out["api_odds_home"] = pd.NA
    out["api_odds_away"] = pd.NA
    out["odds_source"] = ""
    out["odds_bookmaker"] = ""
    out["odds_home"] = pd.NA
    out["odds_away"] = pd.NA

    odds_rows = fetch_the_odds_api_rows(target_date)
    if not odds_rows:
        logger.info("No The Odds API odds matched live card for %s; rows will remain WATCHLIST_NO_ODDS if selected.", target_date)
        return out

    matched = 0
    for idx, row in out.iterrows():
        odds_row, reversed_order = _match_api_odds_row(row, odds_rows)
        if odds_row is None:
            continue
        if reversed_order:
            api_home = odds_row.get("odds_away")
            api_away = odds_row.get("odds_home")
        else:
            api_home = odds_row.get("odds_home")
            api_away = odds_row.get("odds_away")

        api_home, api_away = align_odds_to_probabilities(
            row.get("prob_home"), row.get("prob_away"), api_home, api_away
        )
        if not valid_two_way_decimal_pair(api_home, api_away):
            continue

        out.at[idx, "api_odds_home"] = api_home
        out.at[idx, "api_odds_away"] = api_away
        out.at[idx, "odds_home"] = api_home
        out.at[idx, "odds_away"] = api_away
        out.at[idx, "odds_source"] = "TheOddsAPI"
        out.at[idx, "odds_bookmaker"] = odds_row.get("bookmaker") or "The Odds API"
        matched += 1

    logger.info("Matched The Odds API odds for %d/%d live card rows", matched, len(out))
    return out

def build_live_rows() -> pd.DataFrame:
    rows = []
    for source_name, predictor, fetcher in [
        ("PredixSport", PredixSportPredictor(), lambda p: p.fetch_daily()),
        ("BetClan", BetClanPredictor(), lambda p: p.fetch_daily()),
        ("Forebet", ForebetPredictor(), lambda p: p.fetch_daily_predictions("today")),
    ]:
        try:
            preds = fetcher(predictor)
        except Exception as e:
            logger.warning("Live source %s failed during warehouse build: %s", source_name, e)
            preds = []
        for row in preds:
            row = dict(row)
            row["source"] = source_name
            rows.append(row)
    if not rows:
        return pd.DataFrame()

    card = pd.DataFrame(rows)
    if card.empty:
        return card

    today_str = date.today().isoformat()
    if "match_date" in card.columns:
        card["match_date"] = card["match_date"].astype(str).str.strip()
        card = card[card["match_date"] == today_str].copy()
    if card.empty:
        return card

    card["player_home"] = card.get("player_home", "").astype(str).map(canonical_display_name)
    card["player_away"] = card.get("player_away", "").astype(str).map(canonical_display_name)
    card = card[(card["player_home"] != "") & (card["player_away"] != "")].copy()
    if card.empty:
        return card

    if "tour_slug" in card.columns:
        card = card[~((card["source"] == "Forebet") & (~card["tour_slug"].astype(str).str.contains("tennis|atp|wta|challenger", case=False, na=False)))].copy()
    if card.empty:
        return card

    card["match_type"] = card.apply(
        lambda r: "Doubles" if "/" in str(r.get("player_home", "")) or "/" in str(r.get("player_away", "")) else "Singles",
        axis=1,
    )

    context_priority_cols = ["tournament", "tour_slug", "tournament_slug", "event_level", "event_text", "match_label", "event", "competition", "category", "league"]
    card["context_used"] = card.apply(
        lambda r: " | ".join([str(r.get(c, "") or "") for c in context_priority_cols if str(r.get(c, "") or "").strip()]),
        axis=1,
    )
    
    inferred = card.apply(lambda r: infer_tour_and_series(r.get("context_used", ""), row=r), axis=1)
    card["tour"] = inferred.apply(lambda x: x[0])
    card["_series"] = inferred.apply(lambda x: x[1])
    
    card["surface"] = card.get("surface", pd.Series(index=card.index, dtype=object)).astype(str).str.strip().str.title()
    card.loc[card["surface"].isin(["", "Nan", "None"]), "surface"] = ""
    card["_surface"] = card.apply(lambda r: infer_surface(r.get("context_used", ""), r.get("surface", "")), axis=1)

    card = card[card["tour"].isin(["ATP", "WTA", "CHALLENGER", "ITF-M", "ITF-W", "UTR"])]
    if card.empty:
        return card

    card = collapse_live_card(card)
    if card.empty:
        return card
    card = enrich_live_card_with_api_odds(card, today_str)

    grouped_rows = []
    for _, first in card.iterrows():
        winners = []
        probs = []
        pick = str(first.get("predicted_winner", "") or "").strip()
        if pick in ("1", "player_a"):
            winners.append("player_a")
            probs.append(pd.to_numeric(first.get("prob_home"), errors="coerce"))
        elif pick in ("2", "player_b"):
            winners.append("player_b")
            probs.append(pd.to_numeric(first.get("prob_away"), errors="coerce"))
        selected = winners[0] if winners else ""
        prob = max([p for p in probs if pd.notna(p)], default=None)
        tournament = first.get("tournament", "") or first.get("tournament_slug", "") or first.get("context_used", "")
        
        odds_h = first.get("odds_home")
        odds_a = first.get("odds_away")
        odds_source = first.get("odds_source", "")
        odds_bookmaker = first.get("odds_bookmaker", "")
        
        grouped_rows.append({
            "match_date": first.get("match_date"),
            "match_time": first.get("match_time", ""),
            "tour": first.get("tour"),
            "tournament": tournament,
            "round": "",
            "player_a": first.get("player_home"),
            "player_b": first.get("player_away"),
            "winner": "",
            "score": "",
            "odds_a": odds_h if pd.notna(odds_h) else pd.NA,
            "odds_b": odds_a if pd.notna(odds_a) else pd.NA,
            "bookmaker": odds_bookmaker if pd.notna(odds_h) else "",
            "source": first.get("source", ""),
            "captured_at": pd.Timestamp.now().isoformat(),
            "oddsportal_url": "",
            "_surface": infer_surface(tournament, first.get("surface", "")),
            "_court": "",
            "_series": first.get("_series", ""),
            "_comment": "live_upcoming_injected",
            "_location": first.get("country", "") if "country" in first.index else "",
            "_winner_rank": pd.NA,
            "_loser_rank": pd.NA,
            "_odds_source": "TheOddsAPI" if pd.notna(odds_h) else "",
            "api_odds_a": first.get("api_odds_home", pd.NA),
            "api_odds_b": first.get("api_odds_away", pd.NA),
            "debug_scraped_odds_a": first.get("scraped_odds_home", pd.NA),
            "debug_scraped_odds_b": first.get("scraped_odds_away", pd.NA),
            "live_predicted_winner": selected,
            "live_prediction_prob": prob,
            "live_predicted_source": "live_card",
            # Explicit boolean flag so downstream code can filter unambiguously,
            # independently of the free-form `_comment` text.
            "_is_live": True,
        })
    if not grouped_rows:
        return pd.DataFrame()
    df = pd.DataFrame(grouped_rows)
    if "_is_live" not in df.columns:
        df["_is_live"] = True
    return df



_WTA_DRAWS_CACHE: dict[tuple[str, str], list[dict]] = {}


def _clean_wta_team_part(text: object) -> str:
    part = str(text or "").replace("/", " ").strip()
    part = re.sub(r"\([^)]*\)", "", part).strip()
    return " ".join(part.split())


def _wta_team_from_row(tr) -> str:
    parts = []
    for a in tr.select("a"):
        part = _clean_wta_team_part(a.get_text(" ", strip=True))
        if part:
            parts.append(part)

    # Preserve order but remove duplicates caused by responsive markup.
    unique = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return " / ".join(unique)


def _wta_numeric_scores_from_row(tr) -> list[int]:
    scores = []
    for td in tr.select("td.match-table__score-cell"):
        clone = BeautifulSoup(str(td), "html.parser")

        # WTA embeds tie-break points as nested superscript elements, e.g.
        # 7<sup>7</sup>. Remove them so the game score remains 7, not 77.
        for tb in clone.select(".match-table__tie-break"):
            tb.extract()

        txt = clone.get_text("", strip=True)
        if not txt or txt in {".", "-"}:
            continue

        m = re.search(r"\d+", txt)
        if m:
            try:
                scores.append(int(m.group(0)))
            except ValueError:
                pass

    return scores


def _parse_wta_draw_results(html: str, *, tour: str, tournament: str, source_url: str) -> list[dict]:
    """Parse completed WTA draw results from an official WTA draw page."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for match in soup.select("div.tennis-match[data-status='F']"):
        trs = match.select("tr.match-table__row")
        if len(trs) < 2:
            continue

        team_a = _wta_team_from_row(trs[0])
        team_b = _wta_team_from_row(trs[1])
        if not team_a or not team_b:
            continue

        score_a = _wta_numeric_scores_from_row(trs[0])
        score_b = _wta_numeric_scores_from_row(trs[1])
        score = " ".join(f"{a}-{b}" for a, b in zip(score_a, score_b) if a != b)

        a_winner = "is-winner" in (trs[0].get("class") or [])
        b_winner = "is-winner" in (trs[1].get("class") or [])
        if a_winner == b_winner:
            continue

        winner = team_a if a_winner else team_b

        rows.append({
            "tour": tour,
            "tournament": tournament,
            "player_a": team_a,
            "player_b": team_b,
            "winner": winner,
            "score": score,
            "source_url": source_url,
        })

    return rows


def _fetch_wta_draw_results(tournament_slug: str, year: str) -> list[dict]:
    """Fetch WTA official draw results for supported tournaments.

    This is intentionally narrow and lives in the existing warehouse
    source-result path. It settles verified WTA official results without
    manual overlays or new scripts. Extend WTA_TOURNAMENTS only after a
    stable official page is verified.
    """
    WTA_TOURNAMENTS = {
        "eastbourne": {"id": "710", "title": "Eastbourne"},
    }

    slug = str(tournament_slug or "").lower()
    info = WTA_TOURNAMENTS.get(slug)
    if not info:
        return []

    key = (slug, str(year))
    if key in _WTA_DRAWS_CACHE:
        return _WTA_DRAWS_CACHE[key]

    url = f"https://www.wtatennis.com/tournaments/{info['id']}/{slug}/{year}/draws"

    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "RacketFactory/1.0"})
        if resp.status_code != 200:
            logger.warning("WTA official draws returned HTTP %s for %s", resp.status_code, url)
            _WTA_DRAWS_CACHE[key] = []
            return []

        rows = _parse_wta_draw_results(
            resp.text,
            tour="WTA",
            tournament=str(info["title"]),
            source_url=url,
        )
        logger.info("Parsed %d WTA official result rows from %s", len(rows), url)
        _WTA_DRAWS_CACHE[key] = rows
        return rows

    except Exception as exc:
        logger.warning("WTA official draw fetch failed for %s: %s", url, exc)
        _WTA_DRAWS_CACHE[key] = []
        return []


def build_wta_official_result_rows(data_path: Path) -> pd.DataFrame:
    """Build settled rows from WTA official draws for archived source picks.

    Uses existing archived source rows, currently BetClan, as the discovery
    surface. This keeps settlement source-driven and avoids manual overlays.
    """
    archive = data_path / "archive_betclan.csv"
    if not archive.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(archive, low_memory=False)
    except Exception as exc:
        logger.warning("Could not read %s for WTA result discovery: %s", archive, exc)
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    out = []

    for _, row in df.iterrows():
        context = " | ".join(
            str(row.get(c, "") or "")
            for c in ["tournament", "event_text", "category", "source_url"]
        ).lower()

        # Verified official result source currently supports Eastbourne.
        if "wta" not in context or "eastbourne" not in context:
            continue

        match_date = str(row.get("match_date") or "")[:10]
        if not match_date:
            continue

        year = match_date[:4]
        official_rows = _fetch_wta_draw_results("eastbourne", year)

        home = str(row.get("player_home") or "").strip()
        away = str(row.get("player_away") or "").strip()
        if not home or not away:
            continue

        for official in official_rows:
            normal = names_match(home, official["player_a"]) and names_match(away, official["player_b"])
            reverse = names_match(home, official["player_b"]) and names_match(away, official["player_a"])
            if not (normal or reverse):
                continue

            player_a, player_b = home, away

            if normal:
                winner = player_a if names_match(official["winner"], official["player_a"]) else player_b
                score = official.get("score", "")
            else:
                winner = player_a if names_match(official["winner"], official["player_b"]) else player_b

                # Reverse score perspective if official row is opposite archive order.
                score_parts = []
                for token in str(official.get("score", "")).split():
                    if "-" in token:
                        a, b = token.split("-", 1)
                        score_parts.append(f"{b}-{a}")
                score = " ".join(score_parts)

            out.append({
                "match_date": match_date,
                "tour": "WTA",
                "tournament": official.get("tournament") or row.get("tournament") or "Eastbourne",
                "round": "",
                "player_a": player_a,
                "player_b": player_b,
                "winner": winner,
                "score": score,
                "odds_a": pd.NA,
                "odds_b": pd.NA,
                "bookmaker": "",
                "source": "WTA_official_results",
                "captured_at": pd.Timestamp.now().isoformat(),
                "oddsportal_url": official.get("source_url", ""),
                "_surface": "Grass",
                "_court": "",
                "_series": "WTA500",
                "_comment": "result_from_wta_official_draw",
                "_location": "Eastbourne",
                "_winner_rank": pd.NA,
                "_loser_rank": pd.NA,
                "_odds_source": "",
                "_is_live": False,
                "_score_perspective": "player_a_games-player_b_games",
            })
            break

    if not out:
        return pd.DataFrame()

    result_df = pd.DataFrame(out).drop_duplicates(
        subset=["match_date", "tour", "tournament", "player_a", "player_b"],
        keep="last",
    )
    logger.info("Built %d WTA official result rows from archived source rows", len(result_df))
    return result_df

def build_warehouse(
    data_dir: str = "localdata",
    output_file: str = "warehouse.csv.gz",
    db_path: str | Path | None = None,
    inject_live: bool = True,
) -> Optional[Path] | dict[str, int]:
    """
    Merge all tennis data sources into a unified warehouse, 
    including external prediction sources.
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error("Data directory not found: %s", data_path)
        return None

    # 1. Load Match Data
    all_files = list(data_path.glob("*.csv.gz"))
    dfs = []
    for f in all_files:
        if "tennis" in f.name and "predictions" not in f.name:
            try:
                logger.info("Loading match data from %s...", f.name)
                temp_df = pd.read_csv(f, low_memory=False)
                if not temp_df.empty:
                    dfs.append(temp_df)
            except Exception as e:
                logger.error("Failed to load %s: %s", f.name, e)
    
    if not dfs:
        logger.error("No valid match data files found.")
        return None
    
    warehouse = pd.concat(dfs, ignore_index=True)

    wta_official_rows = build_wta_official_result_rows(data_path)
    if not wta_official_rows.empty:
        logger.info("Injecting %d WTA official result rows into warehouse before dedupe...", len(wta_official_rows))
        warehouse = pd.concat([warehouse, wta_official_rows], ignore_index=True, sort=False)

    # Mark every historical row as not live-injected. Downstream consumers
    # (mine_edges.py) use this column to keep live rows out of historical
    # slice mining — they have empty `winner` so they would otherwise show as
    # guaranteed losses and bias the win-rate assays downward.
    if "_is_live" not in warehouse.columns:
        warehouse["_is_live"] = False
    else:
        warehouse["_is_live"] = warehouse["_is_live"].fillna(False).astype(bool)

    # Inject live upcoming rows so same-day candidates exist in the warehouse.
    # Legacy unit tests pass db_path and expect a pure offline build; treat that
    # path as compatibility mode unless inject_live is explicitly requested.
    if db_path is not None and inject_live is True:
        inject_live = False
    if inject_live:
        live_rows = build_live_rows()
        if not live_rows.empty:
            logger.info("Injecting %d live upcoming rows into warehouse before dedupe...", len(live_rows))
            if "_is_live" not in live_rows.columns:
                live_rows["_is_live"] = True
            warehouse = pd.concat([warehouse, live_rows], ignore_index=True, sort=False)
    
    # Standard Deduplication
    critical_cols = ["match_date", "tour", "tournament", "player_a", "player_b"]
    for col in critical_cols:
        if col not in warehouse.columns:
            warehouse[col] = ""

    warehouse['p_a_key'] = warehouse['player_a'].apply(player_key)
    warehouse['p_b_key'] = warehouse['player_b'].apply(player_key)
    warehouse['_sorted_players'] = warehouse.apply(
        lambda r: tuple(sorted([r['p_a_key'], r['p_b_key']])), axis=1
    )
    
    warehouse = warehouse.drop_duplicates(
        subset=["match_date", "tour", "tournament", "_sorted_players"], 
        keep="last"
    ).drop(columns=['p_a_key', 'p_b_key', '_sorted_players'])
    
    # 2. Multi-Source Prediction Join
    PRIMARY_SOURCES = {"Forebet"}

    # Create canonical merge keys in warehouse based on match_date and player signatures
    warehouse['_key_a'] = warehouse['player_a'].astype(str).map(name_signature)
    warehouse['_key_b'] = warehouse['player_b'].astype(str).map(name_signature)
    warehouse['_merge_key'] = warehouse.apply(
        lambda r: f"{r['match_date']}|" + "|".join(sorted([r['_key_a'], r['_key_b']])), axis=1
    )
    warehouse = warehouse.drop(columns=['_key_a', '_key_b'])

    pred_files = list(data_path.glob("predictions_*.csv.gz"))
    if pred_files:
        logger.info("Loading %d prediction files for multi-source merge...", len(pred_files))

        source_dfs: dict[str, list[pd.DataFrame]] = {}
        for pf in pred_files:
            try:
                pdf = pd.read_csv(pf, low_memory=False)
                if pdf.empty or "source" not in pdf.columns:
                    continue
                src = pdf["source"].iloc[0]
                source_dfs.setdefault(src, []).append(pdf)
            except Exception as e:
                logger.warning("Failed to load prediction file %s: %s", pf, e)

        for src, frames in source_dfs.items():
            merged = pd.concat(frames, ignore_index=True)
            if "match_date" not in merged.columns or "player_a" not in merged.columns or "player_b" not in merged.columns:
                continue
            merged['_key_a'] = merged['player_a'].astype(str).map(name_signature)
            merged['_key_b'] = merged['player_b'].astype(str).map(name_signature)
            merged['_merge_key'] = merged.apply(
                lambda r: f"{r['match_date']}|" + "|".join(sorted([r['_key_a'], r['_key_b']])), axis=1
            )
            merged = merged.drop_duplicates(subset=['_merge_key'], keep="last")
            
            pred_cols = [c for c in ["predicted_winner", "prediction_prob", "odds_a", "odds_b", "source"]
                         if c in merged.columns]

            if src in PRIMARY_SOURCES:
                logger.info("Merging primary source '%s': %d predictions", src, len(merged))
                warehouse = warehouse.merge(
                    merged[['_merge_key'] + pred_cols],
                    on='_merge_key',
                    how="left",
                    suffixes=('', '_pred'),
                )
                if 'source_pred' in warehouse.columns:
                    warehouse = warehouse.rename(columns={'source_pred': 'predicted_source'})
            else:
                suffix = src.lower().replace(" ", "_").replace("-", "_")
                logger.info("Merging secondary source '%s' (suffix: _%s): %d predictions",
                            src, suffix, len(merged))
                rename_map = {c: f"{c}_{suffix}" for c in pred_cols if c != "source"}
                merged_renamed = merged[['_merge_key'] + pred_cols].rename(columns=rename_map)
                if "source" in merged_renamed.columns:
                    merged_renamed = merged_renamed.drop(columns=["source"])
                warehouse = warehouse.merge(
                    merged_renamed,
                    on='_merge_key',
                    how="left",
                )

        duplicate_groups = {
            "predicted_winner": ["predicted_winner", "predicted_winner_x", "predicted_winner_y", "predicted_winner_pred"],
            "prediction_prob": ["prediction_prob", "prediction_prob_x", "prediction_prob_y", "prediction_prob_pred"],
            "predicted_source": ["predicted_source", "predicted_source_x", "predicted_source_y", "source_pred"],
            "predicted_winner_foretennis": ["predicted_winner_foretennis", "predicted_winner_foretennis_x", "predicted_winner_foretennis_y"],
            "prediction_prob_foretennis": ["prediction_prob_foretennis", "prediction_prob_foretennis_x", "prediction_prob_foretennis_y"],
            "predicted_winner_market": ["predicted_winner_market", "predicted_winner_market_x", "predicted_winner_market_y"],
            "prediction_prob_market": ["prediction_prob_market", "prediction_prob_market_x", "prediction_prob_market_y"],
        }
        for base, variants in duplicate_groups.items():
            present = [c for c in variants if c in warehouse.columns]
            if not present:
                continue
            merged_col = warehouse[present[0]].copy()
            for c in present[1:]:
                merged_col = merged_col.combine_first(warehouse[c])
            warehouse[base] = merged_col
            drop_cols = [c for c in present if c != base]
            if drop_cols:
                warehouse = warehouse.drop(columns=drop_cols)

        if "odds_a" in warehouse.columns:
            for col in [c for c in warehouse.columns if c.startswith("odds_a_")]:
                warehouse["odds_a"] = warehouse["odds_a"].combine_first(warehouse[col])
                warehouse = warehouse.drop(columns=[col])
        if "odds_b" in warehouse.columns:
            for col in [c for c in warehouse.columns if c.startswith("odds_b_")]:
                warehouse["odds_b"] = warehouse["odds_b"].combine_first(warehouse[col])
                warehouse = warehouse.drop(columns=[col])

        if "live_predicted_winner" in warehouse.columns:
            if "predicted_winner" not in warehouse.columns:
                warehouse["predicted_winner"] = pd.NA
            warehouse["predicted_winner"] = warehouse["predicted_winner"].combine_first(warehouse["live_predicted_winner"])
        if "live_prediction_prob" in warehouse.columns:
            if "prediction_prob" not in warehouse.columns:
                warehouse["prediction_prob"] = pd.NA
            warehouse["prediction_prob"] = warehouse["prediction_prob"].combine_first(warehouse["live_prediction_prob"])
        if "live_predicted_source" in warehouse.columns:
            if "predicted_source" not in warehouse.columns:
                warehouse["predicted_source"] = pd.NA
            warehouse["predicted_source"] = warehouse["predicted_source"].combine_first(warehouse["live_predicted_source"])

        drop_live_cols = [c for c in ["live_predicted_winner", "live_prediction_prob", "live_predicted_source", "_merge_key"] if c in warehouse.columns]
        if drop_live_cols:
            warehouse = warehouse.drop(columns=drop_live_cols)

        pred_winner_cols = [c for c in warehouse.columns if c.startswith("predicted_winner")]
        logger.info("Prediction columns in warehouse: %s", pred_winner_cols)
        primary_cov = warehouse["predicted_winner"].notna().sum() if "predicted_winner" in warehouse.columns else 0
        logger.info("Primary (Forebet) prediction coverage: %d/%d rows (%.1f%%)",
                    primary_cov, len(warehouse), 100 * primary_cov / max(len(warehouse), 1))

    if "odds_a" in warehouse.columns:
        warehouse["odds_a"] = pd.to_numeric(warehouse["odds_a"], errors='coerce')
    if "odds_b" in warehouse.columns:
        warehouse["odds_b"] = pd.to_numeric(warehouse["odds_b"], errors='coerce')

    dest_path = data_path / output_file
    warehouse.to_csv(dest_path, index=False, compression="gzip")
    
    logger.info("Warehouse build successful. Total rows: %d", len(warehouse))

    if db_path is not None:
        winner_col = warehouse.get("winner", pd.Series(dtype=object))
        settled_matches = int(
            (winner_col.notna() & ~winner_col.astype(str).str.strip().isin(["", "nan", "<NA>", "None"])).sum()
        ) if len(winner_col) else 0
        market_sides = 0
        if "odds_a" in warehouse.columns:
            market_sides += int(warehouse["odds_a"].notna().sum())
        if "odds_b" in warehouse.columns:
            market_sides += int(warehouse["odds_b"].notna().sum())
        return {
            "oddsportal_rows": int(len(warehouse)),
            "settled_matches": settled_matches,
            "market_sides": market_sides,
        }

    return dest_path