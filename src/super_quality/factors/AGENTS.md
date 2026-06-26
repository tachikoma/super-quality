# AGENTS FOR FACTORS

**Generated:** 2026-06-26
**Branch:** main

## OVERVIEW
This directory contains factor modules that define individual trading signals used by the strategy.

## STRUCTURE
```
src/super_quality/factors/
├── base.py          # Abstract base class for factors
├── value.py         # PBR and market‑cap percentile factor
├── quality.py       # GP/A, dividend‑free score factor
├── market_timing.py # KOSDAQ moving‑average based entry/exit signals
└── supply.py        # Net investor buying‑selling power factor
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Factor logic | `value.py`, `quality.py`, `market_timing.py`, `supply.py` | Core signal calculations |
| Shared types | `base.py` | Abstract interface |

## CONVENTIONS
- Functions expose `calculate` returning a pandas Series of factor values.
- All factor modules follow the same public API.

## COMMANDS
```bash
# Ensure variables are tested by running strategy tests.
pytest tests/test_factors.py
```
