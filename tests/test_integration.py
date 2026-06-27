"""Integration tests for the full Super Quality 2.0 backtesting pipeline.

Uses simulated/mocked data — NO actual API calls.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from super_quality.analysis.metrics import PerformanceMetrics
from super_quality.backtest.engine import BacktestEngine
from super_quality.config import SuperQualityConfig
from super_quality.reporting.report import ReportGenerator


class TestIntegration:
    """Full pipeline integration tests with simulated data."""

    @pytest.fixture
    def simulated_data(self):
        """Generate 6 months of simulated data for 10 tickers."""
        # Create date range: 2023-01-01 to 2023-06-30, trading days only
        dates = pd.bdate_range("2023-01-01", "2023-06-30")
        tickers = [f"{i:06d}" for i in range(1, 11)]

        # Price data: MultiIndex (ticker, date)
        records = []
        np.random.seed(42)
        for ticker in tickers:
            price = 10000  # Starting price
            for d in dates:
                change = np.random.normal(0.001, 0.02)  # ~0.1% drift, 2% vol
                price = price * (1 + change)
                mcap = price * 100000  # 100K shares
                records.append({
                    "ticker": ticker,
                    "date": d,
                    "open": price * 0.995,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": 100000,
                    "mcap": mcap,
                })
        price_data = pd.DataFrame(records)
        price_data = price_data.set_index(["ticker", "date"]).sort_index()

        # Market index data (rising trend so buy signals activate after warmup)
        index_price = 800
        index_records = []
        for d in dates:
            change = np.random.normal(0.0005, 0.015)
            index_price = index_price * (1 + change)
            index_records.append({"date": d, "close": index_price})
        index_data = pd.DataFrame(index_records).set_index("date")

        # Factor data (pre-computed for each ticker on each date)
        # All tickers satisfy buy conditions except some edge cases
        factor_rows = []
        for ticker in tickers:
            for d in dates:
                factor_rows.append({
                    "ticker": ticker,
                    "date": d,
                    "pbr": 0.8 + np.random.random() * 0.4,
                    "pbr_percentile": np.random.random() * 0.15,  # Within bottom 20%
                    "mcap_percentile": np.random.random() * 0.3,  # Within bottom 40%
                    "gpa_percentile": np.random.random() * 100,
                    "supply_percentile": np.random.random() * 100,
                    "share_change_5mo_ago": 0,  # No change (passes C)
                    "share_change_now": 0,  # No change (passes D)
                    "trailing_ni": 1000,  # Positive (passes E)
                    "trailing_ocf": 500,  # Positive (passes F)
                    "buy_signal": True,  # Market timing OK
                    "sell_signal": False,  # No sell signal
                })
        factor_data = pd.DataFrame(factor_rows)

        return {
            "price_data": price_data,
            "index_data": index_data,
            "factor_data": factor_data,
            "financial_data": pd.DataFrame(),
            "dates": dates,
        }

    def test_full_pipeline_runs(self, simulated_data):
        """End-to-end: config -> engine -> metrics -> reports."""
        config = SuperQualityConfig(DART_API_KEY="test")
        engine = BacktestEngine(config)

        sd = simulated_data
        result = engine.run(
            price_data=sd["price_data"],
            index_data=sd["index_data"],
            factor_data=sd["factor_data"],
            financial_data=sd["financial_data"],
        )

        # Verify basic outputs
        assert "portfolio_snapshots" in result
        assert "trade_log" in result
        assert "daily_returns" in result

        # Not empty
        assert len(result["portfolio_snapshots"]) > 0
        assert len(result["daily_returns"]) > 0

        # Compute metrics
        metrics = PerformanceMetrics(result["daily_returns"])
        all_metrics = metrics.compute_all(result["trade_log"])

        # Verify key metrics exist
        assert "cagr" in all_metrics
        assert "sharpe_ratio" in all_metrics
        assert "max_drawdown" in all_metrics
        assert "total_trades" in all_metrics

        # Generate reports
        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = ReportGenerator(output_dir=tmpdir)
            files = reporter.generate_all(
                all_metrics,
                result["portfolio_snapshots"],
                result["trade_log"],
            )
            # ReportGenerator returns display-name keys
            assert "Trade Log" in files
            assert "Equity Curve" in files
            assert "Drawdown" in files
            assert "Tearsheet" in files
            for name, fpath in files.items():
                assert Path(fpath).exists(), f"{name} not found at {fpath}"

    def test_no_trades_with_failing_conditions(self):
        """If all conditions fail, no trades occur, metrics still work."""
        # Create short data where buy signal is False
        dates = pd.bdate_range("2023-01-01", "2023-01-31")
        tickers = ["000001", "000002"]

        price_records = []
        for t in tickers:
            p = 10000
            for d in dates:
                price_records.append({
                    "ticker": t,
                    "date": d,
                    "open": p,
                    "high": p * 1.01,
                    "low": p * 0.99,
                    "close": p,
                    "volume": 100000,
                    "mcap": p * 100000,
                })
        price_data = (
            pd.DataFrame(price_records)
            .set_index(["ticker", "date"])
            .sort_index()
        )

        np.random.seed(42)
        index_close = (800 * (1 + np.random.randn(len(dates)) * 0.01)).cumprod()
        index_data = pd.DataFrame({"close": index_close}, index=dates)

        # All buy_signal = False (no buy allowed)
        factor_rows = []
        for t in tickers:
            for d in dates:
                factor_rows.append({
                    "ticker": t,
                    "date": d,
                    "pbr": 1.0,
                    "pbr_percentile": 0.1,
                    "mcap_percentile": 0.2,
                    "gpa_percentile": 50,
                    "supply_percentile": 50,
                    "share_change_5mo_ago": 0,
                    "share_change_now": 0,
                    "trailing_ni": 100,
                    "trailing_ocf": 100,
                    "buy_signal": False,
                    "sell_signal": False,
                })
        factor_data = pd.DataFrame(factor_rows)

        config = SuperQualityConfig(DART_API_KEY="test")
        engine = BacktestEngine(config)
        result = engine.run(
            price_data,
            index_data,
            factor_data,
            pd.DataFrame(),
        )

        assert len(result["trade_log"]) == 0, "Should have zero trades"

        # Metrics should still work with empty trade_log
        metrics = PerformanceMetrics(result["daily_returns"])
        all_metrics = metrics.compute_all(result["trade_log"])
        assert all_metrics["total_trades"] == 0

    def test_look_ahead_bias_prevention(self, simulated_data):
        """Verify data from future dates is not used in past decisions.

        This test checks that the engine processes dates in chronological order.
        """
        sd = simulated_data

        # Create a factor where buy_signal is True only on specific dates
        factor_data = sd["factor_data"].copy()
        # Set buy signal to False for dates before March 1
        cutoff = pd.Timestamp("2023-03-01")
        factor_data["buy_signal"] = factor_data["date"] >= cutoff

        config = SuperQualityConfig(DART_API_KEY="test")
        engine = BacktestEngine(config)
        result = engine.run(
            sd["price_data"],
            sd["index_data"],
            factor_data,
            sd["financial_data"],
        )

        # Check that no trades happen before March 1
        # (since buy_signal was False before cutoff)
        if len(result["trade_log"]) > 0:
            first_trade_date = result["trade_log"]["entry_date"].min()
            # entry_date might be a Timestamp; compare date part
            first_trade_ts = pd.Timestamp(first_trade_date)
            assert first_trade_ts >= cutoff, (
                f"Trade on {first_trade_date} before buy signal was active "
                f"on {cutoff.date()}"
            )
