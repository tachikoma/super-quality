# KOSPI 200 Momentum + Quality

KOSPI 200 대형주 중심의 모멘텀+품질 백테스팅 시스템입니다.

Super Quality 2.0 (KOSDAQ 소형주 밸류 전략)이 구조적으로 실패한 후,
KOSPI 200 Momentum + Quality 프레임워크로 새 출발한 프로젝트입니다.

## 전략 개요

KOSPI 200 종목 중 리밸런싱 일자에 모멘텀 팩터와 품질 팩터를
크로스섹셔널로 결합하여 상위 N개 종목을 선정하고
동일 비중으로 포트폴리오를 구성합니다.

### 팩터 구성

| 팩터 | 설명 | 가중치 |
|------|------|--------|
| **Momentum (12-7개월)** | 최근 12개월 수익률 중 마지막 2개월 skip | 50% |
| **Quality — ROE** | 당기순이익 / 자본총계 (TTM) | 35% |
| **Quality — Debt/Equity** | 총부채 / 자본총계 (낮을수록 좋음) | 25% |
| **Quality — Operating Margin** | 영업이익 / 매출액 (TTM) | 20% |
| **Quality — Cash Conversion** | 영업현금흐름 / 당기순이익 (TTM) | 20% |

### 리짓 필터

KOSPI 200 지수 > MA200 AND 20일 수익률 > 0 인 경우에만
포지션 100% 유지, 그렇지 않으면 50% 축소.

### 포트폴리오 구성

- 리밸런싱: 월간 또는 분기
- 선택 종목: TOP 20 (기본)
- 배분: 동일 비중 또는 순위 가중
- KOSPI 상위 50개 제외 (메가캡 모멘텀 희석 방지)
- 섹션별 노출 캡: 30%
- 일일 손절: -15% (선택 사항)

## 설치

```bash
# 저장소 클론
git clone <repo-url>
cd super-quality

# uv로 의존성 설치
uv sync
```

## 사용법

### 기본 실행

```bash
uv run python -m k200_mq.main run
```

### 날짜 범위와 파라미터 지정

```bash
uv run python -m k200_mq.main run \
    --start 2015-01-01 --end 2024-12-31 \
    --top-n 20 \
    --rebalance-freq M \
    --weight-momentum 0.50 --weight-quality 0.50
```

### 주요 파라미터

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `--top-n` | 20 | 선택 종목 수 |
| `--rebalance-freq` | M | 리밸런싱 주기 (M=월, Q=분기) |
| `--weight-momentum` | 0.50 | 모멘텀 팩터 가중치 |
| `--weight-quality` | 0.50 | 품질 팩터 가중치 |
| `--exclude-kospi-top-n` | 50 | 모멘텀에서 제외할 KOSPI 상위 N개 |
| `--stop-loss` | -0.15 | 일일 손절 기준 |
| `--max-holdings` | 20 | 최대 동시 보유 |
| `--sector-cap` | 0.30 | 섹션별 최대 노출 |
| `--min-adv-ratio` | 0.01 | 최소 유동성 비율 |

## 프로젝트 구조

```
src/k200_mq/
├── __init__.py
├── main.py                  # CLI 진입점
├── config.py                # K200MQConfig
├── core/                    # 공통 인프라 (레거시에서 재사용)
│   ├── cache.py
│   ├── factors/base.py
│   ├── analysis/metrics.py
│   ├── reporting/report.py
│   └── data/loader.py
├── data/
│   └── universe.py          # KOSPI 200 유니버스 관리
├── factors/
│   ├── momentum.py          # 모멘텀 팩터 (12-7개월)
│   ├── quality.py           # 품질 팩터 (ROE, DE, OPM, CC)
│   └── regime.py            # 리짓 필터 (KOSPI 200 MA200)
├── strategies/
│   └── momentum_quality.py  # 크로스섹셔널 스코어링
├── backtest/
│   └── portfolio_engine.py  # 리밸런싱 엔진
├── analysis/
└── reporting/
```

## 학술 근거

- Kang, Kwon & Park (2014): 한국 대형주에서 외부인 흐름 → 모멘텀 효과
- Sim & Kim (2021): 한국에서 2개월 단기 반전 → 12-7개월 모멘텀 사용 권장
- Choi, Choi & Kang (2013): KOSPI 상위 50개 제외 시 모멘텀 성능 개선
- Park, Bae & Lee (2021): KOSPI 200에서 Quality + Momentum 조합 월 1.34%
- Kim (2018): 한국 장-only 모멘텀 +1.15%/월
- Bae & Lee (2021): 한국 모멘텀 0.50~1.06%/월 (장기 포트폴리오)

## 참고 사항

- 이 전략은 **베타 (Beta)** 상태입니다. 파이프라인은 완성되었지만 결과는 검증되지 않았습니다.
- 백테스트 결과는 아직 검증되지 않았습니다. P0 이슈(리짓 필터 미적용, 리밸런싱 일자 불일치, 품질 팩터 커버리지) 수정 후 재검증 필요.
- 기존 Super Quality 2.0 레거시 코드는 `src/super_quality/`에 frozen 상태로 보존됩니다.

## 초기 백테스트 결과 (2024-07-26, 2020-2024)

```
초기 자본: 100,000,000원 → 최종 자본: 307,063,900원
총 수익률: +207.06%
연간 수익률: +61.94%
연간 변동성: 80.93%
Sharpe 비율: 0.596
최대 낙폭: -60.71%
총 거래: 62건 | 승률: 77.4%
평균 수익률/건: +35.46% | 평균 보유일: 131.3일
평균 보유 종목: 9.2개
```

> **⚠️ 주의**: 이 결과는 P0 이슈(리짓 필터가 백테스트 후 post-processing으로만 적용, 리밸런싱 50% 누락, 품질 팩터 9.5% 커버리지)가 있는 상태에서 실행되었습니다. 신뢰할 수 있는 결과는 P0 수정 완료 후 재실행 필요.