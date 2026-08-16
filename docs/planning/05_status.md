# 현재 상태 - KOSPI 200 모멘텀 + 품질

이 문서는 현재 상태를 기록하는 정식 문서입니다.

최종 갱신: 2026-08-16

## 2026-08-16 현재 체크포인트

- **유니버스 PIT화 완료 (커밋 `b2740ee`)**: pykrx
  `get_index_portfolio_deposit_file`(KRX 공식, 과거 날짜 지원)로 120개 as-of
  날짜(2015-01-30 ~ 2024-12-31)의 실제 KOSPI 200 구성원을 fetch →
  `data/universe/kospi200_bundle_pit/` (323 유니크 티커, 스냅샷 200~202).
  가격 캐시 미보유 155개 티커 백필 완료 (0 missing).
  **생존자 편향 정량화**: 2015-05-29 기준 proxy와 일치 108 / proxy-only 90 /
  pit-only 92 — proxy 구성원의 ~46%가 실제 역사 구성원과 다름.
- **Day 14 strict WF (PIT 유니버스, 최초)**: 유니버스 전 as-of
  `pit_valid=true` (유니버스 PIT 게이트 달성). 5/5 폴드 valid, OOS 1,231점,
  stitched **+20.55%** (proxy +44.15% 대비 -23.6%p — 생존자 편향 제거로 하향).
  첫 리밸런스(2015-05-29) momentum 121/200 usable, six-fact 재무 커버리지
  73/200 (36.5%, proxy 대비 하향 — PIT 구성원에는 상장폐지/후기편입 종목 포함).
- **Classification 승격 검토 (Oracle, 커밋 `57efeec`)**: validator wiring은
  이미 완료·통과 중 (`_validate_prepared_pit_provenance`가 strict preflight +
  interval마다 유니버스/재무 검증). 차단은 하드코딩
  (`main.py:3117,2885,2986,3145` + `walk_forward.py:39-49,579`)와 증거 프록시
  2건 — ① `filing_date_used`가 엔진 소비 증명이 아닌 자기참조 프록시,
  ② quality 6-fact 부분 커버리지가 PIT 유효성에 미포함. 두 전제조건을 닫고
  adapter에서 실제 validator 결과로 classification을 결정해야 승격 가능.
- **Day 15-17 PIT 유니버스 진단 (커밋 `c81dec8`)**:
  - Day 15 ADV 필터: 5/5 폴드 실패 — PIT 유니버스 상장폐지 종목 16개가 가격
    캐시에서 mcap=0 (KRX 상장폐지 데이터는 시가총액 미제공) → ADV 필터
    fail-closed. ADV 필터는 상장폐지 mcap 보강 또는 커버리지 정책 필요.
  - Day 16 모멘텀 0.7/0.3: stitched **+53.23%** (Day 14 대비 개선, proxy와
    동일 방향).
  - Day 17 손절 비활성: stitched **+35.85%**이나 2020 MDD -37.9% 급증 —
    손절 MDD 방어를 PIT에서도 확인.
- **Phase 3 FY2014 XBRL 병합 (커밋 `0113611`)**: `dart_aggregated_day4_extended_fy2014/`
  (facts 304,245행 = 303,693 + 552). 첫 리밸런스 six-fact 커버리지 0/198 →
  92/198 (proxy 기준). OOS 성과는 FY2014가 2015+ 데이터만 쓰는 OOS 구간에
  영향 없음.

## 2026-08-10 체크포인트 (보관)

- **Day 8 strict PIT WF 실행 완료(2026-08-10)**:
  `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0
  --local-pit-universe-path data/universe/kospi200_bundle_strict
  --local-pit-universe-source-kind snapshots
  --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json
  --local-dart-filing-path data/raw/dart_aggregated_day4_extended/dart_filings_merged.csv
  --local-dart-filing-manifest data/raw/dart_aggregated_day4_extended/dart_filings_merged.manifest.json
  --local-dart-financial-path data/raw/dart_aggregated_day4_extended/dart_facts_merged.csv
  --local-dart-financial-manifest data/raw/dart_aggregated_day4_extended/dart_facts_merged.manifest.json
  --output outputs_k200mq_day8_strict_extended`
- 결과: 5/5 폴드 `valid=True`, OOS 1,231점(2020-2024). strict preflight 실패 0건.
- **재무 provenance 첫 승격**: financial mode가 `pit_filing_date` + `pit_valid=true`
  (`filing_date_used=true`, `next_session` 정책)로 이전 strict 실행들의 `non_pit_fiscal_period`에서
  벗어났다. 유니버스도 전 as-of 날짜 `provenance=pit`.
- 성능 최적화 커밋 `238a4de`: `dart_pit.py` 세션 매핑을 searchsorted로, 미래 접수 행 드롭을
  벡터화해 prepare 파이프라인을 8분+ → ~82초로 단축했다. (실행 전 검증: ruff 통과, dart 테스트 50개,
  전체 409 passed + 기존 무관 레거시 실패 1건)
- DART extended aggregate: prepare 후 259,255행 매핑 / future receipt 44,438행 드롭.
- **준비성 해석 정정**: Day 8 `first_ready_rebalance`의 usable 147/198 및
  missing 51은 해당 신호일의 `momentum_z` 준비성이다. `quality_required=false`이므로
  이 수치는 재무/품질 커버리지 결손을 뜻하지 않는다(`src/k200_mq/main.py:1150-1176`).
