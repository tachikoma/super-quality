# PROJECT KNOWLEDGE BASE (Agent Guide)

**Generated:** 2026-06-27 (updated 2026-08-30)
**Branch:** feature/k200-low-volatility (based on main)

## Overview

Quant backtesting system for Korean equities. **No active research. All strategies are archived.**

- **Super Quality 2.0** (KOSDAQ small-cap) — Abandoned 2026-07-25 (`v2.0-abandoned`)
- **KOSPI 200 Momentum + Quality** — Archived/pivoted 2026-08-21, no live trading
- **KOSPI 200 Low-Volatility** — Permanently archived 2026-08-22 (diagnostic failed all 3 gates: CAGR 0.81%, Sharpe 0.141, MDD -61.49%)

No live/paper trading, no further tuning, no repeated OOS. Phase 0/1/2A code contracts remain as historical infra only.

## Repository Structure

```
./
├── docs/                  # Documentation (index: docs/README.md)
│   ├── planning/          # Strategy, design, verification docs
│   └── history/           # Changelog (changelog.md)
├── data/                  # Raw / processed datasets
├── outputs*/              # Backtest outputs (local diagnostics)
├── scripts/               # One-shot runners & data tools (incl. low-vol diagnostic)
├── src/k200_low_vol/      # KOSPI 200 Low-Volatility (archived hypothesis; contract valid)
├── src/k200_mq/           # KOSPI 200 Momentum + Quality (archived)
├── src/super_quality/     # LEGACY — frozen at v2.0-abandoned (do not modify)
└── tests/                 # Pytest suite
```

## Strategy Status

| Strategy | Status | Tag / Notes |
|----------|--------|-------------|
| Super Quality 2.0 (KOSDAQ) | **ABANDONED** | `v2.0-abandoned` |
| KOSPI 200 Momentum + Quality | **ARCHIVED/PIVOTED** | no live trading |
| KOSPI 200 Low-Volatility | **ARCHIVED PERMANENTLY** | 2026-08-22 diagnostic failed |

## Hard Constraints

- No active research — new hypotheses require separate economic justification + preregistration.
- No trading — no live/paper trading, order gateway, or signal export.
- Price-return basis only — no dividend reinvestment; do not claim total return.
- Development cutoff `2024-12-31` fixed — do not use real post-2024 data for design/debug. Guard implemented in `src/k200_low_vol/contract.py` & `src/k200_low_vol/data/validator.py` — do not bypass.
- Single-shot diagnostics — archived diagnostics are one-shot, no tuning/repeated OOS.

## Where to Look

| Task | Location | Notes |
|------|----------|-------|
| Legacy CLI | `src/super_quality/main.py` | `argparse` subcommands, deprecated |
| K200 MQ CLI | `src/k200_mq/main.py` | `uv run python -m k200_mq.main run/robustness/true-walkforward` |
| Low-vol spec | `src/k200_low_vol/spec.py` | Frozen spec (window 252, bottom 20%, quarterly rebalance, price-return only) |
| Low-vol contract/validator | `src/k200_low_vol/data/`, `src/k200_low_vol/contract.py` | raw provenance, validator |
| Shared factor interface | `src/k200_mq/core/factors/base.py` | Reused from legacy |
| Config | `src/super_quality/config.py`, `src/k200_mq/config.py`, `src/k200_low_vol/spec.py` | Pydantic-Settings, `DART_API_KEY` |
| Backtest engine | `src/super_quality/backtest/engine.py`, `src/k200_mq/backtest/`, `src/k200_low_vol/` | daily loop / rebalance engine |
| Docs index | `docs/README.md` | Full doc map |
| Changelog | `docs/history/changelog.md` | Full TASK LOG migrated from this file |

## Conventions

- **Formatting:** Ruff, Python 3.11 target, line length 100 (`pyproject.toml`)
- **Settings:** Pydantic-Settings, env vars `DART_API_KEY`, `FSC_KSD_RIGHTS_SERVICE_KEY`
- **Packaging:** Do not create egg-info/dist-info; `uv sync` only. Entry point `super-quality` deprecated (legacy).

## Anti-Patterns

- No bare `except:` — explicit exception handling.
- No distribution artifacts for personal strategy tools.
- No silent relaxation — no cutoff relaxation, no adjusted-price substitution, no PIT bypass.

## Commands

```bash
# Install
uv sync

# Legacy backtest (archived, for reference)
uv run super-quality run --start 2015-01-01 --end 2024-12-31 --output my_results

# K200 MQ diagnostics (archived research)
uv run python -m k200_mq.main run
uv run python -m k200_mq.main robustness
uv run python -m k200_mq.main true-walkforward --output outputs_k200mq

# Tests & lint
pytest -v
ruff check
```

## Docs

- **Index:** [`docs/README.md`](docs/README.md)
- **Current status:** [`docs/planning/05_status.md`](docs/planning/05_status.md)
- **Low-vol preregistration:** [`docs/planning/10_low_volatility_preregistration.md`](docs/planning/10_low_volatility_preregistration.md)
- **Changelog:** [`docs/history/changelog.md`](docs/history/changelog.md) — full TASK LOG moved here (originally in this file, 2026-06-27 ~ 2026-08-22)
