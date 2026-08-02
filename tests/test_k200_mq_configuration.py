"""Focused tests for K200MQ configuration wiring."""

from __future__ import annotations

from typing import Any

import pandas as pd

from k200_mq import main as main_module
from k200_mq.backtest import portfolio_engine
from k200_mq.config import K200MQConfig
from k200_mq.core.data import loader
from k200_mq.data import universe as universe_module
from k200_mq.factors import momentum as momentum_module


def test_run_cli_uses_documented_output_default_and_preserves_env(monkeypatch) -> None:
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    parser = main_module._build_parser()

    default_config = main_module._build_config(parser.parse_args(["run"]))

    assert default_config.OUTPUT_DIR == "outputs_k200mq"

    monkeypatch.setenv("OUTPUT_DIR", "configured-output")
    env_config = main_module._build_config(parser.parse_args(["run"]))

    assert env_config.OUTPUT_DIR == "configured-output"


def test_run_cli_passes_portfolio_and_liquidity_settings_to_config() -> None:
    parser = main_module._build_parser()
    args = parser.parse_args([
        "run",
        "--start", "2024-01-01",
        "--end", "2024-12-31",
        "--top-n", "12",
        "--max-holdings", "8",
        "--sector-cap", "0.22",
        "--min-adv-ratio", "0.04",
    ])

    config = main_module._build_config(args)

    assert config.TOP_N == 12
    assert config.MAX_HOLDINGS == 8
    assert config.SECTOR_CAP == 0.22
    assert config.MIN_ADV_RATIO == 0.04

    manifest = main_module._build_run_manifest(config)
    assert manifest["config"]["MAX_HOLDINGS"] == 8
    assert manifest["config"]["SECTOR_CAP"] == 0.22
    assert manifest["config"]["MIN_ADV_RATIO"] == 0.04


def test_disabled_regime_filter_skips_scaling_and_reports_manifest_status(
    monkeypatch, tmp_path,
) -> None:
    dates = pd.bdate_range("2024-01-02", periods=3)
    price_rows = [
        {
            "ticker": "A",
            "date": dt,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1_000_000.0,
            "mcap": 1_000_000.0,
        }
        for dt in dates
    ]
    price_data = pd.DataFrame(price_rows).set_index(["ticker", "date"])
    empty_warmup = pd.DataFrame(
        columns=price_data.columns,
        index=pd.MultiIndex.from_arrays([[], []], names=["ticker", "date"]),
    )

    monkeypatch.setattr(
        universe_module,
        "get_kospi200_history",
        lambda start, end, frequency: pd.DataFrame({
            "as_of": [dates[0]],
            "ticker": ["A"],
        }),
    )
    monkeypatch.setattr(
        loader,
        "get_price_data_with_lookback",
        lambda tickers, start, end, **kwargs: (price_data, empty_warmup),
    )

    def unexpected_index_download(*args, **kwargs):
        raise AssertionError("regime index data must not be loaded when disabled")

    monkeypatch.setattr(loader, "get_market_index", unexpected_index_download)

    class FakeMomentumFactor:
        def compute(self, data, **kwargs):
            return pd.DataFrame({
                "ticker": ["A"] * len(dates),
                "date": dates,
                "momentum_z": [1.0] * len(dates),
            })

    monkeypatch.setattr(momentum_module, "MomentumFactor", FakeMomentumFactor)

    captured: dict[str, Any] = {}

    class FakePortfolioRebalanceEngine:
        def __init__(self, config):
            self.config = config

        def run(self, *args, **kwargs):
            captured["index_data"] = args[1]
            captured["regime_scale_map"] = kwargs["regime_scale_map"]
            return {
                "portfolio_snapshots": pd.DataFrame(),
                "trade_log": pd.DataFrame(),
                "daily_returns": pd.Series(dtype=float),
            }

    monkeypatch.setattr(
        portfolio_engine,
        "PortfolioRebalanceEngine",
        FakePortfolioRebalanceEngine,
    )
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        main_module,
        "_save_results",
        lambda results, config: saved.update(results=results),
    )
    monkeypatch.setattr(main_module, "_print_summary", lambda results, config: None)

    config = K200MQConfig(
        START_DATE="2024-01-02",
        END_DATE="2024-01-04",
        OUTPUT_DIR=str(tmp_path),
        TOP_N=1,
        DART_API_KEY="",
        REGIME_FILTER_ENABLED=False,
    )
    main_module._run_pipeline(config)

    assert captured["regime_scale_map"] is None
    assert captured["index_data"].empty

    context = saved["results"]["_manifest_context"]
    assert context["regime_map"] == {
        "enabled": False,
        "status": "disabled",
        "mode": "disabled_by_config",
        "reason": "REGIME_FILTER_ENABLED=False",
        "applied": False,
        "covered_date_count": 0,
        "measured_date_count": len(dates),
        "coverage_ratio": 0.0,
    }
    manifest = main_module._build_run_manifest(config, context)
    assert manifest["config"]["REGIME_FILTER_ENABLED"] is False
    assert manifest["regime_map"]["status"] == "disabled"
