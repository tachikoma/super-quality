# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-27 (updated 2026-08-21)
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
| KOSPI 200 Momentum + Quality | Research stopped/pivoted; no live trading | — |

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
| New strategy package | `src/k200_mq/` | Research archive; no live trading |
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

## TASK LOG — 2026-08-16

- **Momentum readiness 검토 (커밋 `b9ad9c1`)**: 첫 리밸런스(2015-05-29)
  momentum_z 147/198의 missing 51개 = 47개(2015-05-29 이후 첫 가격일, 상장 전)
  + 4개(2014년 하반기 상장 warmup 부족). 가격 데이터 수집 누락 0건 —
  전부 유니버스 proxy 특성. 갭은 유니버스 PIT화로만 해소.
- **Day 11-13 파라미터 진단 (커밋 `0f0f1b7`)**: ① ADV 필터
  (`outputs_k200mq_day11_adv_filter`) stitched -1.37% — 필터가 후보 풀을 축소해
  분산 붕괴, 임계값 재검토 필요. ② 모멘텀 가중 0.7/0.3
  (`outputs_k200mq_day12_sensitivity_mom70`) +71.42% — 개선 신호. ③ 손절 비활성
  (`outputs_k200mq_day13_stress_nostop`) +117.00%지만 2020 MDD -37.4% 급증 —
  손절이 MDD 방어에 유효. 모두 기계적 non-PIT 진단.
- **유니버스 PIT화 + Day 14 (커밋 `b2740ee`)**: pykrx
  `get_index_portfolio_deposit_file`(KRX 공식, 과거 날짜 지원)로 120개 as-of
  실제 구성원 fetch → `data/universe/kospi200_bundle_pit/` (323 유니크 티커,
  스냅샷 200~202). 가격 캐시에 없던 155개 티커 백필 (0 missing).
  생존자 편향 정량화: 2015-05-29 기준 proxy와 일치 108 / proxy-only 90 /
  pit-only 92 — proxy 구성원 ~46%가 역사 구성원과 다름.
  Day 14 strict WF (`outputs_k200mq_day14_strict_pit_universe`): 유니버스
  전 as-of `pit_valid=true` (최초), 5/5 폴드 valid, stitched **+20.55%**
  (proxy +44.15% 대비 -23.6%p — 생존자 편향 제거로 하향). classification은
  `mechanical_expanding_walk_forward_non_pit` 유지 (`walk_forward.py:579-582`가
  validated 승격을 provenance validators wiring 전까지 명시적 거부).
- **Classification 승격 검토 (Oracle, `docs/planning/07_*` 기록)**: validator
  wiring은 이미 완료·통과 중 (`_validate_prepared_pit_provenance`가 strict
  preflight + interval마다 유니버스/재무 검증). 차단은 하드코딩
  (`main.py:3117,2885,2986,3145` + `walk_forward.py:39-49,579`)와 증거 프록시
  2건 — ① `filing_date_used`가 엔진 소비 증명이 아닌 자기참조 프록시,
  ② quality 6-fact 부분 커버리지가 PIT 유효성에 미포함. 두 전제조건을 닫고
  adapter에서 실제 validator 결과로 classification을 결정해야 승격 가능.
- **Next priority**: ① classification 승격 전제조건 2건 해소 (filing_date_used
  하드 증명, quality 커버리지 게이트/명시 한계), ② PIT 유니버스 기준
  민감도/스트레스 재실행 (Day 15-17, `scripts/run_day15_17_pit_diagnostics.sh`),
  ③ PIT 유니버스 기준 DART 재무 커버리지 재점검 (Day 14: 첫 리밸런스 73/200 =
  36.5%, proxy 대비 하향).
- **Day 15-17 PIT 유니버스 진단 (커밋 `57efeec` 이후)**: ① Day 15 ADV 필터
  (`outputs_k200mq_day15_pit_adv_filter`) — 5/5 폴드 실패. 원인: PIT 유니버스
  상장폐지 종목 16개(000030 등)가 가격 캐시에서 mcap=0 (KRX 상장폐지 데이터는
  시가총액 미제공) → `_build_adv_ratio_map`이 제외 → `_apply_adv_filter`가
  fail-closed RuntimeError. proxy 유니버스(Day 11)는 상장폐지가 없어 통과했음.
  ADV 필터는 상장폐지 mcap 보강 또는 커버리지 정책(fail-open/제외) 필요.
  ② Day 16 모멘텀 0.7/0.3 (`outputs_k200mq_day16_pit_sensitivity_mom70`)
  stitched **+53.23%** (Day 14 +20.55% 대비 개선, proxy와 같은 방향).
  ③ Day 17 손절 비활성 (`outputs_k200mq_day17_pit_stress_nostop`) **+35.85%**
  이나 2020 MDD -37.9% 급증 (손절 활성 -20.8%) — 손절 MDD 방어 PIT에서도 확인.
