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
| **Momentum (skipped-return, v4)** | `close[t-skip_days] / close[t-long_window] - 1` (기본 `close[t-42] / close[t-252] - 1`) | 50% |
| **Quality — ROE** | 당기순이익 / 자본총계 (현재 normalized 재무 입력; TTM 필터 없음) | 35% |
| **Quality — Debt/Equity** | 총부채 / 자본총계 (낮을수록 좋음) | 25% |
| **Quality — Operating Margin** | 영업이익 / 매출액 (현재 normalized 재무 입력; TTM 필터 없음) | 20% |
| **Quality — Cash Conversion** | 영업현금흐름 / 당기순이익 (현재 normalized 재무 입력; TTM 필터 없음) | 20% |

### 리짓 필터

`KPI200 > MA(REGIME_MA_PERIOD)` AND 20거래일 누적 수익률 >
`REGIME_MIN_RETURN` (기본값 0.0)인 경우에만 포지션 100% 유지,
그렇지 않으면 50% 축소합니다. 수익률 계산 window는 20거래일로 유지됩니다.

### 포트폴리오 구성

- 리밸런싱: 월간 또는 분기
- 선택 종목: TOP 20 (기본)
- 배분: 동일 비중 또는 순위 가중
- KOSPI 상위 50개 제외 (메가캡 모멘텀 희석 방지)
- 섹터별 노출 캡: **unsupported/deferred** (현재 엔진에 적용하지 않음)
- 일일 손절: -15% (선택 사항)

### 구현 범위와 deferred 설정

현재 엔진이 실제로 적용하는 것은 TOP_N, KOSPI 상위 제외, 포지션 배분,
regime scaling, stop-loss, 그리고 명시적 거래 비용입니다. 다음 설정은
호환성을 위해 유지되지만 **unsupported/deferred**이며 백테스트에 적용되지
않습니다: `SECTOR_CAP`, `MIN_ADV_RATIO`/ADV 유동성 필터, `MIN_CASH_RATIO`,
`MAX_HOLDINGS`, `UNIVERSE_SIZE`, `USE_52WEEK_HIGH`/52주 고점 신호.
`QUALITY_MIN_TTM_QUARTERS`도
현재 inert이며 TTM 분기 필터를 수행하지 않습니다. `EXCLUDE_MANAGEMENT`,
`EXCLUDE_INVESTMENT_NOTICE`, `EXCLUDE_PREFERRED`, `EXCLUDE_ETF_ETN`은
**unsupported/inert**이고 runtime consumer가 없으며 호환성을 위해서만 유지됩니다.
`YearHighFactor` 계산 코드가 있더라도 `USE_52WEEK_HIGH`가 켜졌다고 해서 랭킹에
포함되지 않습니다.
`MOMENTUM_WINDOW_SHORT`는 `momentum_6m` 표시용 **diagnostic-only** 값이며
랭킹, readiness, sensitivity 차원이 아닙니다.

### 모멘텀 공식 버전과 결과 비교 경계

현재 모멘텀 공식은 `k200mq-momentum-skipped-return-v4`이며,
`close[t-skip_days] / close[t-long_window] - 1` (기본
`close[t-42] / close[t-252] - 1`)입니다. 이 의미론적 변경 이전에 생성된 결과는
모두 `obsolete_pre_momentum_v4` / non-current diagnostic으로 분류합니다. 따라서
보존된 **+26.97%**, 이전 **+207%/+245-era** 결과와 pre-v4 independent
robustness 및 WF 수치(이 문서의 +44.6426% 포함)는 현재 공식 결과와 비교해서는
안 됩니다. 감사(audit) 기록은 삭제하지 않으며, 공식/현재 성과로 취급하지 않습니다.
공식 또는 다른 factor 의미론이 바뀔 때마다 fresh true-WF run을 다시 수행해야 합니다.

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
walk-forward CV가 아닙니다. 공식/팩터 의미론이 바뀐 뒤에는 이 진단을 반드시
재실행해야 하며, 이전 실행은 `obsolete_pre_momentum_v4` non-current
diagnostic입니다.

```bash
uv run python -m k200_mq.main robustness
```

기존 사용자를 위해 `walkforward` 명령도 호환 alias로 유지되지만,
출력과 `subperiod_robustness_summary.csv`는 독립 subperiod robustness 결과로
표시됩니다.

### True expanding-window WF

`true-walkforward` 명령은 2015–2024 고정 5-fold expanding-window 일정에서
모든 후보를 train-only로 평가하고 선택된 후보만 각 test interval에서 실행합니다.
입력은 한 번 준비되지만, 각 train/test 실행 전에 가격·팩터·지수·유니버스·regime
자료가 해당 interval로 잘립니다. 준비된 가격 거래일이 있으면 test OOS 날짜가
예상 거래일과 정확히 일치해야 하며, 누락된 결과는 invalid입니다.
준비된 거래일 달력이 없는 pure-runner 사용에서는 이 exact 검사를 적용할 수
없으므로 구조적 날짜 검증과 non-empty 검증만 적용됩니다.

