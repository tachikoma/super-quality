# KOSPI 200 Momentum + Quality

KOSPI 200 대형주 중심의 모멘텀+품질 백테스팅 시스템입니다.

Super Quality 2.0 (KOSDAQ 소형주 밸류 전략)이 구조적으로 실패한 후,
KOSPI 200 Momentum + Quality 프레임워크로 새 출발한 프로젝트입니다.

## 전략 개요

KOSPI 200 종목 중 리밸런싱 일자에 모멘텀 팩터와 품질 팩터를
크로스섹셔널로 결합하여 상위 N개 종목을 선정하고
동일 비중으로 포트폴리오를 구성합니다.

### 팩터 구성

| 팩터 | 설명 | 가중치 |
|------|------|--------|
| **Momentum (12-7개월)** | 최근 12개월 수익률 중 마지막 2개월 skip | 50% |
| **Quality — ROE** | 당기순이익 / 자본총계 (TTM) | 35% |
| **Quality — Debt/Equity** | 총부채 / 자본총계 (낮을수록 좋음) | 25% |
| **Quality — Operating Margin** | 영업이익 / 매출액 (TTM) | 20% |
| **Quality — Cash Conversion** | 영업현금흐름 / 당기순이익 (TTM) | 20% |

### 리짓 필터

KOSPI 200 지수 > MA200 AND 20일 수익률 > 0 인 경우에만
포지션 100% 유지, 그렇지 않으면 50% 축소.

### 포트폴리오 구성

- 리밸런싱: 월간 또는 분기
- 선택 종목: TOP 20 (기본)
- 배분: 동일 비중 또는 순위 가중
- KOSPI 상위 50개 제외 (메가캡 모멘텀 희석 방지)
- 섹션별 노출 캡: 30%
- 일일 손절: -15% (선택 사항)

## 설치

```bash
# 저장소 클론
git clone <repo-url>
cd super-quality

# uv로 의존성 설치
uv sync
```

## 사용법

### 기본 실행

```bash
uv run python -m k200_mq.main run
```

### 날짜 범위와 파라미터 지정

```bash
uv run python -m k200_mq.main run \
    --start 2015-01-01 --end 2024-12-31 \
    --top-n 20 \
    --rebalance-freq M \
    --weight-momentum 0.50 --weight-quality 0.50
```

### 독립 subperiod robustness test

고정된 5개 독립 기간(2014–2016, 2017–2018, 2019–2020, 2021–2022,
2023–현재)을 각각 별도 백테스트하여 기간별 결과의 견고성을 확인합니다.
이 명령은 학습/파라미터 피팅이 없는 **subperiod robustness test**이며,
walk-forward CV가 아닙니다.

```bash
uv run python -m k200_mq.main robustness
```

기존 사용자를 위해 `walkforward` 명령도 호환 alias로 유지되지만,
출력과 `subperiod_robustness_summary.csv`는 독립 subperiod robustness 결과로
표시됩니다. 학습 구간을 사용하는 expanding-window true walk-forward 검증은
아직 실행 파이프라인에 연결되지 않았습니다.

### True expanding-window WF core (Phase 1)

`src/k200_mq/validation/walk_forward.py`에 2015–2024 고정 5-fold expanding-window
일정, 실제 의미가 있는 보수적 후보 라이브러리, train-only Sharpe 선택기와
직렬화 결과가 구현되어 있습니다. 현재 결과는
`mechanical_expanding_walk_forward_non_pit`으로 분류되며, 이 순수 core는
아직 `robustness` 동작·전략 실행·live data pipeline에 연결되지 않았습니다.
기본 후보는 `BASE` (TOP_N=20/regime on), `TOP_N_10`, `TOP_N_30`,
`REGIME_OFF`이며, `BASE`와 동일한 `TOP_N_20` 및 `REGIME_ON`은 중복이므로
포함하지 않습니다.
현재 손절 설정에는 안전한 on/off 표현이 없어 후보 라이브러리에서도 제외했습니다.
PIT 데이터 계약을 충족한 실행만 `validated_expanding_walk_forward_pit`로
분류할 수 있습니다.

### 데이터 유효성 계약

기본 실행은 기존의 탐색적 성과 동작을 유지하지만, `run_manifest.json`에
데이터 한계를 기록합니다. 현재 FinanceDataReader KOSPI200 목록은 `as_of`를
무시하는 **`proxy_current`**이고, 현재 종목 목록의 시가총액 상위 200개
fallback은 **`mcap_proxy`**입니다. 둘 다 PIT(point-in-time) 유니버스가 아닙니다.
이 날짜 키는 PIT 복원을 보장하지 않는 **as-of-keyed proxy cache**일 뿐이며,
구조화된 source/effective-date contract/fingerprint가 없는 레거시 캐시는
**`legacy_proxy_unknown`**으로 분류합니다.
현재 정규화된 DART 재무 데이터도 공시일을 보존하지 않으므로 품질 데이터
모드는 **`non_pit_fiscal_period`**입니다.

검증 가능한 실행이 필요하면 다음처럼 strict 모드를 사용합니다.

```bash
uv run python -m k200_mq.main run --strict-pit
```

