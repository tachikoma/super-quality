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
import os
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
    # 1. 설정 — CLI 인자 우선, 없으면 환경 변수 사용
    api_key = args.dart_api_key or os.environ.get("DART_API_KEY", "")
    config = SuperQualityConfig(
        DART_API_KEY=api_key,
        START_DATE=_parse_date(args.start),
        END_DATE=_parse_date(args.end) if args.end else date.today(),
    )
    logger.info("설정이 로드되었습니다 (시작=%s, 종료=%s)", config.START_DATE, config.END_DATE)
    if not config.DART_API_KEY:
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
            get_kosdaq_index,
            get_krx_listings,
            get_price_data,
            get_retail_net_buy,
            get_paid_in_capital_increases,
        )


        # 2a. 리스팅 → 코스닥(KOSDAQ) 필터링
        all_listings = get_krx_listings()
        listings = all_listings[all_listings["market"] == "KOSDAQ"].copy()
        tickers: list[str] = listings["ticker"].tolist()
        logger.info("로드된 코스닥 티커 수: %d", len(tickers))

        # 2b. 가격 데이터 + 코스닥 지수
        price_data = get_price_data(tickers, config.START_DATE, config.END_DATE)
        kosdaq_data = get_kosdaq_index(config.START_DATE, config.END_DATE)

        # 2c. 재무 데이터 (백테스트 기간의 연도 범위)
        from_date: date = _parse_date(args.start)
        to_date: date = _parse_date(args.end) if args.end else date.today()
        years = list(range(from_date.year, to_date.year + 1))
        financial_data = get_financial_data(tickers[:50], years, api_key=api_key)  # API 속도 제한을 위해 일부 티커만 사용
        logger.info("로드된 재무 데이터: %d행", len(financial_data))

        # 2d. 개인 순매수 (티커별, API 호출을 줄이기 위해 시총+PBR로 필터링된 티커 사용)
        # 현재는 시연을 위해 상위 20개 티커만 샘플링
        supply_tickers = tickers[:20]
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
        for i, ticker in enumerate(tickers[:50]):
            try:
                dts = get_paid_in_capital_increases(ticker, years, api_key=api_key)
                capital_increases[ticker] = dts
                if dts:
                    logger.info("  유상증자 데이터: %s (일정 %d건)", ticker, len(dts))
            except Exception as e:
                logger.warning("  유상증자 조회 실패: %s (%s)", ticker, e)

    except Exception as exc:  # noqa: BLE001
        logger.error("데이터 수집 실패: %s", exc)
        sys.exit(1)


    # 3. 팩터 계산
    logger.info("팩터를 계산 중입니다…")
    try:
        from super_quality.factors.market_timing import KosdaqMAFactor
        from super_quality.factors.quality import GPAFactor, NewFScoreFactor
        from super_quality.factors.supply import RetailSupplyFactor
        from super_quality.factors.value import MarketCapFactor, PBRFactor

        # 3a. 재무 데이터로부터 티커별 최신 연간 스냅샷 생성
        fin_latest = financial_data.copy()
        if "year" in fin_latest.columns:
            fin_latest = (
                fin_latest.sort_values(["ticker", "year", "quarter"])
                .groupby("ticker")
                .last()
                .reset_index()
            )

        # 가격 데이터에서 시가총액(mcap) 병합 (최신 유효 mcap 사용)
        price_df = price_data.reset_index()
        # 미래/휴일 데이터는 mcap=0이므로 유효한 mcap이 있는 최근일 사용
        valid_mcap = price_df[price_df["mcap"] > 0]
        if not valid_mcap.empty:
            price_last_mcap = valid_mcap.groupby("ticker").last().reset_index()
        else:
            price_last_mcap = price_df.groupby("ticker").last().reset_index()
        fin_with_mcap = fin_latest.merge(
            price_last_mcap[["ticker", "mcap"]], on="ticker", how="left"
        )
        logger.info("재무 스냅샷: %d행", len(fin_with_mcap))

        # 3b. TTM 파생 컬럼 계산 (trailing_ni, trailing_ocf)
        # DART 데이터는 누적(thstrm_amount)이므로 단일 분기 값으로 변환 후 합산
        ttm_df = pd.DataFrame()
        if not financial_data.empty and {"ticker", "year", "quarter", "net_income", "operating_cf"}.issubset(
            financial_data.columns
        ):
            fin_sorted = financial_data.sort_values(["ticker", "year", "quarter"])
            ttm_rows: list[dict[str, Any]] = []
            for ticker, grp in fin_sorted.groupby("ticker"):
                grp = grp.tail(4)
                if len(grp) < 4:
                    continue
                vals = grp[["net_income", "operating_cf"]].values.astype(float)
                single_vals = vals.copy()
                for i in range(1, len(vals)):
                    single_vals[i] = vals[i] - vals[i - 1]
                ttm_rows.append({
                    "ticker": ticker,
                    "trailing_ni": float(single_vals.sum(axis=0)[0]),
                    "trailing_ocf": float(single_vals.sum(axis=0)[1]),
                })
            if ttm_rows:
                ttm_df = pd.DataFrame(ttm_rows)
                fin_with_mcap = fin_with_mcap.merge(ttm_df, on="ticker", how="left")
            else:
                fin_with_mcap["trailing_ni"] = 0.0
                fin_with_mcap["trailing_ocf"] = 0.0
        else:
            fin_with_mcap["trailing_ni"] = 0.0
            fin_with_mcap["trailing_ocf"] = 0.0
        # 유상증자 일정 반영 (최신 스냅샷 기준)
        fin_with_mcap["share_change_5mo_ago"] = 0
        fin_with_mcap["share_change_now"] = 0
        for idx, row in fin_with_mcap.iterrows():
            tkr = row["ticker"]
            dts = capital_increases.get(tkr, [])
            if dts:
                end_dt = pd.to_datetime(config.END_DATE)
                for dt in dts:
                    dt_pd = pd.to_datetime(dt)
                    diff = (end_dt - dt_pd).days
                    if 0 <= diff <= 90:
                        fin_with_mcap.at[idx, "share_change_now"] = 1
                    if 120 <= diff <= 180:
                        fin_with_mcap.at[idx, "share_change_5mo_ago"] = 1


        # 3c. 횡단면 팩터 계산 (티커당 하나의 값)
        pbr_df = PBRFactor().compute(fin_with_mcap)
        mcap_df = MarketCapFactor().compute(fin_with_mcap)
        gpa_df = GPAFactor().compute(fin_with_mcap)
        fscore_df = NewFScoreFactor().compute(fin_with_mcap)

        # 3c. 공급 팩터 (티커 × 날짜)
        if not retail_buy.empty:
            supply_df = RetailSupplyFactor(
                supply_days=config.SUPPLY_SCORE_DAYS
            ).compute(retail_buy)
        else:
            supply_df = pd.DataFrame(columns=["ticker", "supply_score", "supply_percentile"])

        # 3d. 코스닥 타이밍 시그널
        kosdaq_ma = KosdaqMAFactor().compute(kosdaq_data["close"])
        kosdaq_signals = kosdaq_ma[["date", "buy_signal", "sell_signal"]].copy()

        # 3e. factor_data: 티커 × 날짜 그리드 구성
        # price_data의 모든 티커-날짜 쌍으로 시작
        factor_data = price_data.reset_index()[["ticker", "date"]].copy()
        factor_data["date"] = pd.to_datetime(factor_data["date"])

        # 횡단면 팩터 병합 (티커당 동일 값)
        for df in [pbr_df, mcap_df, gpa_df, fscore_df]:
            if "ticker" in df.columns:
                factor_data = factor_data.merge(df, on="ticker", how="left")

        # TTM 값 병합 (trailing_ni, trailing_ocf — e_pass/f_pass 평가에 필요)
        if not ttm_df.empty:
            factor_data = factor_data.merge(ttm_df, on="ticker", how="left")

        # 공급 팩터 병합 (티커 × 날짜)
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

        # 나머지 NaN은 합리한 기본값으로 채움
        # 컬럼이 이미 존재하면 NaN만 채우고, 없으면 기본값으로 생성
        for col, default in [
            ("pbr_percentile", 50.0),
            ("mcap_percentile", 50.0),
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
            factor_data[col] = factor_data[col] if col in factor_data.columns else 0.0


        # 코스닥 타이밍 시그널을 날짜별로 병합
        if "buy_signal" in kosdaq_signals.columns:
            kosdaq_merge = kosdaq_signals.copy()
            kosdaq_merge["date"] = pd.to_datetime(kosdaq_merge["date"])
            factor_data = factor_data.merge(
                kosdaq_merge[["date", "buy_signal", "sell_signal"]],
                on="date",
                how="left",
            )
            factor_data["kosdaq_buy_signal"] = factor_data["buy_signal"].fillna(False)
            factor_data["kosdaq_sell_signal"] = factor_data["sell_signal"].fillna(False)
            factor_data.drop(columns=["buy_signal", "sell_signal"], inplace=True)

        logger.info(
            "팩터 데이터 생성 완료: %d행 × %d열",
            len(factor_data),
            len(factor_data.columns),
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("팩터 계산 실패: %s", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 4. 백테스트 실행
    logger.info("백테스트 시뮬레이션을 실행 중입니다…")
    try:
        engine = BacktestEngine(config)
        result = engine.run(price_data, kosdaq_data, factor_data, financial_data)
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