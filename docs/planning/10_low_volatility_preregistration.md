# KOSPI 200 저변동성 연구 사전등록 (종료·보관; Phase 2 Stage A까지)

**등록일: 2026-08-21**

**최종 갱신: 2026-08-22**

**상태: Phase 0 사전등록, Phase 1 구현 및 Phase 2 Stage A 오프라인 기반 완료 — Oracle Pass; 연구 종료·보관**

> 이 문서는 **연구 전용** 사전등록이다. live trading과 paper trading을 하지
> 않는다. 폐기·중단된 KOSPI 200 Momentum + Quality(MQ) 전략과 별개의 가격 전용
> 가설이다. 이 문서만으로 과거 WFA 실행이나 2025년 이후 holdout 실행을 승인하지
> 않는다.

## Phase 1 완료 기록

- Oracle Pass를 완료했다.
- 고정 사양의 factor·selector, 분기 리밸런싱 스케줄, engine injection 경계,
  기업행동 합성(synthetic) 정책, development cutoff guard를 구현·확인했다.
- 검증 근거는 focused 테스트 35 passed, 전체 테스트 518 passed, Ruff passed이다.
- Phase 1에서는 역사적 WFA와 실제 2025년 이후 데이터에 접근하지 않았다.

## Phase 2 Stage A 완료 기록 (2026-08-22)

- 오프라인 production-evidence 기반을 완료하고 Oracle Pass를 받았다.
- synthetic fixture만으로 KRX raw-byte provenance, ISU_CD/ISIN identity,
  raw-to-canonical binding, root validator, zero-event action coverage,
  same-artifact transition marker, fail-closed 정책을 구현·확인했다.
- 검증 근거는 focused 테스트 66 passed, 전체 테스트 549 passed, Ruff passed이다.
- 네트워크 호출, raw 데이터 수집, WFA, adapter/engine production wiring, 실제
  2025년 이후 데이터 접근은 수행하지 않았다. Stage 2 데이터는 수집·검증되지 않았다.
- schema probe 결과 가격/유니버스 계약은 부분 관찰되었지만 action/status coverage는
  미입증이다. 준수 가능한 action/status 원천의 명시적 승인 또는 연구 종료 전까지
  실제 수집은 차단한다.

## KRX schema probe 기록 (2026-08-22; Phase 2 완료 아님)

- 사용자 승인 schema probe를 완료했다. probe raw artifact는 local/gitignored이며,
  역사적 데이터 수집과 WFA는 수행하지 않았다.
- `2024-12-30` `MDCSTAT01501` (KOSPI)은 `ISU_CD`, `ISU_SRT_CD`, OHLCV 및
  상장주식수 필드를 포함한 raw OHLCV 961행을 반환했다.
- 같은 날 `MDCSTAT00601`은 KOSPI 200 200행을 반환했으나 `ISU_CD`가 아닌
  `ISU_SRT_CD`만 포함했다. 운영 identity에는 같은 날짜 raw OHLCV와의 대조가
  필요하다.
- 2024년 `MDCSTAT23801`은 `ISU_CD`, 상장폐지일·사유, successor ticker 후보를
  포함한 상장폐지 56행을 반환했다. `MDCSTAT23902` selected-case query는 유효한
  zero-row 응답을 반환했으므로 price-history row schema를 입증하지 못했다.
- `MDCSTAT212`는 문서화된 UI에 historical-date query가 없어 호출하지 않았다.
  `MDCSTAT20901`의 2024 요청은 current-price 필드를 노출했으므로 응답과 sidecar를
  즉시 폐기했으며, cutoff-safe historical action source에서 제외한다.
- 결론: KRX price/universe 계약은 부분 관찰 상태이고 action/status coverage는
  미입증이다. KRX만으로 완전한 기업행동 ledger를 구성할 수 없다.
- 다음 게이트는 준수 가능한 action/status 원천(예: KSD/FSC rights-schedule API)의
  명시적 승인 또는 연구 종료다. 실제 raw bundle 수집과 WFA는 그 전까지 차단한다.

## KSD/FSC schema probe 기록 (dataset 15059609; 2026-08-22; Phase 2 완료 아님)