- **Next priority (갱신)**: ① classification 승격 전제조건 2건 해소
  (filing_date_used 하드 증명, quality 커버리지 게이트/명시 한계) 후 adapter에서
  실제 validator 결과로 classification 결정, ② ADV 필터 상장폐지 mcap 보강
  또는 커버리지 정책 정리, ③ PIT 유니버스 기준 DART 재무 커버리지 재점검
  (첫 리밸런스 73/200 = 36.5%).

## TASK LOG — 2026-08-17 (classification 승격 완료)

- **승격 구현 (커밋 `5b45211`)**: Oracle 스펙(A/B/C/D)에 따라
  - `filing_date_used` 하드 증명: `_convert_financial_to_daily`가
    `filing_date_mapped_rows`/`quarter_end_fallback_rows` 카운트 → attrs 및
    `PreparedK200MQInputs.financial_filing_date_mapped_row_count`로 전달.
    strict preflight는 측정 카운트 사용 (레거시 None이면 기존 proxy 폴백).
  - guard 완화: `walk_forward.py` deferral raise 제거, `select_candidate`에
    `pit_valid_evidence` 요구 (문자열 단독 승격 불가 불변식 유지),
    `runner.py`는 Mapping `pit_valid_context` 수용·스레딩·freeze.
  - adapter: strict preflight 후 유니버스/재무 validator 재실행 →
    `strict_pit && universe_ok && financial_ok`일 때만 validated,
    invalid 결과는 저장 전 차단 (누수 가드).
  - ADV 필터: 커버리지 누락 티커(상장폐지 mcap=0)는 경고+제외로 진행
    (None 맵은 여전히 fail-closed) — Day 15 PIT 실패 해소.
- **증거 품질 수정 (커밋 `4da34ad`)**: coverage_summary가
  `records[0]`(2015-01-30, 0/200) 대신 `first_ready_rebalance.scheduled_date`
  매칭 record(2015-05-29, 73/200=36.5%) 사용; validated limitations에서
  "Mechanical non-PIT" 문구 제거 → validated 문구 + 실측 커버리지 명시.
- **Day 18 검증 (`outputs_k200mq_day18_validation_check_v2`)**: 최초로
  **classification=`validated_expanding_walk_forward_pit`** 승격 확인.
  valid=True, 5/5 폴드, OOS 1,231점. coverage_summary:
  six_fact 0.365 (73/200) + momentum 0.605 (121/200). stitched **+20.55%**
  (Day 14와 동일 — 승격 코드가 성과 로직에 영향 없음). claim:
  "validated PIT walk-forward; universe + financial provenance validators passed".
- **scorecard 2026-08-16 (`08_go_no_go_scorecard_2026-08-16.md`)**: **Hold** —
  데이터 게이트 완성, 성과 게이트 미달 (OOS CAGR/Sharpe/Calmar 기준 이하,
  2020 서브기간 편중). classification 승격은 라벨 정직성 개선이지 성과 판정
  변경이 아님.
- **Next priority**: ① scorecard 성과 게이트 미달 구간 분석 (모멘텀 가중
  0.7/0.3 승격 후 재검증 — Day 16 +53.23% 신호), ② ADV 필터 PIT 유니버스
  재실행 (Day 15 수정 후, `outputs_k200mq_day15_pit_adv_filter` 재실행),
  ③ PIT 유니버스 기준 재무 커버리지 개선 (첫 리밸런스 73/200 = 36.5%).

## TASK LOG — 2026-08-17 (cont., Day 19-20 승격 후 검증)