`--strict-pit` (또는 `STRICT_PIT_VALIDATION=true`)는 PIT 유니버스와 실제
filing/publication timestamp 또는 명시적 cutoff를 사용한 재무 데이터가
없으면 팩터·백테스트 전에 중단합니다. 현재 top-N 제외 순위는 현재 시가총액
스냅샷이므로 strict 모드에서는 `EXCLUDE_KOSPI_TOP_N=0`이어야 합니다. strict
모드를 통과하려면 KRX의 과거 유효일자별 구성종목 파일과 원시 DART filing
metadata를 확보하고, 그 공시일을 재무 데이터 가용일로 사용하는 로더가
필요합니다. 분기말이나 임의의 deadline으로 공시일을 추정하지 않습니다.

### 주요 파라미터

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `--top-n` | 20 | 선택 종목 수 |
| `--rebalance-freq` | M | 리밸런싱 주기 (M=월, Q=분기) |
| `--weight-momentum` | 0.50 | 모멘텀 팩터 가중치 |
| `--weight-quality` | 0.50 | 품질 팩터 가중치 |
| `--exclude-kospi-top-n` | 50 | 모멘텀에서 제외할 KOSPI 상위 N개 |
| `--stop-loss` | -0.15 | 일일 손절 기준 |
| `--max-holdings` | 20 | 최대 동시 보유 |
| `--sector-cap` | 0.30 | 섹션별 최대 노출 |
| `--min-adv-ratio` | 0.01 | 최소 유동성 비율 |
| `--strict-pit` | false | PIT 유니버스·filing-date 재무 데이터가 없으면 중단 |

## 프로젝트 구조

```
src/k200_mq/
├── __init__.py
├── main.py                  # CLI 진입점 및 subperiod robustness test
├── config.py                # K200MQConfig
├── core/                    # 공통 인프라 (레거시에서 재사용)
│   ├── cache.py
│   ├── factors/base.py
│   ├── analysis/metrics.py
│   ├── reporting/report.py
│   └── data/loader.py
├── data/
│   ├── __init__.py
│   ├── universe.py          # KOSPI 200 유니버스 및 PIT provenance
│   └── provenance.py        # Filing timestamp/PIT validity contracts
├── factors/
│   ├── momentum.py          # 모멘텀 팩터 (12-7개월)
│   ├── quality.py           # 품질 팩터 (ROE, DE, OPM, CC)
│   └── regime.py            # 리짓 필터 (KOSPI 200 MA200)
├── strategies/
│   └── momentum_quality.py  # 크로스섹셔널 스코어링
├── backtest/
│   └── portfolio_engine.py  # 리밸런싱 엔진
├── analysis/
└── reporting/
```

## 리밸런싱 준비 게이트

`run_manifest.json`의 `rebalance_readiness`는 모멘텀 warmup으로 인한 초기
리밸런싱 건너뜀을 기록합니다. `first_scheduled_rebalance`는 측정 기간의
첫 예정일이고, `first_ready_rebalance`는 `min(TOP_N, 해당 유니버스 규모)`개의
유한한 모멘텀 후보가 처음 확보된 예정일입니다. 실제 엔진 거래는
`measured_trading_readiness_date` 이후에만 시작되며, 그 전의
`skipped_not_ready_rebalances`와 커버리지가 함께 저장됩니다.

## 학술 근거

- Kang, Kwon & Park (2014): 한국 대형주에서 외부인 흐름 → 모멘텀 효과
- Sim & Kim (2021): 한국에서 2개월 단기 반전 → 12-7개월 모멘텀 사용 권장
- Choi, Choi & Kang (2013): KOSPI 상위 50개 제외 시 모멘텀 성능 개선
- Park, Bae & Lee (2021): KOSPI 200에서 Quality + Momentum 조합 월 1.34%
- Kim (2018): 한국 장-only 모멘텀 +1.15%/월
- Bae & Lee (2021): 한국 모멘텀 0.50~1.06%/월 (장기 포트폴리오)

## 검증 상태 및 참고 사항

- 이 전략은 **베타 (Beta)** 상태입니다. 파이프라인은 완성되었지만 결과는 검증되지 않았습니다.
- 현재 `robustness` 명령은 고정된 독립 subperiod robustness test입니다. 학습/피팅이
  포함된 expanding-window true walk-forward CV의 Phase 1 pure core는 구현됐지만,
  실행 파이프라인에는 아직 연결되지 않았습니다.
- 백테스트 결과는 아직 검증되지 않았습니다. 리짓 필터 적용, 리밸런싱 일자 통합,
  품질 팩터 커버리지 개선은 구현되었지만, PIT 유니버스와 filing-date 재무 데이터
  한계가 남아 있어 결과를 전략 성과로 해석할 수 없습니다.
- 기존 Super Quality 2.0 레거시 코드는 `src/super_quality/`에 frozen 상태로 보존됩니다.

## 초기 백테스트 결과 (2026-07-26, 2020-2024; P0 수정 전)

```
초기 자본: 100,000,000원 → 최종 자본: 307,063,900원
총 수익률: +207.06%
연간 수익률: +61.94%
연간 변동성: 80.93%
Sharpe 비율: 0.596
최대 낙폭: -60.71%
총 거래: 62건 | 승률: 77.4%
평균 수익률/건: +35.46% | 평균 보유일: 131.3일
평균 보유 종목: 9.2개
```

> **⚠️ 주의**: 위 수치는 P0 수정 전의 과거 실행 결과이므로 무효입니다. 현재 P0
> 수정은 반영되었지만, PIT 유니버스와 filing-date 재무 데이터가 없는 한 재실행
> 결과도 탐색적 후보로만 취급해야 합니다.
