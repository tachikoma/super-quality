#!/usr/bin/env bash
# Sequential parameter-stability grid on the PIT universe (validated).
# Each run ~80 min. Parameters NOT already covered by the walk-forward
# candidate library (TOP_N_10/30, REGIME_OFF are candidates):
#   momentum weight 0.4/0.6/0.8 (0.5=Day18, 0.7=Day20 baselines exist)
#   rebalance Q, stop-loss -0.10/-0.20, max position weight 0.15,
#   min cash ratio 0.10
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMMON_ARGS=(
  true-walkforward
  --strict-pit
  --exclude-kospi-top-n 0
  --local-pit-universe-path data/universe/kospi200_bundle_pit
  --local-pit-universe-source-kind snapshots
  --local-pit-universe-manifest data/universe/kospi200_bundle_pit/bundle.manifest.json
  --local-dart-filing-path data/raw/dart_aggregated_day4_extended_fy2014/dart_filings_merged.csv
  --local-dart-filing-manifest data/raw/dart_aggregated_day4_extended_fy2014/dart_filings_merged.manifest.json
  --local-dart-financial-path data/raw/dart_aggregated_day4_extended_fy2014/dart_facts_merged.csv
  --local-dart-financial-manifest data/raw/dart_aggregated_day4_extended_fy2014/dart_facts_merged.manifest.json
)

LOG_DIR="/var/folders/q0/lsmgmb2j143990vrm9wszm3c0000gn/T/opencode"

run_one() {
  local label="$1"; shift
  local out="$1"; shift
  local envvars=("$@")
  echo "[$(date +%H:%M:%S)] === $label ==="
  env "${envvars[@]}" uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
    --output "$out" > "$LOG_DIR/${label}.log" 2>&1
  echo "[$(date +%H:%M:%S)] $label done (exit $?)"
}

# 1. momentum weight sweep (0.5/0.7 baselines exist)
run_one "grid_mom04"  outputs_k200mq_grid_mom04  WEIGHT_MOMENTUM=0.4 WEIGHT_QUALITY=0.6
run_one "grid_mom06"  outputs_k200mq_grid_mom06  WEIGHT_MOMENTUM=0.6 WEIGHT_QUALITY=0.4
run_one "grid_mom08"  outputs_k200mq_grid_mom08  WEIGHT_MOMENTUM=0.8 WEIGHT_QUALITY=0.2
# 2. rebalance frequency (at momentum 0.7)
run_one "grid_rebalQ" outputs_k200mq_grid_rebalQ  WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 REBALANCE_FREQ=Q
# 3. stop-loss threshold sweep (at momentum 0.7)
run_one "grid_sl10"   outputs_k200mq_grid_sl10    WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 SL_STOP_LOSS=-0.10
run_one "grid_sl20"   outputs_k200mq_grid_sl20    WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 SL_STOP_LOSS=-0.20
# 4. sizing / cash buffer (at momentum 0.7)
run_one "grid_pos15"  outputs_k200mq_grid_pos15   WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 MAX_POSITION_WEIGHT=0.15
run_one "grid_cash10" outputs_k200mq_grid_cash10  WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 MIN_CASH_RATIO=0.10

echo "[$(date +%H:%M:%S)] ALL DONE"
