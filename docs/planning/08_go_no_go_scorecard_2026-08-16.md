# Go/No-Go 판정표 (2026-08-16 Day 14 PIT 유니버스 strict WF 실측)

기준 템플릿: `docs/planning/08_go_no_go_scorecard_template.md`
이전 판정: `docs/planning/08_go_no_go_scorecard_2026-08-10.md` (Continue 조건부)

## 실행 개요

- 명령:
  - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_pit --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_pit/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_day4_extended_fy2014/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_day4_extended_fy2014/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_day4_extended_fy2014/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_day4_extended_fy2014/dart_facts_merged.manifest.json --output outputs_k200mq_day14_strict_pit_universe`
- 결과: 완료 (5/5 folds `valid=True`, OOS 1,231점, 2020-2024)
- 유니버스: `data/universe/kospi200_bundle_pit/` — pykrx `get_index_portfolio_deposit_file`
  (KRX 공식)로 120개 as-of 실제 구성원 fetch, 323 유니크 티커, 전 as-of `pit_valid=true`
- DART 데이터: `data/raw/dart_aggregated_day4_extended_fy2014/` (FY2014 XBRL 병합,
  facts 304,245행)

## 1) 데이터/검증 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 0 | 통과 | `outputs_k200mq_day14_strict_pit_universe/true_walkforward/summary.csv` (5/5 valid) |
| 유니버스 예외 정리 | 완료 | 완료 (120개 as-of, 스냅샷 200~202, 전 as-of `pit_valid=true`) | 통과 | `data/universe/kospi200_bundle_pit/bundle.manifest.json`; `selection_and_folds.json` `preparation_manifest_context.universe_provenance` |
| DART filing-date 커버리지 | 임계치 이상 | financial `pit_filing_date` + `pit_valid=true`; 첫 리밸런스(2015-05-29) six-fact 73/200 (36.5%) | 부분 통과 | `selection_and_folds.json` `prepared_inputs.coverage`; 재무 커버리지 36.5%는 상장폐지·후기편입 종목 포함 PIT 구성원 기준 |

재무 provenance 상세: `mode=pit_filing_date`, `availability_policy=next_session`,
`pit_valid=true`. 유니버스는 전 as-of `provenance=pit`.

## 2) 성과 게이트 (비용 반영 OOS, 2020-2024 stitched)

| 지표 | 기준 | 현재 값 (PIT) | 비교 (proxy Day 10) | 통과 여부 | 근거 |
|---|---|---|---|---|---|
| OOS CAGR | >= 5% | +3.81% | +7.59% | 미달 | `oos_returns.csv` stitched +20.55% |
| OOS MDD | >= -25% | -25.56% (fold5) | -20.38% (fold4) | 근접 미달 | `summary.csv` fold별 max drawdown |
| OOS Sharpe | >= 0.7 | -0.036 (fold 평균) | +0.719 | 미달 | `summary.csv` test_sharpe |
| OOS Calmar | >= 0.3 | ~0.15 | ~0.37 | 미달 | CAGR/MDD 비율 |

폴드별: 2020 +50.7% / 2021 -14.3% / 2022 -14.3% / 2023 +1.6% / 2024 +7.2%
(전 폴드 REGIME_OFF 선택). 생존자 편향 제거 후 성과가 하향 — proxy 성과는
편출·상장폐지 종목 부진 미반영으로 과대 추정이었음.

## 3) 안정성 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| 파라미터 소폭 변화 내구성 | 붕괴 없음 | 모멘텀 0.7/0.3: +53.23% (개선); 손절 비활성: +35.85%이나 2020 MDD -37.9% (MDD 붕괴) | 부분 | Day 16/17 PIT 진단 |
| 서브기간 편중 | 과도 편중 없음 | 2020 한 해가 stitched 성과의 대부분 (+50.7%) | 편중 있음 | `oos_returns.csv` |

## 4) 최종 판정

- Continue 조건: 필수 게이트 모두 통과 + 성과 컷오프 충족
- Hold 조건: 필수 게이트 통과, 성과 컷오프 일부 미달
- Pivot 조건: 필수 게이트 미통과 또는 안정성 반복 실패

최종 판정: **Hold (데이터 게이트 완성, 성과 게이트 미달 — classification 승격 전제)**

판정 사유 (3줄 요약):
1. 유니버스 PIT화·FY2014 재무 병합·strict preflight 0건으로 데이터/검증 게이트는
   완성됐으나 (Day 14 최초 전 as-of `pit_valid=true`), 성과 게이트는 OOS
   CAGR/Sharpe/Calmar 기준 미달이며 2020 서브기간 편중이 있다.
2. 생존자 편향 제거가 stitched +44.15%(proxy) → +20.55%(PIT)로 성과를 하향
   조정 — proxy 수치는 편출·상장폐지 종목 부진을 반영하지 않아 과대 추정이었음.
3. classification은 `mechanical_expanding_walk_forward_non_pit` 유지.
   승격은 전제조건 2건(filing_date_used 하드 증명, quality 커버리지 게이트/명시
   한계) 해소 후 adapter에서 validator 결과로 결정. 파라미터 진단(모멘텀 가중
   0.7/0.3 개선 신호)은 승격 후 재검증 대상.

관련 산출물:
- `outputs_k200mq_day14_strict_pit_universe/true_walkforward/{summary.csv,oos_returns.csv,selection_and_folds.json}`
- `data/universe/kospi200_bundle_pit/` (PIT 유니버스 번들)
- `outputs_k200mq_day15_pit_adv_filter/`, `outputs_k200mq_day16_pit_sensitivity_mom70/`, `outputs_k200mq_day17_pit_stress_nostop/` (PIT 파라미터 진단)
