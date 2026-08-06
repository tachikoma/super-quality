"""Shared OpenDART account-id / account-name to wide column mapping.

Both the live OpenDartReader download path (``k200_mq.core.data.loader``)
and the local DART PIT path (``k200_mq.data.dart_pit``) need to map raw
account rows to the six semantic columns the quality factor consumes.  Keeping
the mapping and the lookup helper here avoids drift between the two paths.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pandas as pd

# Wide column name -> candidate account ids / names (IFRS tags first, Korean
# display names last).  A row matches when its account id or account name is
# one of these (exact) or contains one of them (containment fallback).
ACCOUNT_COLUMN_MAPPING: dict[str, tuple[str, ...]] = {
    "revenue": ("ifrs-full_Revenue", "ifrs_Revenue", "매출액"),
    "cogs": ("ifrs-full_CostOfSales", "ifrs_CostOfSales", "매출원가"),
    "net_income": ("ifrs-full_ProfitLoss", "ifrs_ProfitLoss", "당기순이익"),
    "operating_cf": (
        "ifrs-full_CashFlowsFromOperatingActivities",
        "ifrs_CashFlowsFromOperatingActivities",
        "영업활동현금흐름",
        "영업현금흐름",
    ),
    "total_assets": ("ifrs-full_Assets", "ifrs_Assets", "자산총계"),
    "total_equity": ("ifrs-full_Equity", "ifrs_Equity", "자본총계"),
}

_VALUE_COLUMNS = ("numeric_value", "thstrm_amount", "amt", "amount")


def account_row_name(row: Mapping[str, Any]) -> str:
    """Return the most specific account label available for *row*."""
    for key in ("account_name", "account_nm", "account_id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def account_row_names(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every distinct account label on *row* (name and id)."""
    seen: set[str] = set()
    labels: list[str] = []
    for key in ("account_name", "account_nm", "account_id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            label = str(value).strip()
            if label not in seen:
                seen.add(label)
                labels.append(label)
    return tuple(labels)


def account_row_value(row: Mapping[str, Any]) -> float | None:
    """Return the numeric amount for *row* across supported value columns."""
    for key in _VALUE_COLUMNS:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_rows(data: Mapping[str, Any] | Sequence[Mapping[str, Any]] | pd.DataFrame) -> list[Mapping[str, Any]]:
    """Normalize a raw row, a list of rows, or a frame to a row list."""
    if isinstance(data, pd.DataFrame):
        return [dict(row) for row in data.to_dict("records")]  # type: ignore[union-attr]
    if isinstance(data, Mapping):
        # A dict whose keys may directly be account names (e.g. one pre-pivoted
        # row) is treated as a single-row container.
        return [data]
    return list(data)


def find_account_value(
    data: Mapping[str, Any] | Sequence[Mapping[str, Any]] | pd.DataFrame,
    column_name: str,
) -> float | None:
    """Find the value for one semantic column across long-format account rows.

    Matching order is: exact account id/name, containment, then a conservative
    Korean keyword fallback for ``net_income`` and ``operating_cf``.
    """
    possible_names = ACCOUNT_COLUMN_MAPPING[column_name]
    rows = _as_rows(data)

    # Pass 1: exact match against any account label (name or id).
    for row in rows:
        if any(label in possible_names for label in account_row_names(row)):
            value = account_row_value(row)
            if value is not None:
                return value

    # Pass 2: containment match.
    for row in rows:
        for label in account_row_names(row):
            for candidate in possible_names:
                if candidate in label or label in candidate:
                    value = account_row_value(row)
                    if value is not None:
                        return value

    # Pass 3: conservative keyword fallback.
    for row in rows:
        name = account_row_name(row)
        if not name:
            continue
        if column_name == "net_income":
            if "순이익" in name and "주당" not in name and "희석" not in name:
                value = account_row_value(row)
                if value is not None:
                    return value
        elif column_name == "operating_cf":
            if "영업" in name and "현금흐름" in name and "재무" not in name and "투자" not in name:
                value = account_row_value(row)
                if value is not None:
                    return value
    return None
