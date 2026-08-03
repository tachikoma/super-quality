# 백테스트 및 검증 사양 - KOSPI 200 모멘텀 + 품질

## 근거의 경계

현재 구현에는 두 가지 기계적 진단 경로가 있습니다.

1. `robustness`는 학습이나 파라미터 적합 없이 고정된 독립 하위 기간을 실행합니다.
   이는 독립 하위 기간 강건성 테스트이며 walk-forward 교차검증이 아닙니다.
2. `true-walkforward`는 학습 전용 후보 선택을 포함한 expanding-window 학습/테스트
   오케스트레이션을 실행합니다. 실제 역사 유니버스 및 filing-date 검증기가 제공되지
   않으면 분류는 `mechanical_expanding_walk_forward_non_pit`입니다.

아직 검증된 PIT 성과 근거는 없습니다. 기계적 구간 분할은 미래 행이 구간에 들어오는
것을 막을 뿐, 역사적 구성원 또는 공시일 provenance를 만들어 내지 않습니다.

## 현재 공식과 결과의 경계

현재 모멘텀 버전은 `k200mq-momentum-skipped-return-v4`입니다.

```text
close[t-skip_days] / close[t-long_window] - 1
```

기본값은 `close[t-42] / close[t-252] - 1`입니다. 이 의미 교정 이전에 생성된 결과는
현재 공식과 비교해서는 안 됩니다. 해당 결과는 canonical status 문서
(`docs/planning/05_status.md`)에 감사 기록으로만 보존되어 있습니다.

## 독립 하위 기간 강건성

`robustness` 명령은 다음 기간을 독립적으로 실행합니다.

| 하위 기간 | 날짜 |
|-----------|------|
| 1 | 2014-2016 |
| 2 | 2017-2018 |
| 3 | 2019-2020 |
| 4 | 2021-2022 |
| 5 | 2023-현재 |

각 기간은 고정 설정을 사용하며 학습 구간이 없습니다. 팩터 또는 실행 의미가 바뀌면
모든 하위 기간을 새로 실행해야 합니다.

## 확장형(expanding-window) 워크포워드 검증

현재 고정 폴드 일정은 다음과 같습니다.

```text
Fold 1: Train 2015-2019 | Test 2020
Fold 2: Train 2015-2020 | Test 2021
Fold 3: Train 2015-2021 | Test 2022
Fold 4: Train 2015-2022 | Test 2023
Fold 5: Train 2015-2023 | Test 2024
```

실행기는 먼저 모든 학습 선택을 완료하고 고정한 다음, 테스트 구간에서 선택된 후보를
평가합니다. 준비된 가격 캘린더는 정확한 OOS 날짜 범위를 요구하며, 잘린 폴드는
무효입니다. 산출물은 `true_walkforward/selection_and_folds.json`, `summary.csv`,
`oos_returns.csv`와 비밀값을 제외한 config/hash, git, preparation 맥락으로 저장됩니다.

실제 검증기가 제공되지 않는 한 이는 기계적 비-PIT WF입니다. proxy 구성원, 회계기간
재무 데이터, 임의의 PIT 플래그 또는 합성 근거에 기반한 실행에는
`validated_expanding_walk_forward_pit` 라벨을 사용해서는 안 됩니다. 다음 데이터
게이트가 충족될 때까지 strict PIT WF는 보류합니다.

### 현재 v4 no-DART 진단 (2026-08-03)

명령:

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward \
  --output /tmp/k200mq_true_wf_v4_no_dart
