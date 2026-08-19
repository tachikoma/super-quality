#!/usr/bin/env bash
set -euo pipefail

# Data refresh automation for K200MQ backtesting system.
#
# Usage:
#   ./scripts/refresh_data.sh                  # refresh all
#   ./scripts/refresh_data.sh --price-only     # price data only
#   ./scripts/refresh_data.sh --universe-only  # universe snapshots only
#   ./scripts/refresh_data.sh --dart-only      # DART filings only (requires DART_API_KEY)
#
# Schedule recommendations:
#   Price data:    daily (trading day close)
#   Universe:      monthly (1st trading day of month)
#   DART filings:  quarterly (after DART filing deadline for each quarter)
#
# Environment variables:
#   DART_API_KEY        Required for --dart-only. OpenDART API key.
#   PRICE_START_YEAR    Override price data start year (default: 2014)
#   PRICE_END_YEAR      Override price data end year (default: current year)
#   UNIVERSE_SOURCE     "krx" for live KRX fetch, "local" for existing bundle (default: krx)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PRICE_START_YEAR="${PRICE_START_YEAR:-2014}"
PRICE_END_YEAR="${PRICE_END_YEAR:-$(date +%Y)}"
UNIVERSE_SOURCE="${UNIVERSE_SOURCE:-krx}"
PRICE_CACHE_DIR="data/raw"
UNIVERSE_BUNDLE_DIR="data/universe/kospi200_bundle_pit"

# Parse flags
PRICE_ONLY=false
UNIVERSE_ONLY=false
DART_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --price-only)    PRICE_ONLY=true ;;
    --universe-only) UNIVERSE_ONLY=true ;;
    --dart-only)     DART_ONLY=true ;;
    --help|-h)
      echo "Usage: $0 [--price-only|--universe-only|--dart-only]"
      echo ""
      echo "Refresh backtesting data: price history, universe snapshots, DART filings."
      echo ""
      echo "Flags:"
      echo "  --price-only     Refresh price data only"
      echo "  --universe-only  Refresh universe snapshots only"
      echo "  --dart-only      Refresh DART filings only (requires DART_API_KEY)"
      echo "  --help           Show this help"
      echo ""
      echo "Environment:"
      echo "  DART_API_KEY       Required for --dart-only"
      echo "  PRICE_START_YEAR   Price data start year (default: 2014)"
      echo "  PRICE_END_YEAR     Price data end year (default: current year)"
      echo "  UNIVERSE_SOURCE    'krx' or 'local' (default: krx)"
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $arg"
      echo "        Use --help for usage."
      exit 1
      ;;
  esac
done

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="outputs/refresh_logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/refresh_${TIMESTAMP}.log"

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$msg" | tee -a "$LOG_FILE"
}

# ─── Step 1: Price data refresh ────────────────────────────────────────

refresh_price() {
  log "=== Step 1: Price data refresh (${PRICE_START_YEAR}-${PRICE_END_YEAR}) ==="

  # Check if pykrx is available
  if ! uv run python -c "import pykrx" 2>/dev/null; then
    log "[ERROR] pykrx not installed. Run: uv sync"
    return 1
  fi

  # Trigger price cache download by calling the loader with a minimal request.
  # The loader uses pykrx and caches to price_YYYY.parquet files.
  # We request a small universe for today to trigger the cache write.
  log "Refreshing price cache for years ${PRICE_START_YEAR}-${PRICE_END_YEAR}..."

  uv run python -c "
import sys
from pathlib import Path
from datetime import date

# Add src to path
sys.path.insert(0, str(Path('src')))

from k200_mq.core.data.loader import get_price_data

# Use a known liquid ticker to trigger cache download
# KOSPI 200 index top constituents
test_tickers = ['005930', '000660', '035420', '051910', '028260']
start = f'${PRICE_START_YEAR}-01-01'
end = date.today().isoformat()

try:
    df = get_price_data(test_tickers, start, end)
    if df.empty:
        print('[WARN] Price data returned empty — check network/pykrx')
    else:
        print(f'[OK] Price cache refreshed: {len(df)} rows, {df.index.get_level_values(0).nunique()} tickers')
        # Show which year files exist
        import glob
        files = sorted(glob.glob('data/raw/price_*.parquet'))
        for f in files:
            print(f'  {f}')
except Exception as e:
    print(f'[ERROR] Price refresh failed: {e}')
    sys.exit(1)
" 2>&1 | tee -a "$LOG_FILE"

  log "Price data refresh complete."
}

