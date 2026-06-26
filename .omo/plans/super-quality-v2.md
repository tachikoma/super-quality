# super-quality-v2 - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->

**What you'll get:** A Python backtesting system for 강환국 스타일 슈퍼 퀄리티 2.0 전략. 매일 종목을 스크리닝하고, PBR 하위 20% 저평가 소형주 중 신F-SCORE(주식수 변화無+순이익 흑자+영업현금 흑자)를 통과한 종목을 GP/A와 개인투자자 순매수로 순위 매겨 매수합니다. KOSDAQ 시장 타이밍(3/5/10일선)과 -7% 손절을 적용합니다. 최종 산출물: HTML 성과보고서, 체결내역 CSV, equity curve 차트.

**Why this approach:** 벡터화 백테스팅(pandas)은 팩터 기반 전략에 가장 빠르고 정확합니다. FinanceDataReader+pykrx+OpenDartReader는 한국 시장 데이터의 표준 조합이며, uv는 현대 파이썬 프로젝트 관리의 사실상 표준입니다.

**What it will NOT do:** 실시간 매매, 증권사 API 연동, 웹 대시보드, KOSPI 시장 타이밍, 업종 필터, PER/PSR 등 다른 밸류 팩터.

**Effort:** Large (6 components, ~15 files, greenfield)
**Risk:** Medium - 데이터 파이프라인(특히 수급점수)과 DART API 의존성이 주요 리스크
**Decisions to sanity-check:** 수급점수=개인투자자 순매수, E/F=당기순이익+영업현금흐름, 피벗매도=익일시가

Your next move: approve to start work, or request a high-accuracy Momus review first.

---

> TL;DR (machine): Large effort, Medium risk. Build vectorized Python backtesting system for Super Quality 2.0 strategy. 6 components, ~15 files, greenfield on empty repo.

## Scope
### Must have
1. 프로젝트 초기화 (uv + pyproject.toml + src/super_quality/ 패키지)
2. 데이터 파이프라인: 가격(FinanceDataReader), 재무제표(OpenDartReader), 수급(pykrx), KOSDAQ 지수
3. 팩터 계산 모듈: GP/A, PBR 백분위, 시총 백분위, 신F-SCORE(주식수 변화/순이익/영업현금), 수급점수, KOSDAQ MA
4. 전략 로직: 매수 A~J 평가, 우선순위 산정, 매도 조건, 포트폴리오 관리
5. 백테스팅 엔진: 일별 시뮬레이션, 체결, 보유기간, 손익 추적
6. 성과 분석 + 리포팅: CAGR/MDD/Sharpe, HTML tearsheet, CSV trade log, 차트
7. README에 DART API 키 설정 방법과 실행 방법 문서화

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 증권사 API / 실매매 코드 — 백테스팅 전용
- 웹 UI / 대시보드
- KOSPI 지수 시장 타이밍
- 업종/섹터 필터
- PER, PSR, PCR 등 PBR 외 밸류 팩터
- 머신러닝/최적화
- 실시간 데이터 수집

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with pytest
- Evidence: .omo/evidence/task-<N>-super-quality-v2.<ext>
- Lint: ruff (configured in pyproject.toml)
- Smoke test: `pytest -v` must pass all tests
- Acceptance criteria: each todo has agent-executable `python -c` or `pytest` commands

## Execution strategy
### Parallel execution waves
- Wave 1: Project scaffold (task 1)
- Wave 2: Config module (task 2) + Data pipeline (task 3) — sequential (2→3)
- Wave 3: Factor calculation (task 4) — blocked by task 3
- Wave 4: Strategy logic + Backtesting engine (task 5) — blocked by task 4
- Wave 5: Performance analytics + Reporting (task 6) — blocked by task 5
- Wave 6: Integration test + Docs (task 7) — final

