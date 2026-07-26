"""KOSPI 200 Momentum + Quality 백테스트용 CLI 진입점.

사용법:
    uv run python -m k200_mq.main --help
    uv run python -m k200_mq.main run --dart-api-key=... \
        --start 2015-01-01 --end 2024-12-31 --output ./outputs
"""

from __future__ import annotations

import argparse
import hashlib
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


def _parse_date(s: str) -> date:
    """YYYY-MM-DD 문자열을 date 객체로 파싱합니다."""
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
        help="시작일 YYYY-MM-DD",
    )
    run_parser.add_argument(
        "--end",
        default=None,
        help="종료일 YYYY-MM-DD",
    )
    run_parser.add_argument(
        "--output",
        "-o",
        default="outputs",
        help="보고서 출력 디렉토리",
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

    return parser


def main() -> None:
    """CLI 진입점입니다."""
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        logger.info("KOSPI 200 Momentum + Quality 백테스트를 시작합니다.")
        logger.info("  기간: %s ~ %s", args.start, args.end or "today")
        logger.info(
            "  모멘텀 가중치: %.2f, 품질 가중치: %.2f",
            args.weight_momentum,
            args.weight_quality,
        )
        logger.info(
            "  리밸런싱: %s, 선택 종목: %d",
            args.rebalance_freq,
            args.top_n,
        )

        from k200_mq.config import K200MQConfig

        config_kwargs: dict[str, Any] = {
            "START_DATE": _parse_date(args.start),
            "END_DATE": _parse_date(args.end) if args.end else date.today(),
            "TOP_N": args.top_n,
            "REBALANCE_FREQ": args.rebalance_freq,
            "WEIGHT_MOMENTUM": args.weight_momentum,
            "WEIGHT_QUALITY": args.weight_quality,
        }
        if args.dart_api_key:
            config_kwargs["DART_API_KEY"] = args.dart_api_key
        config = K200MQConfig(**config_kwargs)
        logger.info("설정이 로드되었습니다.")

        logger.info("TODO: Pipeline execution — Phase 1 data layer → Phase 2 factors → Phase 3 strategy & engine")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
