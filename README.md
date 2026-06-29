Odds-first tennis research lab.

Racket Factory starts with tennis market history and results, then audits whether any market segment has exploitable behaviour. Prediction sources are supporting signals, not automatic truth.

Current state

As of 2026-06-29, the daily pipeline has been tightened around three operational principles:

1. **Daily runs should not scrape OddsPortal by default.** OddsPortal remains the market-history/backfill source, but browser-backed bulk capture is now opt-in because of Cloudflare risk.
2. **Live pricing should come from The Odds API when available.** Prediction-site scraped prices are not trusted as the primary live EV price.
3. **Recent audit settlement should come from source-settled result rows.** ForeTennis and Forebet now emit warehouse-compatible settled rows where their pages expose actual results.

What works now

- Same-day live/upcoming rows are injected directly into `localdata/warehouse.csv.gz` during `build_warehouse.py`.
- Live injection pulls from:
  - PredixSport
  - BetClan
  - Forebet
- Broader live exploration is supported for:
  - ATP
  - WTA
  - CHALLENGER
  - ITF-M
  - ITF-W
  - UTR
- Live injection is no longer singles-only; doubles can enter the warehouse.
- The Odds API H2H prices are matched onto live rows and used for live EV pricing when present.
- The Odds API sport keys are configurable via `.env`; tennis uses tournament-specific keys such as:
  - `tennis_atp_wimbledon`
  - `tennis_wta_wimbledon`
- The Odds API supports multiple keys via:
  - `THE_ODDS_API_KEYS=key1,key2,key3`
  - plus legacy `THE_ODDS_API_KEY=key1`
- `scripts/daily.py` and `scripts/daily_pipeline.sh` load `.env` automatically without printing secrets.
- ForeTennis `actual_result` values are converted into settled result rows:
  - `localdata/foretennis_results_tennis_YYYY-MM.csv.gz`
- Forebet finished rows are parsed for actual set scores and converted into settled result rows:
  - `localdata/forebet_results_tennis_YYYY-MM.csv.gz`
- Audit can settle picks from these generated source-result files once the warehouse is rebuilt.

Important caveats

- OddsPortal capture is still valuable for historical market research, but daily browser scraping is opt-in:

```bash
RACKET_FACTORY_REFRESH_ODDSPORTAL=1 RACKET_FACTORY_ODDSPORTAL_DELAY=60 PYTHONPATH=src python3 scripts/daily.py
Without that env flag, daily runs use source/TennisData refreshes and skip heavy OddsPortal capture.
The Odds API has a quota; avoid repeated manual warehouse rebuilds unless needed.
ForeTennis result strings are set totals such as 20, 02, 21, 12; they can confirm match winner and sets won, but not set order.
Forebet result strings can include ordered set scores such as 7-6 3-6 1-6, so those can support per-set diagnostics.
Per-set diagnostics are not betting ROI unless matching set-market odds are also captured.
A result like zero actionable picks can be a legitimate model outcome on a sparse slate.
Quickstart
Bash

pip install -r requirements.txt
PYTHONPATH=src python3 -m pytest -q
Normalize exported OddsPortal-like CSV:

Bash

PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-csv data.csv
PYTHONPATH=src python3 scripts/build_warehouse.py
PYTHONPATH=src python3 scripts/audit_market.py
Run daily pipeline without heavy OddsPortal capture:

Bash

PYTHONPATH=src python3 scripts/daily.py
Run daily pipeline with explicit OddsPortal refresh:

Bash

RACKET_FACTORY_REFRESH_ODDSPORTAL=1 RACKET_FACTORY_ODDSPORTAL_DELAY=60 PYTHONPATH=src python3 scripts/daily.py
Useful live commands
Inspect upcoming/live prediction surfaces:

Bash

PYTHONPATH=src python3 scripts/predict_upcoming.py
Rebuild warehouse with same-day injected live rows:

Bash

PYTHONPATH=src python3 scripts/build_warehouse.py --data-dir localdata --output warehouse.csv.gz
Run edge mining on the rebuilt warehouse:

Bash

PYTHONPATH=src python3 scripts/mine_edges.py --warehouse localdata/warehouse.csv.gz --date 2026-06-29
Generate the human-readable report after mining:

Bash

PYTHONPATH=src python3 - <<'PY'
from scripts.daily import generate_daily_report
generate_daily_report("2026-06-29")
PY
Audit recent picks:

Bash

PYTHONPATH=src python3 scripts/audit_recent_picks.py --end 2026-06-29 --days 30 --warehouse localdata/warehouse.csv.gz
Quick check of injected same-day live rows:

Bash

PYTHONPATH=src python3 - <<'PY'
import pandas as pd

df = pd.read_csv("localdata/warehouse.csv.gz")
today = pd.Timestamp.today().date().isoformat()
x = df[(df["match_date"].astype(str) == today) & (df["_comment"].fillna("") == "live_upcoming_injected")].copy()
cols = [c for c in [
    "match_date", "player_a", "player_b", "tour", "_series", "_surface",
    "predicted_winner", "prediction_prob", "predicted_source", "odds_a", "odds_b", "_odds_source", "source", "_comment",
] if c in x.columns]
print("today injected rows:", len(x))
print(x[cols].to_string(index=False) if len(x) else "(none)")
PY
.env essentials
Example only — never commit real keys:

Bash

THE_ODDS_API_KEY=primary_key_here
THE_ODDS_API_KEYS=primary_key_here,backup_key_2,backup_key_3
THE_ODDS_API_SPORT_KEYS=tennis_atp_wimbledon,tennis_wta_wimbledon
THE_ODDS_API_SPORTS=tennis_atp_wimbledon,tennis_wta_wimbledon
THE_ODDS_API_REGIONS=uk,eu,us,au
RACKET_FACTORY_ODDSPORTAL_DELAY=60
The Odds API quota/cache controls
The daily pipeline may rebuild the warehouse more than once. To protect The Odds API quota, parsed H2H odds rows are cached by date/config in:

text

localdata/theoddsapi_odds_cache_YYYY-MM-DD.json
The cache stores parsed odds rows only. It never stores API keys.

Useful .env settings:

Bash

THE_ODDS_API_KEY=primary_key_here
THE_ODDS_API_KEYS=primary_key_here,backup_key_2,backup_key_3
THE_ODDS_API_SPORT_KEYS=tennis_atp_wimbledon,tennis_wta_wimbledon
THE_ODDS_API_SPORTS=tennis_atp_wimbledon,tennis_wta_wimbledon
THE_ODDS_API_REGIONS=uk,eu,us,au
THE_ODDS_API_CACHE_TTL_MINUTES=30
THE_ODDS_API_DISABLE_CACHE=0
RACKET_FACTORY_ODDSPORTAL_DELAY=60
Use THE_ODDS_API_DISABLE_CACHE=1 only when forcing a fresh API pull.

Audit set diagnostics
Forebet settled rows can include ordered set scores such as:

text

7-6 3-6 1-6
Audit reports can therefore show whether the selected player won any set or a specific set. ForeTennis settled rows provide set totals such as 20, 02, 21, or 12, so they support match/set-count diagnostics but not set order.

Set diagnostics are informational and are not betting ROI unless set-market odds are captured.

Notes
This is still an odds/results-first project. No certified betting edge is claimed without ROI and settlement. A couple of real-world winners on a given day are encouraging, but they are not validation by themselves; the repo still relies on historical slice quality, proper settlement, and walk-forward discipline.