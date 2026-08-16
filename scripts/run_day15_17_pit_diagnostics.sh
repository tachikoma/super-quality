#!/usr/bin/env bash
# Sequential Day 15-17 diagnostics on the PIT universe: ADV filter,
# momentum-weight sensitivity, and stop-loss stress. Each ~80 min.
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

echo "[$(date +%H:%M:%S)] === Day 15: ADV filter (PIT universe) ==="
ENABLE_ADV_FILTER=True uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day15_pit_adv_filter \
  > "$LOG_DIR/k200mq_day15_pit_adv.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 15 done (exit $?)"

echo "[$(date +%H:%M:%S)] === Day 16: momentum-weight 0.7/0.3 (PIT universe) ==="
WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day16_pit_sensitivity_mom70 \
  > "$LOG_DIR/k200mq_day16_pit_sens.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 16 done (exit $?)"

echo "[$(date +%H:%M:%S)] === Day 17: stop-loss disabled (PIT universe) ==="
ENABLE_STOP_LOSS=False uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day17_pit_stress_nostop \
  > "$LOG_DIR/k200mq_day17_pit_stress.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 17 done (exit $?)"

echo "[$(date +%H:%M:%S)] ALL DONE"
