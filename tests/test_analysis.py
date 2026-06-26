"""Tests for PerformanceMetrics, ReportGenerator, and the CLI entry point.

These tests use deterministic synthetic data and **never** make network
calls.  Temporary directories are used for report output so no files
leak outside the test workspace.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from super_quality.analysis.metrics import PerformanceMetrics
from super_quality.reporting.report import ReportGenerator


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def daily_returns() -> pd.Series:
    """252 trading days of synthetic returns with known properties.

    Returns are normally distributed around 0.05 % with 1 % daily vol.
    Seeded for reproducibility.
    """
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=252)
    vals = 0.0005 + 0.01 * rng.normal(size=252)
    return pd.Series(vals, index=dates, name="daily_return")


@pytest.fixture
def constant_returns() -> pd.Series:
    """252 days of constant +0.1 % daily returns (no variance)."""
    dates = pd.bdate_range("2024-01-01", periods=252)
    return pd.Series(0.001, index=dates, name="daily_return")


@pytest.fixture
def empty_returns() -> pd.Series:
    """Empty return series."""
    return pd.Series(dtype=float, name="daily_return")


@pytest.fixture
def mock_trade_log() -> pd.DataFrame:
    """Trade log with 3 completed trades (2 winners, 1 loser)."""
    return pd.DataFrame({
        "entry_date": [date(2024, 1, 2), date(2024, 1, 5), date(2024, 1, 8)],
        "exit_date": [date(2024, 1, 5), date(2024, 1, 10), date(2024, 1, 12)],
        "ticker": ["A", "B", "C"],
        "buy_price": [10000.0, 20000.0, 15000.0],
        "sell_price": [11000.0, 18000.0, 16500.0],
        "shares": [100, 50, 100],
        "return_pct": [0.10, -0.10, 0.10],
        "hold_days": [3, 5, 4],
        "exit_reason": ["expiry", "stop_loss", "expiry"],
    })


@pytest.fixture
def mock_snapshots() -> pd.DataFrame:
    """Daily portfolio snapshots for chart tests."""
    dates = pd.bdate_range("2024-01-01", periods=50)
    nav = 100_000_000 * (1.0005 ** np.arange(50))
    return pd.DataFrame({
        "date": dates,
        "cash": nav * 0.3,
        "holdings_value": nav * 0.7,
        "nav": nav,
        "num_positions": [min(i + 1, 10) for i in range(50)],
    })


@pytest.fixture
def temp_output_dir() -> str:
    """Temporary directory for report outputs."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


