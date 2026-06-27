#!/bin/bash
set -e

for year in {2012..2026}; do
    for tour in atp wta; do
        echo "Running backfill for $tour $year..."
        PYTHONPATH=src python3 scripts/backfill_foretennis.py --mode historical --tour $tour --year $year --output-dir localdata || true
    done
done
echo "Historical backfill complete!"
