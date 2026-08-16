# 2주 실행 체크리스트 (Strict PIT Gate)

목적: 2주 안에 Continue/Hold/Pivot 판단에 필요한 최소 근거를 확보한다.

## 2026-08-06 현재 체크포인트

- Day 4 자동화 경로는 준비되었고, `scripts/run_day4_dart_backfill.sh`로 fetch/aggregate/recheck를 연속 실행할 수 있다.
- Day 4 배치 스펙(`data/raw/dart_batch_spec_day4_missing_both.json`)이 현재 워크스페이스에서 누락되어 있었으나, 누락 종목/법인코드 목록을 재산출한 뒤 동일 조건(7,667 requests)으로 재생성했다.
- 다만 현재 환경에서 `DART_API_KEY`가 미설정이므로, 실제 backfill은 아직 시작되지 않았다.
- 오프라인 union 실험은 strict preflight에서 여전히 조인/커버리지 문제를 드러내, 데이터 확보가 우선 병목임이 확인되었다.
- 다음 실행은 API key 수신 후 바로 진행하는 것이 적절하며, 그 전까지는 성과 판정보다 데이터 게이트 정리를 우선한다.

## 운영 원칙

- 원칙 1: strict preflight 실패는 원인/증거/수정 커밋까지 같은 날 닫는다.
- 원칙 2: 수익률 숫자보다 provenance 완결성을 우선한다.
- 원칙 3: 매일 종료 전 scorecard를 갱신한다.

## 주차별 목표

### Week 1 (데이터/검증 게이트 닫기)

- Day 1
  - strict true-walkforward 1회 실행.
  - 실패/성공 로그를 저장하고 원인 분류(유니버스, DART, 엔진) 기록.
- Day 2
  - 198 constituent 날짜 예외 목록 고정.
  - documented transition exception 초안 작성.
- Day 3
  - DART filing/facts 커버리지 점검(기간/종목/결측 비율).
  - 최소 커버리지 임계치 제안값 확정.
- Day 4
  - 수집 부족 구간 보강(fetch batch 재실행 + aggregate).
  - sidecar manifest 검증 통과 여부 확인.
- Day 5
  - strict true-walkforward 재실행.
  - preflight 실패 0건 목표.

### Week 2 (성과/안정성 게이트 판정)

- Day 6
  - 최신 strict 실행 산출물에서 OOS 지표 산출(CAGR, MDD, Sharpe, Calmar).
- Day 7
  - 후보 파라미터 소폭 변형 실험 1차(예: TOP_N, weight 비율).
- Day 8
  - 후보 파라미터 소폭 변형 실험 2차(예: hold/stop/regime 토글).
- Day 9
  - 서브기간 안정성 점검(구간 편중 여부).
- Day 10
  - Continue/Hold/Pivot 최종 판정.
  - 판정 근거를 문서/커밋/산출물 경로와 함께 고정.

## 일일 점검 항목

- strict preflight 실패 건수 = 0 인가?
- 유니버스 예외가 문서화/보정되었는가?
- DART filing-date 입력 커버리지가 기준 이상인가?
- 오늘 생성한 산출물 경로가 명확한가?
- scorecard가 최신으로 갱신되었는가?

## 완료 기준 (2주 종료 시)

- 필수 게이트 3개(데이터/검증, 성과, 안정성) 상태가 모두 판정됨.
- Go/No-Go scorecard가 숫자와 근거 경로를 포함해 완결됨.
- 최종 의사결정(Continue/Hold/Pivot)이 문서화됨.

## Day 1 실행 기록 (2026-08-04)

- 실행 1 (strict + mixed DART aggregate)
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_pilot_mixed/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mixed/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mixed/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mixed/dart_facts_merged.manifest.json --output outputs_k200mq_day1_20260804`
  - 결과: invalid
  - 핵심 원인: local DART facts/filings 조인 키 무결성 실패로 strict financial availability 확정 불가.
  - 근거: outputs_k200mq_day1_20260804/true_walkforward/summary.csv

- 실행 2 (strict + mapped DART aggregate)
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_pilot_mapped/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mapped/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mapped/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mapped/dart_facts_merged.manifest.json --output outputs_k200mq_day1_20260804_mapped`
  - 결과: invalid
  - 핵심 원인: financial facts 중복/누락 조인으로 strict financial availability 확정 불가.
  - 근거: outputs_k200mq_day1_20260804_mapped/true_walkforward/summary.csv

- Day 1 판정
  - 상태: 실패 분류 완료 (유니버스 통과, DART 조인 무결성 실패)
  - 다음 액션: Day 2에서 DART duplicate/missing join 정리 우선 수행.

