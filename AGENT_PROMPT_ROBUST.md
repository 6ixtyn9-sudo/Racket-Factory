# Racket Factory — Robust Agent Prompt (v2026-09-18)

You are working on `6ixtyn9-sudo/Racket-Factory` on branch `main` ONLY. No arena branches. All pushes go to `main` via fast-forward. Tests must stay green. ROI is mandatory before claiming profitability.

## Mission
Make Racket Factory do the best it can: ML chooses winners, BetExplorer consensus is REAL price, auto-tickets stake REAL accas that keep winning, self-monitoring vetoes decaying slices.

## Repo Truth
- Single source of truth: `localdata/` committed artifacts + `src/` code
- Workflow: `.github/workflows/daily.yml` — manual dispatch only (curl trigger), no schedule. Env secrets: THE_ODDS_API_KEY(S), BZZOIRO_TOKEN, SUPABASE, CALLMEBOT
- Pipeline: daily.py -> backfill_forebet/foretennis/betclan/bzzoiro/predixsport -> build_warehouse.py (twice) -> mine_edges.py -> audit_recent_picks.py + audit_clv.py -> auto_tickets.py + grade -> sync_supabase -> whatsapp
- Warehouse: `localdata/warehouse.csv.gz` built from `predictions_*.csv.gz` + `*results*.csv.gz` + `oddsportal_*.csv.gz` + `theoddsapi_*.csv.gz` + `betexplorer_*.csv.gz`
- Picks: `localdata/picks_today.json` (official), `picks_YYYY-MM-DD.json` archives, `picks_forecast_*.json`
- Audit: `picks_audit_rolling.json`, `picks_audit_YYYY-MM-DD.md`, `picks_audit_history.json` (full-coverage fix landed in 314f3ab: stale pruning, carryover dedupe, bucket reconciliation)
- CLV: `clv_rolling.json`, `clv_rolling.md`
- Odds compare: `odds_compare_status.json` — BetExplorer x OddsPortal upcoming cross-check
- Tickets: `auto_tickets_today.json`, `auto_tickets_state.json`, `auto_tickets_performance.json`

## Current State (2026-09-18, main=314f3ab)
- warehouse 3791 rows, predictions coverage 2597 rows / 10 files, results 4785 rows
- Deep search: Bzzoiro 30d backfill works (248 matched, token limit 402 at 2500), Forebet tournament mode was 0/50 parsed due to tnmscn missing — fixed in 984d16a to try Jina markdown fallback
- BetExplorer: 169 rows/day single (OddsPortal 0 rows for 5 days, only 2026-09-14 had 49 rows 4 agree/39 disagree). Latest 2026-09-17 picks 6/6 priced via BetExplorer consensus (was 0/1), but 2026-09-18 is 0/4 priced — matching broke again
- Audit: 70 archived rows, 48 settled, hit 45.8%, ROI -24.6% real (n=43), bank 106.27% (was 114% before RED DAY 2026-09-16)
- Auto tickets: 0 accas on 2026-09-17 (only 1 CERTIFIED_CLEAN) — correct NO BET behavior per user preference
- Tests: 264/264 passing after 314f3ab

## Hard Constraints (from user)
- ONLY push to `main`, never create arena branches. If task says land commit X, do: fetch, checkout main, pull, `git merge-base --is-ancestor origin/main X` must be 0 else stop, `git merge --ff-only X`, push, verify rev-parse, delete source branch, verify ls-remote empty
- Keep tests green: `PYTHONPATH=src pytest -q` — specifically `test_climb_never_enters_multi_match_box` and `test_betexplorer_parse Alpha B.` fixture must pass
- ROI mandatory: do not claim profitability without data from `picks_audit_rolling.json` + `clv_rolling.json`
- CLEAN BASH ONLY: no comments in bash blocks for operator copy-paste
- No Codespace access — must push to main and rely on Actions logs
- n<30 is fluke — do not scale stake if sample <30
- Prefers NO BET days over losing money — capital protection over coverage
- 0% min EV too low — requires positive edge, min EV +2%+ and min odds ≥1.30 to block negative EV shorts (1.19/1.24 shorts with EV -20% lost -12.68% bank)
- Do not use edit_file/write_file to modify code if instructed as consultant — provide exact bash blocks. In this repo you ARE executor, so you can edit, but keep patches minimal

