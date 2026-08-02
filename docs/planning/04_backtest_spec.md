# 백테스트 검증 규격 — KOSPI 200 Momentum + Quality

## 검증 철학

단일 10년 백테스트는 과적합 위험이 높습니다. 현재 CLI는 고정된 기간을
나누어 실행하는 **independent subperiod robustness test**를 제공합니다.
이는 학습/파라미터 피팅이 없는 기간별 견고성 점검이며, true walk-forward
cross-validation이 아닙니다. 파라미터 최적화와 expanding-window WF는
향후 작업으로 남아 있습니다.

---

## 현재 Subperiod Robustness 설계

현재 `robustness` 명령은 다음 독립 기간을 실행합니다.

| Subperiod | 기간 |
|-----------|------|
| 1 | 2014–2016 |
| 2 | 2017–2018 |
| 3 | 2019–2020 |
| 4 | 2021–2022 |
| 5 | 2023–현재 |

각 기간에는 training window가 없으며 전략 파라미터는 고정됩니다.

## True Walk-Forward 설계 (Phase 1 core 구현, 실행 연결 전)

고정 fold 일정과 train-only 후보 선택/직렬화 core가
`src/k200_mq/validation/walk_forward.py`에 구현되었습니다. 이 단계는
순수한 validation primitive만 제공하며 기존 `robustness`, 전략 실행 및 live
data pipeline은 변경하지 않습니다. 따라서 현재 분류는
`mechanical_expanding_walk_forward_non_pit`이며, PIT 데이터 계약을 충족한
실행만 `validated_expanding_walk_forward_pit`로 분류할 수 있습니다.

실제 fold 실행과 결과 재실행은 후속 통합 작업입니다.

### 기본 구조 (Expanding Window)

```
Fold 1: Train [2015-01 ~ 2019-12] | Test [2020-01 ~ 2020-12]
Fold 2: Train [2015-01 ~ 2020-12] | Test [2021-01 ~ 2021-12]
Fold 3: Train [2015-01 ~ 2021-12] | Test [2022-01 ~ 2022-12]
Fold 4: Train [2015-01 ~ 2022-12] | Test [2023-01 ~ 2023-12]
Fold 5: Train [2015-01 ~ 2023-12] | Test [2024-01 ~ 2024-12]
```

### Purge & Embargo
- **현재 상태: deferred/not applicable** — 이 pure core의 후보는 과거 데이터만
  사용하는 backward-only fixed-signal 후보이며 forward label이나 overlapping
  outcome을 사용한 피팅이 없습니다.
- 따라서 위의 정확한 인접 fold schedule을 유지하고, 현재 구현에는 purge/embargo가
  없습니다. 이 core를 purge/embargo가 구현된 것으로 해석하거나 주장하지 않습니다.
- 향후 forward label 또는 overlapping outcome을 사용하는 후보 피팅을 연결하면
  해당 누수 구조에 맞춰 fold schedule과 purge/embargo를 먼저 재설계해야 합니다.

### True WF 교차검증 지표 (구현 시 per fold)

| 지표 | 목표 | 비고 |
|------|------|------|
| CAGR | > +5%/년 | 최저 기대 |
| Sharpe Ratio | > 0.3 | 한국 시장에서 양수면 우수 |
| Max Drawdown | > -25% | 한계 |
| Win Rate | > 50% | 모멘텀 특성상 50% 이상 기대 어려울 수 있음 |
| Profit Factor | > 1.0 | 순이익 / 총손실 |
| Total Trades | > 5 | 통계적으로 의미있는 수 |

### Fold 간 안정성 진단
- 5개 fold 성과 지표의 평균 ± 표준편차 산출
- 표준편차 > 평균의 50% → 과적합 경고
- train/test 성과 차이 > 20% → 과적합 경고

---

## 트랜잭션 비용 모델

### 명시적 비용 (2026 기준)

| 항목 | KOSPI | KOSDAQ |
|------|-------|--------|
| 증권거래세 (매도) | 0.20% | 0.20% |
| 증권사 수수료 (매수/매도) | 0.015% | 0.015% |
| 슬리피지 | 0.10% | 0.10% |
| **1회 거래 (매수+매도)** | **0.43%** | **0.43%** |

### 시장 영향 비용 (Implicit)
- ADV 기반 동적 슬리피지 모델:
  ```
  impact = k × (size / ADV) ^ alpha
  ```
  - k = 0.1 (calibration constant)
  - alpha = 0.5 (Korea market specific)
  - size = 포지션 금액 (KRW)
  - ADV = 해당 종목 일평균 거래대금
- 대형주 (KOSPI 200): impact ≈ 0.01-0.05%
- 중소형주: impact ≈ 0.05-0.30%

