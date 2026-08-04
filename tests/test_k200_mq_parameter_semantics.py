"""Regression tests for K200MQ parameter semantics before sensitivity runs."""

from __future__ import annotations

import pandas as pd
import pytest

from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.config import K200MQConfig
from k200_mq.factors.momentum import MomentumFactor
from k200_mq.factors.quality import QualityFactor
from k200_mq.factors.regime import RegimeFactor
from k200_mq.strategies.momentum_quality import MomentumQualityStrategy
from k200_mq.main import _build_config, _build_parser, _build_run_manifest


def test_momentum_uses_skipped_return_endpoint_and_long_origin() -> None:
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    prices = pd.DataFrame({
        "ticker": ["A"] * len(dates),
        "date": dates,
        "close": [10.0, 11.0, 13.0, 17.0, 19.0, 23.0, 29.0, 31.0],
    })

    result = MomentumFactor().compute(
        prices,
        long_window=5,
        short_window=2,
        skip_days=2,
    )
    row = result.loc[result["date"] == dates[5]].iloc[0]

    # close[t-skip_days] / close[t-long_window] - 1 = close[3] / close[0] - 1.
    assert row["momentum"] == pytest.approx(17.0 / 10.0 - 1.0)
    assert row["momentum_6m"] == pytest.approx(23.0 / 17.0 - 1.0)

    no_skip = MomentumFactor().compute(
        prices,
        long_window=5,
        short_window=2,
        skip_days=0,
    )
    no_skip_row = no_skip.loc[no_skip["date"] == dates[5]].iloc[0]
    # With no skipped endpoint, close[t] / close[t-long_window] remains intact.
    assert no_skip_row["momentum"] == pytest.approx(23.0 / 10.0 - 1.0)


def _quality_data() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["A", "B", "C", "D"],
        "date": pd.Timestamp("2024-01-31"),
        "net_income": [10.0, 20.0, 30.0, 40.0],
        "total_equity": [100.0] * 4,
        "total_debt": [10.0, 20.0, 30.0, 40.0],
        "revenue": [100.0] * 4,
        "operating_income": [10.0, 12.0, 14.0, 16.0],
        "operating_cf": [10.0, 20.0, 30.0, 40.0],
    })


def test_quality_weights_are_normalized_and_change_composite() -> None:
    data = _quality_data()
    roe_only = QualityFactor(weight_roe=2.0, weight_de=0.0, weight_opmargin=0.0,
                             weight_cashconv=0.0)
    de_only = QualityFactor(weight_roe=0.0, weight_de=3.0, weight_opmargin=0.0,
                            weight_cashconv=0.0)

    roe_result = roe_only.compute(data)
    de_result = de_only.compute(data)

    assert sum(roe_only.weights.values()) == pytest.approx(1.0)
    assert sum(de_only.weights.values()) == pytest.approx(1.0)
    assert not roe_result["quality_composite_z"].equals(de_result["quality_composite_z"])


def test_quality_weights_reject_negative_or_zero_sum() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        QualityFactor(weight_roe=-1.0)
    with pytest.raises(ValueError, match="positive sum"):
        QualityFactor(weight_roe=0.0, weight_de=0.0, weight_opmargin=0.0, weight_cashconv=0.0)
    with pytest.raises(ValueError, match="positive sum"):
        K200MQConfig(
            QUALITY_WEIGHT_ROE=0.0,
            QUALITY_WEIGHT_DE=0.0,
            QUALITY_WEIGHT_OPMARGIN=0.0,
            QUALITY_WEIGHT_CASHCONV=0.0,
        )


def test_quality_missing_component_remains_neutral() -> None:
    data = _quality_data()
    data.loc[data["ticker"] == "A", "operating_cf"] = float("nan")
    data.loc[data["ticker"] == "B", "operating_cf"] = 10.0
    data.loc[data["ticker"] == "C", "operating_cf"] = 60.0
    data.loc[data["ticker"] == "D", "operating_cf"] = 20.0

    result = QualityFactor().compute(data)
    row = result.loc[result["ticker"] == "A"].iloc[0]

    expected_without_cash = (
        0.35 * row["roe_z"]
        + 0.25 * row["de_z"]
        + 0.20 * row["opmargin_z"]
    )
    assert pd.isna(row["cashconv_z"])
    assert row["quality_composite_z"] == pytest.approx(expected_without_cash)


