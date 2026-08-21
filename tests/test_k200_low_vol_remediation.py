"""Synthetic remediation tests for the guarded low-volatility lane."""

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from k200_low_vol import (
    LowVolSpec,
    LowVolatilityExecutionAdapter,
    LowVolatilitySelector,
    PendingCloseTarget,
    PriceActionBundle,
    SyntheticBundleError,
    build_cutoff_validation_evidence,
    build_pit_universe_evidence,
    build_synthetic_manifest,
    build_validated_price_action_evidence,
    construct_prices,
    execute_fold_carry_in,
)
from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.config import K200MQConfig


def _config(**overrides: object) -> K200MQConfig:
    values: dict[str, object] = {
        "INITIAL_CAPITAL": 10_000.0,
        "MAX_HOLDINGS": 10,
        "TOP_N": 10,
        "EXCLUDE_KOSPI_TOP_N": 0,
        "MAX_POSITION_WEIGHT": 1.0,
        "MIN_CASH_RATIO": 0.0,
        "COMMISSION_RATE": 0.0,
        "TAX_RATE": 0.0,
        "SLIPPAGE": 0.0,
        "ENABLE_STOP_LOSS": False,
        "REGIME_FILTER_ENABLED": False,
        "CONTINUOUS_REGIME": False,
        "REGIME_REDUCTION": 0.0,
        "QUALITY_PRIMARY": False,
        "ENABLE_ADV_FILTER": False,
        "ENABLE_CORRELATION_FILTER": False,
        "ENABLE_SECTOR_CAP": False,
        "ENABLE_DELISTING_DETECTION": False,
    }
    values.update(overrides)
    return K200MQConfig.model_validate(values)


def _panel(count: int = 500, tickers: int = 10) -> pd.DataFrame:
    dates = pd.bdate_range(end="2024-12-31", periods=count)
    rows: list[dict[str, object]] = []
    for number in range(tickers):
        ticker = f"{number:06d}"
        close = np.full(count, 100.0)
        for dt, value in zip(dates, close, strict=True):
            rows.append({
                "ticker": ticker,
                "security_id": f"sid-{ticker}",
                "date": dt,
                "open": value,
                "high": value,
                "low": value,
                "close": value,
                "volume": 1_000.0,
                "observed": True,
                "suspended": False,
                "stale": False,
                "missing": False,
            })
    return pd.DataFrame(rows)


def _adapter(
    panel: pd.DataFrame,
    *,
    action_rows: pd.DataFrame | None = None,
    universe_date: Any = None,
    **config: Any,
):
    actions = action_rows if action_rows is not None else pd.DataFrame(
        columns=["ticker", "security_id", "action_date", "action_type", "resolved"]
    )
    bundle = PriceActionBundle(panel, actions, build_synthetic_manifest(panel, actions))
    snapshot_dates = (
        [pd.Timestamp(value) for value in (
            "2023-12-29", "2024-03-29", "2024-06-28", "2024-09-30", "2024-12-31",
        )]
        if universe_date is None
        else [pd.Timestamp(universe_date)]
    )
    universe_rows = pd.DataFrame({
        "as_of": [value for value in snapshot_dates for _ in range(10)],
        "ticker": [f"{number:06d}" for _ in snapshot_dates for number in range(10)],
    })
    cutoff = build_cutoff_validation_evidence(
        "2024-12-31", price_rows=panel, universe_rows=universe_rows
    )
    return LowVolatilityExecutionAdapter(
        LowVolatilitySelector(),
        LowVolSpec(),
        build_validated_price_action_evidence(bundle, cutoff),
        build_pit_universe_evidence(universe_rows),
        cutoff,
    ), config


def test_actual_selector_adapter_preserves_bottom20_equal_weight_and_neutral_controls() -> None:
    panel = _panel()
    adapter, config = _adapter(panel)
    engine = PortfolioRebalanceEngine(_config(**config))
    result = adapter.run(engine)
    buys = result["trade_log"][result["trade_log"]["entry_notional"] > 0]
    assert set(buys["ticker"]) == {"000000", "000001"}
    assert buys["entry_notional"].sum() == pytest.approx(10_000.0)