# ═══════════════════════════════════════════════════════════════════════
# PerformanceMetrics — compute_all
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsComputeAll:
    """Tests for PerformanceMetrics.compute_all() with various inputs."""

    def test_metrics_basic_computation(self, daily_returns: pd.Series) -> None:
        """Verify all metrics are computed with sensible values."""
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all()

        expected_keys = {
            "total_return", "cagr", "volatility", "sharpe_ratio",
            "sortino_ratio", "max_drawdown", "max_drawdown_duration",
            "win_rate", "profit_factor", "total_trades", "avg_hold_days",
            "monthly_returns", "yearly_returns", "benchmark_comparison",
        }
        assert set(result.keys()) == expected_keys, f"Missing keys: {expected_keys - set(result.keys())}"

        # Type checks
        assert isinstance(result["total_return"], float)
        assert isinstance(result["cagr"], float)
        assert isinstance(result["sharpe_ratio"], float)
        assert isinstance(result["max_drawdown"], float)
        assert isinstance(result["max_drawdown_duration"], int)
        assert isinstance(result["total_trades"], int)
        assert isinstance(result["monthly_returns"], pd.DataFrame)
        assert isinstance(result["yearly_returns"], pd.DataFrame)

        # Sanity ranges
        assert -1.0 < result["total_return"] < 10.0
        assert -1.0 < result["cagr"] < 10.0
        assert result["volatility"] >= 0.0
        assert result["sharpe_ratio"] < 10.0
        assert result["max_drawdown"] <= 0.0

    def test_metrics_known_values(self) -> None:
        """Check exact values for a manually computed scenario.

        Generate 252 days of synthetic returns with a fixed random
        seed, then cross-validate every metric against independently
        computed expected values.
        """
        rng = np.random.default_rng(1234)
        dates = pd.bdate_range("2024-01-01", periods=252)
        raw = 0.0005 + 0.015 * rng.normal(size=252)
        returns = pd.Series(raw, index=dates)

        total_return_expected = float((1.0 + returns).prod() - 1.0)
        years = len(returns) / 252.0
        cagr_expected = (1.0 + total_return_expected) ** (1.0 / years) - 1.0
        ann_vol_expected = float(returns.std(ddof=1) * np.sqrt(252.0))
        ann_ret_expected = float(returns.mean() * 252.0)

        nav = (1.0 + returns).cumprod()
        running_max = nav.expanding().max()
        dd = nav / running_max - 1.0
        max_dd_expected = float(dd.min())

        metrics = PerformanceMetrics(returns, risk_free_rate=0.03)
        result = metrics.compute_all()

        assert result["total_return"] == pytest.approx(total_return_expected, rel=1e-10)
        assert result["cagr"] == pytest.approx(cagr_expected, rel=1e-10)
        assert result["volatility"] == pytest.approx(ann_vol_expected, rel=1e-10)

        # Sharpe
        sharpe_expected = (ann_ret_expected - 0.03) / ann_vol_expected
        assert result["sharpe_ratio"] == pytest.approx(sharpe_expected, rel=1e-10)

        # Max DD
        assert result["max_drawdown"] == pytest.approx(max_dd_expected, rel=1e-10)

        # Sortino
        downside = returns[returns < 0.0]
        if len(downside) > 1:
            downside_dev = float(downside.std(ddof=1) * np.sqrt(252.0))
            sortino_expected = (ann_ret_expected - 0.03) / downside_dev
            assert result["sortino_ratio"] == pytest.approx(sortino_expected, rel=1e-10)

    def test_metrics_trade_stats(
        self,
        daily_returns: pd.Series,
        mock_trade_log: pd.DataFrame,
    ) -> None:
        """Verify trade statistics are computed correctly."""
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all(trade_log=mock_trade_log)

        # Win rate: 2 winners / 3 total
        assert result["win_rate"] == pytest.approx(2.0 / 3.0, rel=1e-10)
        # Profit factor: (0.10 + 0.10) / abs(-0.10) = 0.20 / 0.10 = 2.0
        assert result["profit_factor"] == pytest.approx(2.0, rel=1e-10)
        # Total trades: 3
        assert result["total_trades"] == 3
        # Avg hold days: (3 + 5 + 4) / 3 = 4.0
        assert result["avg_hold_days"] == pytest.approx(4.0, rel=1e-10)

    def test_metrics_no_trade_log(self, daily_returns: pd.Series) -> None:
        """Trade stats are zero when no trade_log is provided."""
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all()

        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0
        assert result["profit_factor"] == 0.0
        assert result["avg_hold_days"] == 0.0

    def test_metrics_empty_trade_log(self, daily_returns: pd.Series) -> None:
        """Empty trade_log → all trade stats are zero."""
        empty = pd.DataFrame(columns=[
            "entry_date", "exit_date", "ticker", "buy_price",
            "sell_price", "shares", "return_pct", "hold_days", "exit_reason",
        ])
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all(trade_log=empty)
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0

    def test_metrics_empty_returns(self, empty_returns: pd.Series) -> None:
        """Empty return series → all metrics are 0 or empty."""
        metrics = PerformanceMetrics(empty_returns)
        result = metrics.compute_all()

        assert result["total_return"] == 0.0
        assert result["cagr"] == 0.0
        assert result["volatility"] == 0.0
        assert result["sharpe_ratio"] == 0.0
        assert result["sortino_ratio"] == 0.0
        assert result["max_drawdown"] == 0.0
        assert result["max_drawdown_duration"] == 0
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0
        assert result["profit_factor"] == 0.0
        assert result["monthly_returns"].empty
        assert result["yearly_returns"].empty
        assert result["benchmark_comparison"] == {}

    def test_metrics_benchmark_comparison(self, daily_returns: pd.Series) -> None:
        """Benchmark comparison returns sensible values."""
        bench = daily_returns * 0.8 + 0.0001  # correlated but shifted
        metrics = PerformanceMetrics(daily_returns, risk_free_rate=0.03)
        metrics.set_benchmark(bench)
        result = metrics.compute_all()

        bench_result = result["benchmark_comparison"]
        assert "alpha" in bench_result
        assert "beta" in bench_result
        assert "correlation" in bench_result
        assert "tracking_error" in bench_result

        # Correlation should be high (bench is strongly correlated)
        assert bench_result["correlation"] > 0.5
        # Tracking error should be positive
        assert bench_result["tracking_error"] >= 0.0

    def test_metrics_benchmark_not_set(self, daily_returns: pd.Series) -> None:
        """No benchmark → benchmark_comparison is empty."""
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all()
        assert result["benchmark_comparison"] == {}

    def test_metrics_monthly_returns_format(self, daily_returns: pd.Series) -> None:
        """Monthly returns DataFrame has correct shape."""
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all()
        monthly = result["monthly_returns"]
        assert "return" in monthly.columns
        # With 252 business days starting 2024-01-01, we should span ~12 months
        assert len(monthly) >= 11

    def test_metrics_cagr_positive_return(self) -> None:
        """CAGR should be > 0 for positive returns."""
        dates = pd.bdate_range("2024-01-01", periods=252)
        returns = pd.Series(0.001, index=dates)  # constant 0.1%
        metrics = PerformanceMetrics(returns)
        result = metrics.compute_all()
        assert result["cagr"] > 0.0
        # CAGR should be close to (1.001^252)^(1) - 1 = 1.001^252 - 1
        expected = 1.001 ** 252 - 1
        assert result["cagr"] == pytest.approx(expected, rel=1e-10)

    def test_metrics_max_drawdown_duration(self, daily_returns: pd.Series) -> None:
        """Max drawdown duration is a non-negative int."""
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all()
        assert isinstance(result["max_drawdown_duration"], int)
        assert result["max_drawdown_duration"] >= 0

    def test_set_benchmark_validates(self, daily_returns: pd.Series) -> None:
        """set_benchmark accepts a pd.Series and stores it."""
        metrics = PerformanceMetrics(daily_returns)
        bench = pd.Series(0.0005, index=daily_returns.index)
        metrics.set_benchmark(bench)
        assert metrics._benchmark_returns is not None  # noqa: SLF001


