#!/usr/bin/env bash
set -euo pipefail

# Day 4 one-shot runner:
# 1) fetch missing DART responses from batch spec
# 2) rebuild merged local aggregates
# 3) run strict true-walkforward with rebuilt inputs

SPEC_FILE="${SPEC_FILE:-data/raw/dart_batch_spec_day4_missing_both.json}"
BATCH_OUT_DIR="${BATCH_OUT_DIR:-data/raw/dart_batch_day4_missing}"
AGG_OUT_DIR="${AGG_OUT_DIR:-data/raw/dart_aggregated_day4_missing}"
RUN_OUT_DIR="${RUN_OUT_DIR:-/tmp/k200mq_day4_$(date +%Y%m%d)_strict_recheck}"

if [[ ! -f "$SPEC_FILE" ]]; then
  echo "[ERROR] Missing batch spec: $SPEC_FILE"
  exit 1
fi

if [[ -z "${DART_API_KEY:-}" ]]; then
  echo "[ERROR] DART_API_KEY is not set."
  echo "        export DART_API_KEY='<your_key>'"
  exit 2
fi

echo "[INFO] Day 4 backfill start"
echo "[INFO] SPEC_FILE=$SPEC_FILE"
echo "[INFO] BATCH_OUT_DIR=$BATCH_OUT_DIR"
echo "[INFO] AGG_OUT_DIR=$AGG_OUT_DIR"
echo "[INFO] RUN_OUT_DIR=$RUN_OUT_DIR"

mkdir -p "$BATCH_OUT_DIR" "$AGG_OUT_DIR"

echo "[STEP 1/3] Fetch local DART responses"
uv run python scripts/fetch_local_dart_response.py \
  --batch-file "$SPEC_FILE" \
  --output-dir "$BATCH_OUT_DIR" \
  --continue-on-error

echo "[CHECK] Validate fetched API status before aggregation"
STATUS_CHECK="$(BATCH_OUT_DIR="$BATCH_OUT_DIR" uv run python - <<'PY'
import glob
import json
import os
from pathlib import Path

batch_out_dir = os.environ['BATCH_OUT_DIR']
manifest_files = glob.glob(f'{batch_out_dir}/*.manifest.json')
quota = 0
verified = 0
for path in manifest_files:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    status = str(payload.get('api_status', ''))
    if status == '020':
        quota += 1
    if status in {'000', '0'}:
        verified += 1

print(f'verified={verified};quota={quota};total_manifests={len(manifest_files)}')
PY
 )"
echo "[CHECK] $STATUS_CHECK"

if [[ "$STATUS_CHECK" == *"verified=0"* ]]; then
  if [[ "$STATUS_CHECK" == *"quota="* && "$STATUS_CHECK" != *"quota=0"* ]]; then
    echo "[ERROR] No verified OpenDART responses were fetched (API quota exceeded: status=020)."
    echo "        Retry with chunked batch after quota reset using --start-index/--max-requests."
  else
    echo "[ERROR] No verified OpenDART responses were fetched. Aborting before aggregation."
  fi
  exit 3
fi

echo "[STEP 2/3] Build merged local DART aggregates"
uv run python scripts/build_local_dart_aggregates.py \
  --input-dir "$BATCH_OUT_DIR" \
  --output-dir "$AGG_OUT_DIR"

echo "[STEP 3/3] Strict true-walkforward rerun"
uv run python -m k200_mq.main true-walkforward \
  --strict-pit \
  --exclude-kospi-top-n 0 \
  --local-pit-universe-path data/universe/kospi200_bundle_strict \
  --local-pit-universe-source-kind snapshots \
  --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json \
  --local-dart-filing-path "$AGG_OUT_DIR/dart_filings_merged.csv" \
  --local-dart-filing-manifest "$AGG_OUT_DIR/dart_filings_merged.manifest.json" \
  --local-dart-financial-path "$AGG_OUT_DIR/dart_facts_merged.csv" \
  --local-dart-financial-manifest "$AGG_OUT_DIR/dart_facts_merged.manifest.json" \
  --output "$RUN_OUT_DIR"

echo "[DONE] Day 4 backfill run finished"
echo "[DONE] Strict output: $RUN_OUT_DIR"