def test_non_quarter_universe_snapshot_cannot_trigger_selection() -> None:
    adapter, config = _adapter(_panel(), universe_date="2024-09-27")
    with pytest.raises(SyntheticBundleError):
        adapter.run(PortfolioRebalanceEngine(_config(**config)))


@pytest.mark.parametrize("field,value", [
    ("ENABLE_ADV_FILTER", True),
    ("ENABLE_CORRELATION_FILTER", True),
    ("ENABLE_SECTOR_CAP", True),
    ("ENABLE_STOP_LOSS", True),
    ("ENABLE_DELISTING_DETECTION", True),
    ("MIN_CASH_RATIO", 0.1),
    ("MAX_HOLDINGS", 1),
])
def test_adapter_fails_closed_for_mq_controls(field: str, value: object) -> None:
    adapter, config = _adapter(_panel(), **{field: value})
    with pytest.raises(SyntheticBundleError):
        adapter.run(PortfolioRebalanceEngine(_config(**config)))


def test_global_session_positions_do_not_extend_missing_ticker_window() -> None:
    from k200_low_vol import LowVolatilityFactor

    panel = _panel(253, 2)
    missing_date = panel.loc[panel["ticker"].eq("000000")].iloc[2]["date"]
    panel = panel[~(panel["ticker"].eq("000000") & panel["date"].eq(missing_date))]
    result = LowVolatilityFactor().compute(panel)
    row = result[(result["ticker"] == "000000") & (result["date"] == pd.Timestamp("2024-12-31"))]
    assert row.iloc[0]["valid_return_count"] == 249


def test_casefolded_split_is_applied_before_open_and_preserves_position_value() -> None:
    panel = _panel()
    split_date = pd.Timestamp("2024-12-30")
    panel.loc[(panel["ticker"] == "000000") & (panel["date"] >= split_date), ["open", "high", "low", "close"]] /= 2.0
    actions = pd.DataFrame([{
        "ticker": "000000", "security_id": "sid-000000", "action_date": split_date,
        "action_type": "SpLiT", "ratio": 2.0, "resolved": True, "confirmed": True,
    }])
    adapter, config = _adapter(panel, action_rows=actions)
    result = adapter.run(PortfolioRebalanceEngine(_config(**config)))
    prices = construct_prices(adapter.price_action.bundle)
    before = prices[(prices["ticker"] == "000000") & (prices["date"] == pd.Timestamp("2024-12-27"))].iloc[0]
    after = prices[(prices["ticker"] == "000000") & (prices["date"] == split_date)].iloc[0]
    assert after["constructed_close"] == pytest.approx(before["constructed_close"])
    assert result["portfolio_snapshots"]["nav"].iloc[-1] == pytest.approx(10_000.0)


def test_suspension_blocks_trading_and_retains_last_official_close_nav() -> None:
    panel = _panel()
    action_date = pd.Timestamp("2024-12-30")
    panel.loc[
        (panel["ticker"] == "000000") & (panel["date"] >= action_date), "close"
    ] = 777.0
    actions = pd.DataFrame([{
        "ticker": "000000", "security_id": "sid-000000", "action_date": action_date,
        "action_type": "suspension", "resolved": True, "confirmed": True,
    }])
    adapter, config = _adapter(panel, action_rows=actions)
    result = adapter.run(PortfolioRebalanceEngine(_config(**config)))
    sells = result["trade_log"][result["trade_log"]["exit_notional"] > 0]
    assert sells.empty
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 2
    assert result["portfolio_snapshots"]["nav"].iloc[-1] == pytest.approx(10_000.0)


@pytest.mark.parametrize("confirmed,recovery,expected_nav", [
    (True, 25.0, 6_250.0),
    (False, None, 5_000.0),
])
def test_delisting_closes_position_using_confirmed_or_zero_recovery(
    confirmed: bool,
    recovery: float | None,
    expected_nav: float,
) -> None:
    panel = _panel()
    action: dict[str, object] = {
        "ticker": "000000", "security_id": "sid-000000", "action_date": "2024-12-30",
        "action_type": "delisting", "resolved": True, "confirmed": confirmed,
    }
    if recovery is not None:
        action["recovery_value"] = recovery
    actions = pd.DataFrame([action])
    adapter, config = _adapter(panel, action_rows=actions)
    result = adapter.run(PortfolioRebalanceEngine(_config(**config)))
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 1
    assert result["portfolio_snapshots"]["nav"].iloc[-1] == pytest.approx(expected_nav)


