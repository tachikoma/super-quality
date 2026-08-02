"""KOSPI 200 Momentum + Quality 백테스트용 CLI 진입점.

사용법:
    uv run python -m k200_mq.main --help
    uv run python -m k200_mq.main run --dart-api-key=... \
        --start 2015-01-01 --end 2024-12-31 --output ./outputs_k200mq
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_SECRET_FIELD_MARKERS = (
    "api",
    "credential",
    "key",
    "password",
    "passwd",
    "pw",
    "pwd",
    "private",
    "secret",
    "token",
)


def _is_secret_field(name: str) -> bool:
    """Return whether a config key should be excluded from the manifest."""
    lowered = name.lower()
    if lowered in {"krx_id", "krx_pw"}:
        return True
    return any(marker in lowered for marker in _SECRET_FIELD_MARKERS)


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
        action="store_true",
        default=False,
        help="팩터 캐시를 건너뛰고 재계산",
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
        default=argparse.SUPPRESS,
        help="모멘텀 계산용 선행 일수 (기본 252)",
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
        help="일일 손절 기준 (기본 -15%)",
    )
    run_parser.add_argument(
        "--max-holdings",
        type=int,
        default=argparse.SUPPRESS,
        help="최대 동시 보유 종목 수 (기본 20)",
    )
    run_parser.add_argument(
        "--sector-cap",
        type=float,
        default=argparse.SUPPRESS,
        help="섹션별 최대 노출 비율 (기본 0.30)",
    )
    run_parser.add_argument(
        "--min-adv-ratio",
        type=float,
        default=argparse.SUPPRESS,
        help="최소 유동성 비율 (기본 0.01)",
    )

    wf_parser = sub.add_parser("walkforward", help="5-fold walk-forward 교차 검증")
    wf_parser.add_argument(
        "--dart-api-key",
        default=argparse.SUPPRESS,
        help="OpenDartReader API 키",
    )
    wf_parser.add_argument(
        "--output",
        "-o",
        default=argparse.SUPPRESS,
        help="결과 출력 디렉토리 (기본값: outputs_k200mq)",
    )
    wf_parser.add_argument(
        "--top-n",
        type=int,
        default=argparse.SUPPRESS,
        help="선택 종목 수 (기본 20)",
    )
    wf_parser.add_argument(
        "--rebalance-freq",
        default=argparse.SUPPRESS,
        help="리밸런싱 주기: M(월간) 또는 Q(분기)",
    )
    wf_parser.add_argument(
        "--strict-pit",
        action="store_true",
        default=argparse.SUPPRESS,
        help="PIT 유니버스와 filing-date 재무 데이터가 없으면 중단",
    )

    return parser


def _build_config(args: argparse.Namespace) -> Any:
    """CLI 인자를 기반으로 K200MQConfig를 구성합니다."""
    from k200_mq.config import K200MQConfig

    if hasattr(args, "command") and args.command == "walkforward":
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
        "max_holdings": "MAX_HOLDINGS",
        "sector_cap": "SECTOR_CAP",
        "min_adv_ratio": "MIN_ADV_RATIO",
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
    print(f"  최대 보유: {config.MAX_HOLDINGS}")
    print(f"  모멘텀 가중치: {config.WEIGHT_MOMENTUM}")
    print(f"  품질 가중치: {config.WEIGHT_QUALITY}")
    print(f"  KOSPI 상위 제외: {config.EXCLUDE_KOSPI_TOP_N}")
    print(f"  DART API: {'설정됨' if config.DART_API_KEY else '미설정 (품질 팩터 비활성)'}")
    print(f"  Strict PIT 검증: {'활성' if config.STRICT_PIT_VALIDATION else '비활성'}")
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
        "momentum": "existing momentum definition retained; not changed in this pass",
        "adv": "ADV status: configured in K200MQ settings but not applied by the current engine",
        "sector_cap": "sector-cap status: configured in K200MQ settings but not applied by the current engine",
        "dart": (
            "missing DART mode: quality disabled and missing quality values filled with 0"
            if not dart_configured
            else "missing DART mode: not applicable; DART was configured"
        ),
    }
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
        "factors": context.get(
            "factors", {"row_count": 0, "ticker_count": 0},
        ),
        # ``first_scheduled_rebalance`` is the calendar schedule we inspect;
        # ``first_ready_rebalance`` is the first schedule allowed to trade.
        # Keep this distinction explicit because the first schedule may have
        # no factor rows during momentum warmup.
        "rebalance_readiness": context.get("rebalance_readiness", {}),
        "regime_map": context.get(
            "regime_map", {"covered_date_count": 0, "measured_date_count": 0},
        ),
        "quality": quality_context,
        "data_validity": {
            "strict_pit_validation": bool(
                getattr(config, "STRICT_PIT_VALIDATION", False)
            ),
            "universe": universe_validity,
            "financials": financial_validity,
        },
        "execution": {
            "signal_policy": "close signal",
            "entry_policy": "next open",
            "exit_policy": "next open",
        },
        "costs": costs,
        "git": _git_manifest_state(),
        "limitations": limitations,
    }


def _enforce_strict_pit_validation(
    universe_provenance: dict[str, Any],
    financial_provenance: dict[str, Any] | None = None,
    config: Any | None = None,
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


# ═══════════════════════════════════════════════════════════════════
# 메인 파이프라인
# ═══════════════════════════════════════════════════════════════════


def _run_pipeline(config: Any) -> None:
    """실제 백테스트 파이프라인을 실행합니다.

    Steps
    -----
    1. 유니버스 구성 (KOSPI 200 종속 이력)
    2. 가격 데이터 로드 (lookback 포함)
    3. 팩터 계산 (Momentum, Quality, Regime)
    4. PortfolioRebalanceEngine로 백테스트 실행
    5. 결과 저장
    """
    from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
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

    start_date = _parse_date(config.START_DATE) if isinstance(config.START_DATE, str) else config.START_DATE
    end_date = _parse_date(config.END_DATE) if isinstance(config.END_DATE, str) else config.END_DATE
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
    universe_history = get_kospi200_history(
        start_date, end_date, config.REBALANCE_FREQ,
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
    logger.info("2단계: 가격 데이터 로드 (252d lookback + ADV)")
    lookback_days = 252
    required_momentum_observations = max(
        config.MOMENTUM_WINDOW_LONG - config.MOMENTUM_SKIP_DAYS,
        config.MOMENTUM_WINDOW_SHORT,
    ) + 1
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

    # 4a. 모멘텀 팩터
    logger.info("  3a. 모멘텀 팩터 (12-7개월) 계산 중...")
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
            quality_factor = QualityFactor()
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

    # 4c. 리짓 필터 (KOSPI 200 지수)
    regime_filter_enabled = bool(getattr(config, "REGIME_FILTER_ENABLED", True))
    if regime_filter_enabled:
        logger.info("  3c. 리짓 필터 계산 중 (KOSPI 200 MA200)...")
        index_ticker = config.MARKET_INDEX_TICKER  # KPI200
        # MA200 needs roughly 200 trading observations before the first measured
        # date.  Use a deliberately conservative calendar window so weekends and
        # holidays do not leave the first measured regime silently incomplete.
        regime_history_days = max(config.REGIME_MA_PERIOD * 2, 365)
        regime_index_start = pd.Timestamp(start_date) - pd.Timedelta(days=regime_history_days)
        index_raw = get_market_index(index_ticker, regime_index_start.date(), end_date)

        if not index_raw.empty:
            regime_factor = RegimeFactor()
            index_for_regime = index_raw.reset_index()
            regime_df = regime_factor.compute(
                index_for_regime,
                ma_period=config.REGIME_MA_PERIOD,
                min_return_days=20,
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
        index_raw = pd.DataFrame()
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
    }
    manifest_context["rebalance_readiness"] = rebalance_readiness
    manifest_context["quality"] = quality_coverage
    manifest_context["financial_provenance"] = financial_provenance

    # ── 5. 백테스트 실행 ──────────────────────────────────────
    logger.info("5단계: PortfolioRebalanceEngine로 백테스트 실행")
    engine = PortfolioRebalanceEngine(config)
    results = engine.run(
        backtest_data, index_raw, factor_data, universe_history,
        regime_scale_map=regime_scale_map,
        measured_start=start_date,
        measured_end=end_date,
        active_trading_start=rebalance_readiness["measured_trading_readiness_date"],
    )
    results["_manifest_context"] = manifest_context

    # ── 6. 결과 저장 ──────────────────────────────────────────
    logger.info("6단계: 결과 저장")
    _save_results(results, config)

    # ── 7. 요약 출력 ──────────────────────────────────────────
    _print_summary(results, config)


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

    manifest = _build_run_manifest(
        config,
        results.get("_manifest_context"),
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
# Walk-Forward CV
# ═══════════════════════════════════════════════════════════════════


def _print_fold_metrics(label: str, daily_returns: pd.Series, trade_log: pd.DataFrame) -> dict[str, Any]:
    """단일 fold의 메트릭을 계산하고 출력합니다."""
    if daily_returns.empty or daily_returns.isna().all():
        print(f"  {label}: 데이터 없음")
        return {"label": label, "total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0, "win_rate": 0.0, "n_trades": 0}

    cum = (1 + daily_returns).cumprod()
    total_ret = cum.iloc[-1] - 1.0
    vol = daily_returns.std()
    n_years = len(daily_returns) / 252
    ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1
    ann_vol = vol * (252 ** 0.5)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    completed = trade_log[trade_log["return_pct"].notna()] if "return_pct" in trade_log.columns else pd.DataFrame()
    win_rate = (completed["return_pct"] > 0).mean() * 100 if len(completed) > 0 else 0.0

    print(f"  {label}: 수익률 {total_ret*100:+.2f}% | "
          f"Sharpe {sharpe:.3f} | MDD {max_dd*100:.2f}% | "
          f"승률 {win_rate:.1f}% | 거래 {len(completed)}건")

    return {
        "label": label,
        "total_return": total_ret,
        "ann_return": ann_ret,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "n_trades": len(completed),
    }


def _run_walkforward(config: Any) -> None:
    """5-fold walk-forward cross validation.

    각 fold는 독립적인 기간에 대해 백테스트를 실행하고,
    모든 fold의 결과를 집계하여 보고합니다.
    """
    base_output = Path(config.OUTPUT_DIR)
    wf_dir = base_output / "walkforward"

    today_str = date.today().isoformat()
    folds = [
        ("2014-01-01", "2016-12-31", "Fold 1: 2014-2016"),
        ("2017-01-01", "2018-12-31", "Fold 2: 2017-2018"),
        ("2019-01-01", "2020-12-31", "Fold 3: 2019-2020"),
        ("2021-01-01", "2022-12-31", "Fold 4: 2021-2022"),
        ("2023-01-01", today_str,     "Fold 5: 2023-2026"),
    ]

    all_daily_returns: list[pd.Series] = []
    all_trade_logs: list[pd.DataFrame] = []
    fold_metrics_list: list[dict[str, Any]] = []

    for start_str, end_str, label in folds:
        print(f"\n{'=' * 60}")
        print(f"  {label} ({start_str} ~ {end_str})")
        print(f"{'=' * 60}")

        fold_output = wf_dir / label.replace(":", "").replace(" ", "_")
        fold_config = config.model_copy(update={
            "START_DATE": start_str,
            "END_DATE": end_str,
            "OUTPUT_DIR": str(fold_output),
        })

        _run_pipeline(fold_config)

        trade_path = fold_output / "trade_log.csv"
        returns_path = fold_output / "daily_returns.csv"

        dr = pd.Series(dtype=float)
        if returns_path.exists():
            dr_df = pd.read_csv(returns_path)
            if "daily_return" in dr_df.columns:
                dr = dr_df["daily_return"]
                dr.index = pd.to_datetime(dr_df.iloc[:, 0]) if "date" in dr_df.columns else dr.index

        tl = pd.read_csv(trade_path) if trade_path.exists() else pd.DataFrame()

        all_daily_returns.append(dr)
        all_trade_logs.append(tl)

        metrics = _print_fold_metrics(label, dr, tl)
        fold_metrics_list.append(metrics)

    # ── 종합 집계 ──────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("  Walk-Forward CV — 접힘별 결과")
    print(f"{'=' * 60}")
    print(f"  {'Fold':<22} {'수익률':>10} {'Sharpe':>8} {'MDD':>8} {'승률':>8} {'거래':>6}")
    print(f"  {'-'*22} {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")

    for m in fold_metrics_list:
        print(f"  {m['label']:<22} {m['total_return']*100:>+9.2f}% "
              f"{m['sharpe']:>8.3f} {m['max_dd']*100:>7.2f}% "
              f"{m['win_rate']:>7.1f}% {m['n_trades']:>6d}")

    print(f"  {'─' * 22} {'─' * 10} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 6}")

    returns = [m["total_return"] for m in fold_metrics_list]
    sharpes = [m["sharpe"] for m in fold_metrics_list]
    mdds = [m["max_dd"] for m in fold_metrics_list]
    wins = [m["win_rate"] for m in fold_metrics_list]
    trades = [m["n_trades"] for m in fold_metrics_list]

    geo_mean_return = (1 + np.prod([1 + r for r in returns]) ** (1 / len(returns))) - 1 if returns else 0.0
    mean_sharpe = np.mean(sharpes)
    mean_mdd = np.min(mdds)
    mean_win = np.mean(wins)
    total_trades = sum(trades)

    print(f"  {'기하 평균':<22} {geo_mean_return*100:>+9.2f}% "
          f"{mean_sharpe:>8.3f} {mean_mdd*100:>7.2f}% "
          f"{mean_win:>7.1f}% {total_trades:>6d}")
    print(f"  {'Sharpe 범위':22} {min(sharpes):>7.3f} ~ {max(sharpes):>7.3f}")

    print(f"\n  전체 결과 저장: {base_output / 'walkforward_summary.csv'}")
    print(f"{'=' * 60}")

    # ── 요약 저장 ──────────────────────────────────────────
    summary_df = pd.DataFrame(fold_metrics_list)
    summary_path = base_output / "walkforward_summary.csv"
    summary_df.to_csv(summary_path, index=False)


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

    elif args.command == "walkforward":
        config = _build_config(args)
        _print_config_summary(config)
        logger.info("Walk-forward CV 시작...")
        _run_walkforward(config)
        logger.info("Walk-forward CV 완료.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
