# Current Status - KOSPI 200 Momentum + Quality

This is the canonical current-status document.

Last updated: 2026-08-03

## Status in one view

| Category | Current state |
|----------|---------------|
| Implemented infrastructure | Pipeline, factors, portfolio engine, CLI, benchmark, fill-cost attribution, provenance guards, and tests are implemented. |
| Mechanical non-PIT diagnostics | `robustness` independent subperiod testing and expanding-window `true-walkforward` are available. |
| Validated PIT evidence | None exists yet. |
| Current official diagnostic | v4 no-DART momentum-only mechanical WF: +4.0408% stitched return, -32.0408% MDD, 1,231 OOS points. |
| Obsolete results | All pre-v4 and pre-cash-fix performance outputs are audit-only and non-current. |
| Next gate | Historical PIT constituents and filing-date financial data. |

The project is beta infrastructure, not a validated investment strategy.

## Implemented infrastructure

- `src/k200_mq/` contains the data preparation, v4 momentum, normalized-input
  quality, regime, strategy, portfolio engine, reporting, and validation layers.
- The pipeline runs universe preparation, price lookback, factor preparation,
  strategy selection, execution, and output persistence.
- The portfolio engine includes regime scaling, trailing stop-loss, next-session
  execution, target-weight resizing, cash propagation, and configured explicit
  commission/slippage/sell-tax handling.
- The `run`, `robustness`, and `true-walkforward` module CLI paths are wired.
- `robustness` is explicitly independent subperiod testing. It has no training
  or parameter fitting and is not walk-forward cross-validation.
- The expanding-window WF runner performs train-only selection, freezes all
  selections before test evaluation, slices prepared inputs by interval, and
  checks exact prepared OOS dates when a trading calendar is available.
- WF artifacts include selection/fold results, summary, OOS returns, secret-free
  effective config/hash, git state, and preparation context.
- The benchmark is implemented as KPI200 close-based **price return**. It is not
  total return and excludes dividends/distributions.
- Cost attribution is implemented for actual filled trades. Trade log fields,
  execution statistics, snapshots, metrics, and manifest totals are reconciled.
- Semantic safety fixes are implemented and tested: v4 skipped-return momentum,
  explicit quality weights, regime return threshold, stop-loss validation, and
  related runtime-domain checks.
- K200MQ factor, strategy, engine, provenance, benchmark, cost, and WF regression
  tests are implemented. The legacy `src/super_quality/` package remains frozen.
- A structural local-file PIT candidate importer is implemented at
  `src/k200_mq/data/pit_universe.py`. It normalizes CSV/JSON/Parquet/bytes
  snapshots or explicit effective-dated intervals, but remains unverified
  unless a separate acquisition manifest proves official KRX source metadata
  and verifies the raw-byte SHA-256. It has no network path and is not
  connected to the proxy default loader.

## Mechanical non-PIT diagnostics

The current diagnostic classification is
`mechanical_expanding_walk_forward_non_pit`. Interval slicing is a mechanical
future-row protection only. It does not prove that a security was a historical
KOSPI 200 constituent or that financial information was public by the signal
date.

The current universe uses `proxy_current` or `mcap_proxy` behavior. Normalized
financial inputs use fiscal-period data or are absent when DART is unavailable.
Neither path supplies the required historical PIT evidence.

## Current v4 no-DART true-WF diagnostic

Run command:

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward \
  --output /tmp/k200mq_true_wf_v4_no_dart
