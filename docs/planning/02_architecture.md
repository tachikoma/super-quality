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
│   │   ├── account_mapping.py    # 공용 OpenDART account→wide 매핑
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
│   │   ├── quality.py            # ROE, DE, Gross-Margin Proxy, CashConv
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
    │    ├── quality.py  → ROE, DE, Gross-Margin Proxy, CashConv → z-score 순위
    │    │         └─ 입력: 정규화 로더 또는 로컬 DART pivot(account_mapping)
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
    │    ├─ 섹터 노출 한도: ENABLE_SECTOR_CAP + PIT 섹터 맵 조건에서만 적용
    │    ├─ ADV 유동성 필터: ENABLE_ADV_FILTER + trailing ADV turnover 조건에서 적용
    │    └─ 상관관계 제약: ENABLE_CORRELATION_FILTER + trailing return 이력 조건에서 적용
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
검증합니다. 로컬 OpenDART 계약은 존재하며, 2026-08-06부터 공용 account→wide 매핑
(`data/account_mapping.py`)과 long→wide pivot(`dart_pit.pivot_financial_facts_to_wide`)을
통해 품질 팩터 기본 경로에 연결되었습니다. 남은 작업은 원시 API/벌크 수집으로
역사 데이터를 확보하는 것입니다.

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
- **Gross-Margin Proxy**: `max(revenue - cogs, 0) / revenue`; 매출액과 매출원가에서
  파생한 floored gross-profit / gross-margin proxy입니다. true operating income이나
  operating margin이 아니며, canonical 출력/가중치 명칭은 `gross_margin_proxy`입니다
  (PIT TTM 필터 없음). 기존 `OPMARGIN` 설정 별칭만 호환성을 위해 deprecated로 유지합니다.
- **Cash Conversion**: 영업현금흐름 / 당기순이익 (현재 정규화 재무 입력; PIT TTM
  필터 없음)
- **TTM quarter filter**: `QUALITY_MIN_TTM_QUARTERS`는 비활성·미지원/보류
- **정규화**: 리밸런싱 일자별 횡단면 z-score
- **DART 계정 매핑**: `ACCOUNT_COLUMN_MAPPING`(`src/k200_mq/data/account_mapping.py`)이
  OpenDART 계정명/계정코드를 wide 6컬럼(revenue, cogs, net_income, operating_cf,
  total_assets, total_equity)으로 매핑하고, `dart_pit.pivot_financial_facts_to_wide`가
  long facts를 wide로 피벗해 로컬 DART 입력이 품질 팩터로 직접 흐릅니다.
  정규화 로더(API 경로)도 동일 매핑을 공유합니다.
- 위 six-fact 중 하나라도 누락된 원천 row는 quality-scored 대상이 아닙니다. 최종
  factor merge에서 품질 결측을 허용하는 경우 quality는 neutral-fill(0)되며, 이
  neutral-fill은 원천 품질 커버리지의 증거가 아닙니다.

### 레짐(Regime) 팩터 (`factors/regime.py`)

- **신호**: KPI200 종가 > `MA(REGIME_MA_PERIOD)` AND 20거래일 누적 수익률 >
  `REGIME_MIN_RETURN` (기본 0.0; 수익률 구간은 20일 고정)
