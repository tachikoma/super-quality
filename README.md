# Super Quality / KOSPI 200 Momentum + Quality

| 전략 | 상태 | 태그 |
|------|------|------|
| Super Quality 2.0 (KOSDAQ 소형주 밸류+품질) | **ABANDONED** | `v2.0-abandoned` |
| KOSPI 200 Momentum + Quality | 베타 (Beta), mechanical non-PIT diagnostics only | — |

**Super Quality 2.0**은 강환국 스타일의 한국 주식 퀀트 백테스팅 시스템입니다. 10년(2015-2024) 백테스트 결과 어떤 파라미터 조합으로도 양수 수익을 달성하지 못해 2026-07-25에 전략을 폐기했습니다.

**KOSPI 200 Momentum + Quality**는 폐기된 전략의 인프라(data pipeline, backtest engine, factor framework, reporting)를 재사용하여 KOSPI 200 대형주 중심의 모멘텀+품질 전략으로 새 출발하는 프로젝트입니다.

> **Current evidence boundary:** The current v4 true-WF result is a
> momentum-only mechanical non-PIT diagnostic, not validated performance
> evidence. With DART unset it reports **+4.0408% stitched return**,
> **-32.0408% stitched MDD**, and **1,231 OOS points** (2020-2024).
> Historical PIT constituents and filing-date financial data are not connected
> yet. See [docs/planning/05_status.md](docs/planning/05_status.md) for the
> canonical status and obsolete-result boundary.

## 전략 개요

Super Quality 2.0은 여덟 가지 조건(A-H)으로 KOSPI 및 KOSDAQ 종목을 스크리닝합니다:

- **가치 (Value)**
  - **A**: PBR 하위 20% (저평가)
  - **B**: 순자산 양수 (PBR > 0)
- **품질 (Quality)**
  - **C**: 5개월 전 유상증자 없음
  - **D**: 현재 유상증자 없음
  - **E**: 최근 당기순이익 양수
  - **F**: 최근 영업현금흐름 양수
- **소형주**: **G** — 시가총액 하위 40%
- **시장 타이밍**: **H** — KOSDAQ 지수(KQ11)가 3/5/10일 이동평균 중 하나 이상보다 높음 (전체 시장 공통)

**우선순위 점수**: GP/A 백분위 + 개인 순매수 공급 백분위 (내림차순). 8개 조건(A-H)을 모두 통과한 종목만 대상.

**포지션 규칙**: 최대 20개 동시 보유, 포지션당 NAV의 10%, 매수 지정가 = 전일 종가 × 0.99, 최대 5거래일 보유.

**매도 조건** (매일 확인, 첫 매치 우선):
1. 손절: 진입가 대비 -7%
2. 시장 타이밍 이탈: KOSDAQ 지수 < 3MA AND < 5MA
3. 만기: 5거래일 도달

## 설치

### 사전 요구사항