- `FSC_KSD_RIGHTS_SERVICE_KEY` 인증은 성공했다. 기업 필터를 적용한
  `2024-12-30` 요청은 정상적인 zero-event envelope를 반환했으나, 이는 포괄적인
  no-action coverage를 입증하지 않는다.
- 비필터 `basDt=20241230` schema 응답에는 `2025-01-13`까지의 권리 일정일이
  노출되었다. raw artifact와 sidecar는 즉시 폐기했다.
- 따라서 `basDt`는 cutoff-safe effective/event-date 경계가 아니다. KSD/FSC 원천은
  현재 strict development cutoff 계약에 채택하지 않는다. 데이터 수집이나 유효한
  action ledger를 주장하지 않는다.
- KRX 가격/유니버스는 부분 관찰 상태이며 KRX/KSD action/status coverage는 여전히
  불충분하다. 명시적으로 승인된 cutoff-safe historical action/status 원천이 없으면
  연구를 종료하며, 실제 bundle 수집과 WFA는 허용하지 않는다.

## OpenDART 기업행동 schema probe 기록 (2026-08-22; Phase 2 완료 아님)

- 기존 `DART_API_KEY` 사용, 모든 쿼리는 `2024-12-31` 이하로 제한. raw artifact와
  SHA-256 manifest는 local/gitignored `data/raw/k200_low_vol_schema_probe/opendart_actions/`에
  보관. bundle 수집이나 WFA는 수행하지 않았다.
- `/api/list.json` (삼성전자 corp_code 00126380): 접수일 범위 쿼리가 누수 없이
  동작(`rcept_dt` 2024-12-09~2024-12-23 for Dec window). 2024-10~12 전체 페이지네이션
  (112행, 12페이지; `per_page` 무시, 10행/페이지 기본) 중 배당 관련 공시는
  `현금ㆍ현물배당결정` 하나(`rcept_dt=2024-10-31`)뿐 — 이사회 결정 공시는 통상
  ex-date보다 먼저 인덱스에 게재된다. cutoff-safe publication-bounded 인덱스 경로다.
- `/api/alotMatter.json` (bsns_year=2023, reprt_code=11011): 실제 스키마는
  `se`(구분)/`thstrm`/`frmtrm`/`lwfr`/`stlm_dt`(결산기준일)/`stock_knd`/`rcept_no`.
  ex-date/record-date/payment-date 필드는 없다 — 기존 desk-research 주장(커뮤니티
  필드명)은 오류였고 raw probe가 이를 바로잡았다. 사업보고서 기준이라 회계연도 말
  이후 수개월 공시 지연이 있다.
- `/api/irdsSttus.json`: 사업보고서 기준 회계연도 키 `isu_dcrs_*` 필드. 삼성전자
  2023은 자본 변동 없음(전부 '-'). 동일한 지연 한계.
- 거래정지(suspension): OpenDART에 전혀 없음이 확인되었다.
- 결론: OpenDART는 PARTIAL 후보일 뿐이다. 적시 경로는 있으나 이벤트 상세는 비표준
  원문 파싱이 필요하고, 구조화 endpoint는 지연된 회계연도 요약이며, status(거래정지)
  커버리지가 누락된다. OpenDART 단독으로는 저변동성 전략의 완전한 cutoff-safe
  기업행동 + status ledger 계약을 충족하지 못한다. 연구는 종료·보관 유지.

## 진단 계층 예외 선언 (2026-08-22; adjusted-price 단일 실행, 미검증)

- 사용자 승인(2026-08-22)으로 연구 종료 상태에 범위 한정 진단 계층 예외를 선언했다
  (Oracle 권고). 기존 pykrx adjusted-price 캐시를 동결 저변동성 스펙(분기 신호일,
  trailing 252세션 변동성, 하위 20% 동일비중)에 적용하는 단일 실행 진단이다.
- adjusted price는 총수익률을 근사하므로 가격수익률 목표를 과대평가(optimistic
  upper-bound)한다. 명시 라벨 `diagnostic_adjusted_price_non_validated`. 검증된
  주장이 아니며 classification 승격 불가.