- **Day 19 — ADV 필터 재실행 (`outputs_k200mq_day19_pit_adv_rerun`)**:
  classification=`validated_expanding_walk_forward_pit`, valid=True, 5/5 폴드 —
  ADV 커버리지 정책 수정(fix-2, 커밋 `5b45211`)이 Day 15 실패를 해소 (누락
  티커 경고+제외, 예: 000030/002270/003410). Stitched **-22.74%** (2020 +45.4%
  / 2021 -10.9% / 2022 -3.9% / 2023 -13.6% / 2024 -28.2%, 전 폴드 TOP_N_10).
  ADV 필터는 PIT에서도 성과를 크게 악화 — 커버리지 누락으로 후보 풀 축소,
  분산 붕괴. 임계값 재설계 또는 비활성 유지 권장.
- **Day 20 — 모멘텀 0.7/0.3 승격 후 검증 (`outputs_k200mq_day20_validated_mom70`)**:
  classification=`validated_expanding_walk_forward_pit`, valid=True, 5/5 폴드.
  Stitched **+53.23%**, Day 16(mechanical)와 폴드별 완전 동일 — 승격 코드가
  성과에 영향 없음 재확인. coverage_summary 0.365/0.605.
- **Next priority**: ① 성과 게이트 재평가 — 모멘텀 0.7/0.3 validated에서
  CAGR ~8.9%/Sharpe ~0.7 근접 (Day 20 +53.23%), ② ADV 필터 임계값 재설계 또는
  비활성 유지, ③ PIT 기준 재무 커버리지 개선 (73/200 = 36.5%).

## TASK LOG — 2026-08-17 (cont., 검증 인프라 확장)

- **몬테카를로 부트스트랩 (`scripts/monte_carlo_bootstrap.py`, 커밋 `5ded220`)**:
  OOS 1,231일 일별 수익률 stationary bootstrap (블록 20, 5,000회). Day 20
  (0.7/0.3 validated): CAGR 9.13% CI[-10.1%,+32.4%] / Sharpe 0.555 CI[-0.47,1.58]
  / MDD -31.2% CI[-58.3%,-19.3%] / Calmar 0.293 CI[-0.19,1.46]. 게이트 통과
  확률: CAGR≥5% 65.4% / Sharpe≥0.7 40.5% / MDD≥-25% **17.4%** / Calmar≥0.3
  48.6% — **성과 게이트 미달은 표본 우연이 아닌 구조적 신호** (MDD 취약).
- **WFA 후보 라이브러리 확장 (커밋 `48f3fc8`)**: `MOM60`(0.6/0.4),
  `MOM70`(0.7/0.3) 후보 추가 (`k200mq-wf-candidates-v3`) — Oracle 지적
  "0.7/0.3 사후 탐색 스누핑" 해소. 이제 train 성과로 가중 축이 선택됨.
- **실전 적용 검토 (Oracle, 커밋 `78dfa9e` scorecard 반영)**: **자동 주문
  실전 No, paper trading 조건부 Yes.** 실전 전 필수 5항목: ① 0.7/0.3 WF
  후보화(완료), ② ADV 유동성 정책 명시, ③ 상장폐지/거래정지 감지+강제청산,
  ④ 데이터 갱신 자동화(가격 일일/유니버스 월별/DART 공시 트리거),
  ⑤ 리스크 가드레일(일일/월간 손실한도, 드로다운 중단). 신호 export 어댑터
  (signals.json)와 주문 게이트웨이 부재. EXCLUDE_KOSPI_TOP_N=0 고정 전제.
- **scorecard 2026-08-17 (`08_go_no_go_scorecard_2026-08-17.md`)**: **Hold** —
  CAGR point 통과(9.13%)지만 MDD/Sharpe/Calmar 미달 + 부트스트랩 MDD 통과
  17.4% + 2020 서브기간 편중. 실전 자본 배치는 scorecard Go + 필수 5항목
  완료 후.
- **Next priority**: ① 파라미터 안정성 그리드 결과 수렴 (`outputs_k200mq_grid_*`,
  모멘텀 가중 0.4~0.8/rebalance Q/손절 ±0.10,0.20/비중·현금 — 실행 중),
  ② scorecard 재판정 (그리드 + bootstrap 반영), ③ 실전 준비 5항목 중
  ②-⑤ 진행.

## TASK LOG — 2026-08-17 (cont., 파라미터 안정성 그리드)

