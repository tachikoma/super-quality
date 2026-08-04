# 현재 상태 - KOSPI 200 모멘텀 + 품질

이 문서는 현재 상태를 기록하는 정식 문서입니다.

최종 갱신: 2026-08-04

## 한눈에 보는 상태

| 범주 | 현재 상태 |
|------|-----------|
| 구현된 인프라 | 파이프라인, 팩터, 포트폴리오 엔진, CLI, 벤치마크, 실제 체결 비용 귀속, KRX/DART 로컬 provenance 계약, 검증 보호 장치, 테스트가 구현됨. |
| 기계적 비-PIT 진단 | `robustness` 독립 하위 기간 테스트와 expanding-window `true-walkforward`를 사용할 수 있음. |
| 검증된 PIT 근거 | 아직 없음. |
| 현재 공식 진단 | v4 no-DART 모멘텀 전용 기계적 WF: 연결 수익률 +4.0408%, 최대 낙폭 -32.0408%, OOS 지점 1,231개. |
| 폐기된 결과 | v4 이전 및 현금 전파 수정 이전의 모든 성과 출력은 감사 전용이며 현재 결과가 아님. |
| 다음 게이트 | 충분한 기간의 역사적 KRX 구성원 입력과 제출일 기반 DART 재무 데이터. |

이 프로젝트는 검증된 투자 전략이 아니라 베타 단계의 인프라입니다.

## 구현된 인프라

- `src/k200_mq/`에 데이터 준비, v4 모멘텀, 정규화 입력 품질, regime, 전략,
  포트폴리오 엔진, 보고, 검증 계층이 들어 있습니다.
- 파이프라인은 유니버스 준비, 가격 룩백, 팩터 준비, 전략 선택, 실행, 결과 저장을
  순서대로 수행합니다.
- 포트폴리오 엔진은 regime 비중 조절, trailing stop-loss, 다음 세션 실행, 목표
  비중 조정, 현금 전파, 설정된 수수료/슬리피지/매도세 처리를 포함합니다.
- `run`, `robustness`, `true-walkforward` 모듈 CLI 경로가 연결되어 있습니다.
- `robustness`는 학습이나 파라미터 적합이 없는 독립 하위 기간 테스트이며
  walk-forward 교차검증이 아닙니다.
- expanding-window WF 실행기는 학습 전용 선택을 수행하고, 테스트 평가 전에 모든
  선택을 고정하며, 입력을 구간별로 자르고, 거래 캘린더가 있을 때 정확한 준비 OOS
  날짜를 확인합니다.
- WF 산출물에는 선택/폴드 결과, 요약, OOS 수익률, 비밀값을 제외한 유효 config/hash,
  git 상태, 준비 맥락이 포함됩니다.
- 벤치마크는 KPI200 종가 기반 **가격수익률**로 구현되어 있습니다. 총수익률이 아니며
  배당/분배금을 제외합니다.
- 비용 귀속은 실제 체결 거래에 구현되어 있습니다. 거래 로그, 실행 통계, 스냅샷,
  metrics, 매니페스트의 비용 합계를 서로 조정·일치시킵니다.
- 의미 안전성 수정과 테스트가 구현되어 있습니다: v4 skipped-return 모멘텀,
  명시적 품질 가중치, regime 수익률 기준, 손절 검증 및 관련 실행 도메인 검사.
- K200MQ 팩터, 전략, 엔진, provenance, 벤치마크, 비용, WF 회귀 테스트가 구현되어
  있습니다. 레거시 `src/super_quality/` 패키지는 동결된 상태입니다.
- 로컬 파일 구조 후보 importer가 `src/k200_mq/data/pit_universe.py`에 구현되어
  있습니다. CSV/JSON/Parquet/bytes 스냅샷 또는 명시적 유효일 구간을 정규화하지만,
  별도 수집 매니페스트가 공식 KRX 원천 메타데이터와 원시 바이트 SHA-256을 입증하기
  전에는 검증되지 않은 상태입니다.
