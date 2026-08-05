# 2주 실행 체크리스트 (Strict PIT Gate)

목적: 2주 안에 Continue/Hold/Pivot 판단에 필요한 최소 근거를 확보한다.

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
