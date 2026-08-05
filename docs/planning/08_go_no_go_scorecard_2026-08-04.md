# Go/No-Go 판정표 (2026-08-04 Day 1 실측)

기준 템플릿: `docs/planning/08_go_no_go_scorecard_template.md`

## 1) 데이터/검증 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 3 | 실패 | outputs_k200mq_day1_20260804/true_walkforward/summary.csv, outputs_k200mq_day1_20260804_mapped/true_walkforward/summary.csv, outputs_k200mq_day1_20260804_mixed_keydedup/true_walkforward/summary.csv |
| 유니버스 예외 정리 | 완료 | 부분 완료 | 보류 | data/universe/kospi200_bundle_strict/bundle.manifest.json |
| DART filing-date 커버리지 | 임계치 이상 | 조인 무결성 실패로 미측정 | 실패 | outputs_k200mq_day1_20260804/true_walkforward/selection_and_folds.json |

## 2) 성과 게이트 (비용 반영 OOS)

| 지표 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| OOS CAGR | >= 5% | N/A (run invalid) | 미판정 | outputs_k200mq_day1_20260804/true_walkforward/summary.csv |
| OOS MDD | >= -25% | N/A (run invalid) | 미판정 | outputs_k200mq_day1_20260804/true_walkforward/summary.csv |
| OOS Sharpe | >= 0.7 | N/A (run invalid) | 미판정 | outputs_k200mq_day1_20260804/true_walkforward/summary.csv |
| OOS Calmar | >= 0.3 | N/A (run invalid) | 미판정 | outputs_k200mq_day1_20260804/true_walkforward/summary.csv |

## 3) 안정성 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| 파라미터 소폭 변화 내구성 | 붕괴 없음 | 미실행 | 미판정 | - |
| 서브기간 편중 | 과도 편중 없음 | 미실행 | 미판정 | - |

## 4) 임시 판정 (Day 1)

- Continue 조건: 보류
- Hold 조건: 해당
- Pivot 조건: 아직 미해당

임시 판정: Hold

판정 사유 (3줄 요약):
1. strict true-walkforward는 3회 모두 invalid로 종료되었고, 초기 병목은 DART 조인 무결성으로 확인됐다.
2. key-dedup 후보 적용 후 조인 병목은 완화됐지만, 다음 병목이 KRX 세션 매핑 실패로 이동했다.
3. 성과/안정성 게이트는 유효 실행이 나오기 전까지 측정 불가다.

다음 액션 (Day 2 우선순위):
- DART facts 기준으로 (corp_code, rcept_no) 중복/누락 조인 키를 정리한다.
- 정리 후 aggregate 재생성, strict 재실행 1회, scorecard 재갱신.

진단 스냅샷 (2026-08-04):
- mixed aggregate
	- filings 중복 (corp_code, rcept_no): 9788 rows
	- facts missing filing join: 0 rows
	- facts 중복 (corp_code, rcept_no, account_id, account_detail): 4839 rows
- mapped aggregate
	- filings 중복 (corp_code, rcept_no): 0 rows
	- facts missing filing join: 93048 rows
	- facts 중복 (corp_code, rcept_no, account_id, account_detail): 28020 rows
- mixed key-dedup candidate
	- filings 중복 (corp_code, rcept_no): 0 rows
	- facts 중복 (corp_code, rcept_no, reprt_code, fs_div, sj_div, account_id, account_detail, period_end): 0 rows
	- strict 실패 원인: `one or more filings cannot be mapped to a provided KRX session`

## 5) Day 2 갱신 (2026-08-05)

### 재실행 결과

- strict true-walkforward 재실행 1회 수행.
- 출력 경로: `/tmp/k200mq_day2_20260805_strict_recheck`
- 결과: 완료

### 달라진 점

- DART 세션 매핑 실패 진단을 보강해 unmapped 건수/세션 범위/예시 receipt key를 바로 확인할 수 있게 함.
- local DART 입력의 future receipt 정리는 DART provenance를 보존하는 방식으로 처리했고, 재실행에서 세션 매핑 blocker는 재현되지 않음.
- strict true-walkforward가 산출물까지 생성되었고, 최종 분류는 `mechanical_expanding_walk_forward_non_pit`로 유지됨.

### 현재 1차 blocker

- strict preflight 실패 건수: 1/1
- strict preflight 실패 건수: 0/1
- 현재 분류: `mechanical_expanding_walk_forward_non_pit`
- 해석: strict 실행은 통과했지만, current execution은 아직 validator-backed `pit_filing_date` 성과 주장으로 승격되지 않음.
- transition exception: `data/universe/kospi200_bundle_strict/bundle.manifest.json`의 `transition_exceptions_by_as_of`에 120개 월말 일자가 문서화되어 있음(2015-01-30 ~ 2024-12-31, 각 날짜 198 구성원).