- **Phase 3 FY2014 XBRL 현황**: FY2014 원본 접수 141건 선정, 검증된 XBRL ZIP
  119건, strict six-fact accepted 92건, 요청한 XBRL 문서를 이용할 수 없음을
  나타내는 OpenDART 공식 상태 `014` 22건(단순한 로컬 파일 누락이 아님),
  parser fail-closed 27건. FY2014 XBRL은 재무 PIT facts를 개선하지만 momentum
  warmup을 해결하지는 않는다.

## 2026-08-06 이전 체크포인트 (보관)

- Day 4 backfill 자동화 스크립트가 추가되어 fetch → aggregate → strict rerun 흐름을 한 번에 실행할 수 있게 되었다.
- Day 4 배치 스펙(`data/raw/dart_batch_spec_day4_missing_both.json`)이 워크스페이스에서 누락되어 있었으나, 결손 ticker/corp_code 목록 재산출 후 동일 조건(7,667 requests)으로 복구했다.
- 2026-08-06 재개 시도에서 7,667건(187 filing + 7,480 financial) 요청이 전부 OpenDART `status=020`(요청 제한 초과)으로 반환되어, fetch 후 aggregate 전에 중단되었다. 근본 제약은 API 일일 쿼터(일반적으로 2만 건 수준)이며 자정 리셋 후 청크 재수집이 필요하다.
- fetch 스크립트와 day4 runner는 멱등 청크 재개를 지원한다: `--skip-verified`(이미 verified된 파일 재수집 방지), `--delay-seconds`(초당 제한 회피), `FETCH_ONLY`(청크 수집 중 aggregate/WF 재실행 방지).
- 연간 전용 축소 배치 스펙(`data/raw/dart_batch_spec_day4_missing_annual.json`, 2,057건)이 추가되었다. 품질 팩터 입력은 신호일 기준 최신 facts를 ffill로 쓰고 TTM 필터가 inert여서, 통제된 fixture에서 연간(11011)만으로 PIT 준비 파이프라인(`pit_valid=True`)이 통과함을 확인했다. 재개 실행은   이 축소 스펙을 우선 사용해 020 재발 위험을 3.7배 낮춘다.
- 로컬 DART → 품질 팩터 배선 결함이 수정되었다(2026-08-06). `ACCOUNT_COLUMN_MAPPING`
  (`src/k200_mq/data/account_mapping.py`)이 계정명/계정코드를 wide 6컬럼으로 매핑하고,
  `dart_pit.pivot_financial_facts_to_wide`가 long format facts를 wide로 피벗하며,
  `main.py` 품질 게이트가 `DART_API_KEY` 또는 로컬 원천 준비 상태를 검사한다.
  통합 테스트로 ROE 8.3% / D/E 0.67 / gross-margin proxy 60% / CashConv 0.8을 확인했다.
- 실제 파일 end-to-end 스모크 완료(2026-08-06). keydedup pilot DART + bundle 유니버스로
  `run`(2020-2024)을 실행했고, manifest에서 `data_mode=pit_filing_date`, `pit_valid=true`,
  `quality_factor_row_count=10,990`(7개 티커)을 확인해 로컬 DART → 품질 팩터 경로가
  실데이터에서 활성화됨을 검증했다. 이는 진단이며, 커버 7종목뿐이라 검증된 성과 근거가 아니다.
- 매핑 커버리지 진단(2026-08-06): 실제 facts 57개 보고서 기준 6개 품질 컬럼 매칭률은
  revenue/net_income/operating_cf/total_assets/total_equity 100%, cogs 96.5%(미매칭 2건은
  금융사 00104856으로 COGS 자체가 없음). 실제 갭 1건(`매출 원가` 공백 변형)을 매핑에 반영했다.
  equity 정확성도 확인: 57/57 보고서가 exact equity 행을 가지며 equity ≤ assets.
- 현재 환경에서는 `DART_API_KEY`가 설정되어 있지 않아 live fetch는 실행될 수 없고, 이 상태에서는 신규 DART 수집을 통한 coverage 개선이 불가능하다.
- 로컬 union 기반 오프라인 대안으로도 strict 준비 조인 불안정성이 해소되지 않아, 현재는 데이터 확보 단계가 여전히 병목이다.
- strict PIT 로컬 상한 조사(2026-08-06): 로더의 `join_financial_facts_to_filings`는 `(corp_code, rcept_no)`로 모든 facts가 filings에 존재해야 하며, `_reject_fact_provenance_collisions`가 facts로부터 일자 파생을 금지한다. mapped 배치는 corp당 filing 1페이지만(`pagination.complete: false`)이고 20 corp는 filing 파일이 전혀 없어, **strict PIT 커버리지는 로컬 데이터로 7종목 상한**이다. 확장은 DART API(키·쿼터)에 전적으로 종속된다.
- Option D 실행(2026-08-06): keydedup 7종목 DART + bundle 유니버스로 `true-walkforward` 2020-2024를 실행해 `outputs_k200mq_mechanical_full/`에 저장했다. 품질 팩터 18,305행 계산(51행 재무 입력), stitched OOS 수익률 +79.7%(5년), CAGR 12.4%, 최대 낙폭 -20.9%, OOS 1,231점. 단 유니버스가 현재 시점 mcap proxy라 `mechanical_expanding_walk_forward_non_pit`으로 분류되며 검증된 성과 주장이 아니다.
- 따라서 현재 공식 진단은 계속해서 비-PIT 기계적 진단으로 유지되며, strict PIT 근거 승격은 API key 확보 후 재실행이 필요하다.

