"""Offline, fail-closed contracts for production evidence.

This module deliberately stops at evidence capture and validation.  It has no
HTTP client and therefore cannot download market data by accident.  A caller
must inject a transport which returns already-captured response bytes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, NoReturn, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd

from k200_low_vol.spec import DEVELOPMENT_CUTOFF


SCHEMA_VERSION = "phase2-production-evidence-v1"
NORMALIZATION_VERSION = "canonical-raw-v1"
KRX_DATA_HOST = "data.krx.co.kr"
KRX_DATA_ENDPOINT = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_SNAPSHOT_BLD = "dbms/MDC/STAT/standard/MDCSTAT00601"
KOSPI200_INDEX_SELECTOR = "028"
KOSPI200_INDEX_TYPE = "1"
KOSPI200_TARGET_SIZE = 200
ACTION_MAPPING_VERSION = "krx-kind-action-map-v1"
STATUS_MAPPING_VERSION = "krx-ohlcv-status-v1"
PRODUCTION_ROLES = frozenset(
    {"sessions", "identities", "universe", "ohlcv", "actions", "benchmark"}
)
ACTION_START_ALIASES = ("start_date", "strtDd", "from_date")
ACTION_END_ALIASES = ("end_date", "endDd", "to_date")
APPROVED_RAW_CODE_MAP = {
    "DIV": "cash_dividend",
    "SPLIT": "split",
    "RSPLIT": "reverse_split",
    "SUSP": "suspension",
    "DELIST": "delisting",
}
SUPPORTED_ACTIONS = frozenset(
    {"split", "reverse_split", "cash_dividend", "suspension", "delisting"}
)
REQUIRED_OHLCV_COLUMNS = frozenset(
    {
        "security_id",
        "ticker",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "observed",
        "suspended",
        "stale",
        "missing",
        "source_artifact_sha256",
        "source_row_key",
    }
)
_HASH_KEYS = frozenset({"raw_sha256", "component_sha256", "manifest_sha256"})


class ProductionBundleError(ValueError):
    """Raised whenever production evidence is incomplete or unsafe."""


def _fail(message: str) -> NoReturn:
    raise ProductionBundleError(message)


def _as_date(value: Any, label: str) -> date:
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError("invalid date")
        parsed = timestamp.date()
        if parsed > DEVELOPMENT_CUTOFF:
            raise ValueError("post-cutoff date")
        return parsed
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProductionBundleError(f"{label} is invalid") from exc


def _dates(values: Sequence[Any], label: str) -> tuple[date, ...]:
    return tuple(sorted({_as_date(value, label) for value in values}))


def _hash(value: Any) -> str:
    def jsonable(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): jsonable(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [jsonable(nested) for nested in item]
        return item

    payload = json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    def freeze(nested: Any) -> Any:
        if isinstance(nested, Mapping):
            return MappingProxyType({str(key): freeze(item) for key, item in nested.items()})
        if isinstance(nested, (list, tuple, set)):
            return tuple(freeze(item) for item in nested)
        return nested

    return cast(Mapping[str, Any], freeze(value))


def _validate_query_dates(value: Any, key: str = "query parameter") -> None:
    """Check date-bearing query parameters without interpreting arbitrary IDs as dates."""
    normalized_key = key.casefold().replace("_", "")
    date_key = any(token in normalized_key for token in ("date", "from", "to", "start", "end", "trddd", "asof"))
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _validate_query_dates(nested_value, str(nested_key))
    elif isinstance(value, (list, tuple, set)):
        for nested_value in value:
            _validate_query_dates(nested_value, key)
    elif date_key and isinstance(value, (date, datetime, pd.Timestamp, str)):
        _as_date(value, key)


def _parse_response(raw_bytes: bytes) -> Any:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionBundleError("raw response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, (Mapping, list)):
        _fail("raw response must be an object or row array")
    return payload


def _response_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    for key in ("output", "OutBlock_1", "OutBlock1", "outBlock_1", "block1", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
        if isinstance(value, Mapping):
            nested = value.get("output", value.get("rows", value.get("data")))
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, Mapping)]
    result = payload.get("result")
    if isinstance(result, Mapping):
        for key in ("output", "data", "rows"):
            value = result.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
    return [payload] if isinstance(payload, Mapping) else []


def _response_dates(payload: Any) -> tuple[date, ...]:
    found: list[date] = []
    date_keys = {
        "date", "trddd", "as_of_date", "asof", "action_date", "session_date",
        "start_date", "end_date", "from_date", "to_date", "strtdd", "enddd",
    }

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).casefold().replace("_", "") in {
                    item.replace("_", "") for item in date_keys
                }:
                    try:
                        found.append(_as_date(nested, f"response {key}"))
                    except ProductionBundleError:
                        raise
                elif isinstance(nested, (Mapping, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
    return tuple(sorted(set(found)))


def _transition_marker(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    marker = payload.get("transition_marker", payload.get("transition"))
    if not isinstance(marker, Mapping):
        return None
    try:
        marker_date = _as_date(marker.get("as_of_date"), "transition as-of date")
        count = int(cast(Any, marker.get("constituent_count")))
    except (TypeError, ValueError, ProductionBundleError):
        return None
    index_code = str(marker.get("index_code", marker.get("indIdx2", "")))
    source = str(marker.get("source", marker.get("source_identity", ""))).upper()
    official = marker.get("official") is True or marker.get("verified") is True
    if index_code != KOSPI200_INDEX_SELECTOR or source != "KRX" or not official or count < 0:
        return None
    return {"as_of_date": marker_date, "constituent_count": count, "index_code": index_code}


def _validate_role_response(role: str, payload: Any, response_dates: tuple[date, ...]) -> None:
    rows = _response_rows(payload)
    if role == "actions" and not rows:
        coverage = payload.get("coverage") if isinstance(payload, Mapping) else None
        if not isinstance(coverage, Mapping):
            _fail("zero-event action response lacks official coverage evidence")
    elif not rows or not response_dates:
        _fail("raw response lacks parseable dated rows")
    if role == "universe":
        envelope_keys = ("output", "OutBlock_1", "OutBlock1", "outBlock_1", "data", "result")
        if not isinstance(payload, Mapping) or not rows or not any(key in payload for key in envelope_keys):
            _fail("universe response must use an official KRX row structure")
        if not all("ISU_SRT_CD" in row for row in rows):
            _fail("universe response lacks ISU_SRT_CD constituent fields")
        index_codes = [row.get("IDX_CD") for row in rows if "IDX_CD" in row]
        if any(str(value) != KOSPI200_INDEX_SELECTOR for value in index_codes):
            _fail("universe response is not the KOSPI 200 index")
    elif role == "sessions":
        if not all(any(name in row for name in ("date", "session_date", "trdDd")) for row in rows):
            _fail("session response rows lack dates")
    elif role == "identities":
        if not all(
            any(name in row for name in ("ISU_CD", "ISU_SRT_CD", "security_id"))
            and "ticker" in row
            and "effective_from" in row
            for row in rows
        ):
            _fail("identity response rows lack canonical identity fields")
    elif role == "ohlcv":
        if not all(
            any(name in row for name in ("security_id", "ISU_SRT_CD", "ISU_CD"))
            and all(name in row for name in ("date", "open", "high", "low", "close", "volume"))
            and all(name in row for name in ("observed", "suspended", "stale", "missing"))
            for row in rows
        ):
            _fail("OHLCV response rows lack canonical value fields")
        if not isinstance(payload, Mapping) or payload.get("status_mapping_version") != STATUS_MAPPING_VERSION:
            _fail("OHLCV status mapping version is missing")
        if any(type(row[name]) is not bool for row in rows for name in ("observed", "suspended", "stale", "missing")):
            _fail("OHLCV status source fields must be strict booleans")
    elif role == "actions":
        if rows and not all(
            any(name in row for name in ("security_id", "ISU_SRT_CD", "ISU_CD"))
            and all(name in row for name in (
                "action_date", "raw_code", "event_id", "conflict_key", "source_identity", "resumption_date",
                "resolved", "confirmed", "ratio", "recovery_value", "price_adjusted", "portfolio_cash",
            ))
            for row in rows
        ):
            _fail("action response rows lack canonical event fields")
        if rows and any(
            type(row[name]) is not bool
            for row in rows
            for name in ("resolved", "confirmed", "price_adjusted", "portfolio_cash")
        ):
            _fail("action economics source fields must be strict booleans")
    elif role == "benchmark":
        if not all("benchmark_close" in row and any(name in row for name in ("date", "trdDd")) for row in rows):
            _fail("benchmark response rows lack canonical value fields")


def _raw_row_key(row: Mapping[str, Any], position: int) -> str:
    value = row.get("source_row_key")
    return str(value) if value is not None else f"row-{position}"


def _raw_rows_for(artifact: "RawArtifact") -> list[Mapping[str, Any]]:
    return _response_rows(_parse_response(artifact.response_bytes))


def _normal_identity(value: Any) -> str:
    text = str(value).strip()
    return text.split(":", 1)[-1].zfill(6)


def _raw_field(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    _fail(f"raw response row lacks required field {names[0]}")


def _same_date(left: Any, right: Any) -> bool:
    return _as_date(left, "raw row date") == _as_date(right, "canonical row date")


def _same_number(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(str(left).replace(",", "")), float(right), rel_tol=0.0, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


def _same_optional_number(left: Any, right: Any) -> bool:
    left_empty = left is None or (isinstance(left, str) and left.strip() in {"", "None", "NaN"})
    right_empty = right is None or (isinstance(right, str) and right.strip() in {"", "None", "NaN"})
    return (left_empty and right_empty) or (not left_empty and not right_empty and _same_number(left, right))


def _bound_raw_row(component: _Component, row: Mapping[str, Any]) -> Mapping[str, Any]:
    source_hash = str(row["source_artifact_sha256"])
    artifacts = [artifact for artifact in component.artifacts if str(artifact.raw_sha256) == source_hash]
    if len(artifacts) != 1:
        _fail("canonical row source artifact is missing or ambiguous")
    key = str(row["source_row_key"])
    matches = [
        raw_row for position, raw_row in enumerate(_raw_rows_for(artifacts[0])) if _raw_row_key(raw_row, position) == key
    ]
    if len(matches) != 1:
        _fail("canonical row source_row_key does not resolve to exactly one raw row")
    return matches[0]


def _validate_row_binding(component: _Component, role: str) -> None:
    for row in component.rows.to_dict("records"):
        raw = _bound_raw_row(component, row)
        if role == "sessions":
            if not _same_date(_raw_field(raw, "date", "session_date", "trdDd"), row["date"]):
                _fail("session canonical date does not match its raw row")
        elif role == "identities":
            if _normal_identity(_raw_field(raw, "ISU_CD", "ISU_SRT_CD", "security_id")) != _normal_identity(row["security_id"]):
                _fail("identity canonical security does not match its raw row")
            if str(_raw_field(raw, "ticker", "ISU_SRT_CD", "ISU_CD")) != str(row["ticker"]):
                _fail("identity canonical ticker does not match its raw row")
            for field_name in ("effective_from", "effective_to"):
                raw_value = _raw_field(raw, field_name)
                if pd.isna(row[field_name]) and raw_value in (None, "", "NaT"):
                    continue
                if not _same_date(raw_value, row[field_name]):
                    _fail("identity canonical effective date does not match its raw row")
        elif role == "universe":
            if _normal_identity(_raw_field(raw, "ISU_SRT_CD", "ISU_CD")) != _normal_identity(row["security_id"]):
                _fail("universe canonical security does not match its raw row")
            raw_date = next((raw[name] for name in ("trdDd", "date", "as_of_date") if name in raw), None)
            if raw_date is not None and not _same_date(raw_date, row["as_of_date"]):
                _fail("universe canonical date does not match its raw row")
        elif role == "ohlcv":
            if _normal_identity(_raw_field(raw, "security_id", "ISU_SRT_CD", "ISU_CD")) != _normal_identity(row["security_id"]):
                _fail("OHLCV canonical security does not match its raw row")
            if not _same_date(_raw_field(raw, "date", "trdDd"), row["date"]):
                _fail("OHLCV canonical date does not match its raw row")
            for field_name in ("open", "high", "low", "close", "volume"):
                if not _same_number(_raw_field(raw, field_name), row[field_name]):
                    _fail(f"OHLCV canonical {field_name} does not match its raw row")
            for field_name in ("observed", "suspended", "stale", "missing"):
                if type(row[field_name]) is not bool or _raw_field(raw, field_name) is not row[field_name]:
                    _fail(f"OHLCV canonical {field_name} does not match its raw status")
        elif role == "actions":
            if _normal_identity(_raw_field(raw, "security_id", "ISU_SRT_CD", "ISU_CD")) != _normal_identity(row["security_id"]):
                _fail("action canonical security does not match its raw row")
            if not _same_date(_raw_field(raw, "action_date", "date"), row["action_date"]):
                _fail("action canonical date does not match its raw row")
            for field_name in ("raw_code", "event_id", "conflict_key", "source_identity"):
                if str(_raw_field(raw, field_name)) != str(row[field_name]):
                    _fail(f"action canonical {field_name} does not match its raw row")
            for field_name in ("resolved", "confirmed", "price_adjusted", "portfolio_cash"):
                if type(row[field_name]) is not bool or _raw_field(raw, field_name) is not row[field_name]:
                    _fail(f"action canonical {field_name} does not match its raw economics")
            for field_name in ("ratio", "recovery_value"):
                if not _same_optional_number(_raw_field(raw, field_name), row[field_name]):
                    _fail(f"action canonical {field_name} does not match its raw economics")
            raw_resumption = _raw_field(raw, "resumption_date")
            if (pd.isna(row.get("resumption_date")) or row.get("resumption_date") in (None, "")) and raw_resumption in (None, "", "NaT"):
                pass
            elif not _same_date(raw_resumption, row.get("resumption_date")):
                _fail("action canonical resumption does not match its raw row")
        elif role == "benchmark":
            if not _same_date(_raw_field(raw, "date", "trdDd"), row["date"]):
                _fail("benchmark canonical date does not match its raw row")
            if not _same_number(_raw_field(raw, "benchmark_close", "close"), row["benchmark_close"]):
                _fail("benchmark canonical value does not match its raw row")


def _query_security_ids(params: Mapping[str, Any]) -> set[str]:
    for key in ("security_ids", "security_id", "isuCd", "ISU_CD", "ISU_SRT_CD"):
        if key in params:
            value = params[key]
            values = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
            return {_normal_identity(item) for item in values if str(item).strip()}
    return set()


def _raw_action_scope(artifact: "RawArtifact") -> tuple[set[str], date, date]:
    params = artifact.query_params
    query_ids = _query_security_ids(params)
    if not query_ids:
        _fail("action query must declare its security scope")
    start_value = next((params.get(key) for key in ACTION_START_ALIASES if key in params), None)
    end_value = next((params.get(key) for key in ACTION_END_ALIASES if key in params), None)
    if start_value is None or end_value is None:
        _fail("action query must declare an inclusive start and end date")
    query_start = _as_date(start_value, "action query start")
    query_end = _as_date(end_value, "action query end")
    if query_start > query_end:
        _fail("action query date range is reversed")
    payload = _parse_response(artifact.response_bytes)
    rows = _raw_rows_for(artifact)
    raw_ids = {_normal_identity(_raw_field(row, "security_id", "ISU_SRT_CD", "ISU_CD")) for row in rows}
    raw_dates = {_as_date(_raw_field(row, "action_date", "date"), "raw action date") for row in rows}
    if not raw_ids.issubset(query_ids):
        _fail("action event rows exceed parsed query security scope")
    if raw_dates and (min(raw_dates) < query_start or max(raw_dates) > query_end):
        _fail("parsed raw action dates do not fit the inclusive query range")
    coverage = payload.get("coverage") if isinstance(payload, Mapping) else None
    if not isinstance(coverage, Mapping):
        _fail("action response lacks explicit coverage evidence")
    evidence_ids = {_normal_identity(item) for item in coverage.get("security_ids", ())}
    evidence_start_value = next((coverage.get(key) for key in ACTION_START_ALIASES if key in coverage), None)
    evidence_end_value = next((coverage.get(key) for key in ACTION_END_ALIASES if key in coverage), None)
    evidence_start = _as_date(evidence_start_value, "raw action coverage start")
    evidence_end = _as_date(evidence_end_value, "raw action coverage end")
    if evidence_start > evidence_end:
        _fail("raw action coverage date range is reversed")
    if (evidence_ids, evidence_start, evidence_end) != (query_ids, query_start, query_end):
        _fail("action response coverage evidence does not match its query")
    return query_ids, query_start, query_end


def _frame_hash(frame: pd.DataFrame) -> str:
    ordered = frame.reset_index(drop=True).reindex(sorted(frame.columns), axis=1)

    def canonical(value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, (date, datetime, pd.Timestamp)):
            return pd.Timestamp(value).isoformat()
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        return str(value)

    records = [
        {str(key): canonical(value) for key, value in record.items()}
        for record in ordered.to_dict(orient="records")
    ]
    return _hash(records)


def _artifact_fingerprint(artifact: "RawArtifact") -> dict[str, Any]:
    return {
        "raw_sha256": artifact.raw_sha256,
        "endpoint": artifact.endpoint,
        "build_identifier": artifact.build_identifier,
        "query_params": dict(artifact.query_params),
        "requested_observation_dates": list(artifact.requested_observation_dates),
        "response_date_evidence": list(artifact.response_date_evidence),
        "cache_dates": list(artifact.cache_dates),
        "row_count": artifact.row_count,
        "schema_version": artifact.schema_version,
        "source_kind": artifact.source_kind,
        "role": artifact.role,
        "retrieved_at": artifact.retrieved_at.isoformat(),
        "retrieved_at_seoul": cast(datetime, artifact.retrieved_at_seoul).isoformat(),
        "retrieved_at_utc": cast(datetime, artifact.retrieved_at_utc).isoformat(),
    }


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """An exact raw response and its auditable request/evidence envelope."""

    response_bytes: bytes
    endpoint: str
    build_identifier: str
    query_params: Mapping[str, Any]
    retrieved_at: datetime
    requested_observation_dates: tuple[str, ...]
    response_date_evidence: tuple[str, ...]
    row_count: int
    schema_version: str
    cache_dates: tuple[str, ...] = ()
    source_kind: str = "official_raw"
    role: str = ""
    retrieved_at_seoul: datetime | None = None
    retrieved_at_utc: datetime | None = None
    raw_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.response_bytes, bytes) or not self.response_bytes:
            _fail("raw response bytes are required")
        if not self.endpoint or not self.build_identifier or self.role not in PRODUCTION_ROLES:
            _fail("endpoint and build identifier are required")
        parsed_endpoint = urlparse(self.endpoint)
        if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != KRX_DATA_HOST:
            _fail("raw artifacts require the official KRX data endpoint host")
        if not isinstance(self.retrieved_at, datetime) or self.retrieved_at.tzinfo is None:
            _fail("retrieved_at must be timezone-aware")
        if type(self.row_count) is not int or self.row_count < 0:
            _fail("row_count must be a non-negative integer")
        if self.source_kind != "official_raw":
            _fail("only direct official raw artifacts are accepted")
        _validate_query_dates(self.query_params)
        if not self.query_params:
            _fail("the exact role query contract is required")
        request_dates = _dates(self.requested_observation_dates, "requested observation date")
        response_dates = _dates(self.response_date_evidence, "response date evidence")
        cache_dates = _dates(self.cache_dates, "cache date")
        if self.role == "universe":
            expected = {
                "bld": KRX_SNAPSHOT_BLD,
                "indIdx2": KOSPI200_INDEX_SELECTOR,
                "indIdx": KOSPI200_INDEX_TYPE,
            }
            if any(str(self.query_params.get(key)) != value for key, value in expected.items()):
                _fail("universe artifact does not use the official KOSPI 200 query contract")
            requested_dd = str(self.query_params.get("trdDd", ""))
            requested_compact = {item.replace("-", "") for item in self.requested_observation_dates}
            if requested_dd not in requested_compact:
                _fail("universe query trdDd is not bound to the requested as-of date")
        object.__setattr__(self, "requested_observation_dates", tuple(d.isoformat() for d in request_dates))
        object.__setattr__(self, "response_date_evidence", tuple(d.isoformat() for d in response_dates))
        object.__setattr__(self, "cache_dates", tuple(d.isoformat() for d in cache_dates))
        object.__setattr__(self, "query_params", _freeze_mapping(self.query_params))
        seoul = self.retrieved_at.astimezone(ZoneInfo("Asia/Seoul"))
        utc = self.retrieved_at.astimezone(ZoneInfo("UTC"))
        if self.retrieved_at_seoul is not None and self.retrieved_at_seoul != seoul:
            _fail("retrieved_at_seoul does not match retrieved_at")
        if self.retrieved_at_utc is not None and self.retrieved_at_utc != utc:
            _fail("retrieved_at_utc does not match retrieved_at")
        object.__setattr__(self, "retrieved_at_seoul", seoul)
        object.__setattr__(self, "retrieved_at_utc", utc)
        payload = _parse_response(self.response_bytes)
        parsed_rows = _response_rows(payload)
        parsed_dates = _response_dates(payload)
        _validate_role_response(self.role, payload, parsed_dates)
        if len(parsed_rows) != self.row_count:
            _fail("declared artifact row_count does not match parsed raw-row count")
        if set(parsed_dates) != set(response_dates):
            _fail("caller response dates do not match parsed raw response dates")
        actual = hashlib.sha256(self.response_bytes).hexdigest()
        if self.raw_sha256 is not None and self.raw_sha256.casefold() != actual:
            _fail("raw SHA-256 does not match response bytes")
        object.__setattr__(self, "raw_sha256", actual)


def _response_bytes(response: Any) -> bytes:
    if isinstance(response, bytes):
        return response
    if isinstance(response, bytearray):
        return bytes(response)
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    _fail("injected transport must return response bytes or a response with byte content")


def capture_raw_artifact(
    transport: Callable[[str, Mapping[str, Any]], Any],
    *,
    endpoint: str,
    build_identifier: str,
    query_params: Mapping[str, Any],
    retrieved_at: datetime,
    requested_observation_dates: Sequence[Any],
    response_date_evidence: Sequence[Any],
    row_count: int,
    schema_version: str = SCHEMA_VERSION,
    cache_dates: Sequence[Any] = (),
    role: str,
) -> RawArtifact:
    """Capture bytes from an injected transport; never creates a network client."""
    request = dict(query_params)
    if role not in PRODUCTION_ROLES:
        _fail("unknown production artifact role")
    if urlparse(endpoint).scheme != "https" or urlparse(endpoint).hostname != KRX_DATA_HOST:
        _fail("raw artifacts require the official KRX data endpoint host")
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
        _fail("retrieved_at must be timezone-aware")
    _validate_query_dates(request)
    _dates(requested_observation_dates, "requested observation date")
    _dates(cache_dates, "cache date")
    if role == "universe":
        expected = {"bld": KRX_SNAPSHOT_BLD, "indIdx2": KOSPI200_INDEX_SELECTOR, "indIdx": KOSPI200_INDEX_TYPE}
        if any(str(request.get(key)) != value for key, value in expected.items()):
            _fail("universe artifact does not use the official KOSPI 200 query contract")
        requested_compact = {str(item).replace("-", "") for item in requested_observation_dates}
        if str(request.get("trdDd")) not in requested_compact:
            _fail("universe query trdDd is not bound to the requested as-of date")
    response = transport(endpoint, request)
    return RawArtifact(
        response_bytes=_response_bytes(response),
        endpoint=endpoint,
        build_identifier=build_identifier,
        query_params=request,
        retrieved_at=retrieved_at,
        requested_observation_dates=tuple(str(value) for value in requested_observation_dates),
        response_date_evidence=tuple(str(value) for value in response_date_evidence),
        row_count=row_count,
        schema_version=schema_version,
        cache_dates=tuple(str(value) for value in cache_dates),
        role=role,
    )


def validate_raw_artifact(artifact: RawArtifact) -> RawArtifact:
    """Recompute the byte hash and enforce every date-side cutoff."""
    if not isinstance(artifact, RawArtifact):
        _fail("a RawArtifact is required")
    actual = hashlib.sha256(artifact.response_bytes).hexdigest()
    if actual != artifact.raw_sha256:
        _fail("raw artifact was tampered with")
    _dates(artifact.requested_observation_dates, "requested observation date")
    _dates(artifact.response_date_evidence, "response date evidence")
    _dates(artifact.cache_dates, "cache date")
    _validate_query_dates(artifact.query_params)
    if not artifact.response_date_evidence:
        _fail("response-date evidence is required")
    if artifact.schema_version != SCHEMA_VERSION:
        _fail("unsupported raw artifact schema version")
    if artifact.source_kind != "official_raw" or artifact.role not in PRODUCTION_ROLES:
        _fail("raw artifact role or source kind is invalid")
    if urlparse(artifact.endpoint).scheme != "https" or urlparse(artifact.endpoint).hostname != KRX_DATA_HOST:
        _fail("raw artifacts require the official KRX data endpoint host")
    expected_seoul = artifact.retrieved_at.astimezone(ZoneInfo("Asia/Seoul"))
    expected_utc = artifact.retrieved_at.astimezone(ZoneInfo("UTC"))
    if artifact.retrieved_at_seoul != expected_seoul or artifact.retrieved_at_utc != expected_utc:
        _fail("retrieval timestamp representations do not match")
    if artifact.role == "universe":
        expected = {"bld": KRX_SNAPSHOT_BLD, "indIdx2": KOSPI200_INDEX_SELECTOR, "indIdx": KOSPI200_INDEX_TYPE}
        if any(str(artifact.query_params.get(key)) != value for key, value in expected.items()):
            _fail("universe artifact does not use the official KOSPI 200 query contract")
        if str(artifact.query_params.get("trdDd")) not in {
            item.replace("-", "") for item in artifact.requested_observation_dates
        }:
            _fail("universe query trdDd is not bound to the requested as-of date")
    payload = _parse_response(artifact.response_bytes)
    parsed_rows = _response_rows(payload)
    parsed_dates = _response_dates(payload)
    _validate_role_response(artifact.role, payload, parsed_dates)
    if len(parsed_rows) != artifact.row_count:
        _fail("declared artifact row_count does not match parsed raw-row count")
    if set(parsed_dates) != set(_dates(artifact.response_date_evidence, "response date evidence")):
        _fail("response date evidence does not match parsed response")
    if artifact.retrieved_at.tzinfo is None:
        _fail("retrieved_at must remain timezone-aware")
    return artifact


@dataclass(frozen=True, slots=True)
class _Component:
    rows: pd.DataFrame
    artifacts: tuple[RawArtifact, ...]
    schema_version: str = SCHEMA_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    coverage: Mapping[str, Any] = field(default_factory=dict)
    security_scope: tuple[str, ...] = ()
    cutoff: date = DEVELOPMENT_CUTOFF
    component_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rows, pd.DataFrame):
            _fail("component rows must be a DataFrame")
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage))
        object.__setattr__(self, "security_scope", tuple(sorted(str(x) for x in self.security_scope)))
        if self.component_sha256 is None:
            object.__setattr__(self, "component_sha256", _component_hash(self))


@dataclass(frozen=True, slots=True)
class ProductionSessionComponent(_Component):
    """Canonical official trading-session evidence."""


@dataclass(frozen=True, slots=True)
class SecurityIdentityComponent(_Component):
    """Effective-dated ISU_CD/ISIN identity mappings."""


@dataclass(frozen=True, slots=True)
class PITUniverseComponent(_Component):
    """Point-in-time universe snapshots with per-row raw proof."""


@dataclass(frozen=True, slots=True)
class ProductionOHLCVComponent(_Component):
    """Unadjusted, explicit-session raw OHLCV evidence."""


@dataclass(frozen=True, slots=True)
class CorporateActionComponent(_Component):
    """Raw-code-preserving corporate-action ledger."""


@dataclass(frozen=True, slots=True)
class KPI200BenchmarkComponent(_Component):
    """Official KPI200 price-return benchmark evidence."""


@dataclass(frozen=True, slots=True)
class ProductionEvidenceManifest:
    component_hashes: Mapping[str, str]
    schema_version: str = SCHEMA_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    coverage: Mapping[str, Any] = field(default_factory=dict)
    security_scope: tuple[str, ...] = ()
    cutoff: date = DEVELOPMENT_CUTOFF
    manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_hashes", _freeze_mapping(self.component_hashes))
        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage))
        object.__setattr__(self, "security_scope", tuple(sorted(str(x) for x in self.security_scope)))
        if self.manifest_sha256 is None:
            object.__setattr__(self, "manifest_sha256", _manifest_hash(self))


@dataclass(frozen=True, slots=True)
class ProductionEvidenceBundle:
    sessions: ProductionSessionComponent
    identities: SecurityIdentityComponent
    universe: PITUniverseComponent
    ohlcv: ProductionOHLCVComponent
    actions: CorporateActionComponent
    benchmark: KPI200BenchmarkComponent
    manifest: ProductionEvidenceManifest


# Descriptive aliases keep the public vocabulary stable while retaining one
# implementation for each canonical production component.
ProductionRawArtifact = RawArtifact
ProductionBundleManifest = ProductionEvidenceManifest
SessionComponent = ProductionSessionComponent
IdentityComponent = SecurityIdentityComponent
UniverseComponent = PITUniverseComponent
RawOHLCVComponent = ProductionOHLCVComponent
ActionLedgerComponent = CorporateActionComponent
BenchmarkComponent = KPI200BenchmarkComponent
RootProductionBundle = ProductionEvidenceBundle


def _component_hash(component: _Component) -> str:
    return _hash(
        {
            "rows_sha256": _frame_hash(component.rows),
            "artifacts": [_artifact_fingerprint(a) for a in component.artifacts],
            "schema_version": component.schema_version,
            "normalization_version": component.normalization_version,
            "coverage": dict(component.coverage),
            "security_scope": list(component.security_scope),
            "cutoff": component.cutoff.isoformat(),
        }
    )


def _manifest_hash(manifest: ProductionEvidenceManifest) -> str:
    return _hash(
        {
            "component_hashes": dict(manifest.component_hashes),
            "schema_version": manifest.schema_version,
            "normalization_version": manifest.normalization_version,
            "coverage": dict(manifest.coverage),
            "security_scope": list(manifest.security_scope),
            "cutoff": manifest.cutoff.isoformat(),
        }
    )


def _root_coverage(components: Mapping[str, _Component]) -> dict[str, Any]:
    date_columns = {
        "sessions": "date",
        "identities": "effective_from",
        "universe": "as_of_date",
        "ohlcv": "date",
        "actions": "action_date",
        "benchmark": "date",
    }
    component_dates: dict[str, list[str]] = {}
    for name, column in date_columns.items():
        frame = components[name].rows
        if column in frame and not frame.empty:
            component_dates[name] = [item.isoformat() for item in _dates(frame[column].tolist(), column)]
    return {
        "security_scope": sorted(set(components["ohlcv"].rows["security_id"].astype(str))),
        "date_range": {
            "start": component_dates["sessions"][0],
            "end": component_dates["sessions"][-1],
        },
        "component_dates": component_dates,
        "component_security_scopes": {
            name: sorted(set(component.rows["security_id"].astype(str)))
            for name, component in components.items()
            if "security_id" in component.rows
        },
        "artifact_dates": {
            name: sorted({date_value for artifact in component.artifacts for date_value in artifact.response_date_evidence})
            for name, component in components.items()
        },
    }


def build_production_manifest(
    components: Mapping[str, _Component], *, coverage: Mapping[str, Any] | None = None
) -> ProductionEvidenceManifest:
    names = {"sessions", "identities", "universe", "ohlcv", "actions", "benchmark"}
    if set(components) != names:
        _fail("root manifest requires exactly six canonical components")
    scope = set(components["ohlcv"].rows["security_id"].astype(str))
    return ProductionEvidenceManifest(
        component_hashes={
            name: cast(str, component.component_sha256) for name, component in components.items()
        },
        coverage=coverage if coverage is not None else _root_coverage(components),
        security_scope=tuple(sorted(scope)),
    )


def build_production_bundle(
    *,
    sessions: ProductionSessionComponent,
    identities: SecurityIdentityComponent,
    universe: PITUniverseComponent,
    ohlcv: ProductionOHLCVComponent,
    actions: CorporateActionComponent,
    benchmark: KPI200BenchmarkComponent,
    coverage: Mapping[str, Any] | None = None,
) -> ProductionEvidenceBundle:
    components = {
        "sessions": sessions,
        "identities": identities,
        "universe": universe,
        "ohlcv": ohlcv,
        "actions": actions,
        "benchmark": benchmark,
    }
    return ProductionEvidenceBundle(**components, manifest=build_production_manifest(components, coverage=coverage))


def _validate_component_common(component: _Component, name: str) -> None:
    if component.cutoff != DEVELOPMENT_CUTOFF:
        _fail(f"{name} cutoff is not the registered development cutoff")
    if component.schema_version != SCHEMA_VERSION:
        _fail(f"{name} schema version is unsupported")
    if component.normalization_version != NORMALIZATION_VERSION:
        _fail(f"{name} normalization version is unsupported")
    if not component.artifacts:
        _fail(f"{name} requires direct raw artifacts")
    if not {"source_artifact_sha256", "source_row_key"}.issubset(component.rows.columns):
        _fail(f"{name} canonical rows require raw source binding fields")
    for artifact in component.artifacts:
        if artifact.role != name:
            _fail(f"{name} rows must use role-specific raw artifacts")
        validate_raw_artifact(artifact)
    if component.coverage.get("aggregate_only") is True:
        _fail(f"{name} cannot use aggregate-only proof")
    if sum(artifact.row_count for artifact in component.artifacts) < len(component.rows):
        _fail(f"{name} row count exceeds raw artifact evidence")
    if component.component_sha256 != _component_hash(component):
        _fail(f"{name} component hash does not match rows or evidence")


def _artifact_hashes(component: _Component) -> set[str]:
    return {str(artifact.raw_sha256) for artifact in component.artifacts}


def _identity_value(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value.isdigit() and len(value) == 6:
        return False
    upper = value.upper()
    return (
        upper.startswith("ISU_CD:")
        or upper.startswith("ISIN:")
        or re.fullmatch(r"KR[A-Z0-9]{10}", upper) is not None
    )


def _validate_sessions(component: ProductionSessionComponent) -> tuple[date, ...]:
    _validate_component_common(component, "sessions")
    if "date" not in component.rows or component.rows.empty:
        _fail("official session rows are required")
    _validate_row_binding(component, "sessions")
    dates = _dates(component.rows["date"].tolist(), "session date")
    if component.rows["date"].duplicated().any():
        _fail("duplicate official sessions")
    if "source_artifact_sha256" in component.rows:
        if not set(component.rows["source_artifact_sha256"].astype(str)).issubset(_artifact_hashes(component)):
            _fail("session row lacks raw artifact proof")
    else:
        _fail("session rows require raw artifact proof")
    return dates


def _mapping_for(identity: SecurityIdentityComponent, security_id: str, when: date) -> pd.Series:
    rows = identity.rows
    effective_from = pd.to_datetime(rows["effective_from"], errors="coerce").dt.normalize()
    effective_to = pd.to_datetime(rows["effective_to"], errors="coerce").dt.normalize()
    point = pd.Timestamp(when)
    matches = rows[
        (rows["security_id"].astype(str) == security_id)
        & (effective_from <= point)
        & (rows["effective_to"].isna() | (effective_to >= point))
    ]
    if len(matches) != 1:
        _fail(f"identity {security_id} does not resolve exactly on {when}")
    return matches.iloc[0]


def _validate_identities(component: SecurityIdentityComponent) -> None:
    _validate_component_common(component, "identities")
    required = {
        "security_id",
        "ticker",
        "effective_from",
        "effective_to",
        "identity_source",
        "source_artifact_sha256",
    }
    if not required.issubset(component.rows.columns) or component.rows.empty:
        _fail("effective-dated security identity rows are required")
    _validate_row_binding(component, "identities")
    rows = component.rows.copy()
    if not rows["security_id"].map(_identity_value).all():
        _fail("security identity must be explicitly ISU_CD/ISIN-derived")
    if not rows["identity_source"].astype(str).str.upper().str.contains("ISU_CD|ISIN", regex=True).all():
        _fail("identity source must name ISU_CD or ISIN")
    if not rows["source_artifact_sha256"].astype(str).isin(_artifact_hashes(component)).all():
        _fail("identity row lacks official raw artifact proof")
    if rows["ticker"].isna().any() or rows["ticker"].astype(str).str.len().eq(0).any():
        _fail("ticker mapping is incomplete")
    if rows.groupby("ticker")["security_id"].nunique().gt(1).any():
        _fail("ticker reuse across security identities is rejected")
    if set(rows["security_id"].astype(str)) != set(component.security_scope):
        _fail("identity security scope declaration does not match canonical rows")
    for security_id, group in rows.groupby("security_id"):
        intervals = []
        for row in group.to_dict("records"):
            start = _as_date(row["effective_from"], "identity effective_from")
            end = _as_date(row["effective_to"], "identity effective_to") if pd.notna(row["effective_to"]) else DEVELOPMENT_CUTOFF
            if end < start:
                _fail("identity effective interval is reversed")
            intervals.append((start, end))
        intervals.sort()
        if any(current[0] <= previous[1] for previous, current in zip(intervals, intervals[1:], strict=False)):
            _fail(f"overlapping ticker mappings for {security_id}")


def _validate_universe(component: PITUniverseComponent, identities: SecurityIdentityComponent) -> set[str]:
    _validate_component_common(component, "universe")
    required = {"as_of_date", "security_id", "ticker", "source_artifact_sha256"}
    if not required.issubset(component.rows.columns) or component.rows.empty:
        _fail("PIT universe requires snapshot rows and raw artifact hashes")
    hashes = _artifact_hashes(component)
    if not component.rows["source_artifact_sha256"].astype(str).isin(hashes).all():
        _fail("PIT universe row lacks official raw artifact proof")
    requested = component.coverage.get("requested_as_of_dates")
    if not isinstance(requested, (list, tuple)) or not requested:
        _fail("requested PIT universe dates are required")
    requested_dates = set(_dates(cast(Sequence[Any], requested), "requested universe as-of date"))
    actual_dates = set(_dates(component.rows["as_of_date"].tolist(), "universe as-of date"))
    if actual_dates != requested_dates:
        _fail("universe is missing a requested as-of snapshot")
    if component.rows.duplicated(["as_of_date", "security_id"]).any():
        _fail("duplicate PIT universe identity")
    _validate_row_binding(component, "universe")
    if set(component.rows["security_id"].astype(str)) != set(component.security_scope):
        _fail("PIT universe security scope declaration does not match canonical rows")
    by_hash = {str(artifact.raw_sha256): artifact for artifact in component.artifacts}
    for row in component.rows.to_dict("records"):
        artifact = by_hash[str(row["source_artifact_sha256"])]
        as_of = _as_date(row["as_of_date"], "as-of date")
        if artifact.endpoint != KRX_DATA_ENDPOINT:
            _fail("PIT universe requires the official KRX snapshot endpoint")
        if set(artifact.response_date_evidence) != {as_of.isoformat()}:
            _fail("PIT universe artifact response date is not bound to its snapshot")
        if str(artifact.query_params.get("trdDd")) != as_of.strftime("%Y%m%d"):
            _fail("PIT universe query date is not bound to its snapshot")
        payload_rows = _response_rows(_parse_response(artifact.response_bytes))
        response_ids = {
            _normal_identity(_raw_field(item, "ISU_SRT_CD", "ISU_CD")) for item in payload_rows
        }
        canonical_id = str(row["security_id"]).split(":", 1)[-1]
        if canonical_id not in response_ids:
            _fail("PIT universe identity is not present in its official response")
    for as_of in sorted(actual_dates):
        canonical_ids = {
            _normal_identity(value)
            for value in component.rows.loc[
                pd.to_datetime(component.rows["as_of_date"]).dt.date == as_of, "security_id"
            ]
        }
        response_ids: set[str] = set()
        for artifact in component.artifacts:
            if artifact.role != "universe":
                continue
            if set(artifact.response_date_evidence) != {as_of.isoformat()}:
                continue
            response_ids.update(
                _normal_identity(_raw_field(row, "ISU_SRT_CD", "ISU_CD"))
                for row in _raw_rows_for(artifact)
            )
        if canonical_ids != response_ids:
            _fail("canonical PIT universe constituents are not exactly the raw response constituents")
    for as_of in sorted(actual_dates):
        count = int((pd.to_datetime(component.rows["as_of_date"]).dt.date == as_of).sum())
        if count != KOSPI200_TARGET_SIZE:
            proven = False
            for artifact in component.artifacts:
                if artifact.role != "universe":
                    continue
                marker = _transition_marker(_parse_response(artifact.response_bytes))
                if marker and marker["as_of_date"] == as_of and marker["constituent_count"] == count:
                    proven = True
            if not proven:
                _fail("non-200 universe snapshot lacks bound official transition evidence")
    for row in component.rows.to_dict("records"):
        resolved = _mapping_for(identities, str(row["security_id"]), _as_date(row["as_of_date"], "as-of date"))
        if str(resolved["ticker"]) != str(row["ticker"]):
            _fail("universe ticker does not resolve from identity evidence")
    return set(component.rows["security_id"].astype(str))


def _validate_ohlcv(
    component: ProductionOHLCVComponent,
    sessions: tuple[date, ...],
    identities: SecurityIdentityComponent,
    universe: PITUniverseComponent,
) -> None:
    _validate_component_common(component, "ohlcv")
    rows = component.rows
    if not REQUIRED_OHLCV_COLUMNS.issubset(rows.columns):
        _fail("raw OHLCV columns are incomplete")
    _validate_row_binding(component, "ohlcv")
    adjusted = {str(column).casefold() for column in rows.columns} & {
        "adjusted_close", "adj_close", "adjusted_price", "adjusted_open", "adjusted_high", "adjusted_low"
    }
    if (
        adjusted
        or ("price_basis" in rows and not rows["price_basis"].astype(str).str.casefold().eq("price_return").all())
        or any(
            column in rows and rows[column].eq(True).any()
            for column in ("adjusted", "price_adjusted", "synthetic", "forward_filled")
        )
    ):
        _fail("adjusted-price provenance is not accepted")
    if rows.duplicated(["security_id", "date"]).any():
        _fail("duplicate security/date OHLCV rows")
    hashes = _artifact_hashes(component)
    if not rows["source_artifact_sha256"].astype(str).isin(hashes).all():
        _fail("OHLCV row lacks raw artifact proof")
    for status in ("observed", "suspended", "stale", "missing"):
        if any(type(value) is not bool for value in rows[status].tolist()):
            _fail(f"{status} must contain strict booleans")
    numeric = rows[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if (numeric["volume"].notna() & (numeric["volume"] < 0)).any():
        _fail("volume cannot be negative")
    observed = rows["observed"]
    if numeric.loc[observed, ["open", "high", "low", "close"]].isna().any().any():
        _fail("observed OHLCV rows require prices")
    if (numeric.loc[observed, ["open", "high", "low", "close"]] <= 0).any().any():
        _fail("observed prices must be positive")
    if (
        observed
        & ((numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
           | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)))
    ).any():
        _fail("OHLC ranges are invalid")
    dates = _dates(rows["date"].tolist(), "OHLCV date")
    if set(dates) != set(sessions):
        _fail("OHLCV does not cover the full explicit session lattice")
    security_ids = set(rows["security_id"].astype(str))
    for security_id, group in rows.groupby(rows["security_id"].astype(str)):
        if set(_dates(group["date"].tolist(), "OHLCV date")) != set(sessions):
            _fail(f"{security_id} lacks an explicit row for a session")
        for row in group.to_dict("records"):
            resolved = _mapping_for(identities, str(security_id), _as_date(row["date"], "OHLCV date"))
            if str(resolved["ticker"]) != str(row["ticker"]):
                _fail("OHLCV ticker does not resolve from identity evidence")
    universe_ids = set(universe.rows["security_id"].astype(str))
    if security_ids != universe_ids:
        _fail("OHLCV does not cover the complete PIT universe security scope")
    if security_ids != set(component.security_scope):
        _fail("OHLCV security scope declaration does not match canonical rows")
    if rows["source_row_key"].isna().any() or rows["source_row_key"].astype(str).str.len().eq(0).any():
        _fail("source_row_key is required")


def _validate_actions(
    component: CorporateActionComponent,
    identities: SecurityIdentityComponent,
    ohlcv: ProductionOHLCVComponent,
) -> None:
    _validate_component_common(component, "actions")
    required = {
        "security_id",
        "ticker",
        "action_date",
        "action_type",
        "raw_code",
        "resolved",
        "confirmed",
        "ratio",
        "recovery_value",
        "price_adjusted",
        "portfolio_cash",
        "event_id",
        "source_identity",
        "conflict_key",
    }
    if not required.issubset(component.rows.columns):
        _fail("corporate-action raw-code ledger is incomplete")
    coverage = component.coverage
    if coverage.get("coverage_version") != "actions-coverage-v1":
        _fail("versioned corporate-action coverage reconciliation is required")
    if coverage.get("raw_code_mapping_version") != ACTION_MAPPING_VERSION:
        _fail("approved raw-code mapping version is required")
    if coverage.get("raw_code_mapping") != APPROVED_RAW_CODE_MAP:
        _fail("corporate-action raw-code mapping is not the approved mapping")
    _validate_row_binding(component, "actions")
    derived_ids = set(ohlcv.rows["security_id"].astype(str))
    derived_dates = _dates(ohlcv.rows["date"].tolist(), "OHLCV date")
    if set(component.security_scope) != derived_ids:
        _fail("action security scope declaration does not match canonical rows")
    declared_ids = coverage.get("security_scope")
    declared_range = coverage.get("date_range")
    if set(map(str, declared_ids or ())) != derived_ids:
        _fail("corporate-action security coverage does not match OHLCV scope")
    if not isinstance(declared_range, Mapping):
        _fail("corporate-action date coverage does not match OHLCV range")
    declared_start = _as_date(declared_range.get("start"), "declared action coverage start")
    declared_end = _as_date(declared_range.get("end"), "declared action coverage end")
    if declared_start > declared_end or (declared_start, declared_end) != (derived_dates[0], derived_dates[-1]):
        _fail("corporate-action date coverage does not match ordered OHLCV bounds")
    raw_scopes = [_raw_action_scope(artifact) for artifact in component.artifacts]
    normalized_derived_ids = {_normal_identity(value) for value in derived_ids}
    if any(scope != (normalized_derived_ids, derived_dates[0], derived_dates[-1]) for scope in raw_scopes):
        _fail("corporate-action query/response scope does not match OHLCV coverage")
    hashes = _artifact_hashes(component)
    if "source_artifact_sha256" not in component.rows or not component.rows["source_artifact_sha256"].astype(str).isin(hashes).all():
        _fail("action row lacks raw artifact proof")
    if component.rows["event_id"].isna().any() or component.rows["event_id"].astype(str).str.len().eq(0).any():
        _fail("stable action event_id is required")
    if component.rows["event_id"].duplicated().any() or component.rows["conflict_key"].duplicated().any():
        _fail("duplicate or conflicting corporate action event")
    if not component.rows["source_identity"].astype(str).str.upper().isin({"KRX", "KIND"}).all():
        _fail("action source identity must be KRX or KIND")
    for row in component.rows.to_dict("records"):
        action_type = str(row["action_type"]).casefold()
        raw_code = str(row["raw_code"]).upper()
        if raw_code not in APPROVED_RAW_CODE_MAP or APPROVED_RAW_CODE_MAP[raw_code] != action_type:
            _fail("unknown corporate action code rejects the production bundle")
        if row["resolved"] is not True:
            _fail("unresolved corporate action rejects the production bundle")
        if action_type != "delisting" and row["confirmed"] is not True:
            _fail("unconfirmed corporate action rejects the production bundle")
        when = _as_date(row["action_date"], "action date")
        if when < derived_dates[0] or when > derived_dates[-1]:
            _fail("action event falls outside the reconciled OHLCV date range")
        resolved = _mapping_for(identities, str(row["security_id"]), when)
        if str(resolved["ticker"]) != str(row["ticker"]):
            _fail("action ticker does not resolve from identity evidence")
        if action_type in {"split", "reverse_split"}:
            try:
                ratio = float(row.get("ratio"))
            except (TypeError, ValueError):
                ratio = math.nan
            if not math.isfinite(ratio) or ratio <= 0:
                _fail("split actions require a positive ratio")
        if action_type == "delisting":
            if row.get("confirmed") is True:
                try:
                    recovery = float(row.get("recovery_value"))
                except (TypeError, ValueError):
                    recovery = math.nan
                if not math.isfinite(recovery) or recovery < 0:
                    _fail("confirmed delisting requires recovery evidence")
            elif not component.coverage.get("registered_zero_recovery_policy"):
                _fail("unconfirmed delisting requires a registered zero policy")
        if action_type == "cash_dividend":
            if row.get("price_adjusted") is True or row.get("portfolio_cash") is True:
                _fail("cash dividends are unadjusted and do not create portfolio cash")
        if action_type == "suspension":
            if row.get("resumption_date") is None:
                _fail("temporary suspension without a resumption date is unresolved")
            resumption = _as_date(row["resumption_date"], "resumption date")
            if resumption <= when:
                _fail("suspension resumption must follow the suspension date")


def _validate_benchmark(component: KPI200BenchmarkComponent, sessions: tuple[date, ...]) -> None:
    _validate_component_common(component, "benchmark")
    required = {"date", "benchmark_close", "source_artifact_sha256"}
    if not required.issubset(component.rows.columns) or component.rows.empty:
        _fail("KPI200 benchmark rows and raw proof are required")
    _validate_row_binding(component, "benchmark")
    if component.rows.duplicated("date").any():
        _fail("duplicate benchmark dates")
    if not component.rows["source_artifact_sha256"].astype(str).isin(_artifact_hashes(component)).all():
        _fail("benchmark row lacks raw artifact proof")
    values = pd.to_numeric(component.rows["benchmark_close"], errors="coerce")
    if values.isna().any() or (values <= 0).any():
        _fail("benchmark close must be positive")
    if set(_dates(component.rows["date"].tolist(), "benchmark date")) != set(sessions):
        _fail("benchmark does not cover official sessions")


def validate_production_bundle(bundle: ProductionEvidenceBundle) -> ProductionEvidenceBundle:
    """Validate all production components and their recomputed root binding."""
    if not isinstance(bundle, ProductionEvidenceBundle):
        _fail("a ProductionEvidenceBundle is required")
    components: dict[str, _Component] = {
        "sessions": bundle.sessions,
        "identities": bundle.identities,
        "universe": bundle.universe,
        "ohlcv": bundle.ohlcv,
        "actions": bundle.actions,
        "benchmark": bundle.benchmark,
    }
    _validate_identities(bundle.identities)
    session_dates = _validate_sessions(bundle.sessions)
    _validate_universe(bundle.universe, bundle.identities)
    _validate_ohlcv(bundle.ohlcv, session_dates, bundle.identities, bundle.universe)
    _validate_actions(bundle.actions, bundle.identities, bundle.ohlcv)
    _validate_benchmark(bundle.benchmark, session_dates)
    manifest = bundle.manifest
    if manifest.manifest_sha256 != _manifest_hash(manifest):
        _fail("root manifest was tampered with")
    expected_hashes = {name: _component_hash(component) for name, component in components.items()}
    if dict(manifest.component_hashes) != expected_hashes:
        _fail("root manifest component hash mismatch")
    if set(manifest.security_scope) != set(bundle.ohlcv.security_scope):
        _fail("root security scope mismatch")
    if manifest.schema_version != SCHEMA_VERSION or manifest.normalization_version != NORMALIZATION_VERSION:
        _fail("root manifest versions are unsupported")
    if manifest.cutoff != DEVELOPMENT_CUTOFF:
        _fail("root manifest cutoff is not registered")
    expected_coverage = _root_coverage(components)
    if _hash(manifest.coverage) != _hash(expected_coverage):
        _fail("root coverage does not match canonical rows and artifact date evidence")
    return bundle


__all__ = [
    "APPROVED_RAW_CODE_MAP",
    "ACTION_MAPPING_VERSION",
    "CorporateActionComponent",
    "ActionLedgerComponent",
    "BenchmarkComponent",
    "IdentityComponent",
    "KPI200BenchmarkComponent",
    "KRX_DATA_ENDPOINT",
    "KRX_SNAPSHOT_BLD",
    "NORMALIZATION_VERSION",
    "PITUniverseComponent",
    "ProductionBundleError",
    "ProductionEvidenceBundle",
    "ProductionEvidenceManifest",
    "ProductionOHLCVComponent",
    "ProductionRawArtifact",
    "ProductionSessionComponent",
    "RawArtifact",
    "RawOHLCVComponent",
    "RootProductionBundle",
    "SCHEMA_VERSION",
    "STATUS_MAPPING_VERSION",
    "SessionComponent",
    "SecurityIdentityComponent",
    "UniverseComponent",
    "ProductionBundleManifest",
    "build_production_bundle",
    "build_production_manifest",
    "capture_raw_artifact",
    "validate_production_bundle",
    "validate_raw_artifact",
]
