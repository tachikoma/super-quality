"""KOSPI 200 Momentum + Quality 백테스트용 CLI 진입점.

사용법:
    uv run python -m k200_mq.main --help
    uv run python -m k200_mq.main run --dart-api-key=... \
        --start 2015-01-01 --end 2024-12-31 --output ./outputs_k200mq
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import logging
import math
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from k200_mq.core.analysis.metrics import (
    PerformanceMetrics,
    benchmark_metadata,
    compute_cost_attribution,
)
from k200_mq.validation.prepared import (
    PreparedK200MQInputs,
    execute_engine_interval,
)
from k200_mq.validation.runner import (
    TestEvaluation,
    TrainEvaluation,
    WalkForwardResult,
    run_walk_forward,
)
from k200_mq.validation.walk_forward import (
    MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
    get_candidate_library,
    get_expanding_window_folds,
)
from k200_mq.factors.momentum import (
    MOMENTUM_FORMULA,
    MOMENTUM_FORMULA_DEFAULT,
    MOMENTUM_FORMULA_VERSION,
)
from k200_mq.factors.quality import QUALITY_FORMULA, QUALITY_FORMULA_VERSION
from k200_mq.factors.regime import REGIME_FORMULA, REGIME_FORMULA_VERSION

logger = logging.getLogger(__name__)

_CREDENTIAL_FIELD_NAMES = frozenset({
    "ACCESS_TOKEN",
    "API_KEY",
    "API_TOKEN",
    "AUTH_TOKEN",
    "CLIENT_SECRET",
    "DART_API_KEY",
    "KRX_ID",
    "KRX_PW",
    "PASSWD",
    "PASSWORD",
    "PRIVATE_KEY",
    "PWD",
    "SECRET",
    "SECRET_KEY",
    "TOKEN",
})

_TRUE_WALKFORWARD_START = date(2015, 1, 1)
_TRUE_WALKFORWARD_END = date(2024, 12, 31)
_TRUE_WALKFORWARD_WARMUP_DAYS = 252
_TRUE_WALKFORWARD_LIMITATIONS = (
    "Mechanical non-PIT walk-forward only; this is not a validated performance claim.",
    "The prepared universe and quality inputs retain their current provenance and coverage limitations.",
    "Static/current market-cap ranking and non-filing-date inputs are not historical PIT evidence.",
    "Candidates are restricted to safe runtime overrides; factor preparation is not recomputed per candidate.",
    "Exact OOS date coverage is enforced when the prepared trading calendar is available; otherwise structural and non-empty OOS checks apply.",
)


class _UnsupportedCLIOption(argparse.Action):
    """Reject a retained compatibility option instead of silently ignoring it."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        option = option_string or self.option_strings[0]
        raise argparse.ArgumentError(
            self,
            f"{option} is unsupported/deferred by the K200MQ runtime and cannot be used",
        )


def _is_secret_field(name: str) -> bool:
    """Return whether a config key should be excluded from the manifest."""
    return str(name).upper() in _CREDENTIAL_FIELD_NAMES


