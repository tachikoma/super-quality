"""Local-file-first point-in-time KOSPI 200 universe ingestion.

The module deliberately has no network dependencies.  It accepts a downloaded
KRX file (or an in-memory frame in tests), normalizes the provider-specific
column names, and attaches the metadata contract consumed by
``data.provenance.validate_universe_provenance``.  The existing proxy loader is
not imported or changed here; callers can opt into this importer explicitly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, cast
from urllib.parse import unquote, urlparse

import pandas as pd

from k200_mq.data.provenance import (
    PIT_EFFECTIVE_DATE_CONTRACT,
    PIT_SCHEMA_CONTRACT,
    _constituent_fingerprint,
    validate_universe_provenance,
)


INDEX_CODE = "KOSPI200"
SOURCE_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ALLOWED_ACQUISITION_SOURCE_TYPES = frozenset({
    "krx_official_snapshot",
    "krx_official_event",
})
_OFFICIAL_KRX_HOSTS = ("krx.co.kr", ".krx.co.kr")

SNAPSHOT_COLUMNS = (
    "index_code",
    "as_of_date",
    "security_code",
    "name",
    "sector",
    "index_weight",
    "index_shares",
    "free_float",
    "source_type",
    "source_url",
    "source_file_sha256",
    "retrieved_at_utc",
)

INTERVAL_COLUMNS = (
    "index_code",
    "effective_from",
    "effective_to",
    "security_code",
    "action",
    "status",
    "announcement_date",
    "provenance",
    "source_type",
    "source_url",
    "source_file_sha256",
    "retrieved_at_utc",
)

# Kept as a read-only spelling for callers that imported the original schema
# constant.  It is not an event schema and no event/event alias is accepted by
# the importer.
EVENT_COLUMNS = INTERVAL_COLUMNS

_SNAPSHOT_REQUIRED = ("index_code", "as_of_date", "security_code")
_EVENT_REQUIRED = ("index_code", "effective_from", "security_code")
_OPTIONAL_SNAPSHOT = (
    "name",
    "sector",
    "index_weight",
    "index_shares",
    "free_float",
)
_SOURCE_FIELDS = (
    "source_type",
    "source_url",
    "source_file_sha256",
    "retrieved_at_utc",
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "index_code": (
        "index_code",
        "index code",
        "index",
        "index_name",
        "index name",
        "지수코드",
        "지수명",
    ),
    "as_of_date": (
        "as_of_date",
        "as of",
        "as_of",
        "effective_date",
        "date",
        "기준일",
        "기준일자",
        "적용일",
        "적용일자",
    ),
    "security_code": (
        "security_code",
        "security code",
        "ticker",
        "symbol",
        "code",
        "종목코드",
        "단축코드",
    ),
    "name": ("name", "security_name", "종목명"),
    "sector": ("sector", "sector_name", "업종", "업종명"),
    "index_weight": ("index_weight", "weight", "편입비중", "지수비중"),
    "index_shares": ("index_shares", "shares", "편입주식수", "지수주식수"),
    "free_float": ("free_float", "free float", "유동비율", "유동주식비율"),
    "source_type": ("source_type", "source type", "출처유형"),
    "source_url": ("source_url", "source url", "출처url", "출처 URL"),
    "source_file_sha256": (
        "source_file_sha256",
        "source file sha256",
        "sha256",
        "파일sha256",
    ),
    "retrieved_at_utc": (
        "retrieved_at_utc",
        "retrieved at utc",
        "retrieved_at",
        "수집시각",
    ),
    "effective_from": (
        "effective_from",
        "effective date",
        "effective_date",
        "편입일",
        "적용시작일",
    ),
    "effective_to": (
        "effective_to",
        "end_date",
        "종료일",
        "적용종료일",
    ),
    "action": ("action", "행위", "변경구분"),
    "status": ("status", "membership_status", "상태", "편입상태"),
    "announcement_date": (
        "announcement_date",
        "announcement date",
        "공고일",
        "발표일",
    ),
    "provenance": ("provenance", "provenance_metadata", "근거"),
}


class PITUniverseError(ValueError):
    """Base error for malformed or non-PIT local universe data."""


class PITUniverseValidationError(PITUniverseError):
    """Raised when a normalized universe cannot satisfy the PIT contract."""

    def __init__(self, report: PITValidationResult):
        self.report = report
        details = "; ".join(report.errors) or "universe validation failed"
        super().__init__(details)


@dataclass(frozen=True)
class AcquisitionManifest:
    """Verified, sidecar-only evidence for one downloaded raw source.

    A manifest is deliberately separate from the raw KRX file.  Its digest is
    checked against the source bytes before this object can be used as PIT
    evidence.  A mapping supplied by a DataFrame caller is still not enough:
    DataFrame inputs have no raw bytes that this class can fingerprint.
    """

    source_url: str
    query_params: Mapping[str, Any]
    date_params: Mapping[str, Any]
    retrieved_at_utc: datetime
    source_file_sha256: str
    source_type: str
    source_is_krx: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "query_params": dict(self.query_params),
            "date_params": dict(self.date_params),
            "retrieved_at_utc": self.retrieved_at_utc.isoformat(),
            "source_file_sha256": self.source_file_sha256,
            "raw_file_sha256": self.source_file_sha256,
            "source_type": self.source_type,
            "source_is_krx": self.source_is_krx,
            "verified": True,
            "verification": "raw_bytes_sha256",
        }


@dataclass(frozen=True)
class TransitionExceptionPolicy:
    """Explicit, documented size exception for a constituent transition."""

    allowed_sizes: frozenset[int]
    reason: str


@dataclass(frozen=True)
class _VerifiedAcquisition:
    """Private trust token attached only after raw-byte verification."""

    manifest: AcquisitionManifest
    raw_sha256: str
    normalized_fingerprint: str


@dataclass(frozen=True, init=False)
class ConstituentSnapshot:
    """One KOSPI 200 membership snapshot and its source provenance.

    ``effective_date`` is accepted as a constructor alias for ``as_of_date``
    because both names are used by downloaded index files.  The canonical
    field, and the field used in normalized frames, is ``as_of_date``.
    """

    index_code: str
    as_of_date: date
    security_code: str
    name: str | None
    sector: str | None
    index_weight: float | None
    index_shares: float | None
    free_float: float | None
    source_type: str
    source_url: str
    source_file_sha256: str
    retrieved_at_utc: datetime

    def __init__(
        self,
        index_code: str,
        as_of_date: date | datetime | str | None = None,
        security_code: str | int = "",
        name: str | None = None,
        sector: str | None = None,
        index_weight: float | None = None,
        index_shares: float | None = None,
        free_float: float | None = None,
        source_type: str = "",
        source_url: str = "",
        source_file_sha256: str = "",
        retrieved_at_utc: datetime | str | None = None,
        *,
        effective_date: date | datetime | str | None = None,
    ) -> None:
        selected_date = as_of_date if as_of_date is not None else effective_date
        if selected_date is None:
            raise TypeError("as_of_date or effective_date is required")
        if as_of_date is not None and effective_date is not None:
            if _normalize_date(as_of_date) != _normalize_date(effective_date):
                raise ValueError("as_of_date and effective_date disagree")
        if retrieved_at_utc is None:
            raise TypeError("retrieved_at_utc is required")
        object.__setattr__(self, "index_code", str(index_code))
        object.__setattr__(self, "as_of_date", _normalize_date(selected_date))
        object.__setattr__(self, "security_code", _normalize_security_code(security_code))
        object.__setattr__(self, "name", _optional_text(name))
        object.__setattr__(self, "sector", _optional_text(sector))
        object.__setattr__(self, "index_weight", _optional_float(index_weight))
        object.__setattr__(self, "index_shares", _optional_float(index_shares))
        object.__setattr__(self, "free_float", _optional_float(free_float))
        object.__setattr__(self, "source_type", str(source_type).strip())
        object.__setattr__(self, "source_url", str(source_url).strip())
        object.__setattr__(self, "source_file_sha256", _normalize_sha(source_file_sha256))
        object.__setattr__(self, "retrieved_at_utc", _normalize_utc(retrieved_at_utc))

    @property
    def effective_date(self) -> date:
        """Compatibility alias for the snapshot's effective/as-of date."""
        return self.as_of_date

    def to_record(self) -> dict[str, Any]:
        """Return a canonical dictionary suitable for a DataFrame row."""
        return {
            "index_code": self.index_code,
            "as_of_date": self.as_of_date,
            "security_code": self.security_code,
            "name": self.name,
            "sector": self.sector,
            "index_weight": self.index_weight,
            "index_shares": self.index_shares,
            "free_float": self.free_float,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "source_file_sha256": self.source_file_sha256,
            "retrieved_at_utc": self.retrieved_at_utc,
        }


