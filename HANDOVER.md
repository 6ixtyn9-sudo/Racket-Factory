# Racket Factory — Handover

Date: 2026-06-24
Repo purpose: odds-first tennis research lab.

## Single source of truth

This file is the handover. Update it in place. Do not create drifting build reports.

## Executive summary

Racket Factory starts from market data, not prediction sources.

Goal:

1. Capture tennis match history and two-way odds, initially from OddsPortal rendered/history pages or exported HTML/CSV.
2. Normalize into CSV + DuckDB.
3. Audit market behavior by tour, tournament, odds band, favorite/underdog, and closing-price side.
4. Only after the market/results warehouse works, add prediction/consensus sources.

Current status: v0.2 MVP skeleton. No betting edges are certified. No picks are emitted.

## Golden rules

- Odds/results first. Prediction sources later.
- ROI is mandatory before any betting claim.
- No "sure bet" language.
- Use walk-forward validation before promoting any rule.
- Do not mix tours blindly: ATP, WTA, Challenger, ITF, doubles, and exhibitions must be segmented.
- Retirements, walkovers, and abandoned matches must be handled explicitly before certification.
- OddsPortal rendered odds are market/display odds, not guaranteed executable single-book close.
- CSV + DuckDB is the analytics engine.

## MVP architecture

```text
Racket-Factory/
  config/routes.json
  src/racketfactory/
    config.py         # route config loading
    entities.py       # player/tour normalization
    oddsportal.py     # OddsPortal HTML/CSV normalization helpers
    warehouse.py      # CSV -> DuckDB
    assay.py          # ROI and market summaries
  scripts/
    capture_oddsportal.py  # normalize exported CSV, saved HTML, or Playwright URL
    build_warehouse.py
    audit_market.py
  tests/
```

## Data contract

Normalized match/odds rows live in `localdata/oddsportal_tennis_YYYY-MM.csv.gz` with columns:

```text
match_date,tour,tournament,round,player_a,player_b,winner,score,odds_a,odds_b,bookmaker,source,captured_at,oddsportal_url
```

Semantics:

- `player_a` and `player_b` are normalized display names.
- `winner` must equal `player_a` or `player_b`, or be empty for unplayed/unsettled rows.
- `odds_a` and `odds_b` are decimal odds for the corresponding players.
- `tour` should be a strict segment such as ATP, WTA, CHALLENGER, ITF, UNKNOWN.

## First research questions

Before adding any prediction source, answer:

- What is blind favorite ROI by odds band?
- What is blind underdog ROI by odds band?
- Does ATP differ from WTA / Challenger / ITF?
- Which odds bands are systematically negative?
- Are longshot underdogs or mid-priced favorites less overtaxed?

## How to run

Install:

```bash
pip install -r requirements.txt
python3 -m playwright install chromium   # only needed for --url capture
```

Normalize an exported CSV:

```bash
PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-csv path/to/export.csv
```

Normalize a saved/rendered OddsPortal HTML file:

```bash
PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-html path/to/page.html --date 2026-06-24 --tour ATP --tournament "Example Open"
```

Render and normalize a URL:

```bash
PYTHONPATH=src python3 scripts/capture_oddsportal.py --url "https://www.oddsportal.com/tennis/..." --tour ATP --save-html localdata/sample.html
```

Build warehouse:

```bash
PYTHONPATH=src python3 scripts/build_warehouse.py
```

Audit market:

```bash
PYTHONPATH=src python3 scripts/audit_market.py
```

Tests:

```bash
PYTHONPATH=src pytest -q
python3 -m py_compile src/racketfactory/*.py scripts/*.py
```

## Next build steps

1. Verify OddsPortal tennis route parsing from real saved HTML.
2. Add route config for one target segment only, likely ATP or WTA first.
3. Add paginated rendered capture after parser is proven on saved HTML fixtures.
4. Add market audit split by year/month/tour/odds band.
5. Only then add prediction-source candidates.
