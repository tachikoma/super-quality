# 2주 실행 체크리스트 (Strict PIT Gate)

목적: 2주 안에 Continue/Hold/Pivot 판단에 필요한 최소 근거를 확보한다.

## 운영 원칙

- 원칙 1: strict preflight 실패는 원인/증거/수정 커밋까지 같은 날 닫는다.
- 원칙 2: 수익률 숫자보다 provenance 완결성을 우선한다.
- 원칙 3: 매일 종료 전 scorecard를 갱신한다.

## 주차별 목표

### Week 1 (데이터/검증 게이트 닫기)

- Day 1
  - strict true-walkforward 1회 실행.
  - 실패/성공 로그를 저장하고 원인 분류(유니버스, DART, 엔진) 기록.
- Day 2
  - 198 constituent 날짜 예외 목록 고정.
  - documented transition exception 초안 작성.
- Day 3
  - DART filing/facts 커버리지 점검(기간/종목/결측 비율).
  - 최소 커버리지 임계치 제안값 확정.
- Day 4
  - 수집 부족 구간 보강(fetch batch 재실행 + aggregate).
  - sidecar manifest 검증 통과 여부 확인.
- Day 5
  - strict true-walkforward 재실행.
  - preflight 실패 0건 목표.

### Week 2 (성과/안정성 게이트 판정)

- Day 6
  - 최신 strict 실행 산출물에서 OOS 지표 산출(CAGR, MDD, Sharpe, Calmar).
- Day 7
  - 후보 파라미터 소폭 변형 실험 1차(예: TOP_N, weight 비율).
- Day 8
  - 후보 파라미터 소폭 변형 실험 2차(예: hold/stop/regime 토글).
- Day 9
  - 서브기간 안정성 점검(구간 편중 여부).
- Day 10
  - Continue/Hold/Pivot 최종 판정.
  - 판정 근거를 문서/커밋/산출물 경로와 함께 고정.

## 일일 점검 항목

- strict preflight 실패 건수 = 0 인가?
- 유니버스 예외가 문서화/보정되었는가?
- DART filing-date 입력 커버리지가 기준 이상인가?
- 오늘 생성한 산출물 경로가 명확한가?
- scorecard가 최신으로 갱신되었는가?

## 완료 기준 (2주 종료 시)

- 필수 게이트 3개(데이터/검증, 성과, 안정성) 상태가 모두 판정됨.
- Go/No-Go scorecard가 숫자와 근거 경로를 포함해 완결됨.
- 최종 의사결정(Continue/Hold/Pivot)이 문서화됨.

## Day 1 실행 기록 (2026-08-04)

- 실행 1 (strict + mixed DART aggregate)
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_pilot_mixed/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mixed/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mixed/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mixed/dart_facts_merged.manifest.json --output outputs_k200mq_day1_20260804`
  - 결과: invalid
  - 핵심 원인: local DART facts/filings 조인 키 무결성 실패로 strict financial availability 확정 불가.
  - 근거: outputs_k200mq_day1_20260804/true_walkforward/summary.csv

- 실행 2 (strict + mapped DART aggregate)
  - 명령:
    - `uv run python -m k200_mq.main true-walkforward --strict-pit --exclude-kospi-top-n 0 --local-pit-universe-path data/universe/kospi200_bundle_strict --local-pit-universe-source-kind snapshots --local-pit-universe-manifest data/universe/kospi200_bundle_strict/bundle.manifest.json --local-dart-filing-path data/raw/dart_aggregated_pilot_mapped/dart_filings_merged.csv --local-dart-filing-manifest data/raw/dart_aggregated_pilot_mapped/dart_filings_merged.manifest.json --local-dart-financial-path data/raw/dart_aggregated_pilot_mapped/dart_facts_merged.csv --local-dart-financial-manifest data/raw/dart_aggregated_pilot_mapped/dart_facts_merged.manifest.json --output outputs_k200mq_day1_20260804_mapped`
  - 결과: invalid
  - 핵심 원인: financial facts 중복/누락 조인으로 strict financial availability 확정 불가.
  - 근거: outputs_k200mq_day1_20260804_mapped/true_walkforward/summary.csv

- Day 1 판정
  - 상태: 실패 분류 완료 (유니버스 통과, DART 조인 무결성 실패)
  - 다음 액션: Day 2에서 DART duplicate/missing join 정리 우선 수행.
