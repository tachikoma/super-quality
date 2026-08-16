# Go/No-Go 판정표 (2026-08-17 Day 20 validated + Monte Carlo CI)

기준 템플릿: `docs/planning/08_go_no_go_scorecard_template.md`
이전 판정: `docs/planning/08_go_no_go_scorecard_2026-08-16.md` (Hold — Day 14)

## 실행 개요

- 검증 대상: `outputs_k200mq_day20_validated_mom70/` — 모멘텀 가중 0.7/0.3,
  PIT 유니버스, classification=`validated_expanding_walk_forward_pit`
- 참조: Day 18 (`outputs_k200mq_day18_validation_check_v2/`, 기본 0.5/0.5, validated)
- 몬테카를로: `scripts/monte_carlo_bootstrap.py` (stationary bootstrap, 블록 20일,
  5,000회, seed 42) — OOS 1,231일 일별 수익률 재표본

## 1) 데이터/검증 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| strict preflight 실패 건수 | 0 | 0 | 통과 | Day 18/20 모두 5/5 valid |
| 유니버스 PIT | 완료 | 120개 as-of, 전 as-of `pit_valid=true` | 통과 | `kospi200_bundle_pit/` |
| 재무 PIT + 하드 증명 | 완료 | `pit_filing_date` + `filing_date_mapped_row_count>0` | 통과 | Day 18 승격 (커밋 `5b45211`) |
| classification | validated | `validated_expanding_walk_forward_pit` | 통과 | Day 18/20 |

## 2) 성과 게이트 (비용 반영 OOS 2020-2024 stitched)

| 지표 | 기준 | Day 18 (0.5/0.5) | Day 20 (0.7/0.3) | 부트스트랩 95% CI (Day 20) | 게이트 통과 확률 (Day 20) | 통과 여부 |
|---|---|---|---|---|---|---|
| OOS CAGR | >= 5% | 3.90% | **9.13%** | [-10.1%, +32.4%] | 65.4% | 통과 (point) |
| OOS MDD | >= -25% | -37.7% | -31.2% | [-58.3%, -19.3%] | **17.4%** | 미달 |
| OOS Sharpe | >= 0.7 | 0.299 | 0.555 | [-0.47, 1.58] | 40.5% | 미달 |
| OOS Calmar | >= 0.3 | 0.104 | 0.293 | [-0.19, 1.46] | 48.6% | 근접 미달 |

핵심: **CAGR 게이트는 point 통과, 그러나 MDD 게이트는 부트스트랩에서 17.4%만
통과 — MDD -31%는 표본 우연이 아닌 구조적 한계.** Sharpe/Calmar도 중앙값이
기준 이하. Day 20이 Day 18 대비 전 지표 개선이나, 성과 게이트 4종 중 1종만
point 통과.

## 3) 안정성 게이트

| 항목 | 기준 | 현재 값 | 통과 여부 | 근거 |
|---|---|---|---|---|
| 파라미터 소폭 변화 내구성 | 붕괴 없음 | 모멘텀 0.6/0.4, 0.8/0.2, rebalance Q, 손절 ±0.10/0.20, 비중/현금 그리드 실행 중 (`outputs_k200mq_grid_*`) | 평가 중 | Day 21+ 그리드 결과 대기 |
| 서브기간 편중 | 과도 편중 없음 | 2020 한 해가 stitched 성과의 대부분 (+54.7%) | 편중 있음 | `oos_returns.csv` |
| 샘플 불확실성 | 신뢰구간 폭 | MDD CI 폭 ~39%p, Sharpe CI 폭 ~2.05 | 통과 (기록용) | 부트스트랩 |

## 4) 실전 적용 검토 (Oracle, 2026-08-17)

- **결론: 자동 주문 실전 No, paper trading 조건부 Yes.** 주문 실행 레이어 없음,
  유동성/상폐 처리/데이터 갱신/파라미터 검증 4개 갭 존재.
- 실전 전 필수 5개: ① 모멘텀 0.7/0.3을 WF 후보로 재검증(스누핑 해소 —
  **완료, 커밋 `48f3fc8` MOM60/MOM70 후보 추가**), ② ADV 유동성 정책 명시,
  ③ 상장폐지/거래정지 감지+강제청산, ④ 데이터 갱신 자동화, ⑤ 리스크 가드레일
  (일일/월간 손실한도, 드로다운 중단).
- EXCLUDE_KOSPI_TOP_N=0 고정 필수 (validated 숫자 재현 전제). quality 커버리지
  36.5%는 quality_z 63.5%가 neutral 채움 → "quality 가중 0.3"의 실질 효과 제한.

## 5) 최종 판정

- Continue: 필수 게이트 모두 통과 + 성과 컷오프 충족
- Hold: 필수 게이트 통과, 성과 컷오프 일부 미달
- Pivot: 필수 게이트 미통과 또는 안정성 반복 실패

최종 판정: **Hold (성과 게이트 미달 — MDD 구조적 한계 + 서브기간 편중)**

판정 사유 (3줄 요약):
1. 모멘텀 0.7/0.3 (Day 20, validated)이 기본 대비 전 지표 개선 (CAGR 9.13%,
   Sharpe 0.555, MDD -31.2%, Calmar 0.293) — CAGR 게이트는 point 통과.
2. 그러나 부트스트랩에서 MDD≥-25% 통과 확률 17.4%, Sharpe≥0.7 40.5% — 성과
   게이트 미달은 구조적. 2020 단일 연도 편중도 지속.
3. classification 승격·WFA 후보 확장(MOM60/70)으로 검증 인프라는 완성됨.
   실전 적용은 자동 주문 No / paper trading 조건부 Yes — 실전 전 필수 5항목
   (①은 완료, ②-⑤ 진행 필요).

관련 산출물:
- `outputs_k200mq_day20_validated_mom70/`, `outputs_k200mq_day18_validation_check_v2/`
- `scripts/monte_carlo_bootstrap.py` + 부트스트랩 JSON
- `outputs_k200mq_grid_*` (파라미터 그리드, 실행 중)
- Oracle 실전 적용 검토 (세션 ora-1, 2026-08-17)