@dataclass(frozen=True)
class MembershipInterval:
    """An explicit effective-dated membership interval."""

    index_code: str
    effective_from: date | None
    security_code: str
    effective_to: date | None = None
    action: str | None = None
    status: str | None = None
    announcement_date: date | None = None
    provenance: Mapping[str, Any] | str | None = None
    source_type: str = ""
    source_url: str = ""
    source_file_sha256: str = ""
    retrieved_at_utc: datetime | None = None


@dataclass(frozen=True)
class PITValidationResult:
    """Structured validation result with mapping-style compatibility."""

    pit_valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    snapshot_sizes: Mapping[str, int] | None = None
    diagnostics: Mapping[str, Any] | None = None
    pit_candidate: bool = False
    provenance: str = "unverified"

    @property
    def valid(self) -> bool:
        return self.pit_valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "pit_valid": self.pit_valid,
            "valid": self.pit_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "snapshot_sizes": dict(self.snapshot_sizes or {}),
            "diagnostics": dict(self.diagnostics or {}),
            "pit_candidate": self.pit_candidate,
            "provenance": self.provenance,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def __bool__(self) -> bool:
        return self.pit_valid


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 fingerprint of a local source file."""
    source_path = Path(path)
    digest = hashlib.sha256()
    with source_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_dataframe(data: pd.DataFrame) -> str:
    """Return a deterministic content fingerprint for an in-memory source.

    Column and row order are canonicalized.  This is useful for tests and for
    callers that have already downloaded bytes into a DataFrame; a file path
    always uses the stronger raw-byte fingerprint from :func:`sha256_file`.
    """
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")
    frame = data.copy()
    frame.columns = [str(column) for column in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise PITUniverseError("duplicate normalized source column labels are not allowed")
    columns = sorted(frame.columns)
    records: list[list[Any]] = []
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        records.append([_canonical_value(value) for value in row])
    records.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
    payload = json.dumps(
        {"columns": columns, "rows": records},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_constituent_snapshots(
    source: str | Path | bytes | pd.DataFrame,
    *,
    column_mapping: Mapping[str, str] | None = None,
    source_format: str | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    source_file_sha256: str | None = None,
    retrieved_at_utc: datetime | str | None = None,
    acquisition_manifest: Mapping[str, Any] | str | Path | None = None,
    manifest: Mapping[str, Any] | str | Path | None = None,
) -> pd.DataFrame:
    """Load and normalize a local CSV, Excel, Parquet, JSON, or DataFrame.

    ``column_mapping`` maps canonical names to source column names.  Automatic
    aliases are intentionally conservative: if two aliases are present, the
    caller must provide an explicit mapping rather than silently choosing one.
    """
    raw, file_metadata = _read_source(source, source_format=source_format)
    manifest_value = _select_manifest(acquisition_manifest, manifest)
    verified_manifest, acquisition_token = _verify_acquisition(
        source,
        file_metadata,
        manifest_value,
    )
    resolved = _resolve_columns(
        raw,
        column_mapping,
        required=_SNAPSHOT_REQUIRED,
        optional=(*_OPTIONAL_SNAPSHOT, *_SOURCE_FIELDS),
    )
    canonical = _rename_columns(raw, resolved)
    metadata = _source_metadata(
        source,
        raw,
        canonical,
        file_metadata=file_metadata,
        source_type=source_type,
        source_url=source_url,
        source_file_sha256=source_file_sha256,
        retrieved_at_utc=retrieved_at_utc,
        verified_manifest=verified_manifest,
    )
    for field, values in metadata.items():
        canonical[field] = values
    result = _normalize_snapshot_frame(canonical)
    _attach_acquisition_metadata(result, verified_manifest, acquisition_token)
    result.attrs["source_file_hash_verified"] = bool(acquisition_token)
    result.attrs["source_file_sha256"] = sorted(
        set(result["source_file_sha256"].astype(str))
    )
    return result


def load_membership_intervals(
    source: str | Path | bytes | pd.DataFrame,
    *,
    column_mapping: Mapping[str, str] | None = None,
    source_format: str | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    source_file_sha256: str | None = None,
    retrieved_at_utc: datetime | str | None = None,
    acquisition_manifest: Mapping[str, Any] | str | Path | None = None,
    manifest: Mapping[str, Any] | str | Path | None = None,
) -> pd.DataFrame:
    """Load and normalize local effective-dated membership intervals."""
    raw, file_metadata = _read_source(source, source_format=source_format)
    manifest_value = _select_manifest(acquisition_manifest, manifest)
    verified_manifest, acquisition_token = _verify_acquisition(
        source,
        file_metadata,
        manifest_value,
    )
    resolved = _resolve_columns(
        raw,
        column_mapping,
        required=_EVENT_REQUIRED,
        optional=(
            "effective_to",
            "action",
            "status",
            "announcement_date",
            "provenance",
            *_SOURCE_FIELDS,
        ),
    )
    canonical = _rename_columns(raw, resolved)
    _promote_interval_provenance_fields(canonical)
    metadata = _source_metadata(
        source,
        raw,
        canonical,
        file_metadata=file_metadata,
        source_type=source_type,
        source_url=source_url,
        source_file_sha256=source_file_sha256,
        retrieved_at_utc=retrieved_at_utc,
        verified_manifest=verified_manifest,
    )
    for field, values in metadata.items():
        canonical[field] = values
    result = _normalize_interval_frame(canonical)
    if "provenance" not in result:
        result["provenance"] = [
            {
                "source_type": row.source_type,
                "source_url": row.source_url,
                "source_file_sha256": row.source_file_sha256,
                "retrieved_at_utc": _normalize_utc(row.retrieved_at_utc).isoformat(),
            }
            for row in _interval_records(result)
        ]
    _attach_acquisition_metadata(result, verified_manifest, acquisition_token)
    return result


def validate_constituent_snapshots(
    snapshots: pd.DataFrame,
    *,
    requested_rebalance_dates: Iterable[date | datetime | str] | None = None,
    requested_rebalance_date: date | datetime | str | None = None,
    target_size: int | None = 200,
    transition_exceptions: Mapping[Any, Any] | Iterable[Any] | None = None,
    raise_on_error: bool = False,
) -> PITValidationResult:
    """Validate normalized snapshots and return a computed PIT report.

    A date is PIT-valid only if all of its rows share one complete source
    metadata record and its constituent fingerprint is computed from the rows.
    No caller-provided PIT boolean is read.
    """
    errors: list[str] = []
    warnings: list[str] = []
    sizes: dict[str, int] = {}
    diagnostics: dict[str, Any] = {}
    if not isinstance(snapshots, pd.DataFrame):
        errors.append("snapshots must be a pandas DataFrame")
        return _finish_validation(errors, warnings, sizes, diagnostics, raise_on_error)

    _append_acquisition_errors(errors, snapshots)

    missing = [field for field in (*SNAPSHOT_COLUMNS,) if field not in snapshots.columns]
    if missing:
        errors.append(f"missing canonical snapshot fields: {', '.join(missing)}")
        return _finish_validation(errors, warnings, sizes, diagnostics, raise_on_error)
    if snapshots.empty:
        errors.append("snapshot data is empty")

    request_dates = _requested_dates(requested_rebalance_dates, requested_rebalance_date)
    materialized_exceptions = _materialize_transition_exceptions(transition_exceptions)
    if request_dates:
        future = snapshots[snapshots["as_of_date"].map(_safe_date).map(
            lambda value: value is not None and value > max(request_dates),
        )]
        if not future.empty:
            errors.append("snapshot effective date is after a requested rebalance date")

    if snapshots["index_code"].astype(str).str.strip().ne(INDEX_CODE).any():
        errors.append(f"index_code must be {INDEX_CODE}")
    _append_empty_errors(errors, snapshots, "security_code", "security/ticker")
    _append_empty_errors(errors, snapshots, "as_of_date", "as-of date")
    invalid_tickers = [
        str(value)
        for value in snapshots["security_code"]
        if _try_security_code(value) is None
    ]
    if invalid_tickers:
        errors.append("security_code contains non-six-digit or empty ticker values")
    invalid_dates = [str(value) for value in snapshots["as_of_date"] if _safe_date(value) is None]
    if invalid_dates:
        errors.append("as_of_date contains empty or invalid dates")

    duplicate_mask = snapshots.duplicated(
        subset=["index_code", "as_of_date", "security_code"],
        keep=False,
    )
    if duplicate_mask.any():
        errors.append("duplicate (index_code, as_of_date, security_code) rows")

    for field in _SOURCE_FIELDS:
        _append_empty_errors(errors, snapshots, field, field)
    invalid_hashes = [
        str(value)
        for value in snapshots["source_file_sha256"]
        if _normalize_sha_or_none(value) is None
    ]
    if invalid_hashes:
        errors.append("source_file_sha256 must be a 64-character SHA-256 fingerprint")
    for value in snapshots["retrieved_at_utc"]:
        if _safe_utc(value) is None:
            errors.append("retrieved_at_utc contains an invalid timestamp")
            break

    if not snapshots.empty and not invalid_dates:
        for as_of, group in snapshots.groupby("as_of_date", sort=True):
            key = _normalize_date(as_of).isoformat()
            sizes[key] = len(group)
            exception = _transition_exception(as_of, materialized_exceptions)
            allowed = _size_allowed(len(group), target_size, exception)
            diagnostics.setdefault("snapshot_sizes", {})[key] = {
                "size": len(group),
                "target": target_size,
                "transition_exception": exception is not None,
                "transition_exception_reason": exception.reason if exception else None,
                "accepted": allowed,
            }
            if not allowed:
                errors.append(
                    f"snapshot {key} has {len(group)} constituents; expected {target_size}"
                )
            metadata_columns = [
                "source_type",
                "source_url",
                "source_file_sha256",
                "retrieved_at_utc",
            ]
            if len(group[metadata_columns].drop_duplicates()) != 1:
                errors.append(f"snapshot {key} lacks exact per-date source metadata")
    if request_dates and not snapshots.empty:
        available = {_normalize_date(value) for value in snapshots["as_of_date"]}
        missing_dates = sorted(set(request_dates) - available)
        if missing_dates:
            errors.append(
                "missing requested rebalance snapshots: "
                + ", ".join(value.isoformat() for value in missing_dates)
            )

    result = _finish_validation(errors, warnings, sizes, diagnostics, raise_on_error)
    return result


def validate_membership_intervals(
    intervals: pd.DataFrame,
    *,
    requested_rebalance_dates: Iterable[date | datetime | str] | None = None,
    requested_rebalance_date: date | datetime | str | None = None,
    raise_on_error: bool = False,
) -> PITValidationResult:
    """Validate interval provenance, date ordering, and overlap safety."""
    errors: list[str] = []
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {}
    if not isinstance(intervals, pd.DataFrame):
        errors.append("intervals must be a pandas DataFrame")
        return _finish_validation(errors, warnings, {}, diagnostics, raise_on_error)

    _append_acquisition_errors(errors, intervals)
    missing = [field for field in INTERVAL_COLUMNS if field not in intervals.columns]
    if missing:
        errors.append(f"missing canonical interval fields: {', '.join(missing)}")
        return _finish_validation(errors, warnings, {}, diagnostics, raise_on_error)
    if intervals.empty:
        errors.append("membership interval data is empty")
    if intervals["index_code"].astype(str).str.strip().ne(INDEX_CODE).any():
        errors.append(f"index_code must be {INDEX_CODE}")
    _append_empty_errors(errors, intervals, "security_code", "security/ticker")
    if any(_try_security_code(value) is None for value in intervals["security_code"]):
        errors.append("security_code contains non-six-digit or empty ticker values")
    if any(_safe_date(value) is None for value in intervals["effective_from"]):
        errors.append("effective_from contains empty or invalid dates")
    if any(
        value is not None and _safe_date(value) is None
        for value in intervals["effective_to"]
        if not _is_missing(value)
    ):
        errors.append("effective_to contains invalid dates")
    interval_rows = _interval_records(intervals)
    if any(
        (_is_missing(row.action) and _is_missing(row.status))
        for row in interval_rows
    ):
        errors.append("each interval requires a nonempty action or status")
    if any(_safe_date(value) is None for value in intervals["announcement_date"]):
        errors.append("announcement_date contains empty or invalid dates")
    for field in _SOURCE_FIELDS:
        _append_empty_errors(errors, intervals, field, field)
    if any(_normalize_sha_or_none(value) is None for value in intervals["source_file_sha256"]):
        errors.append("source_file_sha256 must be a 64-character SHA-256 fingerprint")
    if any(_safe_utc(value) is None for value in intervals["retrieved_at_utc"]):
        errors.append("retrieved_at_utc contains an invalid timestamp")
    if any(_is_empty_provenance(value) for value in intervals["provenance"]):
        errors.append("provenance is required for every interval")

    for row in interval_rows:
        if not _known_membership_value(row.action) or not _known_membership_value(row.status):
            errors.append("unknown action/status value; interval membership is fail-closed")
            break
        if (
            _membership_value_state(row.action) is not None
            and _membership_value_state(row.status) is not None
            and _membership_value_state(row.action) != _membership_value_state(row.status)
        ):
            errors.append("action and status disagree on membership state")
            break
        effective_from = _safe_date(row.effective_from)
        announcement_date = _safe_date(row.announcement_date)
        if (
            effective_from is not None
            and announcement_date is not None
            and announcement_date > effective_from
        ):
            errors.append("announcement_date must be on or before effective_from")
            break

    request_dates = _requested_dates(requested_rebalance_dates, requested_rebalance_date)
    if request_dates:
        cutoff = max(request_dates)
        if any(
            (parsed := _safe_date(value)) is not None and parsed > cutoff
            for value in intervals["effective_from"]
        ):
            errors.append("interval effective date is after a requested rebalance date")

    overlap_keys: list[tuple[str, str]] = []
    if not intervals.empty:
        grouped = intervals.groupby(["index_code", "security_code"], sort=True)
        group_count = 0
        for group_key, group in grouped:
            if not isinstance(group_key, tuple) or len(group_key) != 2:
                continue
            index_code, ticker = (str(value) for value in group_key)
            group_count += 1
            ordered = group.sort_values(by="effective_from", kind="mergesort")
            previous_to: date | None = None
            previous_seen = False
            previous_open = False
            for row in _interval_records(ordered):
                start = row.effective_from
                end = row.effective_to
                if start is None or (not _is_missing(row.effective_to) and end is None):
                    continue
                if end is not None and end <= start:
                    errors.append(
                        f"interval for {ticker} has effective_to not after effective_from"
                    )
                if previous_seen and previous_open:
                    errors.append(f"overlapping open intervals for ticker {ticker}")
                elif previous_seen and previous_to is not None and start < previous_to:
                    errors.append(f"overlapping intervals for ticker {ticker}")
                previous_seen = True
                previous_open = end is None
                previous_to = end
                overlap_keys.append((str(index_code), str(ticker)))
        diagnostics["interval_groups"] = group_count
    else:
        diagnostics["interval_groups"] = 0
    return _finish_validation(errors, warnings, {}, diagnostics, raise_on_error)


def snapshots_to_history(
    snapshots: pd.DataFrame,
    *,
    requested_rebalance_dates: Iterable[date | datetime | str] | None = None,
    requested_rebalance_date: date | datetime | str | None = None,
    target_size: int | None = 200,
    transition_exceptions: Mapping[Any, Any] | Iterable[Any] | None = None,
) -> pd.DataFrame:
    """Convert validated snapshots to deterministic legacy ``as_of/ticker``."""
    report = validate_constituent_snapshots(
        snapshots,
        requested_rebalance_dates=requested_rebalance_dates,
        requested_rebalance_date=requested_rebalance_date,
        target_size=target_size,
        transition_exceptions=transition_exceptions,
    )
    if not report.pit_valid:
        raise PITUniverseValidationError(report)
    requested = _requested_dates(requested_rebalance_dates, requested_rebalance_date)
    dates = requested or sorted({_normalize_date(value) for value in snapshots["as_of_date"]})
    records: list[dict[str, Any]] = []
    metadata_by_date: dict[str, dict[str, Any]] = {}
    interval_metadata_by_as_of = snapshots.attrs.get("interval_metadata_by_as_of", {})
    acquisition_manifest = snapshots.attrs.get("acquisition_manifest")
    for as_of in dates:
        group = snapshots[snapshots["as_of_date"].map(_safe_date).eq(as_of)].copy()
        tickers = sorted(group["security_code"].astype(str).tolist())
        source_row = group.iloc[0]
        fingerprint = _constituent_fingerprint(tickers)
        key = as_of.isoformat()
        metadata_by_date[key] = {
            "label": "pit",
            "provenance": "pit",
            "source": str(source_row["source_url"]),
            "source_type": str(source_row["source_type"]),
            "source_url": str(source_row["source_url"]),
            "source_file_sha256": str(source_row["source_file_sha256"]),
            "source_fingerprint": str(source_row["source_file_sha256"]),
            "retrieved_at_utc": _normalize_utc(source_row["retrieved_at_utc"]).isoformat(),
            "schema": dict(PIT_SCHEMA_CONTRACT),
            "effective_date": key,
            "contract": PIT_EFFECTIVE_DATE_CONTRACT,
            "fingerprint": fingerprint,
        }
        if isinstance(acquisition_manifest, Mapping):
            metadata_by_date[key]["acquisition_manifest"] = dict(acquisition_manifest)
        if isinstance(interval_metadata_by_as_of, Mapping):
            rows = interval_metadata_by_as_of.get(key, [])
            if isinstance(rows, list):
                metadata_by_date[key]["membership_intervals"] = rows
        records.extend({"as_of": as_of, "ticker": ticker} for ticker in tickers)
    history = pd.DataFrame.from_records(records, columns=["as_of", "ticker"])
    if history.empty:
        history = pd.DataFrame({"as_of": pd.Series(dtype="datetime64[ns]"), "ticker": pd.Series(dtype="string")})
    history.attrs["provenance_by_as_of"] = {key: "pit" for key in metadata_by_date}
    history.attrs["source_by_as_of"] = dict(history.attrs["provenance_by_as_of"])
    history.attrs["provenance"] = "pit"
    history.attrs["source"] = "pit"
    history.attrs["provenance_metadata_by_as_of"] = metadata_by_date
    history.attrs["_verified_acquisition"] = snapshots.attrs.get("_verified_acquisition")
    history.attrs["pit_valid"] = bool(validate_universe_provenance(history)["pit_valid"])
    return history


def intervals_to_history(
    intervals: pd.DataFrame,
    requested_rebalance_dates: Iterable[date | datetime | str],
    *,
    target_size: int | None = 200,
    transition_exceptions: Mapping[Any, Any] | Iterable[Any] | None = None,
) -> pd.DataFrame:
    """Materialize effective-dated intervals at requested dates."""
    dates = _requested_dates(requested_rebalance_dates, None)
    if not dates:
        raise PITUniverseError("requested_rebalance_dates must not be empty")
    interval_report = validate_membership_intervals(
        intervals, requested_rebalance_dates=dates,
    )
    if not interval_report.pit_valid:
        raise PITUniverseValidationError(interval_report)
    records: list[dict[str, Any]] = []
    interval_metadata_by_as_of: dict[str, list[dict[str, Any]]] = {}
    interval_rows = _interval_records(intervals)
    for as_of in dates:
        key = as_of.isoformat()
        interval_metadata_by_as_of[key] = []
        for row in interval_rows:
            start = _normalize_date(row.effective_from)
            end = None if _is_missing(row.effective_to) else _normalize_date(row.effective_to)
            if not (start <= as_of and (end is None or as_of < end)):
                continue
            if not _interval_is_active(row.action, row.status):
                continue
            interval_metadata_by_as_of[key].append(_interval_metadata(row))
            records.append({
                "index_code": row.index_code,
                "as_of_date": as_of,
                "security_code": row.security_code,
                "name": None,
                "sector": None,
                "index_weight": None,
                "index_shares": None,
                "free_float": None,
                "source_type": row.source_type,
                "source_url": row.source_url,
                "source_file_sha256": row.source_file_sha256,
                "retrieved_at_utc": row.retrieved_at_utc,
            })
    snapshots = _normalize_snapshot_frame(pd.DataFrame.from_records(records, columns=SNAPSHOT_COLUMNS))
    snapshots.attrs.update({
        "interval_metadata_by_as_of": interval_metadata_by_as_of,
        "_verified_acquisition": intervals.attrs.get("_verified_acquisition"),
        "acquisition_manifest": intervals.attrs.get("acquisition_manifest"),
        "acquisition_manifest_verified": intervals.attrs.get(
            "acquisition_manifest_verified", False,
        ),
        "pit_candidate": intervals.attrs.get("pit_candidate", False),
    })
    token = snapshots.attrs.get("_verified_acquisition")
    if isinstance(token, _VerifiedAcquisition):
        snapshots.attrs["_verified_acquisition"] = _VerifiedAcquisition(
            manifest=token.manifest,
            raw_sha256=token.raw_sha256,
            normalized_fingerprint=fingerprint_dataframe(snapshots),
        )
    return snapshots_to_history(
        snapshots,
        requested_rebalance_dates=dates,
        target_size=target_size,
        transition_exceptions=transition_exceptions,
    )


def import_local_pit_universe(
    source: str | Path | bytes | pd.DataFrame,
    *,
    source_kind: str = "snapshots",
    column_mapping: Mapping[str, str] | None = None,
    requested_rebalance_dates: Iterable[date | datetime | str] | None = None,
    requested_rebalance_date: date | datetime | str | None = None,
    target_size: int | None = 200,
    transition_exceptions: Mapping[Any, Any] | Iterable[Any] | None = None,
    source_format: str | None = None,
    source_type: str | None = None,
    source_url: str | None = None,
    source_file_sha256: str | None = None,
    retrieved_at_utc: datetime | str | None = None,
    acquisition_manifest: Mapping[str, Any] | str | Path | None = None,
    manifest: Mapping[str, Any] | str | Path | None = None,
) -> pd.DataFrame:
    """CLI-independent local PIT import entry point.

    ``source_kind="snapshots"`` is the default.  ``"intervals"`` accepts
    effective-dated membership records and materializes the requested dates.
    This function never falls back to KRX/DART/network data.
    """
    kind = source_kind.strip().casefold()
    dates = _requested_dates(requested_rebalance_dates, requested_rebalance_date)
    if kind in {"snapshot", "snapshots"}:
        snapshots = load_constituent_snapshots(
            source,
            column_mapping=column_mapping,
            source_format=source_format,
            source_type=source_type,
            source_url=source_url,
            source_file_sha256=source_file_sha256,
            retrieved_at_utc=retrieved_at_utc,
            acquisition_manifest=acquisition_manifest,
            manifest=manifest,
        )
        return snapshots_to_history(
            snapshots,
            requested_rebalance_dates=dates or None,
            target_size=target_size,
            transition_exceptions=transition_exceptions,
        )
    if kind in {"event", "events"}:
        raise PITUniverseError(
            "event/events source kinds are unsupported; provide explicit "
            "membership intervals or snapshots"
        )
    if kind in {"interval", "intervals"}:
        if not dates:
            raise PITUniverseError("requested_rebalance_dates is required for intervals")
        intervals = load_membership_intervals(
            source,
            column_mapping=column_mapping,
            source_format=source_format,
            source_type=source_type,
            source_url=source_url,
            source_file_sha256=source_file_sha256,
            retrieved_at_utc=retrieved_at_utc,
            acquisition_manifest=acquisition_manifest,
            manifest=manifest,
        )
        return intervals_to_history(
            intervals,
            dates,
            target_size=target_size,
            transition_exceptions=transition_exceptions,
        )
    raise PITUniverseError(f"unknown source_kind: {source_kind!r}")


# Descriptive aliases for callers wiring this into universe.py later.
load_pit_universe = import_local_pit_universe
load_local_pit_universe = import_local_pit_universe
normalize_snapshots = load_constituent_snapshots
normalize_intervals = load_membership_intervals
normalize_constituent_snapshots = load_constituent_snapshots
normalize_membership_intervals = load_membership_intervals
load_snapshots = load_constituent_snapshots
validate_snapshots = validate_constituent_snapshots
validate_intervals = validate_membership_intervals
source_fingerprint = fingerprint_dataframe
file_fingerprint = sha256_file


def _read_source(
    source: str | Path | bytes | pd.DataFrame,
    *,
    source_format: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read local bytes without treating filesystem metadata as provenance."""
    if isinstance(source, pd.DataFrame):
        frame = source.copy(deep=True)
        frame.attrs = {}
        return frame, {}

    if isinstance(source, bytes):
        if not source_format:
            raise PITUniverseError("source_format is required for bytes input")
        raw_bytes = source
        suffix = _normalize_source_format(source_format)
        frame = _read_frame_bytes(raw_bytes, suffix)
        return frame, {
            "bytes": raw_bytes,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "source_type": "local_bytes_candidate",
        }

    path_text = str(source)
    if path_text.casefold().startswith("file://"):
        parsed_url = urlparse(path_text)
        path = Path(unquote(parsed_url.path))
    else:
        path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(path)
    raw_bytes = path.read_bytes()
    suffix = _normalize_source_format(source_format or path.suffix)
    frame = _read_frame_bytes(raw_bytes, suffix)
    resolved_path = path.resolve()
    return frame, {
        "path": resolved_path,
        "bytes": raw_bytes,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        # This is intentionally candidate-only metadata.  It is never used as
        # an official source URL and mtime is never used as retrieval time.
        "source_url": resolved_path.as_uri(),
        "source_type": "local_file_candidate",
    }


