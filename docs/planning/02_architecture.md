# 아키텍처 설계 — KOSPI 200 모멘텀 + 품질

## 패키지 구조

```text
src/
├── k200_mq/                     # 새 전략 패키지
│   ├── __init__.py
│   ├── config.py                # K200MQConfig (전략 파라미터)
│   ├── main.py                  # CLI 진입점
│   ├── data/
│   │   ├── __init__.py
│   │   ├── dart_pit.py           # 로컬 OpenDART 제출일 provenance 계약
│   │   ├── krx_pit.py            # 인증된 KRX KOSPI 200 스냅샷 어댑터
│   │   ├── pit_universe.py       # 로컬 PIT 후보 importer와 검증
│   │   ├── provenance.py         # 유니버스/재무 provenance 계약
│   │   └── universe.py           # KOSPI 200 이력 및 proxy/PIT 원천 선택
│   ├── core/
│   │   ├── cache.py              # 공통 캐시
│   │   ├── data/loader.py        # 가격·재무·시장 데이터 로더
│   │   ├── factors/base.py       # 공통 팩터 인터페이스
│   │   ├── analysis/metrics.py   # 성과·비용·벤치마크 계산
│   │   └── reporting/report.py   # 보고서 생성
│   ├── factors/
│   │   ├── __init__.py
│   │   ├── base.py               # 공통 팩터 인터페이스
│   │   ├── momentum.py           # skipped-return v4
│   │   ├── quality.py            # ROE, DE, OpMargin, CashConv
│   │   └── regime.py             # KPI200 regime 필터
│   ├── strategies/
│   │   ├── __init__.py
│   │   └── momentum_quality.py   # 횡단면 점수화 및 선택
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── benchmark.py          # 벤치마크 계산
│   │   └── portfolio_engine.py   # 리밸런싱 중심 엔진
│   ├── analysis/
│   │   └── __init__.py
│   ├── reporting/
│   │   └── __init__.py
│   └── validation/
│       ├── prepared.py           # 준비 입력 및 strict PIT 보호 장치
│       └── runner.py             # expanding-window WF 실행
└── super_quality/                # 레거시 — v2.0-abandoned 이후 수정 금지

tests/
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
├── 05_status.md
└── 06_benchmark_and_cost_attribution.md
```

## 핵심 데이터 흐름

```text
[리밸런싱 일자]
    │
    ├── 1. 유니버스: get_kospi200_constituents(as_of_date) → 티커
    │         └─ PIT: 미래 정보 편향 방지
    │
    ├── 2. 가격 데이터: get_price_data(tickers, start-252d, end)
    │         └─ 모멘텀 계산용 룩백 구간
    │
    ├── 3. 팩터 (리밸런싱 일자별 횡단면):
    │    ├── momentum.py → skipped-return v4 → z-score 순위
    │    ├── quality.py  → ROE, DE, OpMargin, CashConv → z-score 순위
    │    └── regime.py   → KPI200 > MA(REGIME_MA_PERIOD) AND
    │                       20일 수익률 > REGIME_MIN_RETURN
    │
    ├── 4. 종합 점수: 0.5 × momentum_z + 0.5 × quality_z
    │         └─ config 가중치로 조정
    │
    ├── 5. 선택: 종합 점수 기준 Top N (N은 config에서 설정)
    │         └─ mcap 기준 상위 50개 제외 (KOSPI 50 희석)
    │
    ├── 6. 포트폴리오 구성: 동일 비중 또는 순위 가중
    │         └─ 섹터 노출 한도: 미지원/보류 (적용하지 않음)
    │
    └── 7. 일별: 시가평가, 활성 손절 확인(-15%), 일정에 따른 리밸런싱
```

## 데이터 원천과 PIT 경계

기본 경로는 기존 `proxy_current` 또는 `mcap_proxy` 유니버스를 사용하며, 이 기본값은
현재도 유지됩니다. `LOCAL_PIT_UNIVERSE_PATH`, `LOCAL_PIT_UNIVERSE_SOURCE_KIND`,
`LOCAL_PIT_UNIVERSE_MANIFEST`를 명시하면 `config`에서 `universe`와 `main`을 거쳐
로컬 PIT 원천을 선택하고 proxy/cache 경로를 사용하지 않습니다. 설정된 원천의 파일,
매니페스트, 날짜, 크기, 해시 또는 provenance 검증이 실패하면 즉시 중단(fail closed)
합니다.

