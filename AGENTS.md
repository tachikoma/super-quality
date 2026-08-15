# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-27 (updated 2026-08-03)
**Branch:** main

## OVERVIEW
Super Quality 2.0 is a Python‑based quantitative backtesting system for Korean stocks. **Abandoned 2026-07-25** — replaced by KOSPI 200 Momentum + Quality (`src/k200_mq/`).

## STRUCTURE
```
./
├── docs/planning/      # Strategy pivot docs & implementation plans
├── data/               # Raw / processed datasets
├── outputs/            # Backtest result files (new strategy; diagnostics)
├── src/k200_mq/        # New KOSPI 200 Momentum + Quality package (Beta)
├── src/super_quality/  # LEGACY — frozen at v2.0-abandoned (do not modify)
└── tests/              # Pytest suite
```

## STRATEGY STATUS
| Version | Status | Tag |
|---------|--------|-----|
| Super Quality 2.0 (KOSDAQ small-cap) | **ABANDONED** | `v2.0-abandoned` |
| KOSPI 200 Momentum + Quality | Beta; mechanical non-PIT diagnostics only | — |

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
| New strategy package | `src/k200_mq/` | Beta infrastructure; PIT evidence pending |
| Architecture docs | `docs/planning/` | Planning documents covering pivot, architecture, plan, spec, status, and benchmark/cost attribution |
| Core factor interface | `src/k200_mq/core/factors/base.py` | Reusable from legacy |
| New CLI | `src/k200_mq/main.py` | Use `uv run python -m k200_mq.main` |

## DEPLOYMENT NOTES (LEGACY)
- **Package distribution removed**: `pyproject.toml` `[project.scripts]` entry point (`super-quality`) and `egg-info` / `dist-info` are legacy artifacts. For personal strategy use, distribution packaging is unnecessary.
- **Editable install**: `uv sync` installs the legacy package in editable mode. Not needed for new strategy work.
- **CLI access (legacy only)**: `uv run super-quality run` — deprecated; kept for reference.
- **New strategy CLI**: `uv run python -m k200_mq.main run` (module CLI; no separate
  distribution entry point).

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

# Run new strategy diagnostics
uv run python -m k200_mq.main run
uv run python -m k200_mq.main robustness
uv run python -m k200_mq.main true-walkforward --output outputs_k200mq

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
- **`[project.scripts]`**: Entry point `super-quality` in legacy `pyproject.toml` is deprecated. The new strategy remains a module CLI and has no separate distribution entry point.
- **Distribution packaging**: Not required for personal quantitative strategy tools. `uv sync` for dependency management suffices.

## TASK LOG — 2026-07-26 (cont.)
- **Phase 2 complete**: Momentum/Quality/Regime factors, strategy, portfolio engine.
- **Phase 3 complete**: CLI skeleton, 25 files in package.
- **Phase 4 pipeline complete**: `_run_pipeline()` wiring universe → price → factors → engine → save. The initial performance output is retained only as an `obsolete_pre_momentum_v4` audit diagnostic; fresh PIT WF evidence remains pending.
- **Bug fixes**: config date type, Timestamp vs date comparisons (3 locations), cache key typo, universe lookup type mismatch, stop-loss threshold.
- **Oracle review**: 14 issues identified (P0: 3, P1: 3, P2: 4, P3: 4). Key P0: regime filter in engine, rebalance date unification, quality factor coverage.
- **Strategy status**: WIP → Beta (pipeline functional, results unverified).

## TASK LOG - 2026-08-03

- **Momentum formula correction**: current version is
  `k200mq-momentum-skipped-return-v4`, using
  `close[t-42] / close[t-252] - 1` by default. All pre-v4 performance outputs are
  `obsolete_pre_momentum_v4` audit diagnostics.
- **Current true-WF diagnostic**: a fresh v4 run with `DART_API_KEY=""` used the
  `mechanical_expanding_walk_forward_non_pit` path. It is momentum-only, with
  +4.0408% stitched return, -32.0408% stitched MDD, and 1,231 OOS points across
  2020-2024. It is not validated performance evidence because the universe and
  financial inputs are not PIT.
- **Benchmark and cost attribution**: KPI200 close-based price-return benchmark
  and actual-fill commission/slippage/sell-tax attribution are implemented and
  reconciled across fills, execution statistics, snapshots, and outputs. The
  benchmark is not total return; ADV impact remains deferred.
- **Next priority**: acquire and wire historical KOSPI 200 constituent files with
  effective dates and raw DART filing/publication metadata. Only after that PIT
  data gate should strict PIT WF, PIT sensitivity, and stress tests run.

## TASK LOG - 2026-08-10

