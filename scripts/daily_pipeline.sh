#!/usr/bin/env bash
# =============================================================================
# Racket Factory Daily Pipeline
# Runs every morning at 06:00 via systemd timer (racketfactory-daily.timer).
# Non-fatal per step — a single source failure does not abort the run.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
LOCALDATA="$ROOT/localdata"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
YEAR=$(date '+%Y')

log() { echo "$LOG_PREFIX $*"; }
step() { log "[$1] $2"; }

log "=== RACKET FACTORY DAILY PIPELINE START ==="

# ---------------------------------------------------------------------------
# [1/5] OddsPortal capture — current year
# ---------------------------------------------------------------------------
step "1/5" "Capturing OddsPortal data for current year..."
if PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/capture_oddsportal.py" \
        --all --year "$YEAR" --skip-exists --delay 8 2>&1; then
    log "[1/5] OddsPortal capture complete."
else
    log "[1/5] WARNING: OddsPortal capture failed or returned 0 rows. Continuing."
fi

# ---------------------------------------------------------------------------
# [2/5] tennis-data.co.uk — current year metadata
# ---------------------------------------------------------------------------
step "2/5" "Downloading tennis-data.co.uk for $YEAR..."
if PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/backfill_tennisdata.py" \
        --year "$YEAR" 2>&1; then
    log "[2/5] Tennis-data download complete."
else
    log "[2/5] WARNING: Tennis-data download failed. Continuing."
fi

# ---------------------------------------------------------------------------
# [3/5] Forebet predictions — yesterday's page (predictions + results)
# ---------------------------------------------------------------------------
step "3/5" "Fetching Forebet predictions (daily mode)..."
if PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/backfill_forebet.py" \
        --mode daily \
        --days yesterday \
        --warehouse "$LOCALDATA/warehouse.csv.gz" \
        --output-dir "$LOCALDATA" 2>&1; then
    log "[3/5] Forebet daily fetch complete."
else
    log "[3/5] WARNING: Forebet daily fetch failed. Check chrome133a fingerprint."
fi

# ---------------------------------------------------------------------------
# [4/5] ForeTennis predictions — lastpredictions page
# ---------------------------------------------------------------------------
step "4/5" "Fetching ForeTennis predictions (lastpredictions)..."
if PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/backfill_foretennis.py" \
        --warehouse "$LOCALDATA/warehouse.csv.gz" \
        --output-dir "$LOCALDATA" 2>&1; then
    log "[4/5] ForeTennis fetch complete."
else
    log "[4/5] WARNING: ForeTennis fetch failed. Continuing without cross-source data."
fi

# ---------------------------------------------------------------------------
# [5/7] PredixSport predictions
# ---------------------------------------------------------------------------
step "5/7" "Fetching PredixSport predictions..."
PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/capture_predixsport.py" \
        --warehouse "$LOCALDATA/warehouse.csv.gz" \
        --output-dir "$LOCALDATA" 2>&1 || true

if [ ! -f "$LOCALDATA/predictions_predixsport_daily.csv.gz" ] || [ $(gunzip -c "$LOCALDATA/predictions_predixsport_daily.csv.gz" 2>/dev/null | wc -l) -lt 2 ]; then
    log "[5/7] CRITICAL: PredixSport capture failed or returned < 2 rows. Halting pipeline to prevent data starvation."
    exit 1
fi
log "[5/7] PredixSport fetch complete."

# ---------------------------------------------------------------------------
# [6/7] BetClan predictions
# ---------------------------------------------------------------------------
step "6/7" "Fetching BetClan predictions..."
PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/capture_betclan.py" \
        --warehouse "$LOCALDATA/warehouse.csv.gz" \
        --output-dir "$LOCALDATA" 2>&1 || true

if [ ! -f "$LOCALDATA/predictions_betclan_daily.csv.gz" ] || [ $(gunzip -c "$LOCALDATA/predictions_betclan_daily.csv.gz" 2>/dev/null | wc -l) -lt 2 ]; then
    log "[6/7] CRITICAL: BetClan capture failed or returned < 2 rows. Halting pipeline to prevent data starvation."
    exit 1
fi
log "[6/7] BetClan fetch complete."

# ---------------------------------------------------------------------------
# [7/7] Rebuild warehouse and mine edges
# ---------------------------------------------------------------------------
step "7/7" "Rebuilding warehouse and mining edges..."
PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/build_warehouse.py" \
    --data-dir "$LOCALDATA" \
    --output warehouse.csv.gz 2>&1
log "[7/7] Warehouse rebuild complete."

PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/mine_edges.py" \
    --warehouse "$LOCALDATA/warehouse.csv.gz" 2>&1 \
    | tee "$LOCALDATA/edges_$(date '+%Y-%m-%d').txt"
log "[7/7] Edge mining complete. Results in $LOCALDATA/edges_$(date '+%Y-%m-%d').txt"

log "=== RACKET FACTORY DAILY PIPELINE COMPLETE ==="