## 한눈에 보는 상태

| 범주 | 현재 상태 |
|------|-----------|
| 구현된 인프라 | 파이프라인, 팩터, 포트폴리오 엔진, CLI, 벤치마크, 실제 체결 비용 귀속, KRX/DART 로컬 provenance 계약, 검증 보호 장치, 테스트가 구현됨. |
| 기계적 비-PIT 진단 | `robustness` 독립 하위 기간 테스트와 expanding-window `true-walkforward`를 사용할 수 있음. |
| 검증된 PIT 근거 | 아직 없음. strict preflight는 통과했으나 분류는 여전히 `mechanical_expanding_walk_forward_non_pit`. 승격은 하드코딩 제거 + 증거 프록시 2건 해소 후 adapter에서 결정. |
| 유니버스 PIT | **달성 (2026-08-16)**: `data/universe/kospi200_bundle_pit/` — pykrx KRX 공식 역사 구성원 120개 as-of, 전 as-of `pit_valid=true`. |
| 재무 PIT | 달성: `dart_aggregated_day4_extended_fy2014/` (FY2014 XBRL 병합), `pit_filing_date` + `pit_valid=true`. |
| 현재 공식 진단 | Day 14 strict WF (PIT 유니버스, 2020-2024): stitched **+20.55%** (proxy +44.15% 대비 -23.6%p, 생존자 편향 제거 효과). 5/5 valid, OOS 1,231점. 첫 리밸런스 momentum 121/200, six-fact 73/200 (36.5%). 분류는 `mechanical_expanding_walk_forward_non_pit`. |
| 파라미터 진단 | proxy: ADV -1.37% / mom0.7 +71.42% / no-SL +117%(MDD↑). PIT: mom0.7 **+53.23%** / no-SL +35.85%(MDD↑) / ADV 실행 불가(상장폐지 mcap=0). 모두 기계적 진단. |
| 폐기된 결과 | v4 이전 및 현금 전파 수정 이전의 모든 성과 출력은 감사 전용이며 현재 결과가 아님. |
| 다음 게이트 | classification 승격 전제조건 2건 (filing_date_used 하드 증명, quality 커버리지 게이트/명시 한계) 해소 후 adapter에서 validator 결과로 classification 결정. ADV 필터 상장폐지 mcap 보강 또는 커버리지 정책. |

이 프로젝트는 검증된 투자 전략이 아니라 베타 단계의 인프라입니다.

### Day 8 readiness erratum (2026-08-13)

기존의 “첫 리밸런스 usable 147/198, missing 51”을 재무/품질 커버리지 갭으로
해석한 기록은 superseded이다. 정확히는 first-ready 리밸런스의 `momentum_z`
readiness이며 `quality_required=false`이다(`src/k200_mq/main.py:1150-1176`).
FY2014 XBRL은 재무 PIT facts를 개선하지만 momentum warmup을 해결하지 않는다.

Phase 3 FY2014 XBRL: 원본 접수 141건 선정, 검증된 XBRL ZIP 119건, strict six-fact
accepted 92건, 요청한 XBRL 문서를 이용할 수 없음을 나타내는 OpenDART 공식 상태
`014` 22건(단순한 로컬 파일 누락이 아님), parser fail-closed 27건.

### 리밸런스별 재무 커버리지 진단 (구현)

`src/k200_mq/main.py`에 no-lookahead 재무 커버리지 진단을 구현했다. 각 리밸런스의
정확한 PIT 유니버스 as-of를 기준으로, 해당 리밸런스에 매핑된 측정 신호일 이전 또는
동일한 최신 상태의 완전한 6개 원천 사실(revenue, cogs, net_income, operating_cf,
total_assets, total_equity)을 점검한다. 원천 가용성과 PIT 게이트 통과 커버리지는
분리해 기록하며, 중립값으로 채운 quality 입력/팩터에서 커버리지를 추론하지 않는다.
이 기록은 구현 문서화이며, 신규 실행 숫자를 추가하거나 새 수치 결과를 주장하지 않는다.

품질 의미론은 `max(revenue - cogs, 0) / revenue`인 floored gross-profit /
gross-margin proxy이다. 이는 true operating income 또는 operating margin이 아니다.
완전한 six-fact row가 없는 종목/보고서는 quality-scored 대상이 아니며, 최종 factor
merge에서 품질 결측을 허용하는 경우에만 quality가 neutral-fill(0)된다. neutral-fill은
원천 품질 커버리지를 의미하지 않는다.

## 구현된 인프라

- `src/k200_mq/`에 데이터 준비, v4 모멘텀, 정규화 입력 품질, regime, 전략,
  포트폴리오 엔진, 보고, 검증 계층이 들어 있습니다.
- 파이프라인은 유니버스 준비, 가격 룩백, 팩터 준비, 전략 선택, 실행, 결과 저장을
  순서대로 수행합니다.
