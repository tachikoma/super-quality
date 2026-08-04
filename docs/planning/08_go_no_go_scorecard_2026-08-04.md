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
