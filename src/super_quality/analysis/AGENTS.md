# AGENTS FOR ANALYSIS

**Generated:** 2026-06-26
**Branch:** main

## OVERVIEW
Analysis helpers that compute performance metrics and visualisations from back‑test results.

## STRUCTURE
```
src/super_quality/analysis/
└── metrics.py   # KPI calculations (CAGR, Sharpe, max drawdown, etc.)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Metric computation | `metrics.py` | Provides `compute_metrics(df)` returning a dict of KPIs |
| Helper functions | `metrics.py` | Sharpe, Sortino, win‑rate, turnover calculations |

## CONVENTIONS
- All functions accept a pandas DataFrame with standard column names (`date`, `nav`, `cash`, `position`).
- Returns plain Python types for easy consumption by reporting.

## COMMANDS
```bash
# Run analysis unit tests
pytest tests/test_analysis.py
```
