"""The immutable, non-configurable low-volatility specification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


DEVELOPMENT_CUTOFF = date(2024, 12, 31)


@dataclass(frozen=True, slots=True)
class LowVolSpec:
    """Constants registered for the low-volatility hypothesis.

    The constructor is intentionally still usable by tests that spell out the
    constants, but every value is checked.  Any attempted parameter change is
    rejected rather than becoming an unregistered experiment.
    """

    development_cutoff: date = DEVELOPMENT_CUTOFF
    window: int = 252
    min_valid_returns: int = 200
    bottom_fraction: float = 0.20
    quarterly_months: tuple[int, ...] = (3, 6, 9, 12)
    price_return_basis: str = "price_return"

    def __post_init__(self) -> None:
        expected = {
            "development_cutoff": DEVELOPMENT_CUTOFF,
            "window": 252,
            "min_valid_returns": 200,
            "bottom_fraction": 0.20,
            "quarterly_months": (3, 6, 9, 12),
            "price_return_basis": "price_return",
        }
        values = {
            "development_cutoff": self.development_cutoff,
            "window": self.window,
            "min_valid_returns": self.min_valid_returns,
            "bottom_fraction": self.bottom_fraction,
            "quarterly_months": self.quarterly_months,
            "price_return_basis": self.price_return_basis,
        }
        for name, expected_value in expected.items():
            if values[name] != expected_value:
                raise ValueError(f"{name} is frozen at {expected_value!r}")

        if type(self.development_cutoff) is not date:
            raise TypeError("development_cutoff must be a date")
        if type(self.window) is not int or self.window <= 1:
            raise ValueError("window must be an integer greater than one")
        if type(self.min_valid_returns) is not int or not 1 <= self.min_valid_returns < self.window:
            raise ValueError("min_valid_returns must be an integer below window")
        if type(self.bottom_fraction) is not float or not 0.0 < self.bottom_fraction < 1.0:
            raise ValueError("bottom_fraction must be a float between zero and one")
        if self.quarterly_months != tuple(range(3, 13, 3)):
            raise ValueError("quarterly_months must be March, June, September, December")
        if self.price_return_basis != "price_return":
            raise ValueError("only un-reinvested price returns are supported")
