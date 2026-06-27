"""
Racket Factory Warehouse
Handles the merging and deduplication of various tennis data sources.
"""
import pandas as pd
from pathlib import Path
import logging
from typing import Optional
from datetime import date, timedelta
from racketfactory.entities import player_key
from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor

logger = logging.getLogger(__name__)


def infer_tour_and_series(text: str) -> tuple[str, str]:
    lower = str(text or "").lower()
    if any(x in lower for x in ["wimbledon", "roland garros", "us open", "australian open"]):
        if any(x in lower for x in ["women", "wta", "girls"]):
            return ("WTA", "Grand Slam")
        if any(x in lower for x in ["men", "atp", "boys"]):
            return ("ATP", "Grand Slam")
        return ("UNKNOWN", "Grand Slam")
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
    if any(x in lower for x in ["atp challenger", "challenger"]):
        return ("CHALLENGER", "Challenger")
    if "wta" in lower:
        return ("WTA", "WTA")
    if "atp" in lower:
        return ("ATP", "ATP")
    return ("UNKNOWN", "UNKNOWN")


def build_live_rows() -> pd.DataFrame:
    rows = []
    for source_name, predictor in [("PredixSport", PredixSportPredictor()), ("BetClan", BetClanPredictor())]:
        try:
            preds = predictor.fetch_daily()
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

    card["match_type"] = card.apply(
        lambda r: "Doubles" if "/" in str(r.get("player_home", "")) or "/" in str(r.get("player_away", "")) else "Singles",
        axis=1,
    )
    card = card[card["match_type"] == "Singles"].copy()
    if card.empty:
        return card

    context_cols = [c for c in ["tournament", "event_level", "event_text"] if c in card.columns]
    card["context_used"] = card.apply(
        lambda r: " | ".join([str(r.get(c, "") or "") for c in context_cols if str(r.get(c, "") or "").strip()]),
        axis=1,
    )
    inferred = card["context_used"].apply(infer_tour_and_series)
    card["tour"] = inferred.apply(lambda x: x[0])
    card["_series"] = inferred.apply(lambda x: x[1])
    card["_surface"] = card.get("surface", pd.Series(index=card.index, dtype=object)).astype(str).str.strip().str.title()
    card.loc[card["_surface"].isin(["", "Nan", "None"]), "_surface"] = ""
    card = card[card["tour"].isin(["ATP", "WTA", "CHALLENGER"])]
    if card.empty:
        return card

    card["p_a_key"] = card["player_home"].apply(player_key)
    card["p_b_key"] = card["player_away"].apply(player_key)
    card["_sorted_players"] = card.apply(lambda r: tuple(sorted([r["p_a_key"], r["p_b_key"]])), axis=1)

    grouped_rows = []
    for (_, match_date, sorted_players), g in card.groupby(["tour", "match_date", "_sorted_players"], dropna=False):
        first = g.iloc[0]
        tournament = first.get("tournament", "")
        winners = []
        probs = []
        for _, rr in g.iterrows():
            pick = str(rr.get("predicted_winner", "") or "")
            if pick == "1":
                winners.append("player_a")
                probs.append(pd.to_numeric(rr.get("prob_home"), errors="coerce"))
            elif pick == "2":
                winners.append("player_b")
                probs.append(pd.to_numeric(rr.get("prob_away"), errors="coerce"))
        selected = winners[0] if winners else ""
        prob = max([p for p in probs if pd.notna(p)], default=None)
        grouped_rows.append({
            "match_date": first.get("match_date"),
            "tour": first.get("tour"),
            "tournament": tournament,
            "round": "",
            "player_a": first.get("player_home"),
            "player_b": first.get("player_away"),
            "winner": "",
            "score": "",
            "odds_a": pd.NA,
            "odds_b": pd.NA,
            "bookmaker": "",
            "source": ", ".join(sorted(set(map(str, g["source"])))),
            "captured_at": pd.Timestamp.now().isoformat(),
            "oddsportal_url": "",
            "_surface": first.get("_surface", ""),
            "_court": "",
            "_series": first.get("_series", ""),
            "_comment": "live_upcoming_injected",
            "_location": first.get("country", "") if "country" in first.index else "",
            "_winner_rank": pd.NA,
            "_loser_rank": pd.NA,
            "_odds_source": "",
            "live_predicted_winner": selected,
            "live_prediction_prob": prob,
            "live_predicted_source": "live_card",
        })
    return pd.DataFrame(grouped_rows)


