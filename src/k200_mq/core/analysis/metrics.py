"""Super Quality 2.0 백테스팅 성과 지표.

:class:`PerformanceMetrics`를 제공하여 일별 수익률 시리즈와
선택적 거래 로그로부터 포트폴리오 수준의 통계를 계산합니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from k200_mq.backtest.benchmark import (
    benchmark_metadata,
    build_price_return_benchmark,
)

# Compatibility names for callers that import benchmark construction from the
# analysis module.  The implementation lives in backtest.benchmark so the
# engine and metrics cannot drift apart.
build_benchmark_returns = build_price_return_benchmark
build_benchmark_price_returns = build_price_return_benchmark


def compute_cost_attribution(
    trade_log: pd.DataFrame | None = None,
    snapshots: pd.DataFrame | None = None,
    initial_capital: float | None = None,
    net_return: float | None = None,
) -> dict[str, float | None]:
    """Aggregate actual fill costs and notionals from a trade log.

    A buy fill contributes ``entry_*`` fields and a sell fill contributes
    ``exit_*`` fields.  Older logs without the additive fields are accepted
    and their buy/sell notionals are reconstructed from price and shares;
    unavailable cost components remain zero.  ``snapshots`` is used as a
    fallback for cumulative turnover when no fill log is available.

    ``total_turnover`` is the sum of buy and sell notionals.  When both sides
    are available, ``one_way_turnover`` is half of that amount.  If snapshots
    carry ``cumulative_cost``, its final observed value is used as the
    authoritative cumulative cost (including when the trade log is empty or
    from an older schema).  This function does not alter either input frame.
    """
    zero: dict[str, float | None] = {
        "commission": 0.0,
        "slippage": 0.0,
        "tax": 0.0,
        "total_cost": 0.0,
        "buy_notional": 0.0,
        "sell_notional": 0.0,
        "total_turnover": 0.0,
        "turnover": 0.0,
        "one_way_turnover": 0.0,
        "cost_fraction_initial_capital": 0.0,
        "net_return": net_return,
    }
    snapshot_cost: float | None = None
    snapshot_turnover: float | None = None
    if isinstance(snapshots, pd.DataFrame) and not snapshots.empty:
        if "cumulative_cost" in snapshots.columns:
            costs = pd.to_numeric(snapshots["cumulative_cost"], errors="coerce").dropna()
            if not costs.empty:
                snapshot_cost = float(costs.iloc[-1])
        if "executed_turnover" in snapshots.columns:
            turnover = pd.to_numeric(
                snapshots["executed_turnover"], errors="coerce"
            ).dropna()
            if not turnover.empty:
                snapshot_turnover = float(turnover.iloc[-1])

    if trade_log is None or not isinstance(trade_log, pd.DataFrame) or trade_log.empty:
        if snapshot_cost is not None:
            zero["total_cost"] = snapshot_cost
        if snapshot_turnover is not None:
            zero["total_turnover"] = snapshot_turnover
            zero["turnover"] = snapshot_turnover
            zero["one_way_turnover"] = snapshot_turnover / 2.0
        return _finalize_cost_attribution(zero, initial_capital)

    def _sum_column(column: str) -> float:
        if column not in trade_log.columns:
            return 0.0
        values = pd.to_numeric(trade_log[column], errors="coerce").fillna(0.0)
        return float(values.sum())

    zero["commission"] = _sum_column("entry_commission") + _sum_column("exit_commission")
    zero["slippage"] = _sum_column("entry_slippage") + _sum_column("exit_slippage")
    zero["tax"] = _sum_column("exit_tax")
    zero["total_cost"] = _sum_column("total_cost")
    zero["buy_notional"] = _sum_column("entry_notional")
    zero["sell_notional"] = _sum_column("exit_notional")

    # Keep attribution useful for pre-attribution trade logs.  New logs have
    # the columns above and therefore take the exact-filled path.
    if "entry_notional" not in trade_log.columns:
        if {"buy_price", "shares"}.issubset(trade_log.columns):
            buy_rows = trade_log[trade_log.get("sell_price", pd.Series(index=trade_log.index)).isna()]
            zero["buy_notional"] = float(
                (
                    pd.to_numeric(buy_rows["buy_price"], errors="coerce")
                    * pd.to_numeric(buy_rows["shares"], errors="coerce")
                ).fillna(0.0).sum()
            )
    if "exit_notional" not in trade_log.columns:
        if {"sell_price", "shares"}.issubset(trade_log.columns):
            sell_rows = trade_log[trade_log["sell_price"].notna()]
            zero["sell_notional"] = float(
                (
                    pd.to_numeric(sell_rows["sell_price"], errors="coerce")
                    * pd.to_numeric(sell_rows["shares"], errors="coerce")
                ).fillna(0.0).sum()
            )
    if "total_cost" not in trade_log.columns:
        zero["total_cost"] = float(zero["commission"] + zero["slippage"] + zero["tax"])

    zero["total_turnover"] = float(zero["buy_notional"] + zero["sell_notional"])
    zero["turnover"] = zero["total_turnover"]
    zero["one_way_turnover"] = zero["total_turnover"] / 2.0
    if snapshot_cost is not None:
        # The engine writes this running total from the same fill counters as
        # execution_stats.  Prefer it when present so attribution cannot
        # silently report zero for a schema that lacks per-fill cost columns.
        zero["total_cost"] = snapshot_cost
    return _finalize_cost_attribution(zero, initial_capital)


def _finalize_cost_attribution(
    values: dict[str, float | None],
    initial_capital: float | None,
) -> dict[str, float | None]:
    """Add the initial-capital cost ratio without changing zero-safe values."""
    try:
        capital = float(initial_capital) if initial_capital is not None else 0.0
    except (TypeError, ValueError):
        capital = 0.0
    values["cost_fraction_initial_capital"] = (
        float(values["total_cost"]) / capital if capital > 0.0 else 0.0
    )
    return values


class PerformanceMetrics:
    """포트폴리오 성과 지표 계산기.

    Parameters
    ----------
    daily_returns : pd.Series
        일별 포트폴리오 수익률, index = date (DatetimeIndex).
    risk_free_rate : float
        연간 무위험 수익률 (기본값 0.035 = 3.5 %).
    """

    def __init__(
        self,
        daily_returns: pd.Series,
        risk_free_rate: float = 0.035,
    ) -> None:
        self.daily_returns = daily_returns.astype(float)
        self.risk_free_rate = risk_free_rate
        self._benchmark_returns: pd.Series | None = None
        self._benchmark_metadata: dict[str, object] = {}

    # ── 공개 API ──────────────────────────────────────────────────────

    def set_benchmark(
        self,
        benchmark_returns: pd.Series,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """비교를 위한 벤치마크 수익률 시리즈를 설정합니다.

        Parameters
        ----------
        benchmark_returns : pd.Series
            벤치마크의 일별 수익률, index = date.
        """
        returns = benchmark_returns.astype(float).copy()
        if not isinstance(returns.index, pd.DatetimeIndex):
            returns.index = pd.DatetimeIndex(pd.to_datetime(returns.index, errors="coerce"))
        returns.index = returns.index.normalize()
        returns = returns[~returns.index.isna()].dropna()
        returns = returns[~returns.index.duplicated(keep="last")].sort_index()
        self._benchmark_returns = returns
        attrs = dict(getattr(benchmark_returns, "attrs", {}))
        source = str(
            (metadata or {}).get(
                "source",
                attrs.get("source", attrs.get("benchmark_source", "unknown")),
            )
        )
        self._benchmark_metadata = benchmark_metadata(source=source)
        self._benchmark_metadata.update(dict(metadata or {}))
        self._benchmark_metadata.setdefault(
            "benchmark_source", attrs.get("benchmark_source", source)
        )
        self._benchmark_metadata.setdefault(
            "type", attrs.get("type", attrs.get("benchmark_type", "price_return"))
        )
        self._benchmark_metadata.setdefault(
            "benchmark_type", attrs.get("benchmark_type", self._benchmark_metadata["type"])
        )
        self._benchmark_metadata.setdefault(
            "is_total_return", bool(attrs.get("is_total_return", False))
        )
        self._benchmark_metadata.setdefault(
            "total_return", bool(attrs.get("total_return", False))
        )
        self._benchmark_metadata.setdefault(
            "description", attrs.get("description", benchmark_metadata(source=source)["description"])
        )
        self._benchmark_metadata["available"] = not returns.empty
        self._benchmark_metadata["observation_count"] = int(len(returns))

    def compute_all(
        self,
        trade_log: pd.DataFrame | None = None,
        snapshots: pd.DataFrame | None = None,
        initial_capital: float | None = None,
        net_return: float | None = None,
    ) -> dict:
        """모든 성과 지표를 계산합니다.

        Parameters
        ----------
        trade_log : pd.DataFrame or None
            :class:`BacktestEngine`의 거래 로그로, 컬럼은
            ``entry_date``, ``exit_date``, ``ticker``, ``buy_price``,
            ``sell_price``, ``shares``, ``return_pct``, ``hold_days``,
            ``exit_reason``입니다. 완료된 거래만(non-null
            ``exit_date``) 거래 통계에 사용됩니다.
        snapshots : pd.DataFrame or None
            Optional portfolio snapshots used as a turnover fallback.
        initial_capital : float or None
            Capital base for the cost fraction attribution.
        net_return : float or None
            Optional externally supplied net return.  If omitted, the
            computed portfolio total return is recorded.

        Returns
        -------
        dict
            Keys: ``total_return``, ``cagr``, ``volatility``,
            ``sharpe_ratio``, ``sortino_ratio``, ``max_drawdown``,
            ``max_drawdown_duration``, ``win_rate``, ``profit_factor``,
            ``total_trades``, ``avg_hold_days``, ``monthly_returns``,
            ``yearly_returns``, ``benchmark_comparison``.
        """
        attribution_return = net_return
        if attribution_return is None and not self.daily_returns.empty:
            attribution_return = float((1.0 + self.daily_returns).prod() - 1.0)
        attribution = compute_cost_attribution(
            trade_log,
            snapshots=snapshots,
            initial_capital=initial_capital,
            net_return=attribution_return,
        )
        if self.daily_returns.empty:
            empty_metrics = self._empty_metrics()
            empty_metrics["cost_attribution"] = attribution
            if self._benchmark_metadata:
                empty_metrics["benchmark"] = dict(self._benchmark_metadata)
            return empty_metrics

        # ── 수익률 기반 지표 ─────────────────────────────────────────
        n = len(self.daily_returns)
        years = n / 252.0

        total_return = float((1.0 + self.daily_returns).prod() - 1.0)
        cagr = self._compute_cagr(total_return, years)

        nav = (1.0 + self.daily_returns).cumprod()
        vol = self._compute_volatility()
        sharpe = self._compute_sharpe()
        sortino = self._compute_sortino()
        max_dd = self._compute_max_drawdown(nav)
        max_dd_dur = self._compute_max_drawdown_duration(nav)

        monthly = self._compute_period_returns(freq="ME")
        yearly = self._compute_period_returns(freq="YE")

        # ── 거래 기반 지표 ───────────────────────────────────────────
        trade_stats = self._compute_trade_stats(trade_log)

        # ── 벤치마크 비교 ─────────────────────────────────────────────
        bench = self._compute_benchmark_comparison()

        return {
            "total_return": total_return,
            "cagr": cagr,
            "volatility": vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "max_drawdown_duration": max_dd_dur,
            "win_rate": trade_stats["win_rate"],
            "profit_factor": trade_stats["profit_factor"],
            "total_trades": trade_stats["total_trades"],
            "avg_hold_days": trade_stats["avg_hold_days"],
            "monthly_returns": monthly,
            "yearly_returns": yearly,
            "benchmark_comparison": bench,
            "cost_attribution": attribution,
            "benchmark": dict(self._benchmark_metadata),
        }

    # ── 내부 계산 ─────────────────────────────────────────────────────

    @staticmethod
    def _compute_cagr(total_return: float, years: float) -> float:
        """연평균 성장률(CAGR).

        Parameters
        ----------
        total_return : float
            전체 기간의 총 수익률 (예: 0.25 = 25 %).
        years : float
            경과된 역년 수.
        """
        if years <= 0.0:
            return 0.0
        return (1.0 + total_return) ** (1.0 / years) - 1.0

    def _compute_volatility(self) -> float:
        """연율화 변동성."""
        if len(self.daily_returns) < 2:
            return 0.0
        return float(self.daily_returns.std(ddof=1) * np.sqrt(252.0))

    def _compute_sharpe(self) -> float:
        """연율화 Sharpe ratio."""
        if len(self.daily_returns) < 2:
            return 0.0
        ann_ret = float(self.daily_returns.mean() * 252.0)
        ann_vol = self._compute_volatility()
        if ann_vol <= 0.0:
            return 0.0
        return (ann_ret - self.risk_free_rate) / ann_vol

    def _compute_sortino(self) -> float:
        """연율화 Sortino ratio (하방 편차만)."""
        if len(self.daily_returns) < 2:
            return 0.0
        ann_ret = float(self.daily_returns.mean() * 252.0)
        downside = self.daily_returns[self.daily_returns < 0.0]
        if len(downside) < 2:
            return 0.0
        downside_dev = float(downside.std(ddof=1) * np.sqrt(252.0))
        if downside_dev <= 0.0:
            return 0.0
        return (ann_ret - self.risk_free_rate) / downside_dev

    @staticmethod
    def _compute_max_drawdown(nav: pd.Series) -> float:
        """최대 고점-저점 낙폭(MDD, 음수 값)."""
        running_max = nav.expanding().max()
        drawdown = nav / running_max - 1.0
        return float(drawdown.min())

    @staticmethod
    def _compute_max_drawdown_duration(nav: pd.Series) -> int:
        """최대 낙폭에서 회복하는 데 걸린 영업일 수.

        저점 이전의 고점에서 NAV가 그 고점 수준으로 회복하는 날짜까지의
        영업일 수를 반환합니다. 아직 회복 중이면 마지막 날짜까지의 길이를 반환합니다.
        """
        if len(nav) < 2:
            return 0

        running_max = nav.expanding().max()
        drawdown = nav / running_max - 1.0

        # 저점 날짜
        trough_idx = drawdown.idxmin()
        trough_val = drawdown.min()

        if trough_val >= 0.0:
            return 0

        # 저점 시점의 고점 값 (그 시점의 누적 최대값)
        peak_before = running_max.loc[trough_idx]

        # NAV == peak_before인 가장 최근 날짜(고점 날짜) 찾기
        peak_mask = (nav >= peak_before) & (nav.index <= trough_idx)
        if not peak_mask.any():
            return len(nav.loc[:trough_idx]) - 1

        peak_date = nav[peak_mask].index[-1]

        # 저점 이후 NAV가 peak_before로 회복하는 첫 번째 날짜 찾기
        recovery_mask = (nav >= peak_before) & (nav.index > trough_idx)
        if recovery_mask.any():
            recovery_date = nav[recovery_mask].index[0]
            return int(len(nav.loc[peak_date:recovery_date]) - 1)
        else:
            return int(len(nav.loc[peak_date:]) - 1)

    def _compute_period_returns(self, freq: str = "ME") -> pd.DataFrame:
        """월별/연별 수익률을 계산합니다.

        Parameters
        ----------
        freq : str
            ``"ME"``는 월말, ``"YE"``는 연말.

        Returns
        -------
        pd.DataFrame
            Index는 레이블(년-월 str 또는 연도 int), 컬럼은
            ``"return"``.
        """
        if self.daily_returns.empty:
            return pd.DataFrame(columns=["return"])

        grouped = self.daily_returns.groupby(pd.Grouper(freq=freq))
        period_ret = grouped.apply(lambda x: float((1.0 + x).prod() - 1.0))  # noqa: PD015

        if freq == "ME":
            labels = period_ret.index.strftime("%Y-%m")
        else:
            labels = period_ret.index.year.astype(str)

        return pd.DataFrame({"return": period_ret.values}, index=labels)

    def _compute_trade_stats(
        self,
        trade_log: pd.DataFrame | None,
    ) -> dict:
        """승률, profit factor, 평균 보유일을 계산합니다.

        Parameters
        ----------
        trade_log : pd.DataFrame or None
            완료된 거래가 있는 거래 로그.

        Returns
        -------
        dict with keys ``total_trades``, ``win_rate``, ``profit_factor``,
        ``avg_hold_days``.
        """
        if trade_log is None or trade_log.empty:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_hold_days": 0.0,
            }

        # 완료된 거래만 고려 (exit_date와 return_pct가 있는 경우)
        cols = ["exit_date", "return_pct"]
        if not all(c in trade_log.columns for c in cols):
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_hold_days": 0.0,
            }

        completed = trade_log[
            trade_log["exit_date"].notna() & trade_log["return_pct"].notna()
        ].copy()

        total_trades = len(completed)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_hold_days": 0.0,
            }

        win_rate = float((completed["return_pct"] > 0.0).mean())
        gains = float(completed.loc[completed["return_pct"] > 0.0, "return_pct"].sum())
        losses = float(completed.loc[completed["return_pct"] < 0.0, "return_pct"].sum())
        profit_factor: float = 0.0
        if losses != 0.0:
            profit_factor = gains / abs(losses)

        hold_col = "hold_days"
        if hold_col in completed.columns:
            valid_hold = completed[hold_col].dropna()
            avg_hold = float(valid_hold.mean()) if len(valid_hold) > 0 else 0.0
        else:
            avg_hold = 0.0

        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_hold_days": avg_hold,
        }

    def _compute_benchmark_comparison(self) -> dict:
        """포트폴리오 수익률을 벤치마크와 비교합니다.

        Returns
        -------
        dict
            Keys ``alpha``, ``beta``, ``correlation``, ``tracking_error``.
            벤치마크가 설정되지 않은 경우 빈 dict.
        """
        if self._benchmark_returns is None or self.daily_returns.empty:
            return {}

        # 날짜 기준 정렬
        combined = pd.concat(
            [self.daily_returns, self._benchmark_returns],
            axis=1,
            join="inner",
            keys=["portfolio", "benchmark"],
        )
        if len(combined) < 2:
            return {}

        p = combined["portfolio"]
        b = combined["benchmark"]

        cov = float(np.cov(p, b, ddof=1)[0, 1])
        var_b = float(np.var(b, ddof=1))
        beta = cov / var_b if var_b > 0 else 0.0

        port_ann = float(p.mean() * 252.0)
        bench_ann = float(b.mean() * 252.0)
        alpha = port_ann - self.risk_free_rate - beta * (bench_ann - self.risk_free_rate)

        corr = float(p.corr(b))
        tracking_error = float((p - b).std(ddof=1) * np.sqrt(252.0))

        return {
            "alpha": alpha,
            "beta": beta,
            "correlation": corr,
            "tracking_error": tracking_error,
        }

    # ── 헬퍼 ──────────────────────────────────────────────────────────

    def _empty_metrics(self) -> dict:
        """빈 수익률 시리즈에 대해 0으로 채워진 지표를 반환합니다."""
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "volatility": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_duration": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "avg_hold_days": 0.0,
            "monthly_returns": pd.DataFrame(columns=["return"]),
            "yearly_returns": pd.DataFrame(columns=["return"]),
            "benchmark_comparison": {},
            "cost_attribution": compute_cost_attribution(),
            "benchmark": {},
        }