- **참**: 전체 익스포저(100%)
- **거짓**: 50% 익스포저 (리밸런싱에도 반영; `REGIME_REDUCTION`으로 축소 비율 조정 가능 — v5 라이브러리에서 WFA가 선택)

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
| `LOCAL_PIT_UNIVERSE_PATH` | "" | 로컬 PIT 유니버스 파일 경로; strict 모드에서는 필수 |
| `LOCAL_PIT_UNIVERSE_SOURCE_KIND` | "" | 로컬 원천 형식 (`snapshots` 또는 `intervals`; 기본 `snapshots`) |
| `LOCAL_PIT_UNIVERSE_MANIFEST` | "" | 로컬 PIT 수집 매니페스트 경로; strict 모드에서는 필수 |
| `LOCAL_PIT_SECTOR_PATH` | "" | 선택적 로컬 PIT 섹터 맵 구간 파일 경로; 설정 시 준비 경로에서 검증/스냅샷 생성 |
| `SECTOR_CAP` | 0.30 | `ENABLE_SECTOR_CAP=True`에서 섹터별 최대 노출 상한 |
| `ENABLE_SECTOR_CAP` | false | 로컬 PIT 섹터 맵 검증/전체 커버리지 조건에서만 섹터 캡 적용 |
| `MAX_PAIR_CORRELATION` | 0.90 | `ENABLE_CORRELATION_FILTER=True`에서 허용되는 최대 pairwise 상관계수 |
| `CORRELATION_LOOKBACK_DAYS` | 60 | 상관계수 계산에 사용하는 trailing 수익률 룩백 일수 |
| `ENABLE_CORRELATION_FILTER` | false | 리밸런싱 후보 내 고상관 페어 제한 적용 여부 |
| `MOMENTUM_WINDOW_LONG` | 252 | skipped return v4 순위 특성: `close[t-42] / close[t-252] - 1` (기본) |
| `MOMENTUM_WINDOW_SHORT` | 126 | **진단 전용** `momentum_6m` 표시; 순위/readiness/민감도에 사용하지 않음 |
| `MOMENTUM_SKIP_DAYS` | 42 | 마지막 2개월 제외 |
| `MAX_HOLDINGS` | 20 | 동시 보유 최대 종목 수 |
| `WEIGHT_METHOD` | "equal" | "equal" 또는 "rank_weighted" |
| `MIN_ADV_RATIO` | 0.01 | `ENABLE_ADV_FILTER=True`에서 최소 ADV turnover 비율 임계값 |
| `ADV_LOOKBACK_DAYS` | 20 | ADV turnover 계산에 사용하는 trailing 룩백 일수 |
| `ENABLE_ADV_FILTER` | false | ADV turnover 기반 유동성 필터 적용 여부 |
| `DAILY_LOSS_LIMIT_PCT` | 0.0 | opt-in 일일 NAV 대비 최대 손실 비율 (0=비활성) |
| `MONTHLY_LOSS_LIMIT_PCT` | 0.0 | opt-in 월간(22거래일) NAV 대비 최대 손실 비율 (0=비활성) |
| `DRAWDOWN_HALT_PCT` | 0.0 | opt-in 최고 대비 드로다운 halt 임계값 (0=비활성) |
| `DRAWDOWN_HALT_COOLDOWN_DAYS` | 5 | halt 해소 후 재개 전 최소 대기 거래일 |
| `ENABLE_DELISTING_DETECTION` | true | 보유 종목 상장폐지/거래정지 감지 (기본 활성) |
| `DELISTING_ZERO_VOLUME_DAYS` | 3 | 거래량 0 연속 일수로 상장폐지 판정 |
| `DELISTING_STALE_PRICE_DAYS` | 5 | 가격 0 연속 일수로 상장폐지 판정 |

`MIN_CASH_RATIO`와 `MAX_HOLDINGS`는 현재 엔진에서 각각 최소 현금 버퍼와 동시
보유 수 상한으로 적용됩니다. `USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`,
`UNIVERSE_SIZE`는 현재 구성에 남아 있지만 52주 고점 보조 신호, TTM 분기 필터,
유니버스 크기 제어를 아직 구현하지 않습니다. 이 값들은 민감도 후보로 사용하지
않습니다. `MOMENTUM_WINDOW_SHORT`는 진단용 표시만 계산하며 민감도 또는
readiness/운영 파라미터로 취급하지 않습니다.

`src/k200_mq/data/sector_pit.py`
계약 레이어는 PIT 섹터 맵 정규화/검증과 as-of 스냅샷 생성을 제공하며, 실행 엔진은
`ENABLE_SECTOR_CAP=True`에서 해당 준비 산출물의 검증/전체 커버리지를 요구합니다.
ADV 유동성 필터는 준비 아티팩트 없이 엔진에서 계산되며, 리밸런싱 신호일까지의
trailing ADV turnover(`volume*close/mcap`) 평균이 `MIN_ADV_RATIO` 이상인 후보만
유지합니다.
상관관계 제약은 준비 아티팩트 없이 엔진에서 계산되며, 리밸런싱 신호일까지의 close
수익률 이력을 사용해 pairwise 상관계수를 계산한 뒤 greedy 방식으로 후보를
제한합니다.

`--enable-stop-loss`, `--disable-stop-loss`, `--stop-loss`는 `run` 명령의 CLI
override에만 노출됩니다. `true-walkforward`는 이 플래그들을 노출하지 않고
`ENABLE_STOP_LOSS`/`SL_STOP_LOSS`의 config/environment 값 또는 기본값을 사용합니다.

품질 종합 점수는 네 구성요소 z-score의 가중 평균이며 기본 가중치는 ROE 0.35 / DE
0.25 / gross-margin proxy 0.20 / cash conversion 0.20입니다. 가중치는
`QualityFactor`에서 음이 아닌 값과 양의 합을 검증한 뒤 합이 1이 되도록
정규화합니다. 누락된 구성요소는 종합 점수에서 중립(0)으로 처리합니다.

`BacktestConfig` (core)와 `K200MQConfig` (전략 전용)는 현재 분리되어 구현되어
있습니다.