def build_warehouse(data_dir: str = "localdata", output_file: str = "warehouse.csv.gz") -> Optional[Path]:
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

    # Inject live upcoming rows so same-day candidates exist in the warehouse.
    live_rows = build_live_rows()
    if not live_rows.empty:
        logger.info("Injecting %d live upcoming rows into warehouse before dedupe...", len(live_rows))
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
    # Primary source writes to canonical columns (predicted_winner, prediction_prob,
    # predicted_source). Each additional source gets suffixed columns so sources
    # never silently overwrite each other based on glob order.
    #
    # Source priority: Forebet is primary (most historical coverage). Any source
    # not in PRIMARY_SOURCES is merged as a secondary with a suffix derived from
    # its 'source' column value (lowercased, spaces→underscore).
    PRIMARY_SOURCES = {"Forebet"}
    JOIN_KEYS = ["match_date", "tour", "tournament", "player_a", "player_b"]

    pred_files = list(data_path.glob("predictions_*.csv.gz"))
    if pred_files:
        logger.info("Loading %d prediction files for multi-source merge...", len(pred_files))

        # Load and bucket by source name
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
            # Deduplicate within this source: keep most recent scrape
            merged = merged.drop_duplicates(
                subset=JOIN_KEYS, keep="last"
            )
            pred_cols = [c for c in ["predicted_winner", "prediction_prob", "source"]
                         if c in merged.columns]

            if src in PRIMARY_SOURCES:
                # Primary source → canonical column names
                logger.info("Merging primary source '%s': %d predictions", src, len(merged))
                warehouse = warehouse.merge(
                    merged[JOIN_KEYS + pred_cols],
                    on=JOIN_KEYS,
                    how="left",
                    suffixes=('', '_pred'),
                )
                if 'source_pred' in warehouse.columns:
                    warehouse = warehouse.rename(columns={'source_pred': 'predicted_source'})
            else:
                # Secondary source → suffixed column names
                suffix = src.lower().replace(" ", "_").replace("-", "_")
                logger.info("Merging secondary source '%s' (suffix: _%s): %d predictions",
                            src, suffix, len(merged))
                rename_map = {c: f"{c}_{suffix}" for c in pred_cols if c != "source"}
                merged_renamed = merged[JOIN_KEYS + pred_cols].rename(columns=rename_map)
                # Drop 'source' col — it's redundant given the suffix encodes it
                if "source" in merged_renamed.columns:
                    merged_renamed = merged_renamed.drop(columns=["source"])
                warehouse = warehouse.merge(
                    merged_renamed,
                    on=JOIN_KEYS,
                    how="left",
                )

        # Clean up true duplicate artifacts only; preserve distinct source lanes.
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

        # Backfill canonical prediction columns from injected live rows only where still missing.
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

        drop_live_cols = [c for c in ["live_predicted_winner", "live_prediction_prob", "live_predicted_source"] if c in warehouse.columns]
        if drop_live_cols:
            warehouse = warehouse.drop(columns=drop_live_cols)

        # Report final prediction column coverage
        pred_winner_cols = [c for c in warehouse.columns if c.startswith("predicted_winner")]
        logger.info("Prediction columns in warehouse: %s", pred_winner_cols)
        primary_cov = warehouse["predicted_winner"].notna().sum() if "predicted_winner" in warehouse.columns else 0
        logger.info("Primary (Forebet) prediction coverage: %d/%d rows (%.1f%%)",
                    primary_cov, len(warehouse), 100 * primary_cov / max(len(warehouse), 1))

    # Final safety: ensure numeric types for odds
    if "odds_a" in warehouse.columns:
        warehouse["odds_a"] = pd.to_numeric(warehouse["odds_a"], errors='coerce')
    if "odds_b" in warehouse.columns:
        warehouse["odds_b"] = pd.to_numeric(warehouse["odds_b"], errors='coerce')

    dest_path = data_path / output_file
    warehouse.to_csv(dest_path, index=False, compression="gzip")
    
    logger.info("Warehouse build successful. Total rows: %d", len(warehouse))
    return dest_path
