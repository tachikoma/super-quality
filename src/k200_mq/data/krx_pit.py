"""Opt-in KRX historical KOSPI 200 snapshot acquisition.

This module is deliberately separate from :mod:`k200_mq.data.universe`.  It
contains the authenticated acquisition path for the official KRX web endpoint,
but callers must explicitly request it and retain the raw response together
with its sidecar manifest before the response can be used as PIT evidence.

Credentials are resolved only when ``login`` is called.  They are never put in
request manifests, log messages, exception text, or object representations.
The test suite injects a session; production callers may use the default
``requests.Session`` without this module making a request at import time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, NamedTuple, cast
from zoneinfo import ZoneInfo

import pandas as pd

from k200_mq.data.pit_universe import (
    PITUniverseError,
    SNAPSHOT_COLUMNS,
    fingerprint_dataframe,
    load_constituent_snapshots,
    validate_constituent_snapshots,
)


# Official KRX web endpoints.  ``KRX_ENDPOINTS`` is a descriptive constant;
# callers should use the named constants when constructing requests.
KRX_DATA_ENDPOINT = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_LOGIN_PAGE_ENDPOINT = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
KRX_LOGIN_FRAME_ENDPOINT = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
KRX_LOGIN_ENDPOINT = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
KRX_SNAPSHOT_BLD = "dbms/MDC/STAT/standard/MDCSTAT00601"
KOSPI200_INDEX_CODE = "KOSPI200"
KOSPI200_INDEX_SELECTOR = "028"
KOSPI200_INDEX_TYPE = "1"
KRX_ENDPOINTS = {
    "snapshot": KRX_DATA_ENDPOINT,
    "login_page": KRX_LOGIN_PAGE_ENDPOINT,
    "login_frame": KRX_LOGIN_FRAME_ENDPOINT,
    "login": KRX_LOGIN_ENDPOINT,
}

# Compatibility spellings make the request contract discoverable to callers
# without introducing a second set of values.
KOSPI_200_CODE = KOSPI200_INDEX_CODE
KOSPI200_CODE = KOSPI200_INDEX_SELECTOR
KOSPI200_IND_IDX2 = KOSPI200_INDEX_SELECTOR
KOSPI200_IND_IDX = KOSPI200_INDEX_TYPE
KOSPI200_BLD = KRX_SNAPSHOT_BLD
KRX_SNAPSHOT_ENDPOINT = KRX_DATA_ENDPOINT
KRX_OFFICIAL_DATA_URL = KRX_DATA_ENDPOINT
KRX_LOGIN_URL = KRX_LOGIN_PAGE_ENDPOINT
KRX_LOGIN_SUBMIT_URL = KRX_LOGIN_ENDPOINT

KRX_SOURCE_TYPE = "krx_official_snapshot"
KRX_SCHEMA_VERSION = "k200_mq.krx_kospi200_snapshot.v1"
RAW_FILENAME_TEMPLATE = "krx_kospi200_{date}.json"
MANIFEST_SUFFIX = ".manifest.json"
KRX_TIMEZONE = "Asia/Seoul"
DEFAULT_TIMEOUT_SECONDS = 30.0
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
YYYYMMDD_RE = re.compile(r"^\d{8}$")

KRX_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://data.krx.co.kr",
    "Referer": "https://data.krx.co.kr/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}
KRX_LOGIN_HEADERS = {
    "User-Agent": KRX_HEADERS["User-Agent"],
    "Referer": KRX_LOGIN_PAGE_ENDPOINT,
}

_ENV_USER_KEYS = ("KRX_ID", "KRX_USERNAME", "KRX_USER_ID")
_ENV_PASSWORD_KEYS = ("KRX_PW", "KRX_PASSWORD")
_INDEX_CODE_KEYS = {
    "indidx2",
    "indexcode",
    "indexcd",
    "idxcd",
    "idxcode",
    "idxsrtcd",
    "지수코드",
}
_INDEX_TYPE_KEYS = {"indidx", "indextype", "idxtype"}
_INDEX_NAME_KEYS = {"idxnm", "indexname", "지수명", "지수이름"}
_DATE_MARKER_KEYS = {
    "asof",
    "asofdate",
    "basedate",
    "basdt",
    "date",
    "requestdate",
    "responsedate",
    "snapshotdate",
    "trddd",
    "tradedate",
    "trddate",
    "basdd",
    "tradingdate",
    "workdd",
    "effectivedate",
    "기준일",
    "기준일자",
    "적용일",
    "적용일자",
}


class KRXPITError(ValueError):
    """Base error for KRX acquisition and local verification failures."""


class KRXCredentialError(KRXPITError):
    """Raised when an explicit or call-time environment credential is absent."""


class KRXAuthenticationError(KRXPITError):
    """Raised when KRX does not return the documented successful login code."""


class KRXTransportError(KRXPITError):
    """Raised for HTTP/session failures before a response can be trusted."""


class KRXResponseError(KRXPITError):
    """Raised for an empty, malformed, or wrong-index KRX response."""


class KRXManifestError(KRXPITError):
    """Raised when a raw response is missing or fails its sidecar contract."""


@dataclass(frozen=True, repr=False)
class KRXCredentials:
    """Credentials held in memory for one KRX session.

    The password is intentionally excluded from ``repr``.  The adapter also
    avoids putting either value in logs or exception messages.
    """

    user_id: str
    password: str = field(repr=False)

    def __repr__(self) -> str:
        return "KRXCredentials(<redacted>)"

    @property
    def username(self) -> str:
        """Return the KRX form's user identifier without exposing the secret."""
        return self.user_id


