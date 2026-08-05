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
- 결과: invalid

### 달라진 점

- DART 세션 매핑 실패 진단을 보강해 unmapped 건수/세션 범위/예시 receipt key를 바로 확인할 수 있게 함.
- local DART 입력에서 prepared session 상한 이후 filing/fact 행을 사전 제외하도록 수정.
- 재실행 로그에서 `세션 상한 이후 filings=1461행, facts=1615행 제외`를 확인.
- Day 1의 mixed key-dedup candidate에서 보이던 `one or more filings cannot be mapped to a provided KRX session` blocker는 더 이상 현재 1차 실패 원인이 아님.

### 현재 1차 blocker

- strict preflight 실패 건수: 1/1
- 실패 원인: `financial quality mode is 'non_pit_fiscal_period'`
- 해석: aggregate의 조인/세션 범위 문제 일부는 정리됐지만, 현재 로컬 DART aggregate가 validator-backed `pit_filing_date` financial provenance로는 아직 승격되지 않음.

### 데이터/검증 게이트 상태 재평가

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 1 | 실패 | `/tmp/k200mq_day2_20260805_strict_recheck` 실행 로그 |
| 유니버스 예외 정리 | 완료 | 부분 완료 | 보류 | data/universe/kospi200_bundle_strict/bundle.manifest.json |
| DART filing-date 커버리지 | 임계치 이상 | 미측정 | 실패 | strict preflight가 `non_pit_fiscal_period`에서 중단 |

### Day 2 임시 판정

- Hold 유지

판정 사유 (업데이트):
1. Day 1의 DART 세션 매핑 blocker는 코드 수정과 재실행으로 해소 방향이 확인됐다.
2. 그러나 strict financial provenance는 아직 `pit_filing_date`로 승격되지 않아 preflight 실패 0건 목표를 달성하지 못했다.
3. 따라서 성과/안정성 게이트로 넘어가기 전에 financial provenance 원인 분석과 유니버스 예외 문서화를 먼저 닫아야 한다.

### 다음 액션

- local DART aggregate가 `non_pit_fiscal_period`로 판정되는 validator evidence를 추적한다.
- 198 constituent 날짜 예외 목록과 documented transition exception 초안을 문서화한다.
