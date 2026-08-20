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

| 지표 | 기준 | Day 18 (0.5/0.5) | Day 20 (0.7/0.3) | Day 24 (v5, 11개) | 통과 여부 |
|---|---|---|---|---|---|
| OOS CAGR | >= 5% | 3.90% | **9.13%** | 3.16% | 미달 |
| OOS MDD | >= -25% | -37.7% | -31.2% | **-37.99%** | 미달 (악화) |
| OOS Sharpe | >= 0.7 | 0.299 | 0.555 | 0.274 | 미달 |
| OOS Calmar | >= 0.3 | 0.104 | 0.293 | 0.083 | 미달 |

핵심: **Day 24 (v5 후보 라이브러리)에서 모든 지표가 악화 — CAGR 3.16%, MDD
-37.99%, Sharpe 0.274, Calmar 0.083. REGIME_70/50/30 후보가 어떤 폴드에서도
선택되지 않았고, 기존 패턴(REGIME_OFF×3 + MOM60×2)이 반복됨. MDD 악화는
새로 다운로드된 가격 데이터(상장폐지 종목 포함)와 상장폐지 감지 활성화의 복합
영향.**

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

### 2026-08-19 실전 준비 진행 상황 (커밋 `f47bf9b`)

- ✅ ① WF 후보화: MOM60/MOM70/SL20/SL20_CASH10 추가 (커밋 `48f3fc8`)
- ② ADV 유동성 정책: **비활성 기본 유지**. ENABLE_ADV_FILTER=True 시
  fail-open 정책(커버리지 누락 티커는 경고+제외, None 맵은 fail-closed).
  PIT 유니버스에서 상장폐지 종목 mcap=0 → ADV 미측정 → 제외. 필터 자체가
  후보 풀 축소 → 분산 붕괴 → 성과 악화를 일관되게 유발 (Day 11 -1.37%,
  Day 19 -22.74%). **비활성 유지 권장**.
- ✅ ③ 상장폐지/거래정지 감지+강제청산: `_update_delisting_status()` 구현.
  가격 0 또는 거래량 0 연속 streak → 마지막 알려진 가격으로 강제 체결.
  ENABLE_DELISTING_DETECTION=True 기본.
- ④ 데이터 갱신 자동화: 스크립트 작성 중 (`scripts/refresh_data.sh`).
- ✅ ⑤ 리스크 가드레일: 일일/월간 손실 한도 + 드로다운 중단 + 쿨다운.
  opt-in(기본 비활성). `_check_risk_guardrails()` 구현.
- **보너스**: 리짓 축소 비율 WFA 후보 추가 (REGIME_70/50/30, v5 라이브러리
  8→11개). `REGIME_REDUCTION`을 `_SAFE_RUNTIME_FIELDS`에 추가해 WFA가
  축소 비율을 선택 가능하게 함. **Day 24 검증: REGIME_70/50/30 선택 0건 —
  모든 폴드에서 BASE/REGIME_30/50/70이 동일 train_sharpe 기록. 리짓 신호
  자체(이진)가 성과에 기여하지 않아 축소 비율 조정 무의미 확증.**
- ✅ ④ 데이터 갱신 자동화: `scripts/refresh_data.sh` 완료 (가격/유니버스/DART
  3단계, --price-only/--universe-only/--dart-only 플래그, 로깅).

## 5) 최종 판정

- Continue: 필수 게이트 모두 통과 + 성과 컷오프 충족
- Hold: 필수 게이트 통과, 성과 컷오프 일부 미달
- Pivot: 필수 게이트 미통과 또는 안정성 반복 실패

최종 판정: **Hold (성과 게이트 미달 — Day 24 v5에서 모든 지표 악화. REGIME_REDUCTION 축 실패 확증. 전략 차원 변경만 남음)**

판정 사유 (3줄 요약):
1. **Day 24 WFA v5 (`outputs_k200mq_day24_v5_candidates`)**: 11개 후보 경쟁 → REGIME_70/50/30 선택 0건, 모든 폴드 REGIME_OFF×3 + MOM60×2 (Day 22와 동일). stitched **+16.41%** (Day 22 +20.55% 대비 -4.1%p 하락), MDD **-37.99%** (Day 22 -20.8% 대비 -17.2%p 악화). 데이터 갱신 + 상장폐지 감지 활성화의 복합 영향.
2. **REGIME_REDUCTION 축 구조적 실패 확증**: BASE/REGIME_30/50/70이 모든 폴드에서 동일 train_sharpe — 이진 리짓 신호(close>MA200 AND 20d return>0)의 축소 비율 조정이 성과에 영향 없음. regime 강화의 유일한 경로는 신호 자체 재설계.
3. **실전 준비 5항목 모두 완료** (① WF 후보화, ② ADV 정책, ③ 상장폐지 감지, ④ 데이터 갱신 자동화, ⑤ 리스크 가드레일). 그러나 성과 게이트 미달로 실전 자본 배치 불가. **OOS/게이트 기준 재검토 또는 전략 차원 변경(regime 신호 재설계, quality 팩터 개선) 필요.**

관련 산출물:
- `outputs_k200mq_day22_candidate_v4/`, `outputs_k200mq_day23_pit_annual_merge/`
- `outputs_k200mq_day24_v5_candidates/` — v5 11개 후보 WFA (Day 24, REGIME_70/50/30 검증)
- `scripts/monte_carlo_bootstrap.py` + 부트스트랩 JSON
- `outputs_k200mq_grid_*` (파라미터 그리드 8개), `data/raw/dart_aggregated_day4_extended_fy2014_pit/`
- `scripts/refresh_data.sh` (데이터 갱신 자동화)
- Oracle 실전 적용 검토 (세션 ora-1, 2026-08-17)