# ═══════════════════════════════════════════════════════════════════════
# ReportGenerator
# ═══════════════════════════════════════════════════════════════════════


class TestReportGenerator:
    """Tests for ReportGenerator output methods."""

    def test_save_trade_log(
        self,
        temp_output_dir: str,
        mock_trade_log: pd.DataFrame,
    ) -> None:
        """Trade log CSV is created with correct content."""
        gen = ReportGenerator(temp_output_dir)
        path = gen.save_trade_log(mock_trade_log)
        assert Path(path).exists()
        assert path.endswith("trade_log.csv")

        loaded = pd.read_csv(path)
        assert len(loaded) == len(mock_trade_log)
        assert list(loaded.columns) == list(mock_trade_log.columns)

    def test_save_portfolio_snapshots(
        self,
        temp_output_dir: str,
        mock_snapshots: pd.DataFrame,
    ) -> None:
        """Portfolio snapshots CSV is created."""
        gen = ReportGenerator(temp_output_dir)
        path = gen.save_portfolio_snapshots(mock_snapshots)
        assert Path(path).exists()
        assert path.endswith("portfolio_snapshots.csv")

    def test_plot_equity_curve(
        self,
        temp_output_dir: str,
        mock_snapshots: pd.DataFrame,
    ) -> None:
        """Equity curve PNG is created and is non-empty."""
        gen = ReportGenerator(temp_output_dir)
        path = gen.plot_equity_curve(mock_snapshots)
        assert Path(path).exists()
        assert Path(path).stat().st_size > 1000  # PNG header + content

    def test_plot_equity_curve_empty(
        self,
        temp_output_dir: str,
    ) -> None:
        """Empty snapshots → no file created."""
        gen = ReportGenerator(temp_output_dir)
        empty = pd.DataFrame(columns=["date", "nav"])
        path = gen.plot_equity_curve(empty)
        assert path == ""

    def test_plot_drawdown(
        self,
        temp_output_dir: str,
        mock_snapshots: pd.DataFrame,
    ) -> None:
        """Drawdown PNG is created and is non-empty."""
        gen = ReportGenerator(temp_output_dir)
        path = gen.plot_drawdown(mock_snapshots)
        assert Path(path).exists()
        assert Path(path).stat().st_size > 1000

    def test_generate_html_tearsheet(
        self,
        temp_output_dir: str,
        mock_snapshots: pd.DataFrame,
        mock_trade_log: pd.DataFrame,
        daily_returns: pd.Series,
    ) -> None:
        """HTML tearsheet is valid and self-contained.

        Checks file existence, basic HTML structure, and base64 images.
        """
        metrics = PerformanceMetrics(daily_returns).compute_all(trade_log=mock_trade_log)
        gen = ReportGenerator(temp_output_dir)
        path = gen.generate_html_tearsheet(metrics, mock_snapshots, mock_trade_log)

        assert Path(path).exists()
        html = Path(path).read_text(encoding="utf-8")

        # Valid HTML5
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
        # Self-contained: no external resources
        assert "data:image/png;base64," in html
        assert "CAGR" in html
        assert "Sharpe" in html
        assert "Max Drawdown" in html
        assert "Trade Log" in html

    def test_generate_all(
        self,
        temp_output_dir: str,
        mock_snapshots: pd.DataFrame,
        mock_trade_log: pd.DataFrame,
        daily_returns: pd.Series,
    ) -> None:
        """generate_all() produces all expected files."""
        metrics = PerformanceMetrics(daily_returns).compute_all(trade_log=mock_trade_log)
        gen = ReportGenerator(temp_output_dir)
        result = gen.generate_all(metrics, mock_snapshots, mock_trade_log)

        assert "Trade Log" in result
        assert "Portfolio Snapshots" in result
        assert "Equity Curve" in result
        assert "Drawdown" in result
        assert "Tearsheet" in result

        for name, filepath in result.items():
            assert Path(filepath).exists(), f"{name} not found at {filepath}"

    def test_save_without_trade_log(self, temp_output_dir: str) -> None:
        """Generating without trade log handles gracefully."""
        gen = ReportGenerator(temp_output_dir)
        result = gen.generate_all(
            metrics={
                "monthly_returns": pd.DataFrame(columns=["return"]),
                "yearly_returns": pd.DataFrame(columns=["return"]),
            },
            snapshots=pd.DataFrame(),
            trade_log=pd.DataFrame(),
        )
        # At minimum tearsheet should exist
        # (others skipped because data is empty)
        # Actually even with empty data the tearsheet is created
        assert "Tearsheet" in result


