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
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


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

    start_d = _parse_date(args.start)
    end_d = _parse_date(args.end) if args.end else date.today()

    config_kwargs: dict[str, Any] = {
        "START_DATE": start_d.isoformat() if start_d else args.start,
        "END_DATE": end_d.isoformat() if end_d else "today",
        "TOP_N": args.top_n,
        "REBALANCE_FREQ": args.rebalance_freq,
        "WEIGHT_MOMENTUM": args.weight_momentum,
        "WEIGHT_QUALITY": args.weight_quality,
        "EXCLUDE_KOSPI_TOP_N": args.exclude_kospi_top_n,
        "OUTPUT_DIR": args.output,
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
    print(f"  DART API: {'설정됨' if config.DART_API_KEY else '미설정 (품질 팩터 비활성)'}")
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

    records: list[dict[str, Any]] = []
    for _, row in financial_data.iterrows():
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
    return result


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
    from k200_mq.data.universe import get_kospi200_history
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
    universe_history = get_kospi200_history(
        start_date, end_date, config.REBALANCE_FREQ,
    )
    if universe_history.empty:
        logger.error("유니버스 데이터가 비어 있습니다. 백테스트를 중단합니다.")
        return

    all_tickers = sorted(universe_history["ticker"].unique().tolist())
    logger.info(
        "유니버스: %d개 리밸런싱 일자, %d개 고유 티커",
        universe_history["as_of"].nunique(),
        len(all_tickers),
    )

    # ── 2. 가격 데이터 로드 ───────────────────────────────────
    logger.info("2단계: 가격 데이터 로드 (252d lookback + ADV)")
    lookback_days = 252
    backtest_data, lookback_data = get_price_data_with_lookback(
        all_tickers, start_date, end_date, lookback_days=lookback_days,
    )

    if backtest_data.empty:
        logger.error("백테스트 기간 가격 데이터가 비어 있습니다.")
        return

    n_tickers_price = backtest_data.index.get_level_values("ticker").nunique()
    logger.info(
        "가격 데이터: backtest=%d행 (%d 티커), lookback=%d행",
        len(backtest_data), n_tickers_price, len(lookback_data),
    )

    # 전체 가격 데이터 (팩터 계산용 — lookback 포함)
    full_price = pd.concat([lookback_data, backtest_data]).sort_index()

    # 백테스트 기간 일별 영업일 목록
    backtest_dates = backtest_data.index.get_level_values("date").sort_values().unique()

    # ── 3. 팩터 계산 ──────────────────────────────────────────
    logger.info("3단계: 팩터 계산 (Momentum, Quality, Regime)")

    # 3a. 모멘텀 팩터
    logger.info("  3a. 모멘텀 팩터 (12-7개월) 계산 중...")
    momentum_factor = MomentumFactor()
    momentum_df = momentum_factor.compute(
        full_price,
        long_window=config.MOMENTUM_WINDOW_LONG,
        short_window=config.MOMENTUM_WINDOW_SHORT,
        skip_days=config.MOMENTUM_SKIP_DAYS,
    )
    logger.info("  모멘텀 팩터: %d행", len(momentum_df))

    # 3b. 품질 팩터 (DART API 필요)
    quality_df = pd.DataFrame()
    if config.DART_API_KEY:
        logger.info("  3b. 품질 팩터 계산 중 (DART API)...")
        try:
            years = list(range(start_date.year - 1, end_date.year + 1))
            financial_data = get_financial_data(
                all_tickers, years, api_key=config.DART_API_KEY,
            )
            logger.info("  재무 데이터: %d행", len(financial_data))

            if not financial_data.empty:
                daily_financial = _convert_financial_to_daily(
                    financial_data, full_price.index.get_level_values("date").unique(),
                )
                quality_factor = QualityFactor()
                quality_df = quality_factor.compute(
                    daily_financial,
                    min_ttm_quarters=config.QUALITY_MIN_TTM_QUARTERS,
                )
                logger.info("  품질 팩터: %d행", len(quality_df))
            else:
                logger.warning("  재무 데이터가 비어 있어 품질 팩터를 건너뜁니다.")
        except Exception as exc:
            logger.warning("  품질 팩터 계산 실패 (%s) — 모멘텀 전용으로 진행", exc)
    else:
        logger.info("  3b. DART API 키 미설정 — 품질 팩터 건너뜀 (모멘텀 전용)")

    # 3c. 리짓 필터 (KOSPI 200 지수)
    logger.info("  3c. 리짓 필터 계산 중 (KOSPI 200 MA200)...")
    index_ticker = config.MARKET_INDEX_TICKER  # KPI200
    index_raw = get_market_index(index_ticker, start_date, end_date)

    if not index_raw.empty:
        regime_factor = RegimeFactor()
        index_for_regime = index_raw.reset_index()
        regime_df = regime_factor.compute(
            index_for_regime,
            ma_period=config.REGIME_MA_PERIOD,
            min_return_days=20,
            reduction=config.REGIME_REDUCTION,
        )
        regime_scale_map = regime_df.set_index("date")["position_scale"].to_dict()
        logger.info(
            "  리짓: %d일 중 Bullish %d일 (%.1f%%)",
            len(regime_df),
            regime_df["regime"].sum(),
            regime_df["regime"].mean() * 100,
        )
    else:
        logger.warning("  KOSPI 200 지수 데이터 없음 — 리짓 필터 비활성")
        regime_scale_map = {}

    # ── 3d. 팩터 병합 ────────────────────────────────────────
    logger.info("  3d. 팩터 병합 중...")
    factor_data = momentum_df[["ticker", "date", "momentum_z"]].copy()

    if not quality_df.empty:
        quality_z_map = quality_df.set_index(["ticker", "date"])["quality_composite_z"]
        factor_data["quality_z"] = factor_data.apply(
            lambda r: quality_z_map.get((r["ticker"], r["date"]), 0.0),
            axis=1,
        )
    else:
        factor_data["quality_z"] = 0.0

    # 리짓 스케일 적용 (팩터 수준이 아닌 포트폴리오 수준에서 적용하므로 별도 보관)
    factor_data = factor_data[
        factor_data["date"].isin(pd.to_datetime(backtest_dates))
    ].copy()
    logger.info("  최종 팩터 데이터: %d행, %d개 고유 티커",
                len(factor_data),
                factor_data["ticker"].nunique())

    # ── 4. 백테스트 실행 ──────────────────────────────────────
    logger.info("4단계: PortfolioRebalanceEngine로 백테스트 실행")
    engine = PortfolioRebalanceEngine(config)
    results = engine.run(backtest_data, index_raw, factor_data, universe_history)

    # 리짓 스케일 적용 (NAV 보정)
    if regime_scale_map:
        snapshots = results["portfolio_snapshots"]
        if not snapshots.empty and "nav" in snapshots.columns:
            snapshots["regime_scale"] = snapshots["date"].map(regime_scale_map).fillna(1.0)
            snapshots["nav_adjusted"] = snapshots["nav"] * snapshots["regime_scale"]
            results["portfolio_snapshots"] = snapshots

    # ── 5. 결과 저장 ──────────────────────────────────────────
    logger.info("5단계: 결과 저장")
    _save_results(results, config)

    # ── 6. 요약 출력 ──────────────────────────────────────────
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

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