- 포트폴리오 엔진은 regime 비중 조절, trailing stop-loss, 다음 세션 실행, 목표
  비중 조정, 현금 전파, 설정된 수수료/슬리피지/매도세 처리를 포함합니다.
- `run`, `robustness`, `true-walkforward` 모듈 CLI 경로가 연결되어 있습니다.
- `robustness`는 학습이나 파라미터 적합이 없는 독립 하위 기간 테스트이며
  walk-forward 교차검증이 아닙니다.
- expanding-window WF 실행기는 학습 전용 선택을 수행하고, 테스트 평가 전에 모든
  선택을 고정하며, 입력을 구간별로 자르고, 거래 캘린더가 있을 때 정확한 준비 OOS
  날짜를 확인합니다.
- WF 산출물에는 선택/폴드 결과, 요약, OOS 수익률, 비밀값을 제외한 유효 config/hash,
  git 상태, 준비 맥락이 포함됩니다.
- 벤치마크는 KPI200 종가 기반 **가격수익률**로 구현되어 있습니다. 총수익률이 아니며
  배당/분배금을 제외합니다.
- 비용 귀속은 실제 체결 거래에 구현되어 있습니다. 거래 로그, 실행 통계, 스냅샷,
  metrics, 매니페스트의 비용 합계를 서로 조정·일치시킵니다.
- 의미 안전성 수정과 테스트가 구현되어 있습니다: v4 skipped-return 모멘텀,
  명시적 품질 가중치, regime 수익률 기준, 손절 검증 및 관련 실행 도메인 검사.
- K200MQ 팩터, 전략, 엔진, provenance, 벤치마크, 비용, WF 회귀 테스트가 구현되어
  있습니다. 레거시 `src/super_quality/` 패키지는 동결된 상태입니다.
- 로컬 파일 구조 후보 importer가 `src/k200_mq/data/pit_universe.py`에 구현되어
  있습니다. CSV/JSON/Parquet/bytes 스냅샷 또는 명시적 유효일 구간을 정규화하지만,
  별도 수집 매니페스트가 공식 KRX 원천 메타데이터와 원시 바이트 SHA-256을 입증하기
  전에는 검증되지 않은 상태입니다.
- bundle-directory strict 경로가 추가되어, 날짜별 sidecar manifest와 bundle manifest
  identity를 이용해 다중 날짜 입력을 검증할 수 있습니다. 현재는 2015-01-30처럼
  198 구성원 날짜가 남아 있는 역사 파일이 있어, `target_size=200`을 통과시키려면
  documented transition exception 또는 원천 보정이 필요합니다.
- 인증된 KRX 수집 어댑터가 `src/k200_mq/data/krx_pit.py`에 구현되어 있습니다.
  검증된 라이브 계약은
  `https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd`와
  `bld=dbms/MDC/STAT/standard/MDCSTAT00601`, `indIdx2=028` (KOSPI 200),
  `indIdx=1`, `trdDd=YYYYMMDD`이며, 로그인에는 공식
  `MDCCOMS001.cmd`/`MDCCOMS001D1.cmd` 엔드포인트를 사용합니다. 두 날짜에 대한
  라이브 스모크 테스트를 완료했고 정확한 원시 JSON 응답과 별도 SHA-256 수집
  매니페스트를 저장합니다.
- 원시 KRX 파일과 사이드카는 로컬 수집 산출물이며 커밋하지 않습니다. 두 날짜의
  스모크 테스트와 파일 존재만으로는 충분한 역사적 KOSPI 200 구성원에 대한 운영
  성과 근거가 되지 않습니다.
- 명시적 로컬 KRX PIT 원천 통합이 `config`·`universe`·`main`에 구현되어 있습니다.
  `LOCAL_PIT_UNIVERSE_PATH`, `LOCAL_PIT_UNIVERSE_SOURCE_KIND`,
  `LOCAL_PIT_UNIVERSE_MANIFEST`를 설정하면 해당 원천을 사용하고 proxy/cache 경로를
  사용하지 않습니다. strict 모드(`STRICT_PIT_VALIDATION=true` 또는 `--strict-pit`)에서는
  `LOCAL_PIT_UNIVERSE_PATH`와 `LOCAL_PIT_UNIVERSE_MANIFEST`가 필수이며,
  미설정이면 유니버스 로드 전에 즉시 중단(fail closed)합니다. 설정한 원천의
  파일·매니페스트·날짜·해시·타임스탬프·재로딩 검증 실패도 즉시 중단됩니다.
  이 입력은 `run`/`robustness`/`true-walkforward` CLI에서
  `--local-pit-universe-path`, `--local-pit-universe-source-kind`,
  `--local-pit-universe-manifest`로 직접 전달할 수 있습니다.
  또한 strict 실행에서 `EXCLUDE_KOSPI_TOP_N`은 PIT rank 근거가 없으면 거부되므로,
  `true-walkforward`/`robustness` CLI에서는 `--exclude-kospi-top-n 0`로
  명시적으로 비활성화할 수 있습니다.
