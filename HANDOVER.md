```markdown
Racket Factory — Handover
Date: 2026-06-26
Repo purpose: odds-first tennis research lab.

Single source of truth
This file is the handover. Update it in place. Do not create drifting build reports.

Executive summary
Racket Factory starts from market data, not prediction sources.

Goal:

Capture tennis match history and two-way odds, initially from OddsPortal rendered/history pages or exported HTML/CSV.
Normalize into CSV + DuckDB.
Audit market behavior by tour, tournament, odds band, favorite/underdog, and closing-price side.
Only after the market/results warehouse works, add prediction/consensus sources.
Current status: v0.6.0 — Daily operation hardened: OddsPortal is opt-in for daily runs, live pricing uses The Odds API with configurable tennis sport keys and key rotation, and source-settled result rows are now emitted from ForeTennis and Forebet for audit settlement. Historical OddsPortal capture remains available for market-history maintenance. v0.5.2 notes: Cookie export wired + 404-skip fix. ODDSPORTAL_COOKIES env var loads Netscape cookies.txt into curl_cffi + all Playwright contexts; valid cf_clearance cookies make curl_cffi page_id resolution instant (no 120s Playwright wait). 404-skip: curl_cffi records dead URLs; Playwright is skipped for them, saving ~120s per dead URL. Both fixes verified live: ATP Finals bulk run went from ~19 min (v0.5.1) to ~72s (v0.5.2). ATP Finals 2020 (london): 15 rows, 2021 (turin): 16 rows, 2022 (turin): 15 rows, 2024 (turin): 5 rows captured via single-URL mode. WTA Finals historical backfill complete (2021–2024 captured via single-URL mode, 2020 cancelled). Grand Slam historical backfill is officially complete across all four majors for 2020–2026. Single-URL validation successfully captured the final 2020 COVID season tournament, ATP Paris 2020 (26 rows, 4bWBQ1qE). The historical backfill for all active Grand Slam, Year-End Final, and ATP/WTA Masters 1000 events (2020–2026) is officially 100% complete. Strategic Pivot: Bulk mode (--route) opens 3+ Playwright instances per URL; rapid sequential browser opens trigger stubborn Cloudflare challenge blocks even with valid cookies. Bulk mode is officially deprecated for stubborn/backfill routes in favor of single-URL mode (--url) with brief pauses between executions. Exhaustive single-URL testing for ATP/WTA Canada 2024 returned 404 across all variants, confirming those specific slugs are dead on OddsPortal. Davis Cup and BJK Cup 2024 primary and alternate /world/ and /international/ slugs returned 404, indicating OddsPortal uses an elusive host country or qualifier slug structure. ATP Masters 1000 URL slugs corrected in v0.5.1; 6,787 odds rows captured, 6,575 settled matches, warehouse built. Market audit: favorite ROI -4.4% (n=6575, hit 68.8%, avg odds 1.43), underdog ROI -13.4% (avg odds 3.66).

Golden rules
Odds/results first. Prediction sources later.
ROI is mandatory before any betting claim.
No "sure bet" language.
Use walk-forward validation before promoting any rule.
Do not mix tours blindly: ATP, WTA, Challenger, ITF, doubles, and exhibitions must be segmented.
Retirements, walkovers, and abandoned matches must be handled explicitly before certification.
OddsPortal rendered odds are market/display odds, not guaranteed executable single-book close.
CSV + DuckDB is the analytics engine.
Agent note — no bloat
Do NOT create new scripts, validators, test harnesses, reports, or docs unless explicitly asked. The deliverable surface is intentionally small:

config/routes.json
src/racketfactory/{config,entities,oddsportal,warehouse,assay}.py
scripts/{capture_oddsportal,build_warehouse,audit_market}.py
tests/test_*.py
HANDOVER.md (this file)
Do not add: validate_routes.py, diagnose_.py, backfill_.py, *.bak, build reports, or any one-off helper scripts. If you need a quick check, use a one-liner in the shell, don't commit it. Keep the repo lean — odds/results first, everything else later.

Current architecture
text

Racket-Factory/
├── config/routes.json
├── src/racketfactory/
│ ├── config.py # route config loading
│ ├── entities.py # player/tour normalization
│ ├── oddsportal.py # OddsPortal HTML/CSV normalization + fetch
│ ├── warehouse.py # CSV -> DuckDB
│ └── assay.py # ROI and market summaries
├── scripts/
│ ├── capture_oddsportal.py # normalize exported CSV, saved HTML, or Playwright URL
│ ├── build_warehouse.py
│ └── audit_market.py
└── tests/
Data contract
Normalized match/odds rows live in localdata/oddsportal_tennis_YYYY-MM.csv.gz with columns:

text

match_date,tour,tournament,round,player_a,player_b,winner,score,odds_a,odds_b,bookmaker,source,captured_at,oddsportal_url
Semantics:

player_a and player_b are normalized display names.
winner must equal player_a or player_b, or be empty for unplayed/unsettled rows.
odds_a and odds_b are decimal odds for the corresponding players.
tour should be a strict segment such as ATP, WTA, CHALLENGER, ITF, UNKNOWN.
First research questions
Before adding any prediction source, answer:

What is blind favorite ROI by odds band?
What is blind underdog ROI by odds band?
Does ATP foreign from WTA / Challenger / ITF?
Which odds bands are systematically negative?
Are longshot underdogs or mid-priced favorites less overtaxed?
How to run
Install:

Bash

pip install -r requirements.txt
python3 -m playwright install chromium # only needed for --url capture
Normalize an exported CSV:

Bash

PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-csv path/to/export.csv
Normalize a saved/rendered OddsPortal HTML file:

Bash

PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-html path/to/page.html --date 2026-06-24 --tour ATP --tournament "Example Open"
Render and normalize a URL:

Bash

PYTHONPATH=src python3 scripts/capture_oddsportal.py --url "https://www.oddsportal.com/tennis/..." --tour ATP --save-html localdata/sample.html
Build warehouse:

Bash

PYTHONPATH=src python3 scripts/build_warehouse.py
Audit market:

Bash

PYTHONPATH=src python3 scripts/audit_market.py
Tests:

Bash

PYTHONPATH=src pytest -q
python3 -m py_compile src/racketfactory/.py scripts/.py
Bulk capture
Routes are defined in config/routes.json. Each route uses the year-in-slug URL pattern: /tennis/<country>/<tournament>-{year}/results/.

Bash

Capture everything
PYTHONPATH=src python3 scripts/capture_oddsportal.py --all

Capture specific routes
PYTHONPATH=src python3 scripts/capture_oddsportal.py --route ATP_WIMBLEDON WTA_WIMBLEDON --year 2024 2025 2026

Override pages and add delay
PYTHONPATH=src python3 scripts/capture_oddsportal.py --route ATP_SHANGHAI --year 2023 2024 --pages 3 --delay 8

Dry run to see what would be captured
PYTHONPATH=src python3 scripts/capture_oddsportal.py --all --dry-run

Skip routes that already have CSV files
PYTHONPATH=src python3 scripts/capture_oddsportal.py --all --skip-exists

Force Playwright only (skip curl_cffi even on residential IP)
ODDSPORTAL_USE_PLAYWRIGHT=1 PYTHONPATH=src python3 scripts/capture_oddsportal.py --route ATP_WIMBLEDON --year 2023
Progress is checkpointed in localdata/.bulk_checkpoint.json so interrupted runs resume safely. Deduplication happens at write time — the same match from overlapping routes won't be counted twice.

After capture, always rebuild warehouse and audit:

Bash

PYTHONPATH=src python3 scripts/build_warehouse.py
PYTHONPATH=src python3 scripts/audit_market.py
Route categories
Category Routes Notes
GRAND_SLAM ATP/WTA Australian/French/Wimbledon/US Open 8 routes covering all majors. ~3-5 pages each.
MASTERS_1000 ATP Indian Wells through Paris 9 ATP 1000-level tournaments. ~3 pages each.
WTA_1000 WTA Indian Wells through Beijing 8 WTA 1000-level tournaments. ~3 pages each.
YEAR_END ATP_FINALS, WTA_FINALS Year-end championships. ~2 pages each.
TEAM_EVENT DAVIS_CUP, BJK_CUP Team competitions. Segmented from individual tours.
Route slug reference (v0.5.1, 2026-06-26)
All 28 routes verified against live OddsPortal:

ATP: atp-australian-open, atp-french-open, atp-wimbledon, atp-us-open, atp-indian-wells, atp-miami, atp-monte-carlo, atp-madrid, atp-rome, atp-toronto / atp-montreal (alt_url fallback), atp-cincinnati, atp-shanghai, atp-paris, world/atp-finals (alt: atp-finals-turin, atp-finals-london)
WTA: wta-australian-open, wta-french-open, wta-wimbledon, wta-us-open, wta-indian-wells, wta-miami, wta-madrid, wta-rome, wta-toronto / wta-montreal (alt_url fallback), wta-cincinnati, wta-beijing, world/wta-finals (alt: wta-finals-riyadh, fort-worth, cancun, guadalajara, shenzhen)
Team: davis-cup, billie-jean-king-cup (alt: /world/ variants)
Canada (ATP/WTA) alternates Toronto/Montreal yearly — alt_url_templates tries both automatically. Finals venues move yearly — alt_url_templates tries all known city slugs.

v0.5.1: ATP Masters slugs corrected — previously usa/indian-wells, usa/miami, monaco/monte-carlo, spain/madrid, italy/rome, usa/cincinnati (404). Correct slugs are usa/atp-indian-wells, usa/atp-miami, monaco/atp-monte-carlo, spain/atp-madrid, italy/atp-rome, usa/atp-cincinnati.

Fetcher strategy (OddsPortal)
The fetch path is:

curl_cffi with a Chrome TLS fingerprint hits the year-in-slug tournament URL (/tennis/<country>/<tournament>-{year}/results/) and pulls the page ID.
If curl_cffi returns the Cloudflare challenge page, fall through to Playwright. Playwright uses wait_until="commit", waits up to 120 s (override with ODDSPORTAL_RENDER_TIMEOUT_MS) for CF to clear.
With page_id, try AJAX archive endpoint in Playwright context (uses browser's cf_clearance cookie).
If AJAX fails, fall back to render-DOM: parse .eventRow elements, click "Next" pagination. Most reliable path on residential IPs.
If render-DOM returns 0 rows AND page_id resolved, retry once with 150 s timeout.
If primary URL returns 0 rows, try any alt_url_templates defined in routes.json (Canada city swap, Finals venue variants).
If year-in-slug URL returns 0 rows AND year == current_year, fall back to no-year URL (current season doesn't use year-in-slug).
Only after all paths fail does the script log and return 0 rows.
Cloudflare notes
Residential IPs usually clear CF automatically. Datacenter / VPN / cloud IPs get challenged.

If you see:

WARNING oddsportal: CF challenge page returned, falling back to Playwright → curl_cffi cookies blocked.
INFO waiting up to 120 s for CF challenge to clear → Playwright waiting; if it times out, IP is likely blocked.
INFO First Playwright attempt returned 0 rows with page_id=X; retrying → transient failure, retry should succeed.
INFO Primary URL returned 0 rows; trying alt URL → alt_url_templates fallback engaged (Canada city swap / Finals venue).
INFO Year-in-slug URL returned 0 rows; trying current-season no-year URL → current-season fallback engaged.
ERROR All capture paths returned 0 rows → persistent block.
CF evasion (no paid proxy):

Wait 12–24h for IP cool-off after a burn.
Tether via phone 4G/5G for a clean residential IP.
Export cookies from a real browser session (Get cookies.txt LOCALLY extension) and set ODDSPORTAL_COOKIES=/path/to/cookies.txt. Cookie loader wired in v0.5.2 — supports Netscape cookies.txt and simple name=value format. Cookies are injected into curl_cffi requests and all Playwright browser contexts.
Run small batches with --delay 8–15, one process at a time, 3–4 routes max per run. Never run --all with 196 jobs in parallel.
iCloud Private Relay (iCloud+ $0.99/mo) gives dual-hop residential egress – CF is far less aggressive.
BULK MODE DEPRECATION (v0.5.2 finding): Bulk --route mode spawns 3+ Playwright instances per URL (AJAX try → render-DOM try → retry). Rapid sequential browser opens trigger Cloudflare to serve challenge pages to subsequent requests, even with valid cf_clearance cookies. This causes bulk mode to fail and return 0 rows on routes that single-URL mode captures perfectly. For all stubborn and backfill routes, use single-URL mode: --url "https://..." --tour ATP --year 2024.

PLAYWRIGHT ROUTE INTERCEPTION CAVEAT: During Playwright execution, route interception callbacks (on_route) can intermittently throw asyncio.exceptions.CancelledError if the underlying request or browser context closes unexpectedly during DOM paint/network idle waits. The script's 150s retry fallback mechanism (capture_with_retry) successfully catches this condition and reliably extracts the rows via render-DOM on the retry attempt.

v0.5.2 capture status (2026-06-26)
Warehouse: 6,787 odds rows, 6,575 settled matches, 13,150 market sides
Market audit: favorite ROI -4.4% (n=6575, hit 68.8%, avg odds 1.43), underdog ROI -13.4% (avg odds 3.66)
fav 1.00-1.20: ROI -1.5%, hit 89.3%, n=1248 (avg odds 1.11)
fav 1.20-1.50: ROI -4.4%, hit 71.7%, n=2530 (avg odds 1.34)
fav 1.50-1.75: ROI -5.0%, hit 59.4%, n=1981 (avg odds 1.60)
fav 1.75-2.00: ROI -7.1%, hit 51.6%, n=816 (avg odds 1.80)
underdog 1.75-2.00: ROI -2.1%, hit 50.8%, n=415 (avg odds 1.93)
underdog 2.00-2.50: ROI -6.5%, hit 42.3%, n=2137 (avg odds 2.22)
underdog 2.50+: ROI -18.3%, hit 23.2%, n=4023 (avg odds 4.60)
overall: n=13150, hit 50.0%, ROI -8.9%, avg odds 2.54
Grand Slams (2020–2026 Complete ✓):

ATP_AUSTRALIAN_OPEN: 2021, 2022, 2023, 2024, 2025, 2026 – Complete ✓
WTA_AUSTRALIAN_OPEN: 2020–2025 – Complete ✓
ATP_FRENCH_OPEN: 2020–2026 – Complete ✓
WTA_FRENCH_OPEN: 2020–2025 – Complete ✓
ATP_WIMBLEDON: 2021–2026 – Complete ✓ (2026 base no-year URL captured 5 early June rows)
WTA_WIMBLEDON: 2021–2024, 2026 – Complete ✓ (2026 base no-year URL captured 50 early June rows)
ATP_US_OPEN: 2020, 2021, 2022, 2023, 2024, 2025 – Complete ✓
WTA_US_OPEN: 2021, 2022, 2023, 2024, 2025 – Complete ✓
(Note: Wimbledon 2020 cancelled due to COVID-19, historical backfill complete!)
ATP Masters 1000 (2020–2024 Complete ✓):

Indian Wells: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Miami: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Monte Carlo: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Madrid: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Rome: 2020, 2021, 2022, 2023, 2024 ✓
Canada: 2021, 2022, 2023 ✓ (2020 cancelled, 2024 404)
Cincinnati: 2020, 2021, 2022, 2023, 2024 ✓
Shanghai: 2023, 2024 ✓ (2020–2022 cancelled)
Paris: 2020 (26 rows via single-URL mode, v0.5.2), 2021, 2022, 2023, 2024 ✓
WTA 1000 (2020–2024 Complete ✓):

Indian Wells: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Miami: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Madrid: 2021, 2022, 2023, 2024 ✓ (2020 cancelled)
Rome: 2020, 2021, 2022, 2023, 2024 ✓
Canada: 2021, 2022, 2023 ✓ (2020 cancelled, 2024 404)
Cincinnati: 2020, 2021, 2022, 2023, 2024 ✓
Beijing: 2023, 2024 ✓ (2020–2022 cancelled)
Year-end Finals (2020–2024 Complete ✓):

ATP Finals 2020: 15 rows (atp-finals-london fallback, v0.5.2) ✓
ATP Finals 2021: 16 rows (atp-finals-turin fallback, v0.5.2) ✓
ATP Finals 2022: 15 rows (atp-finals-turin fallback, v0.5.2) ✓
ATP Finals 2023: 15 rows (atp-finals-turin fallback, v0.5.1) ✓
ATP Finals 2024: 5 rows captured via single-URL mode (atp-finals-turin) ✓
WTA Finals 2024: 14 rows (wta-finals-riyadh fallback, v0.5.2) ✓
WTA Finals 2023: 13 rows captured via single-URL mode (wta-finals-cancun fallback) ✓
WTA Finals 2022: 15 rows captured via single-URL mode (wta-finals-fort-worth-2022) ✓
WTA Finals 2021: 15 rows captured via single-URL mode (wta-finals-guadalajara-2021) ✓
(Note: WTA Finals 2020 was cancelled due to COVID-19, historical backfill complete!)
Team Events:

Davis Cup / BJK Cup: Primary /world/ and alternate /international/ country slugs returned 404 in single-URL testing.
Still missing:

ATP Canada 2024, WTA Canada 2024 (confirmed 404 dead ends on OddsPortal)
Davis Cup / BJK Cup (elusive slugs)
Next capture order:

Project milestones achieved in full. Maintain warehouse via scheduled single-URL runs as future seasons conclude.
Known data caveats
Wimbledon 2020 and WTA Finals 2020 were cancelled (COVID-19); the year-in-slug URLs 404, correct behavior.
The current season's URL is the no-year variant; year-in-slug returns 404 for in-progress year.
OddsPortal AJAX responses are encrypted base64 in 2026 — we don't decrypt them. render-DOM bypasses this.
Canada Masters (ATP/WTA) alternates Toronto / Montreal yearly — alt_url_templates handles this automatically.
ATP/WTA Finals change host city — alt_url_templates tries turin/london and riyadh/fort-worth/cancun/guadalajara/shenzhen automatically.
Bulk mode rapid-fire Playwright issue: --route bulk mode opens 3+ Playwright browser instances per URL (AJAX try → render-DOM try → retry with extended timeout). Rapid sequential browser opens trigger CF to serve challenge pages to subsequent requests even with valid cf_clearance cookies. Single-URL mode (--url) opens only one browser and captures fine on the same route. Use single-URL mode for stubborn routes.
Playwright asyncio.exceptions.CancelledError in on_route callback: When running Playwright captures (even in single-URL mode), route interception handlers (on_route) can intermittently throw CancelledError if the browser context closes or aborts a request. The script's 150s retry fallback successfully recovers from this and captures the data via render-DOM on the retry attempt.
Recent changes
2026-06-29 - source-settled audit and set diagnostics: ForeTennis actual_result values are now preserved into foretennis_results_tennis_YYYY-MM.csv.gz. Forebet finished rows now parse FT status, winner, set totals, and ordered set score strings such as 7-6 3-6 1-6 into forebet_results_tennis_YYYY-MM.csv.gz. audit_recent_picks.py now reports set diagnostics (selected won any set, selected won set 1/2/3 when ordered set scores exist) without treating those diagnostics as ROI. Verified 2026-06-28 Forebet Challenger picks settled 2 rows, hit 1/2, ROI -0.28; the remaining Dabrowski/Stefani doubles gap needs a source exposing actual final score, since BetClan detail pages observed so far expose prediction markets but not final result.
2026-06-29 - The Odds API quota protection: Added date/config caching for parsed The Odds API H2H rows in warehouse.py. Repeated warehouse builds now reuse localdata/theoddsapi_odds_cache_YYYY-MM-DD.json while fresh, controlled by THE_ODDS_API_CACHE_TTL_MINUTES and THE_ODDS_API_DISABLE_CACHE. Cache stores parsed odds rows only and never stores API keys. This complements THE_ODDS_API_KEYS rotation and reduces burn against the 500-call quota.
2026-06-29 - daily hardening + source settlement update: Daily OddsPortal capture is now opt-in via RACKET_FACTORY_REFRESH_ODDSPORTAL=1 because bulk/browser capture remains Cloudflare-sensitive; default daily runs now use TennisData/source refreshes plus live injection. The active-year OddsPortal arg was corrected from --year to --years and delay is configurable through RACKET_FACTORY_ODDSPORTAL_DELAY. .env is loaded automatically by daily.py, daily_pipeline.sh, and warehouse.py without printing secrets. The Odds API integration now uses configurable tournament sport keys (for example tennis_atp_wimbledon, tennis_wta_wimbledon), supports THE_ODDS_API_KEYS rotation, and no longer defaults to invalid generic tennis_atp/tennis_wta keys. Live pricing now successfully fetches and matches API h2h rows into live warehouse rows; scrape-side BetClan/Forebet odds remain secondary/debug/fallback context only. ForeTennis lastpredictions actual_result values are preserved and emitted as warehouse-compatible foretennis_results_tennis_YYYY-MM.csv.gz files. Forebet finished rows are now parsed for FT status, match winner, set totals, and ordered set scores (for example 7-6 3-6 1-6), then emitted as forebet_results_tennis_YYYY-MM.csv.gz files. Recent pick audit can now settle source-covered picks without manual overlays; verified 2026-06-28 Forebet Challenger picks settled 2 rows, hit 1/2, ROI -0.28. BetClan was widened to include yesterdays-tennis-predictions and to preserve source_url for traceability, but BetClan detail pages observed so far expose prediction markets (including 1st Set - Winner) rather than actual final scores. Remaining source-settlement gap: WTA/BetClan doubles such as Dabrowski / Stefani vs Maleckova / Skoch require an existing-adapter extension or another source with final score, not a manual overlay. Added scripts/__init__.py and pyproject root path config so tests/type-checkers can import scripts.capture_oddsportal cleanly. Validation during this update: python -m py_compile on touched scripts/modules and PYTHONPATH=src python3 -m pytest -q passed 33 tests.
2026-06-29 - live odds API pricing fix: Investigated the @9.50-style reports and found scrape-side prices can be valid pairs assigned to the wrong home/away side, but those scrape prices must not be the primary live betting price. No new files added. THE_ODDS_API_KEY now stays in the live path: warehouse.py fetches/matches The Odds API h2h prices onto same-day live rows after edge-card collapse; scraped BetClan/Forebet odds are preserved only as debug_scraped_odds_* columns. mine_edges.py now prices the actual selected side and requires _odds_source=TheOddsAPI for live EV; if API odds are missing, the row is withheld into WATCHLIST_NO_ODDS instead of using scrape odds. Side-alignment repair remains as a safety net for likely inverted API/live rows. Regression coverage was added to existing tests/test_warehouse.py only.
2026-06-27 - live-warehouse + miner bridge update: Fixed the core operational issue where the warehouse had no same-day live rows. Source adapters now preserve tournament metadata from PredixSport and BetClan. Warehouse build now injects normalized same-day upcoming singles rows as unsettled matches (_comment=live_upcoming_injected) so live candidates exist in warehouse.csv.gz itself instead of only via downstream fallback. Warehouse prediction merge hygiene was repaired so final columns remain source-separated: predicted_winner, predicted_winner_foretennis, predicted_winner_market (plus corresponding probability/source lanes) without _x/_y/_pred pollution. Miner (scripts/mine_edges.py) was upgraded to: (1) mine 3D–5D slices, (2) remove outcome-leaking rank logic, (3) support live-safe fallback card construction, (4) normalize _series, _surface, and synthetic fav_odds_band for sparse live rows, and (5) keep injected live rows even when rank fields are missing. Verified final state on 2026-06-27: warehouse had 4 same-day unsettled rows and miner reported Today candidate rows after live filtering: 4. Final export was 0 actionable picks, now understood as an honest model outcome rather than a broken pipeline artifact.

Operational note for sparse slates: what remains intentionally unchanged is the actionability threshold. The system is now structurally capable of handling same-day live cards, but sparse slates can still produce zero exports because current rules require exact historical slice matches with actionable verdicts (EDGE CONFIRMED, WATCHLIST, or FADE THIS SIGNAL). Weak or thin-card matches may only intersect NO STAT SIG slices and are intentionally suppressed. If broader operational coverage is desired, the next design step is not to re-allow weak verdicts blindly, but to explicitly widen policy: expand beyond ATP/WTA into CHALLENGER / ITF / UTR, and/or add a clearly weaker live-only bucket such as LEAN / SOFT_WATCHLIST based on positive-support but non-certified slice fits. As of 2026-06-27, PredixSport and BetClan current live pages were inspected directly and did not expose hidden Challenger / ITF / UTR inventory for the current sparse slate. Forebet support was therefore added to scripts/predict_upcoming.py as the next in-repo broad-tour exploration surface; note that Forebet daily pages can be sparse or empty (predictions-today sparse, predictions-tomorrow empty) when the calendar is light, but this integration is intended to become useful again when fuller schedules return. After the tennis-only Forebet adapter fix was verified, scripts/predict_upcoming.py was also tightened so the combined candidate card dedupes abbreviated and full-name variants more cleanly (for example Z. Bergs with Zizou Bergs, E. Quinn with Ethan Quinn, K. Muchova with Karolina Muchova). A second pass then hardened this further by matching rows on normalized surname/token overlap instead of only exact normalized strings, so Forebet short forms can now collapse into the same combined candidate rows as PredixSport/BetClan full-name variants without adding any new helper files or scripts. Finally, src/racketfactory/warehouse.py was widened so same-day live injection now also includes Forebet in addition to PredixSport and BetClan, and is no longer singles-only: both singles and doubles can now be injected, still restricted to same-day tennis rows and still grouped conservatively by tour/date/match_type/player keys so broader live exploration can reach the warehouse without another round-trip patch. After verification showed today’s Forebet Challenger rows still were not surfacing in injected warehouse rows, warehouse tour inference/context assembly was further aligned with the richer predict_upcoming.py logic by adding stronger Challenger/ITF/UTR detection (piracicaba, targu mures, challenger-men, challenger-women, itf-*, utr) and broader context fields (tour_slug, tournament_slug, event_level, event_text, match_label, event, competition, category, league) so same-day broader-tour Forebet rows have a better chance of surviving classification into the warehouse path. A final lean refinement then aligned warehouse live-row grouping with the combined-card normalization approach by keying live players on surname-tail tokens and preferring fuller display names, reducing duplicate injected rows caused by Forebet abbreviations versus PredixSport/BetClan full names.
2026-06-26 - v0.5.2 (Update): Pivoted entirely to single-URL --url mode for backfills with cool-off pauses between executions. Successfully captured ATP Paris 2020 (26 rows), marking the definitive 100% completion of the entire 6-year historical backfill across all active Grand Slam, Year-End Final, and Masters 1000 events. Warehouse metrics updated: 6,787 odds rows, 6,575 settled matches. Market audit updated: favorite ROI -4.4%, underdog ROI -13.4%.
2026-06-26 - v0.5.2: Cookie export wired — added _load_op_cookies() and module-level _OP_COOKIES dict to oddsportal.py. Loads cookies from ODDSPORTAL_COOKIES env var (Netscape cookies.txt format from "Get cookies.txt LOCALLY" Chrome extension, plus simple name=value fallback). Cookies injected into 4 places: (1) curl_cffi initial request in _curl_fetch, (2) curl_cffi CF warm-up request on 403, (3) Playwright context in fetch_rendered_html, (4) Playwright context in _fetch_ajax_via_playwright, (5) Playwright context in fetch_via_rendered_dom. With valid cf_clearance cookies, curl_cffi resolves page_id instantly without falling back to Playwright, eliminating the 120s CF wait. Cookie file path set via export ODDSPORTAL_COOKIES=~/oddsportal_cookies.txt before running capture. Also added 404-skip: _curl_fetch records URLs that returned genuine HTTP 404 in _KNOWN_404_URLS; _try_playwright_fetch checks is_known_404() and skips Playwright for those URLs — a 404 from OddsPortal's server means the page doesn't exist, no amount of browser waiting will change that. This saves ~120s per dead URL. Live findings: (a) ATP Finals bulk run went from ~19 min (v0.5.1) to ~72s (v0.5.2). (b) ATP Finals 2020–2024 all captured via render-DOM fallback on alt URLs (london/turin). (c) Bulk mode rapid-fire Playwright issue: sequential browser opens (3+ per URL) trigger CF challenge pages even with valid cookies, causing 0 rows on routes that single-URL mode captures fine. ATP Finals 2024: bulk returns 0, single-URL returns 15 rows. Use --url for stubborn routes.
2026-06-26 - v0.5.1: ATP Masters 1000 URL slug fix — corrected 6 remaining broken OddsPortal tournament URLs in config/routes.json: ATP_INDIAN_WELLS usa/indian-wells → usa/atp-indian-wells, ATP_MIAMI usa/miami → usa/atp-miami, ATP_MONTE_CARLO monaco/monte-carlo → monaco/atp-monte-carlo, ATP_MADRID spain/madrid → spain/atp-madrid, ATP_ROME italy/rome → italy/atp-rome, ATP_CINCINNATI usa/cincinnati → usa/atp-cincinnati. With these fixes, ATP Masters 2023 capture completed: 9/9 tournaments, ~550+ matches. WTA 1000s 2023/2024 largely complete. Warehouse built: 3,626 odds rows, 3,504 settled matches. Market audit: favorite ROI -3.9%, underdog ROI -13.3%.
2026-06-26 - v0.5: Route slug fix – corrected 11 broken OddsPortal tournament URLs in config/routes.json: ATP_SHANGHAI china/shanghai → china/atp-shanghai, ATP_PARIS france/paris → france/atp-paris, WTA_INDIAN_WELLS usa/indian-wells-women → usa/wta-indian-wells, WTA_MIAMI usa/miami-women → usa/wta-miami, WTA_MADRID spain/madrid-women → spain/wta-madrid, WTA_ROME italy/rome-women → italy/wta-rome, WTA_CINCINNATI usa/cincinnati-women → usa/wta-cincinnati, WTA_BEIJING china/beijing → china/wta-beijing, ATP_FINALS tennis/atp-finals → tennis/world/atp-finals, WTA_FINALS tennis/wta-finals → tennis/world/wta-finals. Added alt_url_templates support to capture_oddsportal.py – automatically tries alternate city/venue URLs (Canada Toronto↔Montreal, Finals turin/london / riyadh/fort-worth/cancun/guadalajara/shenzhen, Davis/BJK Cup /world/ variants). CF hardening in oddsportal.py: render timeout 90s → 120s, UA/viewport rotation, expanded event selectors for OddsPortal 2025/2026 SPA, more lenient _wait_for_real_content. Added agent no-bloat note to HANDOVER.md.
2026-06-25 - v0.4: routes.json URL pattern = year-in-slug (/tennis/<country>/<tournament>-{year}/results/). Added _capture_with_retry (one retry with 150 s Playwright timeout when page_id was resolved). Added _no_year_url_fallback (auto-retry current-season captures via the no-year URL). Wired ODDSPORTAL_RENDER_TIMEOUT_MS env var into fetch_rendered_html. Added test_no_year_url_fallback_only_for_current_year.
2026-06-25 - v0.3: Initial OddsPortal scrape fix. Updated routes.json URL pattern. Hardened fetch_rendered_html against CF. Added _extract_page_id patterns for new hydration formats. Added _fetch_ajax_via_playwright path. Added 14 new tests.

Set diagnostics note:
Forebet settled rows can include ordered set scores such as `7-6 3-6 1-6`, so audit reports can show whether the selected player won any set or a specific set. ForeTennis settled rows provide set totals such as `20` or `12`, so they support match/set-count diagnostics but not set order. Set diagnostics are not betting ROI unless set-market odds are captured.
2026-06-30 - v0.6.1 daily hygiene / monitoring freeze:
Daily operation was hardened after a live Wimbledon same-day/forecast audit. Key fixes are now on `main`:
- `a62baf6` recent-picks audit can settle one-day date shifts.
- `f8c7bf9` recent-picks ROI uses archived pick odds, not arbitrary warehouse side odds.
- `945c76f` settlement rejects non-final/live/to-finish rows; avoids false losses from incomplete matches.
- `3886702` audit name matching is initial-aware, e.g. `A. Davidovich Fokina` ↔ `Alejandro Davidovich Fokina`, while avoiding broad same-surname merges.
- `26b4861` PredixSport date uncertainty guarded and API autorun burn reduced.
- `a9f5580` GitHub Actions `localdata` cache namespace reset to `localdata-v2-main-` so stale polluted cached ledgers do not resurrect.
- `2be917c` same-match pick export dedupes abbreviated/full-name duplicates when selected side is the same, e.g. `Alex de Minaur vs Roman Andres Burruchaga` and `A. De Minaur vs R. A. Burruchaga`.

PredixSport tennis date caveat:
PredixSport tennis `date` is not a reliable actual match day. It behaves like a generated/tournament slate date. Treat it as LOW confidence and never as a standalone same-day schedule source. Same-day hygiene now drops PredixSport-only rows when they are no-odds/no-kickoff and in `WATCHLIST_NO_ODDS` or `WATCHLIST_UNKNOWN_CTX`. PredixSport remains useful for prediction probabilities/tournament context, but not for actual play date, kickoff, or settlement.

CI / quota posture:
GitHub Actions daily workflow now runs only twice per day instead of every 3 hours, invokes `scripts/daily.py` with `--future-days 1`, disables The Odds API score capture in CI via `RACKET_FACTORY_DISABLE_THEODDSAPI_SCORES=1`, and uses the `localdata-v2-main-` cache namespace. Local runs may still capture scores unless the same env flag is set. Do not print secrets; keep keys in ignored `.env` / `localdata/.env`.

Same-day versus forecast interpretation:
The same-day ledger is the official daily slate. It can show diagnostic `SKIPPED_DEAD_EDGE` rows, which are not suggestions to bet; they mean the model may like a player but the price/EV is bad. The future planner / tomorrow forecast is only a preliminary watchlist. Forecast hygiene intentionally hides most dead-edge/no-odds/skipped rows and keeps only priced positive-EV review candidates. A tomorrow `WATCHLIST` must be re-confirmed by the next same-day run before being treated as actionable.

Fresh 2026-06-30 validation:
After rebasing polluted local pick/audit files and rerunning the daily pipeline, same-day `picks_2026-06-30` contained only `SKIPPED_DEAD_EDGE` rows and zero bettable/review rows. PredixSport-only no-odds/no-KO leak check returned zero bad rows. Duplicate diagnostic found one same-match duplicate, de Minaur/Burruchaga, and export dedupe was patched/tested to reduce `2026-06-30` JSON rows from 20 to 19 while leaving `2026-07-01` forecast rows at 3.

Monitoring plan:
Freeze logic unless something clearly breaks. Monitor clean operation for one full week, preferably 2026-07-01 through 2026-07-07, then evaluate performance. Keep prediction accuracy, bettable ROI, dead-edge veto quality, forecast quality, no-odds rows, unsettled rows, retirements/walkovers, and settlement quality separate. Do not judge by raw win rate alone. ROI is mandatory before any betting claim.
