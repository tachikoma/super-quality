# 전략 폐기 및 KOSPI 200 모멘텀+품질 전환

## 요약

Super Quality 2.0 전략(KOSDAQ 소형주 밸류+품질)을 폐기하고, KOSPI 200 Momentum + Quality 프레임워크로 전환합니다.

## 폐기 결정

| 항목 | 내용 |
|------|------|
| **전략** | Super Quality 2.0 (KOSDAQ 소형주 밸류/품질) |
| **최고 백테스트** | -63.84% (2015-2024, MAX_HOLD_DAYS=20, STOP_LOSS=-20%) |
| **현재 빌드** | -71.55% |
| **결정 근거** | 10년 백테스트에서 어떤 파라미터 조합으로도 양수 수익 달성 불가 |
| **폐기 일자** | 2026-07-25 이후 |

## 실패의 근본 원인

### 1. KOSDAQ 소형주 밸류 트랩
- 싼 주식 = 나쁜 기업. PBR < 20% 필터가 너무 느슨해서 저평가된 저품질 종목을 고름
- 소형주일수록 정보를 비효율적으로 반영 → 밸류 트랩에 빠지기 쉬움

### 2. 품질 필터 무력
- GPA(GP/A) 단일 지표로 기업 건전성을 판단 불가
- 6/8 이완 입장 모드에서 핵심 4개 + 보조 2개만으로도 진입 → 품질 부족 종목 다수 포함
- ROE, 부채비율, 현금전환율 같은 다차원 품질 지표 부재

### 3. 시장 타이밍 순환적
- KOSDAQ 지수 MA20 매수 신호 → 자기 자신에 매수하는 순환 구조
- KOSDAQ은 변동성이 크고 노이즈가 심 → 추세 추종이 구조적 열세

## 전환 근거: 왜 모멘텀 + KOSPI 200인가

### 모멘텀 전략의 학술적 근거
- **12-1 모멘텀 존재**: Jegadeesh & Titman (1993), 40개국에서 확인
- **한국에서도 존재**: Kim (2018) — 장-only 모멘텀 +1.15%/월, Bae & Lee (2021) — 0.50~1.06%/월
- **한국 특유의 2개월 반전**: Sim & Kim (2021) — 미국 1개월 vs 한국 2개월 반전 사이클
- **Quality + Momentum 조합 검증**: Park, Bae & Lee (2021) — KOSPI 200에서 품질+모멘텀 조합 월 1.34%

### KOSPI 200으로의 전환 이유
- **유동성**: KOSDAQ 소형주 vs KOSPI 200 대형주 — 거래비용, 슬리피지 대폭 감소
- **정보 효율**: KOSPI 200은 기관 분석이 잘 되어 있어 가격 반영 효율이 높음 → 모멘텀이 의미 있는 신호
- **외국인 흐름**: Kang, Kwon & Park (2014) — 외국인이 KOSPI 200 대형주에서 모멘텀 효과 생성
- **단기 매도 불가**: 2020년 이후 숏레그 제약 → 장-only 전략에 유리

### KOSPI 200에서의 설계 포인트
- **상위 50개 제외**: Choi, Choi & Kang (2013) — 메가캡이 모멘텀 성능을 저하
- **skipped-return (current v4)**: `close[t-skip_days] / close[t-long_window] - 1`
  (기본 `close[t-42] / close[t-252] - 1`; 한국 2개월 반전 회피)
- **포인트인트임 유니버스**: 종속 이력 오염 방지

## 폐기 대상 (코드)

| 파일/모듈 | 이유 |
|-----------|------|
| `strategies/super_quality.py` | A-H 조건 로직 전부 |
| `factors/value.py` | PBR/MarketCap 팩터 — KOSPI 200에서 불필요 |
| `factors/quality.py` | GPA 팩터 — 단일 지표로 불충분, 교체 |
| `factors/market_timing.py` | KOSDAQ MA 신호 — KOSPI 200 리짓 필터로 교체 |
| `factors/supply.py` | 개인순매수 팩터 — KOSDAQ 소형주 중심, 효과 미검증 |
| `config.py` (전략 파라미터) | PBR_PERCENTILE, MCAP_PERCENTILE, RELAXED_ENTRY_MODE 등 |

## 보존 대상 (코드)

| 파일/모듈 | 이유 |
|-----------|------|
| `data/cache.py` | 범용 Parquet 캐시 계층 |
| `data/loader.py` | 가격 데이터, 재무 데이터, 유동성 데이터 로딩 |
| `factors/base.py` | 추상 팩터 인터페이스 |
| `backtest/engine.py` | 백테스트 엔진 (리밸런싱 엔진으로 확장 필요) |
| `analysis/metrics.py` | 성과 지표 계산 |
| `reporting/report.py` | 보고서 생성 |

## 레거시 마이그레이션

1. `git tag v2.0-abandoned` — 기존 코드 frozen
2. `src/super_quality/` → 레거시 상태로 유지 (수정 불가)
3. 새 전략은 `src/k200_mq/` 패키지로 분리
4. 공통 인프라는 `src/k200_mq/core/` 로 추출
