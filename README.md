# Super Quality 2.0

강환국 스타일 Super Quality 2.0 전략을 구현한 한국 주식 퀀트 백테스팅 시스템 (Python 벡터화 기반).

## 전략 개요

Super Quality 2.0은 여덟 가지 조건(A-H)으로 KOSPI 및 KOSDAQ 종목을 스크리닝합니다:

- **가치 (Value)**
  - **A**: PBR 하위 20% (저평가)
  - **B**: 순자산 양수 (PBR > 0)
- **품질 (Quality)**
  - **C**: 5개월 전 유상증자 없음
  - **D**: 현재 유상증자 없음
  - **E**: 최근 당기순이익 양수
  - **F**: 최근 영업현금흐름 양수
- **소형주**: **G** — 시가총액 하위 40%
- **시장 타이밍**: **H** — KOSDAQ 지수(KQ11)가 3/5/10일 이동평균 중 하나 이상보다 높음 (전체 시장 공통)

**우선순위 점수**: GP/A 백분위 + 개인 순매수 공급 백분위 (내림차순). 8개 조건(A-H)을 모두 통과한 종목만 대상.

**포지션 규칙**: 최대 20개 동시 보유, 포지션당 NAV의 10%, 매수 지정가 = 전일 종가 × 0.99, 최대 5거래일 보유.

**매도 조건** (매일 확인, 첫 매치 우선):
1. 손절: 진입가 대비 -7%
2. 시장 타이밍 이탈: KOSDAQ 지수 < 3MA AND < 5MA
3. 만기: 5거래일 도달

## 설치

### 사전 요구사항

- Python 3.12+
- [DART API 키](https://opendart.fss.or.kr) — OpenDartReader를 통한 재무 데이터 조회에 필요

### 설정

```bash
# 저장소 클론
git clone <repo-url>
cd super-quality

# uv로 의존성 설치
uv sync

# DART API 키 설정
echo 'DART_API_KEY=your_key_here' > .env
```

## 사용법

### 전체 백테스트 실행

```bash
# .env 파일에서 DART_API_KEY 읽기
uv run super-quality run

# 날짜 범위와 API 키 명시적 지정
uv run super-quality run --start 2015-01-01 --end 2024-12-31 --dart-api-key YOUR_KEY

# 사용자 정의 출력 디렉토리
uv run super-quality run --output my_results
```

또는 Python으로 직접 실행:

```bash
python -m super_quality.main run --dart-api-key YOUR_KEY
```

### 출력 파일

모든 출력 파일은 `--output` (기본값: `outputs/`) 디렉토리에 저장됩니다:

| 파일 | 설명 |
|------|------|
| `tearsheet.html` | 차트가 포함된 자체 HTML 성과 리포트 |
| `trade_log.csv` | 모든 거래 내역 (진입/종료일, 손익, 보유일, 종료 사유) |
| `portfolio_snapshots.csv` | 일별 포트폴리오 가치, 현금, 보유종목, NAV |
| `equity_curve.png` | 포트폴리오 NAV 추이 |
| `drawdown.png` | 고점 대비 낙폭 차트 |
| `monthly_returns.png` | 월별 수익률 히트맵 |

## 프로젝트 구조

```
src/super_quality/
├── __init__.py
├── main.py                 # CLI 진입점 (argparse)
├── config.py               # pydantic-settings 설정 (DART_API_KEY 등)
├── data/
│   ├── cache.py            # Parquet 기반 데이터 캐시 레이어
│   └── loader.py           # 데이터 수집 (FinanceDataReader, pykrx, OpenDartReader)
├── factors/
│   ├── base.py             # 추상 팩터 기본 클래스
│   ├── value.py            # PBR, 시가총액 백분위 팩터
│   ├── quality.py          # GP/A, 신F-Score 팩터
│   ├── market_timing.py    # KOSDAQ 지수(KQ11) 이동평균 기반 매수/매도 신호
│   └── supply.py           # 개인 투자자 순매수 공급 점수
├── strategies/
│   └── super_quality.py    # 전략: 조건 A-H 평가, 매도 로직
├── backtest/
│   └── engine.py           # 일별 시뮬레이션 루프 (2단계 실행)
├── analysis/
│   └── metrics.py          # 성과 지표 (CAGR, Sharpe, MDD 등)
└── reporting/
    └── report.py            # HTML 티어시트, PNG 차트, CSV 내보내기
```

## 데이터 소스

| 데이터 | 소스 | API 키 필요 | 병렬 처리 |
|--------|------|:---:|:---:|
| KOSPI / KOSDAQ 종목 리스트 | FinanceDataReader | 아니요 | - |
| 일별 OHLCV 가격 | FinanceDataReader | 아니요 | - |
| 시가총액 | FinanceDataReader | 아니요 | - |
| 재무제표 (K-IFRS) | OpenDartReader | 예 | 프리스크리닝 6 workers |
| 개인 투자자 순매수 | pykrx | 아니요 | 8 workers |
| 유상증자 일정 | OpenDartReader | 예 | 4 workers |
| KOSDAQ 지수 (시장 타이밍) | FinanceDataReader | 아니요 | - |

## DART API 키

DART(Data Analysis, Retrieval and Transfer) 시스템은 한국 상장 기업의 전자공시 데이터를 제공합니다.

1. [https://opendart.fss.or.kr](https://opendart.fss.or.kr) 접속
2. 회원가입
3. 마이페이지에서 인증키 신청
4. `.env` 파일에 키 저장:
   ```
   DART_API_KEY=your_40_character_api_key_here
   ```
   또는 환경 변수로 설정:
   ```bash
   export DART_API_KEY=your_40_character_api_key_here
   ```

`DART_API_KEY`가 설정되지 않은 경우 시스템은 경고를 출력하며 재무 데이터 조회가 런타임에 실패합니다.

## 테스트

```bash
# 전체 테스트 실행
pytest -v

# 커버리지 리포트 포함
pytest --cov=super_quality

# 특정 모듈 테스트
pytest tests/test_strategy.py -v
pytest tests/test_backtest.py -v
pytest tests/test_integration.py -v

# 린터 실행
ruff check
```

## 성능

데이터 수집 파이프라인은 병렬 처리를 통해 대폭 가속됩니다:

| 단계 | 순차 처리 | 병렬 처리 | 절감율 |
|------|-----------|-----------|--------|
| 개인 순매수 (2,605개 티커) | ~4.3시간 | ~26분 | 90% |
| DART 프리스크리닝 | ~39분 | ~5분 | 87% |
| 유상증자 조회 | ~1-2시간 | ~20-40분 | 67% |
| **전체 (첫 실행)** | **~5-6시간** | **~1시간** | **83%** |

캐시된 데이터를 사용하면 전체 파이프라인이 수분 이내에 완료됩니다.
