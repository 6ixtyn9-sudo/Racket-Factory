Racket Factory — Handover
Date: 2026-06-24
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
Current status: v0.2 MVP skeleton. No betting edges are certified. No picks are emitted.

Golden rules
Odds/results first. Prediction sources later.
ROI is mandatory before any betting claim.
No "sure bet" language.
Use walk-forward validation before promoting any rule.
Do not mix tours blindly: ATP, WTA, Challenger, ITF, doubles, and exhibitions must be segmented.
Retirements, walkovers, and abandoned matches must be handled explicitly before certification.
OddsPortal rendered odds are market/display odds, not guaranteed executable single-book close.
CSV + DuckDB is the analytics engine.
MVP architecture
text

Racket-Factory/
config/routes.json
src/racketfactory/
config.py # route config loading
entities.py # player/tour normalization
oddsportal.py # OddsPortal HTML/CSV normalization helpers
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
Routes are defined in config/routes.json. Each route has a url_template (with {year} placeholder), configured years, and a default page count.

Bash

Capture everything
PYTHONPATH=src python3 scripts/bulk_capture.py --all

Capture specific routes
PYTHONPATH=src python3 scripts/bulk_capture.py --route ATP_WIMBLEDON WTA_WIMBLEDON --year 2024 2025 2026

Override pages and add delay
PYTHONPATH=src python3 scripts/bulk_capture.py --route ATP_SINGLES --year 2026 --pages 20 --delay 8

Dry run to see what would be captured
PYTHONPATH=src python3 scripts/bulk_capture.py --all --dry-run

Skip routes that already have CSV files
PYTHONPATH=src python3 scripts/bulk_capture.py --all --skip-exists
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
Next build steps
Run bulk capture for all routes and years.
Verify data quality: row counts, date ranges, tour distribution.
Add market audit split by year/month/tour/odds band.
Only then add prediction-source candidates.