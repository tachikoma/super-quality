# 구현 계획 — KOSPI 200 Momentum + Quality

## 일정 요약

| 위상 | 일수 | 누적 | 마일스톤 |
|------|------|------|----------|
| **0: 준비** | 2 | 2 | 레거시 태깅, 새 패키지, 공통 인프라 추출 |
| **1: 데이터/유니버스** | 7 | 9 | 종속 이력, 선행 가격 (ADV 적용은 deferred) |
| **2: 팩터 모듈** | 10 | 19 | 모멘텀, 품질, 리짓 필터 |
| **3: 전략 & 엔진** | 14 | 33 | PortfolioRebalanceEngine, 전략 로직 |
| **4: 통합 & 검증** | 14 | 47 | 워크포워드, 비용 모델, 검증 |
| **5: 마무리** | 7 | 54 | 문서화, CLI, 테스트 보강 |

**총 예상: 5-8주 (35-54영업일)**

---

## Phase 0: 준비 (2일)

- [ ] `git tag v2.0-abandoned` — 기존 코드 frozen
- [ ] `src/k200_mq/` 패키지 구조 생성
- [ ] `src/k200_mq/core/` 에 공통 인프라 추출:
  - `data/cache.py` (reuse as-is or refactor)
  - `data/loader.py` (refactor: extract shared functions)
  - `analysis/metrics.py` (copy)
  - `reporting/report.py` (copy)
  - `factors/base.py` (copy)
  - `backtest/engine.py` (copy as reference, do not modify)
- [ ] `src/k200_mq/config.py` — `BacktestConfig` (core) + `K200MQConfig` (strategy-specific) 정의
- [ ] `.gitignore` 업데이트

### 산출물
- `src/k200_mq/` 패키지 생성
- 공통 인프라 `core/` 하위에 위치
- 기존 `src/super_quality/` → 레거시

---

## Phase 1: 데이터 & 유니버스 (7일)

### 1.1 KOSPI 200 종속 이력
- [ ] 데이터 소스 결정:
  - Option A: KRX `MKD30001` API (PDF/Excel 다운로드 → 파싱)
  - Option B: 프록시 — 매출액 기준 top 200 (각 리밸런싱일)
  - Option C: FinanceDataReader (현재 종속만, 과거는 없음)
  - **선택지 A를 우선 시도, 실패 시 B로 Fallback**
- [ ] `src/k200_mq/data/universe.py` — `get_kospi200_constituents(as_of: date) -> list[str]`
  - Point-in-time 반환
  - Fwd-looking bias 방지: as_of일 이전 종속만 사용
  - Caching: Parquet 파일로 캐시 (cache key: date range)

### 1.2 Price data 확장
- [ ] `get_price_data(tickers, start, end)` 확장 — 시작일 -252일 추가 fetch
- [ ] `compute_prices_with_lookback(tickers, start, end, lookback=252) -> tuple[DataFrame, DataFrame]`
  - 반환: (backtest range data, lookback range data)
- [ ] Cache key에 lookback window 포함 → 캐시 무효화 방지

### 1.3 시장 지수 데이터
- [ ] KPI200 (KOSPI 200 지수) 데이터 지원 추가
- [ ] KS11 (KOSPI composite) 지원 — 레짓 필터용
- [ ] `get_market_index(ticker, start, end)` 에 KPI200/KS11 옵션 추가

### 1.4 ADV (Average Daily Volume) — deferred/unsupported
- [ ] `compute_adv(price_data, window=20) -> DataFrame`
  - ticker × date 기준 20일 평균 거래대금
- [ ] 유동성 필터: 일평균거래대금 대비 포지션 크기 비율
  - 현재 helper가 있어도 portfolio engine에는 연결하지 않음

### 1.5 종속 이력 점검
- [ ] 2015-2024 기간 KOSPI 200 종속의 정확성 검증
- [ ] 레포지토리 내 샘플 JSON/Parquet 에 종속 스냅샷 저장

---

## Phase 2: 팩터 모듈 (10일)

### 2.1 Momentum Factor (`factors/momentum.py`)
- [x] `MomentumFactor.compute(data, long_window=252, skip_days=42)`:
      `close[t-skip_days] / close[t-long_window] - 1` skipped-return formula
  - default definition: `close[t-42] / close[t-252] - 1`
  - 교차섹셔널 z-score 정규화
- [ ] `YearHighFactor.compute(data)` — deferred as a ranking input:
  - 52주 고점 비율: `(close - 52w_low) / (52w_high - 52w_low)`
  - `USE_52WEEK_HIGH` is currently unsupported/inert
- [ ] unit test: synthetic data → 알려진 출력
- [ ] factor_data merge (ticker × date)