- 입력 형식 정리 도구로 `scripts/build_local_pit_universe_snapshot.py`를 추가했습니다.
  월별 파일 묶음을 단일 canonical snapshot CSV + manifest로 변환해
  `--local-pit-universe-path`의 파일 경로 계약을 맞출 수 있습니다.
  이 도구는 형식 변환 유틸리티이며 PIT provenance 사실성을 자동 보증하지 않습니다.
  strict 프로브 결과, 다중 날짜 스냅샷을 단일 매니페스트 토큰으로 대표하면
  `verified acquisition tokens do not cover each date exactly once`로 거부됩니다.
  따라서 strict 유니버스 완주에는 날짜별 identity가 있는 매니페스트 체인 또는
  동등한 interval provenance 계약이 추가로 필요합니다.
  bundle-directory 경로를 실제로 연결한 뒤에는 일부 날짜(예: 2015-01-30)가
  198 구성원으로 검증되어 `target_size=200`에서 멈춥니다. 이 경우는 documented
  transition exception 또는 원천 정리 없이는 strict를 통과할 수 없습니다.
- provenance 계약은 여러 스냅샷의 날짜별 범위, 원시 해시, 시간대가 있는 타임스탬프,
  행 수, 스냅샷 식별자, 매니페스트, 토큰 및 결합 후 정규화 프레임 fingerprint를
  재검증합니다. 여러 날짜를 하나의 해시나 하나의 매니페스트로 잘못 대표하지
  않도록 강화되어 있습니다.
- 로컬 파일 우선 DART provenance 계층이 `src/k200_mq/data/dart_pit.py`에 구현되어
  있습니다. 원시 제출과 재무 사실을 정규화하고, 응답 매니페스트의 SHA-256 사이드카를
  확인하며, `(corp_code, rcept_no)`로만 조인하고, 철회/모호한 조인을 거부하고,
  공식 날짜 전용 `rcept_dt`를 제출일보다 엄격히 뒤인 첫 KRX 세션으로 매핑합니다.
  실시간 API/벌크 수집은 아직 없지만, 2026-08-06부터 로컬 DART 파일은 공용
  `ACCOUNT_COLUMN_MAPPING`(`data/account_mapping.py`)과 `pivot_financial_facts_to_wide`를
  거쳐 품질 팩터 기본 경로에 연결되었습니다.
- strict 준비 경로는 이제 local DART filing metadata와 financial facts를 sidecar
  manifest와 함께 받아들입니다. API 키가 없어도 검증된 로컬 DART 파일로
  filing-date provenance를 세울 수 있지만, 역사 범위가 더 넓어야 검증된 PIT
  근거로 승격할 수 있습니다.
- 공용 DART 계정 매핑(`src/k200_mq/data/account_mapping.py`)과 long→wide pivot
  (`dart_pit.pivot_financial_facts_to_wide`)이 추가되어, 로컬 DART facts가 품질
  팩터 6입력(revenue, cogs, net_income, operating_cf, total_assets, total_equity)으로
  직접 흐릅니다. 정규화 로더(API 경로)와 동일 매핑을 공유하며, `main.py` 품질
  게이트는 `DART_API_KEY` 또는 로컬 원천 준비 상태를 검사합니다.
- 2026-08-04 기준, local DART facts의 `period_end` 보정은 로더 정규화 단계에서
  수행되도록 이동되었습니다. 준비 단계 이후 DataFrame 변형으로 lineage fingerprint가
  깨지던 문제가 제거되어, session-bounded pilot quick check에서는
  `pit_filing_date`/`pit_valid=true`를 확인했습니다.
- raw DART 응답을 canonical CSV + manifest로 정리하는 helper가 추가되어,
  로컬 수집 결과를 strict 입력으로 빠르게 패키징할 수 있습니다.
- OpenDART raw response를 raw bytes와 sanitized manifest로 저장하는 fetch helper도
  추가되어, 실제 수집을 시작할 수 있는 최종 plumbing이 마련되었습니다.
- fetch helper는 batch mode도 지원하여, historical corp/date 조합을 반복 수집할 수
  있습니다.
- corp_code/연도 범위로 batch spec을 자동 생성하는 helper가 추가되어, 반복 수집
  spec 작성이 수동 작업 없이 가능합니다.
- batch spec 생성기는 ticker 목록과 corp map을 받아 corp_code를 자동 유도할 수
  있어, 수집 대상 정합성 확보가 쉬워졌습니다.
- batch 수집 결과를 merged filing/facts CSV + manifest로 통합하는 helper가 추가되어,
  strict LOCAL_DART 입력으로 바로 연결할 수 있습니다.
- batch fetch는 청크 실행과 실패 복구를 지원하여, 대규모 historical 수집을
  중단 지점부터 재개할 수 있습니다.
- 로컬 PIT 섹터 맵 계약 스캐폴딩이 `src/k200_mq/data/sector_pit.py`에 구현되어
  있으며, 준비 경로에서 as-of 섹터 스냅샷으로 연결됩니다.
- 선택적 `LOCAL_PIT_SECTOR_PATH`가 준비 경로(`prepare_k200mq_inputs`)에 연결되어,
  섹터 맵 검증을 통과한 경우 as-of별 ticker→sector 스냅샷과 커버리지 메타데이터를
  준비 입력/매니페스트 컨텍스트에 기록합니다. `ENABLE_SECTOR_CAP=True`일 때는
  로컬 PIT 섹터 맵의 검증 및 전체 커버리지를 요구하며, 조건 미충족 시 즉시 중단
  (fail closed)합니다.
