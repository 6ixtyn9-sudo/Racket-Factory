#!/usr/bin/env python3
"""
Fetch today's and tomorrow's predictions for immediate review and align them to
Racket Factory's live-pick dimensions without creating new pipeline surfaces.
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.sources.predixsport import PredixSportPredictor
from racketfactory.sources.betclan import BetClanPredictor
from racketfactory.sources.forebet import ForebetPredictor


def get_rank_band(rank: float) -> str:
    if pd.isna(rank): return "Unknown"
    if rank <= 10: return "Top 10"
    if rank <= 50: return "11-50"
    if rank <= 100: return "51-100"
    return "100+"


def infer_tour_and_series(text: str) -> tuple[str, str]:
    lower = str(text or "").lower()
    if any(x in lower for x in ["wimbledon", "roland garros", "us open", "australian open"]):
        if any(x in lower for x in ["women", "wta", "girls"]):
            return ("WTA", "Grand Slam")
        if any(x in lower for x in ["men", "atp", "boys"]):
            return ("ATP", "Grand Slam")
        return ("UNKNOWN", "Grand Slam")
    if any(x in lower for x in ["piracicaba", "targu mures", "challenger", "atp challenger"]):
        return ("CHALLENGER", "Challenger")
    if "itf women" in lower:
        return ("ITF-W", "ITF")
    if "itf men" in lower or "itf m" in lower:
        return ("ITF-M", "ITF")
    if "wta" in lower:
        return ("WTA", "WTA")
    if "atp" in lower:
        return ("ATP", "ATP")
    if "utr" in lower:
        return ("UTR", "UTR")
    return ("UNKNOWN", "UNKNOWN")


def infer_from_players(player_home: str, player_away: str) -> tuple[str, str]:
    combo = f"{player_home} {player_away}".lower()
    women_markers = {
        "naomi osaka", "karolina muchova", "tatjana maria", "madison keys",
        "vera zvonareva", "ellen perez", "demi schuurs", "gabriela dabrowski",
        "luisa stefani", "sydney jara", "reese frank",
    }
    men_markers = {
        "zizou bergs", "ugo humbert", "ethan quinn", "alejandro davidovich fokina",
        "sumit nagal", "felix balshaw", "matheus pucinelli de almeida", "gonzalo villanueva",
        "mitchell sheldon", "jose garcia",
    }
    if "/" in str(player_home) or "/" in str(player_away):
        if any(name in combo for name in women_markers):
            return ("WTA", "Doubles")
        if any(name in combo for name in men_markers):
            return ("ATP", "Doubles")
        return ("UNKNOWN", "Doubles")
    if any(name in combo for name in women_markers):
        return ("WTA", "Singles")
    if any(name in combo for name in men_markers):
        return ("ATP", "Singles")
    return ("UNKNOWN", "UNKNOWN")


def normalize_person_name(name: str) -> str:
    name = " ".join(str(name or "").replace(".", " ").split()).strip().lower()
    if not name:
        return ""
    parts = name.split()
    if len(parts) >= 2 and len(parts[0]) == 1:
        parts = parts[1:]
    return " ".join(parts)


def surname_tokens(name: str) -> tuple[str, ...]:
    parts = normalize_person_name(name).split()
    if not parts:
        return tuple()
    if len(parts) == 1:
        return (parts[-1],)
    return tuple(parts[-2:])


def person_tokens(name: str) -> tuple[str, ...]:
    normalized = normalize_person_name(name)
    if not normalized:
        return tuple()
    return tuple(normalized.split())


def names_match(name_a: str, name_b: str) -> bool:
    a = person_tokens(name_a)
    b = person_tokens(name_b)
    if not a or not b:
        return False
    if a == b:
        return True
    if surname_tokens(name_a) == surname_tokens(name_b):
        return True
    if len(a) == len(b):
        shared = sum(1 for x, y in zip(a, b) if x == y)
        if shared >= max(1, len(a) - 1):
            return True
    set_a = set(a)
    set_b = set(b)
    overlap = set_a & set_b
    return len(overlap) >= min(len(set_a), len(set_b)) and len(overlap) >= 1


def canonical_display_name(name: str) -> str:
    raw = " ".join(str(name or "").split()).strip()
    if not raw:
        return ""
    if "/" in raw:
        return " / ".join(part.strip() for part in raw.split("/"))
    return raw


def matchup_key(player_home: str, player_away: str) -> str:
    names = sorted([
        " ".join(surname_tokens(player_home)) or normalize_person_name(player_home),
        " ".join(surname_tokens(player_away)) or normalize_person_name(player_away),
    ])
    return "|".join(names)


def rows_refer_to_same_match(a: pd.Series, b: pd.Series) -> bool:
    if str(a.get("match_date", "")) != str(b.get("match_date", "")):
        return False
    return (
        (names_match(a.get("player_home", ""), b.get("player_home", "")) and names_match(a.get("player_away", ""), b.get("player_away", "")))
        or
        (names_match(a.get("player_home", ""), b.get("player_away", "")) and names_match(a.get("player_away", ""), b.get("player_home", "")))
    )


def collapse_combined_card(combined: pd.DataFrame) -> pd.DataFrame:
    rows = []
    used = set()
    ordered = combined.sort_values(by=[c for c in ["match_date", "name_score"] if c in combined.columns], ascending=[True, False]).reset_index(drop=True)
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
        grp = ordered.iloc[group].copy()
        rows.append({
            "match_date": grp["match_date"].iloc[0],
            "pair_key": grp.apply(lambda r: matchup_key(r.get("player_home", ""), r.get("player_away", "")), axis=1).iloc[0],
            "sources": ", ".join(sorted(set(map(str, grp["source"])))),
            "player_home": grp.iloc[0]["player_home"],
            "player_away": grp.iloc[0]["player_away"],
            "match_type": next((x for x in grp["match_type"] if str(x)), grp["match_type"].iloc[0]),
            "tour": next((x for x in grp["tour"] if str(x) not in {"", "UNKNOWN"}), grp["tour"].iloc[0]),
            "_series": next((x for x in grp["_series"] if str(x) not in {"", "UNKNOWN"}), grp["_series"].iloc[0]),
            "context_used": next((x for x in grp["context_used"] if str(x) not in {"", "<empty>"}), grp["context_used"].iloc[0]),
        })
    return pd.DataFrame(rows)


def classify_row(row: pd.Series) -> tuple[str, str, str]:
    context_cols = ["tournament", "tour_slug", "tournament_slug", "event_level", "match_label", "event", "competition", "category", "league"]
    context_parts = [str(row.get(c, "") or "") for c in context_cols if c in row.index]
    context_text = " | ".join([x for x in context_parts if x.strip()])
    tour, series = infer_tour_and_series(context_text)
    if tour == "UNKNOWN" and series == "UNKNOWN":
        tour, player_series = infer_from_players(str(row.get("player_home", "")), str(row.get("player_away", "")))
        series = player_series if player_series != "Singles" else tour
    if series == "Singles":
        series = tour if tour != "UNKNOWN" else "UNKNOWN"
    match_type = "Doubles" if "/" in str(row.get("player_home", "")) or "/" in str(row.get("player_away", "")) else "Singles"
    return tour, series, context_text or "<empty>"


def to_live_card(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["source"] = source_name
    out["match_label"] = out.get("match_label", "")
    if "tournament" not in out.columns:
        out["tournament"] = out.get("match_label", "")
    out["player_home"] = out.get("player_home", "").astype(str).map(canonical_display_name)
    out["player_away"] = out.get("player_away", "").astype(str).map(canonical_display_name)
    if source_name == "Forebet":
        bad_terms = {"fc", "united", "city", "vaasa", "gnistan", "uzbekistan", "congo"}
        def looks_like_non_tennis(r: pd.Series) -> bool:
            home = str(r.get("player_home", "")).lower()
            away = str(r.get("player_away", "")).lower()
            combo = f"{home} {away}"
            if any(term in combo.split() for term in bad_terms):
                return True
            tournament = str(r.get("tournament", "")).lower()
            if any(term in tournament for term in ["liga", "premier league", "cup qualifying", "vs vaasa"]):
                return True
            return False
        out = out[~out.apply(looks_like_non_tennis, axis=1)].copy()
    classified = out.apply(classify_row, axis=1, result_type="expand")
    out[["tour", "_series", "context_used"]] = classified
    out["match_type"] = out.apply(
        lambda r: "Doubles" if "/" in str(r.get("player_home", "")) or "/" in str(r.get("player_away", "")) else "Singles",
        axis=1,
    )
    out["pred_confidence"] = out.apply(
        lambda r: "High" if max(pd.to_numeric(r.get("prob_home"), errors="coerce") or 0,
                                 pd.to_numeric(r.get("prob_away"), errors="coerce") or 0) >= 70
        else ("Medium" if max(pd.to_numeric(r.get("prob_home"), errors="coerce") or 0,
                              pd.to_numeric(r.get("prob_away"), errors="coerce") or 0) >= 60 else "Low"),
        axis=1,
    )
    out["selected_side"] = out["predicted_winner"].map({"1": "player_home", "2": "player_away"}).fillna("")
    return out


def print_section(title: str, df: pd.DataFrame, cols: list[str]) -> None:
    print(f"\n{title}")
    if df.empty:
        print("(none)")
        return
    present = [c for c in cols if c in df.columns]
    print(df[present].to_string(index=False))


def main():
    print("="*80)
    print("🎾 RACKET FACTORY: UPCOMING PREDICTIONS")
    print("="*80)

    print("\n[1] Fetching PredixSport AI...")
    px = PredixSportPredictor()
    px_preds = px.fetch_daily()
    df_px = pd.DataFrame(px_preds) if px_preds else pd.DataFrame()
    if not df_px.empty:
        df_px = to_live_card(df_px, "PredixSport")
        print_section(
            "PredixSport live card",
            df_px.sort_values(by=[c for c in ["match_date", "match_time"] if c in df_px.columns]),
            ["match_date", "match_time", "player_home", "player_away", "match_type", "tour", "_series", "context_used", "prob_home", "prob_away", "pred_confidence", "predicted_winner_name"],
        )
    else:
        print("No matches found on PredixSport.")

    print("\n[2] Fetching BetClan AI...")
    bc = BetClanPredictor()
    bc_preds = bc.fetch_daily()
    df_bc = pd.DataFrame(bc_preds) if bc_preds else pd.DataFrame()
    if not df_bc.empty:
        for c in ["match_date", "match_time"]:
            if c in df_bc.columns:
                df_bc[c] = df_bc[c].fillna("")
        df_bc = to_live_card(df_bc, "BetClan")
        print_section(
            "BetClan live card",
            df_bc.sort_values(by=[c for c in ["match_date", "match_time"] if c in df_bc.columns]),
            ["match_date", "match_time", "player_home", "player_away", "match_type", "tour", "_series", "context_used", "prob_home", "prob_away", "pred_confidence", "predicted_winner_name"],
        )
    else:
        print("No matches found on BetClan.")

    print("\n[3] Fetching Forebet AI...")
    fb = ForebetPredictor()
    fb_preds = fb.fetch_daily_predictions("today")
    df_fb = pd.DataFrame(fb_preds) if fb_preds else pd.DataFrame()
    if not df_fb.empty:
        df_fb = to_live_card(df_fb, "Forebet")
        print_section(
            "Forebet live card",
            df_fb.sort_values(by=[c for c in ["match_date"] if c in df_fb.columns]),
            ["match_date", "player_home", "player_away", "match_type", "tour", "_series", "context_used", "prob_home", "prob_away", "pred_confidence", "predicted_winner"],
        )
    else:
        print("No matches found on Forebet.")

    combined = pd.concat([df for df in [df_px, df_bc, df_fb] if not df.empty], ignore_index=True) if (not df_px.empty or not df_bc.empty or not df_fb.empty) else pd.DataFrame()
    if not combined.empty:
        combined["pair_key"] = combined.apply(lambda r: matchup_key(r.get("player_home", ""), r.get("player_away", "")), axis=1)
        combined["name_score"] = combined["player_home"].astype(str).str.len() + combined["player_away"].astype(str).str.len()
        summary = collapse_combined_card(combined)
        print_section(
            "\n[4] Combined upcoming candidate card",
            summary.sort_values(by=[c for c in ["match_date"] if c in summary.columns]),
            ["match_date", "player_home", "player_away", "match_type", "tour", "_series", "context_used", "sources"],
        )
    else:
        print("\n[4] Combined upcoming candidate card\n(none)")

    print("\n" + "="*80)


if __name__ == "__main__":
    main()
