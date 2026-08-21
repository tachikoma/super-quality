"""Synthetic-only tests for the frozen low-volatility Phase 1 lane."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from k200_low_vol import (
    LowVolSpec,
    LowVolatilityFactor,
    LowVolatilitySelector,
    PriceActionBundle,
    SyntheticBundleError,
    apply_split_adjustments,
    build_synthetic_manifest,
    construct_prices,
    dataframe_sha256,
    krx_quarterly_schedule,
    validate_development_cutoff,
    validate_price_action_bundle,
)


def _prices(count: int = 252, *, ticker: str = "000001") -> pd.DataFrame:
    dates = pd.bdate_range(end="2024-12-31", periods=count)
    return pd.DataFrame({
        "ticker": ticker,
        "security_id": f"sid-{ticker}",
        "date": dates,
        "close": np.linspace(100.0, 120.0, count),
        "open": np.linspace(99.0, 119.0, count),
        "volume": 1000.0,
        "observed": True,
        "suspended": False,
        "stale": False,
        "missing": False,
    })


def _bundle(prices: pd.DataFrame, actions: pd.DataFrame | None = None) -> PriceActionBundle:
    action_rows = actions if actions is not None else pd.DataFrame(
        columns=["ticker", "security_id", "action_date", "action_type", "resolved"]
    )
    return PriceActionBundle(prices, action_rows, build_synthetic_manifest(prices, action_rows))


def test_spec_is_frozen_and_rejects_parameter_changes() -> None:
    spec = LowVolSpec()
    assert spec.development_cutoff == date(2024, 12, 31)
    with pytest.raises((AttributeError, TypeError)):
        setattr(spec, "window", 251)
    with pytest.raises(ValueError):
        LowVolSpec(window=251)


def test_factor_uses_252_sessions_and_sample_ddof_one() -> None:
    frame = _prices()
    result = LowVolatilityFactor().compute(frame)
    assert len(result) == 1
    returns = frame["close"].to_numpy()[1:] / frame["close"].to_numpy()[:-1] - 1.0
    assert result.loc[0, "valid_return_count"] == 251
    assert result.loc[0, "low_volatility"] == pytest.approx(np.std(returns, ddof=1))
    assert len(result.loc[0, "factor_fingerprint"]) == 64


def test_factor_requires_full_252_window_but_only_200_valid_returns() -> None:
    assert LowVolatilityFactor().compute(_prices(251)).empty
    frame = _prices()
    frame.loc[1:52, "close"] = np.nan
    assert LowVolatilityFactor().compute(frame).empty
    frame.loc[1:51, "close"] = np.linspace(100.1, 104.0, 51)
    assert not LowVolatilityFactor().compute(frame).empty


def test_factor_has_no_lookahead_and_rejects_2025_input() -> None:
    frame = _prices(253)
    full = LowVolatilityFactor().compute(frame)
    changed = frame.copy()
    changed.loc[252, "close"] = 9999.0
    changed_result = LowVolatilityFactor().compute(changed)
    prior_date = frame.loc[251, "date"]
    assert full[full["date"] == prior_date].iloc[0].low_volatility == pytest.approx(
        changed_result[changed_result["date"] == prior_date].iloc[0].low_volatility
    )
    future = frame.copy()
    future.loc[252, "date"] = "2025-01-02"
    with pytest.raises(SyntheticBundleError):
        LowVolatilityFactor().compute(future)


@pytest.mark.parametrize("column,value", [("volume", 0.0), ("suspended", True), ("stale", True)])
def test_factor_excludes_invalid_windows_without_fill(column: str, value: object) -> None:
    frame = _prices()
    frame.loc[100, column] = value
    result = LowVolatilityFactor().compute(frame)
    assert result.loc[0, "valid_return_count"] == 249


def test_selector_bottom_twenty_ties_and_zero_floor() -> None:
    rows = pd.DataFrame({
        "ticker": [f"00000{value}" for value in range(1, 11)],
        "date": pd.Timestamp("2024-12-31"),
        "low_volatility": [1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        "valid_return_count": 251,
    })
    selected = LowVolatilitySelector().select_portfolio(rows, rows.ticker, "2024-12-31")
    assert selected == [{"ticker": "000001", "weight": 0.5}, {"ticker": "000002", "weight": 0.5}]
    assert LowVolatilitySelector().select_portfolio(rows.iloc[:4], ["000001"], "2024-12-31") == []
    with pytest.raises(TypeError):
        LowVolatilitySelector().select_portfolio(rows, rows.ticker, "2024-12-31", momentum=True)


def test_quarterly_schedule_uses_last_available_session_and_complete_years() -> None:
    sessions = pd.to_datetime([
        "2023-03-30", "2023-03-31", "2023-06-29", "2023-06-30", "2023-09-28", "2023-12-29",
        "2024-03-29", "2024-06-28", "2024-09-30", "2024-12-30",
    ])
    assert krx_quarterly_schedule(sessions) == (
        date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 28), date(2023, 12, 29),
        date(2024, 3, 29), date(2024, 6, 28), date(2024, 9, 30), date(2024, 12, 30),
    )


def test_cutoff_request_and_manifest_cache_are_fail_closed() -> None:
    with pytest.raises(SyntheticBundleError):
        validate_development_cutoff("2025-01-01", label="request end date")
    prices = _prices(2)
    actions = pd.DataFrame(columns=["ticker", "security_id", "action_date", "action_type", "resolved"])
    bundle = _bundle(prices, actions)
    validate_price_action_bundle(bundle)
    bad_cache = prices.assign(date=pd.Timestamp("2025-01-01"))
    with pytest.raises(SyntheticBundleError):
        validate_price_action_bundle(PriceActionBundle(prices, actions, bundle.manifest, bad_cache))


def test_split_and_reverse_split_are_factor_and_engine_consistent() -> None:
    prices = _prices(2)
    prices.loc[:, "close"] = [100.0, 50.0]
    split = pd.DataFrame([{
        "ticker": "000001", "security_id": "sid-000001", "action_date": prices.loc[1, "date"],
        "action_type": "split", "ratio": 2.0, "resolved": True, "confirmed": True,
    }])
    constructed = construct_prices(_bundle(prices, split))
    assert constructed["constructed_close"].tolist() == [50.0, 50.0]
    assert apply_split_adjustments(10.0, 100.0, split.to_dict(orient="records")) == {
        "quantity": 20.0, "reference_price": 50.0
    }

    prices.loc[:, "close"] = [100.0, 200.0]
    reverse = split.assign(action_type="reverse_split", ratio=0.5)
    constructed = construct_prices(_bundle(prices, reverse))
    assert constructed["constructed_close"].tolist() == [200.0, 200.0]


def test_high_low_are_optional_for_synthetic_split_construction() -> None:
    prices = _prices(2)
    actions = pd.DataFrame([{
        "ticker": "000001", "security_id": "sid-000001", "action_date": prices.loc[1, "date"],
        "action_type": "split", "ratio": 2.0, "resolved": True, "confirmed": True,
    }])
    constructed = construct_prices(_bundle(prices, actions))
    assert "constructed_high" not in constructed
    assert "constructed_low" not in constructed


def test_unsupported_action_rejects_whole_bundle_and_cash_dividend_is_unadjusted() -> None:
    prices = _prices(2)
    actions = pd.DataFrame([{
        "ticker": "000001", "security_id": "sid-000001", "action_date": prices.loc[1, "date"],
        "action_type": "rights", "resolved": True, "confirmed": True,
    }])
    with pytest.raises(SyntheticBundleError):
        validate_price_action_bundle(_bundle(prices, actions))

    dividend = actions.assign(action_type="cash_dividend", amount=1.0)
    constructed = construct_prices(_bundle(prices, dividend))
    assert constructed["constructed_close"].tolist() == prices["close"].tolist()
    assert dataframe_sha256(prices) == dataframe_sha256(prices.copy())