- **사전 고정 판단 규칙 (실행 전 확정, 사후 변경 금지)**: adjusted-price 진단이 모두
  충족할 때만 기업행동 ledger 인프라 작업으로 진행 — CAGR ≥ 7%, Sharpe ≥ 0.75,
  MDD ≥ −25%. 하나라도 미달이면 신규 경제적 가설 부재 시 저변동성 가설은 영구 보관.
- **무결성 guardrail**: 2024-12-31 development cutoff guard 재사용; 결과와 무관하게
  사후 스펙 변경 금지; 2025+ 데이터 미접촉; 출력 namespace는 검증 artifact와 분리;
  단일 실행, 튜닝/반복 OOS 없음.
- **Oracle 동기**: (a) 동결 validator가 adjusted-price provenance를 전면 거부하므로
  진단은 패키지 외부 standalone script로만 존재; (b) 향후 재개는 지원 action 집합을
  {split, reverse_split, cash_dividend, suspension, delisting} 이상으로 확장 + 가격
  파생 suspension/resumption 일자 포함 필요 — 침묵 아닌 선언적 수정; (c) 구조적 역풍:
  저변동성은 고배당 종목에 치우치나 스펙은 가격수익률(CAGR≥5% 게이트) 기준.
- **정지 조건 재확인**: 진단 실패 → 영구 보관; 검증 단일샷 미달 → 사전등록대로 종료;
  ledger 작업 >6주 또는 cutoff-safety 위반 → 중단; 이 유니버스에서 3회 연속 실패한
  사전등록 가설군 → 해당 시장 연구 중단.

## 저변동성 adjusted-price 진단 실행 결과 (2026-08-22; ARCHIVE_PERMANENTLY)

- 사전 고정 판단 규칙에 따라 단일 실행을 수행했다 (실행 전 커밋 `b9f79dd`). 스크립트:
  `scripts/run_low_vol_diagnostic.py` + `tests/test_low_vol_diagnostic.py` (6 passed,
  ruff clean). 출력: `outputs_k200_lowvol_diagnostic/` (manifest/daily returns/
  rebalance log/summary), 전부 `diagnostic_adjusted_price_non_validated` 라벨.
- 결과 (adjusted-price 낙관적 상한): 연결 총수익률 **+8.20%** (~9.75년), **CAGR
  0.81%** (게이트 ≥7% — FAIL), **Sharpe 0.141** (게이트 ≥0.75 — FAIL), **MDD
  −61.49%** (게이트 >−25% — FAIL), Calmar 0.013. 연도별: 2015 −12.2%, 2016 +21.2%,
  2017 +21.8%, 2018 −27.8%, 2019 +8.6%, 2020 −3.5%, 2021 +3.8%, 2022 −10.2%,
  2023 +8.4%, 2024 +9.1%.
- **판정: ARCHIVE_PERMANENTLY** — 세 게이트 모두 결정적 실패. adjusted price는
  총수익률을 근사해 가격수익률 목표를 과대평가하므로, 검증된 가격수익률 버전은
  a fortiori 실패한다. 사전 규칙에 따라 신규 경제적 가설 부재 시 저변동성 가설은
  영구 보관된다.
- 상태: 저변동성 연구는 이제 영구 보관(기존에는 데이터 원천 대기 종료). MQ는 보관·
  피벗 유지. 양쪽 모두 live trading, 추가 튜닝, OOS 반복 없음. Phase 0/1/2A code
  contract는 유효한 역사적 인프라로 유지.
- 정지 조건 맥락: 이 유니버스/윈도우에서 두 번째 실패한 사전등록 가설군(MQ, 그다음
  저변동성). 선언된 메타 조건에 따라 이 유니버스에서 사전등록 가설군 3회 연속 실패 시
  해당 시장 연구를 전면 중단한다. 향후 연구는 새로운 경제적 정당화와 별도 사전등록이
  필요하다.

## 연구 종료·보관 기록 (2026-08-22)

- KRX/KSD probe로 cutoff-safe full action/status source를 확립하지 못해 Phase 2
  연구를 종료한다. Phase 0/1과 Phase 2 Stage A offline validator foundation의
  완료·승인 상태는 유지되며, 이번 결정은 해당 code contract의 무효화가 아니다.