- **그리드 8개 실행 완료** (`scripts/run_parameter_grid.sh`, 전부
  `validated_expanding_walk_forward_pit`, 5/5 폴드 valid):
  - **sl20 (0.7/0.3, SL-20%)**: stitched **+58.48%**, CAGR 9.66%, Sharpe 0.567,
    MDD -28.37%, Calmar **0.340** — 최고 성과 (Day20 SL-15% +53.23% 대비 개선).
  - mom06 (0.6/0.4) +37.14% / cash10 (현금 10%) +33.18%, **MDD -22.55% 최선**
    (유일하게 MDD 게이트 -25% 통과) / mom08 (0.8/0.2) +32.33% /
    sl10 (SL-10%) +13.89% (손절 강화 오히려 악화) / mom04 (0.4/0.6) +0.40% (붕괴).
  - **유효성 발견 2건**: ① rebalQ는 분기 테스트가 아님 — PIT 스냅샷 번들
    import가 `requested_rebalance_dates=None`으로 개별 import 후 concat하므로
    REBALANCE_FREQ=Q가 무시됨 (rebalQ·pos15 oos_returns 완전 동일 = 월간 기본과
    같음). ② pos15도 무효 — 20종목 등액(5%)은 기본 10% 캡에 미달.
  - 해석: 모멘텀 가중 0.7 최적 (0.4 붕괴~0.8 하락), 손절 -20% 상향이 개선
    신호, 현금 버퍼 10%가 MDD 방어. WFA v3 후보 라이브러리에서 MOM60이
    train으로 선택됨 (mom08/cash10에서 4폴드) — 가중 축 스누핑 해소 확인.
  - 2020 서브기간 편중은 전 조합 공통.
- **scorecard 재판정 (그리드 반영)**: 성과 게이트에 가장 근접한 조합은
  **sl20** — CAGR 9.66% 통과, Calmar 0.340 통과, Sharpe 0.567 근접, MDD
  -28.37% 근접 미달. 단 부트스트랩 MDD 통과 확률 17.4%(SL-15% 기준)는
  SL-20%에서 재검증 필요. 최종 판정은 **Hold 유지** (성과 게이트 완전 통과
  전까지).
- **Next priority**: ① SL-20% 조합 부트스트랩 재검증 + 2020 서브기간 편중
  분석 (모멘텀 0.7/0.3 + SL-20% 승격 후보), ② scorecard Go 조건 재정립,
  ③ 실전 준비 5항목 중 ②-⑤ 진행 (ADV 정책 명시, 상장폐지 감지, 데이터
  갱신 자동화, 리스크 가드레일).

## TASK LOG — 2026-08-17 (cont., Day 22 정직 WFA 검증)

- **후보 라이브러리 v4 (커밋 `f5e8000`)**: SL20(0.7/0.3+SL-20%)과
  SL20_CASH10(0.7/0.3+SL-20%+현금10%) 후보 추가 — 그리드 최고 조합을
  train 성과로 검증하기 위함. `MIN_CASH_RATIO`를 `_SAFE_RUNTIME_FIELDS`에
  추가 (SL20_CASH10 interval 실행용).
- **Day 22 — 후보 경쟁 정직 검증 (`outputs_k200mq_day22_candidate_v4`)**:
  기본 config(0.5/0.5, SL-15%)에서 v4 후보 8개 경쟁 → **모든 폴드에서
  REGIME_OFF 선택** (train Sharpe 최고: fold1 -0.446 vs SL20 -0.761 /
  SL20_CASH10 -0.784 / MOM70 -0.778; fold2 -0.005 vs -0.328 / -0.340).
  OOS는 Day 18과 완전 동일 (+20.55%).
- **정정 (중요)**: 그리드의 sl20 +58.48% / cash10 +33.18%는 **사후 OOS
  선택(스누핑)** 이었으며 정직한 WFA train 선택에서 기각됨. Day 20의
  +53.23%(0.7/0.3)도 base config 사후 조정 결과로 동일 한계. **파라미터
  조정(가중/손절/현금)으로는 성과 게이트 통과 불가**가 확증됨 — 유일한
  구조적 경로는 quality 커버리지 개선(재무 데이터) 또는 전략 차원 변경.
- classification은 `validated_expanding_walk_forward_pit` 유지 (승격은
  정상 동작 — 후보 경쟁 결과와 무관).
- **Next priority**: ① quality 커버리지 개선 (36.5% → 상승) — 유일한
  구조적 게이트 통과 경로, ② 실전 준비 5항목 중 ②-⑤ 진행 (paper trading
  조건부 시작), ③ scorecard Go 조건 재정립 (파라미터 축 제외 명시).

