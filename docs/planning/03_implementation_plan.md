# 구현 계획 - KOSPI 200 모멘텀 + 품질

2026-08-04 기준. 이 계획은 전략이 검증된 성과를 냈다는 주장이 아니라 현재 구현
범위를 기록합니다.

범례: `[x]` 현재 코드/테스트로 구현 및 확인됨, `[ ]` 보류·연기되었거나 PIT 성과
주장에 아직 유효하지 않음을 뜻합니다.

## 0단계: 준비

- [x] 레거시 전략을 `v2.0-abandoned` 태그로 동결.
- [x] `src/k200_mq/` 패키지와 재사용 가능한 핵심 모듈 생성.
- [x] `K200MQConfig`와 모듈 기반 CLI 추가.
- [x] 저장소 제외 규칙 및 문서 지원 파일 갱신.

## 1단계: 데이터와 유니버스

- [x] 기준일(as-of) 키 기반 proxy 유니버스 로더와 캐시 구현. 현재 `proxy_current`와
  `mcap_proxy` 원천은 명시적으로 비-PIT입니다.
- [x] 모멘텀 룩백 및 워밍업을 지원하는 가격 로딩 구현.
- [x] KPI200 및 KS11 시장 지수 입력 구현.
- [x] ADV 계산 도우미 구현. 실행 비용 연결은 보류 상태입니다.
- [x] 로컬 PIT 후보 파일 importer와 다중 스냅샷/날짜/해시/타임스탬프/재로딩
  검증 계약 구현.
- [x] 인증된 KRX 어댑터를 `src/k200_mq/data/krx_pit.py`에 구현하고 두 날짜의
  라이브 스모크 테스트 완료. 원시 파일과 매니페스트는 로컬·미커밋 산출물입니다.
- [x] `config`·`universe`·`main`을 통한 명시적 로컬 KRX PIT 원천 연결 구현.
  설정하지 않으면 proxy 기본 동작을 유지하고, 설정된 원천의 검증이 실패하면
  즉시 중단(fail closed)합니다.
- [x] 유효일이 포함된 KOSPI 200 역사 파일을 bundle-directory 경로로 검증 가능하게
  연결하고, strict preflight가 유니버스 provenance와 per-date manifest identity를
  fail closed로 검증하도록 구현.
- [ ] 일부 역사 날짜의 200 미만 구성원은 documented transition exception 또는
  원천 보정으로만 허용된다. 현재 strict probe에서는 2015-01-30이 198 구성원으로
  남아 있어 다음 게이트다.
- [x] 로컬 DART filing metadata와 financial facts를 sidecar manifest와 함께
  strict 준비 경로에 연결하여, filing-date provenance가 API 키 없이도 검증되도록
  구현.
- [x] raw DART response를 canonical CSV + manifest로 패키징하는 helper
  (`scripts/build_local_dart_bundle.py`)를 추가하여 실제 수집물을 strict 입력으로
  정리할 수 있게 구현.
- [x] OpenDART raw response를 파일+매니페스트로 저장하는 fetch helper
  (`scripts/fetch_local_dart_response.py`)를 추가하여 실제 수집을 시작할 수 있게
  구현.
- [x] fetch helper의 batch mode를 추가하여 historical corp/date 조합을 반복 수집할
  수 있게 구현.
- [x] corp_code/연도 범위에서 batch fetch spec(JSON)을 생성하는 helper
  (`scripts/generate_dart_fetch_batch_spec.py`)를 추가하여 반복 수집 spec 작성을
  자동화.
- [x] batch spec 생성기에서 ticker 목록 + corp map 입력으로 corp_code를 자동
  유도하는 옵션을 추가해, 수집 대상 작성의 수작업 부담을 감소.
- [x] batch 수집된 raw DART 응답을 merged filing/facts CSV + manifest로 통합하는
  helper (`scripts/build_local_dart_aggregates.py`)를 추가하여 strict 입력 연결을
  자동화.
- [x] batch fetch에 청크 실행 및 실패 복구 옵션(`--start-index`,
  `--max-requests`, `--continue-on-error`)을 추가하여 장기 historical 수집의
  재시도 비용을 낮춤.
