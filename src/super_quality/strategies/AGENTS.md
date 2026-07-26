# AGENTS FOR STRATEGIES

**Generated:** 2026-06-26
**Branch:** main

## OVERVIEW
Strategy module that assembles factor signals and applies the A‑H condition pipeline.

## STRUCTURE
```
src/super_quality/strategies/
└── super_quality.py   # Core strategy implementation
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Strategy logic | `super_quality.py` | Combines factor outputs, applies filters, generates trade signals |

## CONVENTIONS
- Exposes `evaluate_buy_conditions(df)` and `evaluate_sell_conditions(position, current_price, entry_price, hold_days, market_sell=False)`.
- Relies on factor modules under `../factors/`.
- Exit priority: stop_loss > take_profit > expiry > (none).
- `market_sell` is passed but currently unused (KOSDAQ MA3 & MA5 breakdown was found too aggressive).

## COMMANDS
```bash
# Run strategy unit tests
pytest tests/test_strategy.py
```
