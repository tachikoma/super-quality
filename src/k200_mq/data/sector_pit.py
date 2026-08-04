"""Local-file-first PIT sector mapping contract for future SECTOR_CAP support.

This module is intentionally independent from the execution engine. It defines
only normalization, validation, and as-of lookup semantics so callers can
prepare a verified sector map before any runtime portfolio constraint is wired.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

SECTOR_INTERVAL_COLUMNS = (
    "ticker",
    "sector",
    "effective_from",
    "effective_to",
    "source_type",
    "source_url",
    "source_file_sha256",
    "retrieved_at_utc",
)


class SectorPITError(ValueError):
    """Base error for malformed sector PIT inputs."""


@dataclass(frozen=True)
class SectorPITValidationResult:
    """Validation result for normalized sector intervals."""

    pit_valid: bool
    errors: tuple[str, ...]
    diagnostics: dict[str, Any]


def _read_source(source: str | Path | pd.DataFrame) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        return source.copy(deep=True)

    path = Path(source)
    if not path.exists():
        raise SectorPITError(f"sector source not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".json":
        return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise SectorPITError(f"unsupported sector source format: {suffix}")


def _normalize_ticker(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) > 6:
        digits = digits[-6:]
    return digits.zfill(6) if digits else ""


def _normalize_sector(value: Any) -> str:
    return str(value or "").strip()


def _normalize_source_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64:
        return ""
    if any(ch not in "0123456789abcdef" for ch in text):
        return ""
    return text


def _parse_date_or_none(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.date()


def _normalize_intervals(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "sector", "effective_from"}
    missing = required - set(raw.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise SectorPITError(f"missing required sector columns: {missing_text}")

    frame = pd.DataFrame({
        "ticker": raw["ticker"].map(_normalize_ticker),
        "sector": raw["sector"].map(_normalize_sector),
        "effective_from": raw["effective_from"].map(_parse_date_or_none),
        "effective_to": raw.get("effective_to", pd.Series([None] * len(raw))).map(_parse_date_or_none),
        "source_type": raw.get("source_type", pd.Series([""] * len(raw))).astype(str),
        "source_url": raw.get("source_url", pd.Series([""] * len(raw))).astype(str),
        "source_file_sha256": raw.get("source_file_sha256", pd.Series([""] * len(raw))).map(_normalize_source_hash),
        "retrieved_at_utc": raw.get("retrieved_at_utc", pd.Series([""] * len(raw))).astype(str),
    })

    frame = frame[list(SECTOR_INTERVAL_COLUMNS)].copy()
    frame = frame.sort_values(["ticker", "effective_from", "effective_to", "sector"]).reset_index(drop=True)
    return frame


def load_sector_intervals(source: str | Path | pd.DataFrame) -> pd.DataFrame:
    """Load and normalize PIT sector intervals from local files or a DataFrame."""
    return _normalize_intervals(_read_source(source))


def validate_sector_intervals(intervals: pd.DataFrame) -> SectorPITValidationResult:
    """Validate PIT sector interval integrity and overlap rules."""
    errors: list[str] = []
    diagnostics: dict[str, Any] = {
        "row_count": int(len(intervals)),
        "ticker_count": int(intervals["ticker"].nunique()) if "ticker" in intervals.columns else 0,
    }

    if intervals.empty:
        errors.append("sector intervals are empty")
        return SectorPITValidationResult(False, tuple(errors), diagnostics)

    for column in SECTOR_INTERVAL_COLUMNS:
        if column not in intervals.columns:
            errors.append(f"missing normalized column: {column}")

    if errors:
        return SectorPITValidationResult(False, tuple(errors), diagnostics)

    bad_ticker = intervals[~intervals["ticker"].str.fullmatch(r"\d{6}", na=False)]
    if not bad_ticker.empty:
        errors.append("ticker must be normalized 6-digit codes")

    if (intervals["sector"].str.len() == 0).any():
        errors.append("sector must be non-empty")

    if intervals["effective_from"].isna().any():
        errors.append("effective_from must be a valid date")

    if ((intervals["effective_to"].notna()) & (intervals["effective_to"] <= intervals["effective_from"])) .any():
        errors.append("effective_to must be strictly later than effective_from when present")

    # Per ticker, intervals are half-open [effective_from, effective_to) and must not overlap.
    for ticker, group in intervals.groupby("ticker", sort=False):
        ordered = group.sort_values("effective_from")
        previous_end: date | None = None
        for _, row in ordered.iterrows():
            start = row["effective_from"]
            end = row["effective_to"]
            if previous_end is not None and start < previous_end:
                errors.append(f"overlapping sector intervals for ticker {ticker}")
                break
            previous_end = end if end is not None else date.max

    # PIT evidence is sidecar-oriented: require hash/url/type/timestamp fields.
    missing_evidence = intervals[
        (intervals["source_type"].str.len() == 0)
        | (intervals["source_url"].str.len() == 0)
        | (intervals["source_file_sha256"].str.len() == 0)
        | (intervals["retrieved_at_utc"].str.len() == 0)
    ]
    if not missing_evidence.empty:
        errors.append("source_type/source_url/source_file_sha256/retrieved_at_utc are required")

    return SectorPITValidationResult(len(errors) == 0, tuple(errors), diagnostics)


def sector_map_as_of(intervals: pd.DataFrame, as_of: date) -> dict[str, str]:
    """Return ticker->sector mapping active on one as-of date.

    Intervals use half-open semantics: [effective_from, effective_to).
    """
    dt = pd.Timestamp(as_of).date()
    active = intervals[
        (intervals["effective_from"] <= dt)
        & (
            intervals["effective_to"].isna()
            | (intervals["effective_to"] > dt)
        )
    ].copy()

    # Use latest applicable interval if duplicates survive normalization.
    active = active.sort_values(["ticker", "effective_from", "effective_to"]).drop_duplicates(
        subset=["ticker"], keep="last",
    )
    return {str(row["ticker"]): str(row["sector"]) for _, row in active.iterrows()}


def sector_map_fingerprint(mapping: dict[str, str]) -> str:
    """Return stable SHA-256 fingerprint for a sector map snapshot."""
    payload = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