### Dependency matrix (matches actual task numbering)
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. Project scaffold | — | 2,3,4,5,6,7 | — |
| 2. Config module | 1 | 3,4,5,6 | — |
| 3. Data pipeline | 1,2 | 4,5,6 | — |
| 4. Factor calculation | 1,2,3 | 5 | — |
| 5. Strategy + backtest | 1,2,3,4 | 6 | — |
| 6. Perf analytics + report | 5 | 7 | — |
| 7. Integration + docs | 6 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. 프로젝트 스캐폴드 및 설정
  What to do / Must NOT do: 
  - `uv init`로 pyproject.toml 생성, src/super_quality/ 패키지 구조 생성
  - 의존성 추가: pandas, numpy, pydantic, pydantic-settings, pyyaml, finance-datareader, pykrx, opendartreader, quantstats, matplotlib, plotly, pyarrow
  - dev 의존성 추가: pytest, pytest-cov, ruff
  - src/super_quality/ 디렉토리 구조: __init__.py, config.py, data/, factors/, strategies/, backtest/, analysis/, reporting/
  - pytest 설정 (pyproject.toml에 [tool.pytest.ini_options])
  - ruff 설정 (pyproject.toml에 [tool.ruff])
  - .gitignore 생성 (Python 표준 + .omo/evidence/ + data/raw/ + data/processed/ + outputs/)
  - Must NOT do: Jupyter 노트북 생성, README에 불필요한 이모지/광고
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2,3,4,5,6,7
  References:
  - /Users/durkjaeyun/projects/investment/super-quality/ (empty repo)
  - Strategy: 위 전략 문서의 슈퍼 퀄리티 2.0 전략 조건 A-J
  - Draft: .omo/drafts/super-quality-v2.md
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  ls pyproject.toml  # must exist
  ls src/super_quality/__init__.py  # must exist
  ls src/super_quality/config.py  # must exist
  ls src/super_quality/data/  # must exist
  ls src/super_quality/factors/  # must exist
  ls src/super_quality/strategies/  # must exist
  ls src/super_quality/backtest/  # must exist
  ls src/super_quality/analysis/  # must exist
  ls src/super_quality/reporting/  # must exist
  python -c "import super_quality; print('OK')"  # must print OK
  pytest --co  # must collect 0 tests initially (no error)
  ```
  QA scenarios:
  - Happy: `uv run python -c "import super_quality; print('import OK')"` → stdout "import OK"
  - Failure: `uv run python -c "import nonexistent_module"` → ModuleNotFoundError (expected)
  Evidence: .omo/evidence/task-1-super-quality-v2.txt
  Commit: Y | chore(init): scaffold project with uv, pytest, ruff, package structure

- [x] 2. 설정 모듈 (config.py)
  What to do / Must NOT do:
  - pydantic-settings를 사용한 설정 클래스 SuperQualityConfig 구현
  - 설정 필드: DART_API_KEY (str, Secret), START_DATE (date: 2015-01-01), END_DATE (date: today), INITIAL_CAPITAL (int: 100000000), MAX_HOLDINGS (int: 20), POSITION_SIZE (float: 0.10), BUY_PRICE_OFFSET (float: 0.99 = -1%), MAX_HOLD_DAYS (int: 5), STOP_LOSS (float: -0.07), COMMISSION_RATE (float: 0.00015), TAX_RATE (float: 0.0018), SLIPPAGE (float: 0.001), SUPPLY_SCORE_DAYS (int: 5), PBR_PERCENTILE (float: 0.20), MCAP_PERCENTILE (float: 0.40), KOSDAQ_TICKER (str: "KQ11")
  - .env 파일에서 DART_API_KEY 로드
  - config.yaml 파일 지원 (옵션)
  - Must NOT do: API 키를 코드에 하드코딩, 민감정보를 git에 커밋
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3,4,5,6
  References:
  - pydantic-settings docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  python -c "
  from super_quality.config import SuperQualityConfig
  cfg = SuperQualityConfig(DART_API_KEY='test')
  assert cfg.MAX_HOLDINGS == 20
  assert cfg.POSITION_SIZE == 0.10
  assert cfg.BUY_PRICE_OFFSET == 0.99
  assert cfg.STOP_LOSS == -0.07
  print('Config OK')
  "
  ```
  QA scenarios:
  - Happy: 설정 기본값 검증 → 모든 필드가 예상된 기본값을 가짐
  - Failure: DART_API_KEY 없이 생성 시도 → ValidationError (API 키 필수)
  - Edge: .env 파일에서 DART_API_KEY 로드 확인
  Evidence: .omo/evidence/task-2-super-quality-v2.txt
  Commit: Y | feat(config): add pydantic-settings config with all strategy parameters

