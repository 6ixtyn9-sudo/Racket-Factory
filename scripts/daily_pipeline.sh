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
ODDSPORTAL_DELAY="${RACKET_FACTORY_ODDSPORTAL_DELAY:-30}"

# Load repo-local secrets for non-interactive systemd/cron runs. Values are not printed.
# Python entry points also load .env themselves; this keeps the legacy shell
# runner compatible with tools that only read process environment.
if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
fi
if [[ -f "$LOCALDATA/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$LOCALDATA/.env"
    set +a
fi

log() { echo "$LOG_PREFIX $*"; }
step() { log "[$1] $2"; }

log "=== RACKET FACTORY DAILY PIPELINE START ==="

# ---------------------------------------------------------------------------
# [1/5] OddsPortal capture — current year
# ---------------------------------------------------------------------------
step "1/5" "Capturing OddsPortal data for current year..."
if PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/capture_oddsportal.py" \
        --all --years "$YEAR" --no-checkpoint --delay "$ODDSPORTAL_DELAY" 2>&1; then
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
        --output-dir "$LOCALDATA" 2>&1 || true
log "[5/7] PredixSport fetch complete."

# ---------------------------------------------------------------------------
# [6/7] BetClan predictions
# ---------------------------------------------------------------------------
step "6/7" "Fetching BetClan predictions..."
PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/capture_betclan.py" \
        --output-dir "$LOCALDATA" 2>&1 || true
log "[6/7] BetClan fetch complete."

# ---------------------------------------------------------------------------
# [7/7] Rebuild warehouse and mine edges
# ---------------------------------------------------------------------------
step "7/7" "Rebuilding warehouse and mining edges..."
PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/build_warehouse.py" \
    --data-dir "$LOCALDATA" \
    --output warehouse.csv.gz 2>&1
log "[7/7] Base warehouse build complete."

PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/resolve_pending.py" \
    --warehouse "$LOCALDATA/warehouse.csv.gz" \
    --data-dir "$LOCALDATA" 2>&1
log "[7/7] Resolved pending AI predictions against warehouse."

PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/build_warehouse.py" \
    --data-dir "$LOCALDATA" \
    --output warehouse.csv.gz 2>&1
log "[7/7] Final warehouse rebuild complete."

PYTHONPATH="$ROOT/src" python3 "$SCRIPT_DIR/mine_edges.py" \
    --warehouse "$LOCALDATA/warehouse.csv.gz" 2>&1 \
    | tee "$LOCALDATA/edges_$(date '+%Y-%m-%d').txt"
log "[7/7] Edge mining complete. Results in $LOCALDATA/edges_$(date '+%Y-%m-%d').txt"

log "=== RACKET FACTORY DAILY PIPELINE COMPLETE ==="