# 아키텍처 설계 — KOSPI 200 Momentum + Quality

## 패키지 구조

```
src/
├── k200_mq/                     # 새 전략 패키지
│   ├── __init__.py
│   ├── config.py                # K200MQConfig (전략 파라미터)
│   ├── main.py                  # CLI 진입점 (k200-mq run)
│   ├── data/
│   │   ├── __init__.py
│   │   ├── cache.py             # 기존 캐시 재사용 (심볼릭 링크 또는 동일)
│   │   ├── loader.py            # 확장: KOSPI 200 종속, 선행 가격 (ADV 적용은 deferred)
│   │   └── universe.py          # 신규: 점포인트임 KOSPI 200 종속 이력
│   ├── factors/
│   │   ├── __init__.py
│   │   ├── base.py              # 기존 재사용
│   │   ├── momentum.py          # 신규: skipped-return v4 (52주 고점 적용은 deferred)
│   │   ├── quality.py           # 신규: ROE, DE, OpMargin, CashConv
│   │   └── regime.py            # 신규: KPI200 > MA(period) + 20일 수익률 리짓 필터
│   ├── strategies/
│   │   ├── __init__.py
│   │   └── momentum_quality.py  # 신규: 크로스섹셔널 스코어링 + 선택
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py            # 기존 재사용 (참조용)
│   │   └── portfolio_engine.py  # 신규: 리밸런싱 중심 엔진
│   ├── analysis/
│   │   └── metrics.py           # 기존 재사용
│   └── reporting/
│       └── report.py            # 기존 재사용
├── super_quality/               # 레거시 — v2.0-abandoned 이후 수정 금지
│   └── ...
└── tests/
    ├── test_momentum.py
    ├── test_quality.py
    ├── test_regime.py
    ├── test_portfolio_engine.py
    ├── test_universe.py
    └── test_integration.py

docs/planning/
├── 01_strategy_pivot.md
├── 02_architecture.md
├── 03_implementation_plan.md
├── 04_backtest_spec.md
└── 05_status.md
```

## 핵심 데이터 흐름

```
[Rebalance Date]
    │
    ├── 1. Universe: get_kospi200_constituents(as_of_date) → tickers
    │         └─ Point-in-time: forward-looking bias 방지
    │
    ├── 2. Price Data: get_price_data(tickers, start-252d, end)
    │         └─ Lookback window for momentum calculation
    │
    ├── 3. Factors (cross-sectional per rebalance date):
    │    ├── momentum.py → skipped-return v4 → z-score rank
    │    ├── quality.py  → ROE, DE, OpMargin, CashConv → z-score rank
    │    └── regime.py   → KPI200 > MA(REGIME_MA_PERIOD) AND 20-day return > REGIME_MIN_RETURN
    │
    ├── 4. Composite Score: 0.5 × momentum_z + 0.5 × quality_z
    │    └─ (weighted by config)
    │
    ├── 5. Selection: Top N by composite score (N from config)
    │    └─ Exclude top 50 by mcap (KOSPI 50 dilution)
    │
    ├── 6. Portfolio Construction: equal weight or rank-weighted
    │    └─ Sector exposure cap: unsupported/deferred (not applied)
    │
    └── 7. Daily: mark-to-market, enabled stop-loss check (-15%), rebalance on schedule
```

## 팩터 설계 상세

### Momentum Factor (`factors/momentum.py`)
- **Primary**: skipped-return v4: `close[t-skip_days] / close[t-long_window] - 1`
  (default `close[t-42] / close[t-252] - 1`)
- **Fallback**: 52-week high percentage — **unsupported/deferred**; not used in ranking
- **Normalization**: Cross-sectional z-score per rebalance date
- **Skip**: 마지막 2개월 (한국 2개월 반전 특성 — Sim & Kim 2021)

### Quality Factor (`factors/quality.py`)
- **ROE**: 순이익 / 자본총계 (현재 normalized 재무 입력; PIT TTM 필터 없음)
- **Debt/Equity**: 총부채 / 자본총계
- **Operating Margin**: 영업이익 / 매출액 (현재 normalized 재무 입력; PIT TTM 필터 없음)
- **Cash Conversion**: 영업현금흐름 / 당기순이익 (현재 normalized 재무 입력; PIT TTM 필터 없음)
- **TTM quarter filter**: `QUALITY_MIN_TTM_QUARTERS` is inert and unsupported/deferred
- **Normalization**: Cross-sectional z-score per rebalance date
- **DART Account Mapping**: 명시적 계정 코드 매핑은 deferred; 현재 normalized loader 입력 사용

### Regime Factor (`factors/regime.py`)
- **Signal**: KPI200 종가 > `MA(REGIME_MA_PERIOD)` AND 20거래일 누적 수익률 >
  `REGIME_MIN_RETURN` (default 0.0; return window remains 20 days)
- **True**: 전 exposures (100%)
- **False**: 50% exposure (리밸런싱 시에도 반영)

