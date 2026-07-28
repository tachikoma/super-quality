# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-27 (updated 2026-07-26)
**Branch:** main

## OVERVIEW
Super Quality 2.0 is a Python‑based quantitative backtesting system for Korean stocks. **Abandoned 2026-07-25** — replaced by KOSPI 200 Momentum + Quality (`src/k200_mq/`).

## STRUCTURE
```
./
├── docs/planning/      # Strategy pivot docs & implementation plans
├── data/               # Raw / processed datasets
├── outputs/            # Backtest result files (new strategy)
├── src/k200_mq/        # New KOSPI 200 Momentum + Quality package (WIP)
├── src/super_quality/  # LEGACY — frozen at v2.0-abandoned (do not modify)
└── tests/              # Pytest suite
```

## STRATEGY STATUS
| Version | Status | Tag |
|---------|--------|-----|
| Super Quality 2.0 (KOSDAQ small-cap) | **ABANDONED** | `v2.0-abandoned` |
| KOSPI 200 Momentum + Quality | In development | — |

## WHERE TO LOOK (LEGACY)
| Task | Location | Notes |
|------|----------|-------|
| CLI entry point | `src/super_quality/main.py` | Uses `argparse` for sub‑commands |
| Configuration | `src/super_quality/config.py` | Pydantic‑Settings, includes `RELAXED_ENTRY_MODE` |
| Factor implementations | `src/super_quality/factors/` | Value, quality, market‑timing, supply |
| Strategy logic | `src/super_quality/strategies/` | `super_quality.py` applies A‑H conditions |
| Backtest engine | `src/super_quality/backtest/engine.py` | Single‑ticker daily loop |
| Reporting | `src/super_quality/reporting/report.py` |
| Tests | `tests/` | pytest config points here |

## WHERE TO LOOK (NEW — KOSPI 200 MQ)
| Task | Location | Notes |
|------|----------|-------|
| New strategy package | `src/k200_mq/` | WIP — planning phase |
| Architecture docs | `docs/planning/` | 5 documents covering pivot, architecture, plan, spec, status |
| Core factor interface | `src/k200_mq/core/factors/base.py` | Reusable from legacy |
| New CLI (future) | `src/k200_mq/main.py` | Will use `k200-mq` command |

## DEPLOYMENT NOTES (LEGACY)
- **Package distribution removed**: `pyproject.toml` `[project.scripts]` entry point (`super-quality`) and `egg-info` / `dist-info` are legacy artifacts. For personal strategy use, distribution packaging is unnecessary.
- **Editable install**: `uv sync` installs the legacy package in editable mode. Not needed for new strategy work.
- **CLI access (legacy only)**: `uv run super-quality run` — deprecated; kept for reference.
- **New strategy CLI (future)**: `uv run k200-mq run` (planned).

## CONVENTIONS
- **Formatting**: Ruff target Python 3.11, line-length 100 (see `pyproject.toml`).
- **Settings**: Pydantic‑Settings loads environment variables (`DART_API_KEY`).
- **CLI**: `uv run super-quality` invokes the `main` entry point (legacy).

## ANTI‑PATTERNS (THIS PROJECT)
- No `as any` or `@ts-ignore` equivalents – type safety enforced by Pydantic & strict linting.
- No empty `except:` blocks; all error handling is explicit.
- **Do not build distribution artifacts** (egg-info, dist-info) for personal strategy tools — unnecessary overhead.

## UNIQUE STYLES
- Factor‑centric design: each trading signal lives in its own module under `factors/`.
- Data‑caching layer uses Parquet files for fast reloads.

## COMMANDS
```bash
# Install dependencies
uv sync

# Run legacy backtest (deprecated)
uv run super-quality run

# Run with custom dates and output dir (legacy)
uv run super-quality run --start 2015-01-01 --end 2024-12-31 --output my_results

# Run new strategy (WIP)
uv run python -m k200_mq.main  # planned

# Run tests
pytest -v

# Lint
ruff check
```

## TASK LOG — 2026-06-27
(legacy — see AGENTS.md for full history)

## TASK LOG — 2026-07-25
- Strategy abandoned: Super Quality 2.0 tagged `v2.0-abandoned`.
- Pivot decision: KOSPI 200 Momentum + Quality framework.
- Oracle review confirmed: KOSDAQ small-cap value strategy structurally failed; momentum + quality on KOSPI 200 is the replacement hypothesis.
- Librarian review: Korean momentum academic evidence exists but conditional (reversal dominates over full sample, 2-month reversal cycle).
- Planning docs created: `docs/planning/01_strategy_pivot.md` through `05_status.md`.

## TASK LOG — 2026-07-26
- **Infrastructure cleanup**:
  - `.omo/` directory deleted (agent plans — not needed for production).
  - `outputs_2023_2024/` deleted (legacy backtest artifacts).
  - `git tag v2.0-abandoned` created on legacy code.
  - Distribution-related notes added to AGENTS.md.
- **Planning documents** created at `docs/planning/`:
  - `01_strategy_pivot.md` — strategy pivot rationale, discard/retain mapping.
  - `02_architecture.md` — KOSPI 200 MQ package structure, factor design, data flow.
  - `03_implementation_plan.md` — 5-phase plan, 5-8 week timeline.
  - `04_backtest_spec.md` — walk-forward CV, cost model, stress testing.
  - `05_status.md` — real-time progress tracker.

## DEPLOYMENT — CLEANUP (2026-07-26)
- **egg-info / dist-info**: Legacy artifacts from `uv sync`. Not needed for personal strategy tools. Will be removed when legacy package is fully frozen or new strategy has its own setup.
- **`[project.scripts]`**: Entry point `super-quality` in legacy `pyproject.toml` is deprecated. New strategy (`k200_mq`) will have its own `pyproject.toml` with `k200-mq` command (planned).
- **Distribution packaging**: Not required for personal quantitative strategy tools. `uv sync` for dependency management suffices.

## TASK LOG — 2026-07-26 (cont.)
- **Phase 2 complete**: Momentum/Quality/Regime factors, strategy, portfolio engine.
- **Phase 3 complete**: CLI skeleton, 25 files in package.
- **Phase 4 pipeline complete**: `_run_pipeline()` wiring universe → price → factors → engine → save. First backtest run: +207.06% (2020-2024, **unverified**).
- **Bug fixes**: config date type, Timestamp vs date comparisons (3 locations), cache key typo, universe lookup type mismatch, stop-loss threshold.
- **Oracle review**: 14 issues identified (P0: 3, P1: 3, P2: 4, P3: 4). Key P0: regime filter in engine, rebalance date unification, quality factor coverage.
- **Strategy status**: WIP → Beta (pipeline functional, results unverified).