# ─── Step 2: Universe snapshot refresh ─────────────────────────────────

refresh_universe() {
  log "=== Step 2: Universe snapshot refresh (source=${UNIVERSE_SOURCE}) ==="

  if [[ "$UNIVERSE_SOURCE" == "krx" ]]; then
    # Check for KRX credentials
    if [[ -z "${KRX_ID:-}" || -z "${KRX_PW:-}" ]]; then
      log "[WARN] KRX_ID/KRX_PW not set. Skipping live KRX fetch."
      log "        Set KRX_ID and KRX_PW in .env for live universe refresh."
      log "        Falling back to local bundle validation."
      UNIVERSE_SOURCE="local"
    fi
  fi

  if [[ "$UNIVERSE_SOURCE" == "krx" ]]; then
    log "Fetching latest KOSPI 200 constituents from KRX..."

    # Fetch current month's snapshot
    uv run python scripts/fetch_kospi200_pit_snapshots.py \
      --start-date "$(date +%Y-%m-01)" \
      --end-date "$(date +%Y-%m-%d)" \
      --output-dir data/universe/kospi200_bundle_pit_src \
      2>&1 | tee -a "$LOG_FILE"

    # Rebuild bundle
    if [[ -d "$UNIVERSE_BUNDLE_DIR" ]]; then
      log "Rebuilding universe bundle..."
      uv run python scripts/build_local_pit_universe_bundle.py \
        --source-dir data/universe/kospi200_bundle_pit_src \
        --output-dir "$UNIVERSE_BUNDLE_DIR" \
        --source-is-krx \
        2>&1 | tee -a "$LOG_FILE"
    fi
  else
    log "Validating existing universe bundle..."
    if [[ -f "$UNIVERSE_BUNDLE_DIR/bundle.manifest.json" ]]; then
      uv run python -c "
from pathlib import Path
import json

manifest = Path('$UNIVERSE_BUNDLE_DIR/bundle.manifest.json')
data = json.loads(manifest.read_text())
exceptions = data.get('transition_exceptions_by_as_of', {})
print(f'Bundle manifest found: {len(exceptions)} as-of dates')
print(f'Allowed sizes: {set(s for e in exceptions.values() for s in e.get(\"allowed_sizes\", []))}')
" 2>&1 | tee -a "$LOG_FILE"
    else
      log "[WARN] No universe bundle found at $UNIVERSE_BUNDLE_DIR"
    fi
  fi

  log "Universe snapshot refresh complete."
}

# ─── Step 3: DART filing refresh ──────────────────────────────────────