## Core Tasks (in priority order)

### 1. BetExplorer consensus = REAL price (fix bad=10 fused names)
- File: `src/racketfactory/sources/_page_odds.py` (`split_match_names`, `_split_fused_names`) and `src/racketfactory/sources/betexplorer.py` (`parse_results_page`)
- Problem: `bad` count = rows where `chosen is None` — names failed to parse despite odds present. Examples: `Tiafoe F. Shelton B.` (bare space), `Zverev A.Shelton B.` (no space after dot), two separate anchors `<a>Borisiouk M.</a> vs <a>Kim D. J.</a>`, full names without initials `Alcaraz Carlos Sinner Jannik`
- Fix: normalize `.(?=[A-Z])` -> `. `, strip trailing odds `\\s+\\d{1,2}\\.\\d{1,2}(?:\\s+\\d{1,2}\\.\\d{1,2})?\\s*$`, handle doubles with `/`, fallback two-anchor parse with `_looks_like_player` + `_looks_like_player_loose`, handle `Van De Zandschulp B.` etc.
- Acceptance: `bad=0` on live results page, 4 previously unpriced matches now priced, `odds_compare_status.json` shows betexplorer_rows >=150/day sustained

### 2. OddsPortal upcoming 0 parsed fix
- File: `src/racketfactory/sources/oddsportal_upcoming.py` + `src/racketfactory/oddsportal.py`
- Log: `scan links=183/154 but match_links=0 parsed=0 candidates=0 priced=0` — listing fetched but no match links found (CF or selector change)
- Fix: ensure Playwright stealth, wait for `.eventRow` etc., fallback to BetExplorer-only is okay but cross-check `agree/disagree` must be >30% when OddsPortal works
- Acceptance: `odds_compare_status.json` `oddsportal_upcoming_rows >0` for 3 consecutive days, `cross_checked agree+disagree / merged >30%`

### 3. Forebet tournament deep search 0 matched fix
- File: `src/racketfactory/sources/forebet.py`
- Log: `Forebet fetched via relay (html) for https://www.forebet.com/en/tennis/atp-singles/atp-bastad-prediction (7924 bytes) Parsed 0 predictions` — tournament pages lack `tnmscn` anchors
- Fix landed in 984d16a: `fetch_tournament_predictions` tries `parse_page` + `parse_jina_markdown` fallback. Verify next Actions run returns >0. Also threshold in `scripts/daily.py` deep check: must be `<0.60` not `<0.40`, include all `predicted_winner*` cols, print coverage
- Acceptance: tournament backfill limit 50 returns >20 predictions, warehouse `any pred` lifts 28.9% -> 60%+, Both agree 1.9% -> 10%+

### 4. Warehouse Bzzoiro merge
- File: `src/racketfactory/warehouse.py`
- File `predictions_bzzoiro_2026-08-18_2026-09-17.csv.gz` exists (248 rows) but warehouse had `bzzoiro cols []` before fix. Archive merge fix `c06d2ec` created `predictions_*_archive_*.csv.gz` — ensure `build_warehouse` globs `predictions_*.csv.gz` including dated files and maps `predicted_winner_bzzoiro`
- Acceptance: `warehouse.csv.gz` has `predicted_winner_bzzoiro` notna >200, `src_count>=2` >100, Both agree >5%

### 5. ML chooses winners + self-monitors
- Files: `src/racketfactory/ml.py` (`ml_predict_proba`, `ml_ev`, `monitor_performance`, `score_pick_strengths`, `should_veto_slice`), `scripts/mine_edges.py`, `scripts/auto_tickets.py`
- Requirements:
  - ML answers first question: `ml_answer_odds_question` returns verdict BOOST/VETO, fair_odds=1/prob, EV, edge%
  - ML chooses winners: calibrated prob from `picks_audit_rolling.json` (High 84.2% n=38, Medium 77.2% n=101, Low 61.1% n=108), not raw confidence
  - Continuously monitors: `picks_audit_rolling.json` ROI by tour/surface/series/confidence, `clv_rolling.json` Wilson LB, `auto_tickets_performance.json` — auto-veto decaying slices via `should_veto_slice`
  - EV gating: min EV +2%+ (was 0% too low, 2%+ required), min odds 1.30, BOOST down to 1.15 allowed + EV>=1% (blocks 1.05 EV -11%)
  - Dynamic max odds: base 1.8 (winning range 1.28-1.88), up to 3.5 when prob>=0.85 n>=30 ROI>=10%