- 인증된 KRX 수집 어댑터가 `src/k200_mq/data/krx_pit.py`에 구현되어 있습니다.
  검증된 라이브 계약은
  `https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd`와
  `bld=dbms/MDC/STAT/standard/MDCSTAT00601`, `indIdx2=028` (KOSPI 200),
  `indIdx=1`, `trdDd=YYYYMMDD`이며, 로그인에는 공식
  `MDCCOMS001.cmd`/`MDCCOMS001D1.cmd` 엔드포인트를 사용합니다. 두 날짜에 대한
  라이브 스모크 테스트를 완료했고 정확한 원시 JSON 응답과 별도 SHA-256 수집
  매니페스트를 저장합니다.
- 원시 KRX 파일과 사이드카는 로컬 수집 산출물이며 커밋하지 않습니다. 두 날짜의
  스모크 테스트와 파일 존재만으로는 충분한 역사적 KOSPI 200 구성원에 대한 운영
  성과 근거가 되지 않습니다.
- 명시적 로컬 KRX PIT 원천 통합이 `config`·`universe`·`main`에 구현되어 있습니다.
  `LOCAL_PIT_UNIVERSE_PATH`, `LOCAL_PIT_UNIVERSE_SOURCE_KIND`,
  `LOCAL_PIT_UNIVERSE_MANIFEST`를 설정하면 해당 원천을 사용하고 proxy/cache 경로를
  사용하지 않습니다. 설정하지 않으면 기존 proxy 기본 동작을 유지하며, 설정한
  원천의 파일·매니페스트·날짜·해시·타임스탬프·재로딩 검증이 실패하면 즉시 중단(fail closed)합니다.
- provenance 계약은 여러 스냅샷의 날짜별 범위, 원시 해시, 시간대가 있는 타임스탬프,
  행 수, 스냅샷 식별자, 매니페스트, 토큰 및 결합 후 정규화 프레임 fingerprint를
  재검증합니다. 여러 날짜를 하나의 해시나 하나의 매니페스트로 잘못 대표하지
  않도록 강화되어 있습니다.
- 로컬 파일 우선 DART provenance 계층이 `src/k200_mq/data/dart_pit.py`에 구현되어
  있습니다. 원시 제출과 재무 사실을 정규화하고, 응답 매니페스트의 SHA-256 사이드카를
  확인하며, `(corp_code, rcept_no)`로만 조인하고, 철회/모호한 조인을 거부하고,
  공식 날짜 전용 `rcept_dt`를 제출일보다 엄격히 뒤인 첫 KRX 세션으로 매핑합니다.
  다만 실시간 API/벌크 수집과 현재 품질 팩터 기본 경로 연결은 아직 없습니다.

## 기계적 비-PIT 진단

현재 진단 분류는 `mechanical_expanding_walk_forward_non_pit`입니다. 구간 자르기는
미래 행을 막는 기계적 보호일 뿐입니다. 어떤 종목이 역사적 KOSPI 200 구성원이었는지
또는 재무 정보가 신호일 전에 공개되었는지는 증명하지 않습니다.

기본 유니버스는 `proxy_current` 또는 `mcap_proxy` 동작을 사용합니다. 정규화 재무
입력은 회계기간 데이터를 사용하거나 DART를 사용할 수 없을 때 비어 있습니다. 두
경로 모두 필요한 역사적 PIT 근거를 제공하지 않습니다. 명시적 로컬 KRX 원천을
설정한 실행은 proxy 기본 경로와 별개로 즉시 중단(fail closed) 검증을 적용하지만, 현재 공식
진단에는 그 역사 입력이 사용되지 않았습니다.

## 현재 v4 no-DART true-WF 진단

실행 명령:

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward \
  --output /tmp/k200mq_true_wf_v4_no_dart
