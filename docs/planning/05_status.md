# 작업 진행 상황

## 마지막 업데이트: 2026-07-26

## 프로젝트 상태: Phase 4 완료 (베타), 검증 및 리팩토링 진행 중

## 최근 업데이트: 2026-07-26 (Phase 4 파이프라인 완성)

## 최초 백테스트 결과 (2020-2024)

```
초기 자본: 100,000,000원 → 최종 자본: 307,063,900원
총 수익률: +207.06%
연간 수익률: +61.94%
Sharpe 비율: 0.596
최대 낙폭: -60.71%
총 거래: 62건 | 승률: 77.4%
평균 보유일: 131.3일 | 평균 보유 종목: 9.2개
```

> **주의**: 결과는 검증되지 않은 초기 실행 결과입니다. P0 이슈(리짓 필터 미적용, 리밸런싱 누락, 품질 팩터 9.5% 커버리지)가 보고된 상태에서 실행되었습니다. 신뢰할 수 있는 결과를 얻으려면 P0 수정 후 재실행 필요.

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
- [x] `05_status.md` ← 현재 파일 (이 문서)
- [x] `src/k200_mq/` 패키지 구조 생성
- [x] `src/k200_mq/core/` 공통 인프라 추출
- [x] 레거시 `src/super_quality/` frozen (수정 불가)

---

## Phase 1: 데이터 & 유니버스
- [x] KOSPI 200 종속 이력 소스 확인 (프록시: mcap top 200 from KRX listings)
- [x] `universe.py` 구현 (get_kospi200_constituents, point-in-time caching)
- [x] `loader.py` 확장 (get_price_data_with_lookback, compute_adv, get_kospi200_price_data)
- [x] KOSPI 200 (KS11) + KOSPI 51 (KOSPI200) index data 지원
- [x] 종속 이력 검증 (프록시 fallback 구현)

---

## Phase 2: 팩터 모듈
- [x] `momentum.py` (12-7개월, 52주 고점 백업)
- [x] `quality.py` (ROE, DE, OpMargin, CashConv, 복합 z-score)
- [x] `regime.py` (MA200 + 20d return filter)
- [x] Unit tests (모든 팩터)

---

## Phase 3: 전략 & 엔진
- [x] `momentum_quality.py` (cross-sectional scoring, TOP-N, KOSPI50 제외, 섹션 캡)
- [x] `portfolio_engine.py` (리밸런싱 루프, stop-loss, 매수/매도 실행)
- [x] `main.py` CLI skeleton (`k200-mq run`)
- [x] Unit tests (모든 팩터, 전략, 엔진)
- [x] 기존 `engine.py` 수정 금지 확인

---

## Phase 4: 통합 & 검증
파이프라인 완성 — 실제 데이터 로드 → 팩터 계산 → 백테스트 → 결과 저장

- [x] `_run_pipeline()` 구현 — universe → price → factors → strategy → engine
- [x] `main.py` CLI (`k200-mq run`) — 전체 파이프라인 연결 완료
- [x] 재무 데이터 → 일별 변환 (`_convert_financial_to_daily`)
- [x] 결과 저장 (portfolio_snapshots, trade_log, daily_returns CSV)
- [x] 요약 통계 출력 (총수익률, Sharpe, 최대낙폭, 승률)
- [x] 최초 백테스트 실행 (2020-2024, +207.06% 수익률)
- [ ] P0 수정 (리짓 필터 엔진 내 적용, 리밸런싱 일자 통합, 품질 팩터 커버리지)
- [ ] Walk-forward CV (5-fold expanding)
- [ ] 파라미터 민감도 분석
- [ ] 레짓 필터 교차 분석
- [ ] 서바이버십 바이어스 테스트
- [ ] 스트레스 테스트
- [ ] Turnover / cost attribution

---

## Phase 5: 마무리
- [x] README_K200MQ.md (새 전략 문서)
- [x] AGENTS.md 업데이트
- [ ] README.md (새 전략 docs 섹션) — 진행 중
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
| 2026-07-26 | Phase 4 파이프라인 완성 | 실제 데이터 → 팩터 → 엔진 → 결과 |
| 2026-07-26 | 최초 백테스트 실행 (2020-2024) | +207.06% (검증 전, P0 이슈 있음) |
| 2026-07-26 | Oracle 리뷰 (14개 이슈) | P0-3개, P1-3개, P2-4개, P3-4개 식별 |

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
| **P0-1: 리짓 필터가 백테스트 후에만 적용됨** | **치명적** | 엔진 내 리밸런싱 시 적용 | 미해결 |
| **P0-2: 리밸런싱 일자 생성 불일치** | **치명적** | universe ↔ engine 간 일자 생성 통합 | 미해결 |
| **P0-3: 품질 팩터 커버리지 9.5%** | **치명적** | 모든 티커 일별 변환, z-score 완화 | 미해결 |
| P1-4: 리밸런싱이 add/remove만 함 (weight drift) | 높음 | weight adjustment 구현 | 미해결 |
| P1-5: 손절이 진입가 기준 (trailing 아님) | 높음 | peak_price 추적 | 미해결 |
| P1-6: 리짓 조건이 너무 엄격 (29.6% bullish) | 높음 | MA200 alone 또는 3-state | 미해결 |

---

## 다음 단계

**P0 우선순위 (결과 신뢰성 확보):**
1. 리짓 필터를 엔진 내 리밸런싱에 적용 (post-processing → during backtest)
2. 리밸런싱 일자 생성 통합 (universe ↔ engine)
3. 품질 팩터 커버리지 개선 (all tickers, relaxed z-score)

**Quick Wins (< 1시간):**
- 리밸런싱 일자 공통 유틸리티 분리
- Trailing stop-loss (peak_price 추적)
- 일관된 실행 가격 (next-day open)
- MIN_CASH_RATIO 적용
- 리밸런싱 로깅 강화 (quality coverage, regime, turnover)