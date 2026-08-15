# Go/No-Go 판정표 (2026-08-10 Day 8 strict PIT WF 실측)

기준 템플릿: `docs/planning/08_go_no_go_scorecard_template.md`
이전 판정: `docs/planning/08_go_no_go_scorecard_2026-08-04.md` (Hold 유지, Day 4까지)

## 실행 개요

- 명령:
  - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_day4_extended/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_day4_extended/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_day4_extended/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_day4_extended/dart_facts_merged.manifest.json --output outputs_k200mq_day8_strict_extended`
- 결과: 완료 (5/5 folds `valid=True`, OOS 1,231점, 2020-2024)
- 선행 성능 최적화: `dart_pit.py` 세션 매핑 searchsorted + 미래 접수 행 벡터화 (prepare 8분+ → ~82초) — 커밋 `238a4de`
- DART 데이터: `data/raw/dart_aggregated_day4_extended/` (extended aggregate, prepare 후 259,255행 매핑 / future receipt 44,438행 드롭)

## 1) 데이터/검증 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 0 | 통과 | `outputs_k200mq_day8_strict_extended/true_walkforward/summary.csv` (5/5 valid) |
| 유니버스 예외 정리 | 완료 | 완료 (120개 월말 198 구성원) | 통과 | `data/universe/kospi200_bundle_strict/bundle.manifest.json` `transition_exceptions_by_as_of` |
| DART filing-date 커버리지 | 임계치 이상 | financial `pit_filing_date` + `pit_valid=true`; 별도 readiness 진단은 first-ready `momentum_z` usable 147/198 (missing 51, `quality_required=false`) | 부분 통과 | `selection_and_folds.json` `prepared_inputs.coverage`; `src/k200_mq/main.py:1150-1176` |

재무 provenance 상세: `mode=pit_filing_date`, `availability_policy=next_session`, `filing_date_used=true`, `source_schema_contract=true`, `pit_valid=true`. 유니버스는 전 as-of 날짜 `provenance=pit`.

**준비성 해석 정정 (2026-08-13)**: 첫-ready 리밸런스(2015-05-29)의
`usable_ticker_count=147/198`, missing 51은 `momentum_z` 가격 warmup/readiness이며
financial/quality coverage가 아니다. `quality_required=false`이다. Quality의
`partial_allowed_fill_missing_with_zero` 모드(covered 169 ticker / factor 393,522행)는
별도 상태다. 이전에 이 51개를 DART financial gap으로 해석한 내용은 superseded/erratum이다.

FY2014 XBRL Phase 3 현황: 원본 접수 141건 선정, 검증된 XBRL ZIP 119건, strict six-fact
accepted 92건, 요청한 XBRL 문서를 이용할 수 없음을 나타내는 OpenDART 공식 상태
`014` 22건(단순한 로컬 파일 누락이 아님), parser fail-closed 27건. FY2014 XBRL은
financial PIT facts를 개선하지만 momentum warmup은 해결하지 않는다.

## 2) 성과 게이트 (비용 반영 OOS, 2020-2024 stitched)

| 지표 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| OOS CAGR | >= 5% | 9.79% | 통과 | `oos_returns.csv` stitched |
| OOS MDD | >= -25% | -23.40% | 통과 | `oos_returns.csv` stitched |
| OOS Sharpe | >= 0.7 | 0.737 | 통과 | `oos_returns.csv` stitched |
| OOS Calmar | >= 0.3 | 0.418 | 통과 | CAGR / |MDD| |

폴드별 (모두 valid):
- 2020 TOP_N_10: +21.7% / Sharpe 1.12 / MDD -11.8%
- 2021 REGIME_OFF: +0.5% / Sharpe -0.10 / MDD -17.7%
- 2022 BASE: -7.8% / Sharpe -1.35 / MDD -14.1%
- 2023 BASE: +22.2% / Sharpe 1.37 / MDD -13.5%
- 2024 BASE: +15.8% / Sharpe 0.80 / MDD -16.6%

Stitched OOS: 총수익률 +57.79% (5년), CAGR 9.79%, Sharpe 0.737, MDD -23.40%, OOS 1,231점.

## 3) 안정성 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| 파라미터 소폭 변화 내구성 | 붕괴 없음 | 미실행 | 미판정 | - |
| 서브기간 편중 | 과도 편중 없음 | 2022 단일 음수(-7.8%), 4/5 폴드 양수 | 부분 확인 | 폴드별 지표 |

## 4) 임시 판정

- Continue 조건: 필수 게이트 모두 통과 + 성과 컷오프 충족
- Hold 조건: 필수 게이트 통과, 성과 컷오프 일부 미달
- Pivot 조건: 필수 게이트 미통과 또는 안정성 반복 실패

임시 판정: Continue (조건부)

판정 사유 (3줄 요약):
1. Day 1~4의 strict blocker(세션 매핑 실패, 조인 무결성, financial provenance)가 모두 해소되어 strict preflight 실패 0건, financial provenance가 처음으로 `pit_filing_date` + `pit_valid=true`로 승격됐다.
2. 성과 컷오프 4종(CAGR/MDD/Sharpe/Calmar)이 5년 OOS에서 모두 통과했고, 2022 음수 외 4/5 폴드 양수로 서브기간 편중도 과도하지 않다.
3. 단, 분류는 여전히 `mechanical_expanding_walk_forward_non_pit`으로 유지된다 —
   first-ready momentum readiness 147/198은 financial/quality coverage 지표가 아니며,
   별도의 PIT 범위·quality partial-fill·안정성 게이트가 남아 있어
   `validated_expanding_walk_forward_pit` 성과 주장으로의 승격은 해당 검증 완결 후
   재실행이 필요하다.

관련 산출물:
- `outputs_k200mq_day8_strict_extended/true_walkforward/{summary.csv,oos_returns.csv,selection_and_folds.json}`
- `data/raw/dart_aggregated_day4_extended/dart_filings_merged.csv`, `dart_facts_merged.csv` (+ manifests)
- `data/universe/kospi200_bundle_strict/bundle.manifest.json`