def test_reverse_split_rejects_non_integral_position_without_rounding() -> None:
    panel = _panel()
    actions = pd.DataFrame([{
        "ticker": "000000", "security_id": "sid-000000", "action_date": "2024-12-30",
        "action_type": "reverse_split", "ratio": 0.5,
        "resolved": True, "confirmed": True,
    }])
    adapter, _config_values = _adapter(panel, action_rows=actions)
    hook = adapter._corporate_action_hook(adapter.price_action.bundle)
    positions = {"000000": {"shares": 3, "entry_price": 100.0, "peak_price": 100.0}}
    with pytest.raises(SyntheticBundleError):
        hook(positions, pd.Timestamp("2024-12-30"), None)
    assert positions["000000"]["shares"] == 3


def test_unresolved_action_is_rejected_before_adapter_execution() -> None:
    panel = _panel()
    actions = pd.DataFrame([{
        "ticker": "000000", "security_id": "sid-000000", "action_date": "2024-12-30",
        "action_type": "split", "ratio": 2.0, "resolved": False, "confirmed": True,
    }])
    with pytest.raises(SyntheticBundleError):
        _adapter(panel, action_rows=actions)


def test_fold_carry_in_counts_only_first_oos_open_and_cancels_final_close() -> None:
    rows = pd.DataFrame({
        "ticker": ["A", "A"],
        "date": pd.to_datetime(["2024-12-30", "2024-12-31"]),
        "open": [np.nan, 10.0],
    })
    rows.attrs["session_calendar"] = ["2024-12-30", "2024-12-31"]
    rows.attrs["session_lattice_verified"] = True
    target = PendingCloseTarget("A", 1.0, date(2024, 12, 29))
    cancelled = execute_fold_carry_in(
        target,
        rows,
        oos_start=date(2024, 12, 30),
        oos_end=date(2024, 12, 31),
        nav=100.0,
        average_daily_nav=100.0,
    )
    assert cancelled.fills == ()
    assert cancelled.cancelled == ("A",)
    valid = rows.copy()
    valid.loc[0, "open"] = 10.0
    filled = execute_fold_carry_in(
        target,
        valid,
        oos_start=date(2024, 12, 30),
        oos_end=date(2024, 12, 31),
        nav=100.0,
        average_daily_nav=100.0,
    )
    assert filled.fills[0]["execution_date"] == pd.Timestamp("2024-12-30")
    assert filled.oos_turnover == pytest.approx(0.5)
    final_close = execute_fold_carry_in(
        target,
        valid.iloc[:1],
        oos_start=date(2024, 12, 31),
        oos_end=date(2024, 12, 31),
        nav=100.0,
        average_daily_nav=100.0,
    )
    assert final_close.fills == ()
    assert final_close.cancelled == ("A",)


def test_fold_carry_in_uses_supplied_average_nav_and_requires_it() -> None:
    rows = pd.DataFrame({"ticker": ["A"], "date": [pd.Timestamp("2024-12-30")], "open": [10.0]})
    rows.attrs["session_calendar"] = ["2024-12-30"]
    rows.attrs["session_lattice_verified"] = True
    target = PendingCloseTarget("A", 1.0, date(2024, 12, 29))
    result = execute_fold_carry_in(
        target,
        rows,
        oos_start=date(2024, 12, 30),
        oos_end=date(2024, 12, 30),
        nav=100.0,
        average_daily_nav=200.0,
    )
    assert result.oos_turnover == pytest.approx(0.25)
    with pytest.raises(TypeError):
        execute_fold_carry_in(
            target,
            rows,
            oos_start=date(2024, 12, 30),
            oos_end=date(2024, 12, 30),
            nav=100.0,
        )