def _ranking_fingerprint(ranking: tuple[str, ...]) -> str | None:
    """Return a stable fingerprint for one prepared ranking order."""
    if not ranking:
        return None
    payload = json.dumps(list(ranking), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ranking_from_price_data(price_data: pd.DataFrame) -> tuple[str, ...]:
    """Build a no-I/O mechanical ranking when the price loader omitted attrs.

    This is a prepared snapshot derived from the supplied frame, not a
    historical/PIT ranking.  It exists only to keep the extraction boundary
    self-contained when a test or older loader returns plain OHLCV data.
    """
    if price_data.empty or "mcap" not in price_data.columns:
        return ()
    if isinstance(price_data.index, pd.MultiIndex):
        frame = price_data.reset_index()
    else:
        frame = price_data.copy()
    if not {"ticker", "mcap"}.issubset(frame.columns):
        return ()
    frame["mcap"] = pd.to_numeric(frame["mcap"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "mcap"])
    if frame.empty:
        return ()
    if "date" in frame.columns:
        latest = frame.sort_values("date").drop_duplicates("ticker", keep="last")
    else:
        latest = frame.drop_duplicates("ticker", keep="last")
    latest = latest[latest["mcap"] > 0].sort_values(
        ["mcap", "ticker"], ascending=[False, True],
    )
    return tuple(latest["ticker"].astype(str).tolist())


def _manifest_safe(value: Any) -> Any:
    """Convert common pandas/config values into deterministic JSON values."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _manifest_safe(value.item())
    if isinstance(value, dict):
        return {
            str(key): _manifest_safe(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            if not _is_secret_field(str(key))
        }
    if isinstance(value, set):
        return sorted((_manifest_safe(item) for item in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_manifest_safe(item) for item in value]
    return str(value)


def _manifest_config(config: Any) -> dict[str, Any]:
    """Return public config values without API keys or credential fields."""
    if hasattr(config, "model_dump"):
        values = config.model_dump()
    elif hasattr(config, "__dict__"):
        values = vars(config)
    else:
        values = {}
    if not isinstance(values, dict):
        return {}
    return {
        str(key): _manifest_safe(value)
        for key, value in sorted(values.items(), key=lambda item: str(item[0]))
        if not _is_secret_field(str(key))
    }


def _quality_weight_metadata(config: Any) -> dict[str, Any]:
    """Return configured and effective quality component weights.

    Configuration stores raw component weights, while ``QualityFactor``
    normalizes them before constructing its composite.  Keep both forms in
    preparation and result artifacts so the weights used by a score are clear.
    """
    raw_weights = {
        "roe": float(getattr(config, "QUALITY_WEIGHT_ROE", 0.35)),
        "de": float(getattr(config, "QUALITY_WEIGHT_DE", 0.25)),
        "opmargin": float(getattr(config, "QUALITY_WEIGHT_OPMARGIN", 0.20)),
        "cashconv": float(getattr(config, "QUALITY_WEIGHT_CASHCONV", 0.20)),
    }
    total = sum(raw_weights.values())
    if not all(math.isfinite(value) for value in raw_weights.values()) or total <= 0:
        raise ValueError("quality component weights must have a finite positive sum")
    effective_weights = {
        name: value / total for name, value in raw_weights.items()
    }
    return {
        # ``weights`` is retained as a compatibility alias for configured
        # values; the explicit names below remove any ambiguity.
        "weights": dict(raw_weights),
        "configured_raw_weights": dict(raw_weights),
        "effective_normalized_weights": effective_weights,
        "weights_used": "effective_normalized_weights",
    }


def _factor_manifest_definitions(config: Any) -> dict[str, Any]:
    """Return versioned factor semantics for every run manifest."""
    quality_weights = _quality_weight_metadata(config)
    return {
        "momentum": {
            "version": MOMENTUM_FORMULA_VERSION,
            "formula": MOMENTUM_FORMULA,
            "default_formula": MOMENTUM_FORMULA_DEFAULT,
            "ranking_column": "momentum_z",
            "diagnostic_column": "momentum_6m",
            "diagnostic_only": True,
        },
        "quality": {
            "version": QUALITY_FORMULA_VERSION,
            "formula": QUALITY_FORMULA,
            **quality_weights,
            "ttm_filter": "unsupported/inert",
        },
        "regime": {
            "version": REGIME_FORMULA_VERSION,
            "formula": REGIME_FORMULA,
            "min_return": getattr(config, "REGIME_MIN_RETURN", 0.0),
            "return_window_days": 20,
            "threshold_semantics": (
                "bullish iff close > rolling MA and the 20-trading-day cumulative "
                "return is greater than REGIME_MIN_RETURN"
            ),
        },
    }


def _merge_factor_manifest_definitions(
    authoritative: Mapping[str, Any],
    supplied: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge only additive caller metadata into authoritative factor definitions.

    A prepared-input context can outlive the factor implementation that created
    it.  Existing factor names therefore keep every field emitted by the current
    implementation; stale context may add only new, non-authoritative fields.
    Unknown factor names are preserved as additive metadata, but never replace a
    current definition.
    """
    merged = {
        str(name): dict(definition)
        for name, definition in authoritative.items()
        if isinstance(definition, Mapping)
    }
    if not isinstance(supplied, Mapping):
        return merged

    for name, supplied_definition in supplied.items():
        factor_name = str(name)
        if not isinstance(supplied_definition, Mapping):
            continue
        if factor_name not in merged:
            merged[factor_name] = dict(supplied_definition)
            continue
        current_definition = merged[factor_name]
        for key, value in supplied_definition.items():
            # Presence in the current definition makes the field authoritative.
            # Only genuinely new metadata can be carried from a stale context.
            if key not in current_definition:
                current_definition[str(key)] = value
    return merged


def _git_manifest_state() -> dict[str, Any]:
    """Best-effort local git state; failure never blocks a backtest save."""
    repo_root = Path(__file__).resolve().parents[2]
    state: dict[str, Any] = {"sha": None, "dirty": None}
    try:
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if sha_result.returncode == 0 and sha_result.stdout.strip():
            state["sha"] = sha_result.stdout.strip()
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if status_result.returncode == 0:
            state["dirty"] = bool(status_result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return state


# ═══════════════════════════════════════════════════════════════════
# CLI 유틸리티
# ═══════════════════════════════════════════════════════════════════


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
    run_parser.epilog = (
        "Compatibility settings EXCLUDE_MANAGEMENT, EXCLUDE_INVESTMENT_NOTICE, "
        "EXCLUDE_PREFERRED, and EXCLUDE_ETF_ETN are unsupported/inert: they have "
        "no runtime consumer and are retained only for compatibility."
    )
    run_parser.add_argument(
        "--dart-api-key",
        default=argparse.SUPPRESS,
        help="OpenDartReader API 키",
    )
    run_parser.add_argument(
        "--start",
        default=argparse.SUPPRESS,
        help="시작일 YYYY-MM-DD (기본값: 2015-01-01)",
    )
    run_parser.add_argument(
        "--end",
        default=argparse.SUPPRESS,
        help="종료일 YYYY-MM-DD (기본값: 오늘)",
    )
    run_parser.add_argument(
        "--output",
        "-o",
        default=argparse.SUPPRESS,
        help="보고서 출력 디렉토리 (기본값: outputs_k200mq)",
    )
    run_parser.add_argument(
        "--no-cache",
        action=_UnsupportedCLIOption,
        nargs=0,
        help="[unsupported/deferred] 현재 K200MQ 런타임에는 캐시 우회 기능이 없음",
    )
    run_parser.add_argument(
        "--strict-pit",
        action="store_true",
        default=argparse.SUPPRESS,
        help="PIT 유니버스와 filing-date 재무 데이터가 없으면 중단",
    )
    run_parser.add_argument(
        "--top-n",
        type=int,
        default=argparse.SUPPRESS,
        help="선택 종목 수 (기본 20)",
    )
    run_parser.add_argument(
        "--rebalance-freq",
        default=argparse.SUPPRESS,
        help="리밸런싱 주기: M(월간) 또는 Q(분기)",
    )
    run_parser.add_argument(
        "--rebalance-lookback",
        type=int,
        action=_UnsupportedCLIOption,
        help=(
            "[unsupported/deferred] 리밸런싱 lookback은 현재 런타임에서 "
            "CLI로 변경할 수 없음"
        ),
    )
    run_parser.add_argument(
        "--weight-momentum",
        type=float,
        default=argparse.SUPPRESS,
        help="모멘텀 팩터 가중치 (기본 0.50)",
    )
    run_parser.add_argument(
        "--weight-quality",
        type=float,
        default=argparse.SUPPRESS,
        help="품질 팩터 가중치 (기본 0.50)",
    )
    run_parser.add_argument(
        "--exclude-kospi-top-n",
        type=int,
        default=argparse.SUPPRESS,
        help="모멘텀에서 제외할 KOSPI 상위 N개 (기본 50)",
    )
    run_parser.add_argument(
        "--stop-loss",
        type=float,
        default=argparse.SUPPRESS,
        help="enabled일 때 -1.0 < 기준 < 0.0인 trailing 손절 (기본 -15%%; disabled면 미사용)",
    )
    stop_loss_group = run_parser.add_mutually_exclusive_group()
    stop_loss_group.add_argument(
        "--enable-stop-loss",
        dest="enable_stop_loss",
        action="store_true",
        default=argparse.SUPPRESS,
        help="[run 전용] trailing stop-loss 주문 활성화 (기본값: 활성화)",
    )
    stop_loss_group.add_argument(
        "--disable-stop-loss",
        "--no-stop-loss",
        dest="enable_stop_loss",
        action="store_false",
        default=argparse.SUPPRESS,
        help="[run 전용] trailing stop-loss 주문 비활성화",
    )
    run_parser.add_argument(
        "--max-holdings",
        type=int,
        default=argparse.SUPPRESS,
        help="최대 동시 보유 종목 수 (기본 20)",
    )
    run_parser.add_argument(
        "--sector-cap",
        action=_UnsupportedCLIOption,
        nargs=0,
        default=argparse.SUPPRESS,
        help="[unsupported/deferred] 섹터별 최대 노출은 현재 런타임에서 지원하지 않음",
    )
    run_parser.add_argument(
        "--min-adv-ratio",
        action=_UnsupportedCLIOption,
        nargs=0,
        default=argparse.SUPPRESS,
        help="[unsupported/deferred] ADV 유동성 비율은 현재 런타임에서 지원하지 않음",
    )

    robustness_parser = sub.add_parser(
        "robustness",
        aliases=["walkforward"],
        help="독립 subperiod robustness test (walk-forward CV 아님)",
    )
    robustness_parser.add_argument(
        "--dart-api-key",
        default=argparse.SUPPRESS,
        help="OpenDartReader API 키",
    )
    robustness_parser.add_argument(
        "--output",
        "-o",
        default=argparse.SUPPRESS,
        help="결과 출력 디렉토리 (기본값: outputs_k200mq)",
    )
    robustness_parser.add_argument(
        "--top-n",
        type=int,
        default=argparse.SUPPRESS,
        help="선택 종목 수 (기본 20)",
    )
    robustness_parser.add_argument(
        "--rebalance-freq",
        default=argparse.SUPPRESS,
        help="리밸런싱 주기: M(월간) 또는 Q(분기)",
    )
    robustness_parser.add_argument(
        "--strict-pit",
        action="store_true",
        default=argparse.SUPPRESS,
        help="PIT 유니버스와 filing-date 재무 데이터가 없으면 중단",
    )

    true_walkforward_parser = sub.add_parser(
        "true-walkforward",
        aliases=["expanding-walkforward"],
        help=(
            "기계적 non-PIT expanding walk-forward 실행 "
            "(검증된 성과 주장 아님)"
        ),
        description=(
            "Prepared K200MQ 입력을 한 번만 사용해 train-only 후보 선택과 "
            "expanding test를 실행합니다. 기계적 non-PIT 결과이며 검증된 "
            "성과 주장이 아닙니다."
        ),
    )
    true_walkforward_parser.add_argument(
        "--dart-api-key",
        default=argparse.SUPPRESS,
        help="OpenDartReader API 키 (재무 입력 준비용)",
    )
    true_walkforward_parser.add_argument(
        "--output",
        "-o",
        default=argparse.SUPPRESS,
        help="결과 출력 디렉토리 (기본값: outputs_k200mq)",
    )
    true_walkforward_parser.add_argument(
        "--strict-pit",
        action="store_true",
        default=argparse.SUPPRESS,
        help="현재 non-PIT 입력을 실행 전에 거부 (기계적 WF에는 사용 불가)",
    )

    return parser


def _build_config(args: argparse.Namespace) -> Any:
    """CLI 인자를 기반으로 K200MQConfig를 구성합니다."""
    from k200_mq.config import K200MQConfig

    if getattr(args, "command", None) in {"robustness", "walkforward"}:
        config_kwargs: dict[str, Any] = {}
        if hasattr(args, "output"):
            config_kwargs["OUTPUT_DIR"] = args.output
        if hasattr(args, "top_n"):
            config_kwargs["TOP_N"] = args.top_n
        if hasattr(args, "rebalance_freq"):
            config_kwargs["REBALANCE_FREQ"] = args.rebalance_freq
        if getattr(args, "strict_pit", False):
            config_kwargs["STRICT_PIT_VALIDATION"] = True
        if hasattr(args, "dart_api_key") and args.dart_api_key:
            config_kwargs["DART_API_KEY"] = args.dart_api_key
        return K200MQConfig(**config_kwargs)

    if getattr(args, "command", None) in {
        "true-walkforward",
        "expanding-walkforward",
    }:
        config_kwargs = {
            "START_DATE": _TRUE_WALKFORWARD_START.isoformat(),
            "END_DATE": _TRUE_WALKFORWARD_END.isoformat(),
            "PRINT_SUMMARY": False,
        }
        if hasattr(args, "output"):
            config_kwargs["OUTPUT_DIR"] = args.output
        if getattr(args, "strict_pit", False):
            config_kwargs["STRICT_PIT_VALIDATION"] = True
        if hasattr(args, "dart_api_key") and args.dart_api_key:
            config_kwargs["DART_API_KEY"] = args.dart_api_key
        return K200MQConfig(**config_kwargs)

    config_kwargs = {}
    if hasattr(args, "start"):
        start_d = _parse_date(args.start)
        config_kwargs["START_DATE"] = start_d.isoformat() if start_d else args.start
    if hasattr(args, "end"):
        end_d = _parse_date(args.end) if args.end else date.today()
        config_kwargs["END_DATE"] = end_d.isoformat() if end_d else "today"
    explicit_options = {
        "top_n": "TOP_N",
        "rebalance_freq": "REBALANCE_FREQ",
        "weight_momentum": "WEIGHT_MOMENTUM",
        "weight_quality": "WEIGHT_QUALITY",
        "exclude_kospi_top_n": "EXCLUDE_KOSPI_TOP_N",
        "stop_loss": "SL_STOP_LOSS",
        "enable_stop_loss": "ENABLE_STOP_LOSS",
        "max_holdings": "MAX_HOLDINGS",
        "output": "OUTPUT_DIR",
    }
    for argument_name, config_name in explicit_options.items():
        if hasattr(args, argument_name):
            config_kwargs[config_name] = getattr(args, argument_name)
    if getattr(args, "strict_pit", False):
        config_kwargs["STRICT_PIT_VALIDATION"] = True
    if getattr(args, "dart_api_key", ""):
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
    print(f"  손절: {'활성' if config.ENABLE_STOP_LOSS else '비활성'} ({config.SL_STOP_LOSS:.1%})")
    print(f"  모멘텀 가중치: {config.WEIGHT_MOMENTUM}")
    print(f"  품질 가중치: {config.WEIGHT_QUALITY}")
    print(f"  KOSPI 상위 제외: {config.EXCLUDE_KOSPI_TOP_N}")
    print(f"  DART API: {'설정됨' if config.DART_API_KEY else '미설정 (품질 팩터 비활성)'}")
    print(f"  Strict PIT 검증: {'활성' if config.STRICT_PIT_VALIDATION else '비활성'}")
    print(
        f"  포트폴리오 제한: MAX_HOLDINGS={config.MAX_HOLDINGS}, "
        f"MIN_CASH_RATIO={config.MIN_CASH_RATIO:.2%}"
    )
    print(
        "  미지원/deferred (미적용): SECTOR_CAP, MIN_ADV_RATIO, "
        "UNIVERSE_SIZE, USE_52WEEK_HIGH, QUALITY_MIN_TTM_QUARTERS"
    )
    print(
        "  unsupported/inert (runtime consumer 없음): EXCLUDE_MANAGEMENT, "
        "EXCLUDE_INVESTMENT_NOTICE, EXCLUDE_PREFERRED, EXCLUDE_ETF_ETN"
    )
    print(
        f"  Regime: MA{config.REGIME_MA_PERIOD} and 20일 return > "
        f"REGIME_MIN_RETURN ({config.REGIME_MIN_RETURN:.4f})"
    )
    print("  MOMENTUM_WINDOW_SHORT: diagnostic-only momentum_6m (ranking/readiness 미사용)")
    print(f"  출력 디렉토리: {config.OUTPUT_DIR}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════
# 재무 데이터 → 일별 변환 유틸리티
# ═══════════════════════════════════════════════════════════════════


def _quarter_end_date(year: int, quarter: int) -> pd.Timestamp:
    """분기 말 일자를 반환합니다 (1→3월, 2→6월, 3→9월, 4→12월)."""
    month_map = {1: 3, 2: 6, 3: 9, 4: 12}
    month = month_map.get(quarter, 12)
    return pd.Timestamp(year=year, month=month, day=28)


def _convert_financial_to_daily(
    financial_data: pd.DataFrame,
    all_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """분기/연간 재무 데이터를 일별 빈도로 변환합니다 (전방 채움).

    Parameters
    ----------
    financial_data : pd.DataFrame
        ``get_financial_data`` 반환값. ``ticker``, ``year``, ``quarter``,
        ``revenue``, ``cogs``, ``net_income``, ``operating_cf``,
        ``total_assets``, ``total_equity`` 컬럼 포함.
    all_dates : pd.DatetimeIndex
        팩터 계산에 사용할 전체 영업일 목록.

    Returns
    -------
    pd.DataFrame
        ``ticker``, ``date``, ``net_income``, ``total_equity``,
        ``total_debt``, ``revenue``, ``operating_income``, ``operating_cf``.
    """
    if financial_data.empty:
        return pd.DataFrame()

    from k200_mq.data.provenance import (
        filing_to_trading_session,
        has_usable_filing_dates,
        find_filing_date_field,
        validate_financial_provenance,
    )

    filing_date_field = find_filing_date_field(financial_data)
    use_filing_dates = has_usable_filing_dates(financial_data)
    financial_provenance = validate_financial_provenance(
        financial_data,
        filing_date_used=use_filing_dates,
    )

    records: list[dict[str, Any]] = []
    for _, row in financial_data.iterrows():
        if use_filing_dates and filing_date_field is not None:
            dt = filing_to_trading_session(
                row[filing_date_field],
                pd.DatetimeIndex(all_dates),
                availability_policy=financial_provenance.get("availability_policy"),
                source_timezone=financial_provenance.get("source_timezone"),
                cutoff_time=financial_provenance.get("cutoff_time"),
            )
            if dt is None:
                continue
        else:
            try:
                dt = _quarter_end_date(int(row["year"]), int(row["quarter"]))
            except (ValueError, KeyError):
                continue

        revenue = float(row.get("revenue", 0) or 0)
        cogs = float(row.get("cogs", 0) or 0)
        total_assets = float(row.get("total_assets", 0) or 0)
        total_equity = float(row.get("total_equity", 0) or 0)

        records.append({
            "ticker": str(row["ticker"]),
            "date": dt,
            "net_income": float(row.get("net_income", 0) or 0),
            "total_equity": total_equity,
            "total_debt": max(total_assets - total_equity, 0.0),
            "revenue": revenue,
            "operating_income": max(revenue - cogs, 0.0),
            "operating_cf": float(row.get("operating_cf", 0) or 0),
        })

    if not records:
        return pd.DataFrame()

    fin_df = pd.DataFrame(records).sort_values(["ticker", "date"])

    daily_parts: list[pd.DataFrame] = []
    for tkr, grp in fin_df.groupby("ticker"):
        grp = grp.drop_duplicates(subset=["date"], keep="last").set_index("date")
        grp = grp[~grp.index.duplicated(keep="last")]
        grp_daily = grp.reindex(all_dates, method="ffill")
        grp_daily["ticker"] = tkr
        grp_daily["date"] = grp_daily.index
        daily_parts.append(grp_daily.reset_index(drop=True))

    if not daily_parts:
        return pd.DataFrame()

    result = pd.concat(daily_parts, ignore_index=True)
    financial_cols = [
        "net_income", "total_equity", "total_debt",
        "revenue", "operating_income", "operating_cf",
    ]
    result[financial_cols] = result[financial_cols].fillna(0.0)
    result.attrs["financial_provenance"] = financial_provenance
    return result


def _validate_first_rebalance_factor_readiness(
    universe_history: pd.DataFrame,
    factor_data: pd.DataFrame,
    measured_dates: pd.DatetimeIndex,
    config: Any | None = None,
) -> dict[str, Any]:
    """Find the first scheduled rebalance with enough momentum coverage.

    Quality is intentionally not part of this gate: DART coverage may be
    partial or disabled, while momentum is the factor whose trading-day
    warmup is required for the measured interval to be valid.  A scheduled
    rebalance can be inspected only after mapping it to the latest measured
    trading bar on or before its calendar date.  Earlier scheduled dates are
    recorded as skipped rather than making the whole run fail.
    """
    if universe_history.empty or "as_of" not in universe_history.columns:
        raise RuntimeError("예정 리밸런싱을 확인할 유니버스 이력이 없습니다.")

    as_of_dates = pd.to_datetime(universe_history["as_of"], errors="coerce")
    valid_as_of = as_of_dates.dropna()
    if valid_as_of.empty:
        raise RuntimeError("유니버스 이력에 유효한 리밸런싱 날짜가 없습니다.")

    measured = pd.DatetimeIndex(pd.to_datetime(measured_dates, errors="coerce"))
    measured = measured.dropna().normalize().unique().sort_values()
    if not len(measured):
        raise RuntimeError("첫 예정 리밸런싱을 확인할 측정 기간 가격 바가 없습니다.")

    configured_top_n = getattr(config, "TOP_N", 1) if config is not None else 1
    try:
        configured_top_n = max(int(configured_top_n), 0)
    except (TypeError, ValueError):
        configured_top_n = 1

    # Only schedules in the measured calendar window are eligible.  The
    # signal date is allowed to be the first measured bar when a schedule is
    # a weekend/holiday, but a schedule before the measured window is not a
    # measured-period trading opportunity.
    scheduled_dates = sorted({
        pd.Timestamp(value).normalize()
        for value in valid_as_of
        if measured.min() <= pd.Timestamp(value).normalize() <= measured.max()
    })
    if not scheduled_dates:
        raise RuntimeError("측정 기간에 예정된 리밸런싱 날짜가 없습니다.")

    factor_dates = pd.Series(pd.NaT, index=factor_data.index, dtype="datetime64[ns]")
    momentum = pd.Series(np.nan, index=factor_data.index, dtype=float)
    if "date" in factor_data.columns:
        factor_dates = pd.to_datetime(factor_data["date"], errors="coerce").dt.normalize()
    if "momentum_z" in factor_data.columns:
        momentum = pd.to_numeric(factor_data["momentum_z"], errors="coerce")

    evaluations: list[dict[str, Any]] = []
    first_ready: dict[str, Any] | None = None
    for scheduled_date in scheduled_dates:
        prior_measured = measured[measured <= scheduled_date]
        signal_date = pd.Timestamp(prior_measured[-1]).normalize() if len(prior_measured) else None

        scheduled_rows = universe_history.loc[
            as_of_dates.dt.normalize() == scheduled_date,
        ]
        universe = set(scheduled_rows["ticker"].dropna().astype(str)) if "ticker" in scheduled_rows else set()
        required_count = min(configured_top_n, len(universe))

        usable_tickers: set[str] = set()
        if signal_date is not None and {"ticker", "date", "momentum_z"}.issubset(factor_data.columns):
            usable = (
                factor_data["ticker"].astype(str).isin(universe)
                & factor_dates.eq(signal_date)
                & momentum.notna()
                & np.isfinite(momentum)
            )
            usable_rows = factor_data.loc[usable]
            if not usable_rows.empty:
                usable_tickers = set(usable_rows["ticker"].astype(str).unique())

        usable_count = len(usable_tickers)
        missing_tickers = sorted(universe - usable_tickers)
        coverage = {
            "scheduled_date": scheduled_date.date().isoformat(),
            "signal_date": signal_date.date().isoformat() if signal_date is not None else None,
            "usable_ticker_count": usable_count,
            "universe_ticker_count": len(universe),
            "required_ticker_count": required_count,
            "usable_tickers": sorted(usable_tickers),
            "missing_tickers": missing_tickers,
            "quality_required": False,
        }
        evaluations.append(coverage)

        is_ready = bool(universe) and signal_date is not None and usable_count >= required_count
        if is_ready and first_ready is None:
            first_ready = coverage
            logger.info(
                "첫 준비 완료 리밸런싱: 예정일=%s, 신호일=%s, momentum=%d/%d개",
                scheduled_date.date(),
                signal_date.date(),
                usable_count,
                required_count,
            )
        elif first_ready is None:
            logger.info(
                "준비 전 리밸런싱 건너뜀: 예정일=%s, 신호일=%s, momentum=%d/%d개",
                scheduled_date.date(),
                signal_date.date() if signal_date is not None else None,
                usable_count,
                required_count,
            )
        else:
            logger.info(
                "첫 준비 완료 이후 momentum 커버리지: 예정일=%s, 신호일=%s, "
                "momentum=%d/%d개",
                scheduled_date.date(),
                signal_date.date() if signal_date is not None else None,
                usable_count,
                required_count,
            )

    if first_ready is None:
        coverage_summary = [
            "%s=%d/%d" % (
                item["scheduled_date"],
                item["usable_ticker_count"],
                item["required_ticker_count"],
            )
            for item in evaluations
        ]
        raise RuntimeError(
            "측정 기간의 모든 예정 리밸런싱에서 momentum 커버리지가 부족합니다: %s. "
            "가격 warmup에 momentum shift에 필요한 거래일 관측치가 포함되었는지 "
            "확인하십시오 (quality 커버리지는 이 검증 대상이 아닙니다)."
            % ", ".join(coverage_summary)
        )

    first_scheduled_coverage = evaluations[0]
    skipped = evaluations[:evaluations.index(first_ready)]
    return {
        "first_scheduled_rebalance": {
            "scheduled_date": first_scheduled_coverage["scheduled_date"],
            "signal_date": first_scheduled_coverage["signal_date"],
        },
        "first_ready_rebalance": first_ready,
        "measured_trading_readiness_date": first_ready["signal_date"],
        "skipped_not_ready_rebalances": skipped,
        "scheduled_rebalance_count": len(evaluations),
        "quality_required": False,
    }


def _build_run_manifest(
    config: Any,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the auditable, secret-free metadata for one pipeline run."""
    context = context or {}
    dart_configured = bool(getattr(config, "DART_API_KEY", ""))
    costs = {
        "commission_rate": _manifest_safe(getattr(config, "COMMISSION_RATE", None)),
        "tax_rate": _manifest_safe(getattr(config, "TAX_RATE", None)),
        "slippage": _manifest_safe(getattr(config, "SLIPPAGE", None)),
    }
    configured_source = str(getattr(config, "MARKET_INDEX_TICKER", "KPI200"))
    supplied_benchmark = context.get("benchmark", {})
    supplied_source = (
        str(supplied_benchmark.get("source"))
        if isinstance(supplied_benchmark, Mapping) and supplied_benchmark.get("source")
        else None
    )
    benchmark_context = benchmark_metadata(source=configured_source)
    if isinstance(supplied_benchmark, Mapping):
        benchmark_context.update(dict(supplied_benchmark))
        if supplied_source is not None and supplied_source != configured_source:
            # Do not carry availability/counts across a context generated for
            # a different ticker.  The configured source remains explicit,
            # but the benchmark is unavailable until that source is prepared.
            benchmark_context.update({"available": False, "observation_count": 0})
    # Manifest provenance follows the configured ticker.  In particular, a
    # custom index must never inherit a KPI200 label from a stale context.
    benchmark_context.update({
        "source": configured_source,
        "source_ticker": configured_source,
        "benchmark_source": configured_source,
        "type": "price_return",
        "benchmark_type": "price_return",
        "is_total_return": False,
        "total_return": False,
        "source_type": (
            "kpi200" if configured_source.upper() == "KPI200" else "configured_market_index"
        ),
        "is_kpi200": configured_source.upper() == "KPI200",
    })
    benchmark_context["description"] = benchmark_metadata(
        source=configured_source,
    )["description"]
    supplied_attribution = context.get("cost_attribution", {})
    cost_attribution = (
        dict(supplied_attribution)
        if isinstance(supplied_attribution, Mapping)
        else compute_cost_attribution()
    )
    cost_attribution.setdefault(
        "definition",
        "Sum of actual filled buy/sell notionals multiplied by the configured "
        "commission, slippage, and sell-only tax rates.",
    )
    universe_context = dict(context.get(
        "universe",
        {"dates": [], "date_count": 0, "ticker_count": 0},
    ))
    supplied_universe_validity = context.get("universe_provenance", {})
    if not isinstance(supplied_universe_validity, dict):
        supplied_universe_validity = {}
    universe_validity = dict(supplied_universe_validity)

    # Never trust a PIT flag supplied directly in a manifest context.  The
    # pipeline carries the actual history object out-of-band so this function
    # can re-run the complete validator, including per-date fingerprints.
    universe_history = context.get("universe_history")
    if universe_history is not None:
        from k200_mq.data.universe import validate_universe_provenance

        try:
            universe_validity = validate_universe_provenance(universe_history)
        except (AttributeError, TypeError, ValueError):
            universe_validity = {
                "provenance": "legacy_proxy_unknown",
                "source": "legacy_proxy_unknown",
                "provenance_by_as_of": {},
                "provenance_metadata_by_as_of": {},
                "pit_valid": False,
                "reason": "manifest could not validate the supplied universe history",
            }
    elif (
        universe_validity.get("provenance") == "pit"
        or universe_validity.get("pit_valid") is True
        or universe_context.get("provenance") == "pit"
        or universe_context.get("pit_valid") is True
    ):
        universe_validity = {
            "provenance": "legacy_proxy_unknown",
            "source": "legacy_proxy_unknown",
            "provenance_by_as_of": {},
            "provenance_metadata_by_as_of": {},
            "pit_valid": False,
            "reason": "manifest rejected an unvalidated PIT universe claim",
        }
    universe_provenance = str(
        universe_validity.get("provenance", universe_context.get("provenance", "unknown"))
    )
    universe_context["provenance"] = universe_provenance
    universe_context["source"] = universe_provenance
    universe_context["provenance_by_as_of"] = universe_validity.get(
        "provenance_by_as_of", {},
    )
    universe_context["provenance_metadata_by_as_of"] = universe_validity.get(
        "provenance_metadata_by_as_of", {},
    )
    universe_context["pit_valid"] = bool(universe_validity.get("pit_valid", False))

    quality_context = dict(context.get("quality", {}))
    financial_validity = context.get("financial_provenance", {})
    if not isinstance(financial_validity, dict):
        financial_validity = {}
    else:
        financial_validity = dict(financial_validity)

    # A manifest must not promote a caller's parseable-column label to PIT.
    # Only the validator's complete contract evidence can support the PIT
    # mode; older/incomplete context is downgraded conservatively.
    financial_pit_claim = (
        financial_validity.get("mode") == "pit_filing_date"
        and financial_validity.get("pit_valid") is True
        and financial_validity.get("filing_date_used") is True
        and financial_validity.get("source_schema_contract") is True
        and (
            (
                financial_validity.get("meaningful_timestamp") is True
                and financial_validity.get("timezone_safe") is True
                and financial_validity.get("cutoff_policy_valid") is True
            )
            or financial_validity.get("availability_policy") == "next_session"
        )
    )
    if financial_validity.get("mode") == "pit_filing_date" and not financial_pit_claim:
        financial_validity["mode"] = "non_pit_fiscal_period"
        financial_validity["pit_valid"] = False
        financial_validity["reason"] = (
            "manifest rejected an incomplete filing-date PIT provenance contract"
        )
    financial_mode = str(
        financial_validity.get("mode", "non_pit_fiscal_period")
    )
    if financial_mode != "pit_filing_date":
        if quality_context.get("financial_data_mode") == "pit_filing_date":
            quality_context["financial_data_mode"] = financial_mode
        if quality_context.get("data_mode") == "pit_filing_date":
            quality_context["data_mode"] = financial_mode
        quality_context["financial_provenance"] = financial_validity
    quality_context.setdefault("financial_data_mode", financial_mode)
    quality_context.setdefault("data_mode", financial_mode)
    quality_context.setdefault("financial_provenance", financial_validity)
    quality_context["weights"] = _quality_weight_metadata(config)
    quality_context.setdefault("weights_applied", None)

    supplied_ranking = context.get(
        "ranking",
        context.get("kospi_mcap_ranking", {}),
    )
    ranking_context = dict(supplied_ranking) if isinstance(supplied_ranking, dict) else {}
    try:
        excluded_top_n = int(getattr(config, "EXCLUDE_KOSPI_TOP_N", 0))
    except (TypeError, ValueError):
        excluded_top_n = 0
    ranking_context.setdefault("required", excluded_top_n > 0)
    if not ranking_context:
        ranking_context = {
            "required": excluded_top_n > 0,
            "status": "unavailable" if excluded_top_n > 0 else "disabled",
            "provenance": "unavailable" if excluded_top_n > 0 else "not_required",
            "effective_date": None,
            "fingerprint": None,
            "pit_valid": False,
            "artifact_available": False,
        }
    else:
        ranking_context.setdefault(
            "status",
            "unavailable" if excluded_top_n > 0 else "disabled",
        )
        ranking_context.setdefault(
            "provenance",
            "unavailable" if excluded_top_n > 0 else "not_required",
        )
        ranking_context.setdefault("effective_date", None)
        ranking_context.setdefault("fingerprint", None)
        ranking_context.setdefault("pit_valid", False)
        ranking_context.setdefault(
            "artifact_available",
            ranking_context.get("fingerprint") is not None,
        )

    # The current prepared path supplies only a static ranking tuple.  Do not
    # preserve caller-provided PIT booleans, status, or classification: a
    # date-indexed artifact and validator-backed effective-date/fingerprint
    # contract do not exist yet.
    ranking_available = bool(ranking_context.get("artifact_available"))
    ranking_context["pit_valid"] = False
    ranking_context["classification"] = (
        "non_pit_mechanical" if ranking_available else "not_required"
    )
    if ranking_available:
        ranking_context["status"] = "non_pit_mechanical"
        ranking_context["provenance"] = "current_market_cap_snapshot"
        ranking_context["effective_date"] = None
    else:
        ranking_context["status"] = "disabled" if excluded_top_n <= 0 else "unavailable"
        ranking_context["provenance"] = "not_required" if excluded_top_n <= 0 else "unavailable"
        ranking_context["effective_date"] = None

    limitations = {
        "universe": (
            "legacy_proxy_unknown universe; cache provenance metadata is missing or untrusted"
            if universe_provenance == "legacy_proxy_unknown"
            else f"{universe_provenance} universe; constituent history is not point-in-time verified"
            if universe_provenance != "pit"
            else "PIT universe provenance recorded"
        ),
        "financials": (
            "financial quality data mode: non_pit_fiscal_period"
            if financial_mode != "pit_filing_date"
            else "financial quality data uses filing/publication dates"
        ),
        "momentum": (
            f"{MOMENTUM_FORMULA_VERSION}: {MOMENTUM_FORMULA}; "
            f"default {MOMENTUM_FORMULA_DEFAULT}; ranking uses momentum_z"
        ),
        "adv": "unsupported/deferred: MIN_ADV_RATIO is configured but not applied by the current engine",
        "sector_cap": "unsupported/deferred: SECTOR_CAP is configured but not applied by the current engine",
        "portfolio_limits": (
            "active: MAX_HOLDINGS caps concurrent holdings and MIN_CASH_RATIO "
            "reserves a minimum cash buffer during buy sizing"
        ),
        "universe_size": (
            "unsupported/deferred: UNIVERSE_SIZE is configured but the current universe "
            "loader determines the KOSPI 200 history without consuming this setting"
        ),
        "year_high": (
            "unsupported/deferred: USE_52WEEK_HIGH is configured but the year-high "
            "feature is not used for ranking"
        ),
        "quality_ttm": (
            "unsupported/deferred: QUALITY_MIN_TTM_QUARTERS is inert; no TTM-quarter "
            "filter is applied"
        ),
        "instrument_exclusions": (
            "unsupported/inert: EXCLUDE_MANAGEMENT, EXCLUDE_INVESTMENT_NOTICE, "
            "EXCLUDE_PREFERRED, and EXCLUDE_ETF_ETN have no runtime consumer; "
            "fields are retained for compatibility"
        ),
        "dart": (
            "missing DART mode: quality disabled and missing quality values filled with 0"
            if not dart_configured
            else "missing DART mode: not applicable; DART was configured"
        ),
        "ranking": (
            "KOSPI exclusion ranking is a prepared non-PIT mechanical snapshot; "
            "historical ranking is not claimed"
            if not ranking_context.get("pit_valid", False)
            else "KOSPI exclusion ranking has explicit PIT provenance"
        ),
    }
    factor_context = context.get(
        "factors",
        {"row_count": 0, "ticker_count": 0},
    )
    if not isinstance(factor_context, dict):
        factor_context = {"row_count": 0, "ticker_count": 0}
    factor_context = dict(factor_context)
    authoritative_factor_definitions = _factor_manifest_definitions(config)
    supplied_definitions = factor_context.get("definitions")
    factor_context["definitions"] = _merge_factor_manifest_definitions(
        authoritative_factor_definitions,
        supplied_definitions if isinstance(supplied_definitions, Mapping) else None,
    )
    return {
        "schema_version": 3,
        "command": context.get("command", "run"),
        "config": _manifest_config(config),
        "measured": {
            "start": context.get("measured_start"),
            "end": context.get("measured_end"),
        },
        "price": context.get(
            "price",
            {"date_range": {"start": None, "end": None}, "ticker_count": 0},
        ),
        "universe": universe_context,
        "factors": factor_context,
        # ``first_scheduled_rebalance`` is the calendar schedule we inspect;
        # ``first_ready_rebalance`` is the first schedule allowed to trade.
        # Keep this distinction explicit because the first schedule may have
        # no factor rows during momentum warmup.
        "rebalance_readiness": context.get("rebalance_readiness", {}),
        "regime_map": context.get(
            "regime_map", {"covered_date_count": 0, "measured_date_count": 0},
        ),
        "ranking": ranking_context,
        "quality": quality_context,
        "data_validity": {
            "strict_pit_validation": bool(
                getattr(config, "STRICT_PIT_VALIDATION", False)
            ),
            "universe": universe_validity,
            "financials": financial_validity,
            "ranking": ranking_context,
        },
        "execution": {
            "signal_policy": "close signal",
            "entry_policy": "next open",
            "exit_policy": "next open",
        },
        "costs": costs,
        "cost_attribution": cost_attribution,
        "benchmark": benchmark_context,
        "git": _git_manifest_state(),
        "limitations": limitations,
    }


def _enforce_strict_pit_validation(
    universe_provenance: dict[str, Any],
    financial_provenance: dict[str, Any] | None = None,
    config: Any | None = None,
    ranking_provenance: dict[str, Any] | None = None,
) -> None:
    """Raise an actionable error when a strict PIT input contract is unmet."""
    if config is not None:
        try:
            excluded_top_n = int(getattr(config, "EXCLUDE_KOSPI_TOP_N", 0))
        except (TypeError, ValueError):
            excluded_top_n = 0
        if excluded_top_n > 0:
            raise RuntimeError(
                "STRICT_PIT_VALIDATION rejects EXCLUDE_KOSPI_TOP_N because the "
                "current exclusion ranks from a market-cap snapshot. Set "
                "EXCLUDE_KOSPI_TOP_N=0 until effective-date PIT rank data exists."
            )
    if not universe_provenance.get("pit_valid", False):
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before factor/backtest execution: "
            f"universe provenance is {universe_provenance.get('provenance', 'unknown')!r}. "
            "Provide KRX historical constituent files with effective dates; "
            "the current FDR listing and market-cap fallback are proxies."
        )
    if financial_provenance is not None and not financial_provenance.get("pit_valid", False):
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before factor/backtest execution: "
            f"financial quality mode is {financial_provenance.get('mode', 'unknown')!r}. "
            "Use raw DART filing metadata with an explicit source/schema contract, "
            "a meaningful timestamp, or a documented conservative next-session "
            "policy; do not infer it from fiscal quarter ends."
        )
    if ranking_provenance is not None and not ranking_provenance.get("pit_valid", False):
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before factor/backtest execution: "
            "KOSPI exclusion ranking is non-PIT. Provide effective-date PIT "
            "ranking data; current market-cap snapshots are mechanical only."
        )


# ═══════════════════════════════════════════════════════════════════
# 메인 파이프라인
# ═══════════════════════════════════════════════════════════════════


def prepare_k200mq_inputs(
    config: Any,
    overall_start: date | str | None = None,
    overall_end: date | str | None = None,
    warmup_days: int = 252,
) -> PreparedK200MQInputs | None:
    """Load and calculate one shared, measured K200MQ input bundle.

    Steps
    -----
    1. 유니버스 구성 (KOSPI 200 종속 이력)
    2. 가격 데이터 로드 (lookback 포함)
    3. 팩터 계산 (Momentum, Quality, Regime)
    4. The returned bundle is executed separately by the in-memory interval
       adapter below.

    The existing loader, provenance, and factor steps remain here as the one
    preparation path.  Warmup rows are used for factor calculation only and
    are never included in the bundle's measured price frame.
    """
    from k200_mq.core.data.loader import (
        get_financial_data,
        get_market_index,
        get_price_data_with_lookback,
    )
    from k200_mq.data.provenance import (
        has_usable_filing_dates,
        validate_financial_provenance,
    )
    from k200_mq.data.universe import get_kospi200_history
    from k200_mq.data.universe import validate_universe_provenance
    from k200_mq.factors.momentum import MomentumFactor
    from k200_mq.factors.quality import QualityFactor
    from k200_mq.factors.regime import RegimeFactor

    supplied_start = overall_start if overall_start is not None else config.START_DATE
    supplied_end = overall_end if overall_end is not None else config.END_DATE
    start_date = _parse_date(supplied_start) if isinstance(supplied_start, str) else supplied_start
    end_date = _parse_date(supplied_end) if isinstance(supplied_end, str) else supplied_end
    if start_date is None or end_date is None:
        logger.error("시작일/종료일 파싱 실패")
        return

    # ── 1. 유니버스 구성 ──────────────────────────────────────
    logger.info("1단계: 유니버스 구성 (KOSPI 200 종속 이력)")
    strict_pit = bool(getattr(config, "STRICT_PIT_VALIDATION", False))
    if strict_pit:
        _enforce_strict_pit_validation(
            {"provenance": "config", "pit_valid": True},
            config=config,
        )
    local_pit_path = getattr(config, "LOCAL_PIT_UNIVERSE_PATH", "")
    universe_kwargs: dict[str, Any] = {}
    if local_pit_path:
        universe_kwargs = {
            "local_pit_universe_path": local_pit_path,
            "local_pit_universe_source_kind": getattr(
                config, "LOCAL_PIT_UNIVERSE_SOURCE_KIND", "",
            ),
            "local_pit_universe_manifest": getattr(
                config, "LOCAL_PIT_UNIVERSE_MANIFEST", "",
            ),
        }
    universe_history = get_kospi200_history(
        start_date, end_date, config.REBALANCE_FREQ, **universe_kwargs,
    )
    if universe_history.empty:
        if strict_pit:
            raise RuntimeError(
                "STRICT_PIT_VALIDATION failed before factor/backtest execution: "
                "universe history is empty and has no PIT provenance. Provide "
                "KRX historical constituent files with effective dates."
            )
        logger.error("유니버스 데이터가 비어 있습니다. 백테스트를 중단합니다.")
        return

    universe_provenance = validate_universe_provenance(universe_history)
    if strict_pit and not universe_provenance["pit_valid"]:
        _enforce_strict_pit_validation(universe_provenance)
    if strict_pit and not config.DART_API_KEY:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION requires DART financial data with an explicit "
            "source/schema filing contract and a meaningful timestamp or documented "
            "next-session policy. Set DART_API_KEY "
            "and provide raw DART filing metadata; the current fiscal-period-only "
            "loader is not PIT-valid."
        )

    all_tickers = sorted(universe_history["ticker"].unique().tolist())
    logger.info(
        "유니버스: %d개 리밸런싱 일자, %d개 고유 티커",
        universe_history["as_of"].nunique(),
        len(all_tickers),
    )

    # ── 2. 가격 데이터 로드 ───────────────────────────────────
    lookback_days = max(int(warmup_days), 1)
    logger.info(
        "2단계: 가격 데이터 로드 (%dd lookback; ADV unsupported/deferred)",
        lookback_days,
    )
    # The long skipped-return is the ranking feature.  The exposed short
    # return is diagnostic only and must not determine cache/readiness coverage.
    required_momentum_observations = config.MOMENTUM_WINDOW_LONG + 1
    backtest_data, lookback_data = get_price_data_with_lookback(
        all_tickers,
        start_date,
        end_date,
        lookback_days=lookback_days,
        required_observations=required_momentum_observations,
    )

    if backtest_data.empty:
        logger.error("백테스트 기간 가격 데이터가 비어 있습니다.")
        return

    n_tickers_price = backtest_data.index.get_level_values("ticker").nunique()
    actual_price_dates = pd.DatetimeIndex(
        pd.to_datetime(backtest_data.index.get_level_values("date"), errors="coerce")
    ).dropna().normalize().unique().sort_values()
    universe_dates = pd.DatetimeIndex(
        pd.to_datetime(universe_history["as_of"], errors="coerce").dropna()
    ).normalize().unique().sort_values()
    manifest_context: dict[str, Any] = {
        "command": "run",
        "measured_start": start_date.isoformat(),
        "measured_end": end_date.isoformat(),
        "price": {
            "date_range": {
                "start": actual_price_dates.min().date().isoformat()
                if len(actual_price_dates)
                else None,
                "end": actual_price_dates.max().date().isoformat()
                if len(actual_price_dates)
                else None,
            },
            "ticker_count": int(n_tickers_price),
        },
        "universe": {
            "dates": [dt.date().isoformat() for dt in universe_dates],
            "date_count": int(len(universe_dates)),
            "ticker_count": int(len(all_tickers)),
        },
        "universe_provenance": universe_provenance,
        "universe_history": universe_history,
    }
    logger.info(
        "가격 데이터: backtest=%d행 (%d 티커), lookback=%d행",
        len(backtest_data), n_tickers_price, len(lookback_data),
    )
    kospi_mcap_ranking = tuple(
        str(ticker)
        for ticker in backtest_data.attrs.get("kospi_mcap_ranking", ())
    )
    if not kospi_mcap_ranking:
        kospi_mcap_ranking = _ranking_from_price_data(backtest_data)
    try:
        excluded_top_n = int(getattr(config, "EXCLUDE_KOSPI_TOP_N", 0))
    except (TypeError, ValueError):
        excluded_top_n = 0
    if excluded_top_n <= 0:
        # Exclusion is disabled, so do not carry or rank an unnecessary
        # artifact into the prepared interval bundle.
        kospi_mcap_ranking = ()
    ranking_status = (
        "non_pit_mechanical"
        if kospi_mcap_ranking
        else ("unavailable" if excluded_top_n > 0 else "disabled")
    )
    ranking_provenance = (
        "current_market_cap_snapshot"
        if kospi_mcap_ranking
        else ("unavailable" if excluded_top_n > 0 else "not_required")
    )
    ranking_context = {
        "required": excluded_top_n > 0,
        "status": ranking_status,
        "provenance": ranking_provenance,
        "effective_date": None,
        "fingerprint": _ranking_fingerprint(kospi_mcap_ranking),
        "pit_valid": False,
        "artifact_available": bool(kospi_mcap_ranking),
    }
    manifest_context["ranking"] = ranking_context

    # 전체 가격 데이터 (팩터 계산용 — lookback 포함).  The loader's
    # contract makes warmup half-open, but de-duplicate here as a safe guard
    # for older cache entries.  Warmup rows are never passed to the engine.
    full_price = pd.concat([lookback_data, backtest_data])
    full_price = full_price[~full_price.index.duplicated(keep="last")].sort_index()
    
    # 품질 팩터용 전체 일자 추출 (reset_index 전에!)
    all_full_dates = full_price.index.get_level_values("date").unique()
    
    full_price = full_price.reset_index()  # MultiIndex → ticker, date 컬럼

    # 백테스트 기간 일별 영업일 목록
    backtest_dates = backtest_data.index.get_level_values("date").sort_values().unique()

    # ── 3. 공시일 재무 데이터 계약 확인 ─────────────────────────
    # The current normalized DART loader intentionally exposes fiscal-period
    # values only.  Load it before any factor computation so strict mode cannot
    # run momentum or quality factors with unverifiable financial availability.
    financial_data = pd.DataFrame()
    daily_financial = pd.DataFrame()
    financial_provenance = validate_financial_provenance(financial_data)
    if config.DART_API_KEY:
        logger.info("  재무 데이터 로드 중 (DART API)...")
        try:
            years = list(range(start_date.year - 1, end_date.year + 1))
            financial_data = get_financial_data(
                all_tickers, years, api_key=config.DART_API_KEY,
            )
            logger.info("  재무 데이터: %d행", len(financial_data))
            financial_provenance = validate_financial_provenance(
                financial_data,
                filing_date_used=has_usable_filing_dates(financial_data),
            )
            if strict_pit and not financial_provenance["pit_valid"]:
                _enforce_strict_pit_validation(
                    universe_provenance,
                    financial_provenance,
                    config=config,
                )
            if not financial_data.empty:
                daily_financial = _convert_financial_to_daily(
                    financial_data, all_full_dates,
                )
                financial_provenance = daily_financial.attrs.get(
                    "financial_provenance", financial_provenance,
                )
        except RuntimeError:
            raise
        except Exception as exc:
            if strict_pit:
                raise RuntimeError(
                    "STRICT_PIT_VALIDATION could not establish filing-date "
                    "financial availability. Obtain raw DART filing metadata "
                    "and preserve its publication date."
                ) from exc
            logger.warning("  재무 데이터 로드 실패 (%s) — 모멘텀 전용으로 진행", exc)

    if strict_pit and not financial_provenance.get("pit_valid", False):
        _enforce_strict_pit_validation(
            universe_provenance,
            financial_provenance,
            config=config,
        )

    # ── 4. 팩터 계산 ──────────────────────────────────────────
    logger.info("4단계: 팩터 계산 (Momentum, Quality, Regime)")
    quality_weight_metadata = _quality_weight_metadata(config)

    # 4a. 모멘텀 팩터
    logger.info(
        "  3a. 모멘텀 팩터 (skipped-return: %s; default %s) 계산 중...",
        MOMENTUM_FORMULA,
        MOMENTUM_FORMULA_DEFAULT,
    )
    momentum_factor = MomentumFactor()
    momentum_df = momentum_factor.compute(
        full_price,
        long_window=config.MOMENTUM_WINDOW_LONG,
        short_window=config.MOMENTUM_WINDOW_SHORT,
        skip_days=config.MOMENTUM_SKIP_DAYS,
    )
    logger.info("  모멘텀 팩터: %d행", len(momentum_df))

    # 4b. 품질 팩터 (DART API 필요)
    quality_df = pd.DataFrame()
    if config.DART_API_KEY and not daily_financial.empty:
        logger.info("  4b. 품질 팩터 계산 중 (DART API)...")
        try:
            quality_factor = QualityFactor(
                weight_roe=config.QUALITY_WEIGHT_ROE,
                weight_de=config.QUALITY_WEIGHT_DE,
                weight_opmargin=config.QUALITY_WEIGHT_OPMARGIN,
                weight_cashconv=config.QUALITY_WEIGHT_CASHCONV,
            )
            quality_weight_metadata["configured_raw_weights"] = dict(
                quality_factor.raw_weights
            )
            quality_weight_metadata["effective_normalized_weights"] = dict(
                quality_factor.weights
            )
            quality_weight_metadata["weights"] = dict(quality_factor.raw_weights)
            quality_df = quality_factor.compute(
                daily_financial,
                min_ttm_quarters=config.QUALITY_MIN_TTM_QUARTERS,
            )
            logger.info("  품질 팩터: %d행", len(quality_df))
        except Exception as exc:
            if strict_pit:
                raise RuntimeError(
                    "STRICT_PIT_VALIDATION quality factor preparation failed; "
                    "verify filing-date financial columns are used."
                ) from exc
            logger.warning("  품질 팩터 계산 실패 (%s) — 모멘텀 전용으로 진행", exc)
    else:
        logger.info("  4b. DART API/재무 데이터 없음 — 품질 팩터 건너뜀 (모멘텀 전용)")

    # 4c. 시장 지수와 리짓 필터
    # The benchmark is an independent output.  Load it even when regime
    # scaling is disabled so REGIME_FILTER_ENABLED cannot accidentally make
    # benchmark attribution unavailable.
    regime_filter_enabled = bool(getattr(config, "REGIME_FILTER_ENABLED", True))
    index_ticker = str(getattr(config, "MARKET_INDEX_TICKER", "KPI200"))
    logger.info("  3c. 시장 지수(%s) 준비 중...", index_ticker)
    # MA200 needs roughly 200 trading observations before the first measured
    # date.  Use a deliberately conservative calendar window so weekends and
    # holidays do not leave the first measured regime silently incomplete.
    regime_history_days = max(config.REGIME_MA_PERIOD * 2, 365) if regime_filter_enabled else 0
    regime_index_start = pd.Timestamp(start_date) - pd.Timedelta(days=regime_history_days)
    index_raw = get_market_index(index_ticker, regime_index_start.date(), end_date)

    if regime_filter_enabled:
        logger.info("  리짓 필터 계산 중 (%s MA200)...", index_ticker)
        if not index_raw.empty:
            regime_factor = RegimeFactor()
            index_for_regime = index_raw.reset_index()
            regime_df = regime_factor.compute(
                index_for_regime,
                ma_period=config.REGIME_MA_PERIOD,
                min_return_days=20,
                min_return=config.REGIME_MIN_RETURN,
                reduction=config.REGIME_REDUCTION,
            )
            measured_regime = regime_df[
                regime_df["date"].isin(pd.to_datetime(backtest_dates))
            ].dropna(subset=["regime", "position_scale"])
            regime_scale_map = measured_regime.set_index("date")["position_scale"].to_dict()
            valid_regime = regime_df.dropna(subset=["regime"])
            logger.info(
                "  리짓: %d일 중 Bullish %d일 (%.1f%%)",
                len(valid_regime),
                int(valid_regime["regime"].sum()),
                valid_regime["regime"].mean() * 100 if not valid_regime.empty else 0.0,
            )
            manifest_context["regime_map"] = {
                "enabled": True,
                "status": "applied",
                "applied": True,
                "min_return_threshold": config.REGIME_MIN_RETURN,
                "covered_date_count": int(len(regime_scale_map)),
                "measured_date_count": int(len(backtest_dates)),
                "coverage_ratio": len(regime_scale_map) / max(len(backtest_dates), 1),
            }
        else:
            logger.warning("  KOSPI 200 지수 데이터 없음 — 리짓 필터 비활성")
            regime_scale_map = None
            manifest_context["regime_map"] = {
                "enabled": True,
                "status": "no_index_data",
                "applied": False,
                "covered_date_count": 0,
                "measured_date_count": int(len(backtest_dates)),
                "coverage_ratio": 0.0,
            }
    else:
        logger.info("  3c. REGIME_FILTER_ENABLED=False — 리짓 축소/스케일 적용 안 함")
        regime_scale_map = None
        manifest_context["regime_map"] = {
            "enabled": False,
            "status": "disabled",
            "mode": "disabled_by_config",
            "reason": "REGIME_FILTER_ENABLED=False",
            "applied": False,
            "covered_date_count": 0,
            "measured_date_count": int(len(backtest_dates)),
            "coverage_ratio": 0.0,
        }

    # ── 4d. 팩터 병합 ────────────────────────────────────────
    logger.info("  3d. 팩터 병합 중...")
    factor_data = momentum_df[["ticker", "date", "momentum_z"]].copy()

    quality_coverage: dict[str, Any]
    if not quality_df.empty:
        quality_z_map = quality_df.set_index(["ticker", "date"])["quality_composite_z"]
        factor_data["quality_z"] = factor_data.apply(
            lambda r: quality_z_map.get((r["ticker"], r["date"]), 0.0),
            axis=1,
        )
        n_with_quality = (factor_data["quality_z"] != 0.0).sum()
        quality_tickers = set(
            factor_data.loc[factor_data["quality_z"] != 0.0, "ticker"].unique()
        )
        logger.info(
            "  품질 커버리지: %d/%d 티커 (%.1f%%), %d/%d 행",
            len(quality_tickers),
            factor_data["ticker"].nunique(),
            len(quality_tickers) / max(factor_data["ticker"].nunique(), 1) * 100,
            n_with_quality,
            len(factor_data),
        )
        quality_keys = set(
            zip(
                quality_df["ticker"].astype(str),
                pd.to_datetime(quality_df["date"], errors="coerce").dt.normalize(),
            )
        )
        factor_keys = list(
            zip(
                factor_data["ticker"].astype(str),
                pd.to_datetime(factor_data["date"], errors="coerce").dt.normalize(),
            )
        )
        covered_rows = sum(key in quality_keys for key in factor_keys)
        covered_tickers = {
            ticker for ticker, factor_date in factor_keys
            if (ticker, factor_date) in quality_keys
        }
        quality_coverage = {
            "mode": "partial_allowed_fill_missing_with_zero",
            "quality_factor_row_count": int(len(quality_df)),
            "quality_factor_ticker_count": int(quality_df["ticker"].nunique()),
            "covered_factor_row_count": int(covered_rows),
            "covered_factor_ticker_count": int(len(covered_tickers)),
            "factor_row_count": int(len(factor_data)),
            "required_full_coverage": False,
        }
    else:
        factor_data["quality_z"] = 0.0
        logger.info("  품질 팩터 없음 — quality_z = 0.0")
        quality_coverage = {
            "mode": "disabled_fill_missing_with_zero",
            "quality_factor_row_count": 0,
            "quality_factor_ticker_count": 0,
            "covered_factor_row_count": 0,
            "covered_factor_ticker_count": 0,
            "factor_row_count": int(len(factor_data)),
            "required_full_coverage": False,
        }

    quality_coverage["financial_data_mode"] = financial_provenance.get(
        "mode", "non_pit_fiscal_period",
    )
    quality_coverage["financial_provenance"] = financial_provenance
    quality_coverage["weights"] = quality_weight_metadata
    quality_coverage["weights_applied"] = bool(not quality_df.empty)

    factor_data = factor_data[
        factor_data["date"].isin(pd.to_datetime(backtest_dates))
    ].copy()
    logger.info("  최종 팩터 데이터: %d행, %d개 고유 티커",
                len(factor_data),
                factor_data["ticker"].nunique())

    rebalance_readiness = _validate_first_rebalance_factor_readiness(
        universe_history,
        factor_data,
        pd.DatetimeIndex(backtest_dates),
        config,
    )
    manifest_context["factors"] = {
        "row_count": int(len(factor_data)),
        "ticker_count": int(factor_data["ticker"].nunique()),
        "definitions": _factor_manifest_definitions(config),
    }
    manifest_context["rebalance_readiness"] = rebalance_readiness
    manifest_context["quality"] = quality_coverage
    manifest_context["financial_provenance"] = financial_provenance

    logger.info("입력 준비 완료: 측정 구간 %s ~ %s", start_date, end_date)
    runtime_config = config
    if hasattr(config, "model_copy"):
        runtime_config = config.model_copy(deep=True, update={
            "DART_API_KEY": "",
            "KRX_ID": "",
            "KRX_PW": "",
        })

    return PreparedK200MQInputs(
        price_data=backtest_data,
        factor_data=factor_data,
        index_data=index_raw,
        universe_history=universe_history,
        financial_data=financial_data,
        regime_scale_map=regime_scale_map,
        kospi_mcap_ranking=kospi_mcap_ranking,
        ranking_status=ranking_status,
        ranking_provenance=ranking_provenance,
        ranking_effective_date=None,
        ranking_fingerprint=ranking_context["fingerprint"],
        ranking_pit_valid=False,
        manifest_context=manifest_context,
        provenance={
            "universe": universe_provenance,
            "financials": financial_provenance,
            "ranking": ranking_context,
        },
        coverage={
            "quality": quality_coverage,
            "rebalance_readiness": rebalance_readiness,
        },
        measured_start=start_date,
        measured_end=end_date,
        warmup_start=(
            pd.Timestamp(all_full_dates.min()).date()
            if len(all_full_dates)
            else None
        ),
        warmup_end=start_date - timedelta(days=1),
        measured_dates=tuple(pd.Timestamp(value) for value in backtest_dates),
        active_trading_start=rebalance_readiness["measured_trading_readiness_date"],
        runtime_config=runtime_config,
    )


def _run_pipeline(config: Any) -> dict[str, Any] | None:
    """Run the existing pipeline using one prepared input bundle."""
    logger.info("1~4단계: 공유 입력 준비")
    prepared = prepare_k200mq_inputs(config)
    if prepared is None:
        return None

    logger.info("5단계: PortfolioRebalanceEngine로 백테스트 실행")
    results = execute_engine_interval(
        prepared,
        config,
        measured_start=prepared.measured_start,
        measured_end=prepared.measured_end,
        active_trading_start=prepared.active_trading_start,
    )
    results["metrics"] = _compute_result_metrics(results)
    manifest_context = dict(prepared.manifest_context)
    manifest_context["cost_attribution"] = results["metrics"].get(
        "cost_attribution", compute_cost_attribution()
    )
    manifest_context["benchmark"] = results.get("benchmark", {})
    results["_manifest_context"] = manifest_context

    logger.info("6단계: 결과 저장")
    _save_results(results, config)

    if getattr(config, "PRINT_SUMMARY", True):
        _print_summary(results, config)
    return results


# Private aliases keep the extraction easy to discover for internal callers
# while the public names above remain the preferred integration boundary.
_prepare_inputs = prepare_k200mq_inputs
_execute_engine_interval = execute_engine_interval


# ═══════════════════════════════════════════════════════════════════
# Mechanical expanding walk-forward integration
# ═══════════════════════════════════════════════════════════════════


def _true_walkforward_json_safe(value: Any) -> Any:
    """Convert pandas/numpy values into strict, deterministic JSON values."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _true_walkforward_json_safe(value.item())
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (int, str, bool)):
        return value
    if isinstance(value, pd.DataFrame):
        return [
            _true_walkforward_json_safe(record)
            for record in value.to_dict(orient="records")
        ]
    if isinstance(value, pd.Series):
        return [
            {
                "date": _true_walkforward_json_safe(index),
                "value": _true_walkforward_json_safe(item),
            }
            for index, item in value.items()
        ]
    if isinstance(value, np.ndarray):
        return [_true_walkforward_json_safe(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {
            str(key): _true_walkforward_json_safe(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_true_walkforward_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(
            (_true_walkforward_json_safe(item) for item in value),
            key=str,
        )
    return str(value)


def _completed_exit_count(trade_log: Any) -> int:
    """Count exits with the same return-log rule used by robustness."""
    if not isinstance(trade_log, pd.DataFrame) or "return_pct" not in trade_log:
        return 0
    return int(trade_log["return_pct"].notna().sum())


def _compute_result_metrics(
    results: Mapping[str, Any],
    returns: pd.Series | None = None,
) -> dict[str, Any]:
    """Compute shared metrics, including optional benchmark and cost data."""
    daily_returns = returns if returns is not None else results.get("daily_returns")
    if not isinstance(daily_returns, pd.Series):
        daily_returns = pd.Series(dtype=float)
    metric_returns = pd.to_numeric(daily_returns, errors="coerce").copy()
    has_real_dates = isinstance(metric_returns.index, pd.DatetimeIndex)
    if not has_real_dates:
        metric_returns.index = pd.bdate_range("2000-01-03", periods=len(metric_returns))

    calculator = PerformanceMetrics(metric_returns)
    benchmark_returns = results.get("benchmark_returns")
    if isinstance(benchmark_returns, pd.Series) and not benchmark_returns.empty and has_real_dates:
        benchmark_metadata_value = results.get("benchmark")
        metadata = (
            dict(benchmark_metadata_value)
            if isinstance(benchmark_metadata_value, Mapping)
            else None
        )
        calculator.set_benchmark(benchmark_returns, metadata=metadata)

    execution_stats = results.get("execution_stats")
    initial_capital = (
        execution_stats.get("initial_capital")
        if isinstance(execution_stats, Mapping)
        else None
    )
    return calculator.compute_all(
        trade_log=results.get("trade_log"),
        snapshots=results.get("portfolio_snapshots"),
        initial_capital=initial_capital,
    )


def _evaluate_interval_result(
    results: Any,
    *,
    returns: pd.Series | None = None,
) -> dict[str, Any]:
    """Extract shared PerformanceMetrics values from one engine interval."""
    if not isinstance(results, dict):
        return {
            "train_sharpe": None,
            "n_exits": 0,
            "valid": False,
            "status": "invalid_engine_result",
            "reason": "engine result is not a mapping",
            "metrics": {},
        }

    daily_returns = returns if returns is not None else results.get("daily_returns")
    if not isinstance(daily_returns, pd.Series) or daily_returns.empty:
        return {
            "train_sharpe": None,
            "n_exits": _completed_exit_count(results.get("trade_log")),
            "valid": False,
            "status": "empty_daily_returns",
            "reason": "daily returns are empty or missing",
            "metrics": {},
        }

    numeric_returns = pd.to_numeric(daily_returns, errors="coerce")
    if numeric_returns.isna().any():
        reason = "daily returns contain missing or non-numeric values"
        return {
            "train_sharpe": None,
            "n_exits": _completed_exit_count(results.get("trade_log")),
            "valid": False,
            "status": "invalid_daily_returns",
            "reason": reason,
            "metrics": {},
        }
    if not np.isfinite(numeric_returns.to_numpy(dtype=float)).all():
        reason = "daily returns contain non-finite values"
        return {
            "train_sharpe": None,
            "n_exits": _completed_exit_count(results.get("trade_log")),
            "valid": False,
            "status": "invalid_daily_returns",
            "reason": reason,
            "metrics": {},
        }
    if (numeric_returns <= -1.0).any():
        reason = "daily returns contain a loss of 100% or more"
        return {
            "train_sharpe": None,
            "n_exits": _completed_exit_count(results.get("trade_log")),
            "valid": False,
            "status": "invalid_daily_returns",
            "reason": reason,
            "metrics": {},
        }
    if isinstance(numeric_returns.index, pd.DatetimeIndex) and numeric_returns.index.isna().any():
        reason = "daily returns contain invalid dates"
        return {
            "train_sharpe": None,
            "n_exits": _completed_exit_count(results.get("trade_log")),
            "valid": False,
            "status": "invalid_daily_returns",
            "reason": reason,
            "metrics": {},
        }

    metric_returns = numeric_returns.copy()
    if not isinstance(metric_returns.index, pd.DatetimeIndex):
        metric_returns.index = pd.bdate_range("2000-01-03", periods=len(metric_returns))
    metrics = _compute_result_metrics(results, returns=metric_returns)
    sharpe = float(metrics["sharpe_ratio"])
    if not np.isfinite(sharpe):
        return {
            "train_sharpe": None,
            "n_exits": _completed_exit_count(results.get("trade_log")),
            "valid": False,
            "status": "invalid_non_finite_sharpe",
            "reason": "PerformanceMetrics returned a non-finite Sharpe",
            "metrics": metrics,
        }
    return {
        "train_sharpe": sharpe,
        "n_exits": _completed_exit_count(results.get("trade_log")),
        "valid": True,
        "status": "valid",
        "reason": "",
        "metrics": metrics,
    }


def _restrict_interval_returns(
    daily_returns: Any,
    measured_start: date,
    measured_end: date,
) -> pd.Series:
    """Return a detached daily-return series restricted to one test fold."""
    if not isinstance(daily_returns, pd.Series) or daily_returns.empty:
        return pd.Series(dtype=float)
    dates = pd.DatetimeIndex(pd.to_datetime(daily_returns.index, errors="coerce"))
    if dates.isna().any():
        return pd.Series(daily_returns.to_numpy(copy=True), index=dates)
    mask = (dates >= pd.Timestamp(measured_start)) & (dates <= pd.Timestamp(measured_end))
    restricted = daily_returns.loc[mask].copy(deep=True)
    restricted.index = dates[mask]
    return restricted


def _true_walkforward_train_evaluator(
    prepared: PreparedK200MQInputs,
):
    """Build a train callback that only executes the prepared interval adapter."""
    def evaluate(fold: Any, candidate: Any, candidate_config: Any) -> TrainEvaluation:
        try:
            results = execute_engine_interval(
                prepared,
                candidate_config,
                measured_start=fold.train_start,
                measured_end=fold.train_end,
                active_trading_start=prepared.active_trading_start,
            )
        except Exception as exc:  # noqa: BLE001 - recorded as an invalid score
            return TrainEvaluation(
                train_sharpe=None,
                n_exits=0,
                valid=False,
                status="train_engine_error",
                metrics={"error": str(exc)},
            )

        evaluation = _evaluate_interval_result(results)
        return TrainEvaluation(
            train_sharpe=evaluation["train_sharpe"],
            n_exits=evaluation["n_exits"],
            valid=evaluation["valid"],
            status=evaluation["status"],
            metrics=_true_walkforward_json_safe(evaluation["metrics"]),
        )

    return evaluate


def _true_walkforward_test_evaluator(
    prepared: PreparedK200MQInputs,
):
    """Build a test callback for the selected candidate and test interval only."""
    def evaluate(fold: Any, candidate: Any, candidate_config: Any) -> TestEvaluation:
        try:
            results = execute_engine_interval(
                prepared,
                candidate_config,
                measured_start=fold.test_start,
                measured_end=fold.test_end,
                active_trading_start=prepared.active_trading_start,
            )
        except Exception as exc:  # noqa: BLE001 - recorded as an invalid fold
            return TestEvaluation(
                returns={},
                metrics={"error": str(exc)},
                results={},
                valid=False,
                status="test_engine_error",
                reason=str(exc),
            )

        raw_test_returns = (
            results.get("daily_returns") if isinstance(results, dict) else None
        )
        test_returns = _restrict_interval_returns(
            raw_test_returns,
            fold.test_start,
            fold.test_end,
        )
        # Do not hide adapter output outside the test interval.  The runner's
        # out-of-bound contract must see those dates and reject the fold.
        if isinstance(raw_test_returns, pd.Series) and not raw_test_returns.empty:
            raw_dates = pd.DatetimeIndex(
                pd.to_datetime(raw_test_returns.index, errors="coerce")
            )
            if not raw_dates.isna().any() and (
                (raw_dates < pd.Timestamp(fold.test_start)).any()
                or (raw_dates > pd.Timestamp(fold.test_end)).any()
            ):
                test_returns = raw_test_returns.copy(deep=True)
                test_returns.index = raw_dates
        evaluation = _evaluate_interval_result(results, returns=test_returns)
        serializable_results = (
            {
                str(key): _true_walkforward_json_safe(value)
                for key, value in results.items()
                if key != "daily_returns"
            }
            if isinstance(results, dict)
            else {}
        )
        return TestEvaluation(
            returns=test_returns,
            metrics=_true_walkforward_json_safe(evaluation["metrics"]),
            results=serializable_results,
            valid=evaluation["valid"],
            status=evaluation["status"],
            reason=evaluation["reason"],
        )

    return evaluate


def _expected_true_walkforward_test_dates(
    prepared: PreparedK200MQInputs,
    folds: tuple[Any, ...],
) -> dict[int, tuple[date, ...]]:
    """Build exact OOS calendars from prepared price coverage when available."""
    if not prepared.measured_dates:
        return {}
    measured_dates = tuple(
        sorted({pd.Timestamp(value).floor("D").date() for value in prepared.measured_dates})
    )
    return {
        fold_number: tuple(
            point_date
            for point_date in measured_dates
            if fold.test_start <= point_date <= fold.test_end
        )
        for fold_number, fold in enumerate(folds, start=1)
    }


def _true_walkforward_summary_rows(result: WalkForwardResult) -> list[dict[str, Any]]:
    """Build stable one-row-per-fold summary records."""
    rows: list[dict[str, Any]] = []
    base_config = _true_walkforward_json_safe(result.base_runtime_config)
    preparation_context = _true_walkforward_json_safe(
        result.preparation_manifest_context
    )
    git_state = _true_walkforward_json_safe(result.git_state)
    for fold_result in result.folds:
        fold = fold_result.fold
        metrics = dict(fold_result.test_metrics)
        rows.append({
            "fold": fold_result.fold_number,
            "train_start": fold.train_start.isoformat(),
            "train_end": fold.train_end.isoformat(),
            "test_start": fold.test_start.isoformat(),
            "test_end": fold.test_end.isoformat(),
            "classification": fold_result.classification,
            "selected_candidate_id": fold_result.selected_candidate_id,
            "selected_config_hash": fold_result.selected_config_hash,
            "effective_config_hash": fold_result.selected_effective_config_hash,
            "selected_effective_config_hash": fold_result.selected_effective_config_hash,
            "base_runtime_config_hash": result.base_runtime_config_hash,
            "base_runtime_config": json.dumps(
                base_config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "effective_config": json.dumps(
                _true_walkforward_json_safe(fold_result.selected_effective_config),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "effective_candidate_configs": json.dumps(
                _true_walkforward_json_safe(fold_result.effective_candidate_configs),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "effective_candidate_config_hashes": json.dumps(
                _true_walkforward_json_safe(
                    fold_result.effective_candidate_config_hashes
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "git_state": json.dumps(
                git_state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "preparation_manifest_context": json.dumps(
                preparation_context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "valid": fold_result.valid,
            "status": fold_result.status,
            "error": fold_result.error,
            "train_scores": json.dumps(
                [score.to_dict() for score in fold_result.train_scores],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "test_total_return": metrics.get("total_return"),
            "test_cagr": metrics.get("cagr"),
            "test_sharpe": metrics.get("sharpe_ratio"),
            "test_max_drawdown": metrics.get("max_drawdown"),
            "test_total_trades": metrics.get("total_trades"),
            "n_test_returns": len(fold_result.test_returns),
            "expected_oos_dates": json.dumps(
                [item.isoformat() for item in fold_result.expected_oos_dates],
                separators=(",", ":"),
            ),
            "returned_oos_dates": json.dumps(
                [item.isoformat() for item in fold_result.returned_oos_dates],
                separators=(",", ":"),
            ),
        })
    return rows


def _save_true_walkforward_artifacts(
    result: WalkForwardResult,
    prepared: PreparedK200MQInputs,
    config: Any,
) -> Path:
    """Save deterministic true-WF artifacts without writing fold intermediates."""
    output_dir = Path(config.OUTPUT_DIR) / "true_walkforward"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = result.to_dict()
    manifest.update({
        "command": "true-walkforward",
        "classification": MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
        "claim": "mechanical non-PIT walk-forward; not a validated performance claim",
        "limitations": list(_TRUE_WALKFORWARD_LIMITATIONS),
        "base_runtime_config": _true_walkforward_json_safe(
            result.base_runtime_config
        ),
        "base_runtime_config_hash": result.base_runtime_config_hash,
        "git": _true_walkforward_json_safe(result.git_state),
        "preparation_manifest_context": _true_walkforward_json_safe(
            result.preparation_manifest_context
        ),
        "prepared_inputs": {
            "warmup_start": _true_walkforward_json_safe(prepared.warmup_start),
            "warmup_end": _true_walkforward_json_safe(prepared.warmup_end),
            "measured_start": _true_walkforward_json_safe(prepared.measured_start),
            "measured_end": _true_walkforward_json_safe(prepared.measured_end),
            "active_trading_start": _true_walkforward_json_safe(
                prepared.active_trading_start,
            ),
            "provenance": _true_walkforward_json_safe(dict(prepared.provenance)),
            "coverage": _true_walkforward_json_safe(dict(prepared.coverage)),
            "manifest_context": _true_walkforward_json_safe(
                dict(prepared.manifest_context)
            ),
        },
        "selected_config_hashes_by_fold": {
            str(fold.fold_number): fold.selected_config_hash
            for fold in result.folds
        },
        "selected_candidates_by_fold": {
            str(fold.fold_number): fold.selected_candidate_id
            for fold in result.folds
        },
        "effective_candidate_configs_by_fold": _true_walkforward_json_safe(
            result.effective_candidate_configs_by_fold
        ),
        "effective_candidate_config_hashes_by_fold": _true_walkforward_json_safe(
            result.effective_candidate_config_hashes_by_fold
        ),
    })
    manifest_path = output_dir / "selection_and_folds.json"
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        json.dump(
            _true_walkforward_json_safe(manifest),
            manifest_file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        manifest_file.write("\n")

    oos_path = output_dir / "oos_returns.csv"
    oos_frame = pd.DataFrame(
        result.to_csv_rows(),
        columns=["fold", "date", "daily_return"],
    )
    oos_frame.to_csv(oos_path, index=False)

    summary_path = output_dir / "summary.csv"
    summary_columns = [
        "fold", "train_start", "train_end", "test_start", "test_end",
        "classification", "selected_candidate_id", "selected_config_hash",
        "effective_config_hash",
        "selected_effective_config_hash", "base_runtime_config_hash",
        "base_runtime_config", "effective_config", "effective_candidate_configs",
        "effective_candidate_config_hashes", "git_state",
        "preparation_manifest_context",
        "valid", "status", "error", "train_scores", "test_total_return",
        "test_cagr", "test_sharpe", "test_max_drawdown", "test_total_trades",
        "n_test_returns", "expected_oos_dates", "returned_oos_dates",
    ]
    pd.DataFrame(
        _true_walkforward_summary_rows(result),
        columns=summary_columns,
    ).to_csv(summary_path, index=False)
    logger.info("true-walkforward artifacts 저장: %s", output_dir)
    return output_dir


def _save_true_walkforward_failure_artifact(
    prepared: PreparedK200MQInputs | None,
    config: Any,
    error: str,
) -> Path:
    """Persist a diagnostic manifest when orchestration fails early."""
    output_dir = Path(config.OUTPUT_DIR) / "true_walkforward"
    output_dir.mkdir(parents=True, exist_ok=True)
    base_runtime_config = _manifest_config(
        prepared.runtime_config if prepared is not None else config
    )
    base_runtime_config_hash = hashlib.sha256(
        json.dumps(
            base_runtime_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    diagnostic = {
        "command": "true-walkforward",
        "classification": MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
        "status": "invalid",
        "valid": False,
        "error": error,
        "claim": "mechanical non-PIT walk-forward; not a validated performance claim",
        "limitations": list(_TRUE_WALKFORWARD_LIMITATIONS),
        "base_runtime_config": base_runtime_config,
        "base_runtime_config_hash": base_runtime_config_hash,
        "git": _git_manifest_state(),
        "preparation_manifest_context": _true_walkforward_json_safe(
            dict(prepared.manifest_context) if prepared is not None else {}
        ),
        "prepared_inputs": {
            "measured_start": _true_walkforward_json_safe(
                prepared.measured_start if prepared is not None else None
            ),
            "measured_end": _true_walkforward_json_safe(
                prepared.measured_end if prepared is not None else None
            ),
            "provenance": _true_walkforward_json_safe(
                dict(prepared.provenance) if prepared is not None else {}
            ),
            "coverage": _true_walkforward_json_safe(
                dict(prepared.coverage) if prepared is not None else {}
            ),
        },
    }
    path = output_dir / "selection_and_folds.json"
    with path.open("w", encoding="utf-8") as diagnostic_file:
        json.dump(
            _true_walkforward_json_safe(diagnostic),
            diagnostic_file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        diagnostic_file.write("\n")
    pd.DataFrame(
        [{
            "status": "invalid",
            "valid": False,
            "error": error,
            "base_runtime_config_hash": base_runtime_config_hash,
            "base_runtime_config": json.dumps(
                diagnostic["base_runtime_config"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "git_state": json.dumps(
                diagnostic["git"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "preparation_manifest_context": json.dumps(
                diagnostic["preparation_manifest_context"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }]
    ).to_csv(output_dir / "summary.csv", index=False)
    pd.DataFrame(columns=["fold", "date", "daily_return"]).to_csv(
        output_dir / "oos_returns.csv",
        index=False,
    )
    return output_dir


def _run_true_walkforward(config: Any) -> WalkForwardResult:
    """Run mechanical expanding WF over one shared prepared input bundle."""
    logger.warning(
        "true-walkforward: mechanical non-PIT WF only; not a validated performance claim"
    )
    logger.warning(
        "현재 universe/quality provenance 및 coverage limitations remain; "
        "candidate/fold마다 입력을 재준비하지 않습니다"
    )

    try:
        prepared = prepare_k200mq_inputs(
            config,
            overall_start=_TRUE_WALKFORWARD_START,
            overall_end=_TRUE_WALKFORWARD_END,
            warmup_days=_TRUE_WALKFORWARD_WARMUP_DAYS,
        )
    except Exception as exc:
        _save_true_walkforward_failure_artifact(None, config, str(exc))
        raise RuntimeError(
            "true-walkforward input preparation failed for the full "
            f"2015-01-01 through 2024-12-31 range: {exc}"
        ) from exc
    if prepared is None:
        _save_true_walkforward_failure_artifact(None, config, "no prepared inputs")
        raise RuntimeError(
            "true-walkforward input preparation returned no prepared inputs for "
            "the full 2015-01-01 through 2024-12-31 range"
        )

    if bool(getattr(config, "STRICT_PIT_VALIDATION", False)):
        _save_true_walkforward_failure_artifact(
            prepared,
            config,
            "true-walkforward rejects --strict-pit before engine execution",
        )
        raise RuntimeError(
            "true-walkforward rejects --strict-pit before engine execution: "
            "the current prepared universe/quality inputs are mechanical non-PIT "
            "and cannot support a validated PIT classification"
        )

    folds = get_expanding_window_folds()
    candidates = get_candidate_library()
    try:
        result = run_walk_forward(
            folds,
            candidates,
            _true_walkforward_train_evaluator(prepared),
            _true_walkforward_test_evaluator(prepared),
            classification=MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
            base_runtime_config=prepared.runtime_config,
            preparation_manifest_context=_true_walkforward_json_safe(
                dict(prepared.manifest_context)
            ),
            git_state=_git_manifest_state(),
            expected_test_dates=_expected_true_walkforward_test_dates(prepared, folds),
        )
    except Exception as exc:
        _save_true_walkforward_failure_artifact(prepared, config, str(exc))
        raise RuntimeError(
            f"true-walkforward failed before a valid result was produced; "
            f"diagnostic artifacts were saved: {Path(config.OUTPUT_DIR) / 'true_walkforward'}"
        ) from exc
    _save_true_walkforward_artifacts(result, prepared, config)
    if not result.valid:
        invalid_folds = [
            f"fold {fold.fold_number}: {fold.status}"
            for fold in result.folds
            if not fold.valid
        ]
        raise RuntimeError(
            "true-walkforward produced an invalid result; diagnostic artifacts were "
            f"saved: {Path(config.OUTPUT_DIR) / 'true_walkforward'}; "
            + ", ".join(invalid_folds)
        )
    logger.warning(
        "true-walkforward 완료: classification=%s; 결과는 검증된 성과 주장이 아님",
        MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
    )
    return result


# Compatibility-friendly internal name for callers using the alternate command.
_run_expanding_walkforward = _run_true_walkforward


# ═══════════════════════════════════════════════════════════════════
# 결과 저장 및 요약
# ═══════════════════════════════════════════════════════════════════


def _save_results(results: dict[str, Any], config: Any) -> None:
    """백테스트 결과를 파일로 저장합니다."""
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshots = results["portfolio_snapshots"]
    trade_log = results["trade_log"]

    if not snapshots.empty:
        snap_path = output_dir / "portfolio_snapshots.csv"
        snapshots.to_csv(snap_path, index=False)
        logger.info("포트폴리오 스냅샷 저장: %s", snap_path)

    if not trade_log.empty:
        trade_path = output_dir / "trade_log.csv"
        trade_log.to_csv(trade_path, index=False)
        logger.info("거래 로그 저장: %s", trade_path)

    daily_returns = results.get("daily_returns")
    if daily_returns is not None and not daily_returns.empty:
        ret_path = output_dir / "daily_returns.csv"
        daily_returns.to_csv(ret_path, header=True)
        logger.info("일별 수익률 저장: %s", ret_path)

    metrics = results.get("metrics")
    if not isinstance(metrics, Mapping):
        metrics = _compute_result_metrics(results)
    metrics_path = output_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as metrics_file:
        json.dump(
            _true_walkforward_json_safe(dict(metrics)),
            metrics_file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        metrics_file.write("\n")
    logger.info("성과 지표 저장: %s", metrics_path)

    benchmark_returns = results.get("benchmark_returns")
    if isinstance(benchmark_returns, pd.Series) and not benchmark_returns.empty:
        benchmark_path = output_dir / "benchmark_returns.csv"
        benchmark_returns.to_csv(benchmark_path, header=True)
        logger.info("벤치마크 가격수익률 저장: %s", benchmark_path)

    manifest_context = dict(results.get("_manifest_context") or {})
    manifest_context.setdefault("cost_attribution", metrics.get("cost_attribution", {}))
    manifest_context.setdefault("benchmark", results.get("benchmark", {}))
    manifest = _build_run_manifest(
        config,
        manifest_context,
    )
    manifest_path = output_dir / "run_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        json.dump(
            _manifest_safe(manifest),
            manifest_file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        manifest_file.write("\n")
    logger.info("실행 매니페스트 저장: %s", manifest_path)


def _print_summary(results: dict[str, Any], config: Any) -> None:
    """백테스트 요약 통계를 출력합니다."""
    snapshots = results["portfolio_snapshots"]
    trade_log = results["trade_log"]
    daily_returns = results.get("daily_returns", pd.Series(dtype=float))

    print("\n" + "=" * 60)
    print("  KOSPI 200 Momentum + Quality — 백테스트 결과")
    print("=" * 60)

    if snapshots.empty:
        print("  결과가 없습니다.")
        print("=" * 60)
        return

    nav_series = snapshots["nav"]
    initial_nav = float(nav_series.iloc[0])
    final_nav = float(nav_series.iloc[-1])
    total_return = (final_nav / initial_nav - 1.0) * 100

    print(f"  기간: {snapshots['date'].iloc[0]} ~ {snapshots['date'].iloc[-1]}")
    print(f"  초기 자본: {initial_nav:,.0f}원")
    print(f"  최종 자본: {final_nav:,.0f}원")
    print(f"  총 수익률: {total_return:+.2f}%")

    if not daily_returns.empty:
        ann_factor = 252
        daily_mean = float(daily_returns.mean())
        daily_std = float(daily_returns.std())
        sharpe = (daily_mean / daily_std * np.sqrt(ann_factor)) if daily_std > 0 else 0.0

        cum = (1 + daily_returns).cumprod()
        running_max = cum.cummax()
        drawdown = (cum - running_max) / running_max
        max_dd = float(drawdown.min()) * 100

        print(f"  연간 수익률: {((1 + daily_mean) ** ann_factor - 1) * 100:+.2f}%")
        print(f"  연간 변동성: {daily_std * np.sqrt(ann_factor) * 100:.2f}%")
        print(f"  Sharpe 비율: {sharpe:.3f}")
        print(f"  최대 낙폭: {max_dd:.2f}%")

    if not trade_log.empty and "return_pct" in trade_log.columns:
        completed = trade_log[trade_log["return_pct"].notna()]
        n_trades = len(completed)
        if n_trades > 0:
            win_rate = (completed["return_pct"] > 0).mean() * 100
            avg_return = completed["return_pct"].mean() * 100
            avg_hold = completed["hold_days"].mean()
            print(f"  총 거래: {n_trades}건")
            print(f"  승률: {win_rate:.1f}%")
            print(f"  평균 수익률: {avg_return:+.2f}%")
            print(f"  평균 보유일: {avg_hold:.1f}일")

    n_positions = snapshots["num_positions"].mean()
    print(f"  평균 보유 종목: {n_positions:.1f}개")
    print(f"  출력 디렉토리: {config.OUTPUT_DIR}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════
# Independent subperiod robustness test
# ═══════════════════════════════════════════════════════════════════


_SUBPERIOD_ARTIFACT_FILENAMES = (
    "portfolio_snapshots.csv",
    "trade_log.csv",
    "daily_returns.csv",
    "benchmark_returns.csv",
    "metrics.json",
    "run_manifest.json",
)


def _clear_subperiod_artifacts(fold_output: Path) -> str | None:
    """Remove outputs from a previous run before starting a subperiod."""
    for filename in _SUBPERIOD_ARTIFACT_FILENAMES:
        try:
            (fold_output / filename).unlink(missing_ok=True)
        except OSError as exc:
            return f"could not clear previous {filename} ({exc})"
    return None


def _subperiods(as_of: date | None = None) -> list[tuple[str, str, str]]:
    """Return the fixed periods used by the independent robustness test."""
    effective_as_of = as_of or date.today()
    return [
        ("2014-01-01", "2016-12-31", "Subperiod 1: 2014-2016"),
        ("2017-01-01", "2018-12-31", "Subperiod 2: 2017-2018"),
        ("2019-01-01", "2020-12-31", "Subperiod 3: 2019-2020"),
        ("2021-01-01", "2022-12-31", "Subperiod 4: 2021-2022"),
        (
            "2023-01-01",
            effective_as_of.isoformat(),
            f"Subperiod 5: 2023-{effective_as_of.year}",
        ),
    ]


def _invalid_fold_metrics(label: str, reason: str) -> dict[str, Any]:
    """Create an explicitly invalid result without zero-valued performance."""
    print(f"  {label}: INVALID subperiod ({reason})")
    return {
        "validation_type": "independent_subperiod_robustness",
        "label": label,
        "status": "invalid",
        "valid": False,
        "reason": reason,
        "total_return": np.nan,
        "cagr": np.nan,
        "ann_return": np.nan,
        "sharpe": np.nan,
        "sharpe_ratio": np.nan,
        "max_dd": np.nan,
        "max_drawdown": np.nan,
        "win_rate": np.nan,
        "n_trades": 0,
    }


def _print_fold_metrics(
    label: str,
    daily_returns: pd.Series,
    trade_log: pd.DataFrame,
    invalid_reason: str | None = None,
    benchmark_returns: pd.Series | None = None,
    snapshots: pd.DataFrame | None = None,
    initial_capital: float | None = None,
) -> dict[str, Any]:
    """Calculate one independent subperiod's metrics using shared definitions."""
    if invalid_reason is not None:
        return _invalid_fold_metrics(label, invalid_reason)
    if daily_returns is None or daily_returns.empty:
        return _invalid_fold_metrics(label, "daily returns are empty")

    numeric_returns = pd.to_numeric(daily_returns, errors="coerce")
    if numeric_returns.isna().any():
        return _invalid_fold_metrics(label, "daily returns contain missing or non-numeric values")
    if not np.isfinite(numeric_returns.to_numpy(dtype=float)).all():
        return _invalid_fold_metrics(label, "daily returns contain non-finite values")
    if (numeric_returns <= -1.0).any():
        return _invalid_fold_metrics(label, "daily returns contain a loss of 100% or more")
    if isinstance(numeric_returns.index, pd.DatetimeIndex) and numeric_returns.index.isna().any():
        return _invalid_fold_metrics(label, "daily returns contain invalid dates")

    # PerformanceMetrics expects a date index for its period-return reports.
    # Pipeline output already has one; a deterministic business-day index keeps
    # this helper usable in unit tests with a plain Series as well.
    metric_returns = numeric_returns.copy()
    if not isinstance(metric_returns.index, pd.DatetimeIndex):
        metric_returns.index = pd.bdate_range("2000-01-03", periods=len(metric_returns))
    calculator = PerformanceMetrics(metric_returns)
    if (
        isinstance(benchmark_returns, pd.Series)
        and not benchmark_returns.empty
        and isinstance(numeric_returns.index, pd.DatetimeIndex)
    ):
        calculator.set_benchmark(benchmark_returns)
    shared_metrics = calculator.compute_all(
        trade_log=trade_log,
        snapshots=snapshots,
        initial_capital=initial_capital,
    )

    completed = (
        trade_log[trade_log["return_pct"].notna()]
        if isinstance(trade_log, pd.DataFrame) and "return_pct" in trade_log.columns
        else pd.DataFrame()
    )
    win_rate = (
        (completed["return_pct"] > 0).mean() * 100
        if len(completed) > 0
        else 0.0
    )
    total_return = float(shared_metrics["total_return"])
    cagr = float(shared_metrics["cagr"])
    sharpe = float(shared_metrics["sharpe_ratio"])
    max_dd = float(shared_metrics["max_drawdown"])

    print(f"  {label}: 수익률 {total_return*100:+.2f}% | "
          f"CAGR {cagr*100:+.2f}% | Sharpe {sharpe:.3f} | "
          f"MDD {max_dd*100:.2f}% | 승률 {win_rate:.1f}% | "
          f"거래 {len(completed)}건")

    return {
        "validation_type": "independent_subperiod_robustness",
        "label": label,
        "status": "valid",
        "valid": True,
        "reason": "",
        "total_return": total_return,
        "cagr": cagr,
        "ann_return": cagr,
        "sharpe": sharpe,
        "sharpe_ratio": sharpe,
        "max_dd": max_dd,
        "max_drawdown": max_dd,
        "win_rate": float(win_rate),
        "n_trades": len(completed),
    }


def _aggregate_subperiod_metrics(fold_metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only valid independent subperiod results."""
    valid_metrics: list[dict[str, Any]] = []
    for metrics in fold_metrics_list:
        if not metrics.get("valid", True):
            continue
        values = [
            metrics.get("total_return"),
            metrics.get("sharpe", metrics.get("sharpe_ratio")),
            metrics.get("max_dd", metrics.get("max_drawdown")),
            metrics.get("win_rate"),
        ]
        try:
            values_are_finite = all(
                value is not None and np.isfinite(float(value))
                for value in values
            )
        except (TypeError, ValueError):
            values_are_finite = False
        if values_are_finite:
            valid_metrics.append(metrics)

    result: dict[str, Any] = {
        "validation_type": "independent_subperiod_robustness",
        "valid_folds": len(valid_metrics),
        "invalid_folds": len(fold_metrics_list) - len(valid_metrics),
        "geo_mean_return": None,
        "mean_sharpe": None,
        "worst_mdd": None,
        "mean_mdd": None,
        "mean_win_rate": None,
        "total_trades": None,
    }
    if not valid_metrics:
        return result

    returns = np.asarray([float(m["total_return"]) for m in valid_metrics])
    result.update({
        "geo_mean_return": float(np.prod(1.0 + returns) ** (1.0 / len(returns)) - 1.0),
        "mean_sharpe": float(np.mean([float(m["sharpe"]) for m in valid_metrics])),
        "worst_mdd": float(min(float(m["max_dd"]) for m in valid_metrics)),
        "mean_mdd": float(np.mean([float(m["max_dd"]) for m in valid_metrics])),
        "mean_win_rate": float(np.mean([float(m["win_rate"]) for m in valid_metrics])),
        "total_trades": int(sum(int(m["n_trades"]) for m in valid_metrics)),
    })
    return result


def _run_subperiod_robustness(config: Any) -> None:
    """Run fixed independent subperiod robustness tests.

    These periods have no training window or parameter fitting.  This is not
    expanding-window walk-forward cross-validation; that validation remains
    future work.
    """
    base_output = Path(config.OUTPUT_DIR)
    robustness_dir = base_output / "subperiod_robustness"
    base_output.mkdir(parents=True, exist_ok=True)
    robustness_dir.mkdir(parents=True, exist_ok=True)
    periods = _subperiods()
    fold_metrics_list: list[dict[str, Any]] = []

    for period_number, (start_str, end_str, label) in enumerate(periods, start=1):
        print(f"\n{'=' * 60}")
        print(f"  {label} ({start_str} ~ {end_str})")
        print(f"{'=' * 60}")

        fold_output = robustness_dir / f"subperiod_{period_number}"
        fold_output.mkdir(parents=True, exist_ok=True)
        fold_config = config.model_copy(update={
            "START_DATE": start_str,
            "END_DATE": end_str,
            "OUTPUT_DIR": str(fold_output),
            "PRINT_SUMMARY": False,
        })

        clear_error = _clear_subperiod_artifacts(fold_output)
        pipeline_result: Any = None
        pipeline_error: str | None = None
        if clear_error is None:
            try:
                pipeline_result = _run_pipeline(fold_config)
            except Exception as exc:  # noqa: BLE001 - one failed fold must be reported
                pipeline_error = f"pipeline failed ({exc})"
        else:
            pipeline_error = clear_error

        trade_path = fold_output / "trade_log.csv"
        returns_path = fold_output / "daily_returns.csv"

        dr = pd.Series(dtype=float)
        returns_error: str | None = None
        tl = pd.DataFrame()

        # _run_pipeline returns its current in-memory results.  Do not fall
        # back to files when it returned an empty result: those files may be
        # leftovers from an earlier run, even if a caller failed to clear
        # them.  The normal pipeline is covered by this branch; the file
        # branch remains for compatibility with lightweight test runners and
        # older pipeline adapters that return None after saving.
        if pipeline_error is not None:
            returns_error = pipeline_error
        elif pipeline_result is not None:
            if not isinstance(pipeline_result, dict):
                returns_error = "pipeline returned no result mapping"
            elif "daily_returns" not in pipeline_result:
                returns_error = "pipeline result has no daily returns"
            else:
                current_returns = pipeline_result["daily_returns"]
                if isinstance(current_returns, pd.Series):
                    dr = current_returns.copy()
                elif current_returns is not None:
                    returns_error = "pipeline daily returns are not a Series"
                current_trade_log = pipeline_result.get("trade_log")
                if isinstance(current_trade_log, pd.DataFrame):
                    tl = current_trade_log.copy()
        elif returns_path.exists():
            try:
                dr_df = pd.read_csv(returns_path)
                if "daily_return" not in dr_df.columns:
                    returns_error = "daily returns file has no daily_return column"
                elif "date" not in dr_df.columns:
                    returns_error = "daily returns file has no date column"
                else:
                    dr = dr_df["daily_return"]
                    dr.index = pd.to_datetime(dr_df["date"], errors="coerce")
            except (OSError, ValueError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
                returns_error = f"could not read daily returns ({exc})"
        else:
            returns_error = "daily returns file is missing"

        if pipeline_error is None and pipeline_result is None and trade_path.exists():
            try:
                tl = pd.read_csv(trade_path)
            except (OSError, ValueError, pd.errors.EmptyDataError, pd.errors.ParserError):
                # A missing trade log does not invalidate return metrics.
                tl = pd.DataFrame()

        benchmark_returns = None
        snapshots = None
        initial_capital = None
        if isinstance(pipeline_result, dict):
            candidate_benchmark = pipeline_result.get("benchmark_returns")
            if isinstance(candidate_benchmark, pd.Series):
                benchmark_returns = candidate_benchmark
            candidate_snapshots = pipeline_result.get("portfolio_snapshots")
            if isinstance(candidate_snapshots, pd.DataFrame):
                snapshots = candidate_snapshots
            candidate_stats = pipeline_result.get("execution_stats")
            if isinstance(candidate_stats, Mapping):
                initial_capital = candidate_stats.get("initial_capital")
        metrics = _print_fold_metrics(
            label,
            dr,
            tl,
            invalid_reason=returns_error,
            benchmark_returns=benchmark_returns,
            snapshots=snapshots,
            initial_capital=initial_capital,
        )
        metrics.update({"period_start": start_str, "period_end": end_str})
        fold_metrics_list.append(metrics)

    # ── 종합 집계 ──────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("  Subperiod robustness test — independent subperiod results")
    print(f"{'=' * 60}")
    print(f"  {'Subperiod':<22} {'수익률':>10} {'CAGR':>10} "
          f"{'Sharpe':>8} {'MDD':>8} {'승률':>8} {'거래':>6}")
    print("  Sharpe: PerformanceMetrics 정의 (연율화 초과수익률 / 표본 변동성)")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")

    for m in fold_metrics_list:
        if not m["valid"]:
            print(f"  {m['label']:<22} INVALID ({m['reason']})")
            continue
        print(f"  {m['label']:<22} {m['total_return']*100:>+9.2f}% "
              f"{m['cagr']*100:>+9.2f}% {m['sharpe']:>8.3f} "
              f"{m['max_dd']*100:>7.2f}% {m['win_rate']:>7.1f}% "
              f"{m['n_trades']:>6d}")

    print(f"  {'─' * 22} {'─' * 10} {'─' * 10} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 6}")

    aggregate = _aggregate_subperiod_metrics(fold_metrics_list)
    if aggregate["valid_folds"]:
        print(f"  유효 subperiod 기하 평균 수익률: "
              f"{aggregate['geo_mean_return']*100:+.2f}%")
        print(f"  유효 subperiod 평균 Sharpe: {aggregate['mean_sharpe']:.3f}")
        print(f"  Worst MDD (유효 subperiod): {aggregate['worst_mdd']*100:.2f}%")
        print(f"  Mean MDD (참고용): {aggregate['mean_mdd']*100:.2f}%")
        print(f"  Sharpe 범위: {min(m['sharpe'] for m in fold_metrics_list if m['valid']):.3f} ~ "
              f"{max(m['sharpe'] for m in fold_metrics_list if m['valid']):.3f}")
        print(f"  총 거래: {aggregate['total_trades']}건")
    else:
        print("  집계 불가: 유효한 independent subperiod가 없습니다.")
    print(f"  유효/무효 subperiod: {aggregate['valid_folds']}/{aggregate['invalid_folds']}")

    summary_path = base_output / "subperiod_robustness_summary.csv"
    print(f"\n  Subperiod robustness summary 저장: {summary_path}")
    print(f"{'=' * 60}")

    # ── 요약 저장 ──────────────────────────────────────────
    summary_df = pd.DataFrame(fold_metrics_list)
    summary_df.to_csv(summary_path, index=False)


def _run_walkforward(config: Any) -> None:
    """Compatibility alias for the independent subperiod robustness test."""
    _run_subperiod_robustness(config)


# ═══════════════════════════════════════════════════════════════════
# 엔트리포인트
# ═══════════════════════════════════════════════════════════════════


def main() -> None:
    """CLI 진입점입니다."""
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        config = _build_config(args)
        _print_config_summary(config)

        logger.info("파이프라인 시작...")
        _run_pipeline(config)
        logger.info("파이프라인 완료.")

    elif args.command in {"robustness", "walkforward"}:
        config = _build_config(args)
        _print_config_summary(config)
        logger.info("Independent subperiod robustness test 시작...")
        _run_subperiod_robustness(config)
        logger.info("Independent subperiod robustness test 완료.")

    elif args.command in {"true-walkforward", "expanding-walkforward"}:
        config = _build_config(args)
        _print_config_summary(config)
        _run_true_walkforward(config)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