- 실행 3 (strict + mixed key-dedup DART aggregate candidate)
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.manifest.json --output outputs_k200mq_day1_20260804_mixed_keydedup`
  - 결과: invalid
  - 핵심 원인: DART filing-date를 KRX 세션으로 매핑하는 단계에서 실패 (`one or more filings cannot be mapped to a provided KRX session`).
  - 근거: outputs_k200mq_day1_20260804_mixed_keydedup/true_walkforward/summary.csv

## Day 2 진행 기록 (2026-08-05)

- 코드 수정 1
  - 내용: DART filing-date 세션 매핑 실패 시 `unmapped` 건수, 세션 범위, 예시 `(corp_code, rcept_no, rcept_date)`를 포함한 진단 메시지를 추가.
  - 목적: strict preflight 실패를 당일에 바로 분류할 수 있도록 원인 가시성을 높임.

- 코드 수정 2
  - 내용: local DART filing/facts 입력에서 prepared session 상한(이번 실행은 2024-12-30) 이후의 접수 행을 사전 제외하도록 로더를 보강.
  - 목적: 2025 접수 자료가 2024 세션 검증을 막는 비본질적 실패를 제거.

- 테스트
  - 명령:
    - `uv run pytest -q tests/test_k200_mq_dart_pit.py tests/test_k200_mq_configuration.py`
  - 결과: `41 passed, 2 warnings`
  - 의미: DART 진단 메시지 보강과 future receipt 범위 필터링 회귀 테스트를 포함해 관련 단위 검증 통과.

- strict 재실행 1회
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.manifest.json --output /tmp/k200mq_day2_20260805_strict_recheck`
  - 결과: 완료
  - 개선 확인:
    - 이전 blocker였던 `one or more filings cannot be mapped to a provided KRX session`는 재현되지 않음.
    - 최종 분류는 `mechanical_expanding_walk_forward_non_pit`로 유지됨.
  - 해석:
    - DART 조인/세션 범위 문제는 일부 정리되었고, strict 실행은 산출물까지 생성했지만 현재 aggregate는 여전히 validator-backed `pit_filing_date` 계약으로 승격되지 못함.

- Day 2 판정
  - 상태: 부분 완료
  - 닫힌 항목: DART 세션 매핑 실패 원인 식별, 비본질적 미래 접수행 차단, strict 실행 재현.
  - 미완료 항목: validator-backed financial provenance 승격.
  - 다음 액션:
    - strict financial provenance가 왜 `non_pit_fiscal_period`로 남는지 진단한다.
    - 198 constituent 역사 날짜 예외는 `data/universe/kospi200_bundle_strict/bundle.manifest.json`의 `transition_exceptions_by_as_of`에 120개 월말 날짜(2015-01-30 ~ 2024-12-31)로 이미 고정되어 있으므로, 이를 문서에서 요약하고 전이 예외 초안을 최종화한다.

- 예외 요약
  - 범위: 2015-01-30 ~ 2024-12-31
  - 개수: 120개 월말 리밸런싱 일자
  - 성격: 각 날짜의 구성원 수가 198인 documented historical transition size
  - 근거: `data/universe/kospi200_bundle_strict/bundle.manifest.json`

## Day 3 진행 기록 (2026-08-05)

- DART filing/facts 커버리지 측정
  - 대상 입력:
    - `data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.csv`
    - `data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.csv`
    - `data/universe/kospi200_bundle_strict/bundle.manifest.json` (120개 월말 as-of)
  - 측정 방법:
    - `prepare_financial_facts(..., amendment_policy="first_filing")`로 strict 로컬 DART 준비 프레임을 생성
    - 각 as-of에서 `availability_session <= as_of`인 고유 `stock_code` 수를 198로 나누어 커버리지 비율 계산
  - 측정 결과:
    - filings 행수: 10,465
    - facts 행수: 8,996
    - prepared 행수: 7,381
    - facts 키 결측 비율: 0.00% (0 / 8,996×9)
    - filing date 범위: 2016-03-30 ~ 2024-03-28
    - prepared 고유 종목수: 7개
    - as-of 커버리지 비율(min/median/max): 0.0000 / 0.0303 / 0.0354
    - as-of 커버드 종목수(min/median/max): 0 / 6 / 7
    - 최저 5개 as-of: 2015-01-30, 2015-02-27, 2015-03-31, 2015-04-30, 2015-05-29 (모두 0)

- Day 3 임계치 제안 (초안)
  - 제안 A(연구 지속 최소선):
    - 전체 as-of median coverage_ratio >= 0.80
    - 전체 as-of min coverage_ratio >= 0.60
  - 제안 B(strict PIT WF 실행선):
    - 전체 as-of median coverage_ratio >= 0.90
    - 전체 as-of min coverage_ratio >= 0.80
  - 현재 상태:
    - median 0.0303, min 0.0000으로 제안 A/B 모두 미달
    - Day 4 우선순위로 수집 구간 보강(fetch batch 재실행 + aggregate 재생성)을 즉시 수행해야 함

## Day 4 진행 기록 (2026-08-05)

- 백필 타깃 산출 (strict universe vs current DART prepared)
  - strict 유니버스 고유 종목수: 198
  - 현재 prepared 커버 종목수: 7
  - 미커버 종목수: 191
  - corp_map 매핑 가능 미커버 종목수: 187
  - corp_map 미매핑 종목수: 4 (`000155`, `005385`, `005387`, `005935`)
  - 산출 파일:
    - `data/raw/k200_day4_missing_tickers.txt` (191 lines)
    - `data/raw/k200_day4_missing_corp_codes.txt` (187 lines)

- Day 4 배치 스펙 생성 완료
  - 생성 명령:
    - `uv run python scripts/generate_dart_fetch_batch_spec.py --mode both --corp-codes-file data/raw/k200_day4_missing_corp_codes.txt --filing-bgn-de 20150101 --filing-end-de 20241231 --financial-start-year 2015 --financial-end-year 2024 --reprt-codes 11011,11013,11012,11014 --output-file data/raw/dart_batch_spec_day4_missing_both.json`
  - 생성 결과:
    - `data/raw/dart_batch_spec_day4_missing_both.json`
    - request spec count: 7,667