```

실행일: 2026-08-03

- 공식: `k200mq-momentum-skipped-return-v4`, 기본
  `close[t-42] / close[t-252] - 1`.
- 분류: `mechanical_expanding_walk_forward_non_pit`.
- DART가 설정되지 않아 품질 팩터를 비활성화했습니다. 이는 **모멘텀 전용 기계적
  진단**이며 Momentum + Quality 결과가 아닙니다.
- 5개 폴드가 모두 유효했습니다.
- OOS 범위: 2020-2024 테스트 구간에 걸쳐 1,231개 지점.
- 연결 누적 수익률: **+4.0408%**.
- 연결 최대 낙폭: **-32.0408%**.
- 폴드 테스트 수익률: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**.

제한 사항:

- KOSPI 200 구성원과 횡단면 순위는 비-PIT proxy 입력에 기반합니다.
- 이 실행에서는 DART 재무 데이터를 사용할 수 없었고, 일반 재무 경로도 기본적으로
  비-PIT입니다. 새 로컬 importer는 이 동작을 바꾸지 않으며, 검증된 역사적 DART
  응답 파일이 파이프라인에 연결되어 있지 않습니다.
- 구현된 벤치마크는 KPI200 가격수익률이며 총수익률이 아닙니다.
- ADV 영향, 섹터 한도, PIT 민감도, 생존자 편향 비교, 계획된 스트레스 테스트는
  완료되지 않았습니다.

이 결과는 v4 공식 기준으로 현재의 진단이지만 검증된 성과 근거가 아니며, 정식/운영
성과 주장으로 승격해서는 안 됩니다. 임시 출력은 저장소에 복사하거나 커밋하지
않습니다. 생성되는 파일은 다음과 같습니다.

```text
/tmp/k200mq_true_wf_v4_no_dart/true_walkforward/
  selection_and_folds.json
  summary.csv
  oos_returns.csv
```

## 검증된 PIT 근거

**검증된 PIT 근거는 아직 없습니다.** 저장소에는 provenance validator와 strict-fail
보호 장치가 있지만, 이를 만족하는 충분한 역사 입력이 확보되어 WF 실행에 연결되지
않았습니다.

현재 `true-walkforward` 경로는 `validated_expanding_walk_forward_pit`로 표시할 수
없습니다. strict PIT WF, PIT 민감도, 생존자 편향 비교, ADV 영향, 계획된 스트레스
테스트 및 성과 결론은 모두 보류 상태입니다.

구성원 importer는 구조적 후보 계약을 정의합니다: `index_code`, `as_of_date` 또는
`effective_date`, `security_code`. 원천 메타데이터는 존재할 때 정규화하지만 원시
행의 값을 그대로 신뢰하지 않습니다. 명시적 구간 파일에는 유효일 경계,
action/status, 공표일, provenance도 포함됩니다. 승격에는 별도 수집 매니페스트
사이드카가 필요하며 공식 HTTPS KRX URL, 질의/날짜 파라미터, 시간대가 있는 수집
시각, 허용된 KRX 원천 유형, 명시적 KRX 확인, 원시 바이트와 일치하는 SHA-256을
담아야 합니다. 로컬 경로, `file://`, 파일 수정 시각, 내장 해시, DataFrame,
임의의 PIT 플래그는 검증되지 않은 상태로 남습니다.

현재 KRX 어댑터는 두 날짜의 라이브 스모크 테스트를 통과했지만 원시 파일은 로컬·
미커밋 상태이며, 충분한 역사 범위의 자료를 운영 성과 근거에 연결하지 않았습니다.
명시적 로컬 원천 통합은 이미 `config`·`universe`·`main`에 존재하고 proxy 기본값을
보존합니다. 따라서 이 구현은 연결 경로를 제공하지만 현재 결과를 검증된 근거로
승격하거나 공식 진단을 변경하지 않습니다. 자동화된 테스트 모음에서는 라이브 API를
호출하지 않습니다.

## 폐기/감사 전용 v4 이전 결과

v4 모멘텀 의미 교정 이전에 생성된 모든 결과는 `obsolete_pre_momentum_v4`로
분류하며 현재 결과와 비교하지 않습니다. 이전에 보고된 +207.06%, +14.17%,
+26.97%, +44.6426%, +245-era 수치는 감사 이력으로만 보존합니다. 일부 결과에는
현금 전파 수정 전의 실행도 포함되어 있습니다. 어느 것도 현재 근거, 검증된 결과,
파라미터 선택의 기반이 아닙니다.

## PIT 게이트와 다음 우선순위

현재 다음 우선순위는 다음과 같습니다.