- [x] 3. 데이터 파이프라인 모듈
  What to do / Must NOT do:
  - src/super_quality/data/loader.py:
    - `get_krx_listings()` → KRX 전체 종목 리스트 (티커, 종목명, 시장, 섹터)
    - `get_price_data(tickers, start, end)` → 일별 OHLCV + 시가총액 (FinanceDataReader)
    - `get_kosdaq_index(start, end)` → KOSDAQ 일별 종가 (KQ11)
    - `get_financial_data(tickers, years)` → 분기 재무데이터 (OpenDartReader: 매출액, 매출원가, 당기순이익, 영업현금흐름, 자산총계, 자본총계, 발행주식수)
    - `get_retail_net_buy(ticker, start, end)` → 일별 개인투자자 순매수 금액 (pykrx: stock.get_market_trading_value_by_date)
    - `get_shares_outstanding(ticker, dates)` → 일별/분기별 발행주식수
  - src/super_quality/data/cache.py:
    - Parquet 기반 로컬 캐시 (data/raw/ 경로)
    - 중복 다운로드 방지, 데이터 갱신 로직
  - TTM 계산 유틸리티:
    - `calculate_ttm(financial_data)` → K-IFRS 누적값 → 단일분기 변환 → 최근 4분기 합산
    - 분기말 기준 lag 적용: Q1→5/16, 반기→8/16, Q3→11/16, 연간→4/1
  - 데이터 볼륨 처리 전략 (수급점수): 2000+ 종목 × 10년 = 수백만 회 API 호출 방지
    - pykrx의 `get_market_trading_value_by_date()`를 모든 종목 개별 호출하지 말고, bulk 조회 가능한 API 우선 활용
    - 실제 전략 백테스트 대상(시총 하위 40% + PBR 하위 20% 필터 통과 종목)만 수급 데이터 조회 (전체 종목 대비 ~10% 수준)
    - Parquet 캐시에 저장 후 재사용: 최초 1회만 API 호출
    - Fallback: 수급점수 데이터를 구할 수 없는 종목은 score = 0 (중립) 처리
  - Must NOT do: 
    - API 키 없이 OpenDartReader 호출
    - pykrx 과도한 호출로 IP 차단 위험 (크롤링 간격 0.5초 이상 권장)
    - FinanceDataReader로 개인투자자 순매수 조회 시도 (pykrx 사용)
    - 재무데이터 lag 무시 (look-ahead bias)
  Parallelization: Wave 1 | Blocked by: 1,2 | Blocks: 4,5,6
  References:
  - FinanceDataReader: https://github.com/FinanceData/FinanceDataReader
  - OpenDartReader: https://github.com/FinanceData/OpenDartReader
  - pykrx: https://github.com/sharebook-kr/pykrx
  - pykrx investor function: stock.get_market_trading_value_by_date(fromdate, todate, ticker) returns columns ['티커', '매도거래량', '매수거래량', '순매수거래량'] for each investor type
  - K-IFRS filing schedule: Q1(5/15), 반기(8/15), Q3(11/15), 연간(3/31)
  - Look-ahead bias prevention: use data available as of each rebalance date only
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  # Smoke test: can import data module
  python -c "from super_quality.data.loader import get_krx_listings; print('import OK')"
  
  # KOSDAQ index fetch (requires internet)
  python -c "
  from super_quality.data.loader import get_kosdaq_index
  import datetime
  df = get_kosdaq_index('2024-01-01', '2024-01-31')
  print(f'Got {len(df)} rows of KOSDAQ data')
  assert len(df) > 0
  "
  
  # TTM calculation test with known data
  python -c "
  import pandas as pd
  from super_quality.data.loader import calculate_ttm
  # Simulate K-IFRS cumulative data: Q1=100, 반기=250, Q3=400, 연간=600
  # TTM after Q1 = Q1(100) + (연간(600) - Q1_prev(90)) = ... 
  # Test with simple case first
  print('TTM function exists')
  "
  ```
  QA scenarios:
  - Happy: KOSDAQ 지수 fetch → 20+ 영업일 데이터 반환
  - Happy: TTM 계산 → 누적값에서 올바른 단일분기 추출 확인
  - Failure: 잘못된 티커 → 빈 DataFrame (오류 아닌 정상 동작)
  - Edge: 인터넷 없는 환경 → 캐시된 데이터만 사용 가능
  Evidence: .omo/evidence/task-3-super-quality-v2.txt
  Commit: Y | feat(data): add data pipeline with FinanceDataReader, pykrx, OpenDartReader, Parquet cache, TTM calc

- [x] 4. 팩터 계산 모듈
  What to do / Must NOT do:
  - src/super_quality/factors/__init__.py (팩터 레지스트리)
  - src/super_quality/factors/base.py (Factor ABC: compute() 메서드)
  - src/super_quality/factors/value.py:
    - `PBRFactor`: trailing PBR = 시가총액 / 자본총계(trailing) → 백분위(오름차순, PBR 낮을수록 rank 낮음)
    - `MarketCapFactor`: 시가총액 백분위(오름차순, 시총 낮을수록 rank 낮음)
  - src/super_quality/factors/quality.py:
    - `GPAFactor`: GP/A = (매출액 - 매출원가) / 자산총계 → 백분위(오름차순: GP/A 높을수록 rank 높음)
    - `NewFScoreFactor`: 신F-SCORE 4조건 (C: shareΔ=0 5mo ago, D: shareΔ=0 now, E: NI>0 trailing, F: OCF>0 trailing) → boolean pass/fail
    - 각 조건 독립 계산 가능
  - src/super_quality/factors/market_timing.py:
    - `KosdaqMAFactor`: KOSDAQ 3/5/10일 MA 대비 현재가 비교 → buy_signal(OR) / sell_signal(AND)
  - src/super_quality/factors/supply.py:
    - `RetailSupplyFactor`: 개인투자자 최근 N일 누적 순매수 금액 → 백분위
  - GP/A 백분위 설명 (코드 주석): "비율(GP/A, 오름차순) gives high GP/A a HIGH percentile score (near 100). Combined with supply score and sorted descending, high-GP/A + high-supply-score stocks get top priority."
  - Must NOT do: 
    - 전체 Piotroski F-Score 구현 (신F-SCORE만: C,D,E,F)
    - GP/A 오름차순 해석 실수 (의도: 높은 GP/A가 높은 점수)
    - 팩터 값 변경 없이 동일한 결과 반환 (pandas vectorized)
  Parallelization: Wave 2 | Blocked by: 1,2,3 | Blocks: 5
  References:
  - Strategy conditions from draft .omo/drafts/super-quality-v2.md
  - Novy-Marx (2013): GP/A profitability premium
  - Piotroski F-Score simplified to 3 criteria
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  python -c "
  import pandas as pd
  from super_quality.factors.quality import GPAFactor
  # Simple test data
  data = pd.DataFrame({
      'revenue': [1000, 500, 2000],
      'cogs': [600, 400, 1500],
      'total_assets': [5000, 2000, 10000],
      'ticker': ['A', 'B', 'C']
  })
  factor = GPAFactor()
  result = factor.compute(data)
  assert 'gpa' in result.columns
  assert 'gpa_percentile' in result.columns
  # B has lowest GP/A (100/2000=0.05), C has highest (500/10000=0.05... wait)
  # A: 400/5000=0.08, B: 100/2000=0.05, C: 500/10000=0.05
  # Actually check GP/A orders correctly
  print('GPAFactor OK')
  "
  
  python -c "
  from super_quality.factors.quality import NewFScoreFactor
  import pandas as pd
  data = pd.DataFrame({
      'ticker': ['A', 'B'],
      'share_change_5mo_ago': [0, 100],  # C: A passes, B fails
      'share_change_now': [0, 0],        # D: both pass
      'trailing_ni': [100, -50],         # E: A passes, B fails
      'trailing_ocf': [200, 300],        # F: both pass
  })
  factor = NewFScoreFactor()
  result = factor.compute(data)
  assert result.loc[0, 'new_fscore_pass'] == True  # A: all pass
  assert result.loc[1, 'new_fscore_pass'] == False # B: C and E fail
  print('NewFScoreFactor OK')
  "
  ```
  QA scenarios:
  - Happy: GPAFactor correct percentile (ascending = high GP/A = high rank)
  - Happy: NewFScoreFactor correctly evaluates all 4 conditions (C,D,E,F)
  - Failure: Zero total_assets → GP/A = 0 (handle division by zero)
  - Edge: All stocks have same GP/A → all get percentile 50
  Evidence: .omo/evidence/task-4-super-quality-v2.txt
  Commit: Y | feat(factors): implement GP/A percentile, 신F-SCORE, retail supply score, KOSDAQ MA timing factors

