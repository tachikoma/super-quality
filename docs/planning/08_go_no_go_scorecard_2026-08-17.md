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
| 파라미터 소폭 변화 내구성 | 붕괴 없음 | 그리드 8개 완료: sl20(SL-20%) **+58.48%** 최고 / mom06 +37.14% / cash10 +33.18% (MDD **-22.55%** 유일 게이트 통과) / mom08 +32.33% / sl10 +13.89% / mom04 +0.40% (붕괴). 모멘텀 0.7 최적, 손절 -20% 상향 개선 | 부분 (mom04 붕괴, 0.4 가중 미지원) | `outputs_k200mq_grid_*` |
| 파라미터 그리드 유효성 | 변형 유효 | **rebalQ 무효** — PIT 번들 import가 `requested_rebalance_dates=None`으로 개별 concat → REBALANCE_FREQ=Q 무시 (rebalQ==pos15 byte 동일=월간 기본). **pos15 무효** — 등액 5%<10% 캡 | 무효 2건 제외 | `pit_universe.py:_import_local_pit_snapshot_bundle` |
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

최종 판정: **Hold (성과 게이트 미달 — MDD 구조적 한계 + 서브기간 편중. Day 22/23이 파라미터·데이터 경로 모두 기각)**

판정 사유 (3줄 요약):
1. **Day 22 후보 경쟁 정직 검증 (커밋 `649de40`)**: v4 후보 8개(SL20/SL20_CASH10/MOM60/MOM70 포함)를 train 성과로 경쟁 → **모든 폴드에서 REGIME_OFF 선택** (fold1 -0.446 vs SL20 -0.761). 그리드의 sl20 +58.48% / cash10 +33.18% / Day 20 +53.23%(0.7/0.3)는 **사후 OOS 선택(스누핑)** 이었고 기각됨 — 파라미터 경로 불가 확증.
2. **Day 23 DART 재무 보강 (커밋 `7dabdc1`)**: 97개 corp 연간 facts 802건 보강으로 리밸런스 커버리지 70%+ (2020-01: 98/200→148/200). 그러나 stitched **+16.85%** (Day 22 +20.55% 대비 하락) — **데이터/커버리지 경로도 기각.** 성과 게이트 미달은 파라미터·데이터 모두로 해결 불가한 구조적 상태.
3. 검증 인프라 완성 (classification 승격 + WFA v4 + 부트스트랩 + 그리드 + Day 22/23). **성과 게이트 통과의 유일한 구조적 경로는 전략 차원 변경** (regime 강화, quality 팩터 재설계, 팩터 window 조정) 또는 OOS/게이트 기준 재검토. 실전 적용은 자동 주문 No / paper trading 조건부 Yes.

관련 산출물:
- `outputs_k200mq_day22_candidate_v4/`, `outputs_k200mq_day23_pit_annual_merge/`
- `scripts/monte_carlo_bootstrap.py` + 부트스트랩 JSON
- `outputs_k200mq_grid_*` (파라미터 그리드 8개), `data/raw/dart_aggregated_day4_extended_fy2014_pit/`
- Oracle 실전 적용 검토 (세션 ora-1, 2026-08-17)
