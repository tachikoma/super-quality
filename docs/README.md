# 문서 체계 (인덱스)

이 문서는 저장소의 모든 문서를 한 곳에서 찾기 위한 지도입니다. 모든 연구는 **보관 상태**이며, 활성 연구나 거래 운용 문서를 새로 만들지 않습니다.

## 현재 상태 (단일 진실 원천)

- **저장소 안내:** [`../AGENTS.md`](../AGENTS.md) — 최소 필수 지식만 남긴 경량 안내서 (이력 제외)
- **현재 상태 종합:** [`planning/05_status.md`](planning/05_status.md) — MQ 보관·피벗 및 저변동성 종료·보관 최종 체크포인트 (2026-08-22 기준)
- **변경 이력:** [`history/changelog.md`](history/changelog.md) — 기존 `AGENTS.md` TASK LOG 전체 (2026-06-27 ~ 2026-08-22) 이전본

## 전략·가설

| 문서 | 설명 | 상태 |
|------|------|------|
| [`planning/01_strategy_pivot.md`](planning/01_strategy_pivot.md) | Super Quality 2.0 폐기 및 KOSPI 200 모멘텀+품질 전환 근거 (버릴 것/유지할 것) | 보관·참고용 |
| [`planning/10_low_volatility_preregistration.md`](planning/10_low_volatility_preregistration.md) | KOSPI 200 저변동성 사전등록 — 가설·고정 스펙·데이터 계약·평가 게이트·Phase 0/1/2A 기록 (2026-08-22 영구 보관 판정 포함) | **동결 스펙 — 수정 금지** |

## 설계·구현

| 문서 | 설명 | 상태 |
|------|------|------|
| [`planning/02_architecture.md`](planning/02_architecture.md) | K200 MQ 패키지 구조, 데이터 흐름, 팩터·엔진 설계 | 보관·참고용 |
| [`planning/03_implementation_plan.md`](planning/03_implementation_plan.md) | 5단계 구현 계획 (준비→데이터→팩터→전략/엔진→검증) | 보관·참고용 |
| [`planning/04_backtest_spec.md`](planning/04_backtest_spec.md) | 백테스트·검증 사양 (서브기간 견고성, 확장형 워크포워드, 비용 귀속, 벤치마크) | 보관·참고용 |
| [`planning/06_benchmark_and_cost_attribution.md`](planning/06_benchmark_and_cost_attribution.md) | 벤치마크 및 비용 귀속 정의 | 보관·참고용 |
| [`planning/09_coverage_gap_analysis.md`](planning/09_coverage_gap_analysis.md) | 커버리지 갭 분석 | 보관·참고용 |

## 검증·판정

| 문서 | 설명 | 상태 |
|------|------|------|
| [`planning/08_go_no_go_scorecard_template.md`](planning/08_go_no_go_scorecard_template.md) | Go/No-Go 판정표 템플릿 | 템플릿 |
| [`planning/08_go_no_go_scorecard_2026-08-17.md`](planning/08_go_no_go_scorecard_2026-08-17.md) | 최종 판정 (2026-08-17) — Hold, 실전 배치 불가 | 보관 |
| [`planning/08_go_no_go_scorecard_2026-08-16.md`](planning/08_go_no_go_scorecard_2026-08-16.md) | 판정 (2026-08-16) — Hold | 보관 |
| [`planning/08_go_no_go_scorecard_2026-08-10.md`](planning/08_go_no_go_scorecard_2026-08-10.md) | 판정 (2026-08-10) — 조건부 Continue | 보관 |
| [`planning/08_go_no_go_scorecard_2026-08-04.md`](planning/08_go_no_go_scorecard_2026-08-04.md) | 판정 (2026-08-04) — 초기 | 보관 |

## 실행·운영 기록 (보관)

| 문서 | 설명 |
|------|------|
| [`planning/05_status.md`](planning/05_status.md) | 2026-08-22까지 모든 체크포인트·PIT 게이트·Day 8~24 진단·종료 선언 포함 (가장 긴 문서, 단일 진실 원천) |
| [`planning/07_two_week_execution_checklist.md`](planning/07_two_week_execution_checklist.md) | 2주 실행 체크리스트 (보관) |
| [`history/changelog.md`](history/changelog.md) | `AGENTS.md`에서 분리된 상세 작업 일지 |

## 문서 작성 규칙

- **언어:** 한글을 기본으로 작성합니다. 코드·파일명·고유명사·수치는 원문 그대로 둡니다.
- **상태 표기:** 모든 전략 문서는 상단에 보관/폐기/동결 상태를 명시합니다.
- **수치 인용:** 과거 백테스트 수치는 `docs/planning/05_status.md`와 `history/changelog.md`에만 보관하고, 다른 문서에서 재인용 시 출처를 명시합니다.
- **신규 문서:** 새 가설은 별도 경제적 정당화와 사전 등록 없이는 만들지 않습니다. 이 인덱스를 먼저 갱신하세요.
