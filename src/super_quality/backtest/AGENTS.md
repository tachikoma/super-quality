# AGENTS FOR BACKTEST

**Generated:** 2026-06-26
**Branch:** main

## OVERVIEW
Engine that drives the daily simulation loop, consuming market data and strategy signals.

## STRUCTURE
```
src/super_quality/backtest/
└── engine.py   # Main backtesting loop
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Backtest loop | `engine.py` | Handles data fetching, filler, performance metrics |

## CONVENTIONS
- Uses `pandas` for OHLCV manipulation.
- Reports metrics via `analysis/metrics.py`.

## COMMANDS
```bash
# Run backtest tests
pytest tests/test_backtest.py
```
