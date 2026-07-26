"""포트폴리오 리밸런싱 엔진.

KOSPI 200 Momentum + Quality 전략의 일별 시뮬레이션 루프를 구현합니다.
기존 BacktestEngine(싱글티커)과 달리,
리밸런싱 일자에 포트폴리오 수준의 포지션 재구성을 수행합니다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from k200_mq.config import K200MQConfig
from k200_mq.strategies.momentum_quality import MomentumQualityStrategy

logger = logging.getLogger(__name__)


class PortfolioRebalanceEngine:
    """KOSPI 200 Momentum + Quality 리밸런싱 엔진.

    일별 mark-to-market, stop-loss 체크,
    리밸런싱 일자에 포트폴리오 재구성을 수행합니다.
    """

    def __init__(self, config: K200MQConfig) -> None:
        self.config = config
        self.strategy = MomentumQualityStrategy(config)

    def run(
        self,
        price_data: pd.DataFrame,
        index_data: pd.DataFrame,
        factor_data: pd.DataFrame,
        universe_data: pd.DataFrame,
    ) -> dict[str, Any]:
        """백테스트 시뮬레이션을 실행합니다.

        Parameters
        ----------
        price_data : pd.DataFrame
            MultiIndex ``(ticker, date)`` with ``open``, ``high``,
            ``low``, ``close``, ``volume``, ``mcap`` columns.
        index_data : pd.DataFrame
            ``date`` index, ``close`` column (KOSPI 200 index).
        factor_data : pd.DataFrame
            팩터 스코어 DataFrame (ticker, date momentum_z, quality_z).
        universe_data : pd.DataFrame
            리밸런싱 일자별 유니버스 (as_of, ticker).

        Returns
        -------
        dict
            ``portfolio_snapshots``, ``trade_log``, ``daily_returns``.
        """
        all_dates = sorted(price_data.index.get_level_values("date").unique())
        if len(all_dates) < 2:
            return self._empty_result()

        date_ordinal = {d: i for i, d in enumerate(all_dates)}

        # 리밸런싱 일정 생성
        rebalance_dates = self._generate_rebalance_dates(all_dates)

        # 종속 이력 딕셔너리 생성
        universe_lookup = self._build_universe_lookup(universe_data)

        # 포지션 추적
        cash = float(self.config.INITIAL_CAPITAL)
        positions: dict[str, dict[str, Any]] = {}

        trade_log: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []

        for i, current_date in enumerate(all_dates):
            current_ts = pd.Timestamp(current_date)
            prev_date = all_dates[i - 1] if i > 0 else None
            prev_ts = pd.Timestamp(prev_date) if prev_date else None

            # 첫째 날: 초기 스냅샷
            if i == 0:
                snapshots.append({
                    "date": current_date,
                    "cash": cash,
                    "holdings_value": 0.0,
                    "nav": cash,
                    "num_positions": 0,
                })
                continue

            # 일일: mark-to-market
            holdings_value = 0.0
            for tkr, pos in list(positions.items()):
                close_p = self._price(price_data, tkr, current_ts, "close")
                if close_p is not None:
                    holdings_value += pos["shares"] * close_p

            # 일일: stop-loss 체크
            positions = self._check_stop_losses(
                positions, price_data, prev_ts, current_ts, date_ordinal,
                trade_log, current_date,
            )

            # 리밸런싱 일자: 포트폴리오 재구성
            if current_date in rebalance_dates:
                positions = self._rebalance(
                    current_date,
                    current_ts,
                    prev_ts,
                    price_data,
                    index_data,
                    factor_data,
                    universe_lookup,
                    positions,
                    cash,
                    trade_log,
                    date_ordinal,
                )

            # 스냅샷 기록
            holdings_value = 0.0
            for tkr, pos in positions.items():
                close_p = self._price(price_data, tkr, current_ts, "close")
                if close_p is not None:
                    holdings_value += pos["shares"] * close_p

            nav = cash + holdings_value
            snapshots.append({
                "date": current_date,
                "cash": cash,
                "holdings_value": holdings_value,
                "nav": nav,
                "num_positions": len(positions),
            })

        # 결과 생성
        snapshots_df = pd.DataFrame(snapshots)
        if not snapshots_df.empty:
            snapshots_df["daily_return"] = snapshots_df["nav"].pct_change().fillna(0.0)
            daily_returns = snapshots_df.set_index("date")["daily_return"]
        else:
            daily_returns = pd.Series(dtype=float)

        trade_log_df = pd.DataFrame(trade_log)

        return {
            "portfolio_snapshots": snapshots_df,
            "trade_log": trade_log_df,
            "daily_returns": daily_returns,
        }

    def _generate_rebalance_dates(
        self, all_dates: list[date]
    ) -> set[date]:
        """리밸런싱 일자를 생성합니다."""
        freq = self.config.REBALANCE_FREQ
        rebalance_dates: set[date] = set()

        if freq == "M":
            for d in all_dates:
                # 월말이거나 그에 가장 가까운 영업일
                next_month = d.month + 1 if d.month < 12 else 1
                next_year = d.year if d.month < 12 else d.year + 1
                try:
                    month_end = pd.Timestamp(
                        year=next_year, month=next_month, day=1
                    ) - pd.Timedelta(days=1)
                except Exception:
                    continue
                search_start = month_end.date()
                # 실제로 가장 가까운 영업일을 찾음
                for ad in all_dates:
                    if ad >= search_start:
                        rebalance_dates.add(ad)
                        break
        elif freq == "Q":
            quarter_months = [3, 6, 9, 12]
            for d in all_dates:
                if d.month in quarter_months:
                    last_day = pd.Timestamp(year=d.year, month=d.month + 1, day=1) - pd.Timedelta(days=1)
                    search_start = last_day.date()
                    for ad in all_dates:
                        if ad >= search_start:
                            rebalance_dates.add(ad)
                            break

        return rebalance_dates

    def _build_universe_lookup(
        self, universe_data: pd.DataFrame
    ) -> dict[date, list[str]]:
        """종속 이력 딕셔너리를 생성합니다."""
        lookup: dict[date, list[str]] = {}
        if universe_data is None or universe_data.empty:
            return lookup
        if "as_of" in universe_data.columns and "ticker" in universe_data.columns:
            for as_of, grp in universe_data.groupby("as_of"):
                d = as_of if isinstance(as_of, date) else pd.Timestamp(as_of).date()
                lookup[d] = grp["ticker"].tolist()
        return lookup

    def _get_universe(self, as_of: date, lookup: dict[date, list[str]]) -> list[str]:
        """특정 일자의 유니버스를 가져옵니다."""
        return lookup.get(as_of, [])

    def _price(
        self,
        price_data: pd.DataFrame,
        ticker: str,
        ts: pd.Timestamp,
        col: str,
    ) -> float | None:
        """특정 티커와 날짜의 가격을 조회합니다."""
        try:
            val = price_data.loc[(ticker, ts), col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            return float(val)
        except (KeyError, ValueError, TypeError):
            return None

    def _check_stop_losses(
        self,
        positions: dict[str, dict[str, Any]],
        price_data: pd.DataFrame,
        prev_ts: pd.Timestamp | None,
        current_ts: pd.Timestamp,
        date_ordinal: dict[date, int],
        trade_log: list[dict[str, Any]],
        current_date: date,
    ) -> dict[str, dict[str, Any]]:
        """일일 손절을 체크합니다."""
        to_sell: list[str] = []
        for tkr, pos in positions.items():
            prev_close = self._price(price_data, tkr, prev_ts, "close") if prev_ts else None
            if prev_close is None or prev_close <= 0:
                continue
            ret = prev_close / pos["entry_price"] - 1.0
            if ret <= self.config.SLIPPAGE:  # placeholder - real stop-loss below
                pass
            # 실제 stop-loss는 별도 config가 필요하므로 추후 구현
        return positions

    def _rebalance(
        self,
        current_date: date,
        current_ts: pd.Timestamp,
        prev_ts: pd.Timestamp | None,
        price_data: pd.DataFrame,
        index_data: pd.DataFrame,
        factor_data: pd.DataFrame,
        universe_lookup: dict[date, list[str]],
        positions: dict[str, dict[str, Any]],
        cash: float,
        trade_log: list[dict[str, Any]],
        date_ordinal: dict[date, int],
    ) -> dict[str, dict[str, Any]]:
        """리밸런싱을 실행합니다."""
        universe = self._get_universe(current_date, universe_lookup)
        if not universe:
            return positions

        # 팩터 데이터 가져오기
        factor_at_date = factor_data[
            factor_data["date"] == pd.Timestamp(current_date)
        ] if "date" in factor_data.columns else factor_data

        # 포트폴리오 선택
        selected = self.strategy.select_portfolio(
            factor_data=factor_at_date,
            universe=universe,
            as_of=current_date,
        )

        target_tickers = {s["ticker"] for s in selected}

        # 현재 보유 중이지 않은 종목 매수
        for s in selected:
            tkr = s["ticker"]
            if tkr in positions:
                continue  # 이미 보유 중

            prev_close = self._price(price_data, tkr, prev_ts, "close") if prev_ts else None
            if prev_close is None or prev_close <= 0:
                continue

            nav = cash
            for pos_tkr, pos in positions.items():
                p = self._price(price_data, pos_tkr, prev_ts, "close")
                if p is not None:
                    nav += pos["shares"] * p

            target_value = nav * s.get("weight", 1.0 / len(selected))
            target_shares = int(target_value / prev_close)
            if target_shares <= 0:
                continue

            cost = target_shares * prev_close * (1.0 + self.config.COMMISSION_RATE + self.config.SLIPPAGE)
            if cash < cost:
                continue

            cash -= cost
            positions[tkr] = {
                "shares": target_shares,
                "entry_price": prev_close,
                "entry_date": current_date,
            }
            trade_log.append({
                "entry_date": current_date,
                "exit_date": None,
                "ticker": tkr,
                "buy_price": prev_close,
                "sell_price": None,
                "shares": target_shares,
                "return_pct": None,
                "hold_days": 0,
                "exit_reason": None,
            })

        # 리밸런싱에서 빠진 종목 매도
        to_sell = [t for t in positions if t not in target_tickers]
        for tkr in to_sell:
            pos = positions[tkr]
            sell_price = self._price(price_data, tkr, current_ts, "close")
            if sell_price is None or sell_price <= 0:
                continue

            ret = sell_price / pos["entry_price"] - 1.0
            sell_cost = 1.0 - self.config.COMMISSION_RATE - self.config.TAX_RATE - self.config.SLIPPAGE
            proceeds = pos["shares"] * sell_price * sell_cost
            cash += proceeds

            trade_log.append({
                "entry_date": pos["entry_date"],
                "exit_date": current_date,
                "ticker": tkr,
                "buy_price": pos["entry_price"],
                "sell_price": sell_price,
                "shares": pos["shares"],
                "return_pct": ret,
                "hold_days": date_ordinal[current_date] - date_ordinal[pos["entry_date"]],
                "exit_reason": "rebalance",
            })
            del positions[tkr]

        return positions

    def _empty_result(self) -> dict[str, Any]:
        """빈 결과를 반환합니다."""
        return {
            "portfolio_snapshots": pd.DataFrame(
                columns=["date", "cash", "holdings_value", "nav", "num_positions"],
            ),
            "trade_log": pd.DataFrame(
                columns=[
                    "entry_date", "exit_date", "ticker", "buy_price",
                    "sell_price", "shares", "return_pct", "hold_days", "exit_reason",
                ],
            ),
            "daily_returns": pd.Series(dtype=float),
        }