```bash
uv run python -m k200_mq.main true-walkforward --output outputs_k200mq
```

결과는 `outputs_k200mq/true_walkforward/`에 저장됩니다.

- `selection_and_folds.json`: fold 선택/결과, base runtime config, fold별 유효
  merged config 및 hash, git state, preparation manifest context
- `summary.csv`: fold별 test 지표와 위 provenance를 포함한 요약
- `oos_returns.csv`: 검증된 stitched OOS 일별 수익률

현재 결과는
`mechanical_expanding_walk_forward_non_pit`으로 분류되며, 이 순수 core는
실제 historical constituent와 filing-date provenance validator가 연결되지 않은
기계적 결과입니다. 따라서 성과 주장이 아니며, strict PIT 분류로 승격되지
않습니다. `selection_and_folds.json`의 config hash는 후보 파라미터만이 아니라
비밀을 제외한 비용·자본·제외·팩터 등 유효 runtime config를 포함합니다.
기본 후보는 `BASE` (TOP_N=20/regime on), `TOP_N_10`, `TOP_N_30`,
`REGIME_OFF`이며, `BASE`와 동일한 `TOP_N_20` 및 `REGIME_ON`은 중복이므로
포함하지 않습니다.
현재 손절 설정은 `--enable-stop-loss`/`--disable-stop-loss`로 명시적으로 제어할
수 있으며, 이 두 플래그와 `--stop-loss`는 **`run` 명령에서만** 사용할 수
있습니다. 활성화 시 기준은 `-1.0 < SL_STOP_LOSS < 0.0`이어야 합니다.
`true-walkforward`는 이 플래그들을 노출하지 않고 `K200MQConfig` 환경 설정 또는
기본값을 사용합니다. 손절은 기본 후보 라이브러리의 sensitivity 차원에는 포함하지
않습니다.
`validated_expanding_walk_forward_pit` 승격은 실제 universe/financial provenance
validator 결과를 pure runner에 연결하는 후속 작업으로 보류했습니다. 임의의 bool,
`{"valid": true}` 또는 설명용 evidence는 PIT 근거로 인정하지 않습니다.

#### Fresh v4 기계적 True-WF 진단 실행 (2026-08-03; current formula, non-PIT)