```

Run date: 2026-08-03

- Formula: `k200mq-momentum-skipped-return-v4`, default
  `close[t-42] / close[t-252] - 1`.
- Classification: `mechanical_expanding_walk_forward_non_pit`.
- DART was unset, so quality was disabled. This is a **momentum-only
  mechanical diagnostic**, not a Momentum + Quality result.
- All five folds were valid.
- OOS coverage: 1,231 points across the 2020-2024 test intervals.
- Stitched cumulative return: **+4.0408%**.
- Stitched maximum drawdown: **-32.0408%**.
- Fold test returns: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**.

Limitations:

- KOSPI 200 membership and cross-sectional ranking are based on non-PIT proxy
  inputs.
- DART financial data was unavailable for this run, and the normal financial
  path does not yet use filing/publication dates.
- The benchmark available in the implementation is KPI200 price return, not
  total return.
- ADV impact, sector caps, PIT sensitivity, and stress tests are not complete.

This result is current under the v4 formula, but it is not validated performance
evidence and must not be promoted to a canonical/production performance claim.
The temporary output is not copied into or committed to the repository. It
contains:

```text
/tmp/k200mq_true_wf_v4_no_dart/true_walkforward/
  selection_and_folds.json
  summary.csv
  oos_returns.csv
```

## Validated PIT evidence

**No validated PIT evidence exists yet.** The repository contains provenance
validators and strict-fail guards, but actual historical inputs satisfying them
have not been acquired and connected to the WF execution.

The current `true-walkforward` path therefore cannot be labeled
`validated_expanding_walk_forward_pit`. A strict PIT WF, PIT sensitivity, and
any performance conclusion remain pending.

The importer defines the structural candidate contract: `index_code`,
`as_of_date` (or `effective_date`), and `security_code`; source metadata is
normalized when present but is not trusted from raw rows. Explicit interval
files also carry effective bounds, action/status, announcement date, and
provenance. A separate acquisition-manifest sidecar is required for promotion
and must contain an official HTTPS KRX URL, query/date parameters, a
timezone-aware retrieval timestamp, an allowlisted KRX source type, explicit
KRX attestation, and a raw-byte SHA-256 match. Local paths, `file://`, mtimes,
embedded hashes, DataFrames, and arbitrary PIT flags remain unverified. No
official KRX historical PIT file has been acquired or connected, so this
implementation does not change current results or upgrade any diagnostic to
validated evidence.

## Obsolete/audit-only pre-v4 results

All results generated before the v4 momentum semantic correction are classified
as `obsolete_pre_momentum_v4` and are not comparable with the current result.
The earlier reported +207.06%, +14.17%, +26.97%, +44.6426%, and +245-era
figures are retained only for audit history. They also include executions before
the cash-propagation fix where applicable. None is current evidence, a
validated result, or a basis for parameter selection.

## PIT gate and next priority

The explicit next priority is:

1. Acquire historical KOSPI 200 constituent files with effective dates and
   connect the local-file-first importer to the universe loader.
2. Acquire raw DART filing/publication metadata and map availability to safe
   trading sessions without substituting fiscal-period dates.
3. Re-run strict PIT WF and only then run PIT sensitivity and stress tests.

Until these steps are complete, output numbers are mechanical diagnostics only.

## Deferred or unsupported settings

The following settings remain compatibility fields or future work and are not
current sensitivity dimensions: `SECTOR_CAP`, `MIN_ADV_RATIO`,
`MIN_CASH_RATIO`, `MAX_HOLDINGS`, `UNIVERSE_SIZE`, `USE_52WEEK_HIGH`,
`QUALITY_MIN_TTM_QUARTERS`, and the management/investment/preferred/ETF-ETN
exclusion flags. `MOMENTUM_WINDOW_SHORT` is diagnostic-only.

ADV calculation exists as a helper, but ADV-based liquidity and market-impact
execution are not connected. `--no-cache` and `--rebalance-lookback` are
explicitly unsupported/deferred and rejected. Stop-loss flags are `run`-only;
`true-walkforward` uses configuration/default values.

## Output artifacts

`run` may write `portfolio_snapshots.csv`, `trade_log.csv`,
`daily_returns.csv`, `metrics.json`, `benchmark_returns.csv`, and
`run_manifest.json`. `robustness` writes
`subperiod_robustness_summary.csv`. `true-walkforward` writes
`true_walkforward/selection_and_folds.json`, `summary.csv`, and
`oos_returns.csv`. These artifacts include diagnostic/provenance information;
they do not turn non-PIT data into validated evidence.