- Day 4 다음 실행 순서 (API key 필요)
  1. 배치 fetch 실행
     - `uv run python scripts/fetch_local_dart_response.py --api-key "$DART_API_KEY" --batch-file data/raw/dart_batch_spec_day4_missing_both.json --output-dir data/raw/dart_batch_day4_missing --continue-on-error`
  2. aggregate 재생성
     - `uv run python scripts/build_local_dart_aggregates.py --input-dir data/raw/dart_batch_day4_missing --output-dir data/raw/dart_aggregated_day4_missing`
  3. strict true-walkforward 재실행
     - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_day4_missing/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_day4_missing/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_day4_missing/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_day4_missing/dart_facts_merged.manifest.json --output /tmp/k200mq_day4_20260805_strict_recheck`
  4. Day 3 지표 재측정 및 scorecard 재판정

- Day 4 실행 결과 (2026-08-05, 추가)
  - 환경 확인:
    - `DART_API_KEY=unset` 확인 (배치 fetch 즉시 실행 불가)
  - 오프라인 대안 검증:
    - `dart_batch_pilot_mapped` + `dart_batch_pilot_mixed` union으로
      `data/raw/dart_aggregated_day4_union_local` 재생성 시도
    - aggregate 생성 자체는 성공 (filings 19,887 / facts 93,048)
    - 그러나 strict 준비 조인에서 무결성 실패:
      - duplicate `(corp_code, rcept_no)`
      - facts duplicate identity
      - missing filing joins
  - 유효 조합 탐색 결과:
    - 로컬 조합 30개 중 strict 준비 통과 조합 5개
    - 통과 조합의 coverage는 기존과 동일:
      - min 0.0000 / median 0.0303 / max 0.0354
  - 결론:
    - 네트워크 신규 수집(fetch) 없이는 Day 4 coverage 개선 불가
    - 다음 액션의 선행조건은 `DART_API_KEY` 세팅

- Day 4 재개 자동화 (2026-08-05, 추가)
  - 신규 스크립트:
    - `scripts/run_day4_dart_backfill.sh`
  - 목적:
    - fetch -> aggregate -> strict rerun을 one-shot으로 실행
  - 검증:
    - API 키 미설정 상태에서 guard 동작 확인 (`exit_code=2`)
  - 실행:
    - `export DART_API_KEY="..."`
    - `./scripts/run_day4_dart_backfill.sh`
  - 선택 파라미터(환경변수 override):
    - `SPEC_FILE`, `BATCH_OUT_DIR`, `AGG_OUT_DIR`, `RUN_OUT_DIR`

## Day 4 재개 점검 기록 (2026-08-06)

- 점검 결과
  - `scripts/run_day4_dart_backfill.sh` 최초 실행 시 `Missing batch spec`로 중단됨(`exit_code=1`).
  - 원인: `data/raw/dart_batch_spec_day4_missing_both.json` 파일 부재.
- 복구 조치
  - strict 유니버스(198) 대비 현재 DART facts-join 커버(7)를 재계산해 결손 목록 재생성:
    - `data/raw/k200_day4_missing_tickers.txt` (191)
    - `data/raw/k200_day4_missing_corp_codes.txt` (187)
    - 미매핑 4개: `000155`, `005385`, `005387`, `005935`
  - 동일 파라미터로 배치 스펙 재생성:
    - `data/raw/dart_batch_spec_day4_missing_both.json` (7,667 requests)
- 재검증
  - 스크립트 재실행 시 `DART_API_KEY is not set` 가드로 중단됨(`exit_code=2`).
  - 해석: 자동화 경로 자체는 정상 복구되었고, 현재 유일한 선행조건은 API key 설정.

## Day 4 쿼터 실패 및 재개 준비 기록 (2026-08-06, 추가)

- 실패 원인 (확정)
  - `DART_API_KEY` 설정 후 one-shot fetch 시도에서 7,667건(187 filing + 7,480 financial) 요청이
    전부 OpenDART `status=020`(사용한도 초과)으로 반환됨.
  - raw 응답·manifest 모두 `api_status=020`, `verified=False`이며 `batch_summary.json`은 7,667건 기록.
  - aggregate는 "no valid filing raw files were found"로 정상 중단(`exit_code` 비0). fetch/aggregate 스크립트 버그가 아님.
- 재발 방지 및 재개 보강 (커밋)
  - `scripts/fetch_local_dart_response.py`:
    - fetch 후 `verified=0`이면 aggregate 전 중단 게이트를 runner에 추가(2026-08-06).
    - `--skip-verified`: 이미 verified(`api_status` 000/0)인 output/manifest가 있으면 재수집 건너뜀.
    - `--delay-seconds`: 요청 간 대기로 초당 제한 회피(기본 0).
  - `scripts/run_day4_dart_backfill.sh`:
    - `FETCH_START_INDEX` / `FETCH_MAX_REQUESTS`(청크 범위), `FETCH_DELAY_SECONDS` 지원.
    - `FETCH_ONLY=1`이면 fetch + 상태 게이트만 수행하고 aggregate/strict rerun 생략.
    - fetch 호출에 `--skip-verified --delay-seconds` 상시 적용.
- 근본 제약
  - OpenDART 무료 티어 일일 쿼터(일반적으로 2만 건 수준)를 초과하면 020 반환. 일일 리셋 후 재시도 필요.
  - 실행 전제: `export DART_API_KEY="..."` 세팅.
- 연간 전용 축소 스펙 (2026-08-06, 추가)
  - 배경: 7,667건 전체가 하루 쿼터 내 실행되기 어려워, 품질 팩터가 실제로 필요한 요청만 남기기로 함.
  - 검증: 통제된 fixture로 연간(11011)만으로 `prepare_financial_facts` → PIT 검증(`pit_valid=True`)까지 무결성 통과를 확인.
    - 품질 팩터 입력은 `revenue/cogs/net_income/operating_cf/total_assets/total_equity` 6개 컬럼이고,
      `_convert_financial_to_daily`(main.py:1062-1068)가 신호일 기준 최신 facts를 ffill하므로 기간 코드 구분 없이 동작.
    - 품질 팩터 TTM 필터는 inert(`min_ttm_quarters` 미사용, factors/quality.py:136)라 분기 데이터가 필수가 아님.
  - 생성 명령:
    - `uv run python scripts/generate_dart_fetch_batch_spec.py --mode both --corp-codes-file data/raw/k200_day4_missing_corp_codes.txt --filing-bgn-de 20150101 --filing-end-de 20241231 --financial-start-year 2015 --financial-end-year 2024 --reprt-codes 11011 --output-file data/raw/dart_batch_spec_day4_missing_annual.json`
  - 결과: `data/raw/dart_batch_spec_day4_missing_annual.json` — filing 187 + financial 1,870 = **2,057건** (기존 7,667 대비 3.73배 감소).
  - 판정: 연간만으로 품질 팩터 입력이 확보되므로, 재개 실행은 **축소 스펙을 우선 사용**한다.
- 재개 런북 (쿼터 리셋 + 키 세팅 후)
  1. 작은 청크로 성공 확인 (축소 스펙 2,057건 사용):
     - `SPEC_FILE=data/raw/dart_batch_spec_day4_missing_annual.json FETCH_ONLY=1 FETCH_START_INDEX=1 FETCH_MAX_REQUESTS=200 ./scripts/run_day4_dart_backfill.sh`
  2. 성공하면 범위를 확대하며 반복 (예: 200건 단위):
     - `SPEC_FILE=data/raw/dart_batch_spec_day4_missing_annual.json FETCH_ONLY=1 FETCH_START_INDEX=201 FETCH_MAX_REQUESTS=200 ./scripts/run_day4_dart_backfill.sh`
     - ... `FETCH_START_INDEX=1901`까지 (2,057건 누적)
  3. 전체 청크 verified 누적 확인 후 원샷 완주:
     - `SPEC_FILE=data/raw/dart_batch_spec_day4_missing_annual.json ./scripts/run_day4_dart_backfill.sh`  (FETCH_ONLY 해제 → aggregate + strict rerun)
  4. Day 3 지표 재측정 및 scorecard 재판정
- 보안 권고
  - 이전 실행 중 프로세스 목록에 API 키가 노출될 수 있었으므로 키 재발급(회전)을 권장.
- strict 유니버스 전이 예외 최종화 (2026-08-06, 추가)
  - `data/universe/kospi200_bundle_strict/bundle.manifest.json`의 `transition_exceptions_by_as_of`를 재확인:
    - 120개 월말 날짜(2015-01-30 ~ 2024-12-31) 모두 `allowed_sizes: [198]`로 이미 고정.
    - 실제 CSV도 모든 날짜가 198 구성원으로 일치(`pit_universe.py`가 bundle manifest의 예외를 로드해 검증에 반영).
  - 결론: 198-member strict 게이트 병목은 **이미 manifest 전이 예외로 해소된 상태**이며, Day 4 재개 시 별도 조치 불필요.

## 품질 의미론 및 결측 공개

- `max(revenue - cogs, 0) / revenue`는 floored gross-profit / gross-margin
  proxy이며 true operating income/margin이 아니다. six-fact 중 하나라도 빠진
  원천 row는 quality-scored 대상이 아니고, 최종 factor merge에서 허용되는 품질
  결측만 neutral-fill(0)된다.

## 품질 배선 수정 (2026-08-06, 추가)

- 확인: 로컬 DART facts가 품질 팩터로 흐르지 않던 배선 결함 2건을 oracle 검토로 확정하고 수정했다.
  - 공용 매핑: `src/k200_mq/data/account_mapping.py`의 `ACCOUNT_COLUMN_MAPPING`이 계정명/계정코드를 wide 6컬럼(revenue, cogs, net_income, operating_cf, total_assets, total_equity)으로 매핑(정규화 로더 API 경로와 공유).
  - pivot: `dart_pit.pivot_financial_facts_to_wide`가 long format facts를 wide로 피벗해 `_load_local_dart_financial_inputs`에 연결.
  - 게이트: `main.py` 품질 활성 조건을 `DART_API_KEY` 단독에서 `(DART_API_KEY or 로컬 원천 준비) and daily_financial 비어있지 않음`으로 수정.
- 검증: `tests/test_k200_mq_local_dart_quality.py` 통합 테스트로 ROE 8.3% / D/E 0.67 / gross-margin proxy 60% / CashConv 0.8 확인, 관련 스위트 전체 통과(408 passed + 기존 무관 실패 1건), ruff clean.
- 함의: 연간(11011) 축소 스펙 판정이 품질 팩터 실제 소비 경로에서 검증됨. API 키 확보 후 축소 스펙(2,057건)으로 재개하면 로컬 DART 품질 입력이 strict 실행에 반영된다.

## 실파일 스모크 + 매핑 커버리지 (2026-08-06, 추가)

- 스모크 실행 (keydedup pilot DART + bundle 유니버스, `run` 2020-2024):
  - 결과: manifest에서 `data_mode=pit_filing_date`, `pit_valid=true`, `filing_date_used=true`,
    `quality_factor_row_count=10,990` / `quality_factor_ticker_count=7` — 로컬 DART → 품질 팩터
    경로가 실데이터에서 활성화됨을 확인. (진단이며, 커버 7종목뿐이라 검증된 성과 근거 아님)
- 매핑 커버리지 (실제 facts 57개 보고서):
  - revenue/net_income/operating_cf/total_assets/total_equity **100%**, cogs **96.5%**.
  - cogs 미매칭 2건은 금융사 00104856(수수료/이자 계정)으로 COGS 자체가 없어 정상.
  - 실제 갭 1건 수정: `매출 원가`(공백 포함)를 cogs 후보에 추가(commit 예정).
  - equity 오염 확인: 57/57 보고서가 exact `자본총계`/`ifrs_Equity` 행을 가져
    `부채 및 자본총계`(부채+자본) containment가 선행되지 않음. equity ≤ assets 전건 확인.
- 회귀: 관련 스위트 49 passed, 전체 409 passed + 기존 무관 레거시 실패 1건, ruff clean.
- 함의: 191개 신규 종목 수집 전에 매핑이 대부분의 실제 계정명을 처리함을 확인. cogs처럼
  공백 변형 계정명이 더 발견되면 후보를 확장한다.

## Option D 실행 — 7종목 DART 품질 기계적 WF (2026-08-06, 추가)

- 배경: strict PIT 로컬 상한이 7종목임을 확정(로더는 `(corp_code, rcept_no)`로 전 facts→filings
  조인을 요구하고, mapped 배치는 corp당 filing 1페이지뿐, 20 corp는 filing 파일 부재,
  `rcept_no` 일자 파생은 `_reject_fact_provenance_collisions`가 금지). Oracle 검토로
  7종목 strict 실행은 infra 검증으로도 미미(대형주 서바이버 편향)하므로 보류하고,
  **로컬 가용 데이터로 최대의 기계적 진단**(Option D)을 우선 실행하기로 결정.
- 실행 명령:
  - `uv run python -m k200_mq.main true-walkforward --local-dart-filing-path data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.manifest.json --output outputs_k200mq_mechanical_full --exclude-kospi-top-n 0`
- 결과 (keydedup 7종목 DART + bundle 유니버스, 2015-2024 expanding WF):
  - 품질 팩터: 재무 입력 51행 → 품질 팩터 18,305행 계산 (로컬 DART provenance 활성)
  - 폴드별(모두 TOP_N_10 선택, status=valid):
    - 2020: +27.1% / Sharpe 1.32 / MDD -12.6%
    - 2021: +4.3% / Sharpe 0.13 / MDD -13.1%
    - 2022: -6.4% / Sharpe -0.90 / MDD -14.6%
    - 2023: +13.1% / Sharpe 0.61 / MDD -20.9%
    - 2024: +27.9% / Sharpe 1.13 / MDD -20.5%
  - Stitched OOS: 수익률 +79.7% (5년), CAGR 12.4%, MDD -20.9%, OOS 1,231점 (2020-2024)
- 분류: `mechanical_expanding_walk_forward_non_pit` — 유니버스가 현재 시점 mcap proxy
  (`contract: current_market_cap_snapshot`)라 **검증된 성과 주장이 아님**. 성과 자체보다
  7종목 PIT 품질 → 엔진 전체 경로가 기계적 WF에서 동작함을 확인한 것이 본 실행의 가치.
- 산출물: `outputs_k200mq_mechanical_full/true_walkforward/{summary.csv,oos_returns.csv,selection_and_folds.json}`
- 함의: strict PIT 근거 승격은 여전히 광범위 DART filing 메타데이터 확보(API 키·쿼터) 후에만 가능.

## Day 8 실행 기록 (2026-08-10, strict PIT WF)

- 성능 선행 작업 (커밋 `238a4de`)
  - `dart_pit.py` `_map_one_session`을 행별 부울 마스크에서 `searchsorted`(O(log N))로 교체.
  - `_drop_future_unmappable_rows`를 per-row `iloc` 파싱 제거 + unmapped 행만 파싱하도록 벡터화.
  - `ord`(순번) 컬럼을 financial fact identity에 추가, `account_name`+`ord`를
    `_join_errors`/`_amendment_group_columns`/`_resolve_amendments` required_group에 반영.
  - 준비 파이프라인: 8분+ → ~82초 (실행 확인: load 12s + join 19s + map 40.3s).
  - 검증: ruff 통과, dart 테스트 50개, 전체 409 passed + 기존 무관 레거시 실패 1건.
- strict 실행
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_day4_extended/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_day4_extended/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_day4_extended/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_day4_extended/dart_facts_merged.manifest.json --output outputs_k200mq_day8_strict_extended`
  - 결과: 완료, 5/5 폴드 `valid=True`, OOS 1,231점 (2020-2024).
  - strict preflight 실패: 0건. financial provenance `pit_filing_date` + `pit_valid=true`
    (이전 strict 실행의 `non_pit_fiscal_period`에서 승격), 유니버스 전 as-of `provenance=pit`.
  - 분류: `mechanical_expanding_walk_forward_non_pit` 유지 (검증된 성과 주장 아님).