- [x] 5. 전략 로직 + 백테스팅 엔진
  What to do / Must NOT do:
  - src/super_quality/strategies/super_quality.py:
    - `SuperQualityStrategy` 클래스: 하루 단위 시그널 생성
    - 매수 조건 A-J 평가 (A: PBR<=20%, B: PBR>0, C: shareΔ=0 5mo, D: shareΔ=0 now, E: NI>0, F: OCF>0, G: mcap<=40%, H/I/J: KOSDAQ MA)
    - 우선순위 = GP/A 백분위 + 수급점수 백분위 → 내림차순
    - 매도 조건: (KOSDAQ<3MA AND KOSDAQ<5MA) OR 수익률<=-7%
    - 포트폴리오: 최대 20종목, 보유일수 추적
  - **자산배분 규칙 (명확)**: 
    - 매수 시점에 qualifying stocks ≤ 10개 → 각 10% 균등 배분 (100% 투자)
    - qualifying stocks > 10개 → 우선순위 상위 10개에 각 10% 배분 (100% 투자), 나머지 매수 보류
    - qualifying stocks = 0개 → 현금 보유 (매수 없음)
    - POSITION_SIZE(0.10)는 **최대 포지션 비중**이며, qualifying stocks가 적을 경우 그보다 적게 투자할 수 있음 (예: 3 stocks × 10% = 30% 투자, 70% 현금)
    - 기 보유 포지션이 있는 경우: NAV 대비 신규 포지션 비중 계산, 100% 초과 시 신규 매수 불가
  - src/super_quality/backtest/engine.py:
    - `BacktestEngine` 클래스:
    - 일별 루프: 매도 체크(조건/만기) → 매수 체크(시장타이밍/조건) → 주문 실행
    - 매수 체결: 전일 종가 × 0.99 (지정가)
    - 매도 체결: 
    - 만기 매도 (5일 경과) = **전일종가** (T-1 close, T일 장 시작 전 체결)
    - 조건부 매도 (KOSDAQ 이탈 또는 -7% 손절) = **조건 충족 다음 영업일 시가** (T+1 open)
    - 조건부 매도 판단 기준: T-1일 종가 기준으로 매도 조건 평가 → 충족 시 T일 장 종료 후 매도 결정 → T+1일 시가에 체결
  - 일별 루프 순서:
    a) 매도 체크 (조건부: T-1 데이터 기준, 만기: 보유일수 기준)
    b) 조건부 매도 대상 → T+1 시가에 체결되도록 예약
    c) 만기 매도 대상 → 전일종가(T-1 close)에 체결
    d) 현금 재계산 후 매수 체크 진행
    e) 매수 조건 충족 시 전일종가(T-1 close) × 0.99 지정가 주문 — T 당일 저가가 지정가 이하면 체결 가정
    - 거래비용: 수수료(0.015%) + 세금(0.18%) + 슬리피지(0.1%)
    - 포트폴리오 가치 추적, 현금 관리
    - 일별 포트폴리오 스냅샷 저장
  - 조건부 매도 vs 만기 매도 우선순위: 조건부 먼저 체크
  - 종목 부족 시: 가용 종목에 비례 배분 (예: 8종목 = 각 10% = 80% 투자, 20% 현금)
  - KOSDAQ MA 조건 ASYMMETRY: 매수는 OR(셋 중 하나만 위), 매도는 AND(3일 AND 5일 모두 아래)
  - Must NOT do: 
    - 미래 정보 사용 (반드시 해당 날짜 기준 데이터만)
    - 모의체결 없이 전량 체결 가정
    - 보유기간 초과 유지
  Parallelization: Wave 3 | Blocked by: 1,2,3,4 | Blocks: 6
  References:
  - Strategy conditions: draft's 정리된 조건 A-J with Exact QuantKing syntax
  - 매수 가격: 전일종가 -1%
  - 매도 가격: 만기=전일종가, 조건부=익일시가
  - 보유: 최대 5영업일
  - 시장 타이밍: 매수 OR / 매도 AND asymmetry
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  python -c "
  from super_quality.strategies.super_quality import SuperQualityStrategy
  from super_quality.config import SuperQualityConfig
  cfg = SuperQualityConfig(DART_API_KEY='test')
  strategy = SuperQualityStrategy(cfg)
  print('Strategy class OK')
  "
  
  # Unit test: buy conditions A-J
  pytest tests/test_strategy.py::test_buy_conditions -v
  
  # Unit test: priority ranking
  pytest tests/test_strategy.py::test_priority_ranking -v
  
  # Unit test: sell conditions
  pytest tests/test_strategy.py::test_sell_conditions -v
  ```
  QA scenarios:
  - Happy: Buy signal generated when all A-J conditions met
  - Happy: Sell signal on KOSDAQ < 3MA AND < 5MA
  - Happy: Stop-loss triggered at -7%
  - Failure: No stocks pass screening → no buy signals (empty portfolio OK)
  - Edge: Conflict: KOSDAQ > 10MA (buy OK) AND < 3MA&5MA (sell) → sell takes priority for existing positions
  - Edge: Stock held 5 days without hitting sell conditions → force-sell at previous close
  - Edge: Both 만기 and 조건부 on same day → 조건부(take profit/stop loss) priority
  - Integration: Run 1-month backtest with sample data → trade log has expected structure
  Evidence: .omo/evidence/task-5-super-quality-v2.txt
  Commit: Y | feat(strategy): implement Super Quality 2.0 strategy logic + vectorized backtesting engine

- [x] 6. 성과 분석 및 리포트 생성
  What to do / Must NOT do:
  - src/super_quality/analysis/metrics.py:
    - CAGR, 변동성, Sharpe Ratio (무위험 3.5%), Sortino Ratio, MDD, Win Rate, Profit Factor
    - 월별/연도별 수익률
    - 벤치마크 비교 (KOSDAQ 지수 buy&hold)
    - 회전율(Turnover), 평균 보유일수
  - src/super_quality/reporting/report.py:
    - HTML teasheet: quantstats 스타일 — equity curve, drawdown, monthly returns heatmap, rolling Sharpe, 종목별 breakdown
    - trade_log.csv: 체결내역 (entry_date, exit_date, ticker, buy_price, sell_price, return, hold_days, reason)
    - portfolio_snapshot.csv: 일별 포트폴리오 구성
    - equity_curve.png, drawdown.png 차트 (matplotlib)
  - src/super_quality/main.py:
    - typer CLI로 실행: `uv run super-quality --dart-api-key KEY [--start 2015-01-01] [--end 2025-12-31]`
    - 전체 파이프라인 실행 (데이터 로드 → 팩터 계산 → 백테스트 → 리포팅)
  - Must NOT do: 
    - quantstats 라이브러리 import 실패에도 리포트 불가 (fallback: matplotlib-only)
    - HTML 리포트에 데이터 포함 안 됨 (self-contained HTML)
    - CLI 없이 스크립트 직접 실행만 가능
  Parallelization: Wave 4 | Blocked by: 5 | Blocks: 7
  References:
  - quantstats: https://github.com/ranaroussi/quantstats
  - Performance metrics: CAGR = (End/Start)^(1/years)-1, Sharpe = (Rp-Rf)/σp, MDD = max peak-to-trough
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  # Unit tests
  pytest tests/test_analysis.py::test_metrics -v
  
  # Check CLI works
  python -c "
  from super_quality.main import app
  from typer.testing import CliRunner
  runner = CliRunner()
  result = runner.invoke(app, ['--help'])
  assert result.exit_code == 0
  print('CLI help OK')
  "
  ```
  QA scenarios:
  - Happy: Any equity curve → CAGR, Sharpe, MDD calculated without error
  - Happy: Trade log CSV → 모든 필드가 올바른 타입과 범위
  - Failure: Empty trade log → metrics still calculate (0% return, flat equity curve) with clear warning
  - Edge: Single trade in log → metrics should still compute correctly
  Evidence: .omo/evidence/task-6-super-quality-v2.txt
  Commit: Y | feat(analysis): add performance metrics, HTML tearsheet, trade log, equity/drawdown charts, CLI entry point