- `ENABLE_CORRELATION_FILTER=True`일 때는 엔진이 리밸런싱 신호일까지의 close
  수익률 이력으로 pairwise 상관계수를 계산하고, `MAX_PAIR_CORRELATION` 및
  `CORRELATION_LOOKBACK_DAYS`를 사용해 고상관 페어를 greedy 방식으로 제한합니다.
  선택된 후보 쌍의 상관계수 커버리지가 불완전하면 즉시 중단(fail closed)합니다.
- `ENABLE_ADV_FILTER=True`일 때는 엔진이 리밸런싱 신호일까지의 trailing ADV
  turnover(`volume*close/mcap`) 평균을 계산하고, `MIN_ADV_RATIO` 및
  `ADV_LOOKBACK_DAYS`를 사용해 저유동성 후보를 제외합니다. 선택 후보의 ADV
  turnover 커버리지가 불완전하면 즉시 중단(fail closed)합니다.

## 기계적 비-PIT 진단

현재 진단 분류는 `mechanical_expanding_walk_forward_non_pit`입니다. 구간 자르기는
미래 행을 막는 기계적 보호일 뿐입니다. 어떤 종목이 역사적 KOSPI 200 구성원이었는지
또는 재무 정보가 신호일 전에 공개되었는지는 증명하지 않습니다.

기본 유니버스는 `proxy_current` 또는 `mcap_proxy` 동작을 사용합니다. 정규화 재무
입력은 회계기간 데이터를 사용하거나 DART를 사용할 수 없을 때 비어 있습니다. 두
경로 모두 필요한 역사적 PIT 근거를 제공하지 않습니다. 명시적 로컬 KRX 원천을
설정한 실행은 proxy 기본 경로와 별개로 즉시 중단(fail closed) 검증을 적용하지만, 현재 공식
진단에는 그 역사 입력이 사용되지 않았습니다.

## 현재 v4 no-DART true-WF 진단

실행 명령:

```bash
DART_API_KEY="" uv run python -m k200_mq.main true-walkforward \
  --output /tmp/k200mq_true_wf_v4_no_dart
```

실행일: 2026-08-03

- 공식: `k200mq-momentum-skipped-return-v4`, 기본
  `close[t-42] / close[t-252] - 1`.
- 분류: `mechanical_expanding_walk_forward_non_pit`.
- DART가 설정되지 않아 품질 팩터를 비활성화했습니다. 이는 **모멘텀 전용 기계적
  진단**이며 Momentum + Quality 결과가 아닙니다.
- 5개 폴드가 모두 유효했습니다.
- OOS 범위: 2020-2024 테스트 구간에 걸쳐 1,231개 지점.
- 연결 누적 수익률: **+4.0408%**.
- 연결 최대 낙폭: **-32.0408%**.
- 폴드 테스트 수익률: 2020 **+27.1147%**, 2021 **-16.2567%**, 2022
  **-5.5386%**, 2023 **-0.4543%**, 2024 **+3.9396%**.

제한 사항:

- KOSPI 200 구성원과 횡단면 순위는 비-PIT proxy 입력에 기반합니다.
- 이 실행에서는 DART 재무 데이터를 사용할 수 없었고, 일반 재무 경로도 기본적으로
  비-PIT입니다. 새 로컬 importer는 이 동작을 바꾸지 않으며, 검증된 역사적 DART
  응답 파일이 파이프라인에 연결되어 있지 않습니다.
- 구현된 벤치마크는 KPI200 가격수익률이며 총수익률이 아닙니다.
- ADV 영향, 섹터 한도, PIT 민감도, 생존자 편향 비교, 계획된 스트레스 테스트는
  완료되지 않았습니다.

이 결과는 v4 공식 기준으로 현재의 진단이지만 검증된 성과 근거가 아니며, 정식/운영
성과 주장으로 승격해서는 안 됩니다. 임시 출력은 저장소에 복사하거나 커밋하지
않습니다. 생성되는 파일은 다음과 같습니다.

```text
/tmp/k200mq_true_wf_v4_no_dart/true_walkforward/
  selection_and_folds.json
  summary.csv
  oos_returns.csv
```

## 검증된 PIT 근거

**검증된 PIT 성과 근거는 아직 없습니다.** Day 14 strict 실행(PIT 유니버스 +
FY2014 병합 DART)은 strict preflight(유니버스 전 as-of `pit_valid=true` + financial
`pit_filing_date`)를 통과하고 5/5 폴드 valid를 달성했지만, 분류는 여전히
`mechanical_expanding_walk_forward_non_pit`으로 유지된다.

현재 `true-walkforward` 경로는 `validated_expanding_walk_forward_pit`로 표시할 수
없습니다. 승격 차단 (2026-08-16 Oracle 검토로 확정):
- **하드코딩**: `main.py:3117` (`run_walk_forward(classification=MECHANICAL...)`),
  `main.py:2885,2986` (매니페스트 classification/claim 덮어씀), `main.py:3145`
  (로그 문구), `walk_forward.py:39-49` (`classify_walk_forward_result` 무조건
  MECHANICAL), `walk_forward.py:579-582` + `runner.py:781-786,837-841,1207`
  (deferral guard, `pit_valid_context=None` 고정).
