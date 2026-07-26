"""Data layer for KOSPI 200 Momentum + Quality strategy."""

from k200_mq.data.universe import (
    exclude_kospi_top_n,
    get_kospi200_constituents,
    get_kospi200_history,
    is_kospi200_constituent,
    apply_exclusions,
)