def test_regime_min_return_is_bullish_threshold() -> None:
    dates = pd.date_range("2024-01-01", periods=4, freq="B")
    data = pd.DataFrame({"date": dates, "close": [100.0, 100.0, 102.0, 103.0]})

    default = RegimeFactor().compute(data, ma_period=3, min_return_days=1)
    stricter = RegimeFactor().compute(
        data,
        ma_period=3,
        min_return_days=1,
        min_return=0.025,
    )

    assert bool(default.loc[2, "regime"])
    assert not bool(stricter.loc[2, "regime"])


def test_stop_loss_config_has_explicit_safe_semantics() -> None:
    assert K200MQConfig().ENABLE_STOP_LOSS is True
    assert K200MQConfig(ENABLE_STOP_LOSS=False).ENABLE_STOP_LOSS is False
    for threshold in (-1.0, -1.01, 0.0, 0.01):
        with pytest.raises(ValueError, match="-1.0 < SL_STOP_LOSS < 0.0"):
            K200MQConfig(SL_STOP_LOSS=threshold)

    # A disabled stop-loss may retain an arbitrary finite threshold for
    # reproducibility, but the engine must not interpret it as a trigger.
    for threshold in (-1.0, 0.0, 0.25):
        disabled = K200MQConfig(ENABLE_STOP_LOSS=False, SL_STOP_LOSS=threshold)
        assert disabled.SL_STOP_LOSS == threshold

    cli_config = _build_config(_build_parser().parse_args(["run", "--disable-stop-loss"]))
    assert cli_config.ENABLE_STOP_LOSS is False


def test_manifest_records_factor_formula_versions() -> None:
    manifest = _build_run_manifest(
        K200MQConfig(
            QUALITY_WEIGHT_ROE=2.0,
            QUALITY_WEIGHT_DE=1.0,
            QUALITY_WEIGHT_OPMARGIN=1.0,
            QUALITY_WEIGHT_CASHCONV=0.0,
        )
    )
    definitions = manifest["factors"]["definitions"]

    assert definitions["momentum"]["formula"] == "close[t-skip_days] / close[t-long_window] - 1"
    assert definitions["momentum"]["default_formula"] == "close[t-42] / close[t-252] - 1"
    assert definitions["momentum"]["version"].endswith("-v4")
    assert definitions["quality"]["ttm_filter"] == "unsupported/inert"
    assert definitions["quality"]["configured_raw_weights"] == {
        "roe": 2.0,
        "de": 1.0,
        "opmargin": 1.0,
        "cashconv": 0.0,
    }
    assert definitions["quality"]["effective_normalized_weights"] == {
        "roe": pytest.approx(0.5),
        "de": pytest.approx(0.25),
        "opmargin": pytest.approx(0.25),
        "cashconv": pytest.approx(0.0),
    }
    assert definitions["quality"]["weights_used"] == "effective_normalized_weights"
    assert manifest["quality"]["weights"]["configured_raw_weights"] == definitions[
        "quality"
    ]["configured_raw_weights"]
    assert manifest["quality"]["weights"]["effective_normalized_weights"] == definitions[
        "quality"
    ]["effective_normalized_weights"]
    assert definitions["regime"]["min_return"] == 0.0


def test_manifest_does_not_allow_stale_factor_definitions_to_override_current_semantics() -> None:
    config = K200MQConfig(REGIME_MIN_RETURN=0.025)
    manifest = _build_run_manifest(
        config,
        {
            "factors": {
                "row_count": 10,
                "definitions": {
                    "momentum": {
                        "version": "stale-momentum-v1",
                        "formula": "close[t] / close[t-21] - 1",
                        "default_formula": "stale default",
                        "ranking_column": "stale_rank",
                        "source": "legacy-cache",
                    },
                    "regime": {
                        "version": "stale-regime-v1",
                        "formula": "always bullish",
                        "min_return": -0.25,
                        "return_window_days": 5,
                        "threshold_semantics": "legacy threshold",
                        "source": "legacy-cache",
                    },
                },
            },
        },
    )

    definitions = manifest["factors"]["definitions"]
    assert definitions["momentum"]["version"].endswith("-v4")
    assert definitions["momentum"]["formula"] == (
        "close[t-skip_days] / close[t-long_window] - 1"
    )
    assert definitions["momentum"]["default_formula"] == "close[t-42] / close[t-252] - 1"
    assert definitions["momentum"]["ranking_column"] == "momentum_z"
    assert definitions["momentum"]["source"] == "legacy-cache"
    assert definitions["regime"]["version"].endswith("-v2")
    assert definitions["regime"]["formula"] == (
        "close > rolling_ma(ma_period) and cum_return(min_return_days) > min_return"
    )
    assert definitions["regime"]["min_return"] == 0.025
    assert definitions["regime"]["return_window_days"] == 20
    assert "20-trading-day cumulative" in definitions["regime"]["threshold_semantics"]
    assert definitions["regime"]["source"] == "legacy-cache"


