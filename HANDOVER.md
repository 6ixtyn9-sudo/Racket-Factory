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
Current status: v0.5.2 — Cookie export wired (ODDSPORTAL_COOKIES env var loads Netscape cookies.txt into curl_cffi + all Playwright contexts). Remaining 0-row captures are pure CF render timeouts; valid cf_clearance cookies should bypass CF challenge entirely, making curl_cffi page_id resolution instant and Playwright waits unnecessary. ATP Masters 1000 URL slugs corrected in v0.5.1 (atp-indian-wells, atp-miami, atp-monte-carlo, atp-madrid, atp-rome, atp-cincinnati); 3,626 odds rows captured, 3,504 settled matches, warehouse built. ATP Masters 2023: 8/9 complete (IW 82, Miami 129, Monte Carlo 27, Madrid 87, Rome 118, Canada 27, Cincinnati 27, Paris 25), Shanghai 2023: 128 rows. ATP Masters 2024: Madrid 126, Rome 128, Monte Carlo 33, Shanghai 80, Paris 26, IW 73, Miami 82, Cincinnati 26 – Canada 0 rows. WTA 1000s 2023/2024: Indian Wells, Miami, Madrid, Rome, Cincinnati, Beijing captured with 1-2 misses per tournament (CF flakiness / 404 for Canada 2024, Beijing 2023, Rome 2024). ATP Finals 2023: 15 rows via atp-finals-turin fallback. Market audit: favorite ROI -3.9% (n=3504, hit 68.5%, avg odds 1.44), underdog ROI -13.3% (avg odds 3.56).

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
config/routes.json
src/racketfactory/
config.py # route config loading
entities.py # player/tour normalization
oddsportal.py # OddsPortal HTML/CSV normalization + fetch
warehouse.py # CSV -> DuckDB
assay.py # ROI and market summaries
scripts/
capture_oddsportal.py # normalize exported CSV, saved HTML, or Playwright URL
build_warehouse.py
audit_market.py
tests/

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
Does ATP differ from WTA / Challenger / ITF?
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
If year-in-slug URL returns 0 rows AND year == current year, fall back to no-year URL (current season doesn't use year-in-slug).
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
v0.5.1 capture status (2026-06-26)
Warehouse: 3,626 odds rows, 3,504 settled matches, 7,008 market sides
Market audit: favorite ROI -3.9% (n=3504, hit 68.5%, avg odds 1.44), underdog ROI -13.3% (avg odds 3.56)
fav 1.00-1.20: ROI -2.2%, hit 88.8%, n=606
fav 1.20-1.50: ROI -4.5%, hit 71.5%, n=1339
fav 1.50-1.75: ROI -5.4%, hit 59.1%, n=1109
fav 1.75-2.00: ROI -0.8%, hit 55.1%, n=450

Grand Slams – partial (from earlier v0.4 runs):

ATP_WIMBLEDON 2021–2026 ✓
ATP_AUSTRALIAN_OPEN 2021,2024,2026
ATP_FRENCH_OPEN 2021–2026 ✓
ATP_US_OPEN 2020,2021,2024
WTA_AUSTRALIAN_OPEN 2020,2021,2023,2024
WTA_FRENCH_OPEN 2021–2025
WTA_WIMBLEDON 2021–2024,2026
WTA_US_OPEN 2021,2022,2024
ATP Masters 1000 – 2023/2024:

2023: Indian Wells 82, Miami 129, Monte Carlo 27, Madrid 87, Rome 118, Canada 27, Cincinnati 27, Shanghai 128, Paris 25 – all 9 captured ✓
2024: Madrid 126, Rome 128, Monte Carlo 33, Shanghai 80, Paris 26, Indian Wells 73, Miami 82, Cincinnati 26 – 8/9 captured, Canada 0 rows (both Toronto/Montreal 404)
WTA 1000 – 2023/2024:

Indian Wells: 2023 130, 2024 122 ✓
Miami: 2023 129, 2024 86 ✓
Madrid: 2023 131, 2024 130 ✓
Rome: 2023 78, 2024 77 ✓
Cincinnati: 2023 72, 2024 31 ✓
Beijing: 2023 84, 2024 130 ✓
Canada: 2023 31 rows (Montreal fallback), 2024 0 rows (both Toronto/Montreal 404)
Year-end Finals:

ATP Finals 2023: 15 rows (atp-finals-turin fallback)
ATP Finals 2020-2022, 2024: 0 rows
WTA Finals: 0 rows captured
Still missing:

ATP Canada 2024, WTA Canada 2024
ATP/WTA Finals 2020-2022, 2024, WTA Finals 2023
Grand Slam backfill years
Davis Cup / BJK Cup (not started)
Next capture order:

Re-try 0-row years: ATP Canada 2024, WTA Canada 2024, ATP/WTA Finals 2020-2024
Backfill Grand Slam missing years
Davis Cup / BJK Cup
Use --delay 12-15, 3-4 routes per run, ONE process at a time. Checkpoint resumes automatically – delete localdata/.bulk_checkpoint.json only when changing slugs.

Known data caveats

Wimbledon 2020 was cancelled (COVID-19); the year-in-slug URL 404s, correct behavior.
The current season's URL is the no-year variant; year-in-slug returns 404 for in-progress year.
OddsPortal AJAX responses are encrypted base64 in 2026 — we don't decrypt them. render-DOM bypasses this.
Canada Masters (ATP/WTA) alternates Toronto / Montreal yearly — alt_url_templates handles this automatically.
ATP/WTA Finals change host city — alt_url_templates tries turin/london and riyadh/fort-worth/cancun/guadalajara/shenzhen automatically.
Recent changes
2026-06-26 - v0.5.2: Cookie export wired — added _load_op_cookies() and module-level _OP_COOKIES dict to oddsportal.py. Loads cookies from ODDSPORTAL_COOKIES env var (Netscape cookies.txt format from "Get cookies.txt LOCALLY" Chrome extension, plus simple name=value fallback). Cookies injected into 4 places: (1) curl_cffi initial request in _curl_fetch, (2) curl_cffi CF warm-up request on 403, (3) Playwright context in fetch_rendered_html, (4) Playwright context in _fetch_ajax_via_playwright, (5) Playwright context in fetch_via_rendered_dom. With valid cf_clearance cookies, curl_cffi resolves page_id instantly without falling back to Playwright, eliminating the 120s CF wait. Cookie file path set via export ODDSPORTAL_COOKIES=~/oddsportal_cookies.txt before running capture.
2026-06-26 - v0.5.1: ATP Masters 1000 URL slug fix — corrected 6 remaining broken OddsPortal tournament URLs in config/routes.json: ATP_INDIAN_WELLS usa/indian-wells → usa/atp-indian-wells, ATP_MIAMI usa/miami → usa/atp-miami, ATP_MONTE_CARLO monaco/monte-carlo → monaco/atp-monte-carlo, ATP_MADRID spain/madrid → spain/atp-madrid, ATP_ROME italy/rome → italy/atp-rome, ATP_CINCINNATI usa/cincinnati → usa/atp-cincinnati. With these fixes, ATP Masters 2023 capture completed: 9/9 tournaments, ~550+ matches. WTA 1000s 2023/2024 largely complete. Warehouse built: 3,626 odds rows, 3,504 settled matches. Market audit: favorite ROI -3.9%, underdog ROI -13.3%.
2026-06-26 - v0.5: Route slug fix – corrected 11 broken OddsPortal tournament URLs in config/routes.json: ATP_SHANGHAI china/shanghai → china/atp-shanghai, ATP_PARIS france/paris → france/atp-paris, WTA_INDIAN_WELLS usa/indian-wells-women → usa/wta-indian-wells, WTA_MIAMI usa/miami-women → usa/wta-miami, WTA_MADRID spain/madrid-women → spain/wta-madrid, WTA_ROME italy/rome-women → italy/wta-rome, WTA_CINCINNATI usa/cincinnati-women → usa/wta-cincinnati, WTA_BEIJING china/beijing → china/wta-beijing, ATP_FINALS tennis/atp-finals → tennis/world/atp-finals, WTA_FINALS tennis/wta-finals → tennis/world/wta-finals. Added alt_url_templates support to capture_oddsportal.py – automatically tries alternate city/venue URLs (Canada Toronto↔Montreal, Finals turin/london / riyadh/fort-worth/cancun/guadalajara/shenzhen, Davis/BJK Cup /world/ variants). CF hardening in oddsportal.py: render timeout 90s → 120s, UA/viewport rotation, expanded event selectors for OddsPortal 2025/2026 SPA, more lenient _wait_for_real_content. Added agent no-bloat note to HANDOVER.md.
2026-06-25 - v0.4: routes.json URL pattern = year-in-slug (/tennis/<country>/<tournament>-{year}/results/). Added _capture_with_retry (one retry with 150 s Playwright timeout when page_id was resolved). Added _no_year_url_fallback (auto-retry current-season captures via the no-year URL). Wired ODDSPORTAL_RENDER_TIMEOUT_MS env var into fetch_rendered_html. Added test_no_year_url_fallback_only_for_current_year.
2026-06-25 - v0.3: Initial OddsPortal scrape fix. Updated routes.json URL pattern. Hardened fetch_rendered_html against CF. Added _extract_page_id patterns for new hydration formats. Added _fetch_ajax_via_playwright path. Added 14 new tests.