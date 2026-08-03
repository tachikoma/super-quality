# 작업 진행 상황

## 마지막 업데이트: 2026-08-03

## 프로젝트 상태: Phase 4 검증 진행 중, 엔진·캐시·체결 시점·benchmark/cost attribution 수정 완료, **PIT 데이터 검증 전**

## 최근 업데이트: 2026-08-03 (true-walkforward result-integrity 수정)

`uv run python -m k200_mq.main true-walkforward --output <dir>`가 true
expanding-window 실행 경로입니다. `<dir>/true_walkforward/`의
`selection_and_folds.json`, `summary.csv`, `oos_returns.csv`에 fold 선택,
secret-free effective config/hash, git state, preparation context, exact OOS
coverage 결과를 남깁니다. 이 실행은 historical PIT validator가 연결되지 않은
`mechanical_expanding_walk_forward_non_pit`이며, interval slicing은 mechanical
future-row 방지일 뿐 PIT 보증이 아닙니다.

## 현재 모멘텀 공식과 결과 비교 경계

현재 공식 버전은 `k200mq-momentum-skipped-return-v4`입니다. 정의는
`close[t-skip_days] / close[t-long_window] - 1`이며, 기본값에서는
`close[t-42] / close[t-252] - 1`입니다. 이 의미론적 변경 전에 생성된 성과·진단은
모두 `obsolete_pre_momentum_v4` / non-current diagnostic입니다. 따라서 보존된
**+26.97%**, 이전 **+207%/+245-era** 결과, pre-v4 independent robustness 및
WF 수치는 현재 공식 결과와 비교하지 않습니다. 감사 기록은 삭제하지 않고
보존하며, 공식이나 다른 factor 의미론이 변경될 때마다 fresh true-WF run이 필요합니다.

## Fresh v4 기계적 True-WF 진단 실행 (2026-08-03; current formula, non-PIT)