- **증거 프록시 2건** (승격 전 반드시 해소):
  1. `filing_date_used`가 엔진 소비 증명이 아닌 자기참조 프록시
     (`prepared.py:121`, `main.py:919,1029-1031` — `has_usable_filing_dates(data)`).
  2. quality 6-fact 부분 커버리지(`partial_allowed_fill_missing_with_zero`)가
     PIT 유효성에 미포함 — "validated PIT"가 완전 커버리지를 함의하면 과장.
- **이미 wiring된 validator**: `_validate_prepared_pit_provenance`
  (`validation/prepared.py:81-134`)가 strict preflight(`main.py:3057-3061`)과
  strict interval마다(`prepared.py:618-620`) 유니버스/재무 검증 실행 — Day 14에서
  모두 통과. 승격은 이 validator 결과를 adapter에서 classification 문자열로
  승격하는 배선만 남음.
- PIT 민감도(proxy/PIT), 생존자 편향 비교(Day 14), ADV 영향(proxy 성공/PIT
  실행 불가), 스트레스 테스트(proxy/PIT)는 Day 11-17에서 기계적 진단으로 실행됨.

strict 모드 `true-walkforward`는 더 이상 옵션 자체를 사전 거부하지 않습니다.
대신 실행 전 prepared 입력의 유니버스/재무 provenance를 strict preflight로
검증하며, validator-backed PIT 계약이 불충분하면 즉시 중단(fail closed)합니다.
현재 local DART session-bounded pilot에서는 재무 provenance preflight가
`non_pit_fiscal_period`에서 벗어났고, 2026-08-04 strict true-walkforward 실행은
산출물 저장까지 완료되었습니다(분류는 여전히
`mechanical_expanding_walk_forward_non_pit`). 남은 병목은 유니버스 경계와
역사 범위 확대입니다.

구성원 importer는 구조적 후보 계약을 정의합니다: `index_code`, `as_of_date` 또는
`effective_date`, `security_code`. 원천 메타데이터는 존재할 때 정규화하지만 원시
행의 값을 그대로 신뢰하지 않습니다. 명시적 구간 파일에는 유효일 경계,
action/status, 공표일, provenance도 포함됩니다. 승격에는 별도 수집 매니페스트
사이드카가 필요하며 공식 HTTPS KRX URL, 질의/날짜 파라미터, 시간대가 있는 수집
시각, 허용된 KRX 원천 유형, 명시적 KRX 확인, 원시 바이트와 일치하는 SHA-256을
담아야 합니다. 로컬 경로, `file://`, 파일 수정 시각, 내장 해시, DataFrame,
임의의 PIT 플래그는 검증되지 않은 상태로 남습니다.

현재 KRX 어댑터는 두 날짜의 라이브 스모크 테스트를 통과했지만 원시 파일은 로컬·
미커밋 상태이며, 충분한 역사 범위의 자료를 운영 성과 근거에 연결하지 않았습니다.
명시적 로컬 원천 통합은 이미 `config`·`universe`·`main`에 존재하고 proxy 기본값을
보존합니다. 따라서 이 구현은 연결 경로를 제공하지만 현재 결과를 검증된 근거로
승격하거나 공식 진단을 변경하지 않습니다. 자동화된 테스트 모음에서는 라이브 API를
호출하지 않습니다.

## 폐기/감사 전용 v4 이전 결과

v4 모멘텀 의미 교정 이전에 생성된 모든 결과는 `obsolete_pre_momentum_v4`로
분류하며 현재 결과와 비교하지 않습니다. 이전에 보고된 +207.06%, +14.17%,
+26.97%, +44.6426%, +245-era 수치는 감사 이력으로만 보존합니다. 일부 결과에는
현금 전파 수정 전의 실행도 포함되어 있습니다. 어느 것도 현재 근거, 검증된 결과,
파라미터 선택의 기반이 아닙니다.

## PIT 게이트와 다음 우선순위

현재 다음 우선순위는 다음과 같습니다.

1. ~~bundle-directory 유니버스의 198 구성원 역사 날짜를 documented transition exception
  또는 원천 보정으로 정리해 strict preflight를 통과시킵니다.~~
  **해소됨 (2026-08-06)**: `bundle.manifest.json`의 `transition_exceptions_by_as_of`가
  120개 월말 날짜(2015-01-30 ~ 2024-12-31) 모두에 `allowed_sizes: [198]`로 이미 고정되어 있고,
  `pit_universe.py`가 이를 로드해 검증에 반영한다.
2. ~~원시 DART 제출/공시 메타데이터와 재무 사실을 확보하고, 제출일을 안전한 거래 세션
   가용일로 매핑하여 회계기간 날짜로 대체하지 않습니다.~~
   **부분 해소 (2026-08-10)**: `data/raw/dart_aggregated_day4_extended/` aggregate로
   financial provenance가 `pit_filing_date` + `pit_valid=true` 승격됨. 첫-ready
   momentum missing 51은 재무/품질 coverage가 아니며, FY2014 XBRL 보강은 재무 PIT facts를
   개선하지만 momentum warmup을 해소하지는 않는다.
3. ~~두 입력의 PIT provenance가 실제 자료에서 확인된 뒤 strict PIT WF를 다시 실행합니다.~~
   **해소 (2026-08-10)**: Day 8 strict WF 실행, 5/5 valid, `outputs_k200mq_day8_strict_extended/`.
