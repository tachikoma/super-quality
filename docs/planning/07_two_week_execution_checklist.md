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

## 품질 배선 수정 (2026-08-06, 추가)

- 확인: 로컬 DART facts가 품질 팩터로 흐르지 않던 배선 결함 2건을 oracle 검토로 확정하고 수정했다.
  - 공용 매핑: `src/k200_mq/data/account_mapping.py`의 `ACCOUNT_COLUMN_MAPPING`이 계정명/계정코드를 wide 6컬럼(revenue, cogs, net_income, operating_cf, total_assets, total_equity)으로 매핑(정규화 로더 API 경로와 공유).
  - pivot: `dart_pit.pivot_financial_facts_to_wide`가 long format facts를 wide로 피벗해 `_load_local_dart_financial_inputs`에 연결.
  - 게이트: `main.py` 품질 활성 조건을 `DART_API_KEY` 단독에서 `(DART_API_KEY or 로컬 원천 준비) and daily_financial 비어있지 않음`으로 수정.
- 검증: `tests/test_k200_mq_local_dart_quality.py` 통합 테스트로 ROE 8.3% / D/E 0.67 / OpMargin 60% / CashConv 0.8 확인, 관련 스위트 전체 통과(408 passed + 기존 무관 실패 1건), ruff clean.
- 함의: 연간(11011) 축소 스펙 판정이 품질 팩터 실제 소비 경로에서 검증됨. API 키 확보 후 축소 스펙(2,057건)으로 재개하면 로컬 DART 품질 입력이 strict 실행에 반영된다.