실행 명령은 다음과 같습니다.

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward --output /tmp/k200mq_true_wf_v4_no_dart
```

- 모멘텀 공식: v4 skipped return `close[t-42] / close[t-252] - 1`
- 분류: `mechanical_expanding_walk_forward_non_pit`
- DART API key가 unset되어 quality factor는 비활성화되었습니다. 따라서 이 실행은
  **momentum-only diagnostic**이며 Quality + Momentum 결과가 아닙니다.
- 5개 fold가 모두 유효했습니다. 선택 후보는 fold 1–4에서 `TOP_N_10`, fold 5에서
  `BASE`였습니다.
- OOS는 1,231개 포인트(2020–2024)이며, stitched 누적 수익률은 **+4.0408%**,
  stitched MDD는 **-32.0408%**입니다.
- Fold별 test 수익률: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**

산출물은 `/tmp/k200mq_true_wf_v4_no_dart/true_walkforward/` 아래의
`selection_and_folds.json`, `summary.csv`, `oos_returns.csv`이며 저장소에 복사하거나
커밋하지 않습니다. 이 실행은 v4 공식 모멘텀을 사용했지만, KOSPI 200 유니버스와
cross-sectional ranking이 non-PIT proxy이고 DART가 unset되어 financial data가
없거나 non-PIT이므로 **validated performance evidence가 아닙니다.** 따라서
canonical/production 결과로 승격하지 않습니다.

#### 이전 pre-v4 기계적 진단 실행 (2026-08-03; `obsolete_pre_momentum_v4`)

실행 명령은 `uv run python -m k200_mq.main true-walkforward --output
/tmp/k200mq_true_wf`입니다. 분류는
`mechanical_expanding_walk_forward_non_pit`이며, 5개 fold가 모두 유효했고
모든 fold에서 `TOP_N_10`이 선택되었습니다. OOS는 1,231개 포인트(2020–2024
test 기간), stitched 누적 수익률은 **+44.6426%**, stitched MDD는
**-32.5935%**입니다. Fold별 test 수익률은 2020 **+60.4528%**, 2021
**-2.0225%**, 2022 **-20.2042%**, 2023 **+19.2139%**, 2024
**-3.2801%**입니다.

산출물은 `/tmp/k200mq_true_wf/true_walkforward/` 아래의
`selection_and_folds.json`, `summary.csv`, `oos_returns.csv`이며 저장소에
복사하지 않습니다. 현재 KOSPI 200 membership/ranking은 non-PIT proxy이고
normalized DART 재무 데이터는 `non_pit_fiscal_period`이므로, 이 결과는
**validated performance evidence가 아니며 canonical/production 결과도 아닙니다.**
또한 v4 formula correction 이전 실행이므로 `obsolete_pre_momentum_v4` non-current
diagnostic입니다. 현재 공식과 비교하려면 fresh true-WF 실행이 필요합니다.

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
| `--stop-loss` | -0.15 | enabled일 때 `-1.0 < value < 0.0`인 trailing 손절 기준 |
| `--enable-stop-loss` / `--disable-stop-loss` | enabled | **`run` 전용** 손절 주문 생성 on/off; true-WF에는 없음 |
| `--no-cache` | — | **unsupported/deferred** — 사용 시 명확히 거부 |
| `--rebalance-lookback` | — | **unsupported/deferred** — 사용 시 명확히 거부 |
| `--max-holdings` | 20 | **unsupported/deferred** — 현재 엔진에 적용하지 않음 |
| `--sector-cap` | 0.30 | **unsupported/deferred** — 현재 엔진에 적용하지 않음 |
| `--min-adv-ratio` | 0.01 | **unsupported/deferred** — 현재 엔진에 적용하지 않음 |
| `UNIVERSE_SIZE` | 200 | **unsupported/deferred** — 현재 유니버스 로더가 소비하지 않음 |
| `--strict-pit` | false | PIT 유니버스·filing-date 재무 데이터가 없으면 중단 |

`MIN_CASH_RATIO`, `USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`도 현재는
호환성을 위해 설정에 남아 있지만 지원되지 않거나 inert이다. 이 항목들과
위의 deferred 항목은 sensitivity candidate에 포함하지 않는다. 다음 네 설정도
필드는 유지하지만 **unsupported/inert**이며 runtime consumer가 없다:
`EXCLUDE_MANAGEMENT`, `EXCLUDE_INVESTMENT_NOTICE`, `EXCLUDE_PREFERRED`,
`EXCLUDE_ETF_ETN`.

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
│   ├── momentum.py          # skipped-return 모멘텀 팩터 (v4)
│   ├── quality.py           # 품질 팩터 (ROE, DE, OPM, CC)
│   └── regime.py            # 리짓 필터 (KPI200 > MA(period) + 20일 수익률 조건)
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
- Sim & Kim (2021): 한국에서 2개월 단기 반전 → 12개월 lookback에서 최근 2개월을
  skip한 모멘텀 사용 권장
- Choi, Choi & Kang (2013): KOSPI 상위 50개 제외 시 모멘텀 성능 개선
- Park, Bae & Lee (2021): KOSPI 200에서 Quality + Momentum 조합 월 1.34%
- Kim (2018): 한국 장-only 모멘텀 +1.15%/월
- Bae & Lee (2021): 한국 모멘텀 0.50~1.06%/월 (장기 포트폴리오)

## 검증 상태 및 참고 사항

- 이 전략은 **베타 (Beta)** 상태입니다. 파이프라인은 완성되었지만 결과는 검증되지 않았습니다.
- 현재 `robustness` 명령은 고정된 독립 subperiod robustness test입니다. 학습/피팅이
  없는 반면, `true-walkforward`는 학습/선택과 OOS 실행을 분리한 expanding-window
  command입니다.
- 백테스트 결과는 아직 검증되지 않았습니다. 리짓 필터 적용, 리밸런싱 일자 통합,
  품질 팩터 커버리지 개선은 구현되었지만, PIT 유니버스와 filing-date 재무 데이터
  한계가 남아 있어 결과를 전략 성과로 해석할 수 없습니다.
- 기존 Super Quality 2.0 레거시 코드는 `src/super_quality/`에 frozen 상태로 보존됩니다.
- true-walkforward의 기계적 interval slicing은 future rows가 adapter에 들어가는
  것을 막지만, 이것만으로 historical PIT 유니버스·재무 provenance가 생기지는 않습니다.

## 초기 백테스트 결과 (2026-07-26, 2020-2024; `obsolete_pre_momentum_v4`)

다음 수치는 v4 공식 적용 전의 보존용 감사 기록이다. **현재 결과가 아니며,
현재 공식과 비교하거나 성과 주장에 사용할 수 없다. v4 fresh true-WF를 먼저
실행해야 한다.**

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

> **⚠️ 주의**: 위 수치는 P0 수정 전이고 현재 모멘텀 공식 변경 전의 과거 실행
> 결과인 `obsolete_pre_momentum_v4`입니다. 같은 이유로 보존된 +26.97%와
> 이전 +207%/+245-era 결과, pre-v4 robustness/WF 결과와 현재 공식 결과를
> 비교하지 마십시오. 감사 기록은 유지하며, 현재 공식에는 fresh run이 필요합니다.
