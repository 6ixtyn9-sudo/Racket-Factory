"""
[Tennis-Data.co.uk](http://Tennis-Data.co.uk) adapter
Downloads yearly Excel files and normalizes them into the Racket Factory contract.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import pandas as pd
import urllib.request
from racketfactory.entities import normalize_player, normalize_tour, player_key

logger = logging.getLogger(__name__)

# ---- [tennis-data.co.uk](http://tennis-data.co.uk) column definitions -----------------------------------
TD_COLUMNS_ATP = [
    "ATP", "Location", "Tournament", "Date", "Series", "Court", "Surface",
    "Round", "Best of", "Winner", "Loser", "WRank", "LRank", "WPts", "LPts",
    "W1", "L1", "W2", "L2", "W3", "L3", "W4", "L4", "W5", "L5",
    "Wsets", "Lsets", "Comment",
    "B365W", "B365L", "PSW", "PSL", "MaxW", "MaxL", "AvgW", "AvgL",
]
TD_COLUMNS_WTA = [
    "WTA", "Location", "Tournament", "Date", "Tier", "Court", "Surface",
    "Round", "Best of", "Winner", "Loser", "WRank", "LRank", "WPts", "LPts",
    "W1", "L1", "W2", "L2", "W3", "L3", "Wsets", "Lsets", "Comment",
    "B365W", "B365L", "PSW", "PSL", "MaxW", "MaxL", "AvgW", "AvgL",
]

TENNIS_DATA_BASE = "http://tennis-data.co.uk"
ATP_YEARLY_URL = TENNIS_DATA_BASE + "/{year}/{year}.xlsx"
WTA_YEARLY_URL = TENNIS_DATA_BASE + "/{year}w/{year}.xlsx"

SURFACE_MAP: dict[str, str] = {
    "hard": "Hard", "clay": "Clay", "grass": "Grass", "carpet": "Carpet",
}

def normalize_surface(value: object) -> str:
    if value is None: return ""
    s = str(value).strip().lower()
    return SURFACE_MAP.get(s, str(value).strip())

def parse_comment(comment: object) -> str:
    if comment is None: return "Completed"
    c = str(comment).strip().lower()
    if any(x in c for x in ["retired", "ret.", "retirement"]): return "Retired"
    if any(x in c for x in ["walkover", "walk", "w/o"]): return "Walkover"
    if any(x in c for x in ["abandoned", "cancelled"]): return "Abandoned"
    return "Completed"

def format_score(row: dict, *, flip: bool = False) -> str:
    """Set tokens in player_a-first order (settlement contract).

    The source columns are winner-first (W1/L1...); when player_a is the
    loser (underdog won), every token flips to ``loser-games_winner-games``
    so downstream set counting attributes games to the right side.
    """
    parts = []
    for i in range(1, 6):
        w, l = row.get(f"W{i}"), row.get(f"L{i}")
        if pd.notna(w) and pd.notna(l): # type: ignore
            if int(w) == 0 and int(l) == 0:
                continue  # unplayed set recorded as 0-0: not a set
            a, b = (int(l), int(w)) if flip else (int(w), int(l))
            parts.append(f"{a}-{b}")
    return " ".join(parts)


def flip_score_perspective(score: object) -> tuple[str, int]:
    """Flip plain ``N-M`` tokens of an existing score string.

    Returns (flipped_score, skipped_tokens). Only plain ``digits-digits``
    tokens flip; anything else (tiebreak annotations, retirements) is left
    untouched and counted so callers can report it.
    """
    import re
    flipped: list[str] = []
    skipped = 0
    for tok in str(score or "").split():
        m = re.fullmatch(r"(\d+)-(\d+)", tok)
        # Fused tiebreaks ("7-68") match digits-digits but must never flip;
        # no real set has a side above 30 games.
        if m and int(m.group(1)) <= 30 and int(m.group(2)) <= 30:
            flipped.append(f"{m.group(2)}-{m.group(1)}")
        else:
            flipped.append(tok)
            skipped += 1
    return " ".join(flipped), skipped

def pick_odds(ps: object, b365: object, avg: object) -> Optional[float]:
    for val in [ps, b365, avg]:
        try:
            if pd.notna(val) and float(val) > 1.0: # type: ignore
                return round(float(val), 6)
        except (ValueError, TypeError):
            continue
    return None

def normalize_row(row: dict, *, tour: str) -> Optional[dict[str, Any]]:
    winner_raw = row.get("Winner")
    loser_raw = row.get("Loser")
    if pd.isna(winner_raw) or pd.isna(loser_raw): # type: ignore
        return None

    winner = normalize_player(winner_raw)
    loser = normalize_player(loser_raw)
    if not winner or not loser:
        return None

    odds_winner = pick_odds(row.get("PSW"), row.get("B365W"), row.get("AvgW"))
    odds_loser = pick_odds(row.get("PSL"), row.get("B365L"), row.get("AvgL"))
    has_odds = odds_winner is not None and odds_loser is not None

    date_val = row.get("Date")
    if pd.isna(date_val): return None # type: ignore
    try:
        match_date = pd.Timestamp(date_val).strftime("%Y-%m-%d")
    except Exception: return None

    captured_at = datetime.now(timezone.utc).isoformat()

    # IMPORTANT: Assign player_a/player_b by odds rank (favourite = player_a),
    # NOT by winner/loser identity. This prevents look-ahead bias: the assay
    # must not know who won when deciding which player is 'a' or 'b'.
    # The `winner` field remains the ground truth for outcome checking.
    if has_odds:
        assert odds_winner is not None and odds_loser is not None
        if odds_winner <= odds_loser:
            # Winner is the favourite
            p_a, p_b = winner, loser
            odds_a, odds_b = odds_winner, odds_loser
        else:
            # Loser is the favourite (upset scenario)
            p_a, p_b = loser, winner
            odds_a, odds_b = odds_loser, odds_winner
    else:
        # No market price: keep the result (it still settles on winner)
        # ordered alphabetically by canonical key — also leak-free.
        p_a, p_b = sorted([winner, loser], key=lambda n: player_key(n))
        odds_a, odds_b = None, None
    score = format_score(row, flip=(p_a == loser))

    return {
        "match_date": match_date,
        "tour": normalize_tour(tour),
        "tournament": str(row.get("Tournament") or "").strip(),
        "round": str(row.get("Round") or "").strip(),
        "player_a": p_a,
        "player_b": p_b,
        "winner": winner,
        "score": score,
        "_score_perspective": "player_a_games-player_b_games",
        "odds_a": odds_a,
        "odds_b": odds_b,
        "bookmaker": ("Pinnacle" if pd.notna(row.get("PSW")) else ( # type: ignore
            "Bet365" if pd.notna(row.get("B365W")) else "OddsPortal Avg" # type: ignore
        )) if has_odds else "",
        "source": "tennis-data.co.uk",
        "captured_at": captured_at,
        "oddsportal_url": "",
        "_surface": normalize_surface(row.get("Surface")),
        "_court": str(row.get("Court") or "").strip(),
        "_series": str(row.get("Series") or row.get("Tier") or "").strip(),
        "_comment": parse_comment(row.get("Comment")),
        "_location": str(row.get("Location") or "").strip(),
        "_winner_rank": int(row.get("WRank")) if pd.notna(row.get("WRank")) else None, # type: ignore
        "_loser_rank": int(row.get("LRank")) if pd.notna(row.get("LRank")) else None, # type: ignore
        "_odds_source": ("pinnacle" if pd.notna(row.get("PSW")) else ( # type: ignore
            "bet365" if pd.notna(row.get("B365W")) else "avg" # type: ignore
        )) if has_odds else "",
    }

def read_yearly_excel(path: str | Path, *, tour: str = "ATP") -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        logger.warning("File not found: %s", path)
        return []
    try:
        # explicitly using openpyxl engine
        df = pd.read_excel(path, engine='openpyxl')
    except Exception as e:
        logger.error("Failed to read excel %s: %s", path, e)
        return []
        
    logger.info("Read %d rows from %s", len(df), path)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        normalized = normalize_row(row.to_dict(), tour=tour)
        if normalized:
            rows.append(normalized)
    return rows

def _cache_is_fresh(path: Path, year: int, max_age_hours: float) -> bool:
    """Past-year files are final (cache forever); the current year's file
    grows through the season and must refetch once stale."""
    if not path.exists():
        return False
    if year != datetime.now().year:
        return True
    try:
        age_hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600.0
    except OSError:
        return False
    return age_hours <= max_age_hours


def download_yearly_excel(
    year: int,
    tour: str = "ATP",
    data_dir: str | Path = "localdata/tennisdata",
    *,
    force: bool = False,
    max_age_hours: float = 24.0,
) -> Optional[Path]:
    tour_upper = tour.upper()
    url = ATP_YEARLY_URL.format(year=year) if tour_upper == "ATP" else WTA_YEARLY_URL.format(year=year)
    
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{year}{'' if tour_upper == 'ATP' else 'w'}.xlsx"
    dest = data_dir / filename
    
    if not force and _cache_is_fresh(dest, year, max_age_hours):
        logger.info("Using cached %s (fresh)", dest)
        return dest
    if dest.exists():
        logger.info("Refetching stale %s", dest)
        try:
            dest.unlink()
        except OSError:
            pass

    try:
        logger.info("Downloading %s -> %s", url, dest)
        urllib.request.urlretrieve(url, dest)
        return dest
    except Exception as e:
        logger.error("Failed to download %s: %s", url, e)
        if dest.exists(): dest.unlink()
        return None

def write_monthly_csv(
    rows: list[dict[str, Any]],
    data_dir: str | Path = "localdata",
    *,
    prefix: str = "tennisdata",
) -> list[Path]:
    from racketfactory.oddsportal import COLUMNS as OP_COLUMNS
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    core_cols = list(OP_COLUMNS)
    ext_cols = ["_surface", "_court", "_series", "_comment", "_location",
                "_winner_rank", "_loser_rank", "_odds_source"]
    
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        month_key = str(row["match_date"])[:7]
        by_month.setdefault(month_key, []).append(row)

    written: list[Path] = []
    for month, month_rows in sorted(by_month.items()):
        path = data_dir / f"{prefix}_tennis_{month}.csv.gz"
        all_cols = core_cols + [c for c in ext_cols if any(c in r for r in month_rows)]
        df = pd.DataFrame(month_rows, columns=all_cols)
        
        if path.exists():
            existing = pd.read_csv(path, low_memory=False)
            df = pd.concat([existing, df], ignore_index=True)
            # Use canonical player_key (lowercased, ASCII-folded, alphanumeric only)
            # to deduplicate, not raw display names — prevents accent/case mismatches
            # from creating duplicate entries at the source-file level.
            from racketfactory.entities import player_key as _pk
            df['_pa_key'] = df['player_a'].apply(_pk)
            df['_pb_key'] = df['player_b'].apply(_pk)
            df['_sorted_key'] = df.apply(
                lambda r: tuple(sorted([r['_pa_key'], r['_pb_key']])), axis=1
            )
            df = df.drop_duplicates(
                subset=["match_date", "tour", "tournament", "_sorted_key"],
                keep="last",
            ).drop(columns=['_pa_key', '_pb_key', '_sorted_key'])
        df.to_csv(path, index=False, compression="gzip")
        written.append(path)
    return written

def repair_monthly_scores(path: str | Path) -> dict[str, int]:
    """One-off repair for monthly files written with winner-first scores.

    Rows where player_a != winner get their plain N-M tokens flipped to
    player_a-first; every row gains the ``_score_perspective`` label.
    Rewrites the file in place (gzipped). Returns counts.
    """
    path = Path(path)
    df = pd.read_csv(path, low_memory=False)
    stats = {"rows": len(df), "flipped": 0, "skipped_tokens": 0,
             "stripped_00": 0, "already_labeled": 0}
    if df.empty:
        return stats
    labeled = "_score_perspective" in df.columns
    scores = []
    for _, row in df.iterrows():
        raw = row.get("score")
        score = raw if isinstance(raw, str) else ""
        toks = [x for x in score.split() if x != "0-0"]
        if len(toks) != len(score.split()):
            stats["stripped_00"] += 1
            score = " ".join(toks)
        if labeled and str(row.get("_score_perspective") or "") != "":
            stats["already_labeled"] += 1
            scores.append(score)
            continue
        if str(row.get("player_a") or "") != str(row.get("winner") or "") and score:
            fixed, skipped = flip_score_perspective(score)
            stats["flipped"] += 1
            stats["skipped_tokens"] += skipped
            scores.append(fixed)
        else:
            scores.append(score)
    df["score"] = scores
    df["_score_perspective"] = "player_a_games-player_b_games"
    df.to_csv(path, index=False, compression="gzip")
    return stats


def fetch_and_normalize_years(
    years: list[int],
    tours: list[str] | None = None,
    data_dir: str | Path = "localdata/tennisdata",
    output_dir: str | Path = "localdata",
    *,
    force: bool = False,
    max_age_hours: float = 24.0,
) -> int:
    if tours is None: tours = ["ATP", "WTA"]
    all_rows: list[dict[str, Any]] = []
    for tour in tours:
        for year in years:
            path = download_yearly_excel(year, tour, data_dir,
                                         force=force, max_age_hours=max_age_hours)
            if path:
                rows = read_yearly_excel(path, tour=tour)
                all_rows.extend(rows)
                logger.info("  %s %d: %d rows", tour, year, len(rows))

    if all_rows:
        write_monthly_csv(all_rows, output_dir, prefix="tennisdata")
    return len(all_rows)