- OOS 성과 (stitched, 2020-2024)
  - 총수익률 +57.79%, CAGR 9.79%, Sharpe 0.737, MDD -23.40%, Calmar 0.418.
  - 폴드별: 2020 +21.7% / 2021 +0.5% / 2022 -7.8% / 2023 +22.2% / 2024 +15.8%.
- 커버리지 잔여 갭
  - 첫-ready 리밸런스(2015-05-29): `momentum_z` usable 147/198, missing 51
    티커(`quality_required=false`).
  - quality: `partial_allowed_fill_missing_with_zero`, covered 169 ticker / factor 393,522행.
  - 갭을 닫고 `validated_expanding_walk_forward_pit` 승격을 목표로 PIT 민감도·생존자 편향 비교·
    ADV 영향·스트레스 테스트가 다음 우선순위.
- 산출물: `outputs_k200mq_day8_strict_extended/true_walkforward/{summary.csv,oos_returns.csv,selection_and_folds.json}`
- scorecard: `docs/planning/08_go_no_go_scorecard_2026-08-10.{md,csv}` (임시 판정: Continue 조건부)

## Day 8 커버리지 갭 루트코즈 (2026-08-11, 추가; superseded erratum)

- 분석 문서: `docs/planning/09_coverage_gap_analysis.md`
- **Erratum (2026-08-13)**: 아래 2026-08-11 기록의 51-ticker financial-gap
  해석은 superseded/폐기한다. Day 8 `first_ready_rebalance`의 147/198 및
  missing 51은 `momentum_z` readiness와 가격 warmup 결과이며, financial/quality
  coverage가 아니다. `quality_required=false` (`src/k200_mq/main.py:1150-1176`).