def test_diagnostic_short_momentum_window_does_not_gate_ranking_rows() -> None:
    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    prices = pd.DataFrame({
        "ticker": ["A"] * len(dates),
        "date": dates,
        "close": [10.0, 11.0, 13.0, 17.0, 19.0, 23.0],
    })

    result = MomentumFactor().compute(
        prices,
        long_window=3,
        short_window=99,
        skip_days=1,
    )

    assert not result.empty
    assert result["momentum_z"].notna().all()
    assert result["momentum_6m"].isna().all()


def test_inert_run_cli_options_are_rejected_instead_of_ignored() -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--no-cache"])
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--rebalance-lookback", "252"])
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--min-adv-ratio"])


def test_true_walkforward_does_not_expose_run_only_stop_loss_flags() -> None:
    args = _build_parser().parse_args(["true-walkforward"])

    assert not hasattr(args, "enable_stop_loss")
    assert not hasattr(args, "stop_loss")


def test_disabled_stop_loss_does_not_generate_stop_orders() -> None:
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    price_data = pd.DataFrame([
        {
            "ticker": "A",
            "date": current_date,
            "open": open_price,
            "high": max(open_price, close_price),
            "low": min(open_price, close_price),
            "close": close_price,
            "volume": 1_000_000.0,
            "mcap": 1_000_000.0,
        }
        for current_date, open_price, close_price in zip(
            dates,
            [10.0, 20.0, 19.0, 5.0],
            [10.0, 20.0, 16.0, 5.0],
        )
    ]).set_index(["ticker", "date"])
    factors = pd.DataFrame({
        "ticker": ["A"] * len(dates),
        "date": dates,
        "momentum_z": [1.0] * len(dates),
        "quality_z": [1.0] * len(dates),
    })
    config = K200MQConfig(
        INITIAL_CAPITAL=1_000,
        TOP_N=1,
        EXCLUDE_KOSPI_TOP_N=0,
        MAX_POSITION_WEIGHT=1.0,
        COMMISSION_RATE=0.0,
        TAX_RATE=0.0,
        SLIPPAGE=0.0,
        ENABLE_STOP_LOSS=False,
    )

    result = PortfolioRebalanceEngine(config).run(
        price_data,
        pd.DataFrame(),
        factors,
        pd.DataFrame({"as_of": [dates[0]], "ticker": ["A"]}),
    )

    assert not (result["trade_log"]["exit_reason"] == "stop_loss").any()


def test_portfolio_limit_config_validation_is_explicit() -> None:
    with pytest.raises(ValueError, match="MAX_HOLDINGS"):
        K200MQConfig(MAX_HOLDINGS=0)
    with pytest.raises(ValueError, match="MIN_CASH_RATIO"):
        K200MQConfig(MIN_CASH_RATIO=-0.01)
    with pytest.raises(ValueError, match="MIN_CASH_RATIO"):
        K200MQConfig(MIN_CASH_RATIO=1.01)


def test_strategy_caps_selected_count_by_max_holdings() -> None:
    strategy = MomentumQualityStrategy(
        K200MQConfig(
            TOP_N=3,
            MAX_HOLDINGS=2,
            EXCLUDE_KOSPI_TOP_N=0,
            MAX_POSITION_WEIGHT=1.0,
        )
    )
    factors = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "momentum_z": [3.0, 2.0, 1.0],
        "quality_z": [0.0, 0.0, 0.0],
    })

    selected = strategy.select_portfolio(factors, ["A", "B", "C"], pd.Timestamp("2024-01-31"))

    assert [row["ticker"] for row in selected] == ["A", "B"]
    assert len(selected) == 2
    assert sum(row["weight"] for row in selected) == pytest.approx(1.0)


def test_sector_cap_requires_sector_map_when_enabled() -> None:
    strategy = MomentumQualityStrategy(
        K200MQConfig(
            TOP_N=2,
            ENABLE_SECTOR_CAP=True,
            LOCAL_PIT_SECTOR_PATH="/tmp/mock_sector.csv",
            SECTOR_CAP=0.5,
            EXCLUDE_KOSPI_TOP_N=0,
        )
    )
    factors = pd.DataFrame({
        "ticker": ["A", "B"],
        "momentum_z": [1.0, 0.9],
        "quality_z": [1.0, 0.9],
    })

    with pytest.raises(RuntimeError, match="prepared sector map"):
        strategy.select_portfolio(factors, ["A", "B"], pd.Timestamp("2024-01-31"))