- [ ] 원시 DART 제출/공시 메타데이터의 역사 범위를 더 넓혀 실제 자료에서 PIT
  유니버스 및 제출일(filing-date) provenance를 검증 완료.
- [ ] ADV 기반 시장 영향 모델 구현.

## 2단계: 팩터

- [x] 버전이 명시된 v4 skipped-return 모멘텀 팩터 구현:
  기본 설정에서 `close[t-42] / close[t-252] - 1`.
- [x] 정규화 입력 품질 구성요소(ROE, debt/equity, operating margin,
  cash conversion)와 횡단면 점수화 구현.
- [x] KPI200 이동평균 및 20거래일 수익률 조건을 사용하는 regime 팩터 구현.
- [x] 팩터 준비/병합 및 readiness/warmup 처리 구현.
- [x] 팩터 단위 테스트 및 회귀 테스트 추가.
- [x] 로컬 OpenDART 제출일 재무 provenance 계약 구현. 원시 API/벌크 수집과
  현재 품질 팩터 기본 경로 연결은 아직 하지 않았습니다.
- [ ] 운영용 DART 계정 매핑 테이블 및 PIT TTM 분기 필터 추가.
- [ ] 원시 DART API/벌크 수집을 연결하고 품질 팩터가 제출일(filing-date) 입력을 사용하도록
  연결.

## 3단계: 전략과 포트폴리오 엔진

- [x] 횡단면 모멘텀/품질 순위와 TOP-N 선택 구현.
- [x] 포트폴리오 리밸런싱 루프, regime 비중 조절, 손절, 다음 세션 실행 구현.
- [x] 현금 및 목표 비중 변경을 엔진 전체에 올바르게 전파.
- [x] 체결에 설정된 명시적 수수료, 매도세, 슬리피지 적용.
- [x] 전략·엔진·통합 테스트 추가.
- [x] `MAX_HOLDINGS` 동시 보유 상한 및 `MIN_CASH_RATIO` 최소 현금 버퍼를
  전략/엔진에 반영.
- [x] 향후 `SECTOR_CAP` 연결을 위한 로컬 PIT 섹터 맵 계약 스캐폴딩
  (`src/k200_mq/data/sector_pit.py`)과 검증 테스트 추가.
- [x] 선택적 `LOCAL_PIT_SECTOR_PATH`를 통해 로컬 PIT 섹터 맵을 준비 경로에
  연결하고, as-of 스냅샷/커버리지 메타데이터를 준비 입력과 매니페스트에 기록.
- [x] `ENABLE_SECTOR_CAP=True` + 검증된 로컬 PIT 섹터 맵 조건에서 섹터 한도를
  전략/엔진에 조건부 적용하고, 섹터 맵 부재/미커버리지는 즉시 중단(fail closed).
- [x] `ENABLE_CORRELATION_FILTER=True`에서 trailing close 수익률 기반
  pairwise 상관계수(`MAX_PAIR_CORRELATION`, `CORRELATION_LOOKBACK_DAYS`)를
  사용해 고상관 페어를 greedy 방식으로 제한.

## 4단계: 통합과 검증

### CLI와 진단

- [x] `uv run python -m k200_mq.main`을 통한 `run`, `robustness`,
  `true-walkforward` 구현.
- [x] `robustness`를 walk-forward CV가 아닌 독립 하위 기간 테스트로 유지.
- [x] 학습 전용 후보 선택, 2회 분리 실행, 구간 자르기, 준비 데이터 날짜의 정확한
  포함 범위 확인을 갖춘 expanding-window WF 핵심부 구현.
- [x] WF 선택/폴드, 요약, OOS, config/hash, git, preparation 맥락 산출물 저장.
- [x] 유니버스/재무 provenance 계약과 엄격한 검증 보호 장치를 즉시 중단(fail closed)
  방식으로 구현.