- 아래 D/B/C 분류와 보강 계획은 당시 재무-gap 가설을 보존하는 감사 이력일 뿐, Day 8
  momentum readiness의 원인 또는 해결책으로 사용하지 않는다.
- **기존 기록의 결론(폐기)**: 첫 리밸런스(2015-05-29) missing 51 티커는 3유형.
  - **D (7)**: 2015-05-29 이전 사업보고서(2014.12) 제출했으나 facts 미수집 — 실제 수집 갭.
  - **B (9)**: 2015-05-29 시점 사업보고서 없음 + facts 미수집 — 유니버스 proxy + 수집 갭 혼재.
  - **C (35)**: 신규상장/후기 편입으로 2015-05-29 시점 데이터 부재가 정상 — 유니버스 proxy.
- 근본 원인: DART 배치 스펙이 `--financial-start-year 2015`로 생성되어 FY2014 보고서
  (2015년 3월 제출) facts가 전체에서 누락됨. 2015-05-29 이전 제출 사업보고서 203건
  (141 corp) 중 facts 첨부 0건.
- 특이 케이스: 064400/085620/089860은 2015~2020회계연도 facts가 corp별 불균등 수집으로
  유실(각각 2023~2024/2021~2024만 보유). 별도 보강 필요.
- 준비된 보강 스펙:
  - `data/raw/dart_batch_spec_fy2014_backfill.json` (141 corp × FY2014 = 141건)
  - `data/raw/dart_batch_spec_corp_specific_backfill.json` (3 corp × 2014~2020 = 21건)
  - 대상 corp: `data/raw/k200_fy2014_backfill_corps.txt` (141),
    `data/raw/k200_corp_specific_missing_corps.txt` (3)
