# AGENTS FOR REPORTING

**Generated:** 2026-06-26
**Branch:** main

## OVERVIEW
Reporting utilities that transform back‑test results into HTML reports, PNG charts and CSV exports.

## STRUCTURE
```
src/super_quality/reporting/
└── report.py   # Generates HTML tear‑sheet, plots and CSV files
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Report generation | `report.py` | Uses Plotly/Mpl for charts, Jinja2 for HTML template |
| Asset export | `report.py` functions | Writes `tearsheet.html`, `equity_curve.png`, etc. |

## CONVENTIONS
- Exposes `generate_report(results: dict, output_dir: str)`.
- Writes files into the user‑specified output directory (default `outputs/`).

## COMMANDS
```bash
# Verify report generation via integration test
pytest tests/test_reporting.py
```