## TASK LOG — 2026-08-18 (Day 23: DART 재무 보강 — 데이터 경로도 기각)

- **작업 (커밋 `7dabdc1`)**: `.env`의 DART_API_KEY 확인 후, 2015-05-29
  유니버스 중 facts 전무한 97개 corp의 2015~2024 연간(11011)을 DART API로
  fetch (802건 성공 / 168건 status 013). FY2014는 fnlttSinglAcntAll
  미지원(XBRL만). filing은 페이지네이션 필요 확인 — page_no/page_count=100
  으로 20페이지 + 2026-12-31 확장, (corp_code, rcept_no) 키 중복 제거
  (페이지 반복 25회 해소). 병합 `dart_aggregated_day4_extended_fy2014_pit/`:
  facts 468,921행, filings 303,570행, 조인 무결성 0.
- **커버리지 개선**: 2017-01 86/200→**141/200 (70.5%)**, 2020-01
  98/200→**148/200 (74.0%)**, 2024-01 116/200→**140/200 (70.0%)**. 첫
  리밸런스 36.5%는 FY2014 XBRL 한계로 불변.
- **Day 23 strict WF (`outputs_k200mq_day23_pit_annual_merge`)**: validated
  유지, 5/5 폴드, fold 4/5에서 MOM60 선택(재무 보강이 train 선택에 영향).
  Stitched **+16.85%** (Day 22 +20.55% 대비 소폭 하락).
- **정정 (중요)**: quality 커버리지 개선으로도 성과 게이트 통과 불가 확증.
  파라미터 경로(Day 22)와 데이터 경로(Day 23) 모두 기각 — **성과 게이트
  통과의 구조적 경로는 전략 차원 변경만 남음** (regime/sector 필터 재설계,
  팩터 정의 변경, 또는 OOS/게이트 기준 재검토). 참고: facts 468,921행으로
  재무 로드 ~50분 증가.
- **Next priority (갱신)**: ① 전략 차원 변경 검토 (regime 강화, quality
  팩터 재설계, 팩터 window 조정), ② 실전 준비 5항목 중 ②-⑤ 진행 (paper
  trading 조건부 시작), ③ scorecard Go 조건 재정립 (파라미터·데이터 축
  제외 명시).

## TASK LOG — 2026-08-19

- **Test suite 복구 (커밋 `f47bf9b`)**: macOS 하드코딩 절대 경로로 인한
  30개 테스트 실패를 `__file__` 기반 경로 해석으로 전환 (6개 DART 스크립트
  로더 테스트 + `test_data.py` 1건 실제 버그 수정). ruff lint 클린.
- **Risk guardrails 구현 (커밋 `f47bf9b`)**: `config.py`에 일일/월간 손실
  한도 + 드로다운 중단(cooldown 포함) + 상장폐지/거래정지 감지 설정 12필드
  추가 (opt-in, 기본 비활성). `portfolio_engine.py`에 `_check_risk_guardrails`
  메서드, `_update_delisting_status` 메서드, 강제 청산 로직 추가. 신규
  테스트 `test_k200_mq_risk_guardrails.py` (8건 통과). 기존 백테스트
  영향 없음 (기본 비활성).
- **Regime factor 개선**: `REGIME_REDUCTION`을 `_SAFE_RUNTIME_FIELDS`에
  추가 (`prepared.py`). 후보 라이브러리 v5 (8→11개): `REGIME_70`(0.70),
  `REGIME_50`(0.50), `REGIME_30`(0.30) 추가 — WFA가 리짓 축소 비율을
  선택 가능하게 함. 기존的所有 5폴드 REGIME_OFF 선택 원인 분석: 이진
  리짓 신호(close>MA200 AND 20d return>0)의 고정 50% 축소가 성과를
  일관되게 악화. 후보 확장으로 축소 비율 축 차원 추가.
- **정리**: 15개 파일 변경, 632줄 추가, 65줄 제거. 테스트 484 passed,
  1 skipped, 0 failed.
- **Next priority**: ① WFA 재실행으로 REGIME_70/50/30 후보가 train에서
  선택되는지 검증 (데이터 필요), ② 실전 준비 5항목 중 ②-⑤ 진행,
  ③ scorecard Go 조건 재정립.