def _normalize_source_format(value: str) -> str:
    suffix = str(value).strip().casefold()
    if not suffix:
        raise PITUniverseError("source format must not be empty")
    return suffix if suffix.startswith(".") else f".{suffix}"


def _read_frame_bytes(raw_bytes: bytes, suffix: str) -> pd.DataFrame:
    stream = BytesIO(raw_bytes)
    if suffix == ".csv":
        return pd.read_csv(stream)
    if suffix in {".xls", ".xlsx", ".xlsm"}:
        try:
            return pd.read_excel(stream)
        except ImportError as exc:
            raise PITUniverseError(
                "Excel input is optional and requires an installed pandas Excel "
                "engine (for example openpyxl)"
            ) from exc
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(stream)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(stream, lines=suffix == ".jsonl")
    raise PITUniverseError(f"unsupported local universe file type: {suffix or '<none>'}")


def _select_manifest(
    acquisition_manifest: Mapping[str, Any] | str | Path | None,
    manifest: Mapping[str, Any] | str | Path | None,
) -> Mapping[str, Any] | str | Path | None:
    if acquisition_manifest is not None and manifest is not None:
        raise PITUniverseError("provide only one of acquisition_manifest and manifest")
    return acquisition_manifest if acquisition_manifest is not None else manifest