- **DART pipeline performance**: `dart_pit.py` `_map_one_session` rewritten to
  use `searchsorted` (was row-wise boolean mask), `_drop_future_unmappable_rows`
  vectorized (was per-row `iloc` parse). Prepare pipeline: 8+ min → ~82s
  (load 12s + join 19s + map 40.3s). Added `ord` to financial fact identity and
  `account_name`+`ord` to join/amendment grouping. Commit `238a4de`.
- **First strict PIT WF pass**: `true-walkforward --strict-pit` with bundle
  universe + `data/raw/dart_aggregated_day4_extended/` completed 5/5 folds valid,
  OOS 1,231 points (2020-2024), zero strict preflight failures. Financial
  provenance promoted to `pit_filing_date` + `pit_valid=true` (first time past
  `non_pit_fiscal_period`); universe PIT for all as-of dates.
- **Day 8 OOS (stitched)**: +57.79% total, CAGR 9.79%, Sharpe 0.737, MDD
  -23.40%, Calmar 0.418. Folds: 2020 +21.7% / 2021 +0.5% / 2022 -7.8% /
  2023 +22.2% / 2024 +15.8%.
- **Classification stays `mechanical_expanding_walk_forward_non_pit`**: strict
  preflight pass is not a validated PIT performance claim. The first-ready
  rebalance has `momentum_z` readiness of 147/198 (51 missing tickers), not
  financial or quality coverage (`quality_required=false`; see
  `src/k200_mq/main.py:1150-1176`). The earlier financial-gap interpretation is
  superseded by this erratum. Quality separately runs in
  `partial_allowed_fill_missing_with_zero`. Scorecard 2026-08-10: **Continue
  (conditional)**.

## TASK LOG — 2026-08-13

- **Day 8 readiness erratum**: `first_ready_rebalance` usable 147/198 and
  missing 51 are `momentum_z` readiness counts, not financial/quality coverage;
  `quality_required=false`. FY2014 XBRL improves financial PIT facts but cannot
  resolve the momentum price-history warmup.
- **리밸런스별 재무 커버리지 진단 구현**: `src/k200_mq/main.py`가 각 리밸런스의
  정확한 PIT 유니버스 as-of와 측정 신호일 이전 또는 동일한 최신 상태를 사용해
  완전한 6개 원천 사실(revenue, cogs, net_income, operating_cf, total_assets,
  total_equity)을 점검한다. 원천 가용성과 PIT 게이트 통과 커버리지를 분리하며,
  중립값으로 채운 quality 입력으로 커버리지를 추론하지 않는다. 이번 기록에는
  신규 실행 수치를 추가하지 않는다.
- **Phase 3 FY2014 XBRL status**: 141 original receipts selected, 119 verified
  XBRL ZIP files, 92 strict six-fact accepted, 22 with OpenDART official status
  `014` indicating the requested XBRL document is unavailable (not merely a
  missing local file), and 27 parser fail-closed.
- **Next priority**: complete the separate FY2014 financial PIT validation and
  momentum warmup/readiness review, then run PIT sensitivity, survivorship-bias
  comparison, ADV impact, and stress tests. The classification remains
  `mechanical_expanding_walk_forward_non_pit`.

## TASK LOG — 2026-08-15

- **FY2014 XBRL 병합 + Day 10 strict WF**: `scripts/merge_fy2014_xbrl_into_aggregate.py`
  (커밋 `0113611`)가 확장 facts CSV와 92개 FY2014 XBRL 아티팩트를 로더 검증 경로로
  병합해 `data/raw/dart_aggregated_day4_extended_fy2014/` 생성 (facts 304,245행 =
  303,693 + 552, dedup 0, reload `verified=True`). Day 10 strict WF
  (`outputs_k200mq_day10_strict_extended_fy2014`)는 5/5 폴드 valid, OOS 1,231점,
  첫 리밸런스 2015-05-29 six-fact 커버리지 **0/198 → 92/198** 개선 확인.
- **OOS 성과 Day 9와 동일**: 2020-2024 stitched 수치가 Day 9와 일치하는 것은
  정상 — OOS 구간 quality는 2015+ 재무 데이터만 사용하므로 FY2014 병합이 OOS
  팩터·후보 순위에 영향 없음. train 기간 팩터는 실제 변경됨 (fold1 train_scores
  변경으로 반영 확인). 분류는 `mechanical_expanding_walk_forward_non_pit` 유지.
- **잔여 갭 (불변)**: momentum_z readiness 147/198 (가격 warmup, FY2014와 무관),
  유니버스 proxy (B/C 44개)는 역사적 KOSPI 200 구성원 데이터로만 해결.
- **Next priority**: momentum warmup/readiness 검토 → PIT 민감도, 생존자 편향
  비교, ADV 영향, 스트레스 테스트. classification 승격 전제: 유니버스 PIT화.