실행 명령:

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward --output /tmp/k200mq_true_wf_v4_no_dart
```

- 모멘텀 공식: v4 skipped return `close[t-42] / close[t-252] - 1`
- 분류: `mechanical_expanding_walk_forward_non_pit`
- DART API key가 unset되어 quality factor는 비활성화되었습니다. 이 결과는
  **momentum-only diagnostic**입니다.
- 5개 fold 모두 유효; fold 1–4는 `TOP_N_10`, fold 5는 `BASE` 선택
- OOS 1,231개 포인트 (2020–2024 test 기간)
- Stitched 누적 수익률: **+4.0408%** | Stitched MDD: **-32.0408%**
- Fold별 test 수익률: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**

산출물은 `/tmp/k200mq_true_wf_v4_no_dart/true_walkforward/` 아래의
`selection_and_folds.json`, `summary.csv`, `oos_returns.csv`입니다. 저장소에 복사하거나
커밋하지 않습니다. v4 공식 모멘텀을 사용했지만 KOSPI 200 유니버스와 ranking은
non-PIT proxy이고, DART unset으로 financial data가 missing하며 품질 데이터 경로는
non-PIT이므로 **validated performance evidence가 아닙니다.** canonical/production
성과로 해석하지 않습니다.

## 이전 pre-v4 기계적 True-WF 진단 실행 (2026-08-03; `obsolete_pre_momentum_v4`)

실행 명령:
`uv run python -m k200_mq.main true-walkforward --output /tmp/k200mq_true_wf`

- 분류: `mechanical_expanding_walk_forward_non_pit`
- 5개 fold 모두 유효; 모든 fold의 선택 후보는 `TOP_N_10`
- OOS 1,231개 포인트 (2020–2024 test 기간)
- Stitched 누적 수익률: **+44.6426%** | Stitched MDD: **-32.5935%**
- Fold별 test 수익률: 2020 **+60.4528%**, 2021 **-2.0225%**, 2022
  **-20.2042%**, 2023 **+19.2139%**, 2024 **-3.2801%**

산출물은 `/tmp/k200mq_true_wf/true_walkforward/` 아래의
`selection_and_folds.json`, `summary.csv`, `oos_returns.csv`입니다. 이 경로의
산출물은 저장소에 복사하거나 커밋하지 않습니다.

> **중요 경고:** 현재 KOSPI 200 membership/ranking은 non-PIT proxy이며,
> normalized DART 재무 데이터는 `non_pit_fiscal_period`입니다. 따라서 이번
> 실행은 validated performance evidence가 아니며 canonical/production 결과로
> 해석하지 않습니다. 또한 v4 formula correction 이전 실행인 non-current diagnostic이며,
> 현재 공식과 비교하려면 fresh true-WF 실행이 필요합니다.

## Benchmark / cost attribution 구현 완료 (2026-08-03)

- Benchmark는 `REGIME_FILTER_ENABLED`와 독립적으로 준비하며, 입력 지수의
  실제 configured ticker와 `price_return` provenance를 manifest에 기록합니다.
- 측정 구간 밖의 지수 close는 benchmark 수익률 계산 전에 제외하고, 첫
  구간 close에는 이전 구간 수익률을 붙이지 않습니다.
- 체결된 buy/sell별 commission, slippage, sell-only tax와 누적 비용을
  trade log, execution stats, snapshot에서 상호 대조할 수 있습니다.
- Benchmark는 **price return**이며 배당·분배금을 포함하지 않습니다. 따라서
  total return benchmark가 아닙니다.
- 전체 `pytest`의 legacy `src/super_quality/` 실패는 frozen legacy 코드와
  관련된 별도 문제이며 K200MQ benchmark/cost attribution 검증과 무관합니다.

## 중요: 이전 백테스트 결과 모두 무효 (`obsolete_pre_momentum_v4`)

**2026-07-28: 치명적 cash 변이 버그 발견.**

`_rebalance()`가 `cash`를 float 파라미터로 받아 로컬에서 `cash -= cost`로 수정했지만,
이 변경이 호출자(`run()`)에 반영되지 않아 **cash가 항상 INITIAL_CAPITAL(100M)으로 고정**되어 있었습니다.
NAV가 `cash + holdings`로 계산되므로 cash를 double-counting하여 수익률이 약 2배 부풀려졌습니다.

**P1-5에서 버그 수정**: `_rebalance()`와 `_check_stop_losses()`가 모두 `(positions, cash)` 튜플을 반환하도록 변경.
`run()`에서 반환값을 destructure하여 cash 변경이 올바르게 전파됩니다.

**모든 이전 백테스트 결과(+207%, +244%, 이전에 WF CV로 표기한 +245%)는 cash 변이 버그와
v4 formula correction 이전 실행이라는 이유로 무효입니다.** 이 결과와 pre-v4
robustness/WF 결과는 현재 공식 결과와 비교하지 않는 non-current 진단입니다.

## 수정 후 백테스트 결과 (2015-2024; `obsolete_pre_momentum_v4`)

```
초기 자본: 100,000,000원 → 최종 자본: 114,171,995원
총 수익률: +14.17%
연간 수익률: +2.82%
연간 변동성: 16.85%
Sharpe 비율: 0.165
최대 낙폭: -37.51%
총 거래: 502건 | 승률: 37.6%
평균 보유일: 38.9일 | 평균 보유 종목: 8.1개
출구 사유: stop_loss 388건, rebalance 114건
```

> **해석**: 위 수치는 v4 formula correction 이전의 `obsolete_pre_momentum_v4` non-current
> diagnostic입니다. 전략이 유의미한 alpha를 생성하지 못함. +14.17%/10년은
> KOSPI 200 벤치마크 대비 저조. 리짓 필터가 bullish 36.8%로 시장 참여율이 낮고,
> trailing stop-loss가 77%의 거래를 중단시킴. 현재 공식과 비교하지 말고 fresh
> run 뒤에 해석해야 합니다.

## Phase 2 수정 후 후보 실행 (2015-2024; `obsolete_pre_momentum_v4`)

```
체결: signal일 종가 → 다음 거래일 시가
첫 준비 완료 리밸런싱: 2015-03-31
최종 자본: 126,970,159원
총 수익률: +26.97%
연간 변동성: 14.07%
Sharpe 비율: 0.244
최대 낙폭: -28.72%
요약 거래: 1,179건
```

> **주의**: 이 결과(+26.97%)는 v4 formula correction 전의
> `obsolete_pre_momentum_v4` mechanical candidate입니다. `outputs_k200mq/run_manifest.json`에
> 기록된 비-PIT 유니버스, 비공시일 기준 재무, ADV/sector cap 미적용 제한 때문에
> 전략 성과로 확정하지 않습니다. 현재 공식 결과와 비교하지 말고 fresh run이
> 필요합니다.

## 구성요소 Ablation 후보 결과 (2015-2024; `obsolete_pre_momentum_v4`)

| 구성 | 총수익률 | Sharpe | MDD | 판정 |
|------|---------:|-------:|----:|------|
| Momentum + Quality | +26.97% | 0.244 | -28.72% | 후보 기준 |
| Momentum only | +48.63% | 0.350 | -30.92% | Quality 추가 효과 없음 신호 |
| Quality only | -13.21% | -0.066 | -27.70% | 품질 데이터 시점 문제로 참고용 |

> **주의**: 세 결과 모두 v4 formula correction 이전의 `obsolete_pre_momentum_v4`
> non-current diagnostics이며, 비-PIT 유니버스와 filing-date 미반영 재무 데이터
> 제한도 있습니다. 현재 공식과 비교하거나 Quality 제거·regime 변경을 최종 결정하기
> 전에 factor 의미론 변경 후 fresh ablation/WF/robustness run을 수행해야 합니다.

---

## Phase 0: 준비
- [x] `.omo/` 폴더 삭제 (2026-07-26)
- [x] `outputs_2023_2024/` 삭제 (2026-07-26)
- [x] 레거시 태깅: `git tag v2.0-abandoned` 생성됨 ✓
- [x] `docs/planning/` 디렉토리 생성
- [x] `01_strategy_pivot.md` 작성
- [x] `02_architecture.md` 작성
- [x] `03_implementation_plan.md` 작성
- [x] `04_backtest_spec.md` 작성
- [x] `05_status.md` ← 현재 파일 (이 문서)
- [x] `src/k200_mq/` 패키지 구조 생성
- [x] `src/k200_mq/core/` 공통 인프라 추출
- [x] 레거시 `src/super_quality/` frozen (수정 불가)

---

## Phase 1: 데이터 & 유니버스
- [x] KOSPI 200 종속 이력 소스 확인 (프록시: mcap top 200 from KRX listings)
- [x] `universe.py` 구현 (get_kospi200_constituents, as-of-keyed proxy caching; PIT 아님)
- [x] `loader.py` 확장 (get_price_data_with_lookback, compute_adv helper, get_kospi200_price_data; ADV 적용은 deferred)
- [x] KOSPI 200 (KS11) + KOSPI 51 (KOSPI200) index data 지원
- [x] 종속 이력 검증 (프록시 fallback 구현)

---

## Phase 2: 팩터 모듈
- [x] `momentum.py` (skipped-return v4; 기본 `close[t-42]/close[t-252]-1`; 52주 고점 보조 기능은 deferred)
- [x] `quality.py` (ROE, DE, OpMargin, CashConv, 복합 z-score; TTM quarter filter는 inert)
- [x] `regime.py` (`KPI200 > MA(REGIME_MA_PERIOD)` + 20 trading-day return > `REGIME_MIN_RETURN`, default 0.0)
- [x] Unit tests (모든 팩터)

---

## Phase 3: 전략 & 엔진
- [x] `momentum_quality.py` (cross-sectional scoring, TOP-N, KOSPI50 제외; sector cap은 deferred)
- [x] `portfolio_engine.py` (리밸런싱 루프, stop-loss, 매수/매도 실행)
- [x] `main.py` CLI skeleton (`k200-mq run`)
- [x] Unit tests (모든 팩터, 전략, 엔진)
- [x] 기존 `engine.py` 수정 금지 확인

---

## Phase 4: 통합 & 검증
파이프라인 완성 — 실제 데이터 로드 → 팩터 계산 → 백테스트 → 결과 저장

- [x] `_run_pipeline()` 구현 — universe → price → factors → strategy → engine
- [x] `main.py` CLI (`k200-mq run`) — 전체 파이프라인 연결 완료
- [x] 재무 데이터 → 일별 변환 (`_convert_financial_to_daily`)
- [x] 결과 저장 (portfolio_snapshots, trade_log, daily_returns CSV)
- [x] 요약 통계 출력 (총수익률, Sharpe, 최대낙폭, 승률)
- [x] 최초 백테스트 실행 (2020-2024, +207.06% 수익률 — **cash 변이 버그와 v4 formula correction 이전으로 무효인 `obsolete_pre_momentum_v4` 진단**)
- [x] P0-1: 리짓 필터 엔진 내 적용 (regime_scale → target_value at rebalance)
- [x] P0-2: 리밸런싱 일자 통합 (universe_data as_of 사용)
- [x] P0-3: 품질 팩터 커버리지 개선 (0 분모 → 1 대체 + coverage 로깅)
- [x] Independent subperiod robustness test (5개 고정 독립 기간 — **cash 변이 버그와 v4 formula correction 이전으로 무효인 `obsolete_pre_momentum_v4` 과거 실행**)
- [x] `robustness` CLI 및 `walkforward` 호환 alias를 independent subperiod robustness로 명확화
- [x] P1-5: trailing stop-loss + cash 변이 버그 픽스 (peak_price 추적, cash 반환)
- [x] Phase 2: cache ticker/coverage, warmup, regime date, next-open execution, target-weight resize
- [x] Phase 2 회귀 테스트 (전체 pytest 189개 통과)
- [x] run manifest 저장 및 기계적 후보 실행
- [x] PIT 유니버스 / filing-date 재무 데이터 계약의 bounded validation
- [x] Phase 1: pure expanding-window WF core (folds, conservative candidates, train-only selector)
- [x] True-WF orchestration layer wired to the `true-walkforward` CLI
- [x] WF runner train/test two-pass isolation, strict flags, fold/date/version validation
- [x] Exact prepared test-date coverage; truncated OOS folds are invalid
- [x] Interval slicing for price/factor/index/universe/regime inputs
- [x] Test trade metrics use the interval `trade_log`
- [x] Secret-free effective config/hash, git state, and preparation context in WF artifacts
- [x] Invalid WF runs save diagnostics and exit nonzero
- [x] Pure WF classification remains mechanical non-PIT; validated PIT promotion deferred until actual validator outputs are wired
- [ ] KRX 과거 유효일자별 구성종목 파일 및 raw DART filing metadata 확보·연결
- [x] True expanding-window WF CV 실행 연결·재실행 (현재 robustness test와 별개)
- [ ] 파라미터 민감도 분석
- [ ] 레짓 필터 교차 분석
- [ ] 서바이버십 바이어스 테스트
- [ ] 스트레스 테스트
- [x] Turnover / cost attribution (filled-trade totals, snapshot reconciliation)
- [x] Sensitivity 전 semantic safety fixes: skipped-return momentum v4 formula,
  configurable quality weights, regime return threshold, explicit stop-loss
  enable/disable and active-domain threshold validation
- [ ] Deferred/unsupported settings remain out of sensitivity candidates:
  `SECTOR_CAP`, `MIN_ADV_RATIO`, `MIN_CASH_RATIO`, `UNIVERSE_SIZE`, `USE_52WEEK_HIGH`,
  `MAX_HOLDINGS`, `QUALITY_MIN_TTM_QUARTERS` (TTM filtering)
- [x] `MOMENTUM_WINDOW_SHORT`는 `momentum_6m` diagnostic-only이며 ranking,
  readiness, sensitivity/operational claim에서 제외
- [x] `--no-cache` 및 `--rebalance-lookback`은 unsupported/deferred로 명확히 거부
- [x] `--enable-stop-loss`/`--disable-stop-loss`/`--stop-loss`는 `run` 전용이며
  `true-walkforward`는 config/default를 사용

---

## Phase 5: 마무리
- [x] README_K200MQ.md (새 전략 문서)
- [x] AGENTS.md 업데이트
- [x] README.md (새 전략 docs 섹션)
- [x] Test 보강 (전체 pytest 189개 통과, PIT provenance/config strict-mode 및 robustness tests 추가)
- [x] 기계적 후보 백테스트 (2015-2024, 성과 확정 전)
- [ ] 코드 리뷰
- [ ] v0.1.0 release tag

---

## 의사결정 로그

| 날짜 | 결정 | 근거 |
|------|------|------|
| 2026-07-25 | Super Quality 2.0 폐기 | 10년 백테스트 모든 결과 음수 |
| 2026-07-25 | 모멘텀 전략 고려 | Oracle 초기 제안 |
| 2026-07-25 | 모멘텀 근거 검토 요청 | 사용자 |
| 2026-07-25 | `v2.0-abandoned` 태그 권장 | Oracle 검토 |
| 2026-07-25 | 새 패키지 `k200_mq/` 분리 | Oracle 검토 — 심볼릭 링크 비권장 |
| 2026-07-26 | 일정 15일 → 6주로 수정 | Oracle 비용 분석 |
| 2026-07-26 | `.omo/` 삭제 | 레거시 산출물 정리 |
| 2026-07-26 | `outputs_2023_2024/` 삭제 | 레거시 백테스트 산출물 정리 |
| 2026-07-26 | 계획 문서 5개 작성 | 작업 추적 및 검토용 |
| 2026-07-26 | Phase 4 파이프라인 완성 | 실제 데이터 → 팩터 → 엔진 → 결과 |
| 2026-07-26 | 최초 백테스트 실행 (2020-2024; `obsolete_pre_momentum_v4`) | +207.06% (검증 전, P0 이슈 있음) |
| 2026-07-26 | Oracle 리뷰 (14개 이슈) | P0-3개, P1-3개, P2-4개, P3-4개 식별 |
| 2026-07-28 | P0-1 해결: 리짓 필터 엔진 내 적용 | regime_scale → target_value at rebalance |
| 2026-07-28 | P0-2 해결: 리밸런싱 일자 통합 | universe_data as_of 사용 |
| 2026-07-28 | P0-3 해결: 품질 커버리지 개선 | 0 분모 → 1 대체 + coverage 로깅 |
| 2026-07-28 | 독립 subperiod test 완료 (5개 기간) | 당시 WF CV로 잘못 표기됨; 모든 결과는 cash 버그로 무효 |
| 2026-07-28 | **치명적 cash 변이 버그 발견** | `_rebalance()`의 cash가 local copy, `run()`에 반영 안 됨 |
| 2026-07-28 | P1-5 해결: trailing stop-loss + cash 버그 픽스 | peak_price 추적, `(positions, cash)` 튜플 반환 |
| 2026-07-28 | **이전 모든 결과 무효 선언 (`obsolete_pre_momentum_v4`)** | cash 변이 버그 및 v4 formula correction 이전으로 +207%, +244%, 당시 WF CV 표기 결과 모두 현재 공식과 비교 금지 |
| 2026-07-28 | 수정 후 백테스트: +14.17%/10년, Sharpe 0.165 | 전략 유의미한 alpha 없음, 근본적 재검토 필요
| 2026-08-02 | Phase 2 수정 완료 | next-open 체결, regime warmup, cache coverage, target-weight, readiness gate |
| 2026-08-02 | Phase 2 후보 실행 (`obsolete_pre_momentum_v4`) | +26.97%, Sharpe 0.244 — non-current mechanical diagnostic, 현재 공식과 비교 금지 |
| 2026-08-03 | Momentum 12-2 semantics correction (`k200mq-momentum-skipped-return-v4`) | `close[t-42] / close[t-252] - 1`; 모든 pre-v4 결과 obsolete, fresh true-WF required |

---

## 리스크 로그

| 리스크 | 심각도 | 완화 방안 | 상태 |
|--------|--------|-----------|------|
| KOSPI 200 종속 이력 소스 부재 | **높음** | 프록시(mcap top 200)로 Fallback | 미해결 |
| 모멘텀 크래시 리스크 | **높음** | Long-only + 리짓 필터로 부분 완화 | 미해결 |
| 과적합 (true expanding-window walk-forward 미검증) | **높음** | 독립 subperiod test는 대체 검증이 아님 | 계획 중 |
| 엔진 리모델링 과대 산정 | 중간 | 기존 BacktestEngine 보존, 별도 PortfolioRebalanceEngine | 계획됨 |
| DART 계정 매핑 실패 | 중간 | 3-pass 기존 매칭 유지 + 새 팩터용 매핑 테이블 추가 | 계획됨 |
| 한국 모멘텀 효과 크기 작음 | 중 | 포터블 1.34%/월 목표, net cost 고려 시 1.0% 이상 필요 | 미해결 |
| 반도체 집중 리스크 | **높음** | sector cap is unsupported/deferred; obtain PIT-safe sector mapping | 미해결 |
| ~~P0-1: 리짓 필터가 백테스트 후에만 적용됨~~ | ~~치명적~~ | ~~엔진 내 리밸런싱 시 적용~~ | **해결** (engine._rebalance) |
| ~~P0-2: 리밸런싱 일자 생성 불일치~~ | ~~치명적~~ | ~~universe ↔ engine 간 일자 생성 통합~~ | **해결** (universe_data as_of) |
| ~~P0-3: 품질 팩터 커버리지 9.5%~~ | ~~치명적~~ | ~~모든 티커 일별 변환, z-score 완화~~ | **해결** (0→1 대체, 로깅) |
| ~~P1-4: 리밸런싱이 add/remove만 함 (weight drift)~~ | ~~높음~~ | 목표 비중 resize 구현 | **해결** |
| ~~P1-5: 손절이 진입가 기준 (trailing 아님)~~ | ~~높음~~ | peak_price 추적, cash 반환 | **해결** (trailing stop + cash fix) |
| ~~cash 변이 버그: cash가 로컬 copy로 수정 손실~~ | **치명적** | `_rebalance()`, `_check_stop_losses()` → `(positions, cash)` 반환 | **해결** |
| 전략 alpha 검증 불가 | **치명적** | PIT 유니버스·공시일 재무·benchmark 검증 | 미해결 |
| 실행 모델/입력 데이터 변경으로 과거 결과 비비교 가능 | **높음** | manifest와 canonical 규칙 고정 | 진행 중 |
| P1-6: 리짓 조건이 너무 엄격 (36.8% bullish) | 높음 | 이중 조건 parameter sensitivity 또는 3-state | 미해결 |

---

## 다음 단계

**cash, 체결 시점, cache coverage, regime warmup, target-weight resize 문제를 수정했습니다.**

**경고: 현재 결과는 PIT 유니버스와 filing-date 재무 문제 때문에 아직 전략 성과가 아닙니다.**

현재 파이프라인은 FinanceDataReader의 현재 KOSPI200 목록(`proxy_current`) 또는
현재 리스팅 시가총액 fallback(`mcap_proxy`)만 제공하므로 PIT 유니버스를 만들지
않습니다. 현재 정규화 DART 데이터는 filing/publication date를 버리기 때문에
품질 데이터 모드는 `non_pit_fiscal_period`입니다. 기본 탐색 실행은 계속되지만
이 provenance와 제한은 `run_manifest.json`에 남습니다. `--strict-pit` 또는
`STRICT_PIT_VALIDATION=true`는 이 두 계약이 충족되지 않으면 팩터 계산 전에
실패합니다. 레거시 유니버스 캐시에서 구조화된 source/effective-date
contract/fingerprint가 없으면 `legacy_proxy_unknown`으로 분류합니다. Strict
모드에서는 현재 시가총액으로 수행하는 `EXCLUDE_KOSPI_TOP_N`도 0이어야 하며,
공시 timestamp 또는 명시적 cutoff 계약이 없는 재무 데이터도 거부합니다. 다음
데이터 요구사항은 KRX historical constituent files와 raw DART filing metadata를
실제 가용일 계산에 연결하는 것입니다.

**다음 우선순위:**
1. PIT KOSPI 200 유니버스와 공시일 기준 재무 데이터 확보 또는 proxy 한계 명시
2. KOSPI 200 benchmark와 비용 attribution의 PIT 데이터 범위·성과 해석 검증
3. true expanding-window WF 실행 결과를 PIT 데이터 계약으로 재검증 (현재 독립 subperiod robustness와 구분)
4. Momentum/Quality/Regime/Stop-loss ablation (factor semantic change 후 fresh rerun)
5. 그 후에만 regime·window·N 파라미터 재검토

### Semantic safety boundary (2026-08-03)

- Momentum ranking uses `k200mq-momentum-skipped-return-v4`, the versioned formula
  `close[t-skip_days] / close[t-long_window] - 1` (default
  `close[t-42] / close[t-252] - 1`); the exposed short return is diagnostic only.
- Quality component weights are explicit, nonnegative, positive-sum inputs and
  are normalized by `QualityFactor`. Missing components remain neutral.
- `REGIME_MIN_RETURN` is the bullish 20-trading-day cumulative-return threshold;
  `0.0` preserves the prior behavior and the return window remains 20 days.
- Stop-loss defaults to enabled at `-15%`; disabled mode creates no stop orders.
  Enabled thresholds require `-1.0 < SL_STOP_LOSS < 0.0`.
- `QUALITY_MIN_TTM_QUARTERS` is intentionally inert because PIT TTM quarter
  filtering is not implemented. The settings listed above are configured for
  compatibility/documentation only and are not sensitivity dimensions.
- `EXCLUDE_MANAGEMENT`, `EXCLUDE_INVESTMENT_NOTICE`, `EXCLUDE_PREFERRED`, and
  `EXCLUDE_ETF_ETN` are unsupported/inert with no runtime consumers; their fields
  remain only for compatibility and are not sensitivity dimensions.
- Any pre-v4 robustness, WF, or ablation output remains an
  `obsolete_pre_momentum_v4` non-current diagnostic and requires a fresh
  true-WF run after this factor semantic change.
