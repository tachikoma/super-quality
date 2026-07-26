"""KOSPI 200 Momentum + Quality 백테스트용 CLI 진입점.

사용법:
    uv run python -m k200_mq.main --help
    uv run python -m k200_mq.main run --dart-api-key=... \
        --start 2015-01-01 --end 2024-12-31 --output ./outputs
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """stderr에 간단한 형식으로 로깅을 설정합니다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _parse_date(s: str) -> date | None:
    """YYYY-MM-DD 문자열을 date 객체로 파싱합니다."""
    if s == "today":
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 구성합니다."""
    parser = argparse.ArgumentParser(
        prog="k200-mq",
        description="KOSPI 200 Momentum + Quality — 한국형 모멘텀+품질 백테스팅 시스템",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="전체 백테스트 파이프라인 실행")
    run_parser.add_argument(
        "--dart-api-key",
        default="",
        help="OpenDartReader API 키",
    )
    run_parser.add_argument(
        "--start",
        default="2015-01-01",
        help="시작일 YYYY-MM-DD (기본값: 2015-01-01)",
    )
    run_parser.add_argument(
        "--end",
        default=None,
        help="종료일 YYYY-MM-DD (기본값: 오늘)",
    )
    run_parser.add_argument(
        "--output",
        "-o",
        default="outputs_k200mq",
        help="보고서 출력 디렉토리 (기본값: outputs_k200mq)",
    )
    run_parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="팩터 캐시를 건너뛰고 재계산",
    )
    run_parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="선택 종목 수 (기본 20)",
    )
    run_parser.add_argument(
        "--rebalance-freq",
        default="M",
        help="리밸런싱 주기: M(월간) 또는 Q(분기)",
    )
    run_parser.add_argument(
        "--rebalance-lookback",
        type=int,
        default=252,
        help="모멘텀 계산용 선행 일수 (기본 252)",
    )
    run_parser.add_argument(
        "--weight-momentum",
        type=float,
        default=0.50,
        help="모멘텀 팩터 가중치 (기본 0.50)",
    )
    run_parser.add_argument(
        "--weight-quality",
        type=float,
        default=0.50,
        help="품질 팩터 가중치 (기본 0.50)",
    )
    run_parser.add_argument(
        "--exclude-kospi-top-n",
        type=int,
        default=50,
        help="모멘텀에서 제외할 KOSPI 상위 N개 (기본 50)",
    )
    run_parser.add_argument(
        "--stop-loss",
        type=float,
        default=-0.15,
        help="일일 손절 기준 (기본 -15%)",
    )
    run_parser.add_argument(
        "--max-holdings",
        type=int,
        default=20,
        help="최대 동시 보유 종목 수 (기본 20)",
    )
    run_parser.add_argument(
        "--sector-cap",
        type=float,
        default=0.30,
        help="섹션별 최대 노출 비율 (기본 0.30)",
    )
    run_parser.add_argument(
        "--min-adv-ratio",
        type=float,
        default=0.01,
        help="최소 유동성 비율 (기본 0.01)",
    )

    return parser


def _build_config(args: argparse.Namespace) -> Any:
    """CLI 인자를 기반으로 K200MQConfig를 구성합니다."""
    from k200_mq.config import K200MQConfig

    config_kwargs: dict[str, Any] = {
        "START_DATE": _parse_date(args.start),
        "END_DATE": _parse_date(args.end) if args.end else date.today(),
        "TOP_N": args.top_n,
        "REBALANCE_FREQ": args.rebalance_freq,
        "WEIGHT_MOMENTUM": args.weight_momentum,
        "WEIGHT_QUALITY": args.weight_quality,
        "EXCLUDE_KOSPI_TOP_N": args.exclude_kospi_top_n,
    }
    if args.dart_api_key:
        config_kwargs["DART_API_KEY"] = args.dart_api_key

    return K200MQConfig(**config_kwargs)


def _print_config_summary(config: Any) -> None:
    """구성 요약을 출력합니다."""
    print("\n" + "=" * 60)
    print("  KOSPI 200 Momentum + Quality — 백테스트 구성")
    print("=" * 60)
    print(f"  기간: {config.START_DATE} ~ {config.END_DATE}")
    print(f"  리밸런싱: {config.REBALANCE_FREQ}")
    print(f"  선택 종목: {config.TOP_N}")
    print(f"  최대 보유: {config.MAX_HOLDINGS}")
    print(f"  모멘텀 가중치: {config.WEIGHT_MOMENTUM}")
    print(f"  품질 가중치: {config.WEIGHT_QUALITY}")
    print(f"  KOSPI 상위 제외: {config.EXCLUDE_KOSPI_TOP_N}")
    print(f"  손절 기준: {config.SL_STOP_LOSS if hasattr(config, 'SL_STOP_LOSS') else 'N/A (not yet implemented)'}")
    print(f"  출력 디렉토리: {config.OUTPUT_DIR}")
    print("=" * 60)


def main() -> None:
    """CLI 진입점입니다."""
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        config = _build_config(args)
        _print_config_summary(config)

        logger.info("파이프라인 시작 준비 완료.")
        logger.info(
            "1단계: 유니버스 구성 (KOSPI 200 종속 이력)"
        )
        logger.info(
            "2단계: 가격 데이터 로드 (252d lookback + ADV)"
        )
        logger.info(
            "3단계: 팩터 계산 (Momentum, Quality, Regime)"
        )
        logger.info(
            "4단계: PortfolioRebalanceEngine로 백테스트 실행"
        )
        logger.info(
            "5단계: 성과 지표 계산 및 보고서 생성"
        )
        logger.info("")
        logger.info("참고: 현재 Phase 3입니다. 파이프라인 스켈레톤이 완성되었습니다.")
        logger.info("실제 데이터로 백테스트를 실행하려면 Phase 4 통합이 필요합니다.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()