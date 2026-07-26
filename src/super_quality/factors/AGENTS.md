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
├── market_timing.py # Market timing via KOSDAQ index (KQ11) MA signals
└── supply.py        # Retail net buying power — daily time‑series factor
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Factor logic | `value.py`, `quality.py`, `market_timing.py`, `supply.py` | Core signal calculations |
| Shared types | `base.py` | Abstract interface |

## CONVENTIONS
- Factors inherit from `Factor` base class and implement `compute(df) -> pd.DataFrame`.
- `supply.py` returns daily (ticker × date) rows; merged in `main.py` on `["ticker", "date"]`.
- Cross‑sectional percentiles are computed per date (not historical cumulative).

## COMMANDS
```bash
# Ensure variables are tested by running strategy tests.
pytest tests/test_factors.py
```
