"""Focused tests for K200MQ configuration wiring."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

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


def test_run_cli_passes_supported_portfolio_settings_to_config() -> None:
    parser = main_module._build_parser()
    args = parser.parse_args([
        "run",
        "--start", "2024-01-01",
        "--end", "2024-12-31",
        "--top-n", "12",
        "--max-holdings", "8",
        "--enable-sector-cap",
        "--sector-cap", "0.22",
        "--enable-adv-filter",
        "--min-adv-ratio", "0.02",
        "--adv-lookback-days", "30",
        "--enable-correlation-filter",
        "--max-pair-correlation", "0.85",
        "--correlation-lookback-days", "90",
    ])

    config = main_module._build_config(args)

    assert config.TOP_N == 12
    assert config.MAX_HOLDINGS == 8
    assert config.ENABLE_SECTOR_CAP is True
    assert config.SECTOR_CAP == 0.22
    assert config.ENABLE_ADV_FILTER is True
    assert config.MIN_ADV_RATIO == 0.02
    assert config.ADV_LOOKBACK_DAYS == 30
    assert config.ENABLE_CORRELATION_FILTER is True
    assert config.MAX_PAIR_CORRELATION == 0.85
    assert config.CORRELATION_LOOKBACK_DAYS == 90

    manifest = main_module._build_run_manifest(config)
    assert manifest["config"]["MAX_HOLDINGS"] == 8
    assert manifest["config"]["ENABLE_SECTOR_CAP"] is True
    assert manifest["config"]["SECTOR_CAP"] == 0.22
    assert manifest["config"]["ENABLE_ADV_FILTER"] is True
    assert manifest["config"]["MIN_ADV_RATIO"] == 0.02
    assert manifest["config"]["ADV_LOOKBACK_DAYS"] == 30
    assert manifest["config"]["ENABLE_CORRELATION_FILTER"] is True
    assert manifest["config"]["MAX_PAIR_CORRELATION"] == 0.85
    assert manifest["config"]["CORRELATION_LOOKBACK_DAYS"] == 90


def test_true_walkforward_cli_wires_strict_local_pit_options_to_config() -> None:
    parser = main_module._build_parser()
    args = parser.parse_args([
        "true-walkforward",
        "--strict-pit",
        "--local-pit-universe-path", "local_universe.csv",
        "--local-pit-universe-source-kind", "intervals",
        "--local-pit-universe-manifest", "local_universe.manifest.json",
        "--output", "out",
    ])

    config = main_module._build_config(args)

    assert config.STRICT_PIT_VALIDATION is True
    assert config.LOCAL_PIT_UNIVERSE_PATH == "local_universe.csv"
    assert config.LOCAL_PIT_UNIVERSE_SOURCE_KIND == "intervals"
    assert config.LOCAL_PIT_UNIVERSE_MANIFEST == "local_universe.manifest.json"
    assert config.OUTPUT_DIR == "out"


def test_config_rejects_invalid_correlation_filter_bounds_when_enabled() -> None:
    with pytest.raises(ValueError, match="MAX_PAIR_CORRELATION"):
        K200MQConfig(ENABLE_CORRELATION_FILTER=True, MAX_PAIR_CORRELATION=1.1)
    with pytest.raises(ValueError, match="CORRELATION_LOOKBACK_DAYS"):
        K200MQConfig(ENABLE_CORRELATION_FILTER=True, CORRELATION_LOOKBACK_DAYS=10)


def test_run_cli_accepts_adv_filter_options() -> None:
    parser = main_module._build_parser()
    args = parser.parse_args([
        "run",
        "--enable-adv-filter",
        "--min-adv-ratio", "0.03",
        "--adv-lookback-days", "40",
    ])
    config = main_module._build_config(args)

    assert config.ENABLE_ADV_FILTER is True
    assert config.MIN_ADV_RATIO == 0.03
    assert config.ADV_LOOKBACK_DAYS == 40


def test_adv_filter_validation_requires_valid_bounds_when_enabled() -> None:
    with pytest.raises(ValueError, match="MIN_ADV_RATIO"):
        K200MQConfig(ENABLE_ADV_FILTER=True, MIN_ADV_RATIO=1.1)
    with pytest.raises(ValueError, match="ADV_LOOKBACK_DAYS"):
        K200MQConfig(ENABLE_ADV_FILTER=True, ADV_LOOKBACK_DAYS=3)


def test_runtime_requires_local_sector_source_when_sector_cap_enabled() -> None:
    with pytest.raises(RuntimeError, match="LOCAL_PIT_SECTOR_PATH"):
        main_module._enforce_deferred_runtime_options(K200MQConfig(ENABLE_SECTOR_CAP=True))


def test_runtime_allows_sector_cap_when_local_sector_source_is_configured() -> None:
    config = K200MQConfig(
        ENABLE_SECTOR_CAP=True,
        LOCAL_PIT_SECTOR_PATH="/tmp/mock_sector.csv",
        SECTOR_CAP=0.25,
    )
    main_module._enforce_deferred_runtime_options(config)


def test_runtime_allows_default_values_for_deferred_options() -> None:
    config = K200MQConfig()
    main_module._enforce_deferred_runtime_options(config)


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

    index_data = pd.DataFrame(
        {"close": [100.0, 101.0, 102.0]},
        index=dates,
    )

    monkeypatch.setattr(loader, "get_market_index", lambda *args, **kwargs: index_data)

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
    pd.testing.assert_frame_equal(captured["index_data"], index_data)

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