```

- 공식: v4 skipped return, `close[t-42] / close[t-252] - 1`.
- 분류: `mechanical_expanding_walk_forward_non_pit`.
- DART가 설정되지 않아 품질 팩터를 비활성화했습니다. 이는 모멘텀만 사용한 기계적
  진단이며 Momentum + Quality 결과가 아닙니다.
- 5개 폴드가 유효했고 2020-2024에 걸쳐 OOS 지점 1,231개를 생성했습니다.
- 연결 누적 수익률: **+4.0408%**.
- 연결 최대 낙폭: **-32.0408%**.
- 폴드 테스트 수익률: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**.

이 실행은 비-PIT proxy 유니버스/순위를 사용하며 filing-date 재무 provenance를
확립하지 않습니다. 검증된 성과 근거 또는 canonical 운영 결과가 아닙니다. 출력
디렉토리는 임시 디렉토리이며 저장소에 복사하거나 커밋하지 않습니다.

## 데이터 누수 제거(Purge) 및 embargo

순수 핵심부에 대한 purge와 embargo는 현재 보류/해당 없음입니다. 현재 후보는
후방 정보만 사용하는 고정 신호이며 미래 라벨이나 겹치는 결과를 적합하지 않습니다.
이러한 라벨 또는 겹치는 결과를 추가하면 사용 전에 폴드 일정과 purge/embargo 규칙을
다시 설계해야 합니다.

## 거래 비용과 귀속

실제 체결에는 다음의 설정된 명시적 비용이 적용됩니다.

| 항목 | 현재 설정 |
|------|-----------|
| 수수료 | 거래 한쪽당 0.015% |
| 매도세 | 매도 시 0.20% |
| 슬리피지 | 체결당 0.10% |
| 시장 영향 | 보류/미지원; ADV 모델은 연결되지 않음 |

비용 귀속은 실제 체결 거래에 구현되어 있습니다. 수수료, 슬리피지, 매도 전용 세금,
매수/매도 명목금액, 회전율, 총비용은 거래 로그, 실행 통계, 포트폴리오 스냅샷,
`metrics.json`, 해당되는 경우 `run_manifest.json` 사이에서 조정·일치합니다. 이는
사전 ADV 시장 영향 추정치가 아닙니다.

## 벤치마크

현재 벤치마크는 구성된 **KPI200 가격수익률**입니다. 측정 구간에 맞춰 자른 지수
종가를 사용해 종가 간 수익률을 계산합니다. **총수익률이 아니며** 배당이나 기타
분배금을 포함하지 않습니다. 벤치마크 provenance와 원천 티커는 실행 매니페스트에
기록됩니다. 총수익률, 동일가중, 매수 후 보유 대안은 현재 구현된 근거가 아닙니다.

## PIT 입력 게이트와 보류 분석

다음 게이트는 충분한 기간의 다음 입력을 확보하고 운영 준비 경로에 연결하는 것입니다.

- 유효일이 있는 역사적 KOSPI 200 구성원 파일;
- 안전한 거래 세션 가용일로 매핑한 원시 DART 제출/공시 메타데이터.

### 구조적 후보 importer와 검증된 KRX PIT 수집

로컬 전용 구조 후보 importer는 `src/k200_mq/data/pit_universe.py`에 CLI와 독립된
가져오기(import) 경로로 구현되어 있습니다. CSV, JSON, Parquet, bytes 또는 DataFrame 입력을
읽으며 실시간 KRX/DART 엔드포인트를 호출하지 않습니다. 정규화만으로는 PIT 근거가
되지 않으며 검증되지 않은 `pit_candidate`만 보고합니다.

후보 스냅샷 스키마는 다음과 같습니다.

```text
index_code, as_of_date (or effective_date), security_code,
source_type, source_url, source_file_sha256, retrieved_at_utc
```

선택적 스냅샷 필드는 `name`, `sector`, `index_weight`, `index_shares`, `free_float`입니다.
명시적 구성원 구간 원천은 `effective_from`, nullable exclusive `effective_to`,
`action` 또는 `status`, `announcement_date`, `provenance`를 사용합니다. event/event
별칭은 지원하지 않습니다. 제공자별 표기는 별칭이 모호할 때 명시적 열 매핑으로
선언해야 합니다. 구조 로더는 6자리 종목 코드, 엄격한 날짜와 시간 순서, 중복 행,
구간 겹침, 스냅샷 크기 진단을 검증합니다. 단순한 `True`로 크기 검사를 우회할 수
없으며, 예외에는 허용 크기와 문서화된 근거가 있어야 합니다.

후보를 승격할 수 있는 것은 별도의 수집 매니페스트 사이드카뿐입니다. 이 사이드카는
공식 HTTPS KRX 원천 URL, 질의/날짜 파라미터, 시간대가 있는 수집 시각, 허용 목록에
있는 KRX 원천 유형, 명시적 KRX 확인, 실제 바이트와 대조한 원시 파일 SHA-256을
기록해야 합니다. 로컬 경로, 파일 URL, 파일 수정 시각, 내장 자기 해시, DataFrame,
임의의 호출자 플래그는 데이터를 승격할 수 없습니다.

인증된 KRX 수집 어댑터는 `src/k200_mq/data/krx_pit.py`에 구현되어 다음의 공식
엔드포인트만 대상으로 합니다.

```text
https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd
bld=dbms/MDC/STAT/standard/MDCSTAT00601
indIdx2=028  # KOSPI 200
indIdx=1
trdDd=YYYYMMDD
```

공식 `MDCCOMS001.cmd` 및 `MDCCOMS001D1.cmd` 엔드포인트를 통해 세션을 초기화하고
로그인합니다. 성공한 로그인 응답 코드는 `CD001`뿐이며, 중복 로그인 `CD011`은
`skipDup=Y`로 재시도할 수 있고 그 밖의 로그인 응답은 fail closed로 처리합니다.
어댑터는 KOSPI 200 응답 근거를 요구하고 정확한 응답 바이트를 보존하며 공식 URL,
질의 파라미터, KRX 확인, Asia/Seoul 및 UTC 수집 시각, 응답 SHA-256, 행 수, 스키마
버전이 담긴 사이드카를 기록합니다. 해시는 원시 응답 자체에 삽입하지 않습니다.

이 어댑터는 두 날짜에 대해 라이브 스모크 테스트를 완료했습니다. 원시 KRX 바이트와
매니페스트는 로컬·미커밋 수집 산출물이며, 아직 충분한 역사 자료를 운영 성과
근거에 연결하지 않았습니다. `LOCAL_PIT_UNIVERSE_PATH`,
`LOCAL_PIT_UNIVERSE_SOURCE_KIND`, `LOCAL_PIT_UNIVERSE_MANIFEST`를 통한 명시적 로컬
원천 통합은 `config`·`universe`·`main`에 존재합니다. 이 설정을 하지 않으면 기존
proxy 기본값을 유지하고, 설정한 원천의 파일·매니페스트·날짜·해시·타임스탬프·재로딩
검증이 실패하면 fail closed로 중단합니다.

다중 스냅샷 로더는 날짜별 매니페스트, 원시 해시, 시간대가 있는 타임스탬프, 스냅샷
식별자, 토큰, 결합 후 정규화 프레임 fingerprint를 모두 다시 확인합니다. 이 계약은
여러 날짜의 검증 결과를 단일 해시나 단일 매니페스트로 잘못 대표하지 않도록 합니다.

### 로컬 OpenDART 제출일 provenance 계약

`src/k200_mq/data/dart_pit.py`에는 대응하는 로컬 전용 재무 후보 importer가 있습니다.
JSON, CSV, Parquet 원시 파일과 별도의 사이드카 매니페스트를 받으며 OpenDART를
호출하지 않습니다. 예정된 상위 원천 계약은 다음과 같습니다.

```text
filing list:     https://opendart.fss.or.kr/api/list.json
financial facts: https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json
```

정규화된 제출 행 하나는 하나의 제출을 나타내며 `corp_code`, `rcept_no`, 원시
`rcept_dt`, 파싱된 접수일, 정정 및 철회 상태, 원천 경로, 응답 해시, 수집 시각을
보존합니다. 재무 사실은 `rcept_no`를 보존하고 `(corp_code, rcept_no)`로만 메타데이터와
조인합니다. 회계기간만으로 조인할 수 없으며, 누락되거나 모호한 접수 조인은
무효입니다.

OpenDART `rcept_dt`는 Asia/Seoul 기준 공식 날짜 전용 `YYYYMMDD`입니다. 기본 가용성
정책은 `next_session`이며, 제출일보다 엄격하게 뒤에 있는 첫 번째 제공 KRX 거래일을
사용합니다. 당일 가용성은 거부합니다. 세션 마감 시각 정책은 보류 중입니다. 검증된
원시 제출 매니페스트에 시각 계보가 선언되고 importer가 발행한 정규화 프레임
fingerprint에 포함되지 않으면 사용할 수 없습니다. 정확한 조인 뒤에 시각을 덧붙이면
거부합니다. 철회된 제출은 거부하며 정정에 대해서는 `first_filing` 또는
`latest_filing_available_as_of` 중 하나를 선택해야 합니다.

검증된 원시 응답 매니페스트, 정확한 조인, 세션 매핑, 유효한 해시, 명시적 정책을
모두 갖춘 프레임만 기존 `pit_filing_date` 재무 provenance 계약을 받을 수 있습니다.
그 밖의 프레임은 `non_pit_fiscal_period` 또는 invalid로 남습니다. 현재 importer는
품질 기본 경로에 연결되지 않았고, 기본 경로는 비-PIT입니다.

## 남은 작업 / 다음 단계

현재의 핵심 게이트는 로컬 계약의 구현 여부가 아니라 충분한 역사 원시 자료를 실제
준비·실행 경로에 연결하고 그 provenance를 재검증하는 것입니다.

1. KRX 인증 어댑터로 필요한 리밸런싱 날짜의 역사 스냅샷을 수집하고, 원시 파일과
   사이드카를 로컬에서 보존한 채 날짜별 검증을 완료합니다.
2. 명시적 로컬 KRX 원천을 전체 기간에 적용해 proxy 기본값과 분리된 유니버스 입력을
   만들고, 실제 자료에서 PIT 검증을 통과시킵니다.
3. OpenDART API 또는 벌크 다운로드 작업을 연결해 원시 파일과 매니페스트를 만들고,
   filing-date 재무 입력을 품질 팩터에 연결합니다.
4. 두 PIT 입력 게이트가 통과한 뒤 strict PIT WF를 실행합니다.
5. strict PIT WF 이후에만 PIT 파라미터 민감도, 생존자 편향 비교, ADV 영향 모델 및
   유동성 제약, 계획된 스트레스 테스트를 실행합니다.

따라서 현재 다음 분석은 모두 보류입니다.

- strict PIT WF;
- PIT 파라미터 민감도;
- 생존자 편향 비교;
- ADV 영향 및 유동성 제약;
- 계획된 스트레스 테스트.

다음 설정은 현재 미지원 또는 비활성으로 민감도 주장에서 제외합니다:
`SECTOR_CAP`, `MIN_ADV_RATIO`, `MIN_CASH_RATIO`, `MAX_HOLDINGS`, `UNIVERSE_SIZE`,
`USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`, 그리고
management/investment/preferred/ETF-ETN 제외 플래그. `MOMENTUM_WINDOW_SHORT`는
진단 전용입니다.

## 출력 산출물

`run` 출력 디렉토리에는 `portfolio_snapshots.csv`, `trade_log.csv`,
`daily_returns.csv`, `metrics.json`, `benchmark_returns.csv`, `run_manifest.json`이
생길 수 있습니다. `robustness`는 `subperiod_robustness_summary.csv`를 추가하고,
true WF는 `true_walkforward/` 아래의 세 파일을 추가합니다. 이 산출물은 진단과
provenance를 기록할 뿐, 비-PIT 자료를 검증된 PIT 근거로 바꾸지 않습니다.