- Python 3.12+
- [DART API 키](https://opendart.fss.or.kr) — K200MQ 품질 데이터 사용 시 필요; 미설정/불가 시 momentum-only/non-PIT 모드로 계속 실행

### 설정

```bash
# 저장소 클론
git clone <repo-url>
cd super-quality

# uv로 의존성 설치
uv sync

# DART API 키 설정
echo 'DART_API_KEY=your_key_here' > .env
```

## 사용법

### Super Quality 2.0 (LEGACY — deprecated)

```bash
# .env 파일에서 DART_API_KEY 읽기
uv run super-quality run

# 날짜 범위와 API 키 명시적 지정
uv run super-quality run --start 2015-01-01 --end 2024-12-31 --dart-api-key YOUR_KEY
```

### KOSPI 200 Momentum + Quality (Beta; diagnostics only)

```bash
# 전체 백테스트 실행
uv run python -m k200_mq.main run

# 날짜 범위와 파라미터 지정
uv run python -m k200_mq.main run \
    --start 2015-01-01 --end 2024-12-31 \
    --top-n 20 --rebalance-freq M \
    --weight-momentum 0.50 --weight-quality 0.50
```

### 독립 subperiod robustness test

```bash
# 고정된 독립 기간별 견고성 테스트
uv run python -m k200_mq.main robustness

# 기존 walkforward 명령은 호환 alias
uv run python -m k200_mq.main walkforward
```

현재 명령은 학습/파라미터 피팅이 없는 independent subperiod robustness
test이며 true expanding-window walk-forward CV가 아닙니다. True WF는 다음처럼
별도 실행합니다.

```bash
uv run python -m k200_mq.main true-walkforward --output outputs_k200mq
```

`outputs_k200mq/true_walkforward/`에는 `selection_and_folds.json`,
`summary.csv`, `oos_returns.csv`가 저장됩니다. Selection artifact와 summary에는
secret-free base runtime config, fold별 effective merged candidate config/hash,
git state, preparation manifest context가 포함됩니다. 준비된 가격 거래일이
있을 때 OOS 날짜는 fold의 예상 test 거래일과 정확히 일치해야 하고, truncated
결과는 invalid로 저장된 뒤 CLI가 nonzero로 종료합니다. 이 실행은 모든 fold의
train 선택을 먼저 동결한 뒤 test 평가를 수행하며 현재 결과는
`mechanical_expanding_walk_forward_non_pit`만 허용합니다. 이는 future rows를
interval adapter에서 제거하는 **기계적 non-PIT 보호**이지 historical PIT
유니버스나 filing-date provenance를 만들어 주는 기능은 아닙니다.
준비된 거래일 달력이 없는 pure-runner 호출은 exact 날짜 대신 구조적·non-empty
검사만 사용합니다.

현재 v4 no-DART 실행은 `mechanical_expanding_walk_forward_non_pit`로 분류됩니다.
Quality가 비활성화된 momentum-only 진단이며, proxy universe/ranking과
non-PIT financial path 때문에 validated performance evidence가 아닙니다.

### 출력 파일

K200MQ 출력 파일은 `--output` (기본값: `outputs_k200mq/`) 디렉토리에 저장됩니다:

| 파일 | 설명 |
|------|------|
| `portfolio_snapshots.csv` | 일별 포트폴리오 가치, 현금, 보유종목, NAV (데이터가 있을 때) |
| `trade_log.csv` | 모든 거래 내역 (진입/종료일, 손익, 보유일, 종료 사유; 거래가 있을 때) |
| `daily_returns.csv` | 일별 포트폴리오 수익률 (데이터가 있을 때) |
| `metrics.json` | 성과 지표 및 actual-fill 비용 attribution |
| `benchmark_returns.csv` | 설정된 KPI200 **가격수익률** 벤치마크 (total return 아님) |
| `run_manifest.json` | 설정, 데이터 provenance, 비용/벤치마크 및 알려진 제한사항 |
| `subperiod_robustness_summary.csv` | 독립 subperiod robustness 결과 요약 |
| `true_walkforward/selection_and_folds.json` | expanding-WF 선택, fold 결과 및 provenance |
| `true_walkforward/summary.csv` | fold별 OOS 지표와 config/git/preparation provenance |
| `true_walkforward/oos_returns.csv` | exact-coverage 검사를 통과한 stitched OOS 수익률 |

### 현재 deferred / unsupported settings

PIT historical universe, filing-date financials, strict PIT WF, PIT
sensitivity, and stress tests are pending. ADV impact/liquidity execution,
sector caps, `MAX_HOLDINGS`, `MIN_CASH_RATIO`, `UNIVERSE_SIZE`,
`USE_52WEEK_HIGH`, and `QUALITY_MIN_TTM_QUARTERS` are unsupported or inert and
are excluded from current sensitivity claims. `MOMENTUM_WINDOW_SHORT` is
diagnostic-only. Cost attribution is implemented for actual filled trades; the
current benchmark is price return rather than total return.

## 프로젝트 구조

```
src/
├── super_quality/              # LEGACY — frozen at v2.0-abandoned (do not modify)
│   ├── main.py
│   ├── config.py
│   ├── data/
│   ├── factors/
│   ├── strategies/
│   ├── backtest/
│   ├── analysis/
│   └── reporting/
└── k200_mq/                    # NEW — KOSPI 200 Momentum + Quality (Beta)
    ├── __init__.py
    ├── main.py                 # CLI 진입점
    ├── config.py               # K200MQConfig (BacktestConfig + strategy params)
    ├── core/                   # Reusable infrastructure from legacy
    │   ├── cache.py
    │   ├── factors/base.py
    │   ├── analysis/metrics.py
    │   └── reporting/report.py
    ├── data/                   # Data layer and provenance contracts
    │   ├── __init__.py
    │   ├── universe.py         # Proxy universe and PIT provenance contracts
    │   └── provenance.py       # Filing timestamp/PIT validity contracts
    ├── factors/                # New factors (momentum, quality, regime)
    ├── strategies/             # KOSPI 200 Momentum + Quality strategy
    ├── backtest/               # PortfolioRebalanceEngine
    ├── analysis/
    └── reporting/
```

## 데이터 소스

| 데이터 | 소스 | API 키 필요 | 병렬 처리 | 용도 |
|--------|------|:---:|:---:|------|
| KOSPI / KOSDAQ 종목 리스트 | FinanceDataReader | 아니요 | - | Universe screening |
| 일별 OHLCV 가격 | FinanceDataReader | 아니요 | - | 가격 데이터 |
| 시가총액 | FinanceDataReader | 아니요 | - | 유니버스 구성 |
| 재무제표 (K-IFRS) | OpenDartReader | 품질 사용 시 | 프리스크리닝 6 workers | 품질 팩터 계산 (미설정/불가 시 momentum-only) |
| 개인 투자자 순매수 | pykrx | 아니요 | 8 workers | Supply factor (legacy) |
| 유상증자 일정 | OpenDartReader | 예 | 4 workers | Share change tracking |
| KOSDAQ 지수 (KQ11) | FinanceDataReader | 아니요 | - | 레짓 필터 (legacy) |
| KOSPI 200 지수 (KPI200) | FinanceDataReader | 아니요 | - | 모멘텀+리짓 필터 및 price-return benchmark |

## DART API 키

DART(Data Analysis, Retrieval and Transfer) 시스템은 한국 상장 기업의 전자공시 데이터를 제공합니다.

1. [https://opendart.fss.or.kr](https://opendart.fss.or.kr) 접속
2. 회원가입
3. 마이페이지에서 인증키 신청
4. `.env` 파일에 키 저장:
   ```
   DART_API_KEY=your_40_character_api_key_here
   ```
   또는 환경 변수로 설정:
   ```bash
   export DART_API_KEY=your_40_character_api_key_here
   ```

`DART_API_KEY`가 없거나 DART 품질 데이터가 unavailable인 경우에도 기본 K200MQ
실행은 실패하지 않습니다. 품질 팩터를 비활성화하고 누락 품질 값을 0으로 처리하여
**momentum-only/non-PIT 모드**로 계속 실행하며, 결과 `run_manifest.json`에 DART
unavailable 및 품질 비활성화/재무 데이터 provenance를 명시합니다. `--strict-pit`는
별도의 PIT 계약 검증 모드이므로 필요한 provenance가 없으면 의도적으로 중단할 수
있습니다.

## 테스트

```bash
# 전체 테스트 실행
pytest -v

# 커버리지 리포트 포함
pytest --cov=super_quality

# 특정 모듈 테스트
pytest tests/test_strategy.py -v
pytest tests/test_backtest.py -v
pytest tests/test_integration.py -v

# 린터 실행
ruff check
```

## 성능

데이터 수집 파이프라인은 병렬 처리를 통해 대폭 가속됩니다:

| 단계 | 순차 처리 | 병렬 처리 | 절감율 |
|------|-----------|-----------|--------|
| 개인 순매수 (2,605개 티커) | ~4.3시간 | ~26분 | 90% |
| DART 프리스크리닝 | ~39분 | ~5분 | 87% |
| 유상증자 조회 | ~1-2시간 | ~20-40분 | 67% |
| **전체 (첫 실행)** | **~5-6시간** | **~1시간** | **83%** |

캐시된 데이터를 사용하면 전체 파이프라인이 수분 이내에 완료됩니다.

## 문서

| 문서 | 설명 |
|------|------|
| [README_K200MQ.md](README_K200MQ.md) | KOSPI 200 Momentum + Quality 전략 상세 가이드 |
| [docs/planning/01_strategy_pivot.md](docs/planning/01_strategy_pivot.md) | 전략 전환 이유 및 폐기/유지 매핑 |
| [docs/planning/02_architecture.md](docs/planning/02_architecture.md) | KOSPI 200 MQ 패키지 구조 및 팩터 설계 |
| [docs/planning/03_implementation_plan.md](docs/planning/03_implementation_plan.md) | 5단계 구현 계획 |
| [docs/planning/04_backtest_spec.md](docs/planning/04_backtest_spec.md) | 독립 subperiod robustness, mechanical WF, 비용 모델, PIT gate, 스트레스 테스트 |
| [docs/planning/05_status.md](docs/planning/05_status.md) | 실시간 진행 상황 |
