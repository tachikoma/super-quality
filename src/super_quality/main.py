"""슈퍼 퀄리티 2.0 백테스팅용 CLI 진입점.

사용법
----
    uv run python -m super_quality.main --help
    uv run python -m super_quality.main run --dart-api-key=... \\
        --start 2015-01-01 --end 2025-12-31 --output ./outputs
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

import pandas as pd
from pathlib import Path
from typing import Any

from super_quality.analysis.metrics import PerformanceMetrics
from super_quality.backtest.engine import BacktestEngine
from super_quality.config import SuperQualityConfig
from super_quality.reporting.report import ReportGenerator

logger = logging.getLogger(__name__)


# ── 헬퍼 함수 ───────────────────────────────────────────────────────────


def _parse_date(s: str) -> date:
    """``YYYY-MM-DD`` 문자열을 :class:`datetime.date` 객체로 파싱합니다."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def _setup_logging() -> None:
    """stderr에 간단한 형식으로 로깅을 설정합니다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _print_summary(metrics: dict[str, Any]) -> None:
    """핵심 지표의 요약을 stdout에 출력합니다."""
    def pct(v: Any) -> str:
        try:
            val = float(v) * 100
            return f"{val:+.2f}%"
        except (TypeError, ValueError):
            return "N/A"

    def num(v: Any, d: int = 2) -> str:
        try:
            return f"{float(v):.{d}f}"
        except (TypeError, ValueError):
            return "N/A"

    print("\n" + "=" * 50)
    print("  Super Quality 2.0 — Backtest Results")
    print("=" * 50)
    print(f"  Total Return:     {pct(metrics.get('total_return', 0))}")
    print(f"  CAGR:             {pct(metrics.get('cagr', 0))}")
    print(f"  Sharpe Ratio:     {num(metrics.get('sharpe_ratio', 0))}")
    print(f"  Sortino Ratio:    {num(metrics.get('sortino_ratio', 0))}")
    print(f"  Volatility:       {pct(metrics.get('volatility', 0))}")
    print(f"  Max Drawdown:     {pct(metrics.get('max_drawdown', 0))}")
    print(f"  Win Rate:         {pct(metrics.get('win_rate', 0))}")
    print(f"  Profit Factor:    {num(metrics.get('profit_factor', 0))}")
    print(f"  Total Trades:     {int(metrics.get('total_trades', 0))}")
    print(f"  Avg Hold Days:    {num(metrics.get('avg_hold_days', 0), 1)}")
    print("=" * 50)


# ── 서브 커맨드: run ─────────────────────────────────────────────────


def _cmd_run(args: argparse.Namespace) -> None:
    """전체 백테스트 파이프라인을 실행합니다."""
    # 1. 설정 — CLI 인자가 있으면 전달하고, 없으면 .env 파일에서 읽도록 둠
    config_kwargs: dict[str, Any] = {
        "START_DATE": _parse_date(args.start),
        "END_DATE": _parse_date(args.end) if args.end else date.today(),
    }
    if args.dart_api_key:
        config_kwargs["DART_API_KEY"] = args.dart_api_key
    config = SuperQualityConfig(**config_kwargs)
    logger.info("설정이 로드되었습니다 (시작=%s, 종료=%s)", config.START_DATE, config.END_DATE)
    api_key = config.DART_API_KEY
    if not api_key:
        logger.warning(
            "DART_API_KEY가 설정되지 않았습니다. https://opendart.fss.or.kr 에서\n            API 키를 발급받아 --dart-api-key, DART_API_KEY 환경 변수 또는 .env 파일로 설정하십시오.\n        "
        )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 2. 데이터 수집
    logger.info("시장 데이터를 수집 중입니다…")
    try:
        from super_quality.data.loader import (
            get_financial_data,
            get_krx_listings,
            get_market_index,
            get_price_data,
            get_retail_net_buy,
            get_paid_in_capital_increases,
        )


        # 2a. 리스팅 → KOSPI + KOSDAQ 필터링 (시총 내림차순 정렬)
        all_listings = get_krx_listings()
        listings = all_listings[all_listings["market"].isin(["KOSPI", "KOSDAQ"])].copy()
        if "Marcap" in listings.columns:
            listings = listings.sort_values("Marcap", ascending=False)
        tickers: list[str] = listings["ticker"].tolist()
        logger.info("로드된 티커 수: %d", len(tickers))

        # 2a-2. DART 유효 티커 필터링 (우선주/ETF 제외)
        if api_key:
            try:
                import OpenDartReader  # type: ignore[import-untyped]
                dart = OpenDartReader(api_key)
                corp_codes = dart.corp_codes
                valid_stock_codes = set(
                    corp_codes.loc[
                        corp_codes["stock_code"].str.strip() != "", "stock_code"
                    ].tolist()
                )
                tickers = [t for t in tickers if t in valid_stock_codes]
                logger.info("DART 유효 티커로 필터링: %d개", len(tickers))
            except Exception as exc:
                logger.warning("DART 티커 필터링 실패 (%s), 전체 유니버스 사용", exc)
        else:
            logger.warning("DART API 키 없음 — 유효성 필터링 생략")

        # 재무/리테일/유증 분석 대상: 전체 유효 티커
        fin_tickers: list[str] = tickers
        logger.info("재무 데이터 대상: 유효 KOSPI/KOSDAQ (%d개)", len(fin_tickers))

        # 2b. 가격 데이터 + 시장 지수
        price_data = get_price_data(tickers, config.START_DATE, config.END_DATE)
        index_data = get_market_index(config.MARKET_TIMING_TICKER, config.START_DATE, config.END_DATE)

        # 2c. 재무 데이터 (백테스트 기간의 연도 범위)
        from_date: date = _parse_date(args.start)
        to_date: date = _parse_date(args.end) if args.end else date.today()
        years = list(range(from_date.year - 2, to_date.year + 1))
        financial_data = get_financial_data(fin_tickers, years, api_key=api_key)
        logger.info("로드된 재무 데이터: %d행", len(financial_data))

        # 2d. 개인 순매수 (티커별)
        supply_tickers = fin_tickers
        retail_frames: list[pd.DataFrame] = []
        for i, ticker in enumerate(supply_tickers):
            try:
                df = get_retail_net_buy(ticker, config.START_DATE, config.END_DATE)
                if not df.empty:
                    df["ticker"] = ticker
                    retail_frames.append(df)
                    logger.info("  개인 매수 데이터: %s (%d/%d)", ticker, i + 1, len(supply_tickers))
            except Exception:  # noqa: BLE001
                continue
        retail_buy = pd.concat(retail_frames, ignore_index=False) if retail_frames else pd.DataFrame()
        if not retail_buy.empty and "date" not in retail_buy.columns:
            retail_buy = retail_buy.reset_index()
        logger.info("로드된 개인 순매수 데이터: %d행", len(retail_buy))

        # 2e. 유상증자 일정 조회 (DART API)
        capital_increases: dict[str, list[date]] = {}
        logger.info("유상증자 일정을 조회 중입니다…")
        rate_limited = False
        for i, ticker in enumerate(fin_tickers):
            if rate_limited:
                continue
            try:
                dts = get_paid_in_capital_increases(ticker, years, api_key=api_key)
                capital_increases[ticker] = dts
                if dts:
                    logger.info("  유상증자 데이터: %s (일정 %d건)", ticker, len(dts))
            except Exception as e:
                if "020" in str(e):
                    logger.warning("  DART 사용한도 초과 — 유상증자 조회를 중단합니다")
                    rate_limited = True
                else:
                    logger.debug("  유상증자 조회 실패: %s (%s)", ticker, e)

    except Exception as exc:  # noqa: BLE001
        logger.error("데이터 수집 실패: %s", exc)
        sys.exit(1)


    # 3. 팩터 계산
    logger.info("팩터를 계산 중입니다…")
    try:
        from super_quality.factors.market_timing import KosdaqMAFactor
        from super_quality.factors.quality import GPAFactor
        from super_quality.factors.supply import RetailSupplyFactor
        from super_quality.factors.value import PBRFactor

        from super_quality.data.loader import (
            date_to_financial_epoch,
            get_financial_snapshot,
            compute_ttm_snapshot,
        )

        # 3a. factor_data: 티커 × 날짜 그리드
        factor_data = price_data.reset_index()[["ticker", "date"]].copy()
        factor_data["date"] = pd.to_datetime(factor_data["date"])

        # 3b. 날짜 → 재무 에포크 매핑
        all_epoch_dates = sorted(factor_data["date"].unique())
        date_epoch_map: dict = {}
        for d in all_epoch_dates:
            d_date = d.date() if hasattr(d, "date") else pd.Timestamp(d).date()
            ey, eq = date_to_financial_epoch(d_date)
            date_epoch_map[d] = ey * 10 + eq
        factor_data["_epoch"] = factor_data["date"].map(date_epoch_map)

        # 3c. 에포크별 횡단면 팩터 계산
        unique_epochs = sorted(factor_data["_epoch"].unique())
        epoch_factor_frames: list[pd.DataFrame] = []

        for epoch_key in unique_epochs:
            epoch_year, epoch_quarter = divmod(epoch_key, 10)

            # epoch 첫째 날을 as_of_date로 사용
            epoch_dates = factor_data.loc[
                factor_data["_epoch"] == epoch_key, "date"
            ]
            as_of_ts = epoch_dates.min()
            as_of_date = as_of_ts.date() if hasattr(as_of_ts, "date") else pd.Timestamp(as_of_ts).date()

            # 재무 스냅샷 (as_of_date 기준) — 데이터가 없으면 epoch 건너뜀
            fin_snap = get_financial_snapshot(financial_data, as_of_date)
            if fin_snap.empty:
                logger.warning("에포크 %d (%s): 재무 스냅샷 없음, 건너뜀", epoch_key, as_of_date)
                continue

            # epoch 첫째 날의 mcap을 스냅샷에 병합
            mcap_at_epoch = (
                price_data.xs(as_of_ts, level="date")["mcap"]
                .reset_index()
                .rename(columns={"mcap": "mcap_epoch"})
            )
            fin_snap = fin_snap.merge(mcap_at_epoch, on="ticker", how="left")

            # 횡단면 팩터 계산 (에포크별 1회)
            pbr_df = PBRFactor().compute(
                fin_snap.rename(columns={"mcap_epoch": "mcap"})
            )
            gpa_df = GPAFactor().compute(fin_snap)

            # TTM 계산 (as_of_date 기준)
            ttm_df = compute_ttm_snapshot(financial_data, as_of_date)

            # 하나로 합치기 (mcap_percentile은 일별 재계산하므로 epoch 단위 미포함)
            epoch_df = pbr_df.merge(gpa_df, on="ticker", how="left")
            if not ttm_df.empty:
                epoch_df = epoch_df.merge(ttm_df, on="ticker", how="left")
            else:
                epoch_df["trailing_ni"] = 0.0
                epoch_df["trailing_ocf"] = 0.0

            epoch_df["_epoch"] = epoch_key
            epoch_factor_frames.append(epoch_df)

        if epoch_factor_frames:
            epoch_factors_all = pd.concat(epoch_factor_frames, ignore_index=True)
            factor_data = factor_data.merge(
                epoch_factors_all, on=["ticker", "_epoch"], how="left"
            )
            # 티커별로 최신 epoch 값을 이후 날짜로 forward-fill
            factor_data = factor_data.sort_values(["ticker", "date"])
            for ck in ["pbr_percentile", "gpa_percentile", "trailing_ni", "trailing_ocf"]:
                if ck in factor_data.columns:
                    factor_data[ck] = factor_data.groupby("ticker")[ck].ffill()



        # MCAP 백분위 거래일별 재계산 (시총은 매일 변하므로)
        # price_data의 전체 티커를 대상으로 percentile 계산 (factor_data에
        # 재무 데이터가 없는 티커도 price_data에는 존재하므로 정확한 전일 대비 순위 산출)
        daily_mcap = price_data["mcap"].reset_index()
        daily_mcap["date"] = pd.to_datetime(daily_mcap["date"])
        factor_data = factor_data.merge(
            daily_mcap[["ticker", "date", "mcap"]], on=["ticker", "date"], how="left"
        )
        factor_data["mcap_percentile"] = (
            factor_data.groupby("date")["mcap"]
            .rank(pct=True, ascending=True)
            .fillna(50.0)
            * 100.0
        )

        # 3e. 공급 팩터 (티커 × 날짜)
        if not retail_buy.empty:
            supply_df = RetailSupplyFactor(
                supply_days=config.SUPPLY_SCORE_DAYS
            ).compute(retail_buy)
        else:
            supply_df = pd.DataFrame(columns=["ticker", "supply_score", "supply_percentile"])

        if not supply_df.empty:
            supply_merge = supply_df.rename(
                columns={"supply_score": "supply_score", "supply_percentile": "supply_percentile"}
            )
            supply_merge["ticker"] = supply_merge["ticker"].astype(str)
            factor_data["ticker"] = factor_data["ticker"].astype(str)
            factor_data = factor_data.merge(
                supply_merge[["ticker", "supply_score", "supply_percentile"]],
                on="ticker",
                how="left",
            )
        else:
            factor_data["supply_score"] = 0.0
            factor_data["supply_percentile"] = 50.0

        # 3f. 시장 타이밍 시그널
        market_ma = KosdaqMAFactor().compute(index_data["close"])
        market_signals = market_ma[["date", "buy_signal", "sell_signal"]].copy()

        # NaN 기본값 채움
        for col, default in [
            ("pbr_percentile", 50.0),
            ("gpa_percentile", 50.0),
            ("supply_percentile", 50.0),
            ("pbr", 1.0),
        ]:
            factor_data[col] = factor_data[col].fillna(default) if col in factor_data.columns else default

        # 유상증자 일정 반영 (티커 × 날짜별 동적 계산)
        factor_data["share_change_now"] = 0
        factor_data["share_change_5mo_ago"] = 0
        for tkr, dts in capital_increases.items():
            if not dts:
                continue
            mask = factor_data["ticker"] == tkr
            if not mask.any():
                continue
            dates = factor_data.loc[mask, "date"]
            dt_series = pd.to_datetime(dts)

            now_mask = pd.Series(False, index=dates.index)
            ago_mask = pd.Series(False, index=dates.index)
            for dt in dt_series:
                diff = (dates - dt).dt.days
                now_mask |= (diff >= 0) & (diff <= 90)
                ago_mask |= (diff >= 120) & (diff <= 180)

            factor_data.loc[dates[now_mask].index, "share_change_now"] = 1
            factor_data.loc[dates[ago_mask].index, "share_change_5mo_ago"] = 1

        for col in ["trailing_ni", "trailing_ocf"]:
            if col in factor_data.columns:
                factor_data[col] = factor_data[col].fillna(0.0)
            else:
                factor_data[col] = 0.0

        # 보조 컬럼 정리 (engine 전달 전에 제거)
        factor_data = factor_data.drop(columns=["_epoch", "mcap"], errors="ignore")

        # 시장 타이밍 시그널을 날짜별로 병합
        if "buy_signal" in market_signals.columns:
            signal_merge = market_signals.copy()
            signal_merge["date"] = pd.to_datetime(signal_merge["date"])
            factor_data = factor_data.merge(
                signal_merge[["date", "buy_signal", "sell_signal"]],
                on="date",
                how="left",
            )
            factor_data["buy_signal"] = factor_data["buy_signal"].fillna(False)
            factor_data["sell_signal"] = factor_data["sell_signal"].fillna(False)

        logger.info(
            "팩터 데이터 생성 완료: %d행 × %d열",
            len(factor_data),
            len(factor_data.columns),
        )

        # 진단: 조건별 통과 건수 출력
        try:
            from super_quality.strategies import SuperQualityStrategy
            diag_strategy = SuperQualityStrategy(config)
            diag = diag_strategy.evaluate_buy_conditions(factor_data)
            diag["date"] = factor_data["date"].values
            logger.info("=== 매수 조건 진단 ===")
            for cond in ["a_pass", "b_pass", "c_pass", "d_pass", "e_pass", "f_pass", "g_pass", "h_pass"]:
                cnt = int(diag[cond].sum())
                logger.info("  %s: %d / %d (%.1f%%)", cond, cnt, len(diag), cnt / len(diag) * 100)
            all_pass = int(diag["all_buy_conditions"].sum())
            logger.info("  all_buy_conditions: %d / %d (%.1f%%)", all_pass, len(diag), all_pass / len(diag) * 100)
            tickers_with_conditions = diag[diag["all_buy_conditions"]]["ticker"].nunique()
            logger.info("  통과 티커 수: %d", tickers_with_conditions)
            bs_dates = diag[diag["h_pass"]]["date"].nunique()
            logger.info("  buy_signal True 일수: %d / %d", bs_dates, diag["date"].nunique())
            # TTM 진단
            ttm_ni_valid = int(factor_data["trailing_ni"].notna().sum()) if "trailing_ni" in factor_data.columns else 0
            ttm_oc_valid = int(factor_data["trailing_ocf"].notna().sum()) if "trailing_ocf" in factor_data.columns else 0
            logger.info("  trailing_ni 유효: %d / %d", ttm_ni_valid, len(factor_data))
            logger.info("  trailing_ocf 유효: %d / %d", ttm_oc_valid, len(factor_data))
            if ttm_ni_valid > 0:
                ni_gt0 = int((factor_data["trailing_ni"] > 0).sum())
                oc_gt0 = int((factor_data["trailing_ocf"] > 0).sum())
                logger.info("  trailing_ni > 0: %d / %d", ni_gt0, ttm_ni_valid)
                logger.info("  trailing_ocf > 0: %d / %d", oc_gt0, ttm_oc_valid)
                logger.info("  trailing_ni 샘플: %s", factor_data.loc[factor_data["trailing_ni"] > 0, "trailing_ni"].head(3).tolist())
                logger.info("  trailing_ocf 샘플: %s", factor_data.loc[factor_data["trailing_ocf"] > 0, "trailing_ocf"].head(3).tolist())
        except Exception as diag_exc:
            logger.warning("진단 중 오류: %s", diag_exc)

    except Exception as exc:  # noqa: BLE001
        logger.error("팩터 계산 실패: %s", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 4. 백테스트 실행
    logger.info("백테스트 시뮬레이션을 실행 중입니다…")
    try:
        engine = BacktestEngine(config)
        if args.exclude_conditions:
            all_conds = {"a", "b", "c", "d", "e", "f", "g", "h"}
            exclude_set: set[str] = set()
            for token in args.exclude_conditions.replace(" ", "").split(","):
                if token == "ef":
                    exclude_set.update(["e", "f"])
                else:
                    exclude_set.add(token)
            active = all_conds - exclude_set
            logger.info("  활성 조건: %s (제외: %s)", ",".join(sorted(active)), args.exclude_conditions)
            strategy = SuperQualityStrategy(config, active_conditions=active)
            engine.set_strategy(strategy)
        result = engine.run(price_data, index_data, factor_data, financial_data)
    except Exception as exc:  # noqa: BLE001
        logger.error("백테스트 실패: %s", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    snapshots: Any = result.get("portfolio_snapshots", None)
    trade_log: Any = result.get("trade_log", None)
    daily_returns: Any = result.get("daily_returns", None)

    if daily_returns is None or daily_returns.empty:
        logger.warning("백테스트 결과에 거래일이 없습니다.")
        print("\n백테스트 기간 동안 거래가 발생하지 않았습니다.")
        return

    # 5. 지표 계산
    logger.info("성과 지표를 계산 중입니다…")
    metrics = PerformanceMetrics(daily_returns)
    all_metrics = metrics.compute_all(trade_log=trade_log)

    # 6. 보고서 생성
    logger.info("보고서를 생성 중입니다…")
    report_gen = ReportGenerator(str(output_dir))
    report_paths = report_gen.generate_all(all_metrics, snapshots, trade_log)

    # 7. 요약 출력
    _print_summary(all_metrics)
    for name, path in report_paths.items():
        logger.info("  %s → %s", name, path)


# ── CLI 진입점 ───────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 구성합니다."""
    parser = argparse.ArgumentParser(
        prog="super-quality",
        description="Super Quality 2.0 — 슈퍼 퀄리티 2.0 — 한국형 퀀트 백테스팅 시스템",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="전체 백테스트 파이프라인 실행")
    run_parser.add_argument(
        "--dart-api-key",
        default="",
        help="OpenDartReader API 키 (DART_API_KEY 환경 변수에서도 읽음)",
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
        "--output", "-o",
        default="outputs",
        help="보고서 출력 디렉토리 (기본값: outputs)",
    )
    run_parser.add_argument(
        "--exclude-conditions",
        default="",
        help="제외할 조건 (쉼표 구분, 예: --exclude-conditions g,ef)",
    )

    return parser


def main() -> None:
    """CLI 진입점입니다."""
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        _cmd_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()