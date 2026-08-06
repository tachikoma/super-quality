"""Local-file-first OpenDART filing provenance.

This module is intentionally an importer and validator, not an OpenDART
client.  A downloaded response and a sidecar manifest are the unit of trust.
The manifest must contain the SHA-256 of the response bytes; hashes copied
inside a response, a DataFrame, or a fiscal-period file are not evidence.

The important join in this module is ``(corp_code, rcept_no)``.  A fiscal year
or period is descriptive metadata only and is never used to attach a fact to a
filing.  This keeps a restatement from silently becoming the original filing.

OpenDART's official ``rcept_dt`` is date-only.  The importer therefore uses
strict ``next_session`` availability in the Asia/Seoul local calendar.  A
session-cutoff timestamp cannot be supplied after the join and remains deferred
unless raw filing timestamp lineage is attested by the verified manifest and
normalized frame fingerprint.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from k200_mq.data.account_mapping import ACCOUNT_COLUMN_MAPPING, find_account_value
from k200_mq.data.provenance import (
    FINANCIAL_PROVENANCE_CONTRACT_ATTR,
    NEXT_SESSION_POLICY,
    find_filing_date_field,
)


FILING_METADATA_COLUMNS = (
    "corp_code",
    "stock_code",
    "corp_name",
    "rcept_no",
    "rcept_dt_raw",
    "rcept_date",
    "report_nm",
    "pblntf_ty",
    "pblntf_detail_ty",
    "rm",
    "is_amendment",
    "is_withdrawn",
    "source",
    "raw_payload_path",
    "response_sha256",
    "retrieved_at_utc",
)

FINANCIAL_FACT_COLUMNS = (
    "rcept_no",
    "corp_code",
    "bsns_year",
    "reprt_code",
    "fs_div",
    "sj_div",
    "account_id",
    "account_name",
    "account_detail",
    "period_end",
    "raw_value",
    "numeric_value",
    "currency",
    "payload_sha256",
)

# Descriptive aliases make the schema discoverable without making callers
# depend on one spelling used by an upstream parser.
DART_FILING_METADATA_COLUMNS = FILING_METADATA_COLUMNS
DART_FINANCIAL_FACT_COLUMNS = FINANCIAL_FACT_COLUMNS
RAW_FILING_METADATA_COLUMNS = FILING_METADATA_COLUMNS
RAW_FINANCIAL_FACT_COLUMNS = FINANCIAL_FACT_COLUMNS

AMENDMENT_POLICIES = frozenset({
    "latest_filing_available_as_of",
    "first_filing",
})
EXPLICIT_TIMESTAMP_POLICY = "session_cutoff"
OFFICIAL_FILING_TIMEZONE = "Asia/Seoul"
OPEN_DART_ENDPOINTS = {
    "filing_list": "https://opendart.fss.or.kr/api/list.json",
    "financial_facts": "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
    # Retained as a descriptive reference only.  It is deliberately not an
    # accepted filing-list or financial-facts acquisition endpoint.
    "document": "https://opendart.fss.or.kr/api/document.xml",
}
_DART_FINANCIAL_ENDPOINT_PATHS = frozenset({
    "/api/fnlttSinglAcnt.json",
    "/api/fnlttSinglAcntAll.json",
})
_DART_SOURCE_TYPE_ALIASES = {
    "filing_metadata": frozenset({
        "dartfilinglist",
        "dartfilingmetadata",
        "opendartfilinglist",
        "opendartfilingmetadata",
        "opendartlist",
        "filinglist",
        "filingmetadata",
        "listjson",
    }),
    "financial_facts": frozenset({
        "dartfinancialfacts",
        "opendartfinancialfacts",
        "opendartfinancial",
        "financialfacts",
        "financialfactsjson",
    }),
}
# DART-prefixed aliases are the package-level API.  The short names remain
# available from this module for compatibility with existing direct imports.
DART_AMENDMENT_POLICIES = AMENDMENT_POLICIES
DART_EXPLICIT_TIMESTAMP_POLICY = EXPLICIT_TIMESTAMP_POLICY
DART_OFFICIAL_FILING_TIMEZONE = OFFICIAL_FILING_TIMEZONE
DART_OPEN_DART_ENDPOINTS = OPEN_DART_ENDPOINTS
DART_RAW_FINANCIAL_FACT_COLUMNS = RAW_FINANCIAL_FACT_COLUMNS
DART_RAW_FILING_METADATA_COLUMNS = RAW_FILING_METADATA_COLUMNS
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
YYYYMMDD_RE = re.compile(r"^[0-9]{8}$")

_DART_LINEAGE_ATTR = "_dart_lineage"
_DART_JOIN_EVIDENCE_ATTR = "_dart_join_evidence"
_DART_VALIDATION_EVIDENCE_ATTR = "_dart_validation_evidence"
_DART_FACT_LINEAGE_ATTR = "_dart_fact_lineage"
_DART_FILING_LINEAGE_ATTR = "_dart_filing_lineage"


class _DARTIssuer:
    def __deepcopy__(self, memo: dict[int, Any]) -> "_DARTIssuer":
        return self


_DART_ISSUER = _DARTIssuer()
_DART_TIMESTAMP_FIELDS = (
    "filing_timestamp", "filing_datetime", "availability_timestamp", "availability_datetime",
    "cutoff_timestamp", "cutoff_datetime", "filed_at", "published_at",
)
_DART_SHARED_FACT_COLUMNS = {"corp_code", "rcept_no"}
_DART_STATUS_VALUES = frozenset({"is_amendment", "is_withdrawn"})


@dataclass(frozen=True)
class _DARTLineage:
    """Opaque evidence issued only by this importer."""

    issuer: object
    kind: str
    fingerprint: str
    columns: tuple[str, ...]
    raw_sha256: str | None
    raw_source_path: str | None
    manifest_path: str | None
    manifest_digest: str | None
    manifest_verified: bool
    source_url: str | None


@dataclass(frozen=True)
class _DARTJoinEvidence:
    issuer: object
    fingerprint: str
    columns: tuple[str, ...]
    facts: _DARTLineage | None
    filings: _DARTLineage | None
    # Retained for compatibility with the importer evidence shape.  Validation
    # now requires an exact joined-frame fingerprint and never accepts a
    # post-join projection that replaces ``rcept_dt_raw``.
    without_rcept_dt_raw_fingerprint: str | None = None


@dataclass(frozen=True)
class _DARTValidationEvidence:
    issuer: object
    fingerprint: str
    columns: tuple[str, ...]
    facts: _DARTLineage
    filings: _DARTLineage
    availability_policy: str
    timezone_name: str | None
    cutoff: str | None
    amendment_policy: str


class DARTPITError(ValueError):
    """Raised for malformed local DART input or failed provenance checks."""


class DARTPITValidationError(DARTPITError):
    """Raised when a prepared DART frame cannot satisfy the PIT contract."""

    def __init__(self, report: "DARTPITValidationResult") -> None:
        self.report = report
        details = "; ".join(report.errors) or "DART PIT validation failed"
        super().__init__(details)


@dataclass(frozen=True)
class DARTPITValidationResult:
    """Mapping-compatible report returned by :func:`validate_dart_pit`."""

    pit_valid: bool
    mode: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] | None = None
    provenance: str = "invalid"

    @property
    def valid(self) -> bool:
        return self.pit_valid

    def as_dict(self) -> dict[str, Any]:
        result = {
            "pit_valid": self.pit_valid,
            "valid": self.pit_valid,
            "mode": self.mode,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "diagnostics": dict(self.diagnostics or {}),
            "provenance": self.provenance,
        }
        for key in ("availability_policy", "amendment_policy", "join_key", "source_hashes_verified"):
            if key in result["diagnostics"]:
                result[key] = result["diagnostics"][key]
        return result

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def __bool__(self) -> bool:
        return self.pit_valid


@dataclass(frozen=True)
class _LocalSource:
    frame: pd.DataFrame
    raw_bytes: bytes | None
    path: Path | None
    source_format: str


def sha256_bytes(raw_bytes: bytes) -> str:
    """Return the SHA-256 digest of raw response bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of a local response file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_label(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).strip().casefold())


def _json_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, Mapping):
        nested = payload.get("list")
        if nested is None and isinstance(payload.get("result"), Mapping):
            nested = payload["result"].get("list")
        if nested is None:
            # A single record is useful for small local fixtures, but an
            # OpenDART envelope with no list is not silently treated as data.
            if any(key in payload for key in ("rcept_no", "account_id", "account_nm")):
                records = [payload]
            else:
                raise DARTPITError("JSON response does not contain a list of records")
        else:
            records = nested
    else:
        raise DARTPITError("JSON response must be a list or object")
    if not isinstance(records, list) or not all(isinstance(row, Mapping) for row in records):
        raise DARTPITError("JSON response list must contain objects")
    return [dict(row) for row in records]


def _read_local_source(
    source: str | Path | bytes | bytearray | pd.DataFrame,
    *,
    source_format: str | None = None,
) -> _LocalSource:
    path: Path | None = None
    raw_bytes: bytes | None = None
    if isinstance(source, pd.DataFrame):
        frame = source.copy(deep=True)
        fmt = (source_format or "dataframe").casefold()
        return _LocalSource(frame, None, None, fmt)
    if isinstance(source, (bytes, bytearray)):
        raw_bytes = bytes(source)
        fmt = (source_format or "json").casefold().lstrip(".")
    else:
        path = Path(source)
        if not path.is_file():
            raise DARTPITError(f"local DART source does not exist: {path}")
        raw_bytes = path.read_bytes()
        fmt = (source_format or path.suffix.lstrip(".")).casefold()
    if fmt in {"json", "jsonl"}:
        try:
            if fmt == "jsonl":
                payload = [json.loads(line) for line in raw_bytes.decode("utf-8").splitlines() if line]
            else:
                payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DARTPITError("invalid local DART JSON response") from exc
        frame = pd.DataFrame(_json_records(payload))
    elif fmt == "csv":
        try:
            frame = pd.read_csv(BytesIO(raw_bytes), dtype=object)
        except (OSError, ValueError) as exc:
            raise DARTPITError("invalid local DART CSV response") from exc
    elif fmt in {"parquet", "pq"}:
        try:
            frame = pd.read_parquet(BytesIO(raw_bytes))
        except (OSError, ValueError, ImportError) as exc:
            raise DARTPITError("invalid local DART Parquet response") from exc
    else:
        raise DARTPITError(f"unsupported local DART format: {fmt!r}")
    return _LocalSource(frame, raw_bytes, path, fmt)


