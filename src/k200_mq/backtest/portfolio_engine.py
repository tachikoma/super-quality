"""포트폴리오 리밸런싱 엔진.

KOSPI 200 Momentum + Quality 전략의 일별 시뮬레이션 루프를 구현합니다.
시그널은 종가에 형성되고, 주문은 다음 이용 가능한 바의 시가에만
체결됩니다.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from k200_mq.config import K200MQConfig
from k200_mq.backtest.benchmark import benchmark_metadata, build_price_return_benchmark
from k200_mq.strategies.momentum_quality import MomentumQualityStrategy

logger = logging.getLogger(__name__)


class PortfolioRebalanceEngine:
    """KOSPI 200 Momentum + Quality 리밸런싱 엔진.

    ``price_data`` is the measured range only.  Historical warmup rows used
    by factors must not be passed to this method; ``measured_start`` and
    ``measured_end`` are optional guards for callers that retain them in the
    same frame.

    The execution contract is deliberately small and explicit:

    * a close-*t* rebalance or stop signal becomes a pending order;
    * the pending order executes at the next available bar open;
    * sells execute before buys on that bar; and
    * no order is created from the final close because it has no next bar.
    """

    def __init__(
        self,
        config: K200MQConfig,
        kospi_mcap_ranking: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config
        self.strategy = MomentumQualityStrategy(config, kospi_mcap_ranking)
        self._execution_stats = self._zero_execution_stats()

    def run(
        self,
        price_data: pd.DataFrame,
        index_data: pd.DataFrame,
        factor_data: pd.DataFrame,
        universe_data: pd.DataFrame,
        regime_scale_map: dict[Any, float] | None = None,
        measured_start: date | pd.Timestamp | None = None,
        measured_end: date | pd.Timestamp | None = None,
        active_trading_start: date | pd.Timestamp | None = None,
    ) -> dict[str, Any]:
        """백테스트 시뮬레이션을 실행합니다.

        Parameters
        ----------
        price_data : pd.DataFrame
            MultiIndex ``(ticker, date)`` with ``open``, ``high``, ``low``,
            ``close``, ``volume``, ``mcap`` columns.
        index_data : pd.DataFrame
            ``date`` index, ``close`` column (KOSPI 200 index).
        factor_data : pd.DataFrame
            팩터 스코어 DataFrame (ticker, date, momentum_z, quality_z).
        universe_data : pd.DataFrame
            리밸런싱 일자별 유니버스 (as_of, ticker).
        regime_scale_map : dict, optional
            Signal-date to exposure-scale mapping.
        measured_start, measured_end : date or Timestamp, optional
            Explicit measured interval.  Rows outside it are warmup rows and
            are excluded from snapshots and trading.
        active_trading_start : date or Timestamp, optional
            Earliest measured signal date on which a rebalance or stop signal
            may be formed.  Snapshots before this date remain flat, allowing
            factor warmup to skip early scheduled rebalances.

        Returns
        -------
        dict
            ``portfolio_snapshots``, ``trade_log``, ``daily_returns``.
        """
        self._execution_stats = self._zero_execution_stats()
        start_ts = pd.Timestamp(measured_start).normalize() if measured_start is not None else None
        end_ts = pd.Timestamp(measured_end).normalize() if measured_end is not None else None
        all_dates = self._measured_price_dates(price_data)
        if start_ts is not None:
            all_dates = all_dates[all_dates >= start_ts]
        if end_ts is not None:
            all_dates = all_dates[all_dates <= end_ts]
        if start_ts is None and len(all_dates):
            start_ts = all_dates[0]
        if end_ts is None and len(all_dates):
            end_ts = all_dates[-1]

        source = str(getattr(self.config, "MARKET_INDEX_TICKER", "KPI200"))
        benchmark_returns = build_price_return_benchmark(
            index_data,
            source=source,
            measured_start=(measured_start if measured_start is not None else start_ts),
            measured_end=(measured_end if measured_end is not None else end_ts),
        )
        benchmark_info = benchmark_metadata(
            source=source,
            available=not benchmark_returns.empty,
            observation_count=len(benchmark_returns),
        )

        if price_data.empty or not isinstance(price_data.index, pd.MultiIndex):
            return self._empty_result(benchmark_returns, benchmark_info)

        active_start_ts = (
            pd.Timestamp(active_trading_start).normalize()
            if active_trading_start is not None
            else None
        )

        if len(all_dates) < 2:
            return self._empty_result(benchmark_returns, benchmark_info)

        # Make date lookups robust to a source using datetime.date and to
        # month-end dates that fall on weekends/holidays.
        rebalance_lookup = self._build_universe_lookup(universe_data, all_dates)
        if not rebalance_lookup:
            logger.warning("리밸런싱 일자가 없습니다.")
            return self._empty_result(benchmark_returns, benchmark_info)

        date_ordinal = {pd.Timestamp(d): i for i, d in enumerate(all_dates)}
        cash = float(self.config.INITIAL_CAPITAL)
        positions: dict[str, dict[str, Any]] = {}
        trade_log: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []

        # Pending orders are intentionally represented as plain dictionaries
        # to keep this engine's public surface small.
        pending_rebalance: dict[str, Any] | None = None
        pending_stops: dict[str, dict[str, Any]] = {}

        for i, current_date in enumerate(all_dates):
            current_ts = pd.Timestamp(current_date)

            # Orders formed at the prior close execute before any new signal
            # is formed at today's close.
            if i > 0:
                stop_tickers = set(pending_stops)
                cash, pending_stops = self._execute_pending_stops(
                    positions,
                    cash,
                    pending_stops,
                    price_data,
                    current_ts,
                    trade_log,
                    date_ordinal,
                )
                if pending_rebalance is not None:
                    cash = self._execute_rebalance(
                        positions,
                        cash,
                        pending_rebalance,
                        price_data,
                        current_ts,
                        trade_log,
                        date_ordinal,
                        stop_tickers,
                    )
                    pending_rebalance = None

            # Close-based stop detection.  It creates a pending order only if
            # a next bar exists, so the final close can never fabricate a
            # trade.
            if i + 1 < len(all_dates) and (
                active_start_ts is None or current_ts >= active_start_ts
            ):
                self._queue_stop_signals(
                    positions,
                    price_data,
                    current_ts,
                    pending_stops,
                )

                if current_ts in rebalance_lookup:
                    pending_rebalance = self._form_rebalance_signal(
                        current_ts,
                        price_data,
                        factor_data,
                        rebalance_lookup[current_ts],
                        regime_scale_map,
                    )

            holdings_value = self._holdings_value(
                positions, price_data, current_ts, "close"
            )
            nav = cash + holdings_value
            snapshots.append({
                "date": current_date,
                "cash": cash,
                "holdings_value": holdings_value,
                "nav": nav,
                "num_positions": len(positions),
                "cumulative_cost": self._execution_stats["total_cost"],
                "executed_turnover": self._execution_stats["executed_turnover"],
            })

        snapshots_df = pd.DataFrame(snapshots)
        if snapshots_df.empty:
            daily_returns = pd.Series(dtype=float)
        else:
            snapshots_df["daily_return"] = snapshots_df["nav"].pct_change().fillna(0.0)
            daily_returns = snapshots_df.set_index("date")["daily_return"]

        trade_log_df = pd.DataFrame(trade_log, columns=self._trade_columns())
        return {
            "portfolio_snapshots": snapshots_df,
            "trade_log": trade_log_df,
            "daily_returns": daily_returns,
            "benchmark_returns": benchmark_returns,
            "benchmark": benchmark_info,
            "execution_stats": {
                **self._execution_stats,
                "initial_capital": float(self.config.INITIAL_CAPITAL),
            },
        }

    @staticmethod
    def _measured_price_dates(price_data: pd.DataFrame) -> pd.DatetimeIndex:
        """Return normalized dates available to define the measured interval."""
        if price_data is None or price_data.empty:
            return pd.DatetimeIndex([])

        raw_dates: Any = None
        if "date" in price_data.columns:
            raw_dates = price_data["date"]
        elif isinstance(price_data.index, pd.MultiIndex) and "date" in price_data.index.names:
            raw_dates = price_data.index.get_level_values("date")
        elif price_data.index.name == "date" or isinstance(price_data.index, pd.DatetimeIndex):
            raw_dates = price_data.index
        if raw_dates is None:
            return pd.DatetimeIndex([])

        dates = pd.DatetimeIndex(pd.to_datetime(raw_dates, errors="coerce"))
        if dates.tz is not None:
            dates = dates.tz_localize(None)
        dates = dates.normalize()
        dates = dates[~dates.isna()]
        return dates.unique().sort_values()

    def _build_universe_lookup(
        self,
        universe_data: pd.DataFrame,
        trading_dates: pd.DatetimeIndex | None = None,
    ) -> dict[pd.Timestamp, list[str]]:
        """Map each calendar signal to at most one available trading date.

        A month-end that is a weekend or exchange holiday is mapped to the
        latest available bar on or before that date.  The resulting signal is
        still executed at the *next* available bar open by :meth:`run`.
        """
        lookup: dict[pd.Timestamp, list[str]] = {}
        if universe_data is None or universe_data.empty:
            return lookup
        if "as_of" not in universe_data.columns or "ticker" not in universe_data.columns:
            return lookup

        available = trading_dates if trading_dates is not None else pd.DatetimeIndex([])
        for as_of, group in universe_data.groupby("as_of"):
            signal_date = pd.Timestamp(as_of).normalize()
            if len(available):
                prior_dates = available[available <= signal_date]
                if len(prior_dates) == 0:
                    continue
                signal_date = pd.Timestamp(prior_dates[-1]).normalize()

            tickers = [str(ticker) for ticker in group["ticker"].dropna().tolist()]
            existing = lookup.setdefault(signal_date, [])
            existing.extend(ticker for ticker in tickers if ticker not in existing)

        return lookup

    def _price(
        self,
        price_data: pd.DataFrame,
        ticker: str,
        ts: pd.Timestamp | None,
        col: str,
    ) -> float | None:
        """특정 티커와 날짜의 가격을 조회합니다."""
        if ts is None:
            return None
        try:
            val = price_data.loc[(ticker, ts), col]
            if isinstance(val, pd.Series):
                val = val.iloc[-1]
            return float(val)
        except (KeyError, ValueError, TypeError):
            return None

    def _holdings_value(
        self,
        positions: dict[str, dict[str, Any]],
        price_data: pd.DataFrame,
        ts: pd.Timestamp,
        column: str,
    ) -> float:
        value = 0.0
        for ticker, position in positions.items():
            price = self._price(price_data, ticker, ts, column)
            if price is not None and price > 0:
                value += position["shares"] * price
        return value

    def _queue_stop_signals(
        self,
        positions: dict[str, dict[str, Any]],
        price_data: pd.DataFrame,
        signal_date: pd.Timestamp,
        pending_stops: dict[str, dict[str, Any]],
    ) -> None:
        """Queue close-based stops for execution on the next bar open."""
        if not bool(getattr(self.config, "ENABLE_STOP_LOSS", True)):
            return
        for ticker, position in positions.items():
            current_close = self._price(price_data, ticker, signal_date, "close")
            if current_close is None or current_close <= 0:
                continue

            peak = position.get("peak_price", position["entry_price"])
            if current_close > peak:
                position["peak_price"] = current_close
                peak = current_close

            trail_pct = getattr(self.config, "SL_STOP_LOSS", -0.15)
            stop_level = peak * (1.0 + trail_pct)
            if current_close <= stop_level and ticker not in pending_stops:
                pending_stops[ticker] = {"signal_date": signal_date}

    def _execute_pending_stops(
        self,
        positions: dict[str, dict[str, Any]],
        cash: float,
        pending_stops: dict[str, dict[str, Any]],
        price_data: pd.DataFrame,
        execution_date: pd.Timestamp,
        trade_log: list[dict[str, Any]],
        date_ordinal: dict[pd.Timestamp, int],
    ) -> tuple[float, dict[str, dict[str, Any]]]:
        """Execute queued stop sells at the current bar's open."""
        remaining: dict[str, dict[str, Any]] = {}
        for ticker, order in pending_stops.items():
            if ticker not in positions:
                continue
            sell_price = self._price(price_data, ticker, execution_date, "open")
            if sell_price is None or sell_price <= 0:
                remaining[ticker] = order
                continue
            cash = self._sell_position(
                positions,
                cash,
                ticker,
                sell_price,
                "stop_loss",
                order["signal_date"],
                execution_date,
                trade_log,
                date_ordinal,
            )
        return cash, remaining

    def _form_rebalance_signal(
        self,
        signal_date: pd.Timestamp,
        price_data: pd.DataFrame,
        factor_data: pd.DataFrame,
        universe: list[str],
        regime_scale_map: dict[Any, float] | None,
    ) -> dict[str, Any]:
        """Form a close-time target; execution is deferred to next open."""
        del price_data  # Kept in the signature to make the signal boundary explicit.
        if "date" in factor_data.columns:
            factor_at_date = factor_data[
                factor_data["date"] == signal_date
            ]
        else:
            factor_at_date = factor_data

        selected = self.strategy.select_portfolio(
            factor_data=factor_at_date,
            universe=universe,
            as_of=signal_date,
        )
        return {
            "signal_date": signal_date,
            "selected": selected,
            "regime_scale": self._regime_scale(regime_scale_map, signal_date),
        }

    def _regime_scale(
        self,
        regime_scale_map: dict[Any, float] | None,
        signal_date: pd.Timestamp,
    ) -> float:
        if not regime_scale_map:
            return 1.0
        value = regime_scale_map.get(signal_date)
        if value is None:
            value = regime_scale_map.get(signal_date.date())
        if value is None or pd.isna(value):
            return 1.0
        return float(value)

    def _execute_rebalance(
        self,
        positions: dict[str, dict[str, Any]],
        cash: float,
        order: dict[str, Any],
        price_data: pd.DataFrame,
        execution_date: pd.Timestamp,
        trade_log: list[dict[str, Any]],
        date_ordinal: dict[pd.Timestamp, int],
        blocked_tickers: set[str],
    ) -> float:
        """Resize the portfolio to the close-time target at the next open.

        Missing next-open data is an explicit no-fill policy: a new order is
        skipped, while an existing position is retained.  This avoids
        inventing a price for sparse ticker histories.  All reductions are
        completed before increases, and buy requests share one affordability
        factor so their result does not depend on the selected-list order.
        """
        signal_date = order["signal_date"]
        selected = order["selected"]
        stopped_tickers = set(blocked_tickers)
        target_tickers = {str(item["ticker"]) for item in selected}

        # Target values are based on the pre-trade NAV.  They therefore do
        # not change as outgoing positions are sold, which also makes a
        # rebalance deterministic when selected tickers are reordered.
        nav = cash + self._holdings_value(
            positions, price_data, execution_date, "open"
        )
        regime_scale = float(order.get("regime_scale", 1.0))
        if pd.isna(regime_scale):
            regime_scale = 1.0
        regime_scale = max(regime_scale, 0.0)

        target_shares: dict[str, int] = {}
        target_prices: dict[str, float] = {}
        for item in selected:
            ticker = str(item["ticker"])
            if ticker in target_shares:
                continue
            buy_price = self._price(price_data, ticker, execution_date, "open")
            if buy_price is None or buy_price <= 0:
                # No next-open bar means no fill.  Keep an existing position
                # untouched and skip a new position for this ticker.
                continue
            weight = float(item.get("weight", 1.0 / max(len(selected), 1)))
            if pd.isna(weight):
                weight = 0.0
            target_value = max(nav * weight * regime_scale, 0.0)
            target_shares[ticker] = int(target_value / buy_price)
            target_prices[ticker] = buy_price

        # This is intentionally before all buys.  It permits proceeds from
        # outgoing and overweight positions to fund same-bar increases.
        outgoing = sorted(ticker for ticker in positions if ticker not in target_tickers)
        for ticker in outgoing:
            sell_price = self._price(price_data, ticker, execution_date, "open")
            if sell_price is None or sell_price <= 0:
                # Sparse/missing next-open policy: retain the position and do
                # not fabricate an execution price.
                continue
            cash = self._sell_position(
                positions,
                cash,
                ticker,
                sell_price,
                "rebalance",
                signal_date,
                execution_date,
                trade_log,
                date_ordinal,
            )

        # Reduce selected positions that are above their target before any
        # increases.  A stop-loss ticker is blocked for this execution date,
        # so it cannot be sold and immediately repurchased by this rebalance.
        for ticker in sorted(target_shares):
            if ticker in stopped_tickers or ticker not in positions:
                continue
            current_shares = int(positions[ticker]["shares"])
            excess_shares = current_shares - target_shares[ticker]
            if excess_shares <= 0:
                continue
            cash = self._sell_position(
                positions,
                cash,
                ticker,
                target_prices[ticker],
                "rebalance",
                signal_date,
                execution_date,
                trade_log,
                date_ordinal,
                shares_to_sell=excess_shares,
            )

        # Determine all increases before executing any of them.  Applying a
        # common affordability factor accounts for buy-side costs while
        # avoiding selected-order-dependent cash exhaustion.
        buy_requests: list[tuple[str, int, float]] = []
        for ticker in sorted(target_shares):
            if ticker in stopped_tickers:
                continue
            buy_price = target_prices[ticker]
            current_shares = int(positions[ticker]["shares"]) if ticker in positions else 0
            requested = target_shares[ticker] - current_shares
            if requested > 0:
                buy_requests.append((ticker, requested, buy_price))

        buy_factor = 1.0 + self.config.COMMISSION_RATE + self.config.SLIPPAGE
        requested_cost = sum(shares * price * buy_factor for _, shares, price in buy_requests)
        affordability = min(1.0, max(cash, 0.0) / requested_cost) if requested_cost > 0 else 0.0

        for ticker, requested, buy_price in buy_requests:
            shares = int(requested * affordability)
            if shares <= 0:
                continue
            unit_cost = buy_price * buy_factor
            shares = min(shares, int(max(cash, 0.0) / unit_cost))
            if shares <= 0:
                continue
            entry_notional = shares * buy_price
            entry_commission = entry_notional * self.config.COMMISSION_RATE
            entry_slippage = entry_notional * self.config.SLIPPAGE
            cost = entry_notional + entry_commission + entry_slippage
            cash = max(cash - cost, 0.0)

            if ticker in positions:
                position = positions[ticker]
                old_shares = int(position["shares"])
                old_entry = float(position["entry_price"])
                total_shares = old_shares + shares
                position["entry_price"] = (
                    old_shares * old_entry + shares * buy_price
                ) / total_shares
                position["shares"] = total_shares
                position["peak_price"] = max(float(position["peak_price"]), buy_price)
            else:
                positions[ticker] = {
                    "shares": shares,
                    "entry_price": buy_price,
                    "entry_date": execution_date,
                    "entry_signal_date": signal_date,
                    "peak_price": buy_price,
                }

            trade_log.append({
                "entry_date": execution_date,
                "exit_date": None,
                "ticker": ticker,
                "buy_price": buy_price,
                "sell_price": None,
                "shares": shares,
                "return_pct": None,
                "hold_days": 0,
                "exit_reason": None,
                "signal_date": signal_date,
                "execution_date": execution_date,
                "entry_notional": entry_notional,
                "exit_notional": 0.0,
                "entry_commission": entry_commission,
                "exit_commission": 0.0,
                "entry_slippage": entry_slippage,
                "exit_slippage": 0.0,
                "exit_tax": 0.0,
                "total_cost": entry_commission + entry_slippage,
            })
            self._record_fill(
                side="buy",
                notional=entry_notional,
                commission=entry_commission,
                slippage=entry_slippage,
                tax=0.0,
            )

        return max(cash, 0.0)

    def _sell_position(
        self,
        positions: dict[str, dict[str, Any]],
        cash: float,
        ticker: str,
        sell_price: float,
        reason: str,
        signal_date: pd.Timestamp,
        execution_date: pd.Timestamp,
        trade_log: list[dict[str, Any]],
        date_ordinal: dict[pd.Timestamp, int],
        shares_to_sell: int | None = None,
    ) -> float:
        position = positions[ticker]
        shares_sold = position["shares"] if shares_to_sell is None else min(
            int(shares_to_sell), int(position["shares"])
        )
        if shares_sold <= 0:
            return cash
        ret = sell_price / position["entry_price"] - 1.0
        exit_notional = shares_sold * sell_price
        exit_commission = exit_notional * self.config.COMMISSION_RATE
        exit_slippage = exit_notional * self.config.SLIPPAGE
        exit_tax = exit_notional * self.config.TAX_RATE
        total_cost = exit_commission + exit_slippage + exit_tax
        cash += exit_notional - total_cost
        entry_date = pd.Timestamp(position["entry_date"])
        trade_log.append({
            "entry_date": position["entry_date"],
            "exit_date": execution_date,
            "ticker": ticker,
            "buy_price": position["entry_price"],
            "sell_price": sell_price,
            "shares": shares_sold,
            "return_pct": ret,
            "hold_days": date_ordinal[execution_date] - date_ordinal[entry_date],
            "exit_reason": reason,
            "signal_date": signal_date,
            "execution_date": execution_date,
            "entry_notional": 0.0,
            "exit_notional": exit_notional,
            "entry_commission": 0.0,
            "exit_commission": exit_commission,
            "entry_slippage": 0.0,
            "exit_slippage": exit_slippage,
            "exit_tax": exit_tax,
            "total_cost": total_cost,
        })
        self._record_fill(
            side="sell",
            notional=exit_notional,
            commission=exit_commission,
            slippage=exit_slippage,
            tax=exit_tax,
        )
        remaining_shares = int(position["shares"]) - shares_sold
        if remaining_shares > 0:
            position["shares"] = remaining_shares
        else:
            del positions[ticker]
        return cash

    def _empty_result(
        self,
        benchmark_returns: pd.Series | None = None,
        benchmark_info: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """빈 결과를 반환합니다."""
        source = str(getattr(self.config, "MARKET_INDEX_TICKER", "KPI200"))
        if benchmark_returns is None:
            benchmark_returns = pd.Series(dtype=float, name=f"{source}_price_return")
            benchmark_returns.index.name = "date"
        if benchmark_info is None:
            benchmark_info = benchmark_metadata(source=source)
        return {
            "portfolio_snapshots": pd.DataFrame(
                columns=[
                    "date", "cash", "holdings_value", "nav", "num_positions",
                    "cumulative_cost", "executed_turnover",
                ],
            ),
            "trade_log": pd.DataFrame(columns=self._trade_columns()),
            "daily_returns": pd.Series(dtype=float),
            "benchmark_returns": benchmark_returns,
            "benchmark": benchmark_info,
            "execution_stats": {
                **self._execution_stats,
                "initial_capital": float(self.config.INITIAL_CAPITAL),
            },
        }

    @staticmethod
    def _trade_columns() -> list[str]:
        """Return the stable trade-log schema, including timing fields."""
        return [
            "entry_date", "exit_date", "ticker", "buy_price",
            "sell_price", "shares", "return_pct", "hold_days", "exit_reason",
            "signal_date", "execution_date",
            "entry_notional", "exit_notional", "entry_commission",
            "exit_commission", "entry_slippage", "exit_slippage", "exit_tax",
            "total_cost",
        ]

    @staticmethod
    def _zero_execution_stats() -> dict[str, float]:
        """Return counters for actual fills in one independent engine run."""
        return {
            "commission": 0.0,
            "slippage": 0.0,
            "tax": 0.0,
            "total_cost": 0.0,
            "buy_notional": 0.0,
            "sell_notional": 0.0,
            "executed_turnover": 0.0,
        }

    def _record_fill(
        self,
        side: str,
        notional: float,
        commission: float,
        slippage: float,
        tax: float,
    ) -> None:
        """Update cumulative counters using the filled, not requested, size."""
        cost = commission + slippage + tax
        self._execution_stats["commission"] += commission
        self._execution_stats["slippage"] += slippage
        self._execution_stats["tax"] += tax
        self._execution_stats["total_cost"] += cost
        self._execution_stats["executed_turnover"] += notional
        if side == "buy":
            self._execution_stats["buy_notional"] += notional
        elif side == "sell":
            self._execution_stats["sell_notional"] += notional