class KRXSnapshotResponse(NamedTuple):
    """Raw endpoint bytes and the normalized rows derived from those bytes."""

    raw_bytes: bytes
    rows: pd.DataFrame

    @property
    def raw_response_bytes(self) -> bytes:
        """Descriptive alias for callers that prefer the longer name."""
        return self.raw_bytes

    @property
    def normalized_rows(self) -> pd.DataFrame:
        """Descriptive alias for the normalized snapshot frame."""
        return self.rows


@dataclass(frozen=True)
class KRXSnapshotArtifact:
    """Paths and metadata written for one downloaded snapshot."""

    as_of: date
    raw_path: Path
    manifest_path: Path
    row_count: int
    response_sha256: str


def _normalize_as_of(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise KRXResponseError(f"invalid KRX as-of date: {value!r}") from exc
    if YYYYMMDD_RE.fullmatch(text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError as exc:
            raise KRXResponseError(f"invalid KRX as-of date: {value!r}") from exc
    raise KRXResponseError("KRX as-of date must be YYYY-MM-DD or YYYYMMDD")


def _as_of_query(value: date | datetime | str) -> str:
    return _normalize_as_of(value).strftime("%Y%m%d")


def _snapshot_params(as_of: date | datetime | str) -> dict[str, str]:
    return {
        "bld": KRX_SNAPSHOT_BLD,
        "indIdx2": KOSPI200_INDEX_SELECTOR,
        "indIdx": KOSPI200_INDEX_TYPE,
        "trdDd": _as_of_query(as_of),
    }


def _resolve_credentials(
    credentials: KRXCredentials | Mapping[str, Any] | tuple[str, str] | None,
) -> KRXCredentials:
    """Resolve credentials at call time, never at module import time."""
    if credentials is None:
        user_id = next((os.environ.get(key, "") for key in _ENV_USER_KEYS if os.environ.get(key)), "")
        password = next(
            (os.environ.get(key, "") for key in _ENV_PASSWORD_KEYS if os.environ.get(key)),
            "",
        )
    elif isinstance(credentials, KRXCredentials):
        user_id, password = credentials.user_id, credentials.password
    elif isinstance(credentials, Mapping):
        user_id = next(
            (
                str(credentials[key])
                for key in ("user_id", "username", "id", "KRX_ID")
                if key in credentials and credentials[key] is not None
            ),
            "",
        )
        password = next(
            (
                str(credentials[key])
                for key in ("password", "pw", "KRX_PW")
                if key in credentials and credentials[key] is not None
            ),
            "",
        )
    elif isinstance(credentials, tuple) and len(credentials) == 2:
        user_id, password = (str(credentials[0]), str(credentials[1]))
    else:
        raise KRXCredentialError("KRX credentials must provide a user ID and password")

    if not user_id or not password:
        raise KRXCredentialError(
            "KRX credentials are required; provide them explicitly or set KRX_ID and KRX_PW"
        )
    return KRXCredentials(user_id=user_id, password=password)


def _new_session() -> Any:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is supplied by pykrx
        raise KRXTransportError("the requests package is required for KRX acquisition") from exc
    return requests.Session()


def _response_content(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.encode("utf-8")
    raise KRXResponseError("KRX response did not contain byte content")


def _ensure_http_success(response: Any, label: str) -> None:
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status >= 400:
        raise KRXTransportError(f"KRX {label} request failed with HTTP status {status}")
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        try:
            raise_for_status()
        except Exception as exc:
            raise KRXTransportError(f"KRX {label} request failed") from exc


def _request(session: Any, method: str, url: str, *, label: str, **kwargs: Any) -> Any:
    request = getattr(session, method, None)
    if not callable(request):
        raise KRXTransportError(f"injected KRX session does not implement {method}()")
    try:
        response = request(url, **kwargs)
    except Exception as exc:
        raise KRXTransportError(f"KRX {label} request failed") from exc
    _ensure_http_success(response, label)
    return response


def _json_payload(response: Any, *, label: str) -> Any:
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except (TypeError, ValueError):
            pass
    raw = _response_content(response)
    if not raw.strip():
        raise KRXResponseError(f"KRX {label} response was empty")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KRXResponseError(f"KRX {label} response was not valid JSON") from exc


def _json_bytes_payload(raw_bytes: bytes, *, label: str) -> Any:
    """Decode the exact bytes that will be persisted as the raw artifact."""
    if not raw_bytes.strip():
        raise KRXResponseError(f"KRX {label} response was empty")
    try:
        return json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KRXResponseError(f"KRX {label} response was not valid JSON") from exc


def _mapping_values(payload: Any, keys: set[str]) -> list[Any]:
    values: list[Any] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            normalized = re.sub(r"[\s_\-]+", "", str(key).casefold())
            if normalized in keys:
                values.append(value)
            if isinstance(value, Mapping):
                values.extend(_mapping_values(value, keys))
    elif isinstance(payload, list):
        for value in payload:
            values.extend(_mapping_values(value, keys))
    return values


def _value_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_response_date(value: Any) -> date | None:
    """Parse the bounded date spellings used by KRX response envelopes."""
    text = _value_text(value)
    if not text:
        return None
    candidates = (text, text.replace("/", "").replace(".", ""))
    for candidate in candidates:
        try:
            return _normalize_as_of(candidate)
        except KRXResponseError:
            continue
    try:
        parsed = pd.Timestamp(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is pd.NaT or bool(pd.isna(parsed)):
        return None
    return cast(pd.Timestamp, parsed).date()


def _response_date_markers(payload: Any) -> tuple[date, ...]:
    """Extract and validate every explicit date marker in a raw response."""
    values = _mapping_values(payload, _DATE_MARKER_KEYS)
    dates: list[date] = []
    for value in values:
        parsed = _normalize_response_date(value)
        if parsed is None:
            raise KRXResponseError("KRX response contains an invalid snapshot date marker")
        dates.append(parsed)
    unique = tuple(sorted(set(dates)))
    if len(unique) > 1:
        raise KRXResponseError("KRX response contains conflicting snapshot date markers")
    return unique


def _validate_response_date(payload: Mapping[str, Any], requested_date: date) -> tuple[date, ...]:
    echoed_dates = _response_date_markers(payload)
    if echoed_dates and echoed_dates[0] != requested_date:
        raise KRXResponseError("KRX response snapshot date does not match the requested date")
    return echoed_dates


def _is_kospi200_name(value: Any) -> bool:
    normalized = re.sub(r"\s+", "", _value_text(value)).casefold()
    return normalized in {"kospi200", "코스피200"}


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise KRXResponseError("KRX response must be a JSON object")
    candidates: list[Any] = []
    for key in ("output", "OutBlock_1", "OutBlock1", "outBlock_1", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.append(value)
        elif isinstance(value, Mapping):
            nested = value.get("output", value.get("rows", value.get("data")))
            if isinstance(nested, list):
                candidates.append(nested)
    if not candidates:
        result = payload.get("result")
        if isinstance(result, Mapping):
            for key in ("output", "data", "rows"):
                value = result.get(key)
                if isinstance(value, list):
                    candidates.append(value)
    if not candidates:
        raise KRXResponseError("KRX response did not contain an output row list")
    rows = candidates[0]
    if not rows:
        raise KRXResponseError("KRX KOSPI 200 response contained no rows")
    if not all(isinstance(row, Mapping) for row in rows):
        raise KRXResponseError("KRX output rows must be JSON objects")
    return [dict(row) for row in rows]


def _require_kospi200_evidence(
    payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
) -> None:
    """Reject a response that carries an explicit contradictory index marker.

    MDCSTAT00601 does not consistently echo all form fields in its JSON body.
    Therefore the exact endpoint query is itself retained as acquisition
    evidence, while any index code/type/name echoed by KRX must agree exactly.
    The required constituent columns additionally prevent an unrelated JSON
    success envelope from being accepted as a snapshot.
    """
    index_codes = _mapping_values(payload, _INDEX_CODE_KEYS)
    index_codes.extend(_mapping_values(rows, _INDEX_CODE_KEYS))
    if any(_value_text(value) != KOSPI200_INDEX_SELECTOR for value in index_codes):
        raise KRXResponseError("KRX response index evidence is not KOSPI 200 (028)")

    index_types = _mapping_values(payload, _INDEX_TYPE_KEYS)
    index_types.extend(_mapping_values(rows, _INDEX_TYPE_KEYS))
    if any(_value_text(value) != KOSPI200_INDEX_TYPE for value in index_types):
        raise KRXResponseError("KRX response index type evidence is not KOSPI 200 (1)")

    index_names = _mapping_values(payload, _INDEX_NAME_KEYS)
    index_names.extend(_mapping_values(rows, _INDEX_NAME_KEYS))
    if any(not _is_kospi200_name(value) for value in index_names):
        raise KRXResponseError("KRX response index name evidence is not KOSPI 200")

    if not all("ISU_SRT_CD" in row for row in rows):
        raise KRXResponseError("KRX response lacks KOSPI 200 constituent-code evidence")


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace(" ", "")
    if text in {"", "-", "—", "nan", "None", "null"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _first_value(row: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _security_code(value: Any) -> str | None:
    text = _value_text(value)
    if text.casefold().startswith("a"):
        text = text[1:]
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit() or not 1 <= len(text) <= 6:
        return None
    return text.zfill(6)


def _retrieval_timestamps() -> tuple[datetime, datetime]:
    utc = datetime.now(timezone.utc)
    seoul = utc.astimezone(ZoneInfo(KRX_TIMEZONE))
    return seoul, utc


def _normalize_rows(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    response_sha256: str,
    retrieved_at_utc: datetime,
) -> pd.DataFrame:
    normalized: list[dict[str, Any]] = []
    raw_columns: list[str] = []
    for source_row in raw_rows:
        ticker = _security_code(source_row.get("ISU_SRT_CD"))
        name = _value_text(source_row.get("ISU_ABBRV"))
        if ticker is None or not name:
            raise KRXResponseError("KRX response has an empty or invalid constituent field")
        sector = _first_value(source_row, ("SECUGRP_NM", "IDX_IND_NM", "MKT_NM"))
        record: dict[str, Any] = {
            "index_code": KOSPI200_INDEX_CODE,
            "as_of_date": as_of,
            "security_code": ticker,
            "name": name,
            "sector": None if sector is None else _value_text(sector) or None,
            "index_weight": _numeric(
                _first_value(source_row, ("IDX_WGT", "IDX_WGT_PCT", "INDEX_WEIGHT")),
            ),
            "index_shares": _numeric(
                _first_value(source_row, ("IDX_SHRS", "LIST_SHRS", "INDEX_SHARES")),
            ),
            "free_float": _numeric(
                _first_value(source_row, ("FREE_FLOAT", "FF_SHRS", "FREE_FLOAT_RATE")),
            ),
            "source_type": KRX_SOURCE_TYPE,
            "source_url": KRX_DATA_ENDPOINT,
            "source_file_sha256": response_sha256,
            "retrieved_at_utc": retrieved_at_utc,
        }
        for key, value in source_row.items():
            raw_key = str(key)
            if raw_key not in raw_columns:
                raw_columns.append(raw_key)
            if raw_key in record:
                record[f"krx_{raw_key}"] = value
            else:
                record[raw_key] = value
        normalized.append(record)

    columns = [*SNAPSHOT_COLUMNS]
    columns.extend(
        column
        for column in raw_columns
        if column not in columns
    )
    collision_columns = [
        f"krx_{column}"
        for column in raw_columns
        if any(f"krx_{column}" in record for record in normalized)
    ]
    columns.extend(
        column
        for column in collision_columns
        if column not in columns
    )
    result = pd.DataFrame.from_records(normalized, columns=columns)
    result.attrs.update({
        "krx_response_sha256": response_sha256,
        "krx_source_attested": True,
        "krx_index_code": KOSPI200_INDEX_SELECTOR,
        "krx_index_type": KOSPI200_INDEX_TYPE,
        "krx_retrieved_at_utc": retrieved_at_utc.isoformat(),
    })
    return result


def _response_error_code(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        for key in ("_error_code", "error_code", "errCode", "code"):
            value = payload.get(key)
            if value is not None:
                return str(value).strip()
        for value in payload.values():
            nested = _response_error_code(value)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for value in payload:
            nested = _response_error_code(value)
            if nested is not None:
                return nested
    return None


class KRXClient:
    """Small authenticated client for the official KRX endpoint."""

    def __init__(
        self,
        credentials: KRXCredentials | Mapping[str, Any] | tuple[str, str] | None = None,
        *,
        session: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0:
            raise ValueError("KRX timeout must be positive")
        self._credentials = credentials
        self.session = session if session is not None else _new_session()
        self.timeout = timeout
        self._logged_in = False

    def login(
        self,
        credentials: KRXCredentials | Mapping[str, Any] | tuple[str, str] | None = None,
    ) -> None:
        resolved = _resolve_credentials(self._credentials if credentials is None else credentials)
        _request(
            self.session,
            "get",
            KRX_LOGIN_PAGE_ENDPOINT,
            label="login bootstrap",
            headers=KRX_LOGIN_HEADERS,
            timeout=self.timeout,
        )
        _request(
            self.session,
            "get",
            KRX_LOGIN_FRAME_ENDPOINT,
            label="login frame bootstrap",
            headers=KRX_LOGIN_HEADERS,
            timeout=self.timeout,
        )
        payload = {
            "mbrNm": "",
            "telNo": "",
            "di": "",
            "certType": "",
            "mbrId": resolved.user_id,
            "pw": resolved.password,
        }
        response = _request(
            self.session,
            "post",
            KRX_LOGIN_ENDPOINT,
            label="login",
            data=payload,
            headers=KRX_LOGIN_HEADERS,
            timeout=self.timeout,
        )
        login_body = _json_payload(response, label="login")
        code = _response_error_code(login_body)
        if code == "CD011":
            retry_payload = dict(payload)
            retry_payload["skipDup"] = "Y"
            retry_response = _request(
                self.session,
                "post",
                KRX_LOGIN_ENDPOINT,
                label="duplicate-login retry",
                data=retry_payload,
                headers=KRX_LOGIN_HEADERS,
                timeout=self.timeout,
            )
            login_body = _json_payload(retry_response, label="duplicate-login retry")
            code = _response_error_code(login_body)
        if code != "CD001":
            # Deliberately report only the protocol code.  KRX messages can
            # contain echoed request data, and credentials must never appear in
            # an exception or log record.
            self._logged_in = False
            suffix = f" (code {code})" if code else ""
            raise KRXAuthenticationError(f"KRX login failed{suffix}")
        self._logged_in = True

    def fetch_snapshot(
        self,
        as_of: date | datetime | str,
        credentials: KRXCredentials | Mapping[str, Any] | tuple[str, str] | None = None,
    ) -> KRXSnapshotResponse:
        requested_date = _normalize_as_of(as_of)
        params = _snapshot_params(requested_date)
        if not self._logged_in:
            self.login(credentials)
        response = _request(
            self.session,
            "post",
            KRX_DATA_ENDPOINT,
            label="KOSPI 200 snapshot",
            data=params,
            headers=KRX_HEADERS,
            timeout=self.timeout,
        )
        raw_bytes = _response_content(response)
        if not raw_bytes.strip():
            raise KRXResponseError("KRX KOSPI 200 response was empty")
        # Parse the same bytes that download() writes.  Accepting a response
        # solely because ``response.json()`` returns a useful object would
        # create artifacts that the strict saved loader cannot reload.
        payload = _json_bytes_payload(raw_bytes, label="KOSPI 200 snapshot")
        if not isinstance(payload, Mapping):
            raise KRXResponseError("KRX response must be a JSON object")
        raw_rows = _extract_rows(payload)
        _require_kospi200_evidence(payload, raw_rows)
        echoed_dates = _validate_response_date(payload, requested_date)
        response_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        _seoul, retrieved_at_utc = _retrieval_timestamps()
        rows = _normalize_rows(
            raw_rows,
            as_of=requested_date,
            response_sha256=response_sha256,
            retrieved_at_utc=retrieved_at_utc,
        )
        rows.attrs["krx_query_params"] = dict(params)
        rows.attrs["krx_response_evidence"] = {
            "endpoint": KRX_DATA_ENDPOINT,
            "bld": KRX_SNAPSHOT_BLD,
            "indIdx2": KOSPI200_INDEX_SELECTOR,
            "indIdx": KOSPI200_INDEX_TYPE,
            "trdDd": params["trdDd"],
            "requested_as_of": requested_date.isoformat(),
            "echoed_dates": [value.isoformat() for value in echoed_dates],
            "date_binding": "echoed_response_date" if echoed_dates else "request_query_and_raw_sha256",
        }
        return KRXSnapshotResponse(raw_bytes=raw_bytes, rows=rows)

    # Concise alias for callers that treat the client as the adapter.
    fetch = fetch_snapshot


def fetch_krx_kospi200_snapshot(
    as_of: date | datetime | str,
    credentials: KRXCredentials | Mapping[str, Any] | tuple[str, str] | None = None,
    *,
    session: Any | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> KRXSnapshotResponse:
    """Fetch one KOSPI 200 snapshot without changing the default universe path."""
    client = KRXClient(credentials, session=session, timeout=timeout)
    return client.fetch_snapshot(as_of)


def _manifest_path(raw_path: Path) -> Path:
    return raw_path.with_name(raw_path.name + MANIFEST_SUFFIX)


def _artifact_date(raw_path: Path) -> date | None:
    match = re.fullmatch(r"krx_kospi200_(\d{8})\.json", raw_path.name)
    if match is None:
        return None
    try:
        return _normalize_as_of(match.group(1))
    except KRXResponseError:
        return None


def _manifest_hash(manifest: Mapping[str, Any]) -> str | None:
    for key in ("response_sha256", "raw_file_sha256", "source_file_sha256"):
        value = manifest.get(key)
        if isinstance(value, str) and SHA256_RE.fullmatch(value):
            return value.lower()
    return None


def _manifest_payload(
    *,
    as_of: date,
    raw_bytes: bytes,
    row_count: int,
    retrieved_at_seoul: datetime,
    retrieved_at_utc: datetime,
) -> dict[str, Any]:
    params = _snapshot_params(as_of)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    payload = _json_bytes_payload(raw_bytes, label="saved KOSPI 200 snapshot")
    if not isinstance(payload, Mapping):
        raise KRXResponseError("KRX response must be a JSON object")
    echoed_dates = _validate_response_date(payload, as_of)
    identity_payload = {
        "as_of_date": as_of.isoformat(),
        "query_params": params,
        "raw_sha256": digest,
    }
    identity_serialized = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot_identity = hashlib.sha256(identity_serialized.encode("utf-8")).hexdigest()
    return {
        "schema_version": KRX_SCHEMA_VERSION,
        "schema": list(SNAPSHOT_COLUMNS),
        "source_type": KRX_SOURCE_TYPE,
        "source_is_krx": True,
        "krx_source_attestation": {
            "source": "KRX",
            "verified": True,
            "index_code": KOSPI200_INDEX_SELECTOR,
            "index_type": KOSPI200_INDEX_TYPE,
        },
        "official_source_url": KRX_DATA_ENDPOINT,
        "source_url": KRX_DATA_ENDPOINT,
        "bld": KRX_SNAPSHOT_BLD,
        "query_params": dict(params),
        "date_params": {
            "as_of": as_of.isoformat(),
            "trdDd": params["trdDd"],
        },
        "response_evidence": {
            "index_code": KOSPI200_INDEX_SELECTOR,
            "index_type": KOSPI200_INDEX_TYPE,
            "query_contract": "MDCSTAT00601",
        },
        "response_date_evidence": {
            "requested_as_of": as_of.isoformat(),
            "echoed_dates": [value.isoformat() for value in echoed_dates],
            "date_binding": (
                "echoed_response_date"
                if echoed_dates
                else "request_query_and_raw_sha256"
            ),
        },
        "retrieved_at_seoul": retrieved_at_seoul.isoformat(),
        "retrieved_at_utc": retrieved_at_utc.isoformat(),
        "retrieval_timestamp": retrieved_at_utc.isoformat(),
        "response_sha256": digest,
        "raw_file_sha256": digest,
        "snapshot_identity_sha256": snapshot_identity,
        "row_count": row_count,
        "verified": True,
        "verification": "raw_bytes_sha256",
    }


def download_krx_kospi200_snapshots(
    dates: Iterable[date | datetime | str],
    output_dir: str | Path,
    credentials: KRXCredentials | Mapping[str, Any] | tuple[str, str] | None = None,
    *,
    session: Any | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[KRXSnapshotArtifact]:
    """Download raw KRX JSON and sidecar manifests for explicit dates.

    The response bytes are written exactly as received.  The SHA-256 appears
    only in the sidecar manifest; the raw JSON body is never rewritten with a
    self-referential hash.
    """
    normalized_dates = [_normalize_as_of(value) for value in dates]
    if not normalized_dates:
        raise KRXResponseError("at least one KRX snapshot date is required")
    if len(set(normalized_dates)) != len(normalized_dates):
        raise KRXResponseError("duplicate KRX snapshot dates are not allowed")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    client = KRXClient(credentials, session=session, timeout=timeout)
    artifacts: list[KRXSnapshotArtifact] = []
    for as_of in normalized_dates:
        response = client.fetch_snapshot(as_of)
        raw_path = destination / RAW_FILENAME_TEMPLATE.format(date=as_of.strftime("%Y%m%d"))
        manifest_path = _manifest_path(raw_path)
        retrieved_at_seoul, retrieved_at_utc = _retrieval_timestamps()
        manifest = _manifest_payload(
            as_of=as_of,
            raw_bytes=response.raw_bytes,
            row_count=len(response.rows),
            retrieved_at_seoul=retrieved_at_seoul,
            retrieved_at_utc=retrieved_at_utc,
        )
        raw_path.write_bytes(response.raw_bytes)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifacts.append(KRXSnapshotArtifact(
            as_of=as_of,
            raw_path=raw_path,
            manifest_path=manifest_path,
            row_count=len(response.rows),
            response_sha256=hashlib.sha256(response.raw_bytes).hexdigest(),
        ))
    return artifacts


def _read_manifest(manifest: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(manifest, Mapping):
        return dict(manifest)
    path = Path(manifest)
    if not path.is_file():
        raise KRXManifestError(f"KRX snapshot manifest does not exist: {path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KRXManifestError("KRX snapshot manifest is not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise KRXManifestError("KRX snapshot manifest must be a JSON object")
    return dict(parsed)


def _manifest_date(manifest: Mapping[str, Any]) -> date | None:
    dates: list[date] = []
    for container_name in ("date_params", "query_params"):
        container = manifest.get(container_name)
        if not isinstance(container, Mapping):
            continue
        for key in ("as_of", "trdDd"):
            if key in container:
                values = container[key] if isinstance(container[key], (list, tuple, set)) else (
                    container[key],
                )
                for value in values:
                    try:
                        dates.append(_normalize_as_of(value))
                    except KRXResponseError as exc:
                        raise KRXManifestError("KRX snapshot manifest date is invalid") from exc
    if not dates:
        return None
    if len(set(dates)) != 1:
        raise KRXManifestError("KRX snapshot manifest date parameters disagree")
    return dates[0]


def _manifest_timestamp(manifest: Mapping[str, Any], field: str) -> pd.Timestamp:
    value = manifest.get(field)
    if value is None:
        raise KRXManifestError(f"KRX snapshot manifest {field} is missing")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise KRXManifestError(f"KRX snapshot manifest {field} is invalid") from exc
    if timestamp is pd.NaT or bool(pd.isna(timestamp)) or timestamp.tzinfo is None:
        raise KRXManifestError(
            f"KRX snapshot manifest {field} must be timezone-aware"
        )
    return cast(pd.Timestamp, timestamp)


def _validate_manifest_timestamps(manifest: Mapping[str, Any]) -> None:
    utc = _manifest_timestamp(manifest, "retrieved_at_utc")
    seoul = _manifest_timestamp(manifest, "retrieved_at_seoul")
    utc_offset = utc.to_pydatetime().utcoffset()
    seoul_offset = seoul.to_pydatetime().utcoffset()
    if utc_offset != timedelta(0):
        raise KRXManifestError("KRX snapshot manifest retrieved_at_utc must be UTC")
    if seoul_offset != timedelta(hours=9):
        raise KRXManifestError(
            "KRX snapshot manifest retrieved_at_seoul must use Asia/Seoul offset"
        )
    if seoul.tz_convert("UTC") != utc:
        raise KRXManifestError("KRX snapshot manifest UTC/Seoul timestamps disagree")
    for alias in ("retrieval_timestamp", "retrieved_at", "retrieval_time"):
        if alias in manifest and _manifest_timestamp(manifest, alias).tz_convert("UTC") != utc:
            raise KRXManifestError(
                f"KRX snapshot manifest retrieval timestamp alias {alias} disagrees"
            )


def _validate_krx_manifest(
    manifest: Mapping[str, Any],
    *,
    raw_bytes: bytes,
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    as_of: date | None,
    require_snapshot_identity: bool = False,
) -> date:
    if manifest.get("schema_version") != KRX_SCHEMA_VERSION:
        raise KRXManifestError("KRX snapshot manifest schema/version is missing or mismatched")
    if manifest.get("source_type") != KRX_SOURCE_TYPE:
        raise KRXManifestError("KRX snapshot manifest source_type is mismatched")
    if manifest.get("source_is_krx") is not True:
        raise KRXManifestError("KRX snapshot manifest lacks KRX source attestation")
    attestation = manifest.get("krx_source_attestation")
    if not isinstance(attestation, Mapping) or attestation.get("source") != "KRX":
        raise KRXManifestError("KRX snapshot manifest lacks explicit KRX attestation")
    if attestation.get("verified") is not True:
        raise KRXManifestError("KRX source attestation is not verified")
    if (
        attestation.get("index_code") != KOSPI200_INDEX_SELECTOR
        or attestation.get("index_type") != KOSPI200_INDEX_TYPE
    ):
        raise KRXManifestError("KRX source attestation is not for KOSPI 200")
    response_evidence = manifest.get("response_evidence")
    if response_evidence != {
        "index_code": KOSPI200_INDEX_SELECTOR,
        "index_type": KOSPI200_INDEX_TYPE,
        "query_contract": "MDCSTAT00601",
    }:
        raise KRXManifestError("KRX snapshot response evidence is missing or mismatched")
    if manifest.get("schema") != list(SNAPSHOT_COLUMNS):
        raise KRXManifestError("KRX snapshot manifest schema is missing or mismatched")
    for key, expected in (
        ("official_source_url", KRX_DATA_ENDPOINT),
        ("source_url", KRX_DATA_ENDPOINT),
        ("bld", KRX_SNAPSHOT_BLD),
    ):
        if manifest.get(key) != expected:
            raise KRXManifestError(f"KRX snapshot manifest {key} is mismatched")

    params = manifest.get("query_params")
    if not isinstance(params, Mapping):
        raise KRXManifestError("KRX snapshot manifest query_params are missing")
    manifest_date = _manifest_date(manifest)
    if as_of is not None and manifest_date is not None and manifest_date != as_of:
        raise KRXManifestError("KRX snapshot manifest date does not match requested date")
    expected_date = as_of or manifest_date
    if expected_date is None:
        raise KRXManifestError("KRX snapshot manifest date parameters are missing")
    expected_params = _snapshot_params(expected_date)
    if dict(params) != expected_params:
        raise KRXManifestError("KRX snapshot manifest query parameters are mismatched")
    date_params = manifest.get("date_params")
    if not isinstance(date_params, Mapping) or dict(date_params) != {
        "as_of": expected_date.isoformat(),
        "trdDd": expected_params["trdDd"],
    }:
        raise KRXManifestError("KRX snapshot manifest date parameters are mismatched")

    digest = hashlib.sha256(raw_bytes).hexdigest()
    declared_hashes = [
        manifest.get(key)
        for key in ("response_sha256", "raw_file_sha256", "source_file_sha256")
        if key in manifest
    ]
    if (
        not declared_hashes
        or any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in declared_hashes)
        or any(str(value).lower() != digest for value in declared_hashes)
    ):
        raise KRXManifestError("KRX snapshot manifest SHA-256 does not match raw bytes")
    identity_payload = {
        "as_of_date": expected_date.isoformat(),
        "query_params": expected_params,
        "raw_sha256": digest,
    }
    identity_serialized = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_identity = hashlib.sha256(identity_serialized.encode("utf-8")).hexdigest()
    declared_identity = manifest.get("snapshot_identity_sha256")
    if declared_identity is None and require_snapshot_identity:
        raise KRXManifestError("KRX snapshot date/raw identity is missing")
    if declared_identity is not None and declared_identity != expected_identity:
        raise KRXManifestError("KRX snapshot date/raw identity is missing or mismatched")
    if manifest.get("verified") is not True:
        raise KRXManifestError("KRX snapshot manifest is not marked verified")
    row_count = manifest.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count != len(rows):
        raise KRXManifestError("KRX snapshot manifest row_count is mismatched")
    _validate_manifest_timestamps(manifest)
    echoed_dates = _validate_response_date(payload, expected_date)
    expected_date_evidence = {
        "requested_as_of": expected_date.isoformat(),
        "echoed_dates": [value.isoformat() for value in echoed_dates],
        "date_binding": (
            "echoed_response_date"
            if echoed_dates
            else "request_query_and_raw_sha256"
        ),
    }
    declared_date_evidence = manifest.get("response_date_evidence")
    if declared_date_evidence is not None and declared_date_evidence != expected_date_evidence:
        raise KRXManifestError("KRX snapshot response date evidence is missing or mismatched")
    _require_kospi200_evidence(payload, rows)
    return expected_date


def load_krx_kospi200_snapshot(
    raw_path: str | Path,
    manifest: Mapping[str, Any] | str | Path | None = None,
    *,
    as_of: date | datetime | str | None = None,
    target_size: int | None = 200,
) -> pd.DataFrame:
    """Load one saved raw response after strict sidecar verification.

    Normalization and the existing PIT validator remain in ``pit_universe``;
    this wrapper only verifies the KRX-specific envelope and manifest before
    delegating to them.
    """
    source = Path(raw_path)
    if not source.is_file():
        raise KRXManifestError(f"KRX raw snapshot does not exist: {source}")
    raw_bytes = source.read_bytes()
    if not raw_bytes.strip():
        raise KRXResponseError("KRX raw snapshot is empty")
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KRXResponseError("KRX raw snapshot is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise KRXResponseError("KRX raw snapshot must be a JSON object")
    rows = _extract_rows(payload)
    supplied_as_of = (
        _normalize_as_of(as_of)
        if as_of is not None
        else _artifact_date(source)
    )
    artifact_date = _artifact_date(source)

    sidecar: Mapping[str, Any] | str | Path
    if manifest is None:
        sidecar = _manifest_path(source)
        if not Path(sidecar).is_file():
            raise KRXManifestError("KRX raw snapshot is missing its sidecar manifest")
    else:
        sidecar = manifest
    manifest_data = _read_manifest(sidecar)
    verified_as_of = _validate_krx_manifest(
        manifest_data,
        raw_bytes=raw_bytes,
        payload=payload,
        rows=rows,
        as_of=supplied_as_of,
        require_snapshot_identity=artifact_date is None and as_of is None,
    )

    try:
        normalized = load_constituent_snapshots(
            source,
            source_format="json",
            column_mapping={
                "security_code": "ISU_SRT_CD",
                "name": "ISU_ABBRV",
            },
            acquisition_manifest=sidecar,
            default_index_code=KOSPI200_INDEX_CODE,
            default_as_of_date=verified_as_of,
        )
    except (PITUniverseError, ValueError, TypeError) as exc:
        raise KRXResponseError("KRX raw snapshot could not be normalized") from exc

    report = validate_constituent_snapshots(
        normalized,
        requested_rebalance_date=verified_as_of,
        target_size=target_size,
    )
    if not report.pit_valid:
        raise KRXManifestError("KRX snapshot failed existing PIT validation: " + "; ".join(report.errors))
    return normalized


def load_krx_kospi200_snapshots(
    input_dir: str | Path,
    dates: Iterable[date | datetime | str] | None = None,
    *,
    target_size: int | None = 200,
) -> pd.DataFrame:
    """Load and validate multiple downloaded KRX snapshots from a directory."""
    directory = Path(input_dir)
    if dates is None:
        paths = sorted(
            path for path in directory.glob("krx_kospi200_*.json")
            if not path.name.endswith(MANIFEST_SUFFIX) and _artifact_date(path) is not None
        )
        requested_dates: list[date] = [cast(date, _artifact_date(path)) for path in paths]
    else:
        parsed_dates = [_normalize_as_of(value) for value in dates]
        if len(set(parsed_dates)) != len(parsed_dates):
            raise KRXManifestError("duplicate KRX snapshot dates are not allowed")
        requested_dates = list(parsed_dates)
        paths = [
            directory / RAW_FILENAME_TEMPLATE.format(date=value.strftime("%Y%m%d"))
            for value in parsed_dates
        ]
    if not paths:
        raise KRXManifestError("no KRX KOSPI 200 raw snapshots were found")
    if len(set(requested_dates)) != len(requested_dates):
        raise KRXManifestError("duplicate KRX snapshot dates are not allowed")
    frames = [
        load_krx_kospi200_snapshot(path, as_of=expected_date, target_size=target_size)
        for path, expected_date in zip(paths, requested_dates, strict=True)
    ]
    combined = pd.concat(frames, ignore_index=True)

    # ``pd.concat`` drops attrs when individually loaded frames have different
    # sidecar manifests.  Reattach every verified token and manifest instead of
    # selecting one raw hash as if it covered all concatenated bytes.
    first_attrs = frames[0].attrs
    source_hashes = {
        str(value).casefold()
        for frame in frames
        for value in frame["source_file_sha256"].dropna().tolist()
    }
    first_manifest = first_attrs.get("acquisition_manifest")
    combined.attrs = dict(first_attrs)
    manifests_by_as_of: dict[str, Mapping[str, Any]] = {}
    frame_tokens: list[Any] = []
    manifests_valid = True
    for frame, expected_date in zip(frames, requested_dates, strict=True):
        frame_manifest = frame.attrs.get("acquisition_manifest")
        typed_manifest = (
            cast(Mapping[str, Any], frame_manifest)
            if isinstance(frame_manifest, Mapping) else None
        )
        frame_token = frame.attrs.get("_verified_acquisition")
        frame_dates = {
            value.isoformat()
            for value in frame["as_of_date"].dropna().tolist()
            if isinstance(value, date)
        }
        frame_hashes = {
            str(value).casefold()
            for value in frame["source_file_sha256"].dropna().tolist()
        }
        token_manifest = getattr(getattr(frame_token, "manifest", None), "as_dict", None)
        token_manifest_data = token_manifest() if callable(token_manifest) else None
        token_date = getattr(getattr(frame_token, "manifest", None), "as_of_date", None)
        if token_date is None and isinstance(token_manifest_data, Mapping):
            token_date = _manifest_date(token_manifest_data)
        token_hash = getattr(frame_token, "raw_sha256", None)
        valid_frame = (
            frame_dates == {expected_date.isoformat()}
            and typed_manifest is not None
            and frame_token is not None
            and len(frame_hashes) == 1
            and token_hash in frame_hashes
            and getattr(getattr(frame_token, "manifest", None), "source_file_sha256", None)
            in frame_hashes
            and token_date == expected_date
            and isinstance(token_manifest_data, Mapping)
            and dict(token_manifest_data) == dict(typed_manifest)
        )
        if valid_frame and typed_manifest is not None and frame_token is not None:
            key = expected_date.isoformat()
            previous = manifests_by_as_of.get(key)
            if previous is not None and dict(previous) != dict(typed_manifest):
                valid_frame = False
            else:
                manifests_by_as_of[key] = dict(typed_manifest)
            frame_tokens.append(frame_token)
        manifests_valid = manifests_valid and valid_frame

    combined.attrs["source_file_sha256"] = sorted(source_hashes)
    combined.attrs["acquisition_manifests_by_as_of"] = manifests_by_as_of
    all_verified = (
        manifests_valid
        and len(manifests_by_as_of) == len(frames)
        and len(frame_tokens) == len(frames)
        and all(frame.attrs.get("acquisition_manifest_verified") is True for frame in frames)
    )
    if all_verified:
        normalized_fingerprint = fingerprint_dataframe(combined)
        verified_tokens = tuple(
            replace(token, normalized_fingerprint=normalized_fingerprint)
            for token in frame_tokens
        )
        combined.attrs["_verified_acquisition"] = (
            verified_tokens[0] if len(verified_tokens) == 1 else verified_tokens
        )
        combined.attrs["acquisition_manifest"] = (
            first_manifest
            if len(manifests_by_as_of) == 1
            else {
                "manifest_type": "multiple_verified_raw_snapshots",
                "manifests_by_as_of": manifests_by_as_of,
            }
        )
        combined.attrs["acquisition_manifest_verified"] = True
        combined_report = validate_constituent_snapshots(
            combined,
            requested_rebalance_dates=requested_dates,
            target_size=target_size,
        )
        if not combined_report.pit_valid:
            raise KRXManifestError(
                "KRX multi-snapshot provenance validation failed: "
                + "; ".join(combined_report.errors)
            )
        # This flag is deliberately not evidence.  The public validator must
        # recompute provenance from the final rows and tokens.
        combined.attrs["pit_valid"] = False
    else:
        raise KRXManifestError(
            "KRX multi-snapshot manifests/tokens do not cover each requested date"
        )
    return combined


# Descriptive aliases for callers wiring the adapter into a future acquisition
# job.  No alias is imported by ``universe.py``.
fetch_krx_kospi200 = fetch_krx_kospi200_snapshot
download_krx_kospi200 = download_krx_kospi200_snapshots
load_krx_snapshot = load_krx_kospi200_snapshot


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "KOSPI200_BLD",
    "KOSPI200_CODE",
    "KOSPI200_INDEX_CODE",
    "KOSPI200_INDEX_SELECTOR",
    "KOSPI200_INDEX_TYPE",
    "KOSPI200_IND_IDX",
    "KOSPI200_IND_IDX2",
    "KOSPI_200_CODE",
    "KRXAuthenticationError",
    "KRXClient",
    "KRXCredentialError",
    "KRXCredentials",
    "KRX_DATA_ENDPOINT",
    "KRX_ENDPOINTS",
    "KRX_HEADERS",
    "KRX_LOGIN_ENDPOINT",
    "KRX_LOGIN_PAGE_ENDPOINT",
    "KRX_LOGIN_SUBMIT_URL",
    "KRX_LOGIN_URL",
    "KRXManifestError",
    "KRX_OFFICIAL_DATA_URL",
    "KRXPITError",
    "KRXResponseError",
    "KRX_SCHEMA_VERSION",
    "KRX_SNAPSHOT_BLD",
    "KRX_SNAPSHOT_ENDPOINT",
    "KRX_SOURCE_TYPE",
    "KRXSnapshotArtifact",
    "KRXSnapshotResponse",
    "download_krx_kospi200",
    "download_krx_kospi200_snapshots",
    "fetch_krx_kospi200",
    "fetch_krx_kospi200_snapshot",
    "load_krx_kospi200_snapshot",
    "load_krx_kospi200_snapshots",
    "load_krx_snapshot",
]