refresh_dart() {
  log "=== Step 3: DART filing refresh ==="

  if [[ -z "${DART_API_KEY:-}" ]]; then
    log "[WARN] DART_API_KEY not set. Skipping DART refresh."
    log "        export DART_API_KEY='<your_key>' for DART filing updates."
    return 0
  fi

  # Determine the latest quarter to fetch
  CURRENT_YEAR=$(date +%Y)
  CURRENT_MONTH=$(date +%m)

  if [[ $CURRENT_MONTH -le 3 ]]; then
    # Q4 filings (previous year) due by end of March
    TARGET_YEAR=$((CURRENT_YEAR - 1))
    TARGET_Q="11014"  # Annual report
  elif [[ $CURRENT_MONTH -le 5 ]]; then
    # Q1 filings due by end of May
    TARGET_YEAR=$((CURRENT_YEAR - 1))
    TARGET_Q="11013"  # Q1 quarterly
  elif [[ $CURRENT_MONTH -le 8 ]]; then
    # Q2 filings (half-year) due by end of August
    TARGET_YEAR=$((CURRENT_YEAR - 1))
    TARGET_Q="11012"  # Q2 half-year
  else
    # Q3 filings due by end of November
    TARGET_YEAR=$((CURRENT_YEAR - 1))
    TARGET_Q="11011"  # Q3 quarterly
  fi

  log "Target: year=${TARGET_YEAR}, report_code=${TARGET_Q}"

  # Use existing batch spec generator for the target period
  DART_BATCH_DIR="data/raw/dart_batch_refresh_${TIMESTAMP}"
  mkdir -p "$DART_BATCH_DIR"

  # Check if there are existing tickers to refresh
  if [[ -f "data/universe/kospi200_bundle_pit/bundle.manifest.json" ]]; then
    log "Generating batch spec for current universe tickers..."

    # Extract unique tickers from the bundle
    uv run python -c "
import json, csv
from pathlib import Path

# Load bundle manifest to get tickers
manifest = Path('data/universe/kospi200_bundle_pit/bundle.manifest.json')
data = json.loads(manifest.read_text())

# Get the latest as-of date's tickers
latest_files = sorted(Path('data/universe/kospi200_bundle_pit').glob('kospi200_*.csv'))
if not latest_files:
    print('[ERROR] No universe CSV files found')
    exit(1)

latest = latest_files[-1]
tickers = []
with open(latest) as f:
    reader = csv.DictReader(f)
    for row in reader:
        tickers.append(row.get('ticker', row.get('stock_code', '')))

print(f'Found {len(tickers)} tickers from {latest.name}')

# Write ticker list for batch spec generation
ticker_file = Path('$DART_BATCH_DIR/tickers.txt')
ticker_file.write_text('\n'.join(sorted(set(tickers))))
print(f'Ticker list: {ticker_file}')
" 2>&1 | tee -a "$LOG_FILE"

    if [[ -f "$DART_BATCH_DIR/tickers.txt" ]]; then
      # Generate batch spec
      BATCH_SPEC="$DART_BATCH_DIR/dart_batch_spec.json"
      uv run python scripts/generate_dart_fetch_batch_spec.py \
        --mode both \
        --corp-codes-file "$DART_BATCH_DIR/tickers.txt" \
        --filing-bgn-de "${TARGET_YEAR}0101" \
        --filing-end-de "${TARGET_YEAR}1231" \
        --financial-start-year "$TARGET_YEAR" \
        --financial-end-year "$TARGET_YEAR" \
        --reprt-codes "$TARGET_Q" \
        --output-file "$BATCH_SPEC" \
        2>&1 | tee -a "$LOG_FILE"

      if [[ -f "$BATCH_SPEC" ]]; then
        log "Batch spec generated. Fetching DART data..."

        BATCH_OUT_DIR="$DART_BATCH_DIR/raw"
        AGG_OUT_DIR="$DART_BATCH_DIR/aggregated"
        mkdir -p "$BATCH_OUT_DIR" "$AGG_OUT_DIR"

        # Fetch
        uv run python scripts/fetch_local_dart_response.py \
          --batch-file "$BATCH_SPEC" \
          --output-dir "$BATCH_OUT_DIR" \
          --continue-on-error \
          --skip-verified \
          --delay-seconds 0.5 \
          2>&1 | tee -a "$LOG_FILE"

        # Aggregate
        uv run python scripts/build_local_dart_aggregates.py \
          --input-dir "$BATCH_OUT_DIR" \
          --output-dir "$AGG_OUT_DIR" \
          2>&1 | tee -a "$LOG_FILE"

        log "DART data fetched and aggregated to $AGG_OUT_DIR"
        log "To merge into main aggregate, run:"
        log "  cp $AGG_OUT_DIR/dart_facts_merged.csv data/raw/dart_aggregated_day4_extended_fy2014_pit/dart_facts_merged.csv"
      fi
    fi
  else
    log "[WARN] No universe bundle found. Cannot determine tickers for DART refresh."
  fi

  log "DART filing refresh complete."
}

# ─── Execute ───────────────────────────────────────────────────────────

log "Data refresh started (log: $LOG_FILE)"

if $PRICE_ONLY; then
  refresh_price
elif $UNIVERSE_ONLY; then
  refresh_universe
elif $DART_ONLY; then
  refresh_dart
else
  refresh_price
  refresh_universe
  refresh_dart
fi

log "=== Data refresh finished ==="
log "Log saved to: $LOG_FILE"