- [x] 실제 역사 유니버스와 제출일(filing-date) 검증기를 사용한 strict PIT WF 실행.
  (2026-08-10, `outputs_k200mq_day8_strict_extended/`: 5/5 folds valid, financial
  provenance `pit_filing_date`, strict preflight 실패 0건.) 단 분류는
  `mechanical_expanding_walk_forward_non_pit` 유지 — 커버리지 갭(첫 리밸런스 missing
  51 티커) 종결 후 `validated_expanding_walk_forward_pit` 승격 및 재실행 필요.

### 벤치마크, 비용 및 의미 안전성

- [x] KPI200 종가 기반 가격수익률 벤치마크 구현. 총수익률 벤치마크가 아니며
  배당/분배금을 제외합니다.
- [x] 실제 체결 거래에서 비용을 귀속하고 실행 통계 및 포트폴리오 스냅샷과
  조정·일치.
- [x] v4 모멘텀 공식 교정, 명시적 품질 가중치, regime 수익률 기준, 손절 도메인
  및 관련 의미 안전성 수정 구현 및 테스트.
- [x] 벤치마크, 체결 비용, provenance 계약, WF 오케스트레이션, 의미 수정에 대한
  회귀 테스트 추가.
- [ ] PIT 데이터 게이트 통과 후 PIT 파라미터 민감도 실행.
- [ ] 계획된 스트레스 테스트 시나리오 실행.
- [ ] 현재 구성원과 역사 구성원 사이의 생존자 편향 비교 완료.

## 5단계: 문서화와 릴리스

- [x] `README.md`, `README_K200MQ.md`, `AGENTS.md`, 계획 문서를 갱신하여
  진단과 근거를 구분.
- [x] 구현된 인프라와 검증 보호 장치에 대한 테스트 유지.
- [ ] PIT 입력을 사용한 전략 검토.
- [ ] PIT 검증 및 검토 완료 후 릴리스 태그 생성.

## 현재 보류 또는 미지원 설정

다음 설정은 호환성 필드 또는 향후 작업으로 남아 있으며, 테스트한 민감도 차원으로
제시해서는 안 됩니다: `UNIVERSE_SIZE`,
`USE_52WEEK_HIGH`, `QUALITY_MIN_TTM_QUARTERS`, 그리고 `EXCLUDE_MANAGEMENT`,
`EXCLUDE_INVESTMENT_NOTICE`, `EXCLUDE_PREFERRED`, `EXCLUDE_ETF_ETN` 제외 플래그.
`MOMENTUM_WINDOW_SHORT`는 진단 전용입니다. `--no-cache`와
`--rebalance-lookback`은 미지원/보류 항목으로 명시적으로 거부되며, 손절 CLI
플래그는 `run`에만 있고 `true-walkforward`에는 없습니다. `SECTOR_CAP`은
`ENABLE_SECTOR_CAP=True`와 `LOCAL_PIT_SECTOR_PATH`의 검증/전체 커버리지 조건에서만
실행됩니다. ADV 유동성 필터는 `ENABLE_ADV_FILTER=True`일 때만 활성화되며,
리밸런싱 신호일까지의 trailing ADV turnover(`volume*close/mcap`) 평균이
`MIN_ADV_RATIO` 이상인 후보만 유지합니다. 상관관계 제약은
`ENABLE_CORRELATION_FILTER=True`일 때만 활성화되며, 리밸런싱 신호일까지의 close
수익률 이력으로 계산된 pairwise 상관계수를 사용합니다.

## 남은 작업 / 다음 단계

현재 구현의 다음 게이트는 strict bundle 유니버스 경로를 통과한 뒤 DART 제출일
provenance를 실제 자료로 연결하는 것입니다. KRX 인증 어댑터의 두 날짜 라이브 스모크
테스트는 연결 계약의 확인이지, 역사적 유니버스에 대한 성과 근거가 아닙니다.
원시 파일과 매니페스트는 로컬에서만 관리하고 커밋하지 않습니다.

1. 현재 bundle-directory strict 유니버스 경로의 documented transition exception 또는
  원천 보정을 마무리해, `true-walkforward` strict preflight가 2015-01-30 같은 역사
  예외를 명시적으로 수용하도록 정리합니다.