- [x] 7. 통합 테스트 및 문서화
  What to do / Must NOT do:
  - tests/test_integration.py:
    - 전체 백테스트 단축 실행 (2015-01-01 ~ 2015-06-30, 작은 유니버스)
    - look-ahead bias 검증: 각 거래일 시그널이 해당일 이전 데이터만 사용
    - 예상 결과(스모크): 거래 발생, 포트폴리오 가치 변동
  - README.md:
    - 프로젝트 개요, 설치 방법 (uv sync)
    - DART API 키 발급 안내 (opendart.fss.or.kr → 회원가입 → 인증키 신청)
    - 실행 방법: `uv run super-quality --dart-api-key YOUR_KEY`
    - 전략 조건 요약표 (매수 A-J, 우선순위, 매도)
    - 출력물 설명 (HTML, CSV, PNG)
  - pyproject.toml에 scripts/entry-point 등록: `super-quality = "super_quality.main:app"`
  - Must NOT do: 
    - 실제 API 키를 README에 포함
    - 통합 테스트에서 실제 API 호출 (mock 사용)
    - 불필요한 문서 (설계 문서, 위키 등)
  Parallelization: Wave 5 | Blocked by: 6 | Blocks: —
  References:
  - README template: 표준 Python 프로젝트 README
  - DART API: https://opendart.fss.or.kr
  Acceptance criteria (agent-executable):
  ```bash
  cd /Users/durkjaeyun/projects/investment/super-quality
  # Integration test (mocked API)
  pytest tests/test_integration.py -v
  
  # README exists and has key sections
  grep -q "DART" README.md && echo "DART section OK"
  grep -q "uv run" README.md && echo "run command OK"
  
  # Full test suite
  pytest -v --cov=super_quality
  ```
  QA scenarios:
  - Happy: Integration test runs end-to-end with mock data → produces valid trade_log.csv
  - Happy: All unit tests pass (pytest -v)
  - Failure: Missing DART_API_KEY env var → clear error message (not crash)
  - Edge: No internet connection → integration test uses cached/mock data
  Evidence: .omo/evidence/task-7-super-quality-v2.txt
  Commit: Y | feat(integration): add integration tests, CLI entry point, README with setup instructions

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — ✅ 모든 todo 완료, pytest 120/120 pass, 출력물 5종 정상 생성
- [x] F2. Code quality review — ✅ ruff clean, 모든 함수 타입 힌트 + docstring, no TODOs/FIXMEs
- [x] F3. Output generation (mock data) — ✅ trade_log.csv, tearsheet.html, equity_curve.png, drawdown.png, portfolio_snapshots.csv 모두 생성 확인
      ⚠️ 실전 QA에는 DART_API_KEY 필요 — 사용자 제공 시 `uv run super-quality run --dart-api-key $KEY` 실행 가능
- [x] F4. Scope fidelity — ✅ Must have 7/7 구현, Must NOT have 7/7 제외 확인

## Commit strategy
- Each todo produces exactly ONE atomic commit with conventional commit format: `<type>(<scope>): <summary>`
- Types: chore, feat, fix, test, docs
- No commit is pushed — commits remain local until user decides
- Final commit sequence: 7 commits in order

## Success criteria
1. `uv run super-quality --dart-api-key <KEY>` 실행 시 전체 백테스트 완료
2. `outputs/` 디렉토리에 trade_log.csv, tearsheet.html, equity_curve.png, drawdown.png 생성
3. 전략 조건 A-J가 코드에 정확히 구현됨
4. look-ahead bias 없음 (데이터 lag 적용 검증 완료)
5. pytest -v 100% 통과
6. ruff lint 0 warnings
7. README에 따라 처음 사용자가 10분 내에 실행 가능
