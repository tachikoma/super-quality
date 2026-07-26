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
│   │   ├── loader.py            # 확장: KOSPI 200 종속, ADV, 선행 가격
│   │   └── universe.py          # 신규: 점포인트임 KOSPI 200 종속 이력
│   ├── factors/
│   │   ├── __init__.py
│   │   ├── base.py              # 기존 재사용
│   │   ├── momentum.py          # 신규: 12-7개월 수익률, 52주 고점
│   │   ├── quality.py           # 신규: ROE, DE, OpMargin, CashConv
│   │   └── regime.py            # 신규: KOSPI 200 MA200 리짓 필터
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
    │    ├── momentum.py → 12-7 month return → z-score rank
    │    ├── quality.py  → ROE, DE, OpMargin, CashConv → z-score rank
    │    └── regime.py   → KPI200 > MA200? (binary per rebalance)
    │
    ├── 4. Composite Score: 0.5 × momentum_z + 0.5 × quality_z
    │    └─ (weighted by config)
    │
    ├── 5. Selection: Top N by composite score (N from config)
    │    └─ Exclude top 50 by mcap (KOSPI 50 dilution)
    │
    ├── 6. Portfolio Construction: equal weight or rank-weighted
    │    └─ Sector exposure cap: 30%
    │
    └── 7. Daily: mark-to-market, stop-loss check (-15%), rebalance on schedule
```

## 팩터 설계 상세

### Momentum Factor (`factors/momentum.py`)
- **Primary**: 12-7 month return (252 - 42 trading days)
- **Fallback**: 52-week high percentage
- **Normalization**: Cross-sectional z-score per rebalance date
- **Skip**: 마지막 2개월 (한국 2개월 반전 특성 — Sim & Kim 2021)

### Quality Factor (`factors/quality.py`)
- **ROE**: 순이익 / 자본총계 (TTM)
- **Debt/Equity**: 총부채 / 자본총계
- **Operating Margin**: 영업이익 / 매출액 (TTM)
- **Cash Conversion**: 영업현금흐름 / 당기순이익 (TTM)
- **Normalization**: Cross-sectional z-score per rebalance date
- **DART Account Mapping**: 각 팩터별 명시적 계정 코드 매핑 테이블

### Regime Factor (`factors/regime.py`)
- **Signal**: KOSPI 200 종가 > MA200 AND 20일 수익률 > 0
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
- **Exit**: 리밸런싱에서 빠진 종목 + 일일 stop-loss (-15% config)
- **Take profit**: 없음 (모멘텀은 이익실현 제한)
- **Position sizing**: Equal weight 또는 rank-weighted (config)
- **Cost model**: 매수/매도 시 0.23% explicit + 시장 영향 슬리피지

## 구성

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `TOP_N` | 20 | 리밸런싱당 선택 종목 수 |
| `REBALANCE_FREQ` | "M" | 리밸런싱 주기 (M=월, Q=분기) |
| `W_EFFORT` | 0.5 | 모멘텀 가중치 |
| `W_QUALITY` | 0.5 | 품질 가중치 |
| `SL_STOP_LOSS` | -0.15 | 일일 손절 기준 (-15%) |
| `SECTOR_CAP` | 0.30 | 섹별 최대 노출 (30%) |
| `MOMENTUM_WINDOW` | (252, 147) | 12-7개월 (거래일 기준) |
| `MOMENTUM_SKIP` | 42 | 마지막 2개월 skip |
| `MAX_HOLDINGS` | 20 | 최대 동시 보유 |
| `POSITION_SIZE` | "equal" | "equal" | "rank_weighted" |
| `ADV_RATIO_THRESHOLD` | 0.01 | 최소 유동성 비율 |

`BacktestConfig` (core)와 `K200MQConfig` (strategy-specific) 분리 설계 예정.