### Composite Score
```
score = w_mom × mom_z + w_qual × qual_z
```
기본 가중치: w_mom = 0.5, w_qual = 0.5 (config에서 조정 가능)

## 엔진 설계 (PortfolioRebalanceEngine)

기존 `BacktestEngine`는 daily loop + single-ticker signal 구조. 새로운 전략에는 맞지 않음. 새 엔진 생성.

### PortfolioRebalanceEngine 구조
```python
class PortfolioRebalanceEngine:
    def run(price_data, index_data, factor_data, universe_data) -> BacktestResult:
        rebalance_dates = generate_rebalance_dates(config.REBALANCE_FREQ)
        
        for date in trading_days:
            # 일일: mark-to-market, stop-loss 체크 (daily)
            daily_mark_to_market(date)
            check_stop_losses(date)
            
            # 리밸런싱 일: 포트폴리오 재구성
            if date in rebalance_dates:
                constituents = get_universe(date)
                scores = compute_scores(constituents, factor_data, date)
                selected = select_top_n(scores, config.TOP_N)
                weights = compute_weights(selected, config.WEIGHT_METHOD)
                target_positions = compute_positions(weights, nav)
                orders = generate_orders(current_positions, target_positions)
                execute_orders(orders, date)
```

### 주요 설계 결정
- **Exit**: 리밸런싱에서 빠진 종목 + enabled일 때 일일 trailing stop-loss (-15% 기본)
- **Take profit**: 없음 (모멘텀은 이익실현 제한)
- **Position sizing**: Equal weight 또는 rank-weighted (config)
- **Cost model**: 매수/매도 시 configured explicit costs; ADV 시장 영향은 unsupported/deferred

## 구성

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `TOP_N` | 20 | 리밸런싱당 선택 종목 수 |
| `REBALANCE_FREQ` | "M" | 리밸런싱 주기 (M=월, Q=분기) |
| `WEIGHT_MOMENTUM` | 0.5 | 모멘텀 가중치 |
| `WEIGHT_QUALITY` | 0.5 | 품질 가중치 |
| `ENABLE_STOP_LOSS` | true | `run`과 true-WF가 config/environment 또는 기본값으로 사용하는 trailing stop-loss 주문 생성 여부; true-WF CLI override는 없음 |
| `SL_STOP_LOSS` | -0.15 | enabled일 때 `-1.0 < value < 0.0`인 trailing 손절 기준 |
| `UNIVERSE_SIZE` | 200 | **unsupported/deferred** — 현재 유니버스 로더가 소비하지 않음 |
| `SECTOR_CAP` | 0.30 | **unsupported/deferred** — 현재 엔진에 적용하지 않음 |
| `MOMENTUM_WINDOW_LONG` | 252 | skipped return v4 ranking feature: `close[t-42] / close[t-252] - 1` (default) |
| `MOMENTUM_WINDOW_SHORT` | 126 | **diagnostic-only** `momentum_6m` display; not ranking/readiness/sensitivity |
| `MOMENTUM_SKIP` | 42 | 마지막 2개월 skip |
| `MAX_HOLDINGS` | 20 | **unsupported/deferred** — 현재 엔진에 적용하지 않음 |
| `WEIGHT_METHOD` | "equal" | "equal" 또는 "rank_weighted" |
| `MIN_ADV_RATIO` | 0.01 | **unsupported/deferred** — 현재 엔진에 적용하지 않음 |

`MIN_CASH_RATIO`, `USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`도 현재
구성에는 남아 있지만 각각 현금 버퍼, 52주 고점 랭킹 보조 신호, TTM 분기
필터를 구현하지 않는다. `MAX_HOLDINGS`도 동시 보유 수를 제한하지 않는다.
`UNIVERSE_SIZE`도 현재 유니버스 로더의 runtime consumer가 없다. 이 값들은
sensitivity candidate로 사용하지 않는다. `MOMENTUM_WINDOW_SHORT`는 진단용 표시만
계산하며 sensitivity 또는 readiness/운영 파라미터로 취급하지 않는다.

`--enable-stop-loss`, `--disable-stop-loss`, `--stop-loss`는 `run` 명령의 CLI
override에만 노출된다. `true-walkforward`는 이 플래그들을 노출하지 않고
`ENABLE_STOP_LOSS`/`SL_STOP_LOSS`의 config/environment 값 또는 기본값을 사용한다.

Quality composite는 네 component z-score의 가중 평균이며, 기본 가중치는
ROE 0.35 / DE 0.25 / operating margin 0.20 / cash conversion 0.20이다.
가중치는 `QualityFactor`에서 nonnegative 및 positive-sum을 검증한 뒤 합이
1이 되도록 정규화한다. 누락 component는 composite에서 중립(0)으로
처리한다.

`BacktestConfig` (core)와 `K200MQConfig` (strategy-specific) 분리 설계 예정.
