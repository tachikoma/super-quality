"""Deterministic bottom-20% equal-weight portfolio selection."""

from __future__ import annotations

from collections.abc import Iterable
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from k200_low_vol.spec import LowVolSpec


_FORBIDDEN = {
    "momentum", "momentum_z", "quality", "quality_z", "regime", "regime_scale",
    "adv", "adv_ratio", "stop_loss", "sector", "correlation", "pair_correlation",
    "candidate", "candidate_grid", "grid", "max_position", "max_position_weight",
    "adv_ratio_by_ticker", "pair_correlation_map", "sector_cap",
}


class LowVolatilitySelector:
    """Select only the registered low-volatility factor rows."""

    is_low_volatility = True

    def __init__(self, spec: LowVolSpec | None = None) -> None:
        self.spec = spec or LowVolSpec()

    def select_portfolio(
        self,
        factor_data: pd.DataFrame,
        universe: Iterable[str],
        as_of: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return ``ticker``/``weight`` records for a PIT universe snapshot."""
        forbidden_args = _FORBIDDEN.intersection(kwargs)
        if forbidden_args:
            names = ", ".join(sorted(forbidden_args))
            raise TypeError(f"low-volatility strategy rejects non-registered inputs: {names}")
        if not isinstance(factor_data, pd.DataFrame):
            raise TypeError("factor_data must be a pandas DataFrame")
        forbidden_columns = _FORBIDDEN.intersection(
            str(column).casefold() for column in factor_data.columns
        )
        if forbidden_columns:
            names = ", ".join(sorted(forbidden_columns))
            raise ValueError(f"factor_data contains rejected inputs: {names}")
        required = {"ticker", "date", "low_volatility", "valid_return_count"}
        if not required.issubset(factor_data.columns):
            raise ValueError(f"factor_data requires columns: {', '.join(sorted(required))}")

        signal_date = pd.Timestamp(as_of).normalize()
        pit_universe = {str(ticker) for ticker in universe}
        eligible = factor_data.copy()
        eligible["_date"] = pd.to_datetime(eligible["date"], errors="coerce").dt.normalize()
        eligible = eligible[
            (eligible["_date"] == signal_date)
            & eligible["ticker"].astype(str).isin(pit_universe)
        ].copy()
        eligible["_low_volatility"] = pd.to_numeric(eligible["low_volatility"], errors="coerce")
        eligible["_valid_count"] = pd.to_numeric(eligible["valid_return_count"], errors="coerce")
        eligible = eligible[
            np.isfinite(eligible["_low_volatility"])
            & (eligible["_low_volatility"] >= 0)
            & (eligible["_valid_count"] >= self.spec.min_valid_returns)
        ]
        if eligible.duplicated("ticker").any():
            raise ValueError("factor_data contains duplicate ticker/date rows")

        count = floor(len(eligible) * self.spec.bottom_fraction)
        if count == 0:
            return []
        selected = eligible.sort_values(
            ["_low_volatility", "ticker"], kind="mergesort"
        ).iloc[:count]
        weight = 1.0 / count
        return [{"ticker": str(ticker), "weight": weight} for ticker in selected["ticker"]]


LowVolatilityStrategy = LowVolatilitySelector

__all__ = ["LowVolatilitySelector", "LowVolatilityStrategy"]