### 2.2 Quality Factor (`factors/quality.py`)
- [x] ROE 계산: `net_income / total_equity` (current normalized input)
- [x] Debt/Equity 계산: `total_debt / total_equity`
- [x] Operating Margin 계산: `operating_income / revenue` (현재 normalized 입력; TTM 필터 없음)
- [x] Cash Conversion 계산: `operating_cf / net_income` (현재 normalized 입력; TTM 필터 없음)
- [x] 각 팩터 cross-sectional z-score 정규화
- [ ] **DART Account Mapping 테이블**: 각 팩터별 계정 코드 매핑 (deferred)
  - 기존 `_find_account()` 3-pass 매칭과 별도
- [ ] NaN 처리: TTM 4분기 미달 시 필터링 — deferred; `QUALITY_MIN_TTM_QUARTERS` is inert
- [ ] unit test

### 2.3 Regime Factor (`factors/regime.py`)
- [ ] `RegimeFactor.compute(index_data, as_of_date)`:
  - `KPI200 > MA(REGIME_MA_PERIOD)` AND 20일 수익률 > `REGIME_MIN_RETURN` → True
    (이중 조건; return threshold default 0.0)
  - 둘 다 True → full exposure, else → 50% exposure
- [ ] daily 시계열로 반환 (ticker × date 아님, date only)
- [ ] unit test

### 2.4 팩터 통합 파이프라인
- [ ] `factor_pipeline.py` 또는 `main.py` 내 팩터 계산 로직:
  - 리밸런싱일에만 팩터 계산 (daily 아님)
  - 결과를 `factor_data` DataFrame에 병합

---

## Phase 3: 전략 & 엔진 (14일)

### 3.1 Strategy Module (`strategies/momentum_quality.py`)
- [ ] `MomentumQualityStrategy.evaluate(portfolio_data) -> list[ticker, weight]`
  - 입력: 현재 보유 포지션 + 스크리닝 대상 유니버스 + 팩터 점수
  - 로직:
    1. 유니버스 스크리닝 (제외 리스트; ADV 유동성 필터는 deferred)
    2. 모멘텀 + 품질 z-score 합산 → composite score
    3. 종속 이력 내 TOP N 선택
    4. 섹터별 노출 캡 적용 — deferred/unsupported; 현재 적용하지 않음
    5. Equal weight 또는 rank-weighted 반환
- [ ] unit test

### 3.2 PortfolioRebalanceEngine (`backtest/portfolio_engine.py`)
**일일 루프 구조:**
```
for each trading day:
  1. Mark-to-market (current positions value update)
  2. Check stop-loss (daily, -15% threshold)
  3. If rebalance day:
     a. Get universe constituents (point-in-time)
     b. Compute factor scores (momentum + quality)
     c. Apply regime filter (50% exposure reduction if bearish)
     d. Rank & select top N
     e. Compute target weights (equal / rank-weighted)
     f. Generate orders (buy/sell to target)
     g. Execute orders with cost model
  4. Record snapshot (date, cash, holdings_value, nav, num_positions)
```

**주요 설계 결정:**
- 리밸런싱 스케줄러: 월말/분기말 business day
- 거래 비용: 명시적 0.23% + 슬리피지
- 시장 영향: ADV 비율 기반 모델 — deferred/unsupported; 현재 explicit cost만 적용
- Stop-loss: `run` 명령에서 enabled일 때 일일 trailing stop-loss, 기본 -15%;
  disabled도 지원. `true-walkforward`는 stop-loss CLI 플래그를 노출하지 않고
  config/environment 또는 기본값을 사용

### 3.3 기존 BacktestEngine 보존
- 기존 `engine.py` 수정 금지
- 새 엔진과 기존 엔진은 별도
- 필요 시 `BacktestEngine` 테스트 유지용으로 유지

### 3.4 리스크 관리
- 섹터 노출 캡: **unsupported/deferred** (PIT-safe sector mapping과 함께 구현 예정)
- `MAX_HOLDINGS`: **unsupported/deferred**; 현재 TOP_N과 별도의 보유 수 제한 없음
- `MIN_CASH_RATIO`: **unsupported/deferred**; 현재 현금 버퍼로 적용하지 않음
- `MAX_POSITION_WEIGHT`: 설정은 남아 있지만 별도 risk-contract 검증 전까지
  sensitivity 차원으로 사용하지 않음
- correlation filter: **unsupported/deferred**

---

## Phase 4: 통합 & 검증 (14일)

### 4.1 CLI 진입점
- [ ] `src/k200_mq/main.py` — `k200-mq run` CLI
- [ ] argparse: `--start`, `--end`, `--output`, `--dart-api-key`, `--rebalance-freq`, `--top-n`
- [x] `--no-cache` 및 `--rebalance-lookback`는 unsupported/deferred로 명확히 거부
- [ ] 기존 `super-quality` CLI 와 별도 실행

### 4.2 독립 Subperiod Robustness Test (현재 구현)
현재 CLI의 `robustness` 명령은 다음 고정 기간을 서로 독립적으로
백테스트합니다: 2014–2016, 2017–2018, 2019–2020, 2021–2022,
2023–현재. 학습 구간이나 파라미터 피팅은 없으므로 이는
**independent subperiod robustness test**이지 walk-forward CV가 아닙니다.

