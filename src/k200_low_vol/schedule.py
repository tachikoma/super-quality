"""Deterministic KRX quarterly signal-date construction."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from k200_low_vol.spec import DEVELOPMENT_CUTOFF, LowVolSpec


def krx_quarterly_schedule(
    sessions: Iterable[date | pd.Timestamp | str],
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    spec: LowVolSpec | None = None,
) -> tuple[date, ...]:
    """Return the last supplied KRX session in each complete quarter.

    ``sessions`` is an already verified KRX session calendar.  No calendar is
    inferred and no date is synthesized.  Dates after the development cutoff
    are rejected instead of being silently discarded.
    """
    frozen = spec or LowVolSpec()
    values: list[date] = []
    for value in sessions:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError("KRX session calendar contains an invalid date")
        current = timestamp.date()
        if current > frozen.development_cutoff:
            raise ValueError("KRX session calendar exceeds the development cutoff")
        values.append(current)
    if len(set(values)) != len(values):
        raise ValueError("KRX session calendar contains duplicate dates")

    available = sorted(values)
    years = sorted({value.year for value in available})
    if start_year is not None:
        years = [year for year in years if year >= start_year]
    if end_year is not None:
        years = [year for year in years if year <= end_year]

    result: list[date] = []
    for year in years:
        year_sessions = [value for value in available if value.year == year]
        selected = [
            max((value for value in year_sessions if value.month == month), default=None)
            for month in frozen.quarterly_months
        ]
        if all(value is not None for value in selected):
            result.extend(value for value in selected if value is not None)
    return tuple(result)


quarterly_schedule = krx_quarterly_schedule

__all__ = ["DEVELOPMENT_CUTOFF", "krx_quarterly_schedule", "quarterly_schedule"]
