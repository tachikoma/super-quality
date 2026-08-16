#!/usr/bin/env bash
# Sequential Day 11-13 diagnostics: ADV filter, momentum-weight sensitivity,
# and stop-loss stress test. Each run is a strict true-walkforward (~80 min).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMMON_ARGS=(
  true-walkforward
  --strict-pit
  --exclude-kospi-top-n 0
  --local-pit-universe-path data/universe/kospi200_bundle_strict
  --local-pit-universe-source-kind snapshots
  --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json
  --local-dart-filing-path data/raw/dart_aggregated_day4_extended_fy2014/dart_filings_merged.csv
  --local-dart-filing-manifest data/raw/dart_aggregated_day4_extended_fy2014/dart_filings_merged.manifest.json
  --local-dart-financial-path data/raw/dart_aggregated_day4_extended_fy2014/dart_facts_merged.csv
  --local-dart-financial-manifest data/raw/dart_aggregated_day4_extended_fy2014/dart_facts_merged.manifest.json
)

LOG_DIR="/var/folders/q0/lsmgmb2j143990vrm9wszm3c0000gn/T/opencode"

echo "[$(date +%H:%M:%S)] === Day 11: ADV filter ==="
ENABLE_ADV_FILTER=True uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day11_adv_filter \
  > "$LOG_DIR/k200mq_day11_adv.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 11 done (exit $?)"

echo "[$(date +%H:%M:%S)] === Day 12: momentum-weight sensitivity (0.7/0.3) ==="
WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day12_sensitivity_mom70 \
  > "$LOG_DIR/k200mq_day12_sens.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 12 done (exit $?)"

echo "[$(date +%H:%M:%S)] === Day 13: stop-loss stress (disabled) ==="
ENABLE_STOP_LOSS=False uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day13_stress_nostop \
  > "$LOG_DIR/k200mq_day13_stress.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 13 done (exit $?)"

echo "[$(date +%H:%M:%S)] ALL DONE"