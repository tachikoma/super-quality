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
- Position sizing is dynamic: `nav / MAX_HOLDINGS` (5% per position when MAX_HOLDINGS=20).
- Reports metrics via `analysis/metrics.py`.
- `evaluate_sell_conditions()` supports `market_sell` parameter (currently unused — was too aggressive).

## COMMANDS
```bash
# Run backtest tests
pytest tests/test_backtest.py
```
