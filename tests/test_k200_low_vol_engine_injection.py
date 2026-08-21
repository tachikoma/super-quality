"""Focused tests for injecting a non-MQ target provider into the engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.config import K200MQConfig
from k200_mq.strategies.momentum_quality import MomentumQualityStrategy


def _config(**overrides: object) -> K200MQConfig:
    values: dict[str, object] = {
        "INITIAL_CAPITAL": 1_000.0,
        "MAX_HOLDINGS": 1,
        "TOP_N": 1,
        "EXCLUDE_KOSPI_TOP_N": 0,
        "MAX_POSITION_WEIGHT": 1.0,
        "MIN_CASH_RATIO": 0.0,
        "COMMISSION_RATE": 0.0,
        "TAX_RATE": 0.0,
        "SLIPPAGE": 0.0,
        "ENABLE_STOP_LOSS": False,
    }
    values.update(overrides)
    return K200MQConfig.model_validate(values)


def _prices(dates: list[pd.Timestamp]) -> pd.DataFrame:
    rows = []
    for ticker, open_prices, close_prices in (
        ("A", [10.0, 20.0, 30.0], [11.0, 21.0, 31.0]),
        ("B", [15.0, 25.0, 35.0], [16.0, 26.0, 36.0]),
    ):
        for dt, open_price, close_price in zip(dates, open_prices, close_prices):
            rows.append({
                "ticker": ticker,
                "date": dt,
                "open": open_price,
                "high": max(open_price, close_price),
                "low": min(open_price, close_price),
                "close": close_price,
                "volume": 1_000_000.0,
                "mcap": 1_000_000.0,
            })
    return pd.DataFrame(rows).set_index(["ticker", "date"]).sort_index()


def _factors(dates: list[pd.Timestamp]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": ticker,
            "date": dt,
            "momentum_z": float(ticker == "A"),
            "quality_z": float(ticker == "A"),
        }
        for dt in dates
        for ticker in ("A", "B")
    ])


class _FakeLowVolStrategy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def select_portfolio(
        self,
        factor_data: pd.DataFrame,
        universe: list[str],
        as_of: Any,
        adv_ratio_by_ticker: Mapping[str, float] | None = None,
        pair_correlation_map: Mapping[tuple[str, str], float] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append({
            "factor_data": factor_data.copy(),
            "universe": list(universe),
            "as_of": as_of,
            "adv_ratio_by_ticker": adv_ratio_by_ticker,
            "pair_correlation_map": pair_correlation_map,
        })
        ticker = "A" if as_of == pd.Timestamp("2024-01-02") else "B"
        return [{"ticker": ticker, "weight": 1.0}]


def test_default_engine_uses_momentum_quality_strategy() -> None:
    dates = list(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    engine = PortfolioRebalanceEngine(_config())

    assert isinstance(engine.strategy, MomentumQualityStrategy)
    result = engine.run(
        _prices(dates),
        pd.DataFrame(),
        _factors(dates),
        pd.DataFrame({"as_of": [dates[0]], "ticker": ["A",]}),
    )

    assert result["trade_log"].iloc[0]["ticker"] == "A"


def test_injected_strategy_receives_close_slice_and_maps_are_optional() -> None:
    dates = list(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    strategy = _FakeLowVolStrategy()
    result = PortfolioRebalanceEngine(
        _config(
            ENABLE_ADV_FILTER=True,
            ENABLE_CORRELATION_FILTER=True,
            QUALITY_PRIMARY=False,
        ),
        strategy=strategy,
    ).run(
        _prices(dates),
        pd.DataFrame(),
        _factors(dates),
        pd.DataFrame({"as_of": [dates[0], dates[0]], "ticker": ["A", "B"]}),
    )

    assert len(strategy.calls) == 1
    call = strategy.calls[0]
    assert call["as_of"] == dates[0]
    assert call["universe"] == ["A", "B"]
    pd.testing.assert_frame_equal(call["factor_data"], _factors(dates[:1]))
    assert call["adv_ratio_by_ticker"] is None
    assert call["pair_correlation_map"] is None
    assert result["trade_log"].iloc[0]["ticker"] == "A"


def test_injected_close_signal_executes_at_next_open_only() -> None:
    dates = list(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    strategy = _FakeLowVolStrategy()
    result = PortfolioRebalanceEngine(_config(), strategy=strategy).run(
        _prices(dates),
        pd.DataFrame(),
        _factors(dates),
        pd.DataFrame({"as_of": [dates[0]], "ticker": ["A"]}),
    )

    trade = result["trade_log"].iloc[0]
    assert trade["signal_date"] == dates[0]
    assert trade["execution_date"] == dates[1]
    assert trade["buy_price"] == 20.0


def test_final_close_signal_does_not_create_out_of_range_order() -> None:
    dates = list(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    strategy = _FakeLowVolStrategy()
    result = PortfolioRebalanceEngine(_config(), strategy=strategy).run(
        _prices(dates),
        pd.DataFrame(),
        _factors(dates),
        pd.DataFrame({
            "as_of": [dates[0], dates[-1]],
            "ticker": ["A", "B"],
        }),
    )

    assert len(strategy.calls) == 1
    assert set(result["trade_log"]["ticker"]) == {"A"}
    assert not (result["trade_log"]["execution_date"] == dates[-1]).any()
