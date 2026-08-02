"""No-I/O benchmark construction used by the portfolio engine.

The helper in this module is the single benchmark-return implementation used
by both the engine and the reporting metrics.  It intentionally works only
from supplied index observations; loading and provenance decisions belong to
the preparation layer.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def _benchmark_description(source: str) -> str:
    """Describe the configured index without mislabelling another ticker."""
    return (
        f"{source} close-price return calculated with pct_change(); dividends and "
        "other distributions are not included, so this is not a total return "
        "benchmark."
    )


def _benchmark_attrs(source: str) -> dict[str, object]:
    """Return stable series attributes for a configured market index."""
    source = str(source)
    return {
        "source": source,
        "source_ticker": source,
        "source_type": "kpi200" if source.upper() == "KPI200" else "configured_market_index",
        "benchmark_source": source,
        "type": "price_return",
        "benchmark_type": "price_return",
        "is_kpi200": source.upper() == "KPI200",
        "is_total_return": False,
        "total_return": False,
        "description": _benchmark_description(source),
    }


def build_price_return_benchmark(
    index_data: pd.DataFrame | pd.Series | None,
    source: str = "KPI200",
    measured_start: date | pd.Timestamp | None = None,
    measured_end: date | pd.Timestamp | None = None,
) -> pd.Series:
    """Build close-to-close price returns without filling or lookahead.

    If a measured interval is supplied, the close observations are clipped
    *before* ``pct_change``.  Consequently the first in-period observation has
    no prior in-period close and contributes no return.  This prevents a
    warmup close, or a post-period observation, from leaking into benchmark
    attribution.
    """
    source = str(source)
    empty = pd.Series(dtype=float, name=f"{source}_price_return")
    empty.index.name = "date"
    empty.attrs.update(_benchmark_attrs(source))
    if index_data is None:
        return empty
    closes: pd.Series
    if isinstance(index_data, pd.Series):
        closes = index_data.copy()
    elif isinstance(index_data, pd.DataFrame):
        if "close" not in index_data.columns:
            return empty
        closes = (
            index_data.set_index("date")["close"].copy()
            if "date" in index_data.columns
            else index_data["close"].copy()
        )
    else:
        return empty
    if isinstance(closes.index, pd.MultiIndex):
        return empty
    closes.index = pd.DatetimeIndex(pd.to_datetime(closes.index, errors="coerce"))
    if closes.index.tz is not None:
        closes.index = closes.index.tz_localize(None)
    closes.index = pd.DatetimeIndex(closes.index).normalize()
    closes = pd.Series(
        pd.to_numeric(closes, errors="coerce"), index=closes.index, dtype="float64",
    ).dropna()
    closes = closes[~closes.index.isna()]
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    if measured_start is not None:
        start = pd.Timestamp(measured_start).normalize()
        if start.tzinfo is not None:
            start = start.tz_localize(None)
        closes = closes[closes.index >= start]
    if measured_end is not None:
        end = pd.Timestamp(measured_end).normalize()
        if end.tzinfo is not None:
            end = end.tz_localize(None)
        closes = closes[closes.index <= end]
    if closes.empty:
        return empty
    returns = closes.pct_change().dropna().astype(float)
    returns.name = f"{source}_price_return"
    returns.index.name = "date"
    returns.attrs.update(empty.attrs)
    return returns


def benchmark_metadata(
    source: str = "KPI200",
    available: bool = False,
    observation_count: int = 0,
) -> dict[str, object]:
    """Return stable metadata for a configured price-index benchmark."""
    source = str(source)
    metadata = _benchmark_attrs(source)
    metadata.update({
        "available": bool(available),
        "observation_count": int(observation_count),
    })
    return metadata