### 총 비용 추정 (월 리밸런싱, 20종목)
- 월 거래: ~3-6종목 매도(리밸런싱) + ~3-6종목 매수 = 6-12건
- 연간 거래: 72-144건
- 연간 비용: 72 × 0.43% ~ 144 × 0.43% = **0.31% ~ 0.62% (avg 0.46%)**
- 시장 영향 추가: +0.05~0.10%

### 비용 제약
- 순수 alpha(수수료 전)에서 연 0.5~2.0% 기대 시
- 비용 차감 후 순 alpha: 연 0.04% ~ 1.54%
- 목표: 순 alpha > 0%, 이상적으로 > 1.0%

---

## 레짓 필터 교차 분석

### 시나리오별 분류
| 시나리오 | 기간 | 특징 |
|----------|------|------|
| 추세 상승 | 2020-01 ~ 2021-02 | 코로나 이후 급등 |
| 하락 | 2022-01 ~ 2022-10 | 고금리 + 경기침체 |
| 횡보 | 2017-01 ~ 2019-12 | 박스권 |
| 조정 | 2023-04 ~ 2023-10 | 반도체 사이클 하락 |
| 회복 | 2024-01 ~ 2024-12 | 반도체 회복 |

### 분석 방법
- 각 시나리오에서 전략 성과 개별 산출
- 레짓 필터 ON/OFF 각 시나리오별 비교
- 레짓 필터 효과 = Sharpe 차이 + MDD 개선도

### 기대 결과
- 레짓 필터는 추세 시장에서 성능 개선
- 횡보/하락 시장에서는 성능 악화 가능성 (과적합된 MA200)

---

## 서바이버십 바이어스 테스트

### 방법론
1. **A 방법**: 현재 KOSPI 200 종속(2026년 기준)으로 과거 백테스트
   → forward-looking bias 존재
2. **B 방법**: 과거 KOSPI 200 종속(point-in-time)으로 백테스트
   → 올바른 방법
3. **C 방법**: A 방법 결과와 B 방법 결과를 비교하여 편향 정량화

### 구현
- KRX 과거 종속 데이터 확보 (Option A: PDF/Excel parse, Option B: 프록시)
- 각 fold에서 point-in-time universe를 사용
- A vs B 차이 보고

---

## 팩터 디케이 분석

| 분석 항목 | 방법 |
|-----------|------|
| 모멘텀 디케이 (rebalance 후) | 리밸런링 후 1m/3m/6m 누적 수익률 변화 |
| 품질 디케이 | 리밸런링 후 품질 팩터 순위 변화율 |
| 팩터 중요도 | Shapley value 또는 simple ablation (모멘텀 only vs quality only vs combined) |

---

## 벤치마크

| 벤치마크 | 설명 |
|----------|------|
| KOSPI 200 TR (Total Return) | KOSPI 200 지수 수익률 (배당 재투자 포함) |
| KOSPI 200 Price Return | KOSPI 200 지수 가격 수익률 (배당 미포함) |
| Equal-Weight KOSPI 200 | 200종목 균등 투자 |
| Buy & Hold KOSPI 200 TR | 2015년 초 KOSPI 200 TR 지수 매수 & 보유 |

### 보고 형식
현재 robustness CLI가 출력하는 지표는 CAGR, Sharpe, MaxDD, WinRate,
Tradecount입니다. Profit Factor와 CAGR vs Benchmark는 현재 runner에서
계산하거나 출력하지 않으며, 향후 거래 지표/벤치마크 구현 후 추가합니다.
True walk-forward가 구현되면 train/test 결과를 별도 표로 추가합니다.

| Subperiod | Period | CAGR | Sharpe | MaxDD | WinRate | Tradecount |
|------|--------|------|--------|-------|---------|------------|
| 1 | 2014–2016 | ... | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... | ... |
| **Geometric mean / Worst MDD** | | | | | | |

---

## 스트레스 테스트

| 시나리오 | 기간 | 리스크 |
|----------|------|--------|
| 2020 COVID Crash | 2020-02 ~ 2020-04 | 모멘텀 크래시 확인 |
| 2022 Bear Market | 2022-01 ~ 2022-10 | 하락장 모멘�스 손실 |
| 2024 Semiconductor Rally | 2024-01 ~ 2024-12 | 섹터 집중 리스크 |
| 2017-2019 Sideways | 2017-01 ~ 2019-12 | 횡보장 과다 거래 비용 |
| 2016 Political Crisis | 2016-10 ~ 2017-01 | 이벤트 드리버 리스크 |

스트레스 기간의 최대 낙폭과 회복 기간을 보고합니다.
