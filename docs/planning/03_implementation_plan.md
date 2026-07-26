# 구현 계획 — KOSPI 200 Momentum + Quality

## 일정 요약

| 위상 | 일수 | 누적 | 마일스톤 |
|------|------|------|----------|
| **0: 준비** | 2 | 2 | 레거시 태깅, 새 패키지, 공통 인프라 추출 |
| **1: 데이터/유니버스** | 7 | 9 | 종속 이력, ADV, 선행 가격 |
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

### 1.4 ADV (Average Daily Volume)
- [ ] `compute_adv(price_data, window=20) -> DataFrame`
  - ticker × date 기준 20일 평균 거래대금
- [ ] 유동성 필터: 일평균거래대금 대비 포지션 크기 비율

### 1.5 종속 이력 점검
- [ ] 2015-2024 기간 KOSPI 200 종속의 정확성 검증
- [ ] 레포지토리 내 샘플 JSON/Parquet 에 종속 스냅샷 저장

---

## Phase 2: 팩터 모듈 (10일)

### 2.1 Momentum Factor (`factors/momentum.py`)
- [ ] `MomentumFactor.compute(data, lookback=(252, 147), skip=42)`:
  - `returns = (price[t-lookback[1]] / price[t-lookback[0]]) - 1`
  - 교차섹셔널 z-score 정규화
- [ ] `YearHighFactor.compute(data)`:
  - 52주 고점 비율: `(close - 52w_low) / (52w_high - 52w_low)`
  - 대안 시그널로 사용
- [ ] unit test: synthetic data → 알려진 출력
- [ ] factor_data merge (ticker × date)

### 2.2 Quality Factor (`factors/quality.py`)
- [ ] ROE 계산: `net_income / total_equity` (TTM)
- [ ] Debt/Equity 계산: `total_debt / total_equity`
- [ ] Operating Margin 계산: `operating_income / revenue` (TTM)
- [ ] Cash Conversion 계산: `operating_cf / net_income` (TTM)
- [ ] 각 팩터 cross-sectional z-score 정규화
- [ ] **DART Account Mapping 테이블**: 각 팩터별 계정 코드 매핑
  - 기존 `_find_account()` 3-pass 매칭과 별도
- [ ] NaN 처리: TTM 4분기 미달 시 NaN (보수적 처리 유지)
- [ ] unit test

### 2.3 Regime Factor (`factors/regime.py`)
- [ ] `RegimeFactor.compute(index_data, as_of_date)`:
  - KOSPI 200 종가 > MA200 → True
  - 20일 수익률 > 0 → True (이중 조건)
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
    1. 유니버스 스크리닝 (유동성, 제외 리스트)
    2. 모멘텀 + 품질 z-score 합산 → composite score
    3. 종속 이력 내 TOP N 선택
    4. 섹션별 노출 캡 적용 (30% per sector)
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
- 시장 영향: ADV 비율 기반 모델 (소형주 = 더 큰 슬리피지)
- Stop-loss: 일일 -15%, trailing 옵션 없음 (모멘텀이 트렌드를 타야 하므로)

### 3.3 기존 BacktestEngine 보존
- 기존 `engine.py` 수정 금지
- 새 엔진과 기존 엔진은 별도
- 필요 시 `BacktestEngine` 테스트 유지용으로 유지

### 3.4 리스크 관리
- 섹션 노출 캡: 30% per GICS Level 1
- 단일 포지션 최대: 10% NAV
- 최소 현금 버퍼: 5%
- correlation filter: 같은 섹터 3종목 이상 시 최고 스코어만 유지

---

## Phase 4: 통합 & 검증 (14일)

### 4.1 CLI 진입점
- [ ] `src/k200_mq/main.py` — `k200-mq run` CLI
- [ ] argparse: `--start`, `--end`, `--output`, `--dart-api-key`, `--no-cache`, `--rebalance-freq`, `--top-n`
- [ ] 기존 `super-quality` CLI 와 별도 실행

### 4.2 Walk-Forward 검증
**설계:**
- Training window: 2015-2019 (expanding)
- Test window: 2020, 2021, 2022, 2023, 2024
- Purge: 각 fold 간 3개월 gap
- Embargo: 각 fold 후 1개월 gap
- 교차검증: 5-fold expanding window

**출력:**
- fold별 성과 지표 (CAGR, Sharpe, Max DD, Win Rate)
- fold 간 안정성 분석 (std dev of metrics)
- 과적합 진단 (train vs test 성과 차이)

### 4.3 트랜잭션 비용 모델
- [ ] 명시적 비용: 수수료 0.015% + 세금 0.20%(매도) + 슬리피지 0.10%
- [ ] 시장 영향 비용: ADV 기반 동적 슬리피지
- [ ] Net of cost alpha 산출
- [ ] Turnover 비용 귀속 분석

### 4.4 파라미터 민감도 분석
| 파라미터 | 범위 |
|----------|------|
| MOMENTUM_WINDOW | (252, 21), (252, 126), (126, 63), (252, 147) |
| QUALITY_WEIGHT | 0.0, 0.25, 0.50, 0.75, 1.0 |
| TOP_N | 10, 20, 30, 40 |
| REBALANCE_FREQ | weekly, monthly, quarterly |
| STOP_LOSS | -10%, -15%, -20%, none |
| REGIME_FILTER | on, off |

### 4.5 레짓 필터 교차 분석
- Regime ON vs OFF 성과 비교
- Bear market (2022)에서의 성과
- Sideways market (2017-2019)에서의 성과
- Trend market (2020)에서의 성과

### 4.6 서바이버십 바이어스 테스트
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