2. 원시 DART 제출/공시(filing/publication) 메타데이터와 재무 사실을 API 또는 벌크 수집으로
   확보하고, 기존 로컬 OpenDART 계약 및 `(corp_code, rcept_no)` 조인에 맞춰
   품질 팩터에 연결합니다.
3. 위 입력의 provenance가 실제 자료에서 확인된 뒤 `strict PIT WF`를 실행합니다.
4. strict PIT WF가 통과한 동일 게이트에서 PIT 민감도, 생존자 편향 비교, ADV 기반
   시장 영향, 계획된 스트레스 테스트를 순서대로 실행합니다.
5. 결과 검토가 끝난 뒤에만 전략 검토, 릴리스 태그, 검증된 성과 해석을 수행합니다.

## 검증된 근거까지의 핵심 경로

```text
역사적 PIT 유니버스
  -> 제출일 기반 재무 데이터
  -> strict PIT WF
  -> PIT 민감도 및 스트레스 테스트
  -> 생존자 편향 비교 및 ADV 영향 검토
  -> 검토 및 릴리스
```

PIT 자료 게이트가 완료되기 전까지 모든 성과 출력은 기계적 비-PIT 진단으로만
취급합니다.

## 계속/중단/피벗 결정 규칙 (Go/No-Go)

이 프로젝트의 현재 단계는 알고리즘 튜닝이 아니라 "검증 가능한 성과 근거"를
확보하는 단계입니다. 따라서 진행 여부는 아래 게이트를 숫자로 판단합니다.

### 피벗의 의미

- 여기서 피벗은 구현을 포기한다는 뜻이 아니라, 현재 가설(KOSPI 200 Momentum +
  Quality)의 실전 가능성이 낮다고 판단될 때, 다른 전략 가설(예: 유니버스/팩터/
  보유기간/리스크 구조)로 연구 축을 이동하는 것을 의미합니다.

### 타임박스

- 1차 판단 기한: 2~3주.
- 기한 내 아래 "필수 게이트"를 충족하지 못하면, 현재 전략은 중단 후보로 분류하고
  피벗 옵션을 우선 검토합니다.

### 필수 게이트 (모두 충족 필요)

1. 데이터/검증 게이트
   - strict 경로 재현 실행이 가능하고, strict preflight 실패가 없어야 함.
   - 유니버스 historical 예외(예: 198 constituent 날짜)가 문서화된 전이 예외 또는
     원천 보정으로 정리되어야 함.
   - DART filing-date 기반 입력이 목표 기간에서 최소 커버리지 기준을 충족해야 함.
2. 성과 게이트 (비용 반영 후 OOS)
   - OOS CAGR > 0.
   - OOS MDD가 사전 정의 상한 이내.
   - 벤치마크 대비 위험조정 성과(예: Sharpe 또는 Calmar)가 최소 기준 이상.
3. 안정성 게이트
   - 핵심 파라미터 소폭 변화 시 성과 붕괴가 없어야 함.
   - 서브기간별 성과가 특정 1개 구간에 과도하게 집중되지 않아야 함.

### 권장 초기 컷오프 (보수적)

- OOS CAGR: 연 5% 이상.
- OOS MDD: -25% 이내.
- Sharpe: 0.7 이상.
- Calmar: 0.3 이상.

위 컷오프는 "연구 지속 여부" 판단선이며, 실거래 투입선이 아닙니다.

### 의사결정 규칙

- Continue: 필수 게이트 모두 충족 + 컷오프 충족.
- Hold: 필수 게이트는 충족했지만 컷오프 미달.
- Pivot: 필수 게이트 미충족 또는 게이트는 충족했으나 반복 실험에서 안정성 실패.

### 실행 템플릿

- 2주 실행 체크리스트: [docs/planning/07_two_week_execution_checklist.md](docs/planning/07_two_week_execution_checklist.md)
- Go/No-Go 판정표 템플릿(문서): [docs/planning/08_go_no_go_scorecard_template.md](docs/planning/08_go_no_go_scorecard_template.md)
- Go/No-Go 판정표 템플릿(CSV): [docs/planning/08_go_no_go_scorecard_template.csv](docs/planning/08_go_no_go_scorecard_template.csv)