- 재개 런북 (DART_API_KEY 설정 후):
  1. FY2014 보강 fetch:
     `SPEC_FILE=data/raw/dart_batch_spec_fy2014_backfill.json BATCH_OUT_DIR=data/raw/dart_batch_fy2014 AGG_OUT_DIR=data/raw/dart_aggregated_day4_extended_fy2014 RUN_OUT_DIR=/tmp/k200mq_fy2014_recheck FETCH_ONLY=1 ./scripts/run_day4_dart_backfill.sh`
     (쿼터 예의: `FETCH_START_INDEX`/`FETCH_MAX_REQUESTS`/`FETCH_DELAY_SECONDS` 청크)
  2. corp별 누락 보강 fetch:
     `SPEC_FILE=data/raw/dart_batch_spec_corp_specific_backfill.json BATCH_OUT_DIR=data/raw/dart_batch_corp_specific AGG_OUT_DIR=data/raw/dart_aggregated_day4_extended_corp_specific RUN_OUT_DIR=/tmp/k200mq_corp_specific_recheck ./scripts/run_day4_dart_backfill.sh`
  3. 두 배치를 기존 `dart_aggregated_day4_extended/`에 union 반영 후 strict 재실행
     (로더의 `(corp_code, rcept_no)` 조인 무결성이 유지되는 방식으로 병합 필요).
  4. Day 3 coverage 지표 재측정 + scorecard 재판정.
- 제약: 유니버스 proxy 특성(B/C 44개)은 수집으로 해결 불가 — 역사적 KOSPI 200 구성원
  데이터로 유니버스를 PIT화해야 함.

## Phase 3 FY2014 XBRL 현황 (2026-08-13)

- FY2014 원본 접수 141건 선정.
- 검증된 XBRL ZIP 119건.
- strict six-fact accepted 92건.
- 요청한 XBRL 문서를 이용할 수 없음을 나타내는 OpenDART 공식 상태 `014` 22건
  (단순한 로컬 파일 누락이 아님).
- parser fail-closed 27건.
- FY2014 XBRL 보강은 재무 PIT facts를 개선하지만, `momentum_z` 가격 warmup을
  충족시키거나 first-ready 147/198을 변경하는 해결책은 아니다.

## Day 10 실행 기록 (2026-08-15, strict PIT WF + FY2014 XBRL 병합)

- 배경: Day 8/9의 첫 리밸런스(2015-05-29) financial six-fact 커버리지 0/198은
  배치 스펙 `--financial-start-year 2015`로 FY2014 facts가 전체 누락된 것이 근본
  원인. FY2014 XBRL 파이프라인(141 수용 → 92 strict accepted, 6 facts/corp)이
  구축되어 있었으나 확장 집계(`dart_aggregated_day4_extended`)에 반영되지 않았다.
- 병합 스크립트 (신규, 커밋 `0113611`):
  - `scripts/merge_fy2014_xbrl_into_aggregate.py`
  - 확장 facts CSV를 `load_financial_facts`(사이드카 매니페스트 검증)로 로드하고,
    92개 XBRL 아티팩트 각각을 `.derived.manifest.json`(XBRL provenance 체인 포함)으로
    검증 로드 후 concat + dedup.
  - 출력: `data/raw/dart_aggregated_day4_extended_fy2014/` (facts 304,245행 =
    확장 303,693 + FY2014 552, dedup 0건; reload 검증 `verified=True`).
  - filings CSV + 매니페스트는 그대로 복사 (92개 접수는 이미 존재).
