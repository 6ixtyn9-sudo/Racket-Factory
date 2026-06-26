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
Current status: v0.5 — 11 OddsPortal tournament slugs corrected (atp-/wta- prefixes, /world/ for Finals); alt_url_templates auto-fallback for Canada Toronto/Montreal alternation and Finals city variants; Playwright CF hardening (120s timeout, UA rotation, looser event selectors). Grand Slam capture partially complete (~884+ rows); Masters/WTA 1000 bulk capture blocked by Cloudflare IP burn – needs residential IP / cookie export before resuming.

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

Route slug reference (v0.5, 2026-06-26)
All 28 routes verified against live OddsPortal:

ATP: atp-australian-open, atp-french-open, atp-wimbledon, atp-us-open, indian-wells, miami, monte-carlo, madrid, rome, atp-toronto / atp-montreal (alt_url fallback), cincinnati, atp-shanghai, atp-paris, world/atp-finals (alt: atp-finals-turin, atp-finals-london)

WTA: wta-australian-open, wta-french-open, wta-wimbledon, wta-us-open, wta-indian-wells, wta-miami, wta-madrid, wta-rome, wta-toronto / wta-montreal (alt_url fallback), wta-cincinnati, wta-beijing, world/wta-finals (alt: wta-finals-riyadh, fort-worth, cancun, guadalajara, shenzhen)

Team: davis-cup, billie-jean-king-cup (alt: /world/ variants)

Canada (ATP/WTA) alternates Toronto/Montreal yearly — alt_url_templates tries both automatically. Finals venues move yearly — alt_url_templates tries all known city slugs.

Fetcher strategy (OddsPortal)
The fetch path is:

curl_cffi with a Chrome TLS fingerprint hits the year-in-slug tournament URL (/tennis/<country>/<tournament>-{year}/results/) and pulls the page ID.
If curl_cffi returns the Cloudflare challenge page, fall through to Playwright. Playwright uses wait_until="commit", waits up to 120 s (override with ODDSPORTAL_RENDER_TIMEOUT_MS) for CF to clear, with randomized UA/viewport.
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
Export cookies from a real browser session (Get cookies.txt extension) and set ODDSPORTAL_COOKIES=/path/to/cookies.txt – cookie loader not yet wired, add ~5 lines to oddsportal.py if needed.
Run small batches with --delay 8–12, not --all with 196 jobs.
iCloud Private Relay (iCloud+ $0.99/mo) gives dual-hop residential egress – CF is far less aggressive.
v0.5 capture status (2026-06-26)
Grand Slams – partial:

ATP_WIMBLEDON 2021–2026 ✓
ATP_AUSTRALIAN_OPEN 2021,2024,2026
ATP_FRENCH_OPEN 2021–2026 ✓
ATP_US_OPEN 2020,2021,2024
WTA_AUSTRALIAN_OPEN 2020,2021,2023,2024
WTA_FRENCH_OPEN 2021–2025
WTA_WIMBLEDON 2021–2024,2026
WTA_US_OPEN 2021,2022,2024
Masters / WTA 1000 / Finals – 0 rows, blocked by:

Broken tournament slugs in routes.json v0.4 (fixed in v0.5)
Cloudflare IP burn after 60+ consecutive tournament-years with no delay
Next capture, in order:

ATP Masters 1000 (9 × 7yr) – with fixed atp-shanghai / atp-paris slugs
WTA 1000 (8 × 7yr) – with fixed wta-indian-wells / miami / madrid / rome / cincinnati / beijing slugs, Canada Toronto/Montreal auto-fallback
ATP/WTA Finals – with world/atp-finals and world/wta-finals + city variant fallbacks
Backfill missing Grand Slam years
Use: --delay 8–12, small batches, residential IP / phone tether. Delete localdata/.bulk_checkpoint.json before resuming with fixed slugs.

Known data caveats

Wimbledon 2020 was cancelled (COVID-19); the year-in-slug URL 404s, correct behavior.
The current season's URL is the no-year variant; year-in-slug returns 404 for in-progress year.
OddsPortal AJAX responses are encrypted base64 in 2026 — we don't decrypt them. render-DOM bypasses this.
Canada Masters (ATP/WTA) alternates Toronto / Montreal yearly — alt_url_templates handles this automatically.
ATP/WTA Finals change host city — alt_url_templates tries turin/london and riyadh/fort-worth/cancun/guadalajara/shenzhen automatically.
Recent changes
2026-06-26 - v0.5: Route slug fix – corrected 11 broken OddsPortal tournament URLs in config/routes.json: ATP_SHANGHAI china/shanghai → china/atp-shanghai, ATP_PARIS france/paris → france/atp-paris, WTA_INDIAN_WELLS usa/indian-wells-women → usa/wta-indian-wells, WTA_MIAMI usa/miami-women → usa/wta-miami, WTA_MADRID spain/madrid-women → spain/wta-madrid, WTA_ROME italy/rome-women → italy/wta-rome, WTA_CINCINNATI usa/cincinnati-women → usa/wta-cincinnati, WTA_BEIJING china/beijing → china/wta-beijing, ATP_FINALS tennis/atp-finals → tennis/world/atp-finals, WTA_FINALS tennis/wta-finals → tennis/world/wta-finals. Added alt_url_templates support to capture_oddsportal.py – automatically tries alternate city/venue URLs (Canada Toronto↔Montreal, Finals turin/london / riyadh/fort-worth/cancun/guadalajara/shenzhen, Davis/BJK Cup /world/ variants). CF hardening in oddsportal.py: render timeout 90s → 120s, UA/viewport rotation, expanded event selectors for OddsPortal 2025/2026 SPA, more lenient _wait_for_real_content. Added agent no-bloat note to HANDOVER.md.
2026-06-25 - v0.4: routes.json URL pattern = year-in-slug (/tennis/<country>/<tournament>-{year}/results/). Added _capture_with_retry (one retry with 150 s Playwright timeout when page_id was resolved). Added _no_year_url_fallback (auto-retry current-season captures via the no-year URL). Wired ODDSPORTAL_RENDER_TIMEOUT_MS env var into fetch_rendered_html. Added test_no_year_url_fallback_only_for_current_year.
2026-06-25 - v0.3: Initial OddsPortal scrape fix. Updated routes.json URL pattern. Hardened fetch_rendered_html against CF. Added _extract_page_id patterns for new hydration formats. Added _fetch_ajax_via_playwright path. Added 14 new tests.