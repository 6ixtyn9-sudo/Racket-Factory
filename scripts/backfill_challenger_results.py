#!/usr/bin/env python3
"""
Backfill Challenger results from Flashscore/ATP Challenger archive

Fetches yesterday's Challenger results and writes warehouse-compatible CSVs
for settlement. Uses Flashscore API or ATP Challenger results page.

Usage:
    PYTHONPATH=src python3 scripts/backfill_challenger_results.py --days 3 --output-dir localdata
"""
import argparse
import logging
import sys
from pathlib import Path
from datetime import date, timedelta
import re
import pandas as pd
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL=True
except:
    HAS_CURL=False
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("backfill_challenger")

def parse_tennisexplorer_html(html: str, target_date: str) -> list[dict]:
    """Paired-row parser for TennisExplorer results — tested structure"""
    results=[]
    soup=BeautifulSoup(html, "html.parser")
    # TennisExplorer uses div#tournament-fixtures or table.result
    # Header structure: <div class="box"> <div class="tournament">... <table class="result">
    # Each match is 2 rows: first row player A, second row player B, with score columns
    # Winner is marked with <span class="winner"> or <strong> or class t-name with bold
    tables=soup.find_all("table", class_="result")
    for table in tables:
        tournament=""
        # Find preceding tournament name
        # Look backwards for tournament header
        prev=table.find_previous("span", class_="tournament")
        if not prev:
            prev=table.find_previous("div", class_="tournament")
        if prev:
            tournament=prev.get_text(strip=True)
        # Also check for challenger in tournament text or data attributes
        rows=table.find_all("tr")
        i=0
        while i < len(rows):
            row=rows[i]
            cols=row.find_all("td")
            if len(cols) < 2:
                i+=1
                continue
            # Paired-row format: two consecutive rows share same match
            # Row 0: player A + scores, Row 1: player B + scores
            # Detect if next row exists and looks like second player of same match
            # Heuristic: if cols contain t-name class and next row also has t-name
            has_tname = any("t-name" in (c.get("class") or []) or c.find("a") for c in cols)
            if has_tname and i+1 < len(rows):
                next_row=rows[i+1]
                next_cols=next_row.find_all("td")
                next_has_tname = any("t-name" in (c.get("class") or []) or c.find("a") for c in next_cols)
                if next_has_tname and len(next_cols) >= 2:
                    # Paired rows — parse both
                    try:
                        # Player names from first td with t-name or first td
                        def extract_player(tds):
                            for td in tds:
                                if "t-name" in (td.get("class") or []):
                                    a=td.find("a")
                                    if a:
                                        return a.get_text(strip=True)
                                    return td.get_text(strip=True)
                            # fallback first td
                            return tds[0].get_text(strip=True) if tds else ""
                        p1=extract_player(cols)
                        p2=extract_player(next_cols)
                        if not p1 or not p2:
                            i+=2
                            continue
                        # Determine winner: check for winner class, bold, or strong
                        p1_winner=False
                        p2_winner=False
                        # Check row classes
                        if "winner" in str(row.get("class","")).lower() or row.find(class_=re.compile("winner", re.I)):
                            p1_winner=True
                        if "winner" in str(next_row.get("class","")).lower() or next_row.find(class_=re.compile("winner", re.I)):
                            p2_winner=True
                        # Check td classes
                        for td in cols:
                            if any("winner" in cls.lower() for cls in (td.get("class") or [])):
                                p1_winner=True
                            if td.find("strong") and p1 in td.get_text():
                                # strong often marks winner
                                pass
                        for td in next_cols:
                            if any("winner" in cls.lower() for cls in (td.get("class") or [])):
                                p2_winner=True
                        # Score columns — remaining tds after first are scores
                        # Extract set scores to determine winner if not marked
                        def extract_scores(tds):
                            scores=[]
                            for td in tds[1:]:
                                txt=td.get_text(strip=True)
                                # score like "6", "3", "7", etc or empty
                                if re.match(r"^\d+\s*\(?\d*\)?$", txt) or txt in ["", "RET", "W/O", "DEF"]:
                                    scores.append(txt)
                            return scores
                        scores1=extract_scores(cols)
                        scores2=extract_scores(next_cols)
                        # If winner not marked, infer from sets won
                        if not p1_winner and not p2_winner and scores1 and scores2:
                            p1_sets=0
                            p2_sets=0
                            for s1,s2 in zip(scores1, scores2):
                                if not s1 or not s2:
                                    continue
                                # Handle RET/W/O
                                if s1 in ["RET","W/O","DEF"]:
                                    p2_sets+=1
                                    continue
                                if s2 in ["RET","W/O","DEF"]:
                                    p1_sets+=1
                                    continue
                                try:
                                    n1=int(re.search(r"\d+", s1).group())
                                    n2=int(re.search(r"\d+", s2).group())
                                    if n1>n2:
                                        p1_sets+=1
                                    elif n2>n1:
                                        p2_sets+=1
                                except:
                                    pass
                            if p1_sets>p2_sets:
                                p1_winner=True
                            elif p2_sets>p1_sets:
                                p2_winner=True
                        winner=p1 if p1_winner else p2 if p2_winner else ""
                        if not winner:
                            # Fallback: if we have bold/strong in first row, assume first wins
                            # Skip ambiguous to avoid false settlement
                            i+=2
                            continue
                        loser=p2 if winner==p1 else p1
                        # Build score string
                        score_str=""
                        if scores1 or scores2:
                            pairs=[]
                            for s1,s2 in zip(scores1, scores2):
                                if s1 and s2:
                                    pairs.append(f"{s1}-{s2}" if winner==p1 else f"{s2}-{s1}")
                            score_str=" ".join(pairs)
                        results.append({
                            "match_date": target_date,
                            "tour": "CHALLENGER" if "challenger" in tournament.lower() else "ATP" if "atp" in tournament.lower() else "UNKNOWN",
                            "tournament": tournament,
                            "player_a": winner,
                            "player_b": loser,
                            "winner": winner,
                            "score": score_str,
                        })
                    except Exception as e:
                        logger.debug(f"paired-row parse failed at row {i}: {e}")
                    i+=2
                    continue
            # Single-row fallback: player vs player in same row
            if len(cols)>=3:
                try:
                    p1=cols[0].get_text(strip=True)
                    sc=cols[1].get_text(strip=True)
                    p2=cols[2].get_text(strip=True)
                    if not p1 or not p2:
                        i+=1
                        continue
                    # Determine winner from class
                    winner=""
                    for c in cols:
                        if any("winner" in cls.lower() for cls in (c.get("class") or [])):
                            # Winner cell text might be player name
                            txt=c.get_text(strip=True)
                            if txt in [p1,p2]:
                                winner=txt
                    if not winner:
                        # Try bold
                        strongs=row.find_all("strong")
                        for s in strongs:
                            txt=s.get_text(strip=True)
                            if txt in [p1,p2]:
                                winner=txt
                                break
                    if not winner:
                        i+=1
                        continue
                    loser=p2 if winner==p1 else p1
                    results.append({
                        "match_date": target_date,
                        "tour": "CHALLENGER" if "challenger" in tournament.lower() else "UNKNOWN",
                        "tournament": tournament,
                        "player_a": winner,
                        "player_b": loser,
                        "winner": winner,
                        "score": sc,
                    })
                except Exception:
                    pass
            i+=1
    return results

