"""Fail-closed synthetic raw-price and corporate-action contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
from typing import Any, cast

import numpy as np
import pandas as pd

from k200_low_vol.spec import DEVELOPMENT_CUTOFF


class SyntheticBundleError(ValueError):
    """Raised when a synthetic bundle is incomplete or internally unsafe."""


SUSPENSION_POLICY = "last_official_close_no_trade"
UNCONFIRMED_DELISTING_RECOVERY = 0.0


@dataclass(frozen=True, slots=True)
class PriceActionBundle:
    """In-memory bundle used by the Phase 1 contract tests only."""

    prices: pd.DataFrame
    actions: pd.DataFrame
    manifest: Mapping[str, Any]
    cache_rows: pd.DataFrame | None = None


RawPriceActionBundle = PriceActionBundle


def validate_development_cutoff(value: Any, *, label: str = "date") -> date:
    """Parse one date and reject, rather than truncate, post-cutoff values."""
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise SyntheticBundleError(f"{label} is invalid")
    parsed = timestamp.date()
    if parsed > DEVELOPMENT_CUTOFF:
        raise SyntheticBundleError(f"{label} exceeds development cutoff 2024-12-31")
    return parsed


def validate_rows_cutoff(rows: pd.DataFrame, *, date_column: str = "date") -> None:
    if not isinstance(rows, pd.DataFrame) or date_column not in rows.columns:
        raise SyntheticBundleError(f"rows require a {date_column!r} column")
    dates = pd.to_datetime(rows[date_column], errors="coerce")
    if dates.isna().any():
        raise SyntheticBundleError(f"rows contain invalid {date_column} values")
    if (dates.dt.date > DEVELOPMENT_CUTOFF).any():
        raise SyntheticBundleError("rows exceed development cutoff 2024-12-31")


validate_cache_rows_cutoff = validate_rows_cutoff


def validate_development_inputs(
    *,
    request_end_date: Any | None = None,
    rows: pd.DataFrame | None = None,
    cache_rows: pd.DataFrame | None = None,
) -> None:
    if request_end_date is not None:
        validate_development_cutoff(request_end_date, label="request end date")
    if rows is not None:
        validate_rows_cutoff(rows)
    if cache_rows is not None:
        validate_rows_cutoff(cache_rows, date_column="date")


def dataframe_sha256(frame: pd.DataFrame) -> str:
    """Hash a deterministic JSON representation of a synthetic frame."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("hash input must be a DataFrame")
    ordered = frame.copy()
    ordered = ordered.reindex(sorted(ordered.columns), axis=1)
    records: list[dict[str, Any]] = []
    for record in ordered.to_dict(orient="records"):
        records.append({str(key): _json_value(value) for key, value in record.items()})
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_synthetic_manifest(prices: pd.DataFrame, actions: pd.DataFrame) -> dict[str, Any]:
    """Build the minimum explicit manifest accepted by the validator."""
    sessions = sorted(
        pd.to_datetime(prices["date"], errors="raise").dt.normalize().dt.strftime("%Y-%m-%d").unique()
    )
    return {
        "synthetic": True,
        "source_manifest": {"name": "phase1-synthetic", "version": "1"},
        "hashes": {
            "prices_sha256": dataframe_sha256(prices),
            "actions_sha256": dataframe_sha256(actions),
        },
        "security_identity_fields": ["ticker", "security_id"],
        "price_column": "raw_close" if "raw_close" in prices else "close",
        "session_lattice_verified": True,
        "session_calendar": sessions,
        "status_fields": ["observed", "suspended", "stale", "missing"],
    }