## TASK LOG — 2026-08-19 (cont., Day 24: REGIME_REDUCTION axis 검증)

- **Day 24 WFA v5 실행 (`outputs_k200mq_day24_v5_candidates`)**: 11개
  후보(REGIME_70/50/30 포함) 경쟁, classification=`validated_expanding_walk_forward_pit`,
  valid=True, 5/5 폴드, OOS 1,231점.
- **REGIME_70/50/30 선택 0건**: 모든 폴드에서 BASE, REGIME_30, REGIME_50,
  REGIME_70가 동일한 train_sharpe 기록 — REGIME_REDUCTION 파라미터가
  전략 결과에 영향 없음. 리짓 신호 자체(이진 close>MA200 AND 20d return>0)
  가 성과에 기여하지 않아 축소 비율 조정이 무의미.
- **폴드별 선택**: Fold 1-3 REGIME_OFF / Fold 4-5 MOM60 (Day 22와 동일 패턴).
- **OOS 성과**: stitched **+16.41%** (Day 22 +20.55% 대비 -4.1%p 하락),
  CAGR 3.16%, Sharpe 0.274, MDD -37.99%, Calmar 0.083. MDD 악화(-20.8%→
  -37.99%)는 새로 다운로드된 가격 데이터에서 상장폐지 종목의 price history
  변화와 상장폐지 감지(logic: `ENABLE_DELISTING_DETECTION=true`) 활성화가
  복합 영향.
- **정리**: REGIME_REDUCTION 축은 구조적으로 실패 확증. 리짓 신호 자체가
  불리하면 축소 비율은 무의미. regime 강화의 유일한 경로는 신호 자체 재설계
  (continuous scaling, multi-indicator regime, 등).
- **다운로드 로그**: PIT 유니버스 323개 티커 백필로 pykrx 가격 재다운로드
  (~50분 소요), 재무 PIT 매핑 (~3시간 소요, 468K facts).
- **Next priority**: ① regime 신호 재설계 검토 (continuous MA200 거리 기반,
  multi-indicator), ② scorecard 재판정 (Day 24 결과 반영), ③ 실전 준비
  항목 추가 검토.

## TASK LOG — 2026-08-21 (Iteration 1–2b 최종 결정)

- **MQ 연구 중단·피벗**: Iteration 3 섹터 30% cap은 실행하지 않으며 live
  trading도 하지 않는다. 현재 결과는 진단 기록일 뿐 투자 성과 근거가 아니다.
- **Iteration 1 v6**: quality-primary + continuous volatility-targeted regime
  통합 배선 수정 후 QP 후보 0/5 선택. OOS stitched -0.87%, CAGR -0.18%,
  Sharpe -0.010, MDD -35.80%, Calmar -0.005 — 모든 게이트 미달.
- **Iteration 2 v7**: candidate window override는 factor data가 override 전에
  한 번만 계산되어 MOM6_1과 해당 기본 후보의 train Sharpe가 동일했다. 유효한
  모멘텀 성과 실험이 아니라 아키텍처 발견으로 기록한다.
- **Iteration 2b direct fixed 6-1**: 임시 126일 long/21일 skip 후 되돌림.
  OOS stitched +7.15%, CAGR 1.48%, Sharpe 0.124, MDD -30.35%, Calmar 0.049;
  2020 +17.0%, 2021 +0.6%, 2022 -6.1%, 2023 +7.3%, 2024 -12.6% — 모든
  게이트 미달. Day 24 baseline 대비 MDD는 개선 방향이나 CAGR/Sharpe는 하락.
- 재사용 전 감사: fold winner의 tie threshold 준수, effective config와 momentum
  series fingerprint 기록, factor-computation 설정의 runtime-safe override 제거
  또는 후보별 factor 재계산 명시가 필요하다.
- 새 전략은 별도 경제적 정당화와 사전 등록이 필요하며, 2025–2026/향후
  prospective paper 기간은 보존하고 현재 OOS는 추가 튜닝하지 않는다. 향후
  MQ 유사 주장은 strict PIT 유니버스, DART filing-date 재무, 필요 시 PIT sector
  map, 5/5 folds, validated classification, 동일 cost/delisting 규칙,
  EXCLUDE_KOSPI_TOP_N=0, 모든 성과 게이트를 한 번의 사전 등록 실행에서 충족해야 한다.