def fetch_flashscore_challenger_results(target_date: str) -> list[dict]:
    """Try TennisExplorer with paired-row parser"""
    results=[]
    try:
        url=f"https://www.tennisexplorer.com/results/?type=all&year={target_date[:4]}&month={target_date[5:7]}&day={target_date[8:10]}"
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        if HAS_CURL:
            try:
                resp=curl_requests.get(url, headers=headers, timeout=20, impersonate="chrome")
            except Exception as e:
                print(f"curl_cffi failed for {target_date}: {e}, trying requests")
                resp=requests.get(url, headers=headers, timeout=20)
        else:
            resp=requests.get(url, headers=headers, timeout=20)
        if resp.status_code!=200:
            logger.warning(f"TennisExplorer returned {resp.status_code} for {target_date}")
            return []
        results=parse_tennisexplorer_html(resp.text, target_date)
        logger.info(f"TennisExplorer parsed {len(results)} results for {target_date} (paired-row parser)")
    except Exception as e:
        logger.warning(f"Failed to fetch challenger results for {target_date}: {e}")
        import traceback
        traceback.print_exc()
    return results

def fetch_atp_challenger_results(target_date: str) -> list[dict]:
    return []

def write_result_rows(rows: list[dict], output_dir: Path):
    if not rows:
        return []
    df=pd.DataFrame(rows)
    from datetime import datetime
    df["round"]=""
    df["odds_a"]=pd.NA
    df["odds_b"]=pd.NA
    df["bookmaker"]=""
    df["source"]="Challenger_results"
    df["captured_at"]=datetime.now().isoformat(timespec="seconds")
    df["oddsportal_url"]=""
    df["_surface"]=""
    df["_court"]=""
    df["_series"]=""
    df["_comment"]="result_from_challenger_backfill"
    df["_location"]=""
    df["_winner_rank"]=pd.NA
    df["_loser_rank"]=pd.NA
    df["_odds_source"]=""
    df["_is_live"]=False
    df["_score_perspective"]=""

    df["match_date"]=df["match_date"].astype(str).str[:10]
    written=[]
    for month, group in df.groupby(df["match_date"].str[:7]):
        path=output_dir / f"challenger_results_tennis_{month}.csv.gz"
        if path.exists():
            old=pd.read_csv(path, low_memory=False)
            combined=pd.concat([old, group], ignore_index=True, sort=False)
        else:
            combined=group.copy()
        combined=combined.drop_duplicates(subset=["match_date","tour","tournament","player_a","player_b"], keep="last")
        combined.to_csv(path, index=False, compression="gzip")
        written.append(path)
        logger.info(f"Wrote {len(group)} challenger results to {path}")
    return written

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="Days back from today to fetch")
    ap.add_argument("--output-dir", default="localdata")
    ap.add_argument("--date", help="Specific date YYYY-MM-DD")
    args=ap.parse_args()

    out_dir=Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dates=[]
    if args.date:
        dates=[args.date]
    else:
        today=date.today()
        for i in range(args.days):
            dates.append((today - timedelta(days=i+1)).isoformat())

    all_rows=[]
    for d in dates:
        rows=fetch_flashscore_challenger_results(d)
        if not rows:
            rows=fetch_atp_challenger_results(d)
        all_rows.extend(rows)

    if all_rows:
        write_result_rows(all_rows, out_dir)
        logger.info(f"Total {len(all_rows)} challenger results written")
    else:
        logger.warning("No challenger results found")

if __name__=="__main__":
    main()