인증된 KRX 어댑터는 `src/k200_mq/data/krx_pit.py`에 있으며 공식 KRX 로그인과
스냅샷 요청을 수행합니다. 두 날짜에 대한 라이브 스모크 테스트를 완료했지만, 원시
파일과 매니페스트는 로컬·미커밋 산출물입니다. 충분한 역사 범위의 KRX 파일을
운영 성과 근거에 연결하기 전까지는 이 테스트를 PIT 성과 근거로 볼 수 없습니다.

provenance 계약은 여러 스냅샷의 날짜별 원천, 원시 바이트 SHA-256, 시간대가 있는
타임스탬프, 행 수, 스냅샷 식별자, 재로딩 결과와 최종 정규화 프레임의 일치를
검증합니다. 로컬 OpenDART 계약은 존재하지만 원시 API/벌크 수집과 품질 팩터 기본
경로 연결은 아직 남아 있습니다.

## 팩터 설계 상세

### 모멘텀 팩터 (`factors/momentum.py`)

- **기본 공식**: skipped-return v4: `close[t-skip_days] / close[t-long_window] - 1`
  (기본 `close[t-42] / close[t-252] - 1`)
- **대체 공식**: 52주 고점 비율 — **미지원/보류**이며 순위에 사용하지 않음
- **정규화**: 리밸런싱 일자별 횡단면 z-score
- **제외 구간**: 마지막 2개월 (한국의 2개월 반전 특성 — Sim & Kim 2021)

### 품질 팩터 (`factors/quality.py`)

- **ROE**: 순이익 / 자본총계 (현재 정규화 재무 입력; PIT TTM 필터 없음)
- **Debt/Equity**: 총부채 / 자본총계
- **Operating Margin**: 영업이익 / 매출액 (현재 정규화 재무 입력; PIT TTM 필터 없음)
- **Cash Conversion**: 영업현금흐름 / 당기순이익 (현재 정규화 재무 입력; PIT TTM
  필터 없음)
- **TTM quarter filter**: `QUALITY_MIN_TTM_QUARTERS`는 비활성·미지원/보류
- **정규화**: 리밸런싱 일자별 횡단면 z-score
- **DART 계정 매핑**: 명시적 계정 코드 매핑은 보류. 현재는 정규화 로더 입력을
  사용하며 OpenDART 원시 수집과 품질 팩터 연결이 필요합니다.

### 레짐(Regime) 팩터 (`factors/regime.py`)

- **신호**: KPI200 종가 > `MA(REGIME_MA_PERIOD)` AND 20거래일 누적 수익률 >
  `REGIME_MIN_RETURN` (기본 0.0; 수익률 구간은 20일 고정)
- **참**: 전체 익스포저(100%)
- **거짓**: 50% 익스포저 (리밸런싱에도 반영)

### 종합 점수

```text
score = w_mom × mom_z + w_qual × qual_z
```

기본 가중치: w_mom = 0.5, w_qual = 0.5 (`config`에서 조정 가능)

## 엔진 설계 (PortfolioRebalanceEngine)

기존 `BacktestEngine`은 일일 루프와 단일 티커 신호 구조입니다. 새 전략에는 맞지
않으므로 리밸런싱 중심의 엔진을 사용합니다.

### PortfolioRebalanceEngine 구조

