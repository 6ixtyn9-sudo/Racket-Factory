# Racket Factory

Odds-first tennis research lab.

Racket Factory does **not** start with tipsters. It starts with tennis market history and results, then audits whether any market segment has exploitable behavior. Prediction sources come later.

## Quickstart

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest -q
```

Normalize exported OddsPortal-like CSV:

```bash
PYTHONPATH=src python3 scripts/capture_oddsportal.py --input-csv data.csv
PYTHONPATH=src python3 scripts/build_warehouse.py
PYTHONPATH=src python3 scripts/audit_market.py
```

No certified betting edges are claimed.