1. `src/k200_mq/data/krx_pit.py`로 필요한 역사적 KOSPI 200 스냅샷을 확보하고,
   날짜·원시 해시·타임스탬프·재로딩 검증을 통과시킨 뒤 명시적 로컬 원천으로
   유니버스 전체 기간을 준비합니다. 이 경로를 설정하지 않으면 proxy 기본값을
   유지합니다.
2. 원시 DART 제출/공시 메타데이터와 재무 사실을 API 또는 벌크 수집으로 확보하고,
   제출일을 안전한 거래 세션 가용일로 매핑하여 회계기간 날짜로 대체하지 않습니다.
3. 두 입력의 PIT provenance가 실제 자료에서 확인된 뒤 strict PIT WF를 다시 실행합니다.
4. strict PIT WF가 통과한 뒤에만 PIT 민감도, 생존자 편향 비교, ADV 영향 및 유동성
   제약, 계획된 스트레스 테스트를 실행합니다.

이 단계가 완료될 때까지 출력 수치는 기계적 진단으로만 취급합니다.

### OpenDART 로컬 계약과 다음 단계

로컬 DART 계층은 다음 OpenDART 응답 계약을 기준으로 설계되어 있습니다.

- 제출 목록: `https://opendart.fss.or.kr/api/list.json`;
- 재무 사실: `https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json`;
- 원시 제출 행은 `corp_code`, `rcept_no`, 날짜 전용 `rcept_dt` (`YYYYMMDD`)를
  보존하고, 사실 행은 동일한 접수 번호와 보고서 키를 보존합니다.
- 다운로드한 각 응답에는 응답 SHA-256과 시간대가 있는 `retrieved_at_utc`를 담은
  별도 JSON 매니페스트가 필요합니다. importer는 원시 바이트와 다이제스트를
  대조하며 DataFrame이나 내장 해시를 수집 근거로 취급하지 않습니다.
- 날짜 전용 제출은 `next_session`을 사용합니다. 즉 Asia/Seoul 현지 달력의 공식
  접수일보다 엄격히 뒤인 첫 제공 KRX 거래일을 사용합니다. 세션 마감 시각은 검증된
  원시 제출 매니페스트와 importer가 발행한 정규화 프레임 fingerprint에 시각 계보가
  포함될 때까지 보류합니다. 조인 후 시각을 주입하는 방식은 거부하며 철회 제출과
  미지정 정정 정책도 거부합니다.

다음 단계는 OpenDART API/벌크 다운로드를 이 원시 로컬 파일과 매니페스트에 연결하는
것입니다. 수집 어댑터와 역사 파일이 준비될 때까지 현재 품질 동작과 모든 비-PIT
진단은 변하지 않습니다.

## 보류 또는 미지원 설정

다음 설정은 호환성 필드 또는 향후 작업으로 남아 있으며 현재 민감도 차원이 아닙니다:
`SECTOR_CAP`, `MIN_ADV_RATIO`, `UNIVERSE_SIZE`, `USE_52WEEK_HIGH`,
`QUALITY_MIN_TTM_QUARTERS`, 그리고
`EXCLUDE_MANAGEMENT`, `EXCLUDE_INVESTMENT_NOTICE`, `EXCLUDE_PREFERRED`,
`EXCLUDE_ETF_ETN` 제외 플래그. `MOMENTUM_WINDOW_SHORT`는
진단 전용입니다.

ADV 계산은 도우미로 존재하지만 ADV 기반 유동성 및 시장 영향 실행은 연결되지
않았습니다. `--no-cache`와 `--rebalance-lookback`은 명시적으로 미지원/보류이며
거부됩니다. 손절 플래그는 `run` 전용이고 `true-walkforward`는 구성/기본값을
사용합니다.

## 출력 산출물

`run`은 `portfolio_snapshots.csv`, `trade_log.csv`, `daily_returns.csv`,
`metrics.json`, `benchmark_returns.csv`, `run_manifest.json`을 기록할 수 있습니다.
`robustness`는 `subperiod_robustness_summary.csv`를 기록합니다.
`true-walkforward`는 `true_walkforward/selection_and_folds.json`, `summary.csv`,
`oos_returns.csv`를 기록합니다. 이 산출물에는 진단/provenance 정보가 포함되지만,
비-PIT 자료를 검증된 근거로 바꾸지는 않습니다.