- Acceptance: `ml_verdict` used to filter picks (BOOST increases stake, VETO skips), `monitor_performance()` prints adjustments, losing contexts auto-vetoed, ROI improves from -24.6% toward positive, bank stays >100% with NO BET days allowed

### 6. Picks pricing >30% + REAL accas staked
- File: `src/racketfactory/warehouse.py` `enrich_live_card_with_api_odds` — fetch_the_odds_api_rows + fetch_comparison_rows (BetExplorer consensus) + valid_comparison_odds_pair lenient
- Acceptance: `picks_today.json` priced >30% (not 0/1), `odds_source=BetExplorer`, `odds_bookmaker=BetExplorer consensus`, `_is_paper=false` for REAL, `auto_tickets_today.json` has REAL accas staked (not only paper) when >=2 CERTIFIED_CLEAN/WATCHLIST/CAUTION playable, else 0 accas is acceptable (NO BET)

### 7. Full-coverage audit (landed in 314f3ab, must preserve)
- File: `scripts/audit_recent_picks.py`
- Must keep: stale history pruning (pick no longer in any archived ledger), carryover dedupe (same match+selection re-picked across days), bucket reconciliation, settlement finality guard (reject live/suspended/to-finish), conflict detection (two finals different winners => pending), walkover VOID, retirement to advancer
- Acceptance: `picks_audit_*.md` shows `archived pick rows: 70, duplicate rows merged: 1, stale pruned: 0, settled 48, pending 22, conflicts 0`, identity check `ledger rows - dup = audited`, By-* tables cover every pick

## How to Work
1. `git fetch origin && git checkout main && git pull --ff-only origin main`
2. Read logs: latest Actions run blob (fetch_page), `localdata/pipeline_health.json`, `odds_compare_status.json`, `picks_audit_2026-09-18.md`
3. Make minimal targeted patch (one file at a time), `PYTHONPATH=src pytest -q` — 264 tests
4. Commit with clear message, `git pull --rebase origin main`, `git push origin main`
5. Verify remote SHA: `git ls-remote https://github.com/6ixtyn9-sudo/Racket-Factory.git refs/heads/main`
6. Wait for Actions run, fetch logs, iterate
7. Never claim profit without `picks_audit_rolling.json` ROI + `clv_rolling.json` Wilson LB
8. If asked to land commit X onto main: follow 8-step safety procedure above, do not force-push or rebase if ancestor check fails

## Output Format for Fixes
- Commit message: `fix: <area> — <what> (before->after metric)`
- In PR description / chat: show before/after numbers: any pred %, Both %, bad count, priced share, ROI, bank, accas staked
- Keep bash blocks clean (no comments) for operator

## What NOT to Do
- Do not create new branches, do not push to arena/*
- Do not add new helper scripts unless explicitly asked — use one-liners
- Do not add manual odds CSVs — use automated BetExplorer/OddsPortal/TheOddsAPI
- Do not force fuzzy matches across different opponents
- Do not scale stake when n<30
- Do not chase coverage at cost of ROI — NO BET > losing bet

## Final Check: Is Racket Doing Best?
Run:
```
cat localdata/pipeline_health.json | python3 -m json.tool
cat localdata/odds_compare_status.json | python3 -m json.tool | tail -n 30
python3 -c "import json; p=json.load(open('localdata/picks_today.json')); print(f\"priced {sum(1 for x in p if x.get('odds'))}/{len(p)}\")"
cat localdata/picks_audit_2026-09-18.md | head -n 50
PYTHONPATH=src pytest -q
```
Best = warehouse any pred >60%, Both >10%, BetExplorer bad=0, OddsPortal >0 and cross_checked agree+disagree >30%, picks priced >30%, ROI real >0% (n>=30), bank >100% with NO BET days allowed, auto_tickets REAL accas staked, ML BOOST/VETO active, 264 tests green.

If not best, fix next bottleneck in priority order above and push.