- valid raw production bundle은 존재하지 않는다. 역사적 WFA, holdout, live/paper
  trading, raw bundle 수집, 추가 튜닝은 수행하지 않으며 허용하지 않는다.
- KRX 가격/유니버스 및 KSD cutoff 실패 probe는 역사적 진단일 뿐이다. 미래를 노출한
  KSD artifact는 즉시 폐기되어 evidence가 아니다.
- 연구는 향후 source approval 전까지 종료·보관한다. 재개에는 compliant cutoff-safe
  historical action/status 원천의 명시적 승인과 새로운 evidence review가 필요하다.
  정책을 조용히 완화하거나 adjusted-price/vendor data로 대체하지 않는다.

## 1. 가설과 고정 사양

### 가설

각 분기 신호일에 PIT KOSPI 200 구성원 중 저변동성 하위 20%를 선택하면, 가격수익률
기준에서 장기 위험조정 성과가 개선되는지 검증한다. 종목은 동일 비중으로 보유하고
다음 KRX 거래일 시가에 실행한다.

### 신호와 자격

- 신호일을 포함한 최근 KRX 거래 세션 252개를 사용한다.
- 수익률은 `close[t] / close[t-1] - 1`, 변동성은 표본 표준편차 `ddof=1`이다.
- 기업행동 중립적이고 연속된 세션의 종가 쌍만 유효 관측치로 센다. 유효 수익률은
  최소 200개여야 한다.
- zero volume, 공식 거래정지, stale 또는 관측되지 않은 세션은 유효 수익률을 만들지
  않으며 유효 관측치로 세지 않는다. forward fill, 0 수익률 대체, neutral-value fill,
  암묵적인 `pct_change` fill을 사용하지 않는다.
- 신호일 거래정지, 유효한 종가/거래량 누락, 관측치 부족이 있는 종목은 제외한다.
- 관련 가격 구간에 미해결·미지원 기업행동이 하나라도 있으면 개별 종목을 제외하는
  대신 전체 가격 bundle과 실행을 fail closed한다. 지원 기업행동은 factor의 경제적
  수익률과 engine의 보유 수량/기준가격에 일관되게 반영되어야 한다.
- `floor(eligible_count * 0.20)`개를 선택하고 경계 동률은 ticker 오름차순으로
  결정한다.
- 신호일은 3·6·9·12월의 마지막 KRX 거래 세션으로 연 4회 고정한다. 휴일이면
  캘린더 정의에 따라 직전 KRX 세션으로 롤백한다.
- 유효한 다음 세션 시가가 없으면 주문을 취소한다. 합성 가격이나 이후 자동 재주문은
  금지한다.
- 거래정지는 마지막 공식 종가로 평가하되 거래하지 않는다. 미확인 상장폐지의
  회수 가치는 0으로 한다. 이 정책은 전략과 가격수익률 벤치마크에 동일하게 적용한다.

### 고정 제외 항목

모멘텀, 품질, regime, stop-loss, ADV, 섹터 cap, 상관관계 필터, WFA 후보 선택,
grid, parameter override를 사용하지 않는다. 변경은 새 가설과 새 사전등록이
필요하다.

## 2. 데이터와 PIT 계약

- 기준은 가격수익률이며, 통상 현금배당을 재투자하지 않는다. **총수익률이라고
  주장하지 않는다.**
- 원천은 raw KRX OHLCV canonical bundle이다. `pykrx(adjusted=True)`만으로는
  provenance 원천이 될 수 없다.
- raw-price/action-ledger bundle, 원천 manifest/hash, 기업행동 처리 계약을 요구한다.
  split/reverse split을 포함한 지원 가능한 기업행동을 명시하고, rights 또는
  미해결·미지원 기업행동은 fail closed한다. 증권 식별자 처리도 명시해야 한다.
- 이 validator/ledger가 완성되기 전에는 역사 WFA를 막는다. 기존 MQ 재무 validator를
  재사용하거나 완화하지 않는다. 저변동성 검증 분류에는 universe, raw price,
  corporate-action, development-cutoff validator가 모두 필요하다.
- `2024-12-31`을 hard development cutoff로 고정하고 loader, prepared inputs,
  factor, engine, WF 전 계층에 적용한다. 설계·디버깅에 2025년 이후 실제 데이터를
  사용하지 않는다.
