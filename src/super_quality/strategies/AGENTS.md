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
- Exposes `run_strategy()` returning a pandas DataFrame of trades.
- Relies on factor modules under `../factors/`.

## COMMANDS
```bash
# Run strategy unit tests
pytest tests/test_strategy.py
```
