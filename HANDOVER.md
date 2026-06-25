Racket Factory — Handover
Date: 2026-06-25
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
Current status: v0.4 — year-in-slug URL pattern working; current-season fallback + retry-on-zero added for Cloudflare flakiness; 884+ rows of real historical data captured.

Golden rules
Odds/results first. Prediction sources later.
ROI is mandatory before any betting claim.
No "sure bet" language.
Use walk-forward validation before promoting any rule.
Do not mix tours blindly: ATP, WTA, Challenger, ITF, doubles, and exhibitions must be segmented.
Retirements, walkovers, and abandoned matches must be handled explicitly before certification.
OddsPortal rendered odds are market/display odds, not guaranteed executable single-book close.
CSV + DuckDB is the analytics engine.

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
PYTHONPATH=src python3 scripts/capture_oddsportal.py --route ATP_SINGLES --year 2026 --pages 20 --delay 8

Dry run to see what would be captured
PYTHONPATH=src python3 scripts/capture_oddsportal.py --all --dry-run

Skip routes that already have CSV files
PYTHONPATH=src python3 scripts/capture_oddsportal.py --all --skip-exists

Force Playwright only (skip curl_cffi even on residential IP)
ODDSPORTAL_USE_PLAYWRIGHT=1 PYTHONPATH=src python3 scripts/capture_oddsportal.py --route ATP_WIMBLEDON --year 2026
Progress is checkpointed in localdata/.bulk_checkpoint.json so interrupted runs resume safely. Deduplication happens at write time — the same match from overlapping routes won't be counted twice.

After capture, always rebuild warehouse and audit:

Bash

PYTHONPATH=src python3 scripts/build_warehouse.py
PYTHONPATH=src python3 scripts/audit_market.py

Route categories
Category Routes Notes
TOUR_AGGREGATE ATP_SINGLES, WTA_SINGLES, ATP_CHALLENGER, ITF_MEN, ITF_WOMEN Bulk captures of entire tour seasons. Highest volume, most data.
GRAND_SLAM ATP/WTA Australian/French/Wimbledon/US Open 8 routes covering all majors. ~3-5 pages each.
MASTERS_1000 ATP Indian Wells through Paris 9 ATP 1000-level tournaments. ~3 pages each.
WTA_1000 WTA Indian Wells through Beijing 8 WTA 1000-level tournaments. ~3 pages each.
YEAR_END ATP_FINALS, WTA_FINALS Year-end championships. ~2 pages each.
TEAM_EVENT DAVIS_CUP, BJK_CUP Team competitions. Segmented from individual tours.

Fetcher strategy (OddsPortal)
The fetch path is:

curl_cffi with a Chrome TLS fingerprint impersonation (chrome131 by default; override with ODDSPORTAL_IMPERSONATE env var) hits the year-in-slug tournament URL (/tennis/<country>/<tournament>-{year}/results/) and pulls the page ID.
If curl_cffi returns the Cloudflare challenge page instead of content, the resolver falls through to Playwright. Playwright uses wait_until="commit" (not "networkidle", which is too strict for CF), waits up to 90 s (override with ODDSPORTAL_RENDER_TIMEOUT_MS) for the page to clear the Cloudflare challenge, then extracts the page ID from the rendered DOM.
With the page ID, the AJAX archive endpoint is hit. Note: this endpoint returns base64-encrypted payloads (cipher changed in 2026) so it usually returns 0 rows.
If both curl_cffi and Playwright AJAX fail, the script falls back to the render-DOM path: parses .eventRow elements from the live page and clicks "Next" pagination. This is the most reliable path on residential IPs.
If the render-DOM path also returns 0 rows AND a page_id was resolved, retry once with a 150 s Playwright timeout (covers slow CF clears).
If the year-in-slug URL returns 0 rows AND the year equals the current year, fall back to the no-year URL pattern (current season doesn't use year-in-slug).
Only after all paths fail does the script log a warning and return 0 rows.

Cloudflare notes
Residential IPs usually clear CF automatically. Datacenter / VPN / cloud IPs may still get challenged. If you see:

WARNING oddsportal: CF challenge page returned, falling back to Playwright -> your curl_cffi cookies were not yet cleared.
INFO waiting up to 90 s for CF challenge to clear -> Playwright is waiting; if it times out, your IP is likely blocked.
INFO First Playwright attempt returned 0 rows with page_id=X; retrying -> transient failure, the retry should usually succeed.
INFO Year-in-slug URL returned 0 rows; trying current-season no-year URL -> current-season fallback engaged.
ERROR All capture paths returned 0 rows -> persistent block. Consider running with a residential proxy or manually exporting cookies from a real browser session.

Known data caveats

Wimbledon 2020 was cancelled (COVID-19); the year-in-slug URL 404s, which is correct behavior.
The current season's URL is the no-year variant; year-in-slug returns 404 for the in-progress year.
OddsPortal AJAX responses are encrypted base64 in 2026 — we don't decrypt them. The render-DOM path bypasses this.
Recent changes
2026-06-25 - v0.4: routes.json URL pattern = year-in-slug (/tennis/<country>/<tournament>-{year}/results/). Added _capture_with_retry (one retry with 150 s Playwright timeout when page_id was resolved). Added _no_year_url_fallback (auto-retry current-season captures via the no-year URL). Wired ODDSPORTAL_RENDER_TIMEOUT_MS env var into fetch_rendered_html. Added test_no_year_url_fallback_only_for_current_year.
2026-06-25 - v0.3: Initial OddsPortal scrape fix. Updated routes.json URL pattern. Hardened fetch_rendered_html against CF. Added _extract_page_id patterns for new hydration formats. Added _fetch_ajax_via_playwright path. Added 14 new tests.