"""Focused, network-free tests for the low-volatility adjusted-price diagnostic.

These tests exercise the pure helpers and the engine injection path used by
``scripts/run_low_vol_diagnostic.py``.  They do not touch the large price
cache or the universe bundle, so they are cheap and deterministic.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from k200_low_vol.selector import LowVolatilitySelector
from k200_low_vol.spec import LowVolSpec
from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine

import scripts.run_low_vol_diagnostic as diag


def test_compute_metrics_basic() -> None:
    ret = pd.Series(
        [0.01, -0.02, 0.03, 0.0],
        index=pd.to_datetime(
            ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        ),
    )
    metrics = diag.compute_metrics(ret)
    assert metrics["stitched_total_return"] > 0
    assert "yearly_returns" in metrics
    assert 2020 in metrics["yearly_returns"]


def test_compute_metrics_empty() -> None:
    metrics = diag.compute_metrics(pd.Series(dtype=float))
    assert metrics["cagr"] == 0.0
    assert metrics["stitched_total_return"] == 0.0


def test_decide_rule() -> None:
    archive = diag.decide({"cagr": 0.05, "sharpe": 0.5, "max_drawdown": -0.30})
    assert archive["verdict"] == "ARCHIVE_PERMANENTLY"
    proceed = diag.decide({"cagr": 0.09, "sharpe": 0.80, "max_drawdown": -0.20})
    assert proceed["verdict"] == "PROCEED_TO_LEDGER"
    # MDD exactly at boundary is not strictly greater than -25%
    boundary = diag.decide({"cagr": 0.09, "sharpe": 0.80, "max_drawdown": -0.25})
    assert boundary["verdict"] == "ARCHIVE_PERMANENTLY"


def test_cutoff_fail_closed() -> None:
    with pytest.raises(RuntimeError):
        diag._fail_closed_if_exceeds_cutoff(pd.Timestamp("2025-01-01"), "test")


def test_build_signal_dates_synthetic() -> None:
    # Synthetic 2020 calendar: one session per month-end plus extras.
    cal = []
    for month in range(1, 13):
        cal.append(date(2020, month, 5))
        cal.append(date(2020, month, 25))
    signal_dates = diag.build_signal_dates(
        pd.DataFrame({"date": pd.to_datetime(cal)}), LowVolSpec()
    )
    assert len(signal_dates) == 4
    assert signal_dates[0].month == 3 and signal_dates[0].day == 25
    assert signal_dates[3].month == 12 and signal_dates[3].day == 25


def test_engine_injection_path() -> None:
    """The frozen selector runs through the MQ engine boundary as expected."""
    spec = LowVolSpec()
    dates = pd.to_datetime(
        ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]
    )
    tickers = ["000001", "000002"]
    n = len(tickers) * len(dates)
    price_panel = pd.DataFrame(
        {
            "ticker": [t for t in tickers for _ in dates],
            "date": [d for _ in tickers for d in dates],
            "open": [100 + i for t in tickers for i, _ in enumerate(dates)],
            "high": [101 + i for t in tickers for i, _ in enumerate(dates)],
            "low": [99 + i for t in tickers for i, _ in enumerate(dates)],
            "close": [100 + i for t in tickers for i, _ in enumerate(dates)],
            "volume": [1000] * n,
            "mcap": [1e12] * n,
        }
    ).set_index(["ticker", "date"]).sort_index()

    factor = pd.DataFrame(
        [
            {
                "ticker": "000001",
                "date": pd.Timestamp("2020-01-07"),
                "low_volatility": 0.01,
                "valid_return_count": 250,
            },
            {
                "ticker": "000002",
                "date": pd.Timestamp("2020-01-07"),
                "low_volatility": 0.05,
                "valid_return_count": 250,
            },
        ]
    )
    universe_data = pd.DataFrame(
        {
            "as_of": [pd.Timestamp("2020-01-07"), pd.Timestamp("2020-01-07")],
            "ticker": ["000001", "000002"],
        }
    )

    config = diag.build_engine_config()
    engine = PortfolioRebalanceEngine(config)
    engine.set_target_provider(LowVolatilitySelector(spec))
    result = engine.run(
        price_data=price_panel,
        index_data=pd.DataFrame(),
        factor_data=factor,
        universe_data=universe_data,
        measured_start=pd.Timestamp("2020-01-02"),
        measured_end=pd.Timestamp("2020-01-08"),
    )
    assert not result["daily_returns"].empty
    # Bottom-20% of 2 eligible tickers => 0 selected (floor(2*0.2)=0), so no trade.
    assert result["trade_log"].empty