**출력:**
- subperiod별 성과 지표 (CAGR, Sharpe, Max DD, Win Rate)
- 유효 subperiod만 사용한 기하 평균 수익률과 worst MDD
- 비어 있거나 유효하지 않은 subperiod의 명시적 상태

### 4.3 True expanding-window Walk-Forward
현재 pure core는 다음의 인접한 고정 fold 일정을 사용합니다.
- Training window: 2015–2019 (expanding)
- Test window: 2020, 2021, 2022, 2023, 2024
- Purge/embargo: 현재는 deferred/not applicable. 후보 신호가 과거 데이터만
  사용하는 fixed-signal 후보이며, forward label이나 overlapping outcome을
  사용한 피팅을 하지 않으므로 pure core에 purge/embargo를 구현하지 않았습니다.
- 파라미터 피팅은 training 구간 내부에만 제한
- train/test 성과 차이와 fold 간 안정성 분석

Pure orchestration runner는 두 pass로 동작합니다. 먼저 모든 fold의 train 후보
평가와 선택을 완료하고 JSON 경계에서 동결한 뒤, 두 번째 pass에서 선택된 후보의
test evaluator를 호출합니다. 따라서 test callback의 외부 상태 변이가 이후 train
평가에 영향을 주지 않습니다. `true-walkforward` CLI는 준비된 입력을 각
train/test interval로 잘라 이 runner에 전달하며, 준비된 거래일이 있으면 exact
OOS coverage를 요구합니다. 실제 provenance validator 결과가 전달되기 전까지 분류는
`mechanical_expanding_walk_forward_non_pit`으로 고정합니다. 임의의 PIT flag나
synthetic evidence로 `validated_expanding_walk_forward_pit`를 만들지 않습니다.

향후 forward label 또는 overlapping outcome을 사용하는 후보 피팅을 추가하면
그 시점에 purge/embargo를 포함하도록 fold schedule을 다시 설계해야 합니다.

### 4.4 트랜잭션 비용 모델
- [ ] 명시적 비용: 수수료 0.015% + 세금 0.20%(매도) + 슬리피지 0.10%
- [ ] 시장 영향 비용: ADV 기반 동적 슬리피지 (deferred/unsupported; 현재 미적용)
- [ ] Net of cost alpha 산출
- [ ] Turnover 비용 귀속 분석

### 4.5 파라미터 민감도 분석
| 파라미터 | 범위 |
|----------|------|
| MOMENTUM_WINDOW_LONG | 252 (현재 공식; 변경 시 fresh WF 필요) |
| QUALITY_WEIGHT | 0.0, 0.25, 0.50, 0.75, 1.0 |
| TOP_N | 10, 20, 30, 40 |
| REBALANCE_FREQ | weekly, monthly, quarterly |
| STOP_LOSS | -10%, -15%, -20%, none |
| REGIME_FILTER | on, off |

`SECTOR_CAP`, `MIN_ADV_RATIO`, `MIN_CASH_RATIO`, `MAX_HOLDINGS`,
`UNIVERSE_SIZE`, `USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`는 구현되지
않았거나 inert이므로 현재 candidate library와 sensitivity 실행에서 제외한다.
`MOMENTUM_WINDOW_SHORT`는 `momentum_6m` diagnostic-only이며 sensitivity 또는
운영 파라미터 주장이 아니므로 제외한다.

### 4.6 레짓 필터 교차 분석
- Regime ON vs OFF 성과 비교
- Bear market (2022)에서의 성과
- Sideways market (2017-2019)에서의 성과
- Trend market (2020)에서의 성과

### 4.7 서바이버십 바이어스 테스트
- A) Current constituents backtested (point-in-time 미사용)
- B) Point-in-time constituents backtested
- 두 결과 차이 분석 → 편향 정량화

---

## Phase 5: 마무리 (7일)

- [ ] README.md (새 전략 문서)
- [ ] AGENTS.md (k200_mq/ 관련 내용 추가)
- [ ] Test 보강 (118 → 목표 200+)
- [ ] Performance benchmark (2015-2024 최종 백테스트)
- [ ] 코드 리뷰 (oracle 또는 peer)
- [ ] v0.1.0 release tag

---

## 의존성 순서 (Critical Path)

```
Phase 0 → Phase 1.1 (universe) → Phase 1.2 (price extended) → Phase 1.3-1.5
  → Phase 2.1 (momentum) → Phase 2.2 (quality) → Phase 2.3 (regime)
    → Phase 3.1 (strategy) → Phase 3.2 (engine)
      → Phase 4.1-4.6 (integration & validation)
        → Phase 5 (finalize)
```

Phase 1.1이 병목 — KOSPI 200 종속 이력 데이터소스 확인이 불가하면 Phase 1.2로 진행.