- strict 실행
  - 명령: Day 9와 동일하되 `--local-dart-*` 4개 경로를
    `data/raw/dart_aggregated_day4_extended_fy2014/`로 교체, 출력
    `outputs_k200mq_day10_strict_extended_fy2014` (소요 ~80분).
  - 결과: 5/5 폴드 `valid=True`, OOS 1,231점 (2020-2024), preflight 실패 0건.
  - 분류: `mechanical_expanding_walk_forward_non_pit` 유지 (검증된 성과 주장 아님).
- 커버리지 개선 (핵심)
  - 첫 리밸런스 2015-05-29: six-fact available **0/198 → 92/198**.
  - 2015-03-31부터 8/198, 2015-04-30부터 92/198 (FY2014 보고서 접수일 2015-03~04).
  - 재무 데이터: 1,223행 → 1,775행 (256,204 → 283,664 커버리지 행), 품질 커버리지
    티커 138/187 (73.8%)은 Day 9와 동일 (FY2014 추가는 초기 구간에만 영향).
- OOS 성과 (stitched, 2020-2024): Day 9와 동일
  - 폴드별: 2020 +25.1% / 2021 +2.4% / 2022 -11.5% / 2023 +9.2% / 2024 +16.4%
    (모두 Day 9와 동일 수치; 후보 선택 TOP_N_10/BASE 순위 불변).
  - train 기간(2015-2019) 팩터는 실제로 변경됨 (fold1 train_scores:
    BASE n_exits 768→757, TOP_N_10 Sharpe -0.061→-0.144 등) — FY2014 반영 확인.
  - OOS 동일 원인: OOS 구간 quality는 2015+ 재무 데이터만 사용하므로 FY2014 병합이
    2020-2024 OOS 팩터·후보 순위에 영향을 주지 않음. 정상.
- 잔여 갭 (불변): momentum_z readiness 첫 리밸런스 147/198 (가격 warmup, FY2014와
  무관), 유니버스 proxy(B/C 44개)는 역사적 KOSPI 200 구성원으로만 해결.
- 산출물: `outputs_k200mq_day10_strict_extended_fy2014/true_walkforward/{summary.csv,oos_returns.csv,selection_and_folds.json}`
- 다음 우선순위: momentum warmup/readiness 검토 → PIT 민감도, 생존자 편향 비교,
  ADV 영향, 스트레스 테스트 (classification 승격 전제조건은 유니버스 PIT화).

## Momentum warmup/readiness 검토 (2026-08-16)

- 목적: 첫 리밸런스(2015-05-29) momentum_z readiness 147/198의 missing 51개가
  가격 데이터 수집 누락인지, 유니버스 proxy 특성인지 확정.
- 방법: `kospi200_2015-05-29.csv` 유니버스 198개를 6자리 코드로 정규화하고,
  `data/raw/price_*.parquet`(2014-2024)에서 2015-05-29 이전 관측치 수와 첫 가격일을
  집계. momentum 필요 관측치 = 253 (t-252 ~ t).
- 결과: 147 sufficient + 47 가격 데이터 없음 + 4 warmup 부족 = 198 (정확 일치).
  - **47개 가격 데이터 없음** = 전부 2015-05-29 이후 첫 가격일:
    - 42개: 2015-07-08(085620) ~ 2024-09-27(489790) 사이 첫 가격일 (상장 전).
    - 5개: 2025년 이후 상장 (031210 서울보증보험, 064400 LG씨엔에스, 279570
      케이뱅크, 439260 대한조선, 483650 달바글로벌) — 2014-2024 가격 데이터에
      존재하지 않음이 정상.
  - **4개 warmup 부족**: 018260(132 obs, 2014-11-14~), 028260(108 obs,
    2014-12-18~), 112610(123 obs, 2014-11-27~), 204320(160 obs, 2014-10-06~) —
    2014년 하반기~2015년 초 첫 가격일 (상장일이 늦음).
- 결론: missing 51개는 **가격 데이터 수집 누락 0건**이며 전부 유니버스 proxy
  특성(현재 시점 KOSPI 200 구성원을 2015-05-29에 투영)에서 기인. 가격 warmup
  자체는 정상 동작 (147개는 253+ 관측치 보유). 이 갭은 수집으로 해결 불가 —
  역사적 KOSPI 200 구성원 데이터로 유니버스를 PIT화해야만 닫힘.
- 함의: momentum readiness 갭은 유니버스 PIT화와 동일한 단일 게이트로 해소되며,
  별도 가격 데이터 보강 작업이 필요하지 않음.

## Day 11-13 파라미터 진단 (2026-08-16, ADV/민감도/스트레스)

- 실행기: `scripts/run_day11_13_diagnostics.sh` (커밋 `3c225d9`, 순차 백그라운드).
  Day 10과 동일한 strict-PIT + FY2014 병합 입력, 환경 변수로 단일 설정만 변경.
  모두 5/5 폴드 valid, OOS 1,231점, `mechanical_expanding_walk_forward_non_pit` 유지.
