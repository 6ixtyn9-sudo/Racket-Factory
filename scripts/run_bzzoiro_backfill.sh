#!/bin/bash
set -e

for year in {2020..2026}; do
    echo "Running Bzzoiro backfill for ${year}..."
    PYTHONPATH=src python3 scripts/backfill_bzzoiro.py --start-date "${year}-01-01" --end-date "${year}-12-31" --output-dir localdata || true
done
echo "Bzzoiro backfill complete!"
