# Implementation Plan - KOSPI 200 Momentum + Quality

Status as of 2026-08-03. This plan records the implementation boundary, not a
claim that the strategy has validated performance.

Legend: `[x]` implemented and covered by the current code/tests; `[ ]` pending,
deferred, or not yet valid for a PIT performance claim.

## Phase 0: Preparation

- [x] Freeze the legacy strategy with the `v2.0-abandoned` tag.
- [x] Create the `src/k200_mq/` package and reusable core modules.
- [x] Add `K200MQConfig` and the module-based CLI.
- [x] Update repository ignore/documentation support files.

## Phase 1: Data and universe

- [x] Implement the as-of-keyed proxy universe loader and cache.
  The current `proxy_current` and `mcap_proxy` sources are explicitly non-PIT.
- [x] Implement price loading with momentum lookback/warmup support.
- [x] Implement KPI200 and KS11 market-index inputs.
- [x] Implement the ADV calculation helper; connecting ADV to execution remains
  deferred.
- [ ] Acquire historical KOSPI 200 constituent files with effective dates and
  connect them to the loader as a true PIT universe.
- [ ] Acquire raw DART filing/publication metadata and use filing availability
  dates in the financial loader. Fiscal-period labels are not filing dates.
- [ ] Complete PIT universe and filing-date provenance validation on real data.
- [ ] Implement the ADV-based market-impact model and liquidity constraint.

## Phase 2: Factors

- [x] Implement the versioned v4 skipped-return momentum factor:
  `close[t-42] / close[t-252] - 1` at the default settings.
- [x] Implement normalized-input quality components (ROE, debt/equity,
  operating margin, and cash conversion) and cross-sectional scoring.
- [x] Implement the regime factor using the KPI200 moving-average and
  20-trading-day return conditions.
- [x] Implement factor preparation/merge and readiness/warmup handling.
- [x] Add factor unit and regression tests.
- [ ] Add a production DART account-mapping table and PIT TTM-quarter filter.

## Phase 3: Strategy and portfolio engine

- [x] Implement cross-sectional momentum/quality ranking and TOP-N selection.
- [x] Implement the portfolio rebalance loop, regime scaling, stop-loss, and
  next-session execution.
- [x] Propagate cash and target-weight changes through the engine correctly.
- [x] Apply the configured explicit commission, sell tax, and slippage to fills.
- [x] Add strategy, engine, and integration tests.
- [ ] Implement sector caps, MAX_HOLDINGS, MIN_CASH_RATIO, and correlation
  controls; these are currently unsupported/deferred.

## Phase 4: Integration and validation

### CLI and diagnostics

- [x] Implement `run`, `robustness`, and `true-walkforward` through
  `uv run python -m k200_mq.main`.
- [x] Keep `robustness` as independent subperiod testing, not walk-forward CV.
- [x] Implement the expanding-window WF core with train-only candidate
  selection, two-pass isolation, interval slicing, and exact prepared-date
  coverage checks.
- [x] Persist WF selection/fold, summary, OOS, config/hash, git, and preparation
  context artifacts.
- [x] Implement fail-closed universe/financial provenance contracts and strict
  validation guards.
- [ ] Run a strict PIT WF with actual historical-universe and filing-date
  validators. The current `true-walkforward` output is mechanical non-PIT.

### Benchmark, costs, and semantic safety

- [x] Implement the KPI200 close-based price-return benchmark. It is not a total
  return benchmark and excludes dividends/distributions.
- [x] Implement cost attribution from actual filled trades, with reconciliation
  to execution statistics and portfolio snapshots.
- [x] Implement and test the v4 momentum formula correction, explicit quality
  weights, regime return threshold, stop-loss domain, and related semantic
  safety fixes.
- [x] Add regression tests for the benchmark, fill costs, provenance contracts,
  WF orchestration, and semantic fixes.
- [ ] Run PIT parameter sensitivity after the PIT data gate is met.
- [ ] Run the planned stress-test scenarios.
- [ ] Complete the survivor-bias comparison between current and historical
  constituents.

## Phase 5: Documentation and release

- [x] Update `README.md`, `README_K200MQ.md`, `AGENTS.md`, and the planning docs
  to distinguish diagnostics from evidence.
- [x] Maintain tests for the implemented infrastructure and validation guards.
- [ ] Review the strategy using PIT inputs.
- [ ] Create a release tag after PIT validation and review.

## Current deferred or unsupported settings

The following settings remain compatibility fields or future work and must not
be presented as tested sensitivity dimensions: `SECTOR_CAP`, `MIN_ADV_RATIO`,
`MIN_CASH_RATIO`, `MAX_HOLDINGS`, `UNIVERSE_SIZE`, `USE_52WEEK_HIGH`,
`QUALITY_MIN_TTM_QUARTERS`, and the management/investment/preferred/ETF-ETN
exclusion flags. `MOMENTUM_WINDOW_SHORT` is diagnostic-only. `--no-cache` and
`--rebalance-lookback` are explicitly rejected as unsupported/deferred, and
stop-loss CLI flags are available only for `run`, not `true-walkforward`.

## Critical path to validated evidence

```text
historical PIT universe
  -> filing-date financial data
  -> strict PIT WF
  -> PIT sensitivity and stress tests
  -> review and release
```

PIT data acquisition is the next gate. Until it is complete, all performance
outputs remain mechanical non-PIT diagnostics.