### 데이터/검증 게이트 상태 재평가

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 0 | 통과 | `/tmp/k200mq_day2_20260805_strict_recheck_v2` 실행 로그 |
| 유니버스 예외 정리 | 완료 | 문서화 완료 | 통과 | data/universe/kospi200_bundle_strict/bundle.manifest.json |
| DART filing-date 커버리지 | 임계치 이상 | 기계적 실행은 완료, 검증된 PIT는 미승격 | 보류 | `/tmp/k200mq_day2_20260805_strict_recheck_v2` 실행 로그 |

### Day 2 임시 판정

- Hold 유지

판정 사유 (업데이트):
1. Day 1의 DART 세션 매핑 blocker는 코드 수정과 재실행으로 해소 방향이 확인됐다.
2. 그러나 strict financial provenance는 아직 `pit_filing_date`로 승격되지 않아 preflight 실패 0건 목표를 달성하지 못했다.
3. 따라서 성과/안정성 게이트로 넘어가기 전에 financial provenance 원인 분석과 유니버스 예외 문서화를 먼저 닫아야 한다.

### 다음 액션

- local DART aggregate가 `non_pit_fiscal_period`로 판정되는 validator evidence를 추적한다.
- 198 constituent 날짜 예외 목록과 documented transition exception 초안을 문서화한다.

## 6) Day 3 갱신 (2026-08-05)

### DART coverage 정량 결과

- 측정 입력
	- `data/raw/dart_aggregated_pilot_mixed_keydedup/dart_filings_merged.csv`
	- `data/raw/dart_aggregated_pilot_mixed_keydedup/dart_facts_merged.csv`
	- `data/universe/kospi200_bundle_strict/bundle.manifest.json` (120개 월말 as-of)
- 측정 방식
	- `prepare_financial_facts(..., amendment_policy="first_filing")`로 strict 로컬 DART 준비 프레임 생성
	- 각 as-of에서 `availability_session <= as_of`인 고유 `stock_code` 개수를 198로 나눠 커버리지 비율 계산
- 결과
	- filings: 10,465행
	- facts: 8,996행
	- prepared facts: 7,381행
	- facts key missing ratio: 0.00%
	- filing date range: 2016-03-30 ~ 2024-03-28
	- prepared unique stock_code: 7개
	- as-of coverage ratio (min/median/max): 0.0000 / 0.0303 / 0.0354
	- as-of covered tickers (min/median/max): 0 / 6 / 7

### Gate 영향 재판정

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 0 | 통과 | `/tmp/k200mq_day2_20260805_strict_recheck_v2` 실행 로그 |
| 유니버스 예외 정리 | 완료 | 문서화 완료 | 통과 | data/universe/kospi200_bundle_strict/bundle.manifest.json |
| DART filing-date 커버리지 | 임계치 이상 | min 0.0000 / median 0.0303 / max 0.0354 | 실패 | Day 3 coverage 측정 결과 |

### Day 3 임계치 제안

- 연구 지속 최소선: median >= 0.80, min >= 0.60
- strict PIT WF 실행선: median >= 0.90, min >= 0.80
- 현재 측정치는 두 기준 모두 미달

### Day 3 임시 판정

- Hold 유지

판정 사유 (업데이트):
1. strict preflight 자체는 통과했지만, financial availability coverage 절대량이 매우 낮다.
2. key-level 결측률은 양호하나(0.00%), 종목/기간 커버리지가 Gate 2 병목으로 남아 있다.
3. Day 4는 fetch batch 재구성과 aggregate 재생성으로 coverage 확장이 최우선이다.

## 7) Day 4 착수 갱신 (2026-08-05)

### 백필 타깃 확정

- strict universe 고유 종목수: 198
- current prepared 커버 종목수: 7
- 미커버 종목수: 191
- corp_map 매핑 가능 종목수: 187
- corp_map 미매핑 종목수: 4 (`000155`, `005385`, `005387`, `005935`)
- 생성 아티팩트:
  - `data/raw/k200_day4_missing_tickers.txt` (191 lines)
  - `data/raw/k200_day4_missing_corp_codes.txt` (187 lines)
  - `data/raw/dart_batch_spec_day4_missing_both.json` (7,667 specs)

### Gate 영향 (중간)

| 항목 | 상태 | 코멘트 |
|---|---|---|
| Gate 1 strict preflight | 유지 | 기존 통과 상태 유지 |
| Gate 2 filing-date coverage | 개선 작업 착수 | 백필 대상 및 배치 스펙 생성 완료, fetch/merge/재실행 대기 |
| Gate 3 completeness | 개선 작업 착수 | 대량 백필 후 재측정 필요 |

### Day 4 실행 런북

1. Fetch: `scripts/fetch_local_dart_response.py` 배치 실행
2. Merge: `scripts/build_local_dart_aggregates.py`로 merged artifacts 재생성
3. Strict rerun: `k200_mq.main true-walkforward --strict-pit` 재실행
4. 재측정: Day 3 coverage 지표(min/median/max) 재계산 후 본 문서 Gate 재판정