- **Day 11 — ADV 유동성 필터** (`ENABLE_ADV_FILTER=True`, `outputs_k200mq_day11_adv_filter`)
  - Stitched OOS **-1.37%** (Day 10 +44.15% 대비 큰 악화). 전 폴드 BASE 선택.
  - 연도별: 2020 +31.9% / 2021 -2.9% / 2022 -3.6% / 2023 +2.0% / 2024 **-21.7%**.
  - ADV 필터가 후보 풀을 크게 축소(5~17개 선정)해 분산 효과가 무너진 것으로 해석.
    MIN_ADV_RATIO=1% 임계값이 현행 유니버스에 과도하게 제한적일 수 있음.
- **Day 12 — 모멘텀 가중 민감도** (`WEIGHT_MOMENTUM=0.7, WEIGHT_QUALITY=0.3`,
  `outputs_k200mq_day12_sensitivity_mom70`)
  - Stitched OOS **+71.42%** (기준 0.5/0.5 대비 개선). 전 폴드 TOP_N_10 선택.
  - 연도별: 2020 +31.3% / 2021 +0.4% / 2022 -8.8% / 2023 +17.9% / 2024 +20.9%.
  - 모멘텀 가중치 상향이 전 구간에서 순위 개선 신호 (기계적 진단 한계 내).
- **Day 13 — 손절 비활성 스트레스** (`ENABLE_STOP_LOSS=False`,
  `outputs_k200mq_day13_stress_nostop`)
  - Stitched OOS **+117.00%** (가장 높은 수익률)이나 후보 선택이 REGIME_OFF로
    바뀌고 2020 MDD -37.4%로 급증 — 손절이 MDD 방어에 기여함을 확인.
  - 연도별: 2020 +37.4% / 2021 +2.5% / 2022 -12.0% / 2023 +40.1% / 2024 +24.9%.
- 종합 (모두 기계적 non-PIT 진단): ① ADV 필터는 현 임계값에서 성과를 크게
  악화시켜 재검토 필요, ② 모멘텀 가중 상향이 개선 신호, ③ 손절 활성은 MDD
  방어에 유효. 어느 것도 검증된 성과 주장이 아니며 유니버스 PIT화 전에는
  민감도 결론으로 사용하지 않음.
- 남은 게이트: 생존자 편향 비교(역사 구성원 필요), 유니버스 PIT화 후 최종 재검증.

## Day 14 — 유니버스 PIT화 + 생존자 편향 비교 (2026-08-16)

- 배경: momentum readiness 검토(커밋 `b9ad9c1`)에서 갭 51개 전부가 유니버스
  proxy 특성임을 확정. classification 승격의 유일한 데이터 게이트인 유니버스
  PIT화를 진행.
- 역사적 KOSPI 200 구성원 수집:
  - `scripts/fetch_kospi200_pit_snapshots.py` (신규): pykrx
    `get_index_portfolio_deposit_file`(KRX 공식, 과거 날짜 지원, KRX_ID/KRX_PW
    필요 — .env 보유)로 120개 as-of 날짜(2015-01-30 ~ 2024-12-31)의 실제
    구성원을 fetch → `data/universe/kospi200_bundle_pit_src/kospi200_*.parquet`
    (323 유니크 티커, 스냅샷 크기 200~202).
  - `scripts/build_local_pit_universe_bundle.py --source-is-krx`로
    `data/universe/kospi200_bundle_pit/` 번들 빌드 (120개 per-date CSV + sidecar
    manifest + bundle.manifest.json).
- 가격 데이터 보강: PIT 유니버스 323개 중 가격 캐시에 없던 155개 티커를
  loader `get_price_data`로 백필 (2014-2024 전체 기간, 상장폐지 포함 0 missing
  확인). 2014 캐시도 323개 티커로 확장.
- 생존자 편향 정량화 (proxy vs PIT):
  - 2015-05-29 기준: proxy 198 vs PIT 200, **일치 108 / proxy-only 90 /
    pit-only 92** — proxy 구성원의 ~46%가 실제 역사 구성원과 다름.
  - PIT 323 유니크 vs proxy 198 (proxy는 전 기간 동일 구성원 고정).
  - pit-only 92개는 상장폐지·편출 종목(예: 000030, 000070, 000080) — proxy가
    생존자 편향을 내포함을 확인.
- Day 14 strict WF (`outputs_k200mq_day14_strict_pit_universe`, PIT 유니버스 +
  FY2014 병합 DART):
  - 유니버스 provenance: 전 as-of `pit_valid=true`, `provenance=pit` (최초).
  - 5/5 폴드 valid, OOS 1,231점, preflight 실패 0건.
  - 첫 리밸런스 2015-05-29: momentum 121/200 usable (79 missing) — PIT
    구성원의 상장 시점 분산 반영. `quality_required=false`.
  - Stitched OOS **+20.55%**: 2020 +50.7% / 2021 -14.3% / 2022 -14.3% /
    2023 +1.6% / 2024 +7.2%. 전 폴드 REGIME_OFF 선택.
  - **proxy(+44.15%) 대비 -23.6%p** — 생존자 편향 제거가 성과를 하향
    조정. proxy 유니버스는 편출·상장폐지 종목의 부진을 반영하지 않아 성과를
    과대 추정했음.
- classification: 여전히 `mechanical_expanding_walk_forward_non_pit` —
  `walk_forward.py:579-582`가 `validated_expanding_walk_forward_pit`를
  "provenance validators wiring" 전까지 명시적으로 거부. 유니버스 PIT화(데이터
  게이트)는 달성됐으나 코드 레벨 승격은 별도 작업 필요.
- 남은 작업: ① classification 승격을 위한 provenance validator wiring 검토,
  ② PIT 유니버스 기준 PIT 민감도/스트레스 재실행(현재 파라미터 진단은 proxy
  기준), ③ DART 재무 커버리지 PIT 유니버스 기준 재점검.
