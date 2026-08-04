"""Unit tests for shared K200MQ preparation and interval execution."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pandas as pd
import pytest

from k200_mq.backtest import portfolio_engine
from k200_mq.config import K200MQConfig
from k200_mq.data import universe as universe_module
from k200_mq.validation.prepared import (
    PreparedK200MQInputs,
    _is_pit_ranking,
    execute_engine_interval,
)
from k200_mq.validation.walk_forward import CandidateSpec


def _prepared_inputs() -> PreparedK200MQInputs:
    dates = pd.bdate_range("2024-01-02", periods=8)
    rows = []
    for ticker, base in (("A", 10.0), ("B", 20.0)):
        for offset, current_date in enumerate(dates):
            price = base + offset
            rows.append({
                "ticker": ticker,
                "date": current_date,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1_000_000.0,
                "mcap": 1_000_000.0,
            })
    price_data = pd.DataFrame(rows).set_index(["ticker", "date"]).sort_index()
    factor_data = pd.DataFrame([
        {
            "ticker": ticker,
            "date": current_date,
            "momentum_z": 1.0 if ticker == "A" else 0.5,
            "quality_z": 1.0 if ticker == "A" else 0.5,
        }
        for ticker in ("A", "B")
        for current_date in dates
    ])
    universe = pd.DataFrame({
        "as_of": [dates[2], dates[5]],
        "ticker": ["A", "A"],
    })
    config = K200MQConfig(
        INITIAL_CAPITAL=1_000,
        TOP_N=1,
        EXCLUDE_KOSPI_TOP_N=0,
        MAX_POSITION_WEIGHT=1.0,
        COMMISSION_RATE=0.0,
        TAX_RATE=0.0,
        SLIPPAGE=0.0,
        REGIME_FILTER_ENABLED=True,
        DART_API_KEY="",
    )
    return PreparedK200MQInputs(
        price_data=price_data,
        factor_data=factor_data,
        index_data=pd.DataFrame(),
        universe_history=universe,
        regime_scale_map={dates[2]: 1.0, dates[5]: 1.0},
        measured_start=dates[0],
        measured_end=dates[-1],
        measured_dates=tuple(dates),
        active_trading_start=dates[2],
        runtime_config=config,
    )


def test_two_interval_executions_are_fresh_and_exclude_warmup_rows() -> None:
    prepared = _prepared_inputs()
    dates = pd.bdate_range("2024-01-02", periods=8)

    first = execute_engine_interval(
        prepared,
        CandidateSpec("FIRST", {"TOP_N": 1}),
        measured_start=dates[2],
        measured_end=dates[4],
        active_trading_start=dates[2],
    )
    second = execute_engine_interval(
        prepared,
        CandidateSpec("SECOND", {"TOP_N": 1}),
        measured_start=dates[5],
        measured_end=dates[7],
        active_trading_start=dates[5],
    )

    first_snapshots = first["portfolio_snapshots"]
    second_snapshots = second["portfolio_snapshots"]
    assert first_snapshots["date"].min() == dates[2]
    assert second_snapshots["date"].min() == dates[5]
    assert first_snapshots.iloc[0]["cash"] == 1_000
    assert second_snapshots.iloc[0]["cash"] == 1_000
    assert first_snapshots.iloc[0]["num_positions"] == 0
    assert second_snapshots.iloc[0]["num_positions"] == 0
    assert first["trade_log"]["entry_date"].min() == dates[3]
    assert second["trade_log"]["entry_date"].min() == dates[6]


def test_prepared_interval_disabled_stop_loss_ignores_retained_threshold() -> None:
    prepared = _prepared_inputs()
    dates = pd.bdate_range("2024-01-02", periods=8)
    price_data = prepared.price_data.copy(deep=True)
    # An enabled stop-loss with this threshold would queue a stop as soon as
    # the close falls below the prior peak.  The disabled candidate must not.
    price_data.loc[("A", dates[4]), "close"] = 1.0
    disabled_prepared = PreparedK200MQInputs(
        price_data=price_data,
        factor_data=prepared.factor_data,
        index_data=prepared.index_data,
        universe_history=prepared.universe_history,
        regime_scale_map=prepared.regime_scale_map,
        measured_start=prepared.measured_start,
        measured_end=prepared.measured_end,
        measured_dates=prepared.measured_dates,
        active_trading_start=prepared.active_trading_start,
        runtime_config=prepared.runtime_config,
    )

    result = execute_engine_interval(
        disabled_prepared,
        CandidateSpec(
            "STOP_LOSS_OFF",
            {"TOP_N": 1, "ENABLE_STOP_LOSS": False, "SL_STOP_LOSS": 0.25},
        ),
        measured_start=dates[2],
        measured_end=dates[-1],
        active_trading_start=dates[2],
    )

    assert not (result["trade_log"]["exit_reason"] == "stop_loss").any()


@pytest.mark.parametrize("threshold", [-1.0, 0.0])
def test_prepared_interval_rejects_invalid_active_stop_loss(threshold: float) -> None:
    with pytest.raises(ValueError, match="-1.0 < SL_STOP_LOSS < 0.0"):
        execute_engine_interval(
            _prepared_inputs(),
            CandidateSpec("INVALID_STOP", {"SL_STOP_LOSS": threshold}),
        )


def test_candidate_execution_does_not_mutate_shared_prepared_inputs() -> None:
    prepared = _prepared_inputs()
    factor_before = prepared.factor_data.copy(deep=True)
    universe_before = prepared.universe_history.copy(deep=True)
    prices_before = prepared.price_data.copy(deep=True)
    regime_before = dict(prepared.regime_scale_map or {})

    execute_engine_interval(
        prepared,
        CandidateSpec("REGIME_OFF", {"TOP_N": 1, "REGIME_FILTER_ENABLED": False}),
        measured_start=pd.Timestamp("2024-01-04"),
        measured_end=pd.Timestamp("2024-01-09"),
        active_trading_start=pd.Timestamp("2024-01-04"),
    )

    pd.testing.assert_frame_equal(prepared.factor_data, factor_before)
    pd.testing.assert_frame_equal(prepared.universe_history, universe_before)
    pd.testing.assert_frame_equal(prepared.price_data, prices_before)
    assert dict(prepared.regime_scale_map or {}) == regime_before


def test_cold_interval_execution_does_not_initialize_cache_or_write_files(tmp_path) -> None:
    """Strict prepared execution stays isolated from cache-backed data modules."""
    script = textwrap.dedent(
        """
        from pathlib import Path
        import sys

        import pandas as pd

        from k200_mq.data.provenance import _constituent_fingerprint
        from k200_mq.validation.prepared import (
            PreparedK200MQInputs,
            execute_engine_interval,
        )

        universe = pd.DataFrame({"as_of": ["2024-01-02"], "ticker": ["A"]})
        universe.attrs["provenance_by_as_of"] = {"2024-01-02": "pit"}
        universe.attrs["provenance_metadata_by_as_of"] = {
            "2024-01-02": {
                "label": "pit",
                "source": "test historical constituent file",
                "schema": {"as_of": "date", "ticker": "string"},
                "effective_date": "2024-01-01",
                "contract": "constituents_effective_on_or_before_as_of",
                "fingerprint": _constituent_fingerprint(["A"]),
            },
        }
        financial = pd.DataFrame({
            "ticker": ["A"],
            "filing_timestamp": ["2024-01-02T09:00:00+09:00"],
        })
        financial.attrs["financial_provenance_contract"] = {
            "source": "test raw filing source",
            "source_timezone": "Asia/Seoul",
            "cutoff_time": "15:30",
            "schema": {
                "filing_timestamp": {
                    "type": "timestamp",
                    "role": "filing availability timestamp",
                },
            },
        }

        prepared = PreparedK200MQInputs(
            price_data=pd.DataFrame(),
            factor_data=pd.DataFrame(),
            index_data=pd.DataFrame(),
            universe_history=universe,
            runtime_config={
                "STRICT_PIT_VALIDATION": True,
                "REGIME_FILTER_ENABLED": False,
                "EXCLUDE_KOSPI_TOP_N": 0,
            },
            financial_data=financial,
        )
        try:
            execute_engine_interval(prepared, None)
        except RuntimeError as error:
            assert "PIT provenance" in str(error)

        assert "k200_mq.data.universe" not in sys.modules
        assert "k200_mq.core.cache" not in sys.modules
        assert not Path("data").exists()
        assert not Path("outputs_k200mq").exists()
        """
    )
    env = os.environ.copy()
    source_root = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CACHE_HOME"] = str(tmp_path / "xdg-cache")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "outputs_k200mq").exists()
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "xdg-cache").exists()


def test_nonzero_exclusion_uses_prepared_ranking_without_loader_fallback(monkeypatch) -> None:
    base = _prepared_inputs()
    config = base.runtime_config.model_copy(update={"EXCLUDE_KOSPI_TOP_N": 1})
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        kospi_mcap_ranking=("A", "B"),
        ranking_status="non_pit_mechanical",
        ranking_provenance="current_market_cap_snapshot",
        runtime_config=config,
    )

    def unexpected_loader(*args, **kwargs):
        raise AssertionError("interval execution must not load an exclusion ranking")

    monkeypatch.setattr(universe_module, "exclude_kospi_top_n", unexpected_loader)

    result = execute_engine_interval(prepared, CandidateSpec("TOP_N", {"TOP_N": 1}))

    assert result["portfolio_snapshots"].iloc[0]["cash"] == 1_000
    assert prepared.ranking_status == "non_pit_mechanical"


def test_sector_cap_execution_uses_prepared_sector_map() -> None:
    base = _prepared_inputs()
    config = base.runtime_config.model_copy(update={
        "ENABLE_SECTOR_CAP": True,
        "SECTOR_CAP": 0.5,
        "LOCAL_PIT_SECTOR_PATH": "/tmp/mock_sector.csv",
    })
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        runtime_config=config,
        sector_map_by_as_of={
            "2024-01-04": {"A": "TECH", "B": "TECH"},
            "2024-01-09": {"A": "TECH", "B": "TECH"},
        },
    )

    result = execute_engine_interval(
        prepared,
        CandidateSpec("SECTOR_CAP", {"TOP_N": 1}),
        measured_start=pd.Timestamp("2024-01-04"),
        measured_end=pd.Timestamp("2024-01-09"),
        active_trading_start=pd.Timestamp("2024-01-04"),
    )

    assert not result["portfolio_snapshots"].empty


def test_sector_cap_execution_fails_without_prepared_sector_map() -> None:
    base = _prepared_inputs()
    config = base.runtime_config.model_copy(update={
        "ENABLE_SECTOR_CAP": True,
        "SECTOR_CAP": 0.5,
        "LOCAL_PIT_SECTOR_PATH": "/tmp/mock_sector.csv",
    })
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        runtime_config=config,
    )

    with pytest.raises(RuntimeError, match="prepared sector map"):
        execute_engine_interval(
            prepared,
            CandidateSpec("SECTOR_CAP", {"TOP_N": 1}),
            measured_start=pd.Timestamp("2024-01-04"),
            measured_end=pd.Timestamp("2024-01-09"),
            active_trading_start=pd.Timestamp("2024-01-04"),
        )


def test_enabled_exclusion_without_prepared_artifact_fails_explicitly() -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        runtime_config=base.runtime_config.model_copy(update={"EXCLUDE_KOSPI_TOP_N": 1}),
    )

    with pytest.raises(RuntimeError, match="prepared KOSPI market-cap ranking"):
        execute_engine_interval(prepared, CandidateSpec("TOP_N", {"TOP_N": 1}))


def test_mapping_runtime_config_keeps_initial_capital_and_filters_credentials() -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        runtime_config={
            "INITIAL_CAPITAL": 12_345,
            "DART_API_KEY": "secret",
            "KRX_PW": "secret",
        },
    )

    assert prepared.runtime_config["INITIAL_CAPITAL"] == 12_345
    assert "DART_API_KEY" not in prepared.runtime_config
    assert "KRX_PW" not in prepared.runtime_config


@pytest.mark.parametrize(
    ("runtime_config", "expected"),
    [
        (
            None,
            {
                "INITIAL_CAPITAL": 100_000_000,
                "COMMISSION_RATE": 0.00015,
                "TAX_RATE": 0.002,
                "SLIPPAGE": 0.001,
            },
        ),
        (
            {
                "INITIAL_CAPITAL": 12_345,
                "COMMISSION_RATE": 0.012,
                "TAX_RATE": 0.023,
                "SLIPPAGE": 0.034,
                "EXCLUDE_KOSPI_TOP_N": 0,
            },
            {
                "INITIAL_CAPITAL": 12_345,
                "COMMISSION_RATE": 0.012,
                "TAX_RATE": 0.023,
                "SLIPPAGE": 0.034,
            },
        ),
    ],
)
def test_interval_runtime_config_does_not_reload_environment(
    runtime_config, expected, monkeypatch,
) -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        runtime_config=runtime_config,
    )
    for field in expected:
        monkeypatch.setenv(field, "999999.9")

    observed = {}

    class SpyEngine:
        def __init__(self, config, **kwargs):
            del kwargs
            observed.update({field: getattr(config, field) for field in expected})

        def run(self, *args, **kwargs):
            del args, kwargs
            return {}

    monkeypatch.setattr(portfolio_engine, "PortfolioRebalanceEngine", SpyEngine)
    execute_engine_interval(
        prepared,
        CandidateSpec("CONFIG", {"EXCLUDE_KOSPI_TOP_N": 0}),
    )

    assert observed == expected


def test_regime_off_candidate_does_not_require_a_prepared_regime_map() -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        runtime_config=base.runtime_config,
    )

    result = execute_engine_interval(
        prepared,
        CandidateSpec("REGIME_OFF", {"TOP_N": 1, "REGIME_FILTER_ENABLED": False}),
    )

    assert "portfolio_snapshots" in result


def test_strict_candidate_rejects_non_pit_prepared_context_without_exclusion() -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        runtime_config=base.runtime_config.model_copy(
            update={"EXCLUDE_KOSPI_TOP_N": 0},
        ),
    )

    with pytest.raises(RuntimeError, match="universe provenance"):
        execute_engine_interval(
            prepared,
            CandidateSpec("STRICT", {"STRICT_PIT_VALIDATION": True}),
        )


def test_strict_execution_ignores_fabricated_pit_provenance_mappings() -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        provenance={
            "universe": {
                "provenance": "pit",
                "pit_valid": True,
                "provenance_by_as_of": {"history": "pit"},
            },
            "financials": {"mode": "pit_filing_date", "pit_valid": True},
        },
        runtime_config=base.runtime_config.model_copy(
            update={"EXCLUDE_KOSPI_TOP_N": 0},
        ),
    )

    with pytest.raises(RuntimeError, match="universe provenance"):
        execute_engine_interval(
            prepared,
            CandidateSpec("FABRICATED_PIT", {"STRICT_PIT_VALIDATION": True}),
        )


def test_strict_candidate_rejects_non_pit_financial_context() -> None:
    base = _prepared_inputs()
    pit_universe = base.universe_history.copy(deep=True)
    pit_universe.attrs["provenance_by_as_of"] = {
        date.isoformat(): "pit"
        for date in pd.to_datetime(pit_universe["as_of"]).dt.date.unique()
    }
    pit_universe.attrs["provenance_metadata_by_as_of"] = {
        date.isoformat(): universe_module._provenance_metadata(
            "pit", date, ["A"], "test historical constituent file",
        )
        for date in pd.to_datetime(pit_universe["as_of"]).dt.date.unique()
    }
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=pit_universe,
        regime_scale_map=base.regime_scale_map,
        provenance={
            "universe": {"provenance": "pit", "pit_valid": True},
            "financials": {"mode": "fiscal_period", "pit_valid": False},
        },
        runtime_config=base.runtime_config.model_copy(
            update={"EXCLUDE_KOSPI_TOP_N": 0},
        ),
    )

    with pytest.raises(RuntimeError, match="universe provenance"):
        execute_engine_interval(
            prepared,
            CandidateSpec("STRICT", {"STRICT_PIT_VALIDATION": True}),
        )


def test_strict_preparation_cannot_be_disabled_by_candidate() -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        regime_scale_map=base.regime_scale_map,
        runtime_config=base.runtime_config.model_copy(
            update={
                "EXCLUDE_KOSPI_TOP_N": 0,
                "STRICT_PIT_VALIDATION": True,
            },
        ),
    )

    with pytest.raises(RuntimeError, match="universe provenance"):
        execute_engine_interval(
            prepared,
            CandidateSpec("BYPASS", {"STRICT_PIT_VALIDATION": False}),
        )


@pytest.mark.parametrize(
    "field",
    [
        "MOMENTUM_WINDOW_LONG",
        "MOMENTUM_WINDOW_SHORT",
        "MOMENTUM_SKIP_DAYS",
        "REBALANCE_FREQ",
        "REGIME_MA_PERIOD",
        "REGIME_REDUCTION",
        "SECTOR_CAP",
        "MIN_ADV_RATIO",
        "MIN_CASH_RATIO",
        "USE_52WEEK_HIGH",
        "MAX_HOLDINGS",
        "QUALITY_MIN_TTM_QUARTERS",
    ],
)
def test_candidate_overrides_requiring_recomputation_are_rejected(field: str) -> None:
    prepared = _prepared_inputs()

    with pytest.raises(ValueError, match=field):
        execute_engine_interval(prepared, CandidateSpec("UNSAFE", {field: 7}))


def test_ranking_metadata_is_explicitly_non_pit_in_manifest() -> None:
    from k200_mq import main as main_module

    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        kospi_mcap_ranking=("A", "B"),
        ranking_status="non_pit_mechanical",
        ranking_provenance="current_market_cap_snapshot",
        manifest_context={"ranking": {
            "status": "pit_validated",
            "provenance": "historical_rank_file",
            "effective_date": "2024-01-01",
            "pit_valid": True,
            "artifact_available": True,
            "classification": "pit_validated",
            "fingerprint": "prepared",
        }},
    )

    manifest = main_module._build_run_manifest(
        K200MQConfig(EXCLUDE_KOSPI_TOP_N=1),
        dict(prepared.manifest_context),
    )

    assert manifest["ranking"]["status"] == "non_pit_mechanical"
    assert manifest["ranking"]["pit_valid"] is False
    assert manifest["ranking"]["classification"] == "non_pit_mechanical"
    assert "historical ranking is not claimed" in manifest["limitations"]["ranking"]


@pytest.mark.parametrize(
    ("effective_date", "fingerprint"),
    [
        (pd.Timestamp("2099-01-01"), "matching-looking-but-unvalidated"),
        (pd.Timestamp("2024-01-01"), "mismatched-fingerprint"),
    ],
)
def test_static_ranking_never_claims_pit_from_effective_date_or_fingerprint(
    effective_date: pd.Timestamp,
    fingerprint: str,
) -> None:
    base = _prepared_inputs()
    prepared = PreparedK200MQInputs(
        price_data=base.price_data,
        factor_data=base.factor_data,
        index_data=base.index_data,
        universe_history=base.universe_history,
        kospi_mcap_ranking=("A", "B"),
        ranking_status="pit_validated",
        ranking_provenance="historical_rank_file",
        ranking_effective_date=effective_date,
        ranking_fingerprint=fingerprint,
        ranking_pit_valid=True,
    )

    assert _is_pit_ranking(prepared) is False
    assert prepared.ranking_status == "non_pit_mechanical"
    assert prepared.ranking_effective_date is None
    assert prepared.ranking_pit_valid is False