- 개발 호출의 requested end date, 반환 row, cache row 중 하나라도 `2024-12-31`보다
  늦으면 전체 development call을 거부한다. 조용히 cutoff까지 자르지 않는다.
- 이 전략은 financial data를 소비하지 않으며 financial data를 검증하지도 않는다.
  validated classification은 실제 universe/raw-price/corporate-action/cutoff
  validator 출력만 사용한다. 문자열이나 boolean 하나만으로는 승격할 수 없다.
- 2025년 이후 holdout은 사양·config·data·calendar·cost·git hash를 동결한 뒤
  별도 승인된 실행에서 한 번만 가능하다. 이 문서는 이를 승인하지 않는다.

## 3. 평가 배치 (실행 승인 아님)

Phase 0의 아래 내용은 향후 평가 레이아웃을 고정하는 것이며, 실행을 승인하지 않는다.

- 역사적 development는 2015–2024 고정 5-fold expanding WF만 사용한다.
- 각 fold는 train의 12월 신호일부터 첫 OOS 다음-시가 체결까지 carry-in을 포함해야
  한다. 마지막 미체결 주문은 취소한다.
- fold의 `test_end` 종가에서 발생한 다음 세션 주문이 fold 밖이면 실행하지 않으며,
  비용과 수익률에서도 제외한다.
- 기존 commission/slippage/delisting/suspension 규칙은 전략별 계약이 구현되기
  전까지 유효하다고 가정하지 않는다. 거래세는 날짜별 공식 세율을 사용한다.

### 사전등록 no-go 기준

다음 기준을 모두 충족해야 하며, 하나라도 실패하면 가설을 종료한다. 같은 OOS에서
튜닝하지 않는다.

- 모든 기본 성과지표는 5개 fold 전체를 연결한 post-cost stitched OOS NAV로 계산한다.
- turnover의 buys/sells는 실제 체결 notional이고, 각 연도의 avg daily NAV는 해당
  연도의 일별 NAV 산술평균이다.
- 5/5 fold valid;
- CAGR `>= 5%`;
- Sharpe `>= 0.7` (고정 연 3.5% 무위험수익률);
- MDD `>= -25%`, Calmar `>= 0.3`;
- 양의 OOS 연도 `>= 3개`;
- 연간 one-way turnover 정의 `(buys+sells)/(2*avg daily NAV)`가 carry-in을
  포함한 모든 OOS 연도에서 `<= 2.0`;
- 연간 수익률을 `R_y`로 할 때 positive-year concentration은
  `max_y{max(log(1+R_y), 0)} / sum_y max(log(1+R_y), 0) <= 0.5`;
- 연율화 OOS 변동성이 KPI200의 `<= 90%`이고 strategy Sharpe가 KPI200보다 높음;
- 2x cost stress는 commission/slippage만 2배로 하며, CAGR `> 0` 및 Sharpe
  `>= 0.55`를 충족.

## Phase 1 authorization boundary (완료 범위)

### 허용

동결 사양에 대한 code/unit test, raw-price/action bundle interface의 fail-closed
validator, cutoff guard, factor/scheduler/selector, strategy injection boundary,
carry-in test, fingerprint 기록만 허용한다.

### 금지

실제 ledger 데이터 수집, 2015–2024 WFA, 2025년 이후 데이터 접근, 후보·grid·민감도
실험, 백테스트 결과 주장을 금지한다. 위 범위를 벗어나는 변경이나 실행은 새 가설과
새 사전등록 없이는 수행하지 않는다.

## Phase 2 authorization boundary (Stage A 이후; 실행 승인 아님)

Phase 2에서 허용되는 작업은 **검증된 PIT raw KRX OHLCV + 기업행동 ledger의
data-contract 및 validator 구현**뿐이다. validator fixture/unit test, provenance,
cutoff, fail-closed 검사를 함께 구현할 수 있다. 실제 raw 데이터 수집·연결과
역사적 WFA는 validator와 원천 증거가 별도 승인될 때까지 차단한다. 따라서 Phase 2
데이터가 수집되었거나 검증되었다고 해석해서는 안 된다.
