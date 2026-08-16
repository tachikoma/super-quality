#!/usr/bin/env bash
# Sequential Day 19-20 validated re-runs: ADV filter (fixed) on PIT universe,
# then momentum-weight 0.7/0.3. Each ~80 min. Classification promotion is
# automatic when strict_pit + universe/financial validators pass + folds valid.
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

echo "[$(date +%H:%M:%S)] === Day 19: ADV filter re-run (PIT universe, fixed) ==="
ENABLE_ADV_FILTER=True uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day19_pit_adv_rerun \
  > "$LOG_DIR/k200mq_day19_adv_rerun.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 19 done (exit $?)"

echo "[$(date +%H:%M:%S)] === Day 20: momentum-weight 0.7/0.3 (validated) ==="
WEIGHT_MOMENTUM=0.7 WEIGHT_QUALITY=0.3 uv run python -m k200_mq.main "${COMMON_ARGS[@]}" \
  --output outputs_k200mq_day20_validated_mom70 \
  > "$LOG_DIR/k200mq_day20_mom70.log" 2>&1
echo "[$(date +%H:%M:%S)] Day 20 done (exit $?)"

echo "[$(date +%H:%M:%S)] ALL DONE"