4. ~~strict PIT WF가 통과한 뒤에만 PIT 민감도, 생존자 편향 비교, ADV 영향 및 유동성
   제약, 계획된 스트레스 테스트를 실행합니다.~~
   **실행됨 (2026-08-15~16, 기계적 진단)**: proxy(Day 11-13)와 PIT 유니버스
   (Day 15-17) 기준으로 실행. 결과: 모멘텀 가중 0.7/0.3 개선 신호(양쪽 공통),
   손절 MDD 방어 유효(양쪽 공통), ADV 필터는 proxy에서 성과 악화 / PIT에서
   실행 불가(상장폐지 mcap=0 fail-closed), 생존자 편향 정량화(Day 14: proxy
   ~46% 상이, stitched -23.6%p).
5. **classification 승격 (다음 게이트)**: 전제조건 2건 해소 — ① `filing_date_used`
   엔진 소비 하드 증명, ② quality 6-fact 커버리지 게이트/명시 한계 — 후
   adapter(`main.py:3094-3124`)에서 실제 validator 결과로 classification 결정,
   deferral guard(`walk_forward.py:579`, `runner.py:781,837`) 제거,
   `pit_valid_context` 스레딩, 매니페스트/로그 하드코딩 제거.

이 단계가 완료될 때까지 출력 수치는 기계적 진단으로만 취급합니다. (Day 14 실행은 strict
preflight(유니버스 전 as-of `pit_valid=true` + financial `pit_filing_date`)를 통과하고
5/5 valid를 달성했으나 `validated_expanding_walk_forward_pit` 승격은 위 전제조건 2건과
별도 검증 게이트를 충족한 뒤 adapter 변경으로 결정됩니다.)

### OpenDART 로컬 계약과 다음 단계

로컬 DART 계층은 다음 OpenDART 응답 계약을 기준으로 설계되어 있습니다.

- 제출 목록: `https://opendart.fss.or.kr/api/list.json`;
- 재무 사실: `https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json`;
- 원시 제출 행은 `corp_code`, `rcept_no`, 날짜 전용 `rcept_dt` (`YYYYMMDD`)를
  보존하고, 사실 행은 동일한 접수 번호와 보고서 키를 보존합니다.
- 다운로드한 각 응답에는 응답 SHA-256과 시간대가 있는 `retrieved_at_utc`를 담은
  별도 JSON 매니페스트가 필요합니다. importer는 원시 바이트와 다이제스트를
  대조하며 DataFrame이나 내장 해시를 수집 근거로 취급하지 않습니다.
- 날짜 전용 제출은 `next_session`을 사용합니다. 즉 Asia/Seoul 현지 달력의 공식
  접수일보다 엄격히 뒤인 첫 제공 KRX 거래일을 사용합니다. 세션 마감 시각은 검증된
  원시 제출 매니페스트와 importer가 발행한 정규화 프레임 fingerprint에 시각 계보가
  포함될 때까지 보류합니다. 조인 후 시각을 주입하는 방식은 거부하며 철회 제출과
  미지정 정정 정책도 거부합니다.

다음 단계는 OpenDART API/벌크 다운로드를 이 원시 로컬 파일과 매니페스트에 연결하는
것입니다. 원시 로컬 파일 → 품질 팩터 소비 경로는 공용 계정 매핑과 long→wide pivot으로
이미 연결되어 있습니다(2026-08-06). 수집 어댑터와 역사 파일이 준비될 때까지 현재
품질 동작과 모든 비-PIT 진단은 변하지 않습니다.

## 보류 또는 미지원 설정

다음 설정은 호환성 필드 또는 향후 작업으로 남아 있으며 현재 민감도 차원이 아닙니다:
`UNIVERSE_SIZE`, `USE_52WEEK_HIGH`,
`QUALITY_MIN_TTM_QUARTERS`, 그리고
`EXCLUDE_MANAGEMENT`, `EXCLUDE_INVESTMENT_NOTICE`, `EXCLUDE_PREFERRED`,
`EXCLUDE_ETF_ETN` 제외 플래그. `MOMENTUM_WINDOW_SHORT`는
진단 전용입니다.

ADV 기반 시장 영향 실행은 연결되지 않았습니다. `--no-cache`와
`--rebalance-lookback`은 명시적으로 미지원/보류이며 거부됩니다. `SECTOR_CAP`은 `ENABLE_SECTOR_CAP=True`와
검증된 `LOCAL_PIT_SECTOR_PATH`의 전체 커버리지 조건에서만 활성화됩니다.
ADV 유동성 필터는 `ENABLE_ADV_FILTER=True`일 때만 활성화됩니다.
상관관계 제약은 `ENABLE_CORRELATION_FILTER=True`일 때만 활성화됩니다. 손절 플래그는 `run` 전용이고
`true-walkforward`는 구성/기본값을 사용합니다.

## 출력 산출물

`run`은 `portfolio_snapshots.csv`, `trade_log.csv`, `daily_returns.csv`,
`metrics.json`, `benchmark_returns.csv`, `run_manifest.json`을 기록할 수 있습니다.
`robustness`는 `subperiod_robustness_summary.csv`를 기록합니다.
`true-walkforward`는 `true_walkforward/selection_and_folds.json`, `summary.csv`,
`oos_returns.csv`를 기록합니다. 이 산출물에는 진단/provenance 정보가 포함되지만,
비-PIT 자료를 검증된 근거로 바꾸지는 않습니다.