def validate_price_action_bundle(
    bundle_or_prices: PriceActionBundle | pd.DataFrame,
    actions: pd.DataFrame | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> PriceActionBundle:
    """Validate the complete bundle; one bad action rejects the whole bundle."""
    bundle = (
        bundle_or_prices
        if isinstance(bundle_or_prices, PriceActionBundle)
        else PriceActionBundle(bundle_or_prices, actions if actions is not None else pd.DataFrame(), manifest or {})
    )
    prices = bundle.prices
    actions_frame = bundle.actions
    if not isinstance(prices, pd.DataFrame) or not isinstance(actions_frame, pd.DataFrame):
        raise SyntheticBundleError("prices and actions must be DataFrames")
    source_manifest = bundle.manifest
    if source_manifest.get("synthetic") is not True:
        raise SyntheticBundleError("only explicitly synthetic bundles are accepted")
    if not isinstance(source_manifest.get("source_manifest"), Mapping):
        raise SyntheticBundleError("source manifest is required")
    hashes = source_manifest.get("hashes")
    if not isinstance(hashes, Mapping):
        raise SyntheticBundleError("price and action source hashes are required")
    for key, frame in (("prices_sha256", prices), ("actions_sha256", actions_frame)):
        declared = hashes.get(key)
        if not isinstance(declared, str) or re.fullmatch(r"[0-9a-fA-F]{64}", declared) is None:
            raise SyntheticBundleError(f"manifest hash {key} is missing or invalid")
        if declared.casefold() != dataframe_sha256(frame):
            raise SyntheticBundleError(f"manifest hash {key} does not match rows")
    identity_fields = source_manifest.get("security_identity_fields")
    if identity_fields != ["ticker", "security_id"]:
        raise SyntheticBundleError("ticker/security_id identity fields are required")
    required_price = {"ticker", "security_id", "date", "open", "close", "volume"}
    price_column = source_manifest.get("price_column")
    if price_column not in {"close", "raw_close"}:
        raise SyntheticBundleError("manifest must name a close or raw_close field")
    required_price.add(str(price_column))
    if not required_price.issubset(prices.columns):
        raise SyntheticBundleError("price rows lack required raw-price identity fields")
    if prices.duplicated(["ticker", "date"]).any():
        raise SyntheticBundleError("price rows contain duplicate ticker/date rows")
    validate_rows_cutoff(prices)
    for status_column in ("observed", "suspended", "stale", "missing"):
        if status_column not in prices.columns:
            raise SyntheticBundleError(f"price rows must explicitly represent {status_column}")
        if any(type(value) is not bool for value in prices[status_column].tolist()):
            raise SyntheticBundleError(f"{status_column} status must contain strict booleans")
    if source_manifest.get("session_lattice_verified") is not True:
        raise SyntheticBundleError("verified KRX session lattice evidence is required")
    session_calendar = source_manifest.get("session_calendar")
    if not isinstance(session_calendar, list) or not session_calendar:
        raise SyntheticBundleError("verified KRX session calendar is required")
    try:
        calendar = tuple(sorted({validate_development_cutoff(value, label="session date") for value in session_calendar}))
    except (TypeError, ValueError) as exc:
        raise SyntheticBundleError("session calendar is invalid") from exc
    price_dates = tuple(sorted(set(pd.to_datetime(prices["date"]).dt.date)))
    if price_dates != calendar:
        raise SyntheticBundleError("price rows do not match the verified session calendar")
    for ticker, group in prices.groupby("ticker", sort=False):
        if set(pd.to_datetime(group["date"]).dt.date) != set(calendar):
            raise SyntheticBundleError(
                f"ticker {ticker} lacks explicit rows for one or more KRX sessions"
            )
    if source_manifest.get("status_fields") != ["observed", "suspended", "stale", "missing"]:
        raise SyntheticBundleError("explicit status-field contract is missing")
    if prices[["ticker", "security_id"]].isna().any().any():
        raise SyntheticBundleError("price security identity is incomplete")
    if bundle.cache_rows is not None:
        validate_rows_cutoff(bundle.cache_rows)
    required_action = {"ticker", "security_id", "action_date", "action_type", "resolved"}
    if not required_action.issubset(actions_frame.columns):
        raise SyntheticBundleError("action rows lack fail-closed ledger fields")
    validate_rows_cutoff(actions_frame, date_column="action_date")
    if actions_frame.empty:
        return bundle
    if actions_frame[["ticker", "security_id", "action_date", "action_type"]].isna().any().any():
        raise SyntheticBundleError("action identity is incomplete")
    price_ids = dict(zip(prices["ticker"].astype(str), prices["security_id"].astype(str), strict=False))
    for row in actions_frame.to_dict(orient="records"):
        ticker = str(row["ticker"])
        action_type = str(row["action_type"]).casefold()
        if ticker not in price_ids or str(row["security_id"]) != price_ids[ticker]:
            raise SyntheticBundleError("action security identity does not match price identity")
        if action_type not in {"split", "reverse_split", "cash_dividend", "suspension", "delisting"}:
            raise SyntheticBundleError(f"unsupported corporate action: {action_type}")
        if row["resolved"] is not True:
            raise SyntheticBundleError("unresolved corporate action rejects the whole bundle")
        if action_type in {"split", "reverse_split"}:
            ratio = _number(row.get("ratio"))
            if ratio is None or ratio <= 0:
                raise SyntheticBundleError("split actions require a positive ratio")
        if action_type == "delisting" and row.get("confirmed") is True:
            recovery = _number(row.get("recovery_value"))
            if recovery is None or recovery < 0:
                raise SyntheticBundleError(
                    "confirmed delisting requires a non-negative recovery_value"
                )
        if action_type == "delisting" and row.get("confirmed") is not True:
            # An unconfirmed delisting is allowed only to apply the registered
            # zero-recovery policy; it is never silently treated as a normal exit.
            continue
        if action_type != "delisting" and row.get("confirmed") is not True:
            raise SyntheticBundleError("non-delisting actions must be confirmed")
    return bundle


def validate_canonical_session_panel(bundle: PriceActionBundle) -> pd.DataFrame:
    """Validate and return the canonical verified KRX session panel."""
    return validate_price_action_bundle(bundle).prices.copy()


def construct_prices(bundle: PriceActionBundle) -> pd.DataFrame:
    """Construct split-neutral closes while leaving cash dividends untouched."""
    checked = validate_price_action_bundle(bundle)
    prices = checked.prices.copy()
    raw_column = str(checked.manifest["price_column"])
    prices["constructed_close"] = pd.to_numeric(prices[raw_column], errors="coerce")
    for column in ("open", "high", "low"):
        if column in prices:
            prices[f"constructed_{column}"] = pd.to_numeric(prices[column], errors="coerce")
    prices["date"] = pd.to_datetime(prices["date"])
    actions = checked.actions.copy()
    prices.attrs["session_calendar"] = list(checked.manifest["session_calendar"])
    prices.attrs["session_lattice_verified"] = True
    prices.attrs["raw_price_action_bundle_validated"] = True
    if actions.empty:
        return prices.sort_values(["ticker", "date"], kind="mergesort").reset_index(drop=True)
    actions["action_date"] = pd.to_datetime(actions["action_date"])
    actions["action_type"] = actions["action_type"].astype(str).str.casefold()
    for ticker, indexes in prices.groupby("ticker", sort=False).groups.items():
        ticker_actions = actions[actions["ticker"].astype(str) == str(ticker)]
        for index in indexes:
            price_date = prices.loc[index, "date"]
            denominator = 1.0
            for action in ticker_actions.to_dict(orient="records"):
                action_type = str(action["action_type"]).casefold()
                if action_type == "split" and action["action_date"] > price_date:
                    denominator *= float(action["ratio"])
                elif action_type == "reverse_split" and action["action_date"] > price_date:
                    denominator *= float(action["ratio"])
            if denominator <= 0 or not math.isfinite(denominator):
                raise SyntheticBundleError("invalid split adjustment denominator")
            prices.loc[index, "constructed_close"] /= denominator
            for column in ("open", "high", "low"):
                constructed_column = f"constructed_{column}"
                if constructed_column in prices:
                    prices.loc[index, constructed_column] /= denominator
    return prices.sort_values(["ticker", "date"], kind="mergesort").reset_index(drop=True)


def apply_split_adjustments(
    quantity: float,
    reference_price: float,
    actions: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> dict[str, float]:
    """Apply split/reverse-split quantity and reference-price adjustments."""
    if not math.isfinite(quantity) or quantity < 0:
        raise ValueError("quantity must be finite and non-negative")
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("reference_price must be finite and positive")
    records: list[Mapping[str, Any]]
    if isinstance(actions, pd.DataFrame):
        action_frame = cast(pd.DataFrame, actions)
        records = [dict(row) for row in action_frame.to_dict(orient="records")]
    else:
        records = list(actions)
    ordered = sorted(records, key=lambda row: (str(row.get("action_date", "")), str(row.get("action_type", ""))))
    for action in ordered:
        action_type = str(action.get("action_type", "")).casefold()
        if action_type in {"split", "reverse_split"}:
            ratio = _number(action.get("ratio"))
            if ratio is None or ratio <= 0:
                raise SyntheticBundleError("split actions require a positive ratio")
            quantity *= ratio
            reference_price /= ratio
        elif action_type in {"cash_dividend", "suspension", "delisting"}:
            continue
        else:
            raise SyntheticBundleError(f"unsupported corporate action: {action_type}")
    return {"quantity": float(quantity), "reference_price": float(reference_price)}


apply_corporate_action_adjustments = apply_split_adjustments
process_action_bundle = construct_prices


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def delisting_recovery_value(*, confirmed: bool, contractual_value: float) -> float:
    """Apply the registered zero-recovery policy to an unconfirmed delisting."""
    if not math.isfinite(contractual_value) or contractual_value < 0:
        raise ValueError("contractual_value must be finite and non-negative")
    return float(contractual_value if confirmed else UNCONFIRMED_DELISTING_RECOVERY)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (date, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    return str(value)


__all__ = [
    "DEVELOPMENT_CUTOFF",
    "PriceActionBundle",
    "RawPriceActionBundle",
    "SUSPENSION_POLICY",
    "SyntheticBundleError",
    "UNCONFIRMED_DELISTING_RECOVERY",
    "apply_split_adjustments",
    "apply_corporate_action_adjustments",
    "build_synthetic_manifest",
    "construct_prices",
    "dataframe_sha256",
    "delisting_recovery_value",
    "validate_cache_rows_cutoff",
    "validate_canonical_session_panel",
    "validate_development_cutoff",
    "validate_development_inputs",
    "validate_price_action_bundle",
    "process_action_bundle",
    "validate_rows_cutoff",
]