def _load_manifest(manifest: Mapping[str, Any] | str | Path | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    if isinstance(manifest, Mapping):
        return dict(manifest)
    path = Path(manifest)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DARTPITError(f"invalid DART response manifest: {path}") from exc
    if not isinstance(value, Mapping):
        raise DARTPITError("DART response manifest must be a JSON object")
    return dict(value)


def _manifest_hash(manifest: Mapping[str, Any]) -> str | None:
    value = manifest.get(
        "response_sha256",
        manifest.get(
            "raw_response_sha256",
            manifest.get("raw_file_sha256", manifest.get("source_file_sha256")),
        ),
    )
    return str(value).lower() if isinstance(value, str) and SHA256_RE.fullmatch(value) else None


def _canonical_value(value: Any) -> Any:
    """Convert one normalized cell to a deterministic JSON value."""
    if value is None or value is pd.NaT:
        return None
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _normalized_frame_fingerprint(
    frame: pd.DataFrame,
    columns: Sequence[str] | None = None,
) -> str:
    """Fingerprint normalized values, not mutable DataFrame attributes."""
    selected = tuple(str(column) for column in (columns or frame.columns))
    if any(column not in frame.columns for column in selected):
        return ""
    rows = [
        [_canonical_value(value) for value in row]
        for row in frame.loc[:, list(selected)].itertuples(index=False, name=None)
    ]
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    payload = json.dumps(
        {"columns": selected, "rows": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest_digest(manifest: Mapping[str, Any] | None) -> str | None:
    if not isinstance(manifest, Mapping):
        return None
    normalized = dict(manifest)
    raw_hash = _manifest_hash(normalized)
    if raw_hash is not None:
        normalized["response_sha256"] = raw_hash
    normalized["verified"] = _manifest_has_verified_contract(normalized)
    try:
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return sha256_bytes(payload.encode("utf-8"))


def _manifest_declared_source(manifest: Mapping[str, Any]) -> str | None:
    value = manifest.get(
        "source_url",
        manifest.get(
            "official_source_url",
            manifest.get("official_url", manifest.get("source_endpoint")),
        ),
    )
    if value is None:
        endpoint = manifest.get("endpoint")
        if isinstance(endpoint, str):
            value = OPEN_DART_ENDPOINTS.get(endpoint, endpoint)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _manifest_source_url(manifest: Mapping[str, Any]) -> str | None:
    value = _manifest_declared_source(manifest)
    if value is None:
        return None
    url = value
    parsed = urlparse(url)
    allowed = {urlparse(OPEN_DART_ENDPOINTS["filing_list"]).path}
    allowed.update(_DART_FINANCIAL_ENDPOINT_PATHS)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    financial_path = (
        parsed.path.startswith("/api/fnlttSinglAcnt")
        and parsed.path.endswith(".json")
    )
    if (
        parsed.scheme.casefold() != "https"
        or host != "opendart.fss.or.kr"
        or (parsed.path not in allowed and not financial_path)
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return None
    return url


def _manifest_source_type(manifest: Mapping[str, Any]) -> str | None:
    value = manifest.get(
        "source_type",
        manifest.get("source_kind", manifest.get("dataset_type", manifest.get("kind"))),
    )
    if not isinstance(value, str) or not value.strip():
        return None
    return _normal_label(value)


def _manifest_endpoint_kind(manifest: Mapping[str, Any]) -> str | None:
    """Classify an official OpenDART endpoint without accepting its label."""
    source_url = _manifest_source_url(manifest)
    if source_url is None:
        return None
    path = urlparse(source_url).path
    if path == urlparse(OPEN_DART_ENDPOINTS["filing_list"]).path:
        return "filing_metadata"
    if path in _DART_FINANCIAL_ENDPOINT_PATHS or (
        path.startswith("/api/fnlttSinglAcnt") and path.endswith(".json")
    ):
        return "financial_facts"
    return None


def _manifest_matches_kind(manifest: Mapping[str, Any], expected_kind: str) -> bool:
    endpoint_kind = _manifest_endpoint_kind(manifest)
    if endpoint_kind != expected_kind:
        return False
    source_type = _manifest_source_type(manifest)
    return source_type is None or source_type in _DART_SOURCE_TYPE_ALIASES[expected_kind]


def _manifest_declares_endpoint(manifest: Mapping[str, Any]) -> bool:
    return _manifest_declared_source(manifest) is not None or _manifest_source_type(manifest) is not None


def _manifest_parameter_hash(manifest: Mapping[str, Any]) -> str | None:
    value = manifest.get(
        "sanitized_request_params_sha256",
        manifest.get(
            "request_params_sha256",
            manifest.get(
                "request_params_hash",
                manifest.get(
                    "request_parameter_sha256",
                    manifest.get("sanitized_request_parameters_sha256"),
                ),
            ),
        ),
    )
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        return None
    params = manifest.get("sanitized_request_params", manifest.get("request_params"))
    if params is not None:
        if not isinstance(params, Mapping):
            return None
        secret_names = {"api_key", "crtfc_key", "apikey", "key", "token"}
        if any(str(key).casefold() in secret_names for key in params):
            return None
        try:
            payload = json.dumps(
                dict(params), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return None
        if sha256_bytes(payload.encode("utf-8")) != value.lower():
            return None
    return value.lower()


def _manifest_pagination_complete(manifest: Mapping[str, Any]) -> bool:
    pagination = manifest.get("pagination", manifest.get("completeness"))
    if isinstance(pagination, Mapping):
        value = pagination.get(
            "complete", pagination.get("is_complete", pagination.get("completed")),
        )
    else:
        value = manifest.get("pagination_complete", manifest.get("complete"))
    return value is True


def _flag_is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().casefold() in {"true", "yes", "1"})


def _manifest_api_success(manifest: Mapping[str, Any]) -> bool:
    value = manifest.get("api_status", manifest.get("api_status_code", manifest.get("status")))
    if isinstance(value, Mapping):
        value = value.get("code", value.get("status"))
    if not isinstance(value, (str, int)):
        return False
    return str(value).strip().casefold() in {"000", "0", "ok", "success"}


def _manifest_has_verified_contract(
    manifest: Mapping[str, Any],
    *,
    expected_kind: str | None = None,
) -> bool:
    """Check the acquisition contract without trusting a caller's boolean."""
    fixture = manifest.get("fixture", manifest.get("is_fixture"))
    unverified = manifest.get("unverified")
    if _flag_is_true(fixture):
        return False
    if _flag_is_true(unverified) or manifest.get("verified") is False:
        return False
    if expected_kind is not None and not _manifest_matches_kind(manifest, expected_kind):
        return False
    return bool(
        _manifest_source_url(manifest)
        and _manifest_parameter_hash(manifest)
        and _manifest_api_success(manifest)
        and _manifest_pagination_complete(manifest)
        and _parse_retrieved(manifest.get("retrieved_at_utc"))
    )


def _manifest_timestamp_fields(manifest: Mapping[str, Any]) -> set[str]:
    """Return timestamp columns explicitly attested by a raw-file manifest."""
    fields: set[str] = set()
    for key in (
        "timestamp_fields",
        "filing_timestamp_fields",
        "availability_timestamp_fields",
        "timestamp_lineage_fields",
    ):
        value = manifest.get(key)
        if isinstance(value, str):
            fields.add(value.strip())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            fields.update(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, Mapping):
            fields.update(str(item).strip() for item in value if str(item).strip())
    lineage = manifest.get("timestamp_lineage")
    if isinstance(lineage, str) and lineage.strip():
        fields.add(lineage.strip())
    elif isinstance(lineage, Mapping):
        fields.update(str(key).strip() for key in lineage if str(key).strip() in _DART_TIMESTAMP_FIELDS)
        for key in ("field", "column", "name"):
            value = lineage.get(key)
            if isinstance(value, str) and value.strip():
                fields.add(value.strip())
    return fields


def _verified_manifest(
    source: _LocalSource,
    manifest: Mapping[str, Any] | str | Path | None,
    *,
    expected_kind: str | None = None,
) -> tuple[dict[str, Any] | None, bool, str | None]:
    data = _load_manifest(manifest)
    if data is None:
        return None, False, None
    if isinstance(manifest, (str, Path)) and source.path is not None:
        if Path(manifest).resolve() == source.path.resolve():
            raise DARTPITError("DART response manifest must be a sidecar, not the raw response file")
    expected = _manifest_hash(data)
    if expected is None:
        raise DARTPITError("DART response manifest requires a valid response SHA-256")
    if source.raw_bytes is None:
        raise DARTPITError("raw response manifest cannot verify a DataFrame without bytes")
    actual = sha256_bytes(source.raw_bytes)
    if actual != expected:
        raise DARTPITError("DART response manifest SHA-256 does not match source bytes")
    if (
        expected_kind is not None
        and _manifest_declares_endpoint(data)
        and not _manifest_matches_kind(data, expected_kind)
    ):
        raise DARTPITError(
            f"DART manifest endpoint/source type is not valid for {expected_kind}"
        )
    # A raw-byte match proves only that the sidecar belongs to this file.  PIT
    # eligibility additionally requires the complete importer contract below.
    # Incomplete manifests remain useful as non-PIT candidates, but never issue
    # verified lineage.
    verified = _manifest_has_verified_contract(data, expected_kind=expected_kind)
    data["response_sha256"] = expected
    data["verified"] = verified
    return data, verified, expected if verified else None


_FILING_ALIASES: dict[str, tuple[str, ...]] = {
    "corp_code": ("corp_code", "corp code", "법인코드"),
    "stock_code": ("stock_code", "stock code", "종목코드"),
    "corp_name": ("corp_name", "corp name", "법인명"),
    "rcept_no": ("rcept_no", "rcept no", "접수번호"),
    "rcept_dt_raw": ("rcept_dt_raw", "rcept_dt", "receipt_date", "접수일자"),
    "report_nm": ("report_nm", "report name", "보고서명"),
    "pblntf_ty": ("pblntf_ty", "publication_type", "공시유형"),
    "pblntf_detail_ty": ("pblntf_detail_ty", "publication_detail_type", "공시상세유형"),
    "rm": ("rm", "remarks", "비고"),
    "is_amendment": ("is_amendment", "amendment", "정정"),
    "is_withdrawn": ("is_withdrawn", "withdrawn", "철회"),
    "source": ("source", "source_endpoint", "출처"),
    "raw_payload_path": ("raw_payload_path", "payload_path", "raw path"),
    "response_sha256": ("response_sha256", "response sha256", "raw_file_sha256", "sha256"),
    "retrieved_at_utc": ("retrieved_at_utc", "retrieved_at", "수집시각"),
}

_FACT_ALIASES: dict[str, tuple[str, ...]] = {
    "rcept_no": ("rcept_no", "rcept no", "접수번호"),
    "corp_code": ("corp_code", "corp code", "법인코드"),
    "bsns_year": ("bsns_year", "business_year", "사업연도"),
    "reprt_code": ("reprt_code", "report_code", "보고서코드"),
    "fs_div": ("fs_div", "financial_statement_division", "재무제표구분"),
    "sj_div": ("sj_div", "statement_division", "재무제표구분코드"),
    "account_id": ("account_id", "account code", "계정ID"),
    "account_name": ("account_name", "account_nm", "account name", "계정명"),
    "account_detail": ("account_detail", "account detail", "계정상세"),
    "period_end": ("period_end", "period end", "thstrm_end_de", "기간말"),
    "raw_value": (
        "raw_value", "value_raw", "thstrm_amount", "amount", "value", "금액",
    ),
    "numeric_value": ("numeric_value", "value_numeric", "numeric amount"),
    "currency": ("currency", "통화"),
    "payload_sha256": ("payload_sha256", "payload sha256", "sha256"),
}


def _resolve_columns(
    frame: pd.DataFrame,
    aliases: Mapping[str, Sequence[str]],
    mapping: Mapping[str, str] | None,
) -> dict[str, str]:
    columns = [str(column) for column in frame.columns]
    if len({_normal_label(column) for column in columns}) != len(columns):
        raise DARTPITError("duplicate normalized local DART source columns")
    by_label = {_normal_label(column): column for column in columns}
    explicit = dict(mapping or {})
    resolved: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        if canonical in explicit:
            source_column = explicit[canonical]
            if source_column not in frame.columns:
                raise DARTPITError(f"column mapping for {canonical!r} is missing")
            resolved[canonical] = source_column
            continue
        matches = [by_label[_normal_label(candidate)] for candidate in candidates
                   if _normal_label(candidate) in by_label]
        if len(set(matches)) > 1:
            raise DARTPITError(f"ambiguous local DART columns for {canonical!r}")
        if matches:
            resolved[canonical] = matches[0]
    return resolved


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text if text else None


def _bool_value(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "정정", "철회", "취소", "withdrawn"}:
        return True
    if text in {"0", "false", "no", "n", "정상", "아니오"}:
        return False
    return None


def _strict_rcept_date(value: Any) -> tuple[str | None, date | None]:
    if value is None or pd.isna(value):
        return None, None
    if isinstance(value, bool):
        return None, None
    raw = str(value).strip()
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    if not YYYYMMDD_RE.fullmatch(raw):
        return raw, None
    try:
        parsed = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return raw, None
    return raw, parsed


def _parse_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if YYYYMMDD_RE.fullmatch(text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if pd.isna(parsed) else parsed.date()


def _parse_retrieved(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed) or parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _derive_amendment(report_name: Any, remarks: Any) -> bool:
    text = " ".join(part for part in (_text(report_name), _text(remarks)) if part).casefold()
    return any(term in text for term in ("정정", "amend", "restat", "correction"))


def _derive_withdrawn(remarks: Any, report_name: Any) -> bool:
    text = " ".join(part for part in (_text(remarks), _text(report_name)) if part).casefold()
    return any(term in text for term in ("철회", "취소", "withdraw", "cancel", "폐기"))


def _source_context(
    local: _LocalSource,
    manifest: Mapping[str, Any] | None,
    verified_hash: str | None,
    manifest_verified: bool,
) -> dict[str, Any]:
    manifest = manifest or {}
    source = _manifest_source_url(manifest) if manifest_verified else None
    path = str(local.path.resolve()) if local.path is not None else ""
    retrieved = _parse_retrieved(manifest.get("retrieved_at_utc")) if manifest_verified else None
    return {
        "source": source or "unverified_local_dart_response",
        "raw_payload_path": path,
        "response_sha256": verified_hash if manifest_verified else None,
        "retrieved_at_utc": retrieved,
    }


def _status_value(value: Any, field: str) -> bool:
    parsed = _bool_value(value)
    if parsed is None:
        raise DARTPITError(f"unknown explicit {field} value")
    return parsed


def _reject_fact_provenance_collisions(frame: pd.DataFrame) -> None:
    """Do not allow response facts to override filing authority."""
    filing_labels = {
        _normal_label(alias)
        for canonical, aliases in _FILING_ALIASES.items()
        if canonical not in _DART_SHARED_FACT_COLUMNS
        for alias in aliases
    }
    filing_labels.update(
        _normal_label(column)
        for column in FILING_METADATA_COLUMNS
        if column not in _DART_SHARED_FACT_COLUMNS
    )
    filing_labels.update(
        _normal_label(column)
        for column in (*_DART_TIMESTAMP_FIELDS, "filing_date", "publication_date", "report_date")
    )
    collisions = [
        str(column) for column in frame.columns if _normal_label(column) in filing_labels
    ]
    if collisions:
        raise DARTPITError(
            "financial facts contain filing provenance columns: "
            + ", ".join(collisions)
        )


def _issue_lineage(
    frame: pd.DataFrame,
    *,
    kind: str,
    raw_sha256: str | None,
    manifest: Mapping[str, Any] | None,
    manifest_verified: bool,
    raw_source_path: str | None,
    manifest_path: str | None = None,
) -> _DARTLineage:
    columns = tuple(str(column) for column in frame.columns)
    return _DARTLineage(
        issuer=_DART_ISSUER,
        kind=kind,
        fingerprint=_normalized_frame_fingerprint(frame, columns),
        columns=columns,
        raw_sha256=raw_sha256,
        raw_source_path=raw_source_path,
        manifest_path=manifest_path,
        manifest_digest=_manifest_digest(manifest) if manifest_verified else None,
        manifest_verified=manifest_verified,
        source_url=_manifest_source_url(manifest or {}) if manifest_verified else None,
    )


def _lineage_valid(frame: pd.DataFrame, lineage: Any, expected_kind: str | None = None) -> bool:
    if not isinstance(lineage, _DARTLineage) or lineage.issuer is not _DART_ISSUER:
        return False
    if expected_kind is not None and lineage.kind != expected_kind:
        return False
    return (
        tuple(str(column) for column in frame.columns) == lineage.columns
        and _normalized_frame_fingerprint(frame, lineage.columns) == lineage.fingerprint
    )


def _join_projection_fingerprint(frame: pd.DataFrame) -> str | None:
    if "rcept_dt_raw" not in frame.columns:
        return None
    columns = tuple(
        str(column) for column in frame.columns
        if column != "rcept_dt_raw" and column not in _DART_TIMESTAMP_FIELDS
    )
    return _normalized_frame_fingerprint(frame, columns)


def _join_evidence_valid(frame: pd.DataFrame, evidence: Any) -> bool:
    if not isinstance(evidence, _DARTJoinEvidence) or evidence.issuer is not _DART_ISSUER:
        return False
    if (
        frame.attrs.get("dart_join_valid") is not True
        or frame.attrs.get("dart_join_key") != "(corp_code, rcept_no)"
        or frame.attrs.get("dart_fiscal_period_join_used") is not False
    ):
        return False
    if evidence.facts is not None and (
        not isinstance(evidence.facts, _DARTLineage)
        or evidence.facts.issuer is not _DART_ISSUER
        or evidence.facts.kind != "financial_facts"
    ):
        return False
    if evidence.filings is not None and (
        not isinstance(evidence.filings, _DARTLineage)
        or evidence.filings.issuer is not _DART_ISSUER
        or evidence.filings.kind != "filing_metadata"
    ):
        return False
    current_columns = tuple(str(column) for column in frame.columns)
    current_fingerprint = _normalized_frame_fingerprint(frame, current_columns)
    exact_match = current_columns == evidence.columns and current_fingerprint == evidence.fingerprint
    # A timestamp is authoritative only when it was part of the imported filing
    # frame.  In particular, do not accept the old projection escape hatch that
    # allowed callers to drop ``rcept_dt_raw`` and append a timestamp after the
    # exact join had already been issued.
    if not exact_match:
        return False
    # The joined frame carries a private copy of the filing input.  Validate it
    # as well as the visible joined values so mutating embedded filing
    # metadata cannot leave the join token apparently usable.
    embedded_filings = frame.attrs.get("filing_metadata")
    if not isinstance(embedded_filings, pd.DataFrame):
        return False
    embedded_lineage = _lineage_valid(
        embedded_filings, embedded_filings.attrs.get(_DART_LINEAGE_ATTR), "filing_metadata",
    )
    if evidence.filings is not None:
        if not embedded_lineage or _filing_lineage(embedded_filings) != evidence.filings:
            return False
    elif embedded_lineage:
        # An unverified input may have no filing token; it must not acquire one
        # only because an embedded attrs object was later attached.
        return False
    return True


def _lineage_source_valid(lineage: Any) -> bool:
    if not isinstance(lineage, _DARTLineage):
        return False
    if (
        lineage.issuer is not _DART_ISSUER
        or not lineage.manifest_verified
        or not _valid_hash(lineage.raw_sha256)
        or not lineage.raw_source_path
        or not Path(lineage.raw_source_path).is_file()
        or not lineage.source_url
        or _manifest_endpoint_kind({"source_url": lineage.source_url}) != lineage.kind
    ):
        return False
    try:
        if sha256_file(lineage.raw_source_path) != lineage.raw_sha256:
            return False
        if lineage.manifest_path:
            manifest = _load_manifest(lineage.manifest_path)
            if (
                manifest is None
                or not _manifest_has_verified_contract(manifest, expected_kind=lineage.kind)
                or _manifest_hash(manifest) != lineage.raw_sha256
                or _manifest_digest(manifest) != lineage.manifest_digest
            ):
                return False
        return True
    except OSError:
        return False


def _fact_lineage(data: pd.DataFrame) -> _DARTLineage | None:
    direct = data.attrs.get(_DART_LINEAGE_ATTR)
    if _lineage_valid(data, direct, "financial_facts"):
        return direct
    joined = data.attrs.get(_DART_JOIN_EVIDENCE_ATTR)
    if isinstance(joined, _DARTJoinEvidence) and joined.issuer is _DART_ISSUER:
        if _join_evidence_valid(data, joined):
            return joined.facts
    evidence = data.attrs.get(_DART_VALIDATION_EVIDENCE_ATTR)
    if isinstance(evidence, _DARTValidationEvidence) and evidence.issuer is _DART_ISSUER:
        if _lineage_valid(data, evidence, "prepared_facts"):
            return evidence.facts
    return None


def _infer_period_end_from_report(bsns_year: Any, reprt_code: Any) -> date | None:
    year_text = _text(bsns_year)
    report_code = _text(reprt_code)
    if year_text is None or report_code is None:
        return None
    try:
        year = int(year_text)
    except ValueError:
        return None
    month_day = {
        "11013": (3, 31),
        "11012": (6, 30),
        "11014": (9, 30),
        "11011": (12, 31),
    }.get(report_code)
    if month_day is None:
        return None
    try:
        return date(year, month_day[0], month_day[1])
    except ValueError:
        return None


def _filing_lineage(data: pd.DataFrame) -> _DARTLineage | None:
    direct = data.attrs.get(_DART_LINEAGE_ATTR)
    if _lineage_valid(data, direct, "filing_metadata"):
        return direct
    joined = data.attrs.get(_DART_JOIN_EVIDENCE_ATTR)
    if isinstance(joined, _DARTJoinEvidence) and joined.issuer is _DART_ISSUER:
        if _join_evidence_valid(data, joined):
            return joined.filings
    evidence = data.attrs.get(_DART_VALIDATION_EVIDENCE_ATTR)
    if isinstance(evidence, _DARTValidationEvidence) and evidence.issuer is _DART_ISSUER:
        if _lineage_valid(data, evidence, "prepared_facts"):
            return evidence.filings
    return None


def load_filing_metadata(
    source: str | Path | bytes | bytearray | pd.DataFrame,
    *,
    column_mapping: Mapping[str, str] | None = None,
    source_format: str | None = None,
    manifest: Mapping[str, Any] | str | Path | None = None,
    acquisition_manifest: Mapping[str, Any] | str | Path | None = None,
) -> pd.DataFrame:
    """Load and normalize local OpenDART filing-list response rows.

    No manifest is an allowed *candidate* state, but it cannot pass
    :func:`validate_dart_pit`.  Supplying a manifest verifies the raw bytes
    before its hash is copied into the normalized rows.
    """
    if manifest is not None and acquisition_manifest is not None:
        raise DARTPITError("provide only one of manifest and acquisition_manifest")
    local = _read_local_source(source, source_format=source_format)
    manifest_value, verified, verified_hash = _verified_manifest(
        local, manifest if manifest is not None else acquisition_manifest,
        expected_kind="filing_metadata",
    )
    resolved = _resolve_columns(local.frame, _FILING_ALIASES, column_mapping)
    required = ("corp_code", "rcept_no", "rcept_dt_raw")
    missing = [field for field in required if field not in resolved]
    if missing:
        raise DARTPITError(f"filing metadata is missing required columns: {', '.join(missing)}")
    context = _source_context(local, manifest_value, verified_hash, verified)
    rows: list[dict[str, Any]] = []
    for _, input_row in local.frame.iterrows():
        raw_date, parsed_date = _strict_rcept_date(input_row[resolved["rcept_dt_raw"]])
        report_name = input_row[resolved["report_nm"]] if "report_nm" in resolved else None
        remarks = input_row[resolved["rm"]] if "rm" in resolved else None
        amendment = (
            _status_value(input_row[resolved["is_amendment"]], "is_amendment")
            if "is_amendment" in resolved else None
        )
        withdrawn = (
            _status_value(input_row[resolved["is_withdrawn"]], "is_withdrawn")
            if "is_withdrawn" in resolved else None
        )
        row = {
            "corp_code": _text(input_row[resolved["corp_code"]]),
            "stock_code": _text(input_row[resolved["stock_code"]]) if "stock_code" in resolved else None,
            "corp_name": _text(input_row[resolved["corp_name"]]) if "corp_name" in resolved else None,
            "rcept_no": _text(input_row[resolved["rcept_no"]]),
            "rcept_dt_raw": raw_date,
            "rcept_date": parsed_date,
            "report_nm": _text(report_name),
            "pblntf_ty": _text(input_row[resolved["pblntf_ty"]]) if "pblntf_ty" in resolved else None,
            "pblntf_detail_ty": (
                _text(input_row[resolved["pblntf_detail_ty"]])
                if "pblntf_detail_ty" in resolved else None
            ),
            "rm": _text(remarks),
            "is_amendment": _derive_amendment(report_name, remarks) if amendment is None else amendment,
            "is_withdrawn": _derive_withdrawn(remarks, report_name) if withdrawn is None else withdrawn,
            # Source authority belongs to the acquisition manifest, never to a
            # field copied from the response body.
            "source": context["source"],
            # The raw response itself cannot attest to its acquisition path;
            # use the path of the bytes actually read by this loader.
            "raw_payload_path": context["raw_payload_path"],
            "response_sha256": (
                verified_hash if verified_hash is not None else None
            ),
            "retrieved_at_utc": (
                context["retrieved_at_utc"]
            ),
        }
        rows.append(row)
    result = pd.DataFrame(rows, columns=FILING_METADATA_COLUMNS)
    # A timestamp is not part of the official filing-list schema, but a local
    # enrichment may provide one.  Preserve it only as an explicit optional
    # column so the cutoff policy can reject naive values rather than silently
    # falling back to it.
    for optional in _DART_TIMESTAMP_FIELDS:
        source_column = next(
            (column for column in local.frame.columns if _normal_label(column) == _normal_label(optional)),
            None,
        )
        if source_column is not None:
            result[optional] = local.frame[source_column].tolist()
    result = result.sort_values(["corp_code", "rcept_no"], kind="mergesort").reset_index(drop=True)
    lineage = _issue_lineage(
        result,
        kind="filing_metadata",
        raw_sha256=verified_hash,
        manifest=manifest_value,
        manifest_verified=verified,
        raw_source_path=str(local.path.resolve()) if local.path is not None else None,
        manifest_path=(
            str(Path(manifest if manifest is not None else acquisition_manifest).resolve())
            if isinstance(manifest if manifest is not None else acquisition_manifest, (str, Path))
            else None
        ),
    )
    result.attrs.update({
        "dart_kind": "filing_metadata",
        "raw_hash_verified": verified,
        "manifest_verified": verified,
        "response_sha256": verified_hash,
        "response_manifest": dict(manifest_value or {}),
        "raw_source_path": str(local.path.resolve()) if local.path is not None else None,
        "source_format": local.source_format,
        _DART_LINEAGE_ATTR: lineage,
        "dart_normalized_frame_sha256": lineage.fingerprint,
        "normalized_fingerprint": lineage.fingerprint,
    })
    return result


def _numeric_value(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    if text in {"", "-", "—", "nan", "none", "null"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def load_financial_facts(
    source: str | Path | bytes | bytearray | pd.DataFrame,
    *,
    column_mapping: Mapping[str, str] | None = None,
    source_format: str | None = None,
    manifest: Mapping[str, Any] | str | Path | None = None,
    acquisition_manifest: Mapping[str, Any] | str | Path | None = None,
) -> pd.DataFrame:
    """Load and normalize local OpenDART account-fact response rows."""
    if manifest is not None and acquisition_manifest is not None:
        raise DARTPITError("provide only one of manifest and acquisition_manifest")
    local = _read_local_source(source, source_format=source_format)
    manifest_value, verified, verified_hash = _verified_manifest(
        local, manifest if manifest is not None else acquisition_manifest,
        expected_kind="financial_facts",
    )
    working_frame = local.frame.copy()
    _reject_fact_provenance_collisions(working_frame)
    resolved = _resolve_columns(working_frame, _FACT_ALIASES, column_mapping)
    request_params = manifest_value.get("request_params", {}) if isinstance(manifest_value, Mapping) else {}
    fallback_fields = {
        "bsns_year": "bsns_year",
        "reprt_code": "reprt_code",
        "fs_div": "fs_div",
    }
    if isinstance(request_params, Mapping):
        for field_name, param_name in fallback_fields.items():
            if field_name in resolved:
                continue
            fallback_value = _text(request_params.get(param_name))
            if fallback_value is None:
                continue
            fallback_column = f"_manifest_{field_name}"
            if fallback_column not in working_frame.columns:
                working_frame[fallback_column] = fallback_value
            resolved[field_name] = fallback_column
    required = ("rcept_no", "corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div")
    missing = [field for field in required if field not in resolved]
    if missing:
        raise DARTPITError(f"financial facts are missing required columns: {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for _, input_row in working_frame.iterrows():
        bsns_year = _text(input_row[resolved["bsns_year"]])
        reprt_code = _text(input_row[resolved["reprt_code"]])
        parsed_period_end = (
            _parse_date(input_row[resolved["period_end"]])
            if "period_end" in resolved else None
        )
        if parsed_period_end is None:
            parsed_period_end = _infer_period_end_from_report(bsns_year, reprt_code)
        raw_value = input_row[resolved["raw_value"]] if "raw_value" in resolved else None
        numeric = (
            _numeric_value(input_row[resolved["numeric_value"]])
            if "numeric_value" in resolved else _numeric_value(raw_value)
        )
        row = {
            "rcept_no": _text(input_row[resolved["rcept_no"]]),
            "corp_code": _text(input_row[resolved["corp_code"]]),
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": _text(input_row[resolved["fs_div"]]),
            "sj_div": _text(input_row[resolved["sj_div"]]),
            "account_id": _text(input_row[resolved["account_id"]]) if "account_id" in resolved else None,
            "account_name": (
                _text(input_row[resolved["account_name"]])
                if "account_name" in resolved else None
            ),
            "account_detail": (
                _text(input_row[resolved["account_detail"]])
                if "account_detail" in resolved else None
            ),
            "period_end": parsed_period_end,
            "raw_value": _text(raw_value),
            "numeric_value": numeric,
            "currency": _text(input_row[resolved["currency"]]) if "currency" in resolved else None,
            "payload_sha256": (
                verified_hash
                if verified_hash is not None
                else (_text(input_row[resolved["payload_sha256"]])
                      if "payload_sha256" in resolved else None)
            ),
        }
        rows.append(row)
    result = pd.DataFrame(rows, columns=FINANCIAL_FACT_COLUMNS)
    sort_columns = [field for field in ("corp_code", "rcept_no", "account_id", "period_end")
                    if field in result.columns]
    result = result.sort_values(sort_columns, kind="mergesort", na_position="last").reset_index(drop=True)
    lineage = _issue_lineage(
        result,
        kind="financial_facts",
        raw_sha256=verified_hash,
        manifest=manifest_value,
        manifest_verified=verified,
        raw_source_path=str(local.path.resolve()) if local.path is not None else None,
        manifest_path=(
            str(Path(manifest if manifest is not None else acquisition_manifest).resolve())
            if isinstance(manifest if manifest is not None else acquisition_manifest, (str, Path))
            else None
        ),
    )
    result.attrs.update({
        "dart_kind": "financial_facts",
        "raw_hash_verified": verified,
        "manifest_verified": verified,
        "payload_sha256": verified_hash,
        "response_manifest": dict(manifest_value or {}),
        "raw_source_path": str(local.path.resolve()) if local.path is not None else None,
        "source_format": local.source_format,
        _DART_LINEAGE_ATTR: lineage,
        "dart_normalized_frame_sha256": lineage.fingerprint,
        "normalized_fingerprint": lineage.fingerprint,
    })
    return result


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _require_normalized_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> list[str]:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DARTPITError(f"{label} is missing canonical columns: {', '.join(missing)}")
    return missing


def _join_errors(facts: pd.DataFrame, filings: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    for label, frame in (("financial facts", facts), ("filing metadata", filings)):
        for key in ("corp_code", "rcept_no"):
            if key not in frame.columns:
                errors.append(f"{label} is missing join key {key!r}")
            elif frame[key].isna().any() or frame[key].astype(str).str.strip().eq("").any():
                errors.append(f"{label} has missing {key} values")
    if all(key in filings.columns for key in ("corp_code", "rcept_no")):
        duplicate = filings.duplicated(["corp_code", "rcept_no"], keep=False)
        if duplicate.any():
            errors.append("filing metadata has ambiguous duplicate (corp_code, rcept_no) joins")
    fact_identity = [
        "corp_code", "rcept_no", "bsns_year", "reprt_code", "fs_div", "sj_div",
        "account_id", "account_detail", "period_end",
    ]
    if all(column in facts.columns for column in fact_identity):
        duplicate_facts = facts.duplicated(fact_identity, keep=False)
        if duplicate_facts.any():
            errors.append(
                "financial facts have ambiguous duplicate identities within a filing "
                "(account_detail and rcept_no are part of the key)"
            )
    if all(key in facts.columns for key in ("corp_code", "rcept_no")) and all(
        key in filings.columns for key in ("corp_code", "rcept_no")
    ):
        fact_keys = pd.MultiIndex.from_frame(facts[["corp_code", "rcept_no"]])
        filing_keys = pd.MultiIndex.from_frame(filings[["corp_code", "rcept_no"]])
        missing = ~fact_keys.isin(filing_keys)
        if missing.any():
            errors.append("financial facts contain missing filing (corp_code, rcept_no) joins")
    return errors


def join_financial_facts_to_filings(
    facts: pd.DataFrame,
    filings: pd.DataFrame,
) -> pd.DataFrame:
    """Join facts to exactly one filing using ``(corp_code, rcept_no)``.

    Fiscal fields are deliberately absent from the merge key.  Missing or
    ambiguous keys raise rather than being dropped or matched by period.
    """
    if not isinstance(facts, pd.DataFrame) or not isinstance(filings, pd.DataFrame):
        raise TypeError("facts and filings must be pandas DataFrames")
    _reject_fact_provenance_collisions(facts)
    errors = _join_errors(facts, filings)
    if errors:
        raise DARTPITError("; ".join(errors))
    optional_timestamp_columns = [
        column for column in _DART_TIMESTAMP_FIELDS
        if column in filings.columns and column not in facts.columns
    ]
    metadata_columns = [column for column in (*FILING_METADATA_COLUMNS, *optional_timestamp_columns)
                        if column not in {"corp_code", "rcept_no"} and column not in facts.columns]
    metadata = filings[["corp_code", "rcept_no", *metadata_columns]].copy()
    joined = facts.merge(
        metadata,
        on=["corp_code", "rcept_no"],
        how="left",
        validate="many_to_one",
        sort=False,
    )
    filing_metadata_copy = filings.copy(deep=True)
    filing_metadata_copy.attrs = dict(filings.attrs)
    joined.attrs = dict(facts.attrs)
    joined.attrs.update({
        "dart_join_valid": True,
        "dart_join_key": "(corp_code, rcept_no)",
        "dart_fiscal_period_join_used": False,
        "filing_metadata_hash_verified": bool(filings.attrs.get("raw_hash_verified", False)),
        "filing_metadata_manifest_verified": bool(filings.attrs.get("manifest_verified", False)),
        "filing_metadata": filing_metadata_copy,
    })
    fact_lineage = _fact_lineage(facts)
    filing_lineage = _filing_lineage(filings)
    joined_columns = tuple(str(column) for column in joined.columns)
    join_evidence = _DARTJoinEvidence(
        issuer=_DART_ISSUER,
        fingerprint=_normalized_frame_fingerprint(joined, joined_columns),
        columns=joined_columns,
        facts=fact_lineage,
        filings=filing_lineage,
        without_rcept_dt_raw_fingerprint=_join_projection_fingerprint(joined),
    )
    joined.attrs.update({
        _DART_FACT_LINEAGE_ATTR: fact_lineage,
        _DART_FILING_LINEAGE_ATTR: filing_lineage,
        _DART_JOIN_EVIDENCE_ATTR: join_evidence,
        "dart_normalized_frame_sha256": join_evidence.fingerprint,
        "normalized_fingerprint": join_evidence.fingerprint,
    })
    return joined


def _normalize_policy(policy: Any) -> tuple[str | None, str | None, time | None]:
    timezone_name: str | None = None
    cutoff: Any = None
    if isinstance(policy, Mapping):
        name = policy.get("name", policy.get("policy", policy.get("type")))
        timezone_name = policy.get("timezone", policy.get("source_timezone"))
        cutoff = policy.get("cutoff_time", policy.get("cutoff", policy.get("time")))
    else:
        name = policy
    normalized = str(name).strip().casefold() if name is not None else NEXT_SESSION_POLICY
    normalized = {
        "next-session": NEXT_SESSION_POLICY,
        "conservative_next_session": NEXT_SESSION_POLICY,
        "cutoff": EXPLICIT_TIMESTAMP_POLICY,
        "timestamp_cutoff": EXPLICIT_TIMESTAMP_POLICY,
        "close_cutoff": EXPLICIT_TIMESTAMP_POLICY,
    }.get(normalized, normalized)
    parsed_cutoff: time | None = None
    if isinstance(cutoff, time):
        parsed_cutoff = cutoff.replace(tzinfo=None)
    elif isinstance(cutoff, str):
        try:
            parsed_cutoff = time.fromisoformat(cutoff.strip()).replace(tzinfo=None)
        except ValueError:
            parsed_cutoff = None
    if timezone_name is not None:
        timezone_name = str(timezone_name).strip() or None
        timezone_name = _normalize_official_timezone(timezone_name) or timezone_name
    return normalized, timezone_name, parsed_cutoff


def _true_values(values: pd.Series) -> pd.Series:
    """Interpret status columns without treating the string ``"False"`` as true."""
    return values.map(lambda value: _bool_value(value) is True)


def _timestamp_field(frame: pd.DataFrame) -> str | None:
    fields = [field for field in _DART_TIMESTAMP_FIELDS if field in frame.columns]
    if len(fields) > 1:
        raise DARTPITError(
            "multiple filing timestamp candidate columns are present; refusing to choose one"
        )
    return fields[0] if fields else None


def _timestamp_is_unambiguous(value: Any) -> bool:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return False
    if pd.isna(parsed) or parsed.tzinfo is None:
        return False
    try:
        python_value = parsed.to_pydatetime()
        offsets = {python_value.replace(fold=fold).utcoffset() for fold in (0, 1)}
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
    return len(offsets) <= 1


def _timestamp_has_intraday_time(value: Any) -> bool:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return False
    if pd.isna(parsed) or parsed.tzinfo is None:
        return False
    return any((parsed.hour, parsed.minute, parsed.second, parsed.microsecond, parsed.nanosecond))


def _verified_raw_timestamp_lineage(frame: pd.DataFrame, field: str) -> bool:
    """Require a timestamp to be attested by the imported raw filing frame."""
    evidence = frame.attrs.get(_DART_JOIN_EVIDENCE_ATTR)
    if not isinstance(evidence, _DARTJoinEvidence) or evidence.issuer is not _DART_ISSUER:
        return False
    filing_lineage = evidence.filings
    embedded_filings = frame.attrs.get("filing_metadata")
    if not isinstance(embedded_filings, pd.DataFrame):
        return False
    if not _lineage_valid(
        embedded_filings,
        embedded_filings.attrs.get(_DART_LINEAGE_ATTR),
        "filing_metadata",
    ):
        return False
    if not isinstance(filing_lineage, _DARTLineage) or not _lineage_source_valid(filing_lineage):
        return False
    if field not in filing_lineage.columns:
        return False
    manifest = embedded_filings.attrs.get("response_manifest")
    if not isinstance(manifest, Mapping):
        if filing_lineage.manifest_path:
            try:
                manifest = _load_manifest(filing_lineage.manifest_path)
            except DARTPITError:
                return False
    if not isinstance(manifest, Mapping) or not _manifest_has_verified_contract(
        manifest, expected_kind="filing_metadata",
    ):
        return False
    if _manifest_digest(manifest) != filing_lineage.manifest_digest:
        return False
    if field not in _manifest_timestamp_fields(manifest):
        return False
    declared_fingerprint = manifest.get(
        "normalized_frame_fingerprint",
        manifest.get("normalized_fingerprint", manifest.get("normalized_frame_sha256")),
    )
    return declared_fingerprint is None or declared_fingerprint == filing_lineage.fingerprint


def _valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _normalize_official_timezone(name: str | None) -> str | None:
    """Normalize only timezone names equivalent to the official DART zone."""
    if not isinstance(name, str) or not name.strip():
        return None
    value = name.strip()
    aliases = {
        OFFICIAL_FILING_TIMEZONE.casefold(),
        "rok",
        "etc/gmt-9",
    }
    if value.casefold() in aliases:
        return OFFICIAL_FILING_TIMEZONE
    return OFFICIAL_FILING_TIMEZONE if value == OFFICIAL_FILING_TIMEZONE else None


def _session_dates(trading_dates: Sequence[Any] | pd.DatetimeIndex) -> pd.DatetimeIndex:
    local_dates: list[pd.Timestamp] = []
    for value in trading_dates:
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if pd.isna(parsed):
            continue
        if parsed.tzinfo is not None:
            try:
                parsed = parsed.tz_convert(OFFICIAL_FILING_TIMEZONE).tz_localize(None)
            except (TypeError, ValueError, OverflowError):
                continue
        local_dates.append(pd.Timestamp(parsed.date()))
    return pd.DatetimeIndex(local_dates).unique().sort_values()


def _map_one_session(
    row: Mapping[str, Any],
    sessions: pd.DatetimeIndex,
    policy: str,
    timezone_name: str | None,
    cutoff: time | None,
    timestamp_field: str | None,
) -> pd.Timestamp | None:
    if policy == NEXT_SESSION_POLICY:
        filing_date = row.get("rcept_date")
        if not isinstance(filing_date, date):
            filing_date = _parse_date(filing_date)
        if filing_date is None:
            return None
        eligible = sessions[sessions > pd.Timestamp(filing_date)]
        return pd.Timestamp(eligible[0]).normalize() if len(eligible) else None
    if policy != EXPLICIT_TIMESTAMP_POLICY or timestamp_field is None:
        return None
    if not timezone_name or not _valid_timezone(timezone_name) or cutoff is None:
        return None
    value = row.get(timestamp_field)
    if not _timestamp_is_unambiguous(value) or not _timestamp_has_intraday_time(value):
        return None
    parsed = pd.Timestamp(value)
    try:
        local = parsed.tz_convert(timezone_name)
    except (TypeError, ValueError, OverflowError):
        return None
    local_date = pd.Timestamp(local.date())
    local_clock = local.timetz().replace(tzinfo=None)
    if local_date in sessions and local_clock <= cutoff:
        eligible = sessions[sessions >= local_date]
    else:
        eligible = sessions[sessions > local_date]
    return pd.Timestamp(eligible[0]).normalize() if len(eligible) else None


def _availability_lineage_errors(
    frame: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    policy: str,
    timezone_name: str | None,
    cutoff: time | None,
    timestamp_field: str | None,
) -> list[str]:
    """Check that stored availability is derived from the authoritative filing."""
    errors: list[str] = []
    for row in frame.to_dict(orient="records"):
        filing_date = _parse_date(row.get("rcept_date"))
        if filing_date is None:
            errors.append("parsed rcept_date is missing")
            continue
        if _parse_date(row.get("filing_date")) != filing_date:
            errors.append("filing_date does not match parsed rcept_date")
        expected = _map_one_session(
            row, sessions, policy, timezone_name, cutoff, timestamp_field,
        )
        try:
            actual = pd.Timestamp(row.get("availability_session"))
        except (TypeError, ValueError, OverflowError):
            actual = pd.NaT
        if pd.isna(actual) or expected is None or actual.normalize() != expected:
            errors.append("availability_session does not match filing availability lineage")
    return list(dict.fromkeys(errors))


def _unmapped_availability_message(
    frame: pd.DataFrame,
    availability: Sequence[pd.Timestamp | None],
    sessions: pd.DatetimeIndex,
    *,
    max_examples: int = 3,
) -> str:
    """Return an actionable failure message for unmapped filing availability."""
    failed_indices = [index for index, value in enumerate(availability) if value is None]
    total = len(failed_indices)
    session_start = pd.Timestamp(sessions.min()).date().isoformat()
    session_end = pd.Timestamp(sessions.max()).date().isoformat()
    examples: list[str] = []
    for index in failed_indices[:max_examples]:
        row = frame.iloc[index]
        corp_code = str(row.get("corp_code", ""))
        rcept_no = str(row.get("rcept_no", ""))
        rcept_date = _parse_date(row.get("rcept_date"))
        date_text = rcept_date.isoformat() if rcept_date is not None else "missing"
        examples.append(
            f"corp_code={corp_code}, rcept_no={rcept_no}, rcept_date={date_text}"
        )
    suffix = "; ".join(examples) if examples else "no row details"
    return (
        "one or more filings cannot be mapped to a provided KRX session "
        f"(unmapped={total}, session_range={session_start}..{session_end}, "
        f"examples: {suffix})"
    )


def _drop_future_unmappable_rows(
    frame: pd.DataFrame,
    availability: Sequence[pd.Timestamp | None],
    sessions: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, list[pd.Timestamp | None], int]:
    """Drop only rows whose filing date is strictly after the session range."""
    if not len(sessions):
        return frame, list(availability), 0
    session_max = pd.Timestamp(sessions.max()).date()
    keep_indices: list[int] = []
    dropped = 0
    for index, value in enumerate(availability):
        if value is not None:
            keep_indices.append(index)
            continue
        filing_date = _parse_date(frame.iloc[index].get("rcept_date"))
        if filing_date is not None and filing_date > session_max:
            dropped += 1
            continue
        keep_indices.append(index)
    if dropped == 0:
        return frame, list(availability), 0
    trimmed = frame.iloc[keep_indices].copy(deep=True).reset_index(drop=True)
    trimmed_availability = [availability[index] for index in keep_indices]
    return trimmed, trimmed_availability, dropped


def map_filing_availability(
    joined_facts: pd.DataFrame,
    trading_dates: Sequence[Any] | pd.DatetimeIndex,
    *,
    availability_policy: str | Mapping[str, Any] = NEXT_SESSION_POLICY,
    amendment_policy: str | None = None,
) -> pd.DataFrame:
    """Map each joined filing to a safe KRX session.

    OpenDART ``rcept_dt`` is date-only, so the supported path is strictly the
    first provided trading date after that date in the Asia/Seoul local
    calendar.  Same-day availability is never inferred.  A session-cutoff
    policy is deferred unless a timestamp was present in a verified raw filing
    manifest and in the importer-issued normalized-frame fingerprint; a column
    appended after the exact join is never accepted.
    """
    if not isinstance(joined_facts, pd.DataFrame):
        raise TypeError("joined_facts must be a pandas DataFrame")
    join_evidence = joined_facts.attrs.get(_DART_JOIN_EVIDENCE_ATTR)
    if not _join_evidence_valid(joined_facts, join_evidence):
        raise DARTPITError(
            "joined facts lack importer-issued exact-join evidence or the evidence is stale"
        )
    if amendment_policy not in AMENDMENT_POLICIES:
        raise DARTPITError(
            "an explicit amendment_policy is required: latest_filing_available_as_of or first_filing"
        )
    policy, timezone_name, cutoff = _normalize_policy(availability_policy)
    sessions = _session_dates(trading_dates)
    if not len(sessions):
        raise DARTPITError("KRX trading dates are empty")
    if policy not in {NEXT_SESSION_POLICY, EXPLICIT_TIMESTAMP_POLICY}:
        raise DARTPITError(f"unsupported availability policy: {policy!r}")
    timestamp_field = _timestamp_field(joined_facts)
    if "rcept_dt_raw" in joined_facts.columns and policy != NEXT_SESSION_POLICY:
        raise DARTPITError("official date-only rcept_dt requires the conservative next-session policy")
    if policy == EXPLICIT_TIMESTAMP_POLICY:
        if "rcept_dt_raw" in joined_facts.columns:
            raise DARTPITError(
                "official date-only rcept_dt requires the conservative next-session policy"
            )
        normalized_timezone = _normalize_official_timezone(timezone_name)
        if normalized_timezone is None:
            raise DARTPITError(
                "official DART filing timestamp timezone must be Asia/Seoul"
            )
        timezone_name = normalized_timezone
        if timestamp_field is None or not _valid_timezone(timezone_name) or cutoff is None:
            raise DARTPITError(
                "explicit timestamp availability requires a timezone-aware timestamp and declared cutoff"
            )
        if not _verified_raw_timestamp_lineage(joined_facts, timestamp_field):
            raise DARTPITError(
                "session_cutoff requires verified raw filing timestamp lineage in the manifest"
            )
        if not all(
            _timestamp_is_unambiguous(value)
            and _timestamp_has_intraday_time(value)
            for value in joined_facts[timestamp_field].tolist()
        ):
            raise DARTPITError(
                "explicit timestamp availability requires timezone-aware, unambiguous, non-midnight values"
            )
    for column in _DART_STATUS_VALUES:
        if column in joined_facts.columns and any(
            _bool_value(value) is None for value in joined_facts[column]
        ):
            raise DARTPITError(f"unknown explicit {column} value")
    if "is_withdrawn" in joined_facts.columns and _true_values(joined_facts["is_withdrawn"]).any():
        raise DARTPITError("withdrawn DART filings are not eligible for PIT facts")
    output = joined_facts.copy(deep=True)
    availability: list[pd.Timestamp | None] = []
    for row in output.to_dict(orient="records"):
        availability.append(_map_one_session(
            row, sessions, policy, timezone_name, cutoff, timestamp_field,
        ))
    output, availability, dropped_future = _drop_future_unmappable_rows(
        output,
        availability,
        sessions,
    )
    output["availability_session"] = availability
    output["filing_date"] = output["rcept_date"]
    if not all(value is not None for value in availability):
        raise DARTPITError(_unmapped_availability_message(output, availability, sessions))
    mapping_errors = _availability_lineage_errors(
        output, sessions, policy, timezone_name, cutoff, timestamp_field,
    )
    if mapping_errors:
        raise DARTPITError("; ".join(mapping_errors))
    output.attrs = dict(joined_facts.attrs)
    output.attrs.update({
        "dart_availability_valid": bool(all(value is not None for value in availability)),
        "dart_availability_policy": policy,
        "dart_filing_timezone": OFFICIAL_FILING_TIMEZONE,
        "dart_policy_timezone": timezone_name,
        "dart_policy_cutoff_time": cutoff.isoformat() if cutoff is not None else None,
        "dart_amendment_policy": amendment_policy,
        "dart_future_receipts_dropped": dropped_future,
        "krx_trading_dates": [pd.Timestamp(value).date().isoformat() for value in sessions],
    })
    resolved = _resolve_amendments(output, amendment_policy)
    join_evidence = resolved.attrs.get(_DART_JOIN_EVIDENCE_ATTR)
    if isinstance(join_evidence, _DARTJoinEvidence):
        columns = tuple(str(column) for column in resolved.columns)
        resolved.attrs[_DART_JOIN_EVIDENCE_ATTR] = _DARTJoinEvidence(
            issuer=_DART_ISSUER,
            fingerprint=_normalized_frame_fingerprint(resolved, columns),
            columns=columns,
            facts=join_evidence.facts,
            filings=join_evidence.filings,
            without_rcept_dt_raw_fingerprint=_join_projection_fingerprint(resolved),
        )
        resolved.attrs["dart_normalized_frame_sha256"] = resolved.attrs[
            _DART_JOIN_EVIDENCE_ATTR
        ].fingerprint
        resolved.attrs["normalized_fingerprint"] = resolved.attrs["dart_normalized_frame_sha256"]
    return resolved


def _amendment_group_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in (
        "corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
        "account_detail", "period_end",
    ) if column in frame.columns]


def _resolve_amendments(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    if policy not in AMENDMENT_POLICIES:
        raise DARTPITError("an explicit amendment policy is required")
    required_group = (
        "corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
        "account_detail", "period_end",
    )
    if not all(column in frame.columns for column in required_group):
        # A filing-only availability mapping has no financial fact identity on
        # which to resolve amendments.  Preserve every submission and retain
        # the explicit policy as evidence; financial preparation always has
        # the complete group and takes the resolution branch below.
        output = frame.copy(deep=True).reset_index(drop=True)
        output.attrs = dict(frame.attrs)
        output.attrs["dart_amendment_policy"] = policy
        output.attrs["dart_amendment_resolution_valid"] = True
        return output
    fact_identity = ["rcept_no", *required_group]
    if frame.duplicated(fact_identity, keep=False).any():
        raise DARTPITError(
            "financial facts have ambiguous duplicate identities within one receipt"
        )
    group = list(required_group)
    output = frame.copy(deep=True)
    sort_columns = [*group, "availability_session", "rcept_date", "rcept_no"]
    output = output.sort_values(sort_columns, kind="mergesort", na_position="last")
    if policy == "first_filing":
        output = output.drop_duplicates(group, keep="first")
    else:
        # Keep the historical state at each session.  If two submissions land
        # on one session, the later receipt number is the latest available one.
        output = output.drop_duplicates([*group, "availability_session"], keep="last")
    output = output.sort_values(["corp_code", "rcept_no", "account_id"], kind="mergesort")
    output = output.reset_index(drop=True)
    output.attrs = dict(frame.attrs)
    output.attrs["dart_amendment_policy"] = policy
    output.attrs["dart_amendment_resolution_valid"] = True
    return output


def _hash_evidence_valid(facts: pd.DataFrame, filings: pd.DataFrame | None) -> bool:
    fact_lineage = _fact_lineage(facts)
    filing_lineage = _filing_lineage(filings) if filings is not None else None
    if filing_lineage is None:
        embedded = facts.attrs.get("filing_metadata")
        if isinstance(embedded, pd.DataFrame):
            filing_lineage = _filing_lineage(embedded)
    if not _lineage_source_valid(fact_lineage) or not _lineage_source_valid(filing_lineage):
        return False
    if "payload_sha256" not in facts.columns or facts.empty:
        return False
    if not facts["payload_sha256"].map(_valid_hash).all():
        return False
    if facts["payload_sha256"].astype(str).str.lower().nunique() != 1:
        return False
    if facts["payload_sha256"].iloc[0].casefold() != fact_lineage.raw_sha256:
        return False
    if filings is not None:
        if "response_sha256" not in filings.columns or filings.empty:
            return False
        if not filings["response_sha256"].map(_valid_hash).all():
            return False
        if filings["response_sha256"].astype(str).str.lower().nunique() != 1:
            return False
        if filings["response_sha256"].iloc[0].casefold() != filing_lineage.raw_sha256:
            return False
    return True


def _prepared_evidence_valid(data: pd.DataFrame) -> _DARTValidationEvidence | None:
    evidence = data.attrs.get(_DART_VALIDATION_EVIDENCE_ATTR)
    if not isinstance(evidence, _DARTValidationEvidence) or evidence.issuer is not _DART_ISSUER:
        return None
    if (
        tuple(str(column) for column in data.columns) != evidence.columns
        or _normalized_frame_fingerprint(data, evidence.columns) != evidence.fingerprint
    ):
        return None
    join_evidence = data.attrs.get(_DART_JOIN_EVIDENCE_ATTR)
    if not _join_evidence_valid(data, join_evidence):
        return None
    if join_evidence.facts != evidence.facts or join_evidence.filings != evidence.filings:
        return None
    return evidence


def _metadata_evidence_errors(filings: pd.DataFrame | None) -> list[str]:
    if filings is None:
        return ["raw filing metadata is missing"]
    errors: list[str] = []
    lineage = _filing_lineage(filings)
    if not _lineage_valid(filings, lineage, "filing_metadata"):
        errors.append("filing metadata lacks importer-issued normalized fingerprint evidence")
    if not _lineage_source_valid(lineage):
        errors.append("filing metadata lacks a verified OpenDART acquisition manifest")
    for column in ("source", "raw_payload_path", "response_sha256", "retrieved_at_utc", "rcept_date"):
        if column not in filings.columns:
            errors.append(f"filing metadata is missing {column}")
    if "source" in filings.columns and filings["source"].isna().any():
        errors.append("filing metadata source is missing")
    if "raw_payload_path" in filings.columns and filings["raw_payload_path"].isna().any():
        errors.append("filing metadata raw payload path is missing")
    if "raw_payload_path" in filings.columns:
        for value in filings["raw_payload_path"]:
            if not isinstance(value, str) or not value or not Path(value).is_file():
                errors.append("filing metadata raw payload path is not a local file")
                break
    if "response_sha256" in filings.columns and not filings["response_sha256"].map(_valid_hash).all():
        errors.append("filing metadata response hashes are missing or invalid")
    if "retrieved_at_utc" in filings.columns:
        for value in filings["retrieved_at_utc"]:
            try:
                parsed = pd.Timestamp(value)
            except (TypeError, ValueError, OverflowError):
                parsed = pd.NaT
            if pd.isna(parsed) or parsed.tzinfo is None:
                errors.append("filing metadata retrieved_at_utc must be timezone-aware")
                break
    if "rcept_dt_raw" in filings.columns:
        invalid = [value for value in filings["rcept_dt_raw"]
                   if _strict_rcept_date(value)[1] is None]
        if invalid:
            errors.append("official rcept_dt must be date-only YYYYMMDD")
    if "rcept_date" in filings.columns and filings["rcept_date"].isna().any():
        errors.append("filing metadata contains invalid rcept_date values")
    if "rcept_dt_raw" in filings.columns and "rcept_date" in filings.columns:
        inconsistent = any(
            _strict_rcept_date(raw)[1] != parsed
            for raw, parsed in zip(filings["rcept_dt_raw"], filings["rcept_date"], strict=True)
        )
        if inconsistent:
            errors.append("filing metadata rcept_date does not match parsed rcept_dt_raw")
    for column in _DART_STATUS_VALUES:
        if column not in filings.columns:
            errors.append(f"filing metadata is missing {column}")
        elif any(_bool_value(value) is None for value in filings[column]):
            errors.append(f"filing metadata contains unknown {column} values")
    return list(dict.fromkeys(errors))


def _fact_integrity_errors(facts: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    required = ("rcept_no", "corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "period_end")
    for column in required:
        if column not in facts.columns:
            errors.append(f"financial facts are missing {column}")
        elif facts[column].isna().any() or facts[column].astype(str).str.strip().eq("").any():
            errors.append(f"financial facts contain missing {column} values")
    for column in ("is_amendment", "is_withdrawn"):
        if column in facts.columns and any(_bool_value(value) is None for value in facts[column]):
            errors.append(f"financial facts contain unknown {column} values")
    return list(dict.fromkeys(errors))


def _attach_financial_provenance(
    data: pd.DataFrame,
    report: DARTPITValidationResult,
    *,
    policy: str | None = None,
    timezone_name: str | None = None,
    cutoff: str | None = None,
    filing_field: str | None = None,
) -> pd.DataFrame:
    result = data.copy(deep=True)
    # pandas deep-copies arbitrary attrs.  Restore the issuer-issued objects
    # by identity; otherwise a legitimate prepared copy would look forged.
    attrs = dict(data.attrs)
    embedded_filings = attrs.get("filing_metadata")
    if isinstance(embedded_filings, pd.DataFrame):
        embedded_copy = embedded_filings.copy(deep=True)
        embedded_copy.attrs = dict(embedded_filings.attrs)
        attrs["filing_metadata"] = embedded_copy
    result.attrs = dict(attrs)
    attrs["financial_provenance"] = report.as_dict()
    attrs["dart_pit_validation"] = report.as_dict()
    if report.pit_valid:
        fact_lineage = _fact_lineage(result)
        filing_lineage = _filing_lineage(result)
        if fact_lineage is None or filing_lineage is None:
            # A valid report must never be able to attach a contract without
            # the opaque importer lineage that produced it.
            attrs.pop(FINANCIAL_PROVENANCE_CONTRACT_ATTR, None)
            attrs.pop(_DART_VALIDATION_EVIDENCE_ATTR, None)
            result.attrs = attrs
            return result
        field = filing_field or "filing_date"
        contract: dict[str, Any] = {
            "source": filing_lineage.source_url or "unverified_local_dart_response",
            "schema": {
                field: {
                    "type": "date",
                    "role": "filing availability date from rcept_dt",
                },
            },
            "availability_policy": policy or NEXT_SESSION_POLICY,
        }
        if timezone_name:
            contract["source_timezone"] = timezone_name
        if cutoff:
            contract["cutoff_time"] = cutoff
        attrs[FINANCIAL_PROVENANCE_CONTRACT_ATTR] = contract
        columns = tuple(str(column) for column in result.columns)
        attrs[_DART_VALIDATION_EVIDENCE_ATTR] = _DARTValidationEvidence(
            issuer=_DART_ISSUER,
            fingerprint=_normalized_frame_fingerprint(result, columns),
            columns=columns,
            facts=fact_lineage,
            filings=filing_lineage,
            availability_policy=policy or NEXT_SESSION_POLICY,
            timezone_name=timezone_name,
            cutoff=cutoff,
            amendment_policy=str(attrs.get("dart_amendment_policy", "")),
        )
    else:
        attrs.pop(FINANCIAL_PROVENANCE_CONTRACT_ATTR, None)
        attrs.pop(_DART_VALIDATION_EVIDENCE_ATTR, None)
    result.attrs = attrs
    return result


def validate_dart_pit(
    data: pd.DataFrame,
    filings: pd.DataFrame | None = None,
    trading_dates: Sequence[Any] | pd.DatetimeIndex | None = None,
    *,
    availability_policy: str | Mapping[str, Any] = NEXT_SESSION_POLICY,
    amendment_policy: str | None = None,
) -> DARTPITValidationResult:
    """Validate a prepared frame and report the existing financial mode.

    When ``filings`` and ``trading_dates`` are supplied, the function performs
    the exact-key join and session mapping first.  The caller can then use the
    returned frame from :func:`prepare_financial_facts` with the existing
    ``validate_financial_provenance`` validator; only a fully verified report
    receives the ``pit_filing_date`` contract.
    """
    errors: list[str] = []
    if not isinstance(data, pd.DataFrame):
        return DARTPITValidationResult(False, "non_pit_fiscal_period", ("data must be a DataFrame",))

    prepared = data
    joined_filings = filings
    validator_evidence = _prepared_evidence_valid(data)

    # A prepared frame may be revalidated without rejoining, but only when the
    # opaque evidence issued by this validator still matches every value.
    if filings is None:
        if validator_evidence is None:
            errors.append("prepared DART validation evidence is missing or was not issued by the validator")
        else:
            candidate_filings = data.attrs.get("filing_metadata")
            if isinstance(candidate_filings, pd.DataFrame):
                joined_filings = candidate_filings
                if _filing_lineage(candidate_filings) != validator_evidence.filings:
                    errors.append("prepared filing metadata does not match validator-issued lineage")
            else:
                errors.append("prepared filing metadata evidence is missing")
            if amendment_policy is None:
                amendment_policy = validator_evidence.amendment_policy
            stored_policy = validator_evidence.availability_policy
            availability_policy = {
                "name": stored_policy,
                "timezone": validator_evidence.timezone_name,
                "cutoff_time": validator_evidence.cutoff,
            }
    elif (
        _join_evidence_valid(data, data.attrs.get(_DART_JOIN_EVIDENCE_ATTR))
        and _filing_lineage(filings) is not None
        and _filing_lineage(filings) == _filing_lineage(data)
    ):
        # ``prepare_financial_facts`` already performed this exact join.  Do
        # not feed its joined frame back through the collision guard.
        prepared = data
    else:
        if trading_dates is None:
            errors.append("trading dates are required when filing metadata is supplied")
        else:
            try:
                prepared = map_filing_availability(
                    join_financial_facts_to_filings(data, filings),
                    trading_dates,
                    availability_policy=availability_policy,
                    amendment_policy=amendment_policy,
                )
            except (DARTPITError, KeyError, TypeError, ValueError) as exc:
                errors.append(str(exc))

    policy, timezone_name, cutoff = _normalize_policy(availability_policy)
    try:
        timestamp_field = _timestamp_field(prepared)
        if amendment_policy is None:
            if validator_evidence is not None:
                amendment_policy = validator_evidence.amendment_policy
            else:
                amendment_policy = prepared.attrs.get("dart_amendment_policy")
        if amendment_policy not in AMENDMENT_POLICIES:
            errors.append("an explicit amendment policy is missing or unsupported")
        if policy not in {NEXT_SESSION_POLICY, EXPLICIT_TIMESTAMP_POLICY}:
            errors.append("availability policy is missing or unsupported")
        if policy == EXPLICIT_TIMESTAMP_POLICY:
            if "rcept_dt_raw" in prepared.columns:
                errors.append("official date-only rcept_dt requires the conservative next-session policy")
            elif (
                timestamp_field is None
                or not timezone_name
                or not _valid_timezone(timezone_name)
                or cutoff is None
            ):
                errors.append("explicit timestamp policy lacks timezone/cutoff semantics")
            elif _normalize_official_timezone(timezone_name) is None:
                errors.append("official DART filing timestamp timezone must be Asia/Seoul")
            elif not all(
                _timestamp_is_unambiguous(value)
                and _timestamp_has_intraday_time(value)
                for value in prepared[timestamp_field].tolist()
            ):
                errors.append(
                    "explicit timestamp policy requires timezone-aware, unambiguous, non-midnight values"
                )
            elif not _verified_raw_timestamp_lineage(prepared, timestamp_field):
                errors.append(
                    "session_cutoff requires verified raw filing timestamp lineage in the manifest"
                )
        _require_normalized_columns(prepared, FINANCIAL_FACT_COLUMNS, "financial facts")
        _require_normalized_columns(
            prepared,
            ("corp_name", "rcept_date", "is_amendment", "is_withdrawn"),
            "joined facts",
        )
        if prepared.empty:
            errors.append("financial facts are empty")
        if "availability_session" not in prepared.columns:
            errors.append("availability sessions are missing")
        elif prepared["availability_session"].isna().any():
            errors.append("availability sessions are missing")
        if _true_values(prepared["is_withdrawn"]).any():
            errors.append("withdrawn filings are present")
        if not _join_evidence_valid(prepared, prepared.attrs.get(_DART_JOIN_EVIDENCE_ATTR)):
            errors.append("exact (corp_code, rcept_no) join evidence is missing or stale")
        if prepared.attrs.get("dart_fiscal_period_join_used", True) is not False:
            errors.append("financial facts were not joined exclusively by (corp_code, rcept_no)")
        if not bool(prepared.attrs.get("dart_availability_valid", False)):
            errors.append("availability session mapping is not valid")
        if not bool(prepared.attrs.get("dart_amendment_resolution_valid", False)):
            errors.append("amendment policy was not applied")
        stored_sessions = prepared.attrs.get("krx_trading_dates")
        if isinstance(stored_sessions, list):
            errors.extend(
                _availability_lineage_errors(
                    prepared,
                    _session_dates(stored_sessions),
                    policy,
                    timezone_name,
                    cutoff,
                    timestamp_field,
                )
            )
        else:
            errors.append("verified KRX trading-date lineage is missing")
        errors.extend(_fact_integrity_errors(prepared))
        if not _hash_evidence_valid(prepared, joined_filings):
            errors.append("raw response hashes and sidecar manifest verification are missing or invalid")
        if joined_filings is not None and all(
            column in prepared.columns for column in ("corp_code", "rcept_no")
        ):
            errors.extend(
                _join_errors(prepared[["corp_code", "rcept_no"]], joined_filings)
            )
    except (DARTPITError, KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    errors.extend(_metadata_evidence_errors(joined_filings))
    errors = list(dict.fromkeys(error for error in errors if error))
    valid = not errors
    report = DARTPITValidationResult(
        pit_valid=valid,
        mode="pit_filing_date" if valid else "non_pit_fiscal_period",
        errors=tuple(errors),
        diagnostics={
            "availability_policy": policy,
            "amendment_policy": amendment_policy,
            "source_hashes_verified": _hash_evidence_valid(prepared, joined_filings),
            "join_key": "(corp_code, rcept_no)",
        },
        provenance="pit_filing_date" if valid else "invalid",
    )
    target = prepared if filings is not None else data
    attached = _attach_financial_provenance(
        target,
        report,
        policy=policy if valid else None,
        timezone_name=timezone_name if valid else None,
        cutoff=cutoff.isoformat() if valid and cutoff is not None else None,
        filing_field=(
            _timestamp_field(prepared)
            if valid and policy == EXPLICIT_TIMESTAMP_POLICY else "filing_date"
        ),
    )
    target.attrs = dict(attached.attrs)
    return report


def prepare_financial_facts(
    facts: pd.DataFrame,
    filings: pd.DataFrame,
    trading_dates: Sequence[Any] | pd.DatetimeIndex,
    *,
    availability_policy: str | Mapping[str, Any] = NEXT_SESSION_POLICY,
    amendment_policy: str | None = None,
) -> pd.DataFrame:
    """Join, map, resolve amendments, and attach the financial PIT contract."""
    joined = join_financial_facts_to_filings(facts, filings)
    prepared = map_filing_availability(
        joined,
        trading_dates,
        availability_policy=availability_policy,
        amendment_policy=amendment_policy,
    )
    report = validate_dart_pit(
        prepared,
        filings=filings,
        trading_dates=trading_dates,
        availability_policy=availability_policy,
        amendment_policy=amendment_policy,
    )
    return _attach_financial_provenance(
        prepared,
        report,
        policy=_normalize_policy(availability_policy)[0] if report.pit_valid else None,
        timezone_name=_normalize_policy(availability_policy)[1] if report.pit_valid else None,
        cutoff=(
            _normalize_policy(availability_policy)[2].isoformat()
            if report.pit_valid and _normalize_policy(availability_policy)[2] is not None else None
        ),
        filing_field=(
            _timestamp_field(prepared)
            if report.pit_valid and _normalize_policy(availability_policy)[0] == EXPLICIT_TIMESTAMP_POLICY
            else "filing_date"
        ),
    )


def pivot_financial_facts_to_wide(
    prepared: pd.DataFrame,
    *,
    account_mapping: Mapping[str, Sequence[str]] | None = None,
) -> pd.DataFrame:
    """Convert long-format prepared DART facts into the wide quality-factor schema.

    ``prepare_financial_facts`` returns one row per ``(rcept_no, account_id)``
    fact.  The quality factor and ``_convert_financial_to_daily`` expect one
    row per report with the six semantic financial columns
    (``revenue``, ``cogs``, ``net_income``, ``operating_cf``,
    ``total_assets``, ``total_equity``) resolved from account ids/names.

    The output keeps the PIT contract attached by ``prepare_financial_facts``
    (``financial_provenance_contract``, filing-date field, availability
    session) so downstream validation and daily conversion remain PIT valid.

    Parameters
    ----------
    prepared : pd.DataFrame
        ``prepare_financial_facts`` output (long format).  Requires
        ``stock_code``, ``filing_date`` (or a supported timestamp field) and
        the fact columns.
    account_mapping : dict, optional
        Override for the account id/name -> wide column mapping.

    Returns
    -------
    pd.DataFrame
        One row per report: ``ticker``, the availability date field, and the
        six wide financial columns.
    """
    if prepared.empty:
        return pd.DataFrame()

    mapping = dict(ACCOUNT_COLUMN_MAPPING if account_mapping is None else account_mapping)

    stock_code_field = "stock_code" if "stock_code" in prepared.columns else "corp_code"
    filing_field = find_filing_date_field(prepared)
    if filing_field is None:
        raise DARTPITError("wide pivot requires a validated filing availability field")

    # Group facts by report so each output row aggregates one filing.  The
    # report identity keeps facts of the same filing together even when they
    # carry different statement sections (BS/IS/CF).
    report_keys = [column for column in ("rcept_no", "corp_code") if column in prepared.columns]
    if not report_keys:
        raise DARTPITError("wide pivot requires rcept_no or corp_code to group facts")
    if len(report_keys) == 1:
        report_keys.append("corp_code") if "corp_code" in prepared.columns else report_keys.append("rcept_no")

    records: list[dict[str, Any]] = []
    for _, group in prepared.groupby(report_keys, sort=False, dropna=False):
        rows = [dict(row) for row in group.to_dict("records")]
        record: dict[str, Any] = {
            "ticker": str(group[stock_code_field].iloc[0]),
            filing_field: group[filing_field].iloc[0],
        }
        for column in mapping:
            record[column] = find_account_value(rows, column)
        records.append(record)

    result = pd.DataFrame(records)
    result.attrs = dict(prepared.attrs)
    return result


# Naming aliases keep the importer easy to discover and preserve room for a
# future API/bulk-download adapter without changing the local contract.
load_dart_filing_metadata = load_filing_metadata
load_dart_financial_facts = load_financial_facts
load_raw_filing_metadata = load_filing_metadata
load_raw_financial_facts = load_financial_facts
join_facts_to_filings = join_financial_facts_to_filings
join_financial_facts = join_financial_facts_to_filings
join_dart_financial_facts_to_filings = join_financial_facts_to_filings
map_availability_to_sessions = map_filing_availability
map_dart_filing_availability = map_filing_availability
validate_financial_pit = validate_dart_pit
validate_dart_provenance = validate_dart_pit
prepare_dart_financial_facts = prepare_financial_facts


__all__ = [
    "AMENDMENT_POLICIES",
    "DART_AMENDMENT_POLICIES",
    "DART_FINANCIAL_FACT_COLUMNS",
    "DART_FILING_METADATA_COLUMNS",
    "DART_EXPLICIT_TIMESTAMP_POLICY",
    "DART_OFFICIAL_FILING_TIMEZONE",
    "DART_OPEN_DART_ENDPOINTS",
    "DART_RAW_FINANCIAL_FACT_COLUMNS",
    "DART_RAW_FILING_METADATA_COLUMNS",
    "DARTPITError",
    "DARTPITValidationError",
    "DARTPITValidationResult",
    "EXPLICIT_TIMESTAMP_POLICY",
    "FINANCIAL_FACT_COLUMNS",
    "FILING_METADATA_COLUMNS",
    "NEXT_SESSION_POLICY",
    "OFFICIAL_FILING_TIMEZONE",
    "OPEN_DART_ENDPOINTS",
    "RAW_FINANCIAL_FACT_COLUMNS",
    "RAW_FILING_METADATA_COLUMNS",
    "join_financial_facts_to_filings",
    "join_financial_facts",
    "join_facts_to_filings",
    "join_dart_financial_facts_to_filings",
    "load_dart_financial_facts",
    "load_dart_filing_metadata",
    "load_financial_facts",
    "load_filing_metadata",
    "load_raw_financial_facts",
    "load_raw_filing_metadata",
    "map_availability_to_sessions",
    "map_filing_availability",
    "map_dart_filing_availability",
    "prepare_dart_financial_facts",
    "prepare_financial_facts",
    "sha256_bytes",
    "sha256_file",
    "validate_dart_pit",
    "validate_dart_provenance",
    "validate_financial_pit",
]