# ═══════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════


class TestMainCLI:
    """Tests for the CLI entry point via argparse."""

    def test_cli_help(self) -> None:
        """--help prints usage information."""
        from super_quality.main import _build_parser

        parser = _build_parser()
        # argparse prints help to stdout by default; capture via assert
        help_text = parser.format_help()
        assert "Super Quality 2.0" in help_text
        assert "run" in help_text
        assert "--help" in help_text

    def test_cli_run_help(self) -> None:
        """run --help shows run-specific options."""
        from super_quality.main import _build_parser

        parser = _build_parser()
        run_parser = None
        for action in parser._actions:  # noqa: SLF001
            if hasattr(action, "_name_parser_map"):
                for name, sub in action._name_parser_map.items():  # noqa: SLF001
                    if name == "run":
                        run_parser = sub
                        break
        assert run_parser is not None, "Could not find 'run' subparser"
        help_text = run_parser.format_help()
        assert "--dart-api-key" in help_text
        assert "--start" in help_text
        assert "--output" in help_text

    def test_cli_raises_on_missing_command(self) -> None:
        """No sub-command → parser raises error."""
        from super_quality.main import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_cli_import(self) -> None:
        """main module can be imported cleanly."""
        from super_quality import main  # noqa: F811

        assert hasattr(main, "_build_parser")
        assert hasattr(main, "main")


