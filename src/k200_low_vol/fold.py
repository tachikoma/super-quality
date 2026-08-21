"""Synthetic expanding-fold carry-in execution semantics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from k200_low_vol.contract import SyntheticBundleError, validate_development_cutoff


@dataclass(frozen=True, slots=True)
class PendingCloseTarget:
    """A target formed at a train-period December close."""

    ticker: str
    weight: float
    signal_date: date

    def __post_init__(self) -> None:
        if self.signal_date.month != 12:
            raise SyntheticBundleError("carry-in target must be formed at a December close")
        validate_development_cutoff(self.signal_date, label="carry-in signal date")
        if not self.ticker or self.weight < 0:
            raise SyntheticBundleError("carry-in target identity/weight is invalid")


@dataclass(frozen=True, slots=True)
class FoldCarryInResult:
    fills: tuple[dict[str, Any], ...]
    cancelled: tuple[str, ...]
    oos_turnover: float
    oos_cost: float


def execute_fold_carry_in(
    targets: PendingCloseTarget | Iterable[PendingCloseTarget],
    price_rows: pd.DataFrame,
    *,
    oos_start: date | pd.Timestamp,
    oos_end: date | pd.Timestamp,
    nav: float = 1.0,
    average_daily_nav: float,
    commission_rate: float = 0.0,
    slippage: float = 0.0,
) -> FoldCarryInResult:
    """Fill train-December targets at the first OOS next-session open.

    A target with no valid open in the OOS interval is cancelled.  Only fills
    returned here contribute to OOS turnover and cost; the close-time target
    itself is never counted as an OOS transaction.
    """
    if not isinstance(price_rows, pd.DataFrame) or not {"ticker", "date", "open"}.issubset(price_rows.columns):
        raise SyntheticBundleError("carry-in prices require ticker/date/open columns")
    validate_development_cutoff(oos_start, label="OOS start")
    validate_development_cutoff(oos_end, label="OOS end")
    start = pd.Timestamp(oos_start).normalize()
    end = pd.Timestamp(oos_end).normalize()
    average_nav = float(average_daily_nav)
    if end < start or nav < 0 or average_nav <= 0 or commission_rate < 0 or slippage < 0:
        raise SyntheticBundleError("carry-in interval or cost inputs are invalid")
    rows = price_rows.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce").dt.normalize()
    if rows["date"].isna().any():
        raise SyntheticBundleError("carry-in prices contain invalid dates")
    if (rows["date"].dt.date > date(2024, 12, 31)).any():
        raise SyntheticBundleError("carry-in prices exceed development cutoff")
    rows["open"] = pd.to_numeric(rows["open"], errors="coerce")
    verified_calendar = rows.attrs.get("session_calendar")
    if rows.attrs.get("session_lattice_verified") is not True or verified_calendar is None:
        raise SyntheticBundleError("verified KRX session calendar is required")
    sessions = pd.Series(pd.to_datetime(verified_calendar, errors="coerce")).dropna().sort_values()
    if sessions.empty:
        raise SyntheticBundleError("no verified OOS KRX session is available")
    first_session = pd.Timestamp(sessions.iloc[0]).normalize()
    if first_session < start:
        future_sessions = sessions[sessions >= start]
        if future_sessions.empty:
            raise SyntheticBundleError("no verified first OOS KRX session is available")
        first_session = pd.Timestamp(future_sessions.iloc[0]).normalize()
    if first_session > end:
        raise SyntheticBundleError("no verified first OOS KRX session is available")

    target_list = [targets] if isinstance(targets, PendingCloseTarget) else list(targets)
    fills: list[dict[str, Any]] = []
    cancelled: list[str] = []
    for target in target_list:
        if target.signal_date >= start.date():
            raise SyntheticBundleError("carry-in target must precede the OOS interval")
        candidates = rows[
            (rows["ticker"].astype(str) == target.ticker)
            & (rows["date"] == first_session)
            & rows["open"].notna()
            & (rows["open"] > 0)
        ]
        if candidates.empty:
            cancelled.append(target.ticker)
            continue
        row = candidates.iloc[0]
        price = float(row["open"])
        notional = float(nav) * float(target.weight)
        cost = notional * (commission_rate + slippage)
        fills.append({
            "ticker": target.ticker,
            "signal_date": target.signal_date,
            "execution_date": row["date"],
            "open": price,
            "notional": notional,
            "cost": cost,
        })

    actual_buys = sum(float(fill["notional"]) for fill in fills)
    actual_sells = 0.0
    turnover = (actual_buys + actual_sells) / (2.0 * average_nav)
    return FoldCarryInResult(
        fills=tuple(fills),
        cancelled=tuple(cancelled),
        oos_turnover=turnover,
        oos_cost=sum(float(fill["cost"]) for fill in fills),
    )


__all__ = ["FoldCarryInResult", "PendingCloseTarget", "execute_fold_carry_in"]