def _load_manifest_mapping(
    manifest: Mapping[str, Any] | str | Path,
) -> Mapping[str, Any]:
    if isinstance(manifest, Mapping):
        return dict(manifest)
    path = Path(manifest)
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PITUniverseError(f"invalid acquisition manifest: {path}") from exc
    if not isinstance(parsed, Mapping):
        raise PITUniverseError("acquisition manifest must contain a JSON object")
    return dict(parsed)


def _manifest_value(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _manifest_mapping(data: Mapping[str, Any], *names: str) -> Mapping[str, Any]:
    value = _manifest_value(data, *names)
    if value is None:
        return {}
    if not isinstance(value, Mapping) or not value:
        raise PITUniverseError(f"acquisition manifest field {names[0]!r} must be a nonempty mapping")
    return dict(value)


def _parse_acquisition_manifest(
    manifest: Mapping[str, Any] | str | Path,
) -> AcquisitionManifest:
    data = _load_manifest_mapping(manifest)
    source_url = _manifest_value(data, "official_source_url", "official_url", "source_url")
    parsed_url = urlparse(str(source_url).strip()) if source_url is not None else None
    host = parsed_url.hostname.casefold() if parsed_url and parsed_url.hostname else ""
    if (
        not isinstance(source_url, str)
        or parsed_url is None
        or parsed_url.scheme.casefold() != "https"
        or not host
        or not (host == "krx.co.kr" or host.endswith(_OFFICIAL_KRX_HOSTS[1]))
    ):
        raise PITUniverseError(
            "acquisition manifest requires an official HTTPS KRX source URL"
        )

    query_params = _manifest_mapping(data, "query_params", "query_parameters", "query")
    date_params = _manifest_mapping(
        data,
        "date_params",
        "date_parameters",
        "date_range",
        "dates",
    )
    if not query_params and not date_params:
        combined = _manifest_mapping(
            data,
            "query_date_params",
            "query_date_parameters",
            "query_parameters_and_dates",
            "request_parameters",
            "request_params",
            "date_query_params",
            "parameters",
        )
        query_params = combined
    if not query_params and not date_params:
        raise PITUniverseError(
            "acquisition manifest requires explicit query/date parameters"
        )
    if not date_params and not any(
        any(token in str(key).casefold() for token in ("date", "from", "to", "start", "end", "as_of"))
        for key in query_params
    ):
        raise PITUniverseError(
            "acquisition manifest requires explicit date parameters"
        )
    _validate_manifest_date_parameters({**query_params, **date_params})

    raw_hash = _manifest_value(
        data,
        "raw_file_sha256",
        "raw_bytes_sha256",
        "raw_sha256",
        "file_sha256",
        "source_file_sha256",
        "sha256",
    )
    normalized_hash = _normalize_sha_or_none(raw_hash)
    if normalized_hash is None:
        raise PITUniverseError(
            "acquisition manifest requires a valid raw_file_sha256 SHA-256 value"
        )
    source_type = _manifest_value(data, "source_type")
    if not isinstance(source_type, str) or source_type not in ALLOWED_ACQUISITION_SOURCE_TYPES:
        raise PITUniverseError(
            "acquisition manifest source_type must be one of "
            + ", ".join(sorted(ALLOWED_ACQUISITION_SOURCE_TYPES))
        )
    retrieved = _manifest_value(
        data,
        "retrieval_timestamp",
        "retrieved_at_utc",
        "retrieved_at",
        "retrieval_time",
    )
    retrieved_at = _aware_utc(retrieved)
    if retrieved_at is None:
        raise PITUniverseError(
            "acquisition manifest requires a timezone-aware retrieval timestamp"
        )

    attestation_key = next(
        (
            key for key in (
                "source_is_krx",
                "krx_attested",
                "krx_attestation",
                "krx_source_attestation",
                "source_attestation",
                "attestation",
            )
            if key in data
        ),
        None,
    )
    attestation = data.get(attestation_key) if attestation_key else None
    attested = (
        attestation is True
        and attestation_key in {"source_is_krx", "krx_attested"}
        or isinstance(attestation, str)
        and (
            attestation.strip().casefold() == "krx"
            or attestation_key in {"source_is_krx", "krx_attested"}
            and attestation.strip().casefold() in {"true", "source_is_krx"}
        )
        or isinstance(attestation, Mapping)
        and str(attestation.get("source", "")).strip().casefold() == "krx"
        and attestation.get("verified") is True
    )
    if not attested:
        raise PITUniverseError(
            "acquisition manifest requires an explicit attestation that the source is KRX"
        )
    return AcquisitionManifest(
        source_url=str(source_url).strip(),
        query_params=query_params,
        date_params=date_params,
        retrieved_at_utc=retrieved_at,
        source_file_sha256=normalized_hash,
        source_type=str(source_type),
        source_is_krx=True,
    )


def _verify_acquisition(
    source: str | Path | bytes | pd.DataFrame,
    file_metadata: Mapping[str, Any],
    manifest: Mapping[str, Any] | str | Path | None,
) -> tuple[AcquisitionManifest | None, str | None]:
    if manifest is None:
        return None, None
    parsed = _parse_acquisition_manifest(manifest)
    if isinstance(manifest, (str, Path)) and file_metadata.get("path") is not None:
        if Path(manifest).resolve() == file_metadata["path"]:
            raise PITUniverseError("acquisition manifest must be a sidecar, not the raw source file")
    raw_sha = file_metadata.get("sha256")
    if not isinstance(raw_sha, str):
        # A DataFrame has no raw byte identity.  Keep it structurally usable but
        # never promote it to PIT evidence, even when a caller supplies a hash.
        return parsed, None
    if raw_sha.casefold() != parsed.source_file_sha256:
        raise PITUniverseError("acquisition manifest SHA-256 does not match source bytes")
    return parsed, raw_sha.casefold()


def _validate_manifest_date_parameters(parameters: Mapping[str, Any]) -> None:
    date_tokens = ("date", "from", "to", "start", "end", "as_of", "effective")
    for key, value in parameters.items():
        if not any(token in str(key).casefold() for token in date_tokens):
            continue
        values = value if isinstance(value, (list, tuple, set)) else (value,)
        if any(_safe_date(item) is None for item in values):
            raise PITUniverseError(
                f"acquisition manifest date parameter {key!r} must use ISO or YYYYMMDD text"
            )


def _attach_acquisition_metadata(
    data: pd.DataFrame,
    manifest: AcquisitionManifest | None,
    raw_sha256: str | None,
) -> None:
    token: _VerifiedAcquisition | None = None
    if manifest is not None and raw_sha256 is not None:
        token = _VerifiedAcquisition(
            manifest=manifest,
            raw_sha256=raw_sha256,
            normalized_fingerprint=fingerprint_dataframe(data),
        )
    data.attrs["acquisition_manifest"] = manifest.as_dict() if manifest else None
    data.attrs["acquisition_manifest_verified"] = token is not None
    data.attrs["_verified_acquisition"] = token
    data.attrs["pit_candidate"] = True
    data.attrs["pit_valid"] = False
    data.attrs["provenance"] = "pit_candidate" if token is None else "pit"


def _resolve_columns(
    data: pd.DataFrame,
    mapping: Mapping[str, str] | None,
    *,
    required: Sequence[str],
    optional: Sequence[str],
) -> dict[str, str]:
    columns = list(data.columns)
    normalized_labels = [_column_key(column) for column in columns]
    duplicate_bases = [re.sub(r"\.\d+$", "", label) for label in normalized_labels]
    if (
        len(set(normalized_labels)) != len(normalized_labels)
        or len(set(duplicate_bases)) != len(duplicate_bases)
    ):
        duplicates = sorted({
            label for label in duplicate_bases if duplicate_bases.count(label) > 1
        })
        raise PITUniverseError(
            "duplicate normalized source column labels are not allowed: "
            + ", ".join(duplicates)
        )
    if any(_column_key(column) in {"event", "events"} for column in columns):
        raise PITUniverseError(
            "event/events column aliases are unsupported; provide explicit membership intervals"
        )
    by_normalized: dict[str, list[str]] = {}
    for column in columns:
        by_normalized.setdefault(_column_key(column), []).append(str(column))
    supplied = dict(mapping or {})
    if "effective_date" in supplied:
        if "as_of_date" in supplied and supplied["as_of_date"] != supplied["effective_date"]:
            raise PITUniverseError("mapping cannot define both as_of_date and effective_date")
        supplied["as_of_date"] = supplied.pop("effective_date")
    unknown = set(supplied) - set((*required, *optional))
    if unknown:
        raise PITUniverseError(f"unknown canonical column mapping: {sorted(unknown)}")
    resolved: dict[str, str] = {}
    used: dict[str, str] = {}
    for canonical in (*required, *optional):
        if canonical in supplied:
            raw_name = supplied[canonical]
            if raw_name not in columns:
                raise PITUniverseError(
                    f"column mapping for {canonical!r} points to missing column {raw_name!r}"
                )
            candidates = [raw_name]
        else:
            candidates = []
            for alias in _ALIASES.get(canonical, (canonical,)):
                candidates.extend(by_normalized.get(_column_key(alias), []))
            candidates = list(dict.fromkeys(candidates))
        if len(candidates) > 1:
            raise PITUniverseError(
                f"ambiguous source columns for {canonical!r}: {', '.join(candidates)}; "
                "provide column_mapping"
            )
        if not candidates:
            if canonical in required:
                raise PITUniverseError(f"missing required source field: {canonical}")
            continue
        raw_name = candidates[0]
        previous = used.get(raw_name)
        if previous is not None and previous != canonical:
            raise PITUniverseError(
                f"source column {raw_name!r} is mapped to both {previous!r} and {canonical!r}"
            )
        used[raw_name] = canonical
        resolved[canonical] = raw_name
    return resolved


def _rename_columns(data: pd.DataFrame, resolved: Mapping[str, str]) -> pd.DataFrame:
    result = pd.DataFrame(index=data.index)
    for canonical, raw_name in resolved.items():
        result[canonical] = data[raw_name]
    return result.reset_index(drop=True)


def _source_metadata(
    source: str | Path | bytes | pd.DataFrame,
    raw: pd.DataFrame,
    canonical: pd.DataFrame,
    *,
    file_metadata: Mapping[str, Any],
    source_type: str | None,
    source_url: str | None,
    source_file_sha256: str | None,
    retrieved_at_utc: datetime | str | None,
    verified_manifest: AcquisitionManifest | None,
) -> dict[str, list[Any]]:
    row_count = len(canonical)
    file_hash = file_metadata.get("sha256")
    if verified_manifest is not None:
        return {
            "source_type": [verified_manifest.source_type] * row_count,
            "source_url": [verified_manifest.source_url] * row_count,
            "source_file_sha256": [verified_manifest.source_file_sha256] * row_count,
            "retrieved_at_utc": [verified_manifest.retrieved_at_utc] * row_count,
        }
    column_hash = None
    if "source_file_sha256" in canonical:
        for value in canonical["source_file_sha256"]:
            column_hash = _normalize_sha_or_none(value)
            if column_hash is not None:
                break
    selected_hash = source_file_sha256 or file_hash or column_hash
    normalized_hash = _normalize_sha_or_none(selected_hash)
    if selected_hash is not None and normalized_hash is None:
        raise PITUniverseError("source_file_sha256 must be a valid SHA-256 fingerprint")
    fields: dict[str, list[Any]] = {}
    if "source_type" not in canonical:
        selected_type = source_type or file_metadata.get("source_type")
        if selected_type:
            fields["source_type"] = [selected_type] * row_count
    elif source_type is not None:
        fields["source_type"] = [source_type] * row_count
    if "source_url" not in canonical:
        selected_url = source_url or file_metadata.get("source_url")
        if selected_url:
            fields["source_url"] = [selected_url] * row_count
    elif source_url is not None:
        fields["source_url"] = [source_url] * row_count
    if "source_file_sha256" not in canonical:
        if normalized_hash is not None:
            fields["source_file_sha256"] = [normalized_hash] * row_count
    else:
        existing = canonical["source_file_sha256"]
        if normalized_hash is not None:
            existing = existing.where(existing.notna(), normalized_hash)
        existing_hashes = {_normalize_sha_or_none(value) for value in existing}
        if existing_hashes - {None} and None not in existing_hashes and len(existing_hashes) != 1:
            raise PITUniverseError("source_file_sha256 values are missing, invalid, or mixed")
        if None in existing_hashes and any(value is not None for value in existing_hashes):
            raise PITUniverseError("source_file_sha256 values are missing, invalid, or mixed")
        if existing_hashes - {None}:
            fields["source_file_sha256"] = [next(iter(existing_hashes - {None}))] * row_count
    if "retrieved_at_utc" not in canonical:
        if retrieved_at_utc is not None:
            fields["retrieved_at_utc"] = [retrieved_at_utc] * row_count
    elif retrieved_at_utc is not None:
        fields["retrieved_at_utc"] = [retrieved_at_utc] * row_count
    return fields


def _normalize_snapshot_frame(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    for field in SNAPSHOT_COLUMNS:
        if field not in result:
            result[field] = pd.NA
    result = result.loc[:, list(SNAPSHOT_COLUMNS)]
    result["index_code"] = result["index_code"].map(lambda value: str(value).strip().upper())
    result["as_of_date"] = result["as_of_date"].map(_safe_date)
    result["security_code"] = result["security_code"].map(_try_security_code)
    for field in ("name", "sector"):
        result[field] = result[field].map(_optional_text)
    for field in ("index_weight", "index_shares", "free_float"):
        result[field] = result[field].map(_optional_float)
    for field in ("source_type", "source_url"):
        result[field] = result[field].map(_optional_required_text)
    result["source_file_sha256"] = result["source_file_sha256"].map(_normalize_sha_or_none)
    result["retrieved_at_utc"] = result["retrieved_at_utc"].map(_safe_utc)
    return result


def _normalize_interval_frame(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    for field in INTERVAL_COLUMNS:
        if field not in result:
            result[field] = pd.NA
    result = result.loc[:, list(INTERVAL_COLUMNS)]
    result["index_code"] = result["index_code"].map(lambda value: str(value).strip().upper())
    result["effective_from"] = result["effective_from"].map(
        lambda value: None if _is_missing(value) else _safe_date(value)
    )
    # Do not turn a malformed non-empty end date into ``None``.  ``None`` is
    # the open-ended interval sentinel, so collapsing parse failures to it
    # would allow invalid source data to pass the PIT interval validator.
    result["effective_to"] = result["effective_to"].map(_normalize_effective_to)
    result["announcement_date"] = result["announcement_date"].map(
        lambda value: None if _is_missing(value) else _safe_date(value)
    )
    result["security_code"] = result["security_code"].map(_try_security_code)
    for field in ("action", "status", "source_type", "source_url"):
        result[field] = result[field].map(_optional_text)
    result["source_file_sha256"] = result["source_file_sha256"].map(_normalize_sha_or_none)
    result["retrieved_at_utc"] = result["retrieved_at_utc"].map(_safe_utc)
    result["provenance"] = result["provenance"].map(_parse_provenance)
    result["provenance"] = [
        _complete_provenance(row)
        for row in result.to_dict(orient="records")
    ]
    return result


def _normalize_effective_to(value: Any) -> date | Any | None:
    """Normalize valid end dates while preserving non-empty parse failures."""
    if _is_missing(value):
        return None
    parsed = _safe_date(value)
    return parsed if parsed is not None else value


def _promote_interval_provenance_fields(data: pd.DataFrame) -> None:
    """Promote structured interval provenance into canonical source fields."""
    if "provenance" not in data:
        return
    for field in _SOURCE_FIELDS:
        if field in data:
            continue
        values = [_provenance_value(value, field) for value in data["provenance"]]
        if any(value is not None for value in values):
            data[field] = values


def _provenance_value(value: Any, field: str) -> Any:
    parsed = _parse_provenance(value)
    if isinstance(parsed, Mapping):
        return parsed.get(field)
    return None


def _complete_provenance(row: Mapping[str, Any]) -> Mapping[str, Any] | str | None:
    provenance = row["provenance"]
    if not _is_empty_provenance(provenance):
        if isinstance(provenance, (Mapping, str)):
            return provenance
        return str(provenance)
    if all(
        not _is_missing(row[field])
        for field in ("source_type", "source_url", "source_file_sha256", "retrieved_at_utc")
    ):
        return {
            "source_type": row["source_type"],
            "source_url": row["source_url"],
            "source_file_sha256": row["source_file_sha256"],
            "retrieved_at_utc": _normalize_utc(row["retrieved_at_utc"]).isoformat(),
        }
    return provenance


def _interval_records(data: pd.DataFrame) -> list[MembershipInterval]:
    records: list[MembershipInterval] = []
    for row in data.to_dict(orient="records"):
        records.append(
            MembershipInterval(
                index_code=str(row["index_code"]),
                effective_from=_safe_date(row["effective_from"]),
                effective_to=(
                    None
                    if _is_missing(row["effective_to"])
                    else _safe_date(row["effective_to"])
                ),
                security_code=str(row["security_code"]),
                action=_optional_text(row["action"]),
                status=_optional_text(row["status"]),
                announcement_date=(
                    None
                    if _is_missing(row["announcement_date"])
                    else _safe_date(row["announcement_date"])
                ),
                provenance=row["provenance"],
                source_type=str(row["source_type"]),
                source_url=str(row["source_url"]),
                source_file_sha256=str(row["source_file_sha256"]),
                retrieved_at_utc=_safe_utc(row["retrieved_at_utc"]),
            )
        )
    return records


def _append_acquisition_errors(errors: list[str], data: pd.DataFrame) -> None:
    """Require the private raw-byte token for PIT promotion."""
    token = data.attrs.get("_verified_acquisition")
    if not isinstance(token, _VerifiedAcquisition):
        errors.append(
            "verified acquisition manifest is required for pit_valid=True; "
            "data is only a pit_candidate"
        )
        return
    if data.attrs.get("acquisition_manifest_verified") is not True:
        errors.append("acquisition manifest was not verified against source bytes")
        return
    if data.attrs.get("pit_valid") is True:
        # A caller-supplied flag is not evidence.  The loader always stores
        # False here; this branch prevents a forged attrs flag from being
        # treated as part of the contract.
        errors.append("caller-supplied pit_valid metadata is ignored")
    try:
        current_fingerprint = fingerprint_dataframe(data)
    except (TypeError, ValueError):
        current_fingerprint = None
    if current_fingerprint != token.normalized_fingerprint:
        errors.append("normalized data no longer matches verified source bytes")


def _known_membership_value(value: Any) -> bool:
    if _is_missing(value):
        return True
    return _membership_value_state(value) is not None


def _membership_value_state(value: Any) -> bool | None:
    if _is_missing(value):
        return None
    text = str(value).strip().casefold()
    active_values = {
        "active", "include", "included", "add", "added", "in", "entry",
        "enter", "maintain", "included_in_index", "편입", "유지", "유효",
    }
    inactive_values = {
        "inactive", "remove", "removed", "delete", "deleted", "drop", "dropped",
        "exclude", "excluded", "exit", "out", "탈락", "편출", "제외",
    }
    if text in active_values:
        return True
    if text in inactive_values:
        return False
    return None


def _interval_is_active(action: str | None, status: str | None) -> bool:
    """Return active only for an exact, known membership state."""
    states = [
        state for state in (_membership_value_state(action), _membership_value_state(status))
        if state is not None
    ]
    return bool(states) and all(states)


def _interval_metadata(row: MembershipInterval) -> dict[str, Any]:
    return {
        "index_code": row.index_code,
        "security_code": row.security_code,
        "effective_from": row.effective_from.isoformat() if row.effective_from else None,
        "effective_to": row.effective_to.isoformat() if row.effective_to else None,
        "action": row.action,
        "status": row.status,
        "announcement_date": (
            row.announcement_date.isoformat() if row.announcement_date else None
        ),
        "provenance": row.provenance,
        "source_type": row.source_type,
        "source_url": row.source_url,
        "source_file_sha256": row.source_file_sha256,
        "retrieved_at_utc": (
            _normalize_utc(row.retrieved_at_utc).isoformat()
            if row.retrieved_at_utc is not None else None
        ),
    }


def _materialize_transition_exceptions(
    exceptions: Mapping[Any, Any] | Iterable[Any] | None,
) -> dict[date, TransitionExceptionPolicy]:
    """Consume one-shot iterables once and reject undocumented bypasses."""
    if exceptions is None:
        return {}
    items = exceptions.items() if isinstance(exceptions, Mapping) else (
        (item, True) for item in exceptions
    )
    materialized: dict[date, TransitionExceptionPolicy] = {}
    for raw_date, raw_policy in items:
        parsed_date = _safe_date(raw_date)
        if parsed_date is None:
            raise PITUniverseError(f"invalid transition exception date: {raw_date!r}")
        policy = _parse_transition_policy(raw_policy)
        if policy is not None:
            materialized[parsed_date] = policy
    return materialized


def _parse_transition_policy(value: Any) -> TransitionExceptionPolicy | None:
    if isinstance(value, TransitionExceptionPolicy):
        return value
    if not isinstance(value, Mapping):
        raise PITUniverseError(
            "transition exception requires an explicit documented policy; "
            "bare True or an undocumented size bypass is not accepted"
        )
    reason = value.get("reason", value.get("documentation"))
    allowed_sizes = value.get("allowed_sizes", value.get("sizes"))
    if not isinstance(reason, str) or not reason.strip():
        raise PITUniverseError(
            "transition exception policy requires a nonempty reason/documentation"
        )
    if isinstance(allowed_sizes, int) and not isinstance(allowed_sizes, bool):
        allowed = {allowed_sizes}
    elif isinstance(allowed_sizes, Iterable) and not isinstance(allowed_sizes, (str, bytes)):
        allowed = set(allowed_sizes)
    else:
        raise PITUniverseError(
            "transition exception policy requires explicit allowed_sizes"
        )
    if not allowed or any(isinstance(size, bool) or not isinstance(size, int) for size in allowed):
        raise PITUniverseError("transition exception policy allowed_sizes must contain integers")
    return TransitionExceptionPolicy(frozenset(allowed), reason.strip())


def _finish_validation(
    errors: list[str],
    warnings: list[str],
    sizes: dict[str, int],
    diagnostics: dict[str, Any],
    raise_on_error: bool,
) -> PITValidationResult:
    unique_errors = tuple(dict.fromkeys(errors))
    structural_errors = tuple(
        error for error in unique_errors if not _is_candidate_metadata_error(error)
    )
    pit_candidate = not structural_errors
    provenance = "pit" if not unique_errors else (
        "pit_candidate" if pit_candidate else "unverified"
    )
    report = PITValidationResult(
        pit_valid=not unique_errors and pit_candidate,
        errors=unique_errors,
        warnings=tuple(dict.fromkeys(warnings)),
        snapshot_sizes=dict(sizes),
        diagnostics=diagnostics,
        pit_candidate=pit_candidate,
        provenance=provenance,
    )
    if raise_on_error and not report.pit_valid:
        raise PITUniverseValidationError(report)
    return report


def _is_candidate_metadata_error(error: str) -> bool:
    """Keep raw-file structural candidates distinct from provenance failures."""
    metadata_terms = (
        "verified acquisition manifest",
        "acquisition manifest was not verified",
        "caller-supplied pit_valid",
        "normalized data no longer matches",
        "source_type",
        "source_url",
        "source_file_sha256",
        "retrieved_at_utc",
        "per-date source metadata",
        "provenance is required",
    )
    return any(term in error for term in metadata_terms)


def _requested_dates(
    values: Iterable[date | datetime | str] | None,
    single: date | datetime | str | None,
) -> list[date]:
    raw_values: list[Any] = []
    if values is not None:
        raw_values.extend(values)
    if single is not None:
        raw_values.append(single)
    normalized: list[date] = []
    for value in raw_values:
        parsed = _safe_date(value)
        if parsed is None:
            raise PITUniverseError(f"invalid requested rebalance date: {value!r}")
        normalized.append(parsed)
    return sorted(set(normalized))


def _transition_exception(
    value: Any,
    exceptions: Mapping[date, TransitionExceptionPolicy] | None,
) -> TransitionExceptionPolicy | None:
    if exceptions is None:
        return None
    target = _safe_date(value)
    return exceptions.get(target) if target is not None else None


def _size_allowed(size: int, target: int | None, exception: Any) -> bool:
    if target is None or size == target:
        return True
    if exception is None:
        return False
    if isinstance(exception, TransitionExceptionPolicy):
        return size in exception.allowed_sizes
    return False


def _append_empty_errors(errors: list[str], data: pd.DataFrame, field: str, label: str) -> None:
    if field not in data:
        errors.append(f"missing canonical field: {field}")
        return
    if any(_is_missing(value) for value in data[field]):
        errors.append(f"{label} contains empty values")


def _column_key(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).strip().casefold())


def _normalize_date(value: Any) -> date:
    parsed = _safe_date(value)
    if parsed is None:
        raise ValueError(f"invalid date: {value!r}")
    return parsed


def _safe_date(value: Any) -> date | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                return date.fromisoformat(text)
            if re.fullmatch(r"\d{8}", text):
                return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
        return None
    if isinstance(value, (date, datetime, pd.Timestamp)):
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if bool(pd.isna(parsed)):
            return None
        return cast(date, parsed.to_pydatetime().date())
    return None


def _normalize_security_code(value: Any) -> str:
    normalized = _try_security_code(value)
    if normalized is None:
        raise ValueError(f"invalid six-digit security code: {value!r}")
    return normalized


def _try_security_code(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    if text.casefold().startswith("a"):
        text = text[1:]
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit() or not 1 <= len(text) <= 6:
        return None
    return text.zfill(6)


def _optional_text(value: Any) -> str | None:
    return None if _is_missing(value) else str(value).strip() or None


def _optional_required_text(value: Any) -> str | None:
    return _optional_text(value)


def _optional_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def _normalize_sha(value: Any) -> str:
    normalized = _normalize_sha_or_none(value)
    if normalized is None:
        raise ValueError(f"invalid SHA-256 fingerprint: {value!r}")
    return normalized


def _normalize_sha_or_none(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    if text.casefold().startswith("sha256:"):
        text = text[7:]
    return text.lower() if SOURCE_HASH_RE.fullmatch(text) else None


def _normalize_utc(value: Any) -> datetime:
    parsed = _safe_utc(value)
    if parsed is None:
        raise ValueError(f"invalid UTC timestamp: {value!r}")
    return parsed.to_pydatetime()


def _safe_utc(value: Any) -> pd.Timestamp | None:
    return _aware_utc(value)


def _aware_utc(value: Any) -> pd.Timestamp | None:
    if _is_missing(value):
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if bool(pd.isna(parsed)):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        parsed = parsed.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError):
        return None
    return cast(pd.Timestamp, parsed)


def _parse_provenance(value: Any) -> Mapping[str, Any] | str | None:
    if _is_empty_provenance(value):
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            return parsed if isinstance(parsed, Mapping) else text
        return text
    return str(value)


def _is_empty_provenance(value: Any) -> bool:
    return _is_missing(value) or not str(value).strip()


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _canonical_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        timestamp = cast(pd.Timestamp, pd.Timestamp(value))
        return timestamp.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


__all__ = [
    "ALLOWED_ACQUISITION_SOURCE_TYPES",
    "AcquisitionManifest",
    "EVENT_COLUMNS",
    "INTERVAL_COLUMNS",
    "INDEX_CODE",
    "PITUniverseError",
    "PITUniverseValidationError",
    "PITValidationResult",
    "SNAPSHOT_COLUMNS",
    "ConstituentSnapshot",
    "MembershipInterval",
    "TransitionExceptionPolicy",
    "file_fingerprint",
    "fingerprint_dataframe",
    "import_local_pit_universe",
    "intervals_to_history",
    "load_constituent_snapshots",
    "load_local_pit_universe",
    "load_membership_intervals",
    "load_pit_universe",
    "load_snapshots",
    "normalize_intervals",
    "normalize_constituent_snapshots",
    "normalize_membership_intervals",
    "normalize_snapshots",
    "sha256_file",
    "source_fingerprint",
    "snapshots_to_history",
    "validate_constituent_snapshots",
    "validate_intervals",
    "validate_membership_intervals",
    "validate_snapshots",
]