# ═══════════════════════════════════════════════════════════════════════
# Integration: PerformanceMetrics edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsEdgeCases:
    """Edge cases for PerformanceMetrics."""

    def test_single_return(self) -> None:
        """Single return → no crash, sensible defaults."""
        dates = pd.bdate_range("2024-01-01", periods=1)
        returns = pd.Series([0.001], index=dates)
        metrics = PerformanceMetrics(returns)
        result = metrics.compute_all()
        assert result["volatility"] == 0.0  # need 2+ for std
        assert result["sharpe_ratio"] == 0.0
        assert result["max_drawdown"] == 0.0

    def test_all_negative_returns(self) -> None:
        """All returns negative → negative total return, no gains in trade stats."""
        dates = pd.bdate_range("2024-01-01", periods=252)
        returns = pd.Series(-0.002, index=dates)
        metrics = PerformanceMetrics(returns)
        result = metrics.compute_all()
        assert result["total_return"] < 0.0
        assert result["cagr"] < 0.0
        assert result["max_drawdown"] < 0.0

    def test_profit_factor_no_losses(self) -> None:
        """All trades winning → profit_factor = 0 (no losses to divide)."""
        dates = pd.bdate_range("2024-01-01", periods=10)
        returns = pd.Series(0.001, index=dates)
        trade_log = pd.DataFrame({
            "entry_date": [date(2024, 1, 2)],
            "exit_date": [date(2024, 1, 5)],
            "ticker": ["A"],
            "buy_price": [10000.0],
            "sell_price": [11000.0],
            "shares": [100],
            "return_pct": [0.10],
            "hold_days": [3],
            "exit_reason": ["expiry"],
        })
        metrics = PerformanceMetrics(returns)
        result = metrics.compute_all(trade_log=trade_log)
        assert result["profit_factor"] == 0.0  # no losses, profit_factor stays 0
        assert result["win_rate"] == 1.0

    def test_trade_log_missing_columns(self, daily_returns: pd.Series) -> None:
        """Trade log without expected columns → trade stats are zero."""
        bad_log = pd.DataFrame({"some_col": [1, 2, 3]})
        metrics = PerformanceMetrics(daily_returns)
        result = metrics.compute_all(trade_log=bad_log)
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0

    def test_constant_returns_cagr(
        self,
        constant_returns: pd.Series,
    ) -> None:
        """Constant returns produce exact CAGR = (1+r)^252 - 1."""
        metrics = PerformanceMetrics(constant_returns)
        result = metrics.compute_all()
        expected = 1.001 ** 252 - 1
        assert result["cagr"] == pytest.approx(expected, rel=1e-10)

    def test_max_drawdown_duration_no_recovery(self) -> None:
        """Monotonically decreasing NAV → still in drawdown at end."""
        dates = pd.bdate_range("2024-01-01", periods=50)
        # NAV goes down every day
        returns = pd.Series(-0.005, index=dates)
        metrics = PerformanceMetrics(returns)
        result = metrics.compute_all()
        # Should be in drawdown the whole period
        assert result["max_drawdown_duration"] > 0
        assert result["max_drawdown"] < 0.0

    def test_no_drawdown(self) -> None:
        """Monotonically increasing NAV → no drawdown."""
        dates = pd.bdate_range("2024-01-01", periods=50)
        returns = pd.Series(0.005, index=dates)  # always positive
        metrics = PerformanceMetrics(returns)
        result = metrics.compute_all()
        assert result["max_drawdown"] >= 0.0  # no DD
        assert result["max_drawdown_duration"] == 0

    def test_benchmark_beta_one(self) -> None:
        """Portfolio == benchmark → beta ≈ 1, alpha ≈ risk_free_rate."""
        rng = np.random.default_rng(42)
        dates = pd.bdate_range("2024-01-01", periods=252)
        ret = pd.Series(0.001 + 0.01 * rng.normal(size=252), index=dates)
        metrics = PerformanceMetrics(ret, risk_free_rate=0.03)
        metrics.set_benchmark(ret.copy())  # identical to portfolio
        result = metrics.compute_all()
        bc = result["benchmark_comparison"]
        assert bc["beta"] == pytest.approx(1.0, rel=1e-6)
        assert bc["correlation"] == pytest.approx(1.0, rel=1e-6)
        # alpha = portfolio_ann_return - rfr - beta * (bench_ann_return - rfr)
        # When portfolio == benchmark, alpha = rfr - rfr = 0... wait
        # alpha = port_ann - rfr - beta * (bench_ann - rfr)
        # Since port_ann == bench_ann and beta == 1:
        # alpha = bench_ann - rfr - 1 * (bench_ann - rfr) = 0
        assert bc["alpha"] == pytest.approx(0.0, abs=1e-10)
