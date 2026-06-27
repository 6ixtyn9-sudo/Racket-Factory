Racket Factory
Odds-first tennis research lab.

Racket Factory does not start with tipsters. It starts with tennis market history and results, then audits whether any market segment has exploitable behavior. Prediction sources come later.

Current state
As of 2026-06-27, the repo now supports a fuller same-day live exploration path without adding extra helper scripts or one-off tooling:

same-day live/upcoming rows are injected directly into the warehouse during build_warehouse.py
live injection now pulls from:
PredixSport
BetClan
Forebet
Forebet adapter parsing was fixed to stop leaking football/soccer rows into tennis
broader tour exploration is now supported in the live path for:
ATP
WTA
CHALLENGER
ITF-M
ITF-W
UTR
live injection is no longer singles-only; doubles can also enter the warehouse
scripts/predict_upcoming.py was upgraded into a lean live-card diagnostic that merges cross-source rows more cleanly, including abbreviated vs full-name variants
Important: this does not mean the system will always emit picks. A result like 0 actionable picks can now be a legitimate model outcome on a sparse slate rather than a broken pipeline.

Quickstart
Bash

pip install -r requirements.txt
PYTHONPATH=src pytest -q
Normalize exported OddsPortal-like CSV:

Bash

PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-csv data.csv
PYTHONPATH=src python3 scripts/build_warehouse.py
PYTHONPATH=src python3 scripts/audit_market.py
Useful live commands
Inspect upcoming/live prediction surfaces:

Bash

PYTHONPATH=src python3 scripts/predict_upcoming.py

Rebuild warehouse with same-day injected live rows:

Bash

PYTHONPATH=src python3 scripts/build_warehouse.py
Run edge mining on the rebuilt warehouse:

Bash

PYTHONPATH=src python3 scripts/mine_edges.py
Quick check of injected same-day live rows:

Bash

PYTHONPATH=src python3 - <<'PY'
import pandas as pd

df = pd.read_csv("localdata/warehouse.csv.gz", low_memory=False)
today = pd.Timestamp.today().date().isoformat()
x = df[(df["match_date"].astype(str) == today) & (df["_comment"].fillna("") == "live_upcoming_injected")].copy()
cols = [c for c in [
    "match_date", "player_a", "player_b", "tour", "_series", "_surface",
    "predicted_winner", "prediction_prob", "predicted_source", "source", "_comment",
] if c in x.columns]
print("today injected rows:", len(x))
print(x[cols].to_string(index=False) if len(x) else "(none)")
PY
Notes
This is still an odds/results-first project.
Prediction sources are support signals, not automatic truth.
No certified betting edges are claimed.
A couple of real-world winners on a given day are encouraging, but they are not validation by themselves; the repo still relies on historical slice quality and walk-forward discipline.