def test_sector_cap_is_applied_when_sector_map_is_available() -> None:
    strategy = MomentumQualityStrategy(
        K200MQConfig(
            TOP_N=3,
            WEIGHT_METHOD="equal",
            ENABLE_SECTOR_CAP=True,
            LOCAL_PIT_SECTOR_PATH="/tmp/mock_sector.csv",
            SECTOR_CAP=0.5,
            EXCLUDE_KOSPI_TOP_N=0,
            MAX_POSITION_WEIGHT=1.0,
        ),
        sector_map_by_as_of={
            "2024-01-31": {
                "A": "TECH",
                "B": "TECH",
                "C": "FIN",
            }
        },
    )
    factors = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "momentum_z": [3.0, 2.0, 1.0],
        "quality_z": [0.0, 0.0, 0.0],
    })

    selected = strategy.select_portfolio(
        factors,
        ["A", "B", "C"],
        pd.Timestamp("2024-01-31"),
    )
    by_ticker = {row["ticker"]: row["weight"] for row in selected}

    assert sum(by_ticker.values()) == pytest.approx(1.0)
    assert by_ticker["A"] == pytest.approx(0.25)
    assert by_ticker["B"] == pytest.approx(0.25)
    assert by_ticker["C"] == pytest.approx(0.50)


def test_correlation_filter_requires_pairwise_data_when_enabled() -> None:
    strategy = MomentumQualityStrategy(
        K200MQConfig(
            TOP_N=3,
            ENABLE_CORRELATION_FILTER=True,
            MAX_PAIR_CORRELATION=0.8,
            EXCLUDE_KOSPI_TOP_N=0,
        )
    )
    factors = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "momentum_z": [3.0, 2.0, 1.0],
        "quality_z": [0.0, 0.0, 0.0],
    })

    with pytest.raises(RuntimeError, match="pairwise correlation"):
        strategy.select_portfolio(factors, ["A", "B", "C"], pd.Timestamp("2024-01-31"))


def test_correlation_filter_reduces_highly_correlated_pairs() -> None:
    strategy = MomentumQualityStrategy(
        K200MQConfig(
            TOP_N=3,
            WEIGHT_METHOD="equal",
            ENABLE_CORRELATION_FILTER=True,
            MAX_PAIR_CORRELATION=0.8,
            EXCLUDE_KOSPI_TOP_N=0,
            MAX_POSITION_WEIGHT=1.0,
        )
    )
    factors = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "momentum_z": [3.0, 2.0, 1.0],
        "quality_z": [0.0, 0.0, 0.0],
    })
    pair_map = {
        ("A", "B"): 0.95,
        ("A", "C"): 0.30,
        ("B", "C"): 0.25,
    }

    selected = strategy.select_portfolio(
        factors,
        ["A", "B", "C"],
        pd.Timestamp("2024-01-31"),
        pair_correlation_map=pair_map,
    )
    tickers = [row["ticker"] for row in selected]

    assert tickers == ["A", "C"]
    assert sum(row["weight"] for row in selected) == pytest.approx(1.0)


def test_min_cash_ratio_reserves_cash_during_rebalance_buys() -> None:
    dates = pd.date_range("2024-01-02", periods=2, freq="B")
    price_data = pd.DataFrame([
        {
            "ticker": "A",
            "date": current_date,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1_000_000.0,
            "mcap": 1_000_000.0,
        }
        for current_date in dates
    ]).set_index(["ticker", "date"])
    factors = pd.DataFrame({
        "ticker": ["A", "A"],
        "date": dates,
        "momentum_z": [1.0, 1.0],
        "quality_z": [0.0, 0.0],
    })
    config = K200MQConfig(
        INITIAL_CAPITAL=1_000,
        TOP_N=1,
        MAX_HOLDINGS=1,
        MIN_CASH_RATIO=0.20,
        EXCLUDE_KOSPI_TOP_N=0,
        MAX_POSITION_WEIGHT=1.0,
        COMMISSION_RATE=0.0,
        TAX_RATE=0.0,
        SLIPPAGE=0.0,
    )

    result = PortfolioRebalanceEngine(config).run(
        price_data,
        pd.DataFrame(),
        factors,
        pd.DataFrame({"as_of": [dates[0]], "ticker": ["A"]}),
    )

    final_cash = float(result["portfolio_snapshots"].iloc[-1]["cash"])
    buy_trade = result["trade_log"].loc[result["trade_log"]["buy_price"].notna()].iloc[0]

    assert final_cash == pytest.approx(200.0)
    assert int(buy_trade["shares"]) == 80
