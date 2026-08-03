# Backtest and Validation Specification - KOSPI 200 Momentum + Quality

## Evidence boundary

The current implementation has two mechanical diagnostic paths:

1. `robustness` runs fixed, independent subperiods with no training or
   parameter fitting. It is an independent subperiod robustness test, not
   walk-forward cross-validation.
2. `true-walkforward` runs the expanding-window train/test orchestration with
   train-only candidate selection. Unless actual historical universe and
   filing-date validators are supplied, its classification is
   `mechanical_expanding_walk_forward_non_pit`.

There is no validated PIT performance evidence yet. A mechanical interval split
prevents future rows from entering an interval, but it does not create
historical constituent or filing-date provenance.

## Current formula and result boundary

The current momentum version is `k200mq-momentum-skipped-return-v4`:

```text
close[t-skip_days] / close[t-long_window] - 1
```

The default is `close[t-42] / close[t-252] - 1`. Results produced before this
semantic correction are obsolete and must not be compared with the current
formula. They are retained only as audit records in the canonical status
document (`docs/planning/05_status.md`).

## Independent subperiod robustness

The `robustness` command independently runs:

| Subperiod | Dates |
|-----------|-------|
| 1 | 2014-2016 |
| 2 | 2017-2018 |
| 3 | 2019-2020 |
| 4 | 2021-2022 |
| 5 | 2023-current |

Each period uses fixed settings and has no training window. A factor or
execution semantic change requires a fresh run of every subperiod.

## True expanding-window walk-forward

The current fixed fold schedule is:

```text
Fold 1: Train 2015-2019 | Test 2020
Fold 2: Train 2015-2020 | Test 2021
Fold 3: Train 2015-2021 | Test 2022
Fold 4: Train 2015-2022 | Test 2023
Fold 5: Train 2015-2023 | Test 2024
```

The runner first completes and freezes all train selections, then evaluates
the selected candidates on test intervals. Prepared price calendars require
exact OOS date coverage; truncated folds are invalid. Artifacts are written to
`true_walkforward/selection_and_folds.json`, `summary.csv`, and
`oos_returns.csv`, together with secret-free config/hash, git, and preparation
context.

This is a mechanical non-PIT WF unless actual validators are supplied. The
`validated_expanding_walk_forward_pit` label must not be used for a run based
on proxy constituents, fiscal-period financial data, an arbitrary PIT flag, or
synthetic evidence. Strict PIT WF remains pending until the next data gate is
met.

### Current v4 no-DART diagnostic (2026-08-03)

Command:

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward \
  --output /tmp/k200mq_true_wf_v4_no_dart
```

- Formula: v4 skipped return, `close[t-42] / close[t-252] - 1`.
- Classification: `mechanical_expanding_walk_forward_non_pit`.
- Quality was disabled because DART was unset; this is a momentum-only
  mechanical diagnostic, not a Momentum + Quality result.
- Five folds were valid and produced 1,231 OOS points for 2020-2024.
- Stitched cumulative return: **+4.0408%**.
- Stitched maximum drawdown: **-32.0408%**.
- Fold test returns: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**.

The run uses a non-PIT proxy universe/ranking and does not establish filing-date
financial provenance. It is not validated performance evidence or a canonical
production result. The output directory is temporary and is not copied into or
committed to the repository.

## Purge and embargo

Purge and embargo are currently deferred/not applicable for the pure core. The
current candidates are backward-only fixed signals and do not fit forward labels
or overlapping outcomes. If such labels or overlapping outcomes are added, the
fold schedule and purge/embargo rules must be redesigned before using them.

## Transaction costs and attribution

Actual fills apply the configured explicit costs:

| Item | Current setting |
|------|-----------------|
| Commission | 0.015% per side |
| Sell tax | 0.20% on sells |
| Slippage | 0.10% per fill |
| Market impact | Deferred/unsupported; ADV model not connected |

Cost attribution is implemented for actual filled trades. Commission,
slippage, sell-only tax, buy/sell notionals, turnover, and total cost are
reconciled across the trade log, execution statistics, portfolio snapshots,
`metrics.json`, and `run_manifest.json` where applicable. It is not a
prospective ADV impact estimate.

## Benchmark

The current benchmark is the configured **KPI200 price return**: close-to-close
index price returns clipped to the measured interval before `pct_change()`.
It is **not total return** and does not include dividends or other
distributions. Benchmark provenance and source ticker are recorded in the run
manifest. Total-return, equal-weight, and buy-and-hold alternatives are not
current implemented evidence.

## PIT gate and deferred analyses

The next gate is acquisition and wiring of:

- historical KOSPI 200 constituents with effective dates; and
- raw DART filing/publication metadata mapped to safe trading-session
  availability dates.

After that gate, the following remain pending:

- strict PIT WF;
- PIT parameter sensitivity;
- survivor-bias comparison;
- planned stress tests.

The following settings are currently unsupported or inert and are excluded
from sensitivity claims: `SECTOR_CAP`, `MIN_ADV_RATIO`, `MIN_CASH_RATIO`,
`MAX_HOLDINGS`, `UNIVERSE_SIZE`, `USE_52WEEK_HIGH`,
`QUALITY_MIN_TTM_QUARTERS`, and the management/investment/preferred/ETF-ETN
exclusion flags. `MOMENTUM_WINDOW_SHORT` is diagnostic-only.

## Output artifacts

The `run` output directory may contain `portfolio_snapshots.csv`,
`trade_log.csv`, `daily_returns.csv`, `metrics.json`,
`benchmark_returns.csv`, and `run_manifest.json`. Robustness adds
`subperiod_robustness_summary.csv`; true WF adds the three files under
`true_walkforward/`. These artifacts record diagnostics and provenance, not
validated PIT evidence.
