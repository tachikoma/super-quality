"""Tests for risk guardrails and delisting/suspension detection.

Covers the config contract for the new guardrail fields, the engine's
halt/liquidation behavior, delisting force-liquidation, and the
``halt_events`` result key.  All guardrails except delisting detection are
disabled by default, so existing backtests are unaffected.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine
from k200_mq.config import K200MQConfig


def _price_data(
    dates: pd.DatetimeIndex,
    opens: list[float],
    closes: list[float],
    volumes: list[float],
) -> pd.DataFrame:
    """Build a single-ticker MultiIndex price frame with the given series."""
    rows = [
        {
            "ticker": "A",
            "date": current_date,
            "open": open_price,
            "high": max(open_price, close_price),
            "low": min(open_price, close_price),
            "close": close_price,
            "volume": volume,
            "mcap": 1_000_000.0,
        }
        for current_date, open_price, close_price, volume in zip(
            dates, opens, closes, volumes
        )
    ]
    return pd.DataFrame(rows).set_index(["ticker", "date"]).sort_index()


def _config(**overrides: object) -> K200MQConfig:
    values: dict[str, object] = {
        "INITIAL_CAPITAL": 1_000.0,
        "TOP_N": 1,
        "MAX_HOLDINGS": 1,
        "EXCLUDE_KOSPI_TOP_N": 0,
        "MAX_POSITION_WEIGHT": 1.0,
        "MIN_CASH_RATIO": 0.0,
        "COMMISSION_RATE": 0.0,
        "TAX_RATE": 0.0,
        "SLIPPAGE": 0.0,
        "SL_STOP_LOSS": -0.15,
    }
    values.update(overrides)
    return K200MQConfig.model_validate(values)


def _run(
    config: K200MQConfig,
    price_data: pd.DataFrame,
    dates: pd.DatetimeIndex,
) -> dict[str, Any]:
    """Run the engine once on a single-ticker universe/factor set."""
    factors = pd.DataFrame({
        "ticker": ["A"] * len(dates),
        "date": dates,
        "momentum_z": [1.0] * len(dates),
        "quality_z": [1.0] * len(dates),
    })
    universe = pd.DataFrame({"as_of": [dates[0]], "ticker": ["A"]})
    return PortfolioRebalanceEngine(config).run(
        price_data,
        pd.DataFrame(),
        factors,
        universe,
    )


def test_default_config_has_loss_limit_guardrails_disabled() -> None:
    config = K200MQConfig()

    assert config.ENABLE_DAILY_LOSS_LIMIT is False
    assert config.ENABLE_MONTHLY_LOSS_LIMIT is False
    assert config.ENABLE_DRAWDOWN_HALT is False
    # Delisting detection is a cheap safety net and is on by default per the
    # config contract; it only fires on volume=0 / stale-price streaks.
    assert config.ENABLE_DELISTING_DETECTION is True


def test_config_validation_rejects_invalid_guardrail_thresholds() -> None:
    with pytest.raises(ValueError, match="DAILY_LOSS_LIMIT_PCT"):
        K200MQConfig(ENABLE_DAILY_LOSS_LIMIT=True, DAILY_LOSS_LIMIT_PCT=0.0)
    with pytest.raises(ValueError, match="DAILY_LOSS_LIMIT_PCT"):
        K200MQConfig(ENABLE_DAILY_LOSS_LIMIT=True, DAILY_LOSS_LIMIT_PCT=-1.0)
    with pytest.raises(ValueError, match="MONTHLY_LOSS_LIMIT_PCT"):
        K200MQConfig(ENABLE_MONTHLY_LOSS_LIMIT=True, MONTHLY_LOSS_LIMIT_PCT=0.05)
    with pytest.raises(ValueError, match="MONTHLY_LOSS_LIMIT_PCT"):
        K200MQConfig(ENABLE_MONTHLY_LOSS_LIMIT=True, MONTHLY_LOSS_LIMIT_PCT=-1.5)
    with pytest.raises(ValueError, match="DRAWDOWN_HALT_PCT"):
        K200MQConfig(ENABLE_DRAWDOWN_HALT=True, DRAWDOWN_HALT_PCT=0.0)
    with pytest.raises(ValueError, match="DRAWDOWN_HALT_COOLDOWN_DAYS"):
        K200MQConfig(ENABLE_DRAWDOWN_HALT=True, DRAWDOWN_HALT_COOLDOWN_DAYS=0)
    with pytest.raises(ValueError, match="DELISTING_VOLUME_ZERO_DAYS"):
        K200MQConfig(DELISTING_VOLUME_ZERO_DAYS=0)
    with pytest.raises(ValueError, match="DELISTING_PRICE_STALE_DAYS"):
        K200MQConfig(DELISTING_PRICE_STALE_DAYS=0)
    with pytest.raises(ValueError, match="DELISTING_FORCE_LIQUIDATE_PRICE"):
        K200MQConfig(DELISTING_FORCE_LIQUIDATE_PRICE="invalid")


def test_daily_loss_limit_triggers_halt_and_liquidation() -> None:
    dates = pd.bdate_range("2024-01-02", periods=4)
    price_data = _price_data(
        dates,
        opens=[10.0, 10.0, 10.0, 9.0],
        closes=[10.0, 10.0, 9.0, 9.0],
        volumes=[1_000_000.0] * 4,
    )
    config = _config(
        ENABLE_DAILY_LOSS_LIMIT=True,
        DAILY_LOSS_LIMIT_PCT=-0.03,
        ENABLE_STOP_LOSS=False,
    )

    result = _run(config, price_data, dates)

    halt_events = result["halt_events"]
    assert len(halt_events) == 2
    assert halt_events[0]["reason"].startswith("daily_loss_limit")
    assert halt_events[0]["action"] == "liquidate_all"
    assert pd.Timestamp(halt_events[0]["date"]) == dates[2]
    # The one-shot daily circuit breaker is cleared on the next bar; trading
    # resumes after the forced liquidation.
    assert halt_events[1]["reason"] == "guardrail_lifted"
    # Liquidation executes on the following bar, leaving the book flat.
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 0
    assert (result["trade_log"]["exit_reason"] == "rebalance").any()


def test_monthly_loss_limit_triggers_halt() -> None:
    dates = pd.bdate_range("2024-01-02", periods=5)
    price_data = _price_data(
        dates,
        opens=[10.0, 10.0, 10.0, 9.5, 9.0],
        closes=[10.0, 10.0, 9.5, 9.0, 9.0],
        volumes=[1_000_000.0] * 5,
    )
    config = _config(
        ENABLE_MONTHLY_LOSS_LIMIT=True,
        MONTHLY_LOSS_LIMIT_PCT=-0.10,
        ENABLE_STOP_LOSS=False,
    )

    result = _run(config, price_data, dates)

    halt_events = result["halt_events"]
    assert len(halt_events) == 1
    assert halt_events[0]["reason"].startswith("monthly_loss_limit")
    assert halt_events[0]["action"] == "liquidate_all"
    assert pd.Timestamp(halt_events[0]["date"]) == dates[3]
    # Still below the month-start NAV after liquidation, so the halt persists
    # (no guardrail_lifted event) and the book stays flat.
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 0
    assert (result["trade_log"]["exit_reason"] == "rebalance").any()


def test_drawdown_halt_triggers_and_cooldown_blocks_immediate_restart() -> None:
    dates = pd.bdate_range("2024-01-02", periods=5)
    # Peak NAV is reached on day 1 (close 12).  Day 2 close 9 is a -25%
    # drawdown -> halt.  Day 3 open 12 recovers NAV above the threshold, but
    # the 2-day cooldown must still keep trading halted.  Day 4 cooldown has
    # elapsed, so the halt is lifted.
    price_data = _price_data(
        dates,
        opens=[10.0, 10.0, 12.0, 12.0, 12.0],
        closes=[10.0, 12.0, 9.0, 12.0, 12.0],
        volumes=[1_000_000.0] * 5,
    )
    config = _config(
        ENABLE_DRAWDOWN_HALT=True,
        DRAWDOWN_HALT_PCT=-0.20,
        DRAWDOWN_HALT_COOLDOWN_DAYS=2,
        ENABLE_STOP_LOSS=False,
    )

    result = _run(config, price_data, dates)

    halt_events = result["halt_events"]
    assert len(halt_events) == 2
    assert halt_events[0]["reason"].startswith("drawdown_halt")
    assert halt_events[0]["action"] == "liquidate_all"
    assert pd.Timestamp(halt_events[0]["date"]) == dates[2]
    # The halt must not be lifted on day 3 (drawdown recovered but cooldown
    # not yet elapsed); it is only lifted on the last bar after cooldown.
    assert halt_events[1]["reason"] == "guardrail_lifted"
    assert pd.Timestamp(halt_events[1]["date"]) == dates[4]
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 0


def test_delisting_detection_force_liquidates_on_volume_zero_streak() -> None:
    dates = pd.bdate_range("2024-01-02", periods=8)
    # Five consecutive volume=0 bars (days 2-6) trigger the delisting flow.
    # The final bar has a stale open of 8.0, so a fill at 10.0 proves the
    # forced price (last known close) is used instead of the next open.
    price_data = _price_data(
        dates,
        opens=[10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 8.0],
        closes=[10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        volumes=[1_000_000.0, 1_000_000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    )
    config = _config(
        ENABLE_DELISTING_DETECTION=True,
        DELISTING_VOLUME_ZERO_DAYS=5,
        ENABLE_STOP_LOSS=False,
    )

    result = _run(config, price_data, dates)

    # This is a forced liquidation, not a risk-guardrail halt.
    assert result["halt_events"] == []
    trades = result["trade_log"]
    # Stop-loss is disabled, so the only sell is the delisting force-sell.
    assert (trades["exit_reason"] == "stop_loss").any()
    sold = trades[trades["sell_price"].notna()]
    assert (sold["sell_price"] == 10.0).all()
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 0


def test_guardrails_do_not_interfere_when_disabled() -> None:
    dates = pd.bdate_range("2024-01-02", periods=4)
    price_data = _price_data(
        dates,
        opens=[10.0, 10.0, 11.0, 12.0],
        closes=[10.0, 11.0, 12.0, 13.0],
        volumes=[1_000_000.0] * 4,
    )
    config = _config(
        ENABLE_DAILY_LOSS_LIMIT=False,
        ENABLE_MONTHLY_LOSS_LIMIT=False,
        ENABLE_DRAWDOWN_HALT=False,
        ENABLE_DELISTING_DETECTION=False,
        ENABLE_STOP_LOSS=False,
    )

    result = _run(config, price_data, dates)

    assert result["halt_events"] == []
    assert result["portfolio_snapshots"]["num_positions"].iloc[-1] == 1
    # No sells occurred.
    assert result["trade_log"]["exit_reason"].isna().all()


def test_halt_events_appear_in_run_results() -> None:
    dates = pd.bdate_range("2024-01-02", periods=4)
    price_data = _price_data(
        dates,
        opens=[10.0, 10.0, 10.0, 9.0],
        closes=[10.0, 10.0, 9.0, 9.0],
        volumes=[1_000_000.0] * 4,
    )
    config = _config(
        ENABLE_DAILY_LOSS_LIMIT=True,
        DAILY_LOSS_LIMIT_PCT=-0.03,
        ENABLE_STOP_LOSS=False,
    )

    result = _run(config, price_data, dates)

    assert "halt_events" in result
    assert isinstance(result["halt_events"], list)
    assert len(result["halt_events"]) >= 1
    assert result["halt_events"][0]["action"] == "liquidate_all"

    # Empty runs also expose the key for a stable result schema.
    empty = PortfolioRebalanceEngine(config).run(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    assert "halt_events" in empty
    assert empty["halt_events"] == []