```python
class PortfolioRebalanceEngine:
    def run(price_data, index_data, factor_data, universe_data) -> BacktestResult:
        rebalance_dates = generate_rebalance_dates(config.REBALANCE_FREQ)

        for date in trading_days:
            # 일일: 시가평가, 손절 확인
            daily_mark_to_market(date)
            check_stop_losses(date)

            # 리밸런싱 일자: 포트폴리오 재구성
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

- **청산**: 리밸런싱에서 빠진 종목 및 enabled일 때 일일 trailing stop-loss
  (기본 -15%)
- **이익실현**: 없음 (모멘텀의 이익 실현을 제한하지 않음)
- **포지션 규모**: 동일 비중 또는 순위 가중 (`config`)
- **비용 모델**: 매수/매도 시 설정된 명시적 비용; ADV 시장 영향은 미지원/보류

## 구성

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `TOP_N` | 20 | 리밸런싱마다 선택하는 종목 수 |
| `REBALANCE_FREQ` | "M" | 리밸런싱 주기 (M=월, Q=분기) |
| `WEIGHT_MOMENTUM` | 0.5 | 모멘텀 가중치 |
| `WEIGHT_QUALITY` | 0.5 | 품질 가중치 |
| `ENABLE_STOP_LOSS` | true | `run`과 true-WF가 config/environment 또는 기본값으로 사용하는 trailing stop-loss 주문 생성 여부; true-WF CLI override는 없음 |
| `SL_STOP_LOSS` | -0.15 | 활성화 시 `-1.0 < value < 0.0`인 trailing 손절 기준 |
| `UNIVERSE_SIZE` | 200 | **미지원/보류** — 현재 유니버스 로더가 소비하지 않음 |
| `LOCAL_PIT_UNIVERSE_PATH` | "" | 설정 시 사용하는 로컬 PIT 유니버스 파일 경로; 미설정 시 proxy 로더 사용 |
| `LOCAL_PIT_UNIVERSE_SOURCE_KIND` | "" | 로컬 원천 형식 (`snapshots` 또는 `intervals`; 기본 `snapshots`) |
| `LOCAL_PIT_UNIVERSE_MANIFEST` | "" | 선택적 로컬 PIT 수집 매니페스트 경로 |
| `SECTOR_CAP` | 0.30 | **미지원/보류** — 현재 엔진에 적용하지 않음 |
| `MOMENTUM_WINDOW_LONG` | 252 | skipped return v4 순위 특성: `close[t-42] / close[t-252] - 1` (기본) |
| `MOMENTUM_WINDOW_SHORT` | 126 | **진단 전용** `momentum_6m` 표시; 순위/readiness/민감도에 사용하지 않음 |
| `MOMENTUM_SKIP_DAYS` | 42 | 마지막 2개월 제외 |
| `MAX_HOLDINGS` | 20 | 동시 보유 최대 종목 수 |
| `WEIGHT_METHOD` | "equal" | "equal" 또는 "rank_weighted" |
| `MIN_ADV_RATIO` | 0.01 | **미지원/보류** — 현재 엔진에 적용하지 않음 |

`MIN_CASH_RATIO`와 `MAX_HOLDINGS`는 현재 엔진에서 각각 최소 현금 버퍼와 동시
보유 수 상한으로 적용됩니다. `USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`,
`UNIVERSE_SIZE`는 현재 구성에 남아 있지만 52주 고점 보조 신호, TTM 분기 필터,
유니버스 크기 제어를 아직 구현하지 않습니다. 이 값들은 민감도 후보로 사용하지
않습니다. `MOMENTUM_WINDOW_SHORT`는 진단용 표시만 계산하며 민감도 또는
readiness/운영 파라미터로 취급하지 않습니다.

`SECTOR_CAP`과 `MIN_ADV_RATIO`는 현재 엔진 미구현 항목으로, CLI 플래그뿐 아니라
런타임 설정 채널에서도 비기본값을 명시적으로 거부합니다. `src/k200_mq/data/sector_pit.py`
계약 레이어는 향후 SECTOR_CAP 연결을 위한 PIT 섹터 맵 정규화/검증 스캐폴딩을 제공하며,
현재 실행 엔진에는 아직 연결되지 않았습니다.

`--enable-stop-loss`, `--disable-stop-loss`, `--stop-loss`는 `run` 명령의 CLI
override에만 노출됩니다. `true-walkforward`는 이 플래그들을 노출하지 않고
`ENABLE_STOP_LOSS`/`SL_STOP_LOSS`의 config/environment 값 또는 기본값을 사용합니다.

품질 종합 점수는 네 구성요소 z-score의 가중 평균이며 기본 가중치는 ROE 0.35 / DE
0.25 / operating margin 0.20 / cash conversion 0.20입니다. 가중치는
`QualityFactor`에서 음이 아닌 값과 양의 합을 검증한 뒤 합이 1이 되도록
정규화합니다. 누락된 구성요소는 종합 점수에서 중립(0)으로 처리합니다.

`BacktestConfig` (core)와 `K200MQConfig` (전략 전용)는 현재 분리되어 구현되어
있습니다.
