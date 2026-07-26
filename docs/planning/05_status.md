# 작업 진행 상황

## 마지막 업데이트: 2026-07-26

## 프로젝트 상태: Phase 2 완료, Phase 3 (통합/검증) 대기

---

## Phase 0: 준비
- [x] `.omo/` 폴더 삭제 (2026-07-26)
- [x] `outputs_2023_2024/` 삭제 (2026-07-26)
- [x] 레거시 태깅: `git tag v2.0-abandoned` 생성됨 ✓
- [x] `docs/planning/` 디렉토리 생성
- [x] `01_strategy_pivot.md` 작성
- [x] `02_architecture.md` 작성
- [x] `03_implementation_plan.md` 작성
- [x] `04_backtest_spec.md` 작성
- [ ] `05_status.md` ← 현재 파일 (이 문서)
- [ ] `src/k200_mq/` 패키지 구조 생성
- [ ] `src/k200_mq/core/` 공통 인프라 추출
- [ ] 레거시 `src/super_quality/` frozen (수정 불가)

---

## Phase 1: 데이터 & 유니버스
이 단계 진행 시 갱신

- [ ] KOSPI 200 종속 이력 소스 확인 (KRX API vs 프록시)
- [ ] `universe.py` 구현
- [ ] `loader.py` 확장 (252d pre-fetch, ADV)
- [ ] KPI200/KS11 index data 지원
- [ ] 종속 이력 검증

---

## Phase 2: 팩터 모듈
이 단계 진행 시 갱신

- [ ] `momentum.py` (12-7 month, 52-week high backup)
- [ ] `quality.py` (ROE, DE, OpMargin, CashConv)
- [ ] `regime.py` (MA200 + 20d return filter)
- [ ] Unit tests (모든 팩터)

---

## Phase 3: 전략 & 엔진
이 단계 진행 시 갱신

- [ ] `momentum_quality.py` (cross-sectional scoring)
- [ ] `portfolio_engine.py` (rebalance loop, stop-loss, cost model)
- [ ] Unit tests
- [ ] 기존 `engine.py` 수정 금지 확인

---

## Phase 4: 통합 & 검증
이 단계 진행 시 갱신

- [ ] `main.py` 새 CLI (`k200-mq run`)
- [ ] Walk-forward CV (5-fold expanding)
- [ ] 파라미터 민감도 분석
- [ ] 레짓 필터 교차 분석
- [ ] 서바이버십 바이어스 테스트
- [ ] 스트레스 테스트
- [ ] Turnover / cost attribution

---

## Phase 5: 마무리
이 단계 진행 시 갱신

- [ ] README.md (새 전략 문서)
- [ ] AGENTS.md 업데이트
- [ ] Test 보강 (118 → 200+)
- [ ] 최종 백테스트 (2015-2024)
- [ ] 코드 리뷰
- [ ] v0.1.0 release tag

---

## 의사결정 로그

| 날짜 | 결정 | 근거 |
|------|------|------|
| 2026-07-25 | Super Quality 2.0 폐기 | 10년 백테스트 모든 결과 음수 |
| 2026-07-25 | 모멘텀 전략 고려 | Oracle 초기 제안 |
| 2026-07-25 | 모멘텀 근거 검토 요청 | 사용자 |
| 2026-07-25 | `v2.0-abandoned` 태그 권장 | Oracle 검토 |
| 2026-07-25 | 새 패키지 `k200_mq/` 분리 | Oracle 검토 — 심볼릭 링크 비권장 |
| 2026-07-26 | 일정 15일 → 6주로 수정 | Oracle 비용 분석 |
| 2026-07-26 | `.omo/` 삭제 | 레거시 산출물 정리 |
| 2026-07-26 | `outputs_2023_2024/` 삭제 | 레거시 백테스트 산출물 정리 |
| 2026-07-26 | 계획 문서 5개 작성 | 작업 추적 및 검토용 |

---

## 리스크 로그

| 리스크 | 심각도 | 완화 방안 | 상태 |
|--------|--------|-----------|------|
| KOSPI 200 종속 이력 소스 부재 | **높음** | 프록시(mcap top 200)로 Fallback | 미해결 |
| 모멘텀 크래시 리스크 | **높음** | Long-only + 리짓 필터로 부분 완화 | 미해결 |
| 과적합 (walk-forward 미검증) | **높음** | Phase 4에서 반드시 수행 | 계획 중 |
| 엔진 리모델링 과대 산정 | 중간 | 기존 BacktestEngine 보존, 별도 PortfolioRebalanceEngine | 계획됨 |
| DART 계정 매핑 실패 | 중간 | 3-pass 기존 매칭 유지 + 새 팩터용 매핑 테이블 추가 | 계획됨 |
| 한국 모멘텀 효과 크기 작음 | 중 | 포터블 1.34%/월 목표, net cost 고려 시 1.0% 이상 필요 | 미해결 |
| 반도체 집중 리스크 | **높음** | 섹션 노출 캡 30% | 계획됨 |

---

## 다음 단계

**즉시 실행**: `git tag v2.0-abandoned` → Phase 0 시작 (새 패키지 생성)