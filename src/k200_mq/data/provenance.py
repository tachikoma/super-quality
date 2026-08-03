"""Data-validity helpers for the K200MQ input contract.

These helpers intentionally distinguish fiscal-period labels from dates on
which information became public.  A fiscal quarter end is not a filing date
and must never be promoted to one by inference.

Financial PIT callers must attach ``DataFrame.attrs["financial_provenance_contract"]``
with a non-empty ``source`` and a ``schema`` mapping whose selected field is
declared as filing/publication availability.  Date-only or timezone-naive
values require ``availability_policy="next_session"``.  A timezone-aware
timestamp may instead use an explicit source timezone and exchange cutoff;
after-cutoff values are mapped to the next trading session.  A parseable
column, including one named ``filing_timestamp`` or ``report_date``, is
otherwise non-PIT.

The local DART importer is stricter than this provider-neutral contract:
session-cutoff timestamps remain deferred until their raw filing lineage is
attested by the verified manifest and normalized-frame evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
import hashlib
import json
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

NON_PIT_FINANCIAL_MODE = "non_pit_fiscal_period"
PIT_FINANCIAL_MODE = "pit_filing_date"

FILING_DATE_FIELDS = (
    "filing_date",
    "publication_date",
    "filed_at",
    "published_at",
    "rcept_dt",
    # ``report_date`` is retained as a candidate field only so a provider can
    # explicitly declare that it means filing availability.  It is never
    # trusted from its name alone.
    "report_date",
    "공시일",
    "접수일",
)

# These names are eligible for a timestamp contract.  A midnight value still
# needs the explicit conservative next-session policy below; the field name
# alone never proves intraday availability.
TIMESTAMP_FIELDS = (
    "filing_timestamp",
    "filing_datetime",
    "availability_timestamp",
    "availability_datetime",
    "cutoff_timestamp",
    "cutoff_datetime",
    "filed_at",
    "published_at",
)

# Explicit cutoff fields are eligible only with the same source/schema contract
# and timestamp/policy checks.  ``effective_date`` is intentionally absent: it
# is reserved for universe membership metadata.
CUTOFF_FIELDS = (
    "availability_date",
    "available_from",
    "cutoff_date",
)

FINANCIAL_PROVENANCE_CONTRACT_ATTR = "financial_provenance_contract"
NEXT_SESSION_POLICY = "next_session"

# Universe provenance is kept here with the financial provenance validator so
# callers that only validate already-prepared frames do not need to import the
# cache-backed universe loader.
LEGACY_PROXY_UNKNOWN = "legacy_proxy_unknown"
PIT_EFFECTIVE_DATE_CONTRACT = "constituents_effective_on_or_before_as_of"
PIT_SCHEMA_CONTRACT = {
    "as_of": "date",
    "ticker": "string",
}
PROXY_CONTRACTS = {
    "proxy_current": "current_listing_ignores_as_of",
    "mcap_proxy": "current_market_cap_snapshot",
}
_PIT_SOURCE_TYPES = {"krx_official_snapshot", "krx_official_event"}


def _constituent_fingerprint(tickers: list[str]) -> str:
    """Return a reproducible fingerprint for one constituent set."""
    canonical = sorted({str(ticker) for ticker in tickers})
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_evidence_value(value: Any) -> Any:
    """Convert provenance evidence to a deterministic JSON-compatible value."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_evidence_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_evidence_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_evidence_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _history_evidence_fingerprint(
    history_data: pd.DataFrame,
    provenance_by_as_of: Mapping[str, Any],
    metadata_by_as_of: Mapping[str, Any],
    acquisition_manifest: Mapping[str, Any] | None,
    source_file_sha256: str | None,
) -> str | None:
    """Fingerprint the materialized history and all evidence that authorizes it."""
    if not isinstance(history_data, pd.DataFrame):
        return None
    if not isinstance(provenance_by_as_of, Mapping) or not isinstance(metadata_by_as_of, Mapping):
        return None
    if not isinstance(acquisition_manifest, Mapping) or not source_file_sha256:
        return None
    columns = tuple(str(column) for column in history_data.columns)
    rows = [
        [_canonical_evidence_value(value) for value in row]
        for row in history_data.loc[:, list(columns)].itertuples(index=False, name=None)
    ]
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
    payload = {
        "history_columns": list(columns),
        "history_rows": rows,
        "provenance_by_as_of": _canonical_evidence_value(provenance_by_as_of),
        "provenance_metadata_by_as_of": _canonical_evidence_value(metadata_by_as_of),
        "acquisition_manifest": _canonical_evidence_value(acquisition_manifest),
        "source_file_sha256": str(source_file_sha256).casefold(),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _metadata_label(metadata: Any) -> str | None:
    """Read a label only from a structured provenance record."""
    if not isinstance(metadata, dict):
        return None
    label = metadata.get("label", metadata.get("provenance"))
    return str(label) if isinstance(label, str) else None


def _metadata_matches(
    metadata: Any,
    label: str,
    as_of: Any,
    tickers: list[str],
) -> bool:
    """Validate a constituent provenance record without loading any data."""
    if not isinstance(metadata, dict) or _metadata_label(metadata) != label:
        return False
    if not isinstance(metadata.get("source"), str) or not metadata["source"].strip():
        return False
    if label == "pit":
        schema = metadata.get("schema")
        schema_valid = (
            isinstance(schema, str) and bool(schema.strip())
        ) or (
            isinstance(schema, Mapping)
            and bool(schema)
            and all(
                isinstance(key, str) and str(value).strip()
                for key, value in schema.items()
            )
        )
        if not schema_valid:
            return False
    if "effective_date" not in metadata:
        return False
    try:
        effective_date = pd.Timestamp(metadata["effective_date"])
        if not isinstance(effective_date, pd.Timestamp) or pd.isna(effective_date):
            return False
        effective_day = effective_date.to_pydatetime().date()
    except (TypeError, ValueError, OverflowError):
        return False
    if as_of is not None and effective_day > as_of:
        return False
    expected_fingerprint = _constituent_fingerprint(tickers)
    if metadata.get("fingerprint") not in {
        expected_fingerprint,
        f"sha256:{expected_fingerprint}",
    }:
        return False
    if label == "pit":
        # ``pit`` is only a classification.  It is not itself a source or a
        # contract, so a sidecar containing just that string is never enough.
        if metadata["source"] in {
            "pit", "proxy_current", "mcap_proxy", LEGACY_PROXY_UNKNOWN,
        }:
            return False
        manifest = metadata.get("acquisition_manifest")
        if not isinstance(manifest, Mapping):
            return False
        source_url = manifest.get("source_url", manifest.get("official_source_url"))
        parsed_url = urlparse(source_url) if isinstance(source_url, str) else None
        host = parsed_url.hostname.casefold() if parsed_url and parsed_url.hostname else ""
        if (
            not parsed_url
            or parsed_url.scheme.casefold() != "https"
            or not (host == "krx.co.kr" or host.endswith(".krx.co.kr"))
        ):
            return False
        if manifest.get("source_type") not in _PIT_SOURCE_TYPES:
            return False
        if manifest.get("source_is_krx") is not True or manifest.get("verified") is not True:
            return False
        raw_hash = manifest.get("raw_file_sha256", manifest.get("source_file_sha256"))
        if not isinstance(raw_hash, str) or metadata.get("source_file_sha256") != raw_hash:
            return False
        try:
            retrieved = pd.Timestamp(manifest.get("retrieved_at_utc"))
        except (TypeError, ValueError, OverflowError):
            return False
        if pd.isna(retrieved) or retrieved.tzinfo is None:
            return False
        return metadata.get("contract") == PIT_EFFECTIVE_DATE_CONTRACT
    return label in PROXY_CONTRACTS and metadata.get("contract") == PROXY_CONTRACTS[label]


def _verified_acquisition_matches(
    evidence: Any,
    metadata: Mapping[str, Any],
    *,
    history_data: pd.DataFrame | None = None,
    provenance_by_as_of: Mapping[str, Any] | None = None,
    metadata_by_as_of: Mapping[str, Any] | None = None,
    acquisition_manifest: Mapping[str, Any] | None = None,
) -> bool:
    """Recognize only importer-issued evidence bound to this history."""
    if evidence is None:
        return False
    evidence_type = type(evidence)
    if (
        evidence_type.__name__ != "_VerifiedAcquisition"
        or evidence_type.__module__ != "k200_mq.data.pit_universe"
    ):
        return False
    manifest = getattr(evidence, "manifest", None)
    raw_sha = getattr(evidence, "raw_sha256", None)
    expected = metadata.get("source_file_sha256")
    manifest_sha = getattr(manifest, "source_file_sha256", None)
    if not (isinstance(raw_sha, str) and raw_sha == expected == manifest_sha):
        return False
    if history_data is None:
        return True
    if (
        provenance_by_as_of is None
        or metadata_by_as_of is None
        or acquisition_manifest is None
    ):
        return False
    expected_history_fingerprint = _history_evidence_fingerprint(
        history_data,
        provenance_by_as_of,
        metadata_by_as_of,
        acquisition_manifest,
        raw_sha,
    )
    return getattr(evidence, "history_fingerprint", None) == expected_history_fingerprint


def validate_universe_provenance(universe_history: object) -> dict[str, Any]:
    """Report whether a prepared universe history is PIT-valid.

    This validator intentionally depends only on the supplied frame and its
    provenance metadata.  It must remain safe to import from an interval
    executor: no universe loader, cache, filesystem, or network module is
    imported here.

    A missing provenance attribute is deliberately treated as invalid.  This
    prevents an arbitrary or legacy DataFrame from being upgraded to PIT data
    merely because its columns are named ``as_of`` and ``ticker``.
    """
    if isinstance(universe_history, pd.DataFrame):
        history_data = universe_history
        history_attrs = universe_history.attrs
        raw_sources = universe_history.attrs.get(
            "provenance_by_as_of",
            universe_history.attrs.get("source_by_as_of", {}),
        )
        provenance = str(
            universe_history.attrs.get(
                "provenance",
                universe_history.attrs.get("source", "unknown"),
            )
        )
        raw_metadata = universe_history.attrs.get("provenance_metadata_by_as_of", {})
        acquisition_evidence = universe_history.attrs.get("_verified_acquisition")
    else:
        # ``UniverseHistoryResult`` lives in the cache-backed universe module.
        # Reading its structural fields instead of importing that class keeps
        # this validator independent from the loader and preserves compatibility
        # with the result object accepted by the public validator API.
        history_data = getattr(universe_history, "data")
        history_attrs = history_data.attrs
        raw_sources = getattr(universe_history, "provenance_by_as_of")
        provenance = getattr(universe_history, "provenance")
        raw_metadata = getattr(universe_history, "provenance_metadata_by_as_of")
        acquisition_evidence = getattr(
            universe_history,
            "_verified_acquisition",
            history_data.attrs.get("_verified_acquisition"),
        )

    def _date_key(value: Any) -> str | None:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if pd.isna(timestamp):
            return None
        return timestamp.date().isoformat()

    history_dates: set[str] = set()
    invalid_as_of = (
        "as_of" not in history_data.columns
        or "ticker" not in history_data.columns
    )
    tickers_by_date: dict[str, list[str]] = {}
    if "as_of" in history_data.columns:
        for index, value in enumerate(history_data["as_of"].tolist()):
            key = _date_key(value)
            if key is None:
                invalid_as_of = True
                continue
            history_dates.add(key)
            if "ticker" in history_data.columns:
                ticker = history_data.iloc[index]["ticker"]
                if pd.notna(ticker):
                    tickers_by_date.setdefault(key, []).append(str(ticker))

    # Normalize caller keys to the same canonical date representation as the
    # history.  Unknown and duplicate keys are retained as invalid evidence;
    # silently dropping them would allow an aggregate or malformed claim to
    # look complete.
    supplied_sources: dict[str, str] = {}
    duplicate_source_keys = False
    if isinstance(raw_sources, Mapping):
        for raw_key, raw_source in raw_sources.items():
            key = str(raw_key) if raw_key == "history" else _date_key(raw_key)
            if key is None:
                key = str(raw_key)
            if key in supplied_sources:
                duplicate_source_keys = True
                supplied_sources[key] = LEGACY_PROXY_UNKNOWN
            else:
                supplied_sources[key] = str(raw_source)

    if not supplied_sources and provenance in {
        "proxy_current", "mcap_proxy", LEGACY_PROXY_UNKNOWN,
    }:
        supplied_sources = {key: provenance for key in sorted(history_dates)}

    metadata_by_as_of: dict[str, dict[str, Any]] = {}
    duplicate_metadata_keys = False
    if isinstance(raw_metadata, Mapping):
        for raw_key, metadata in raw_metadata.items():
            key = str(raw_key) if raw_key == "history" else _date_key(raw_key)
            if key is None:
                key = str(raw_key)
            if key in metadata_by_as_of:
                duplicate_metadata_keys = True
                continue
            if isinstance(metadata, dict):
                metadata_by_as_of[key] = metadata

    acquisition_manifest = history_attrs.get("acquisition_manifest")

    # A PIT label without a per-date key is never a valid history claim.  In
    # particular, ``{"history": "pit"}`` is an aggregate assertion and must
    # not be used as a fallback for every row in the history.
    aggregate_pit_claim = (
        supplied_sources.get("history") == "pit"
        or _metadata_label(metadata_by_as_of.get("history")) == "pit"
    )

    resolved_sources: dict[str, str] = {}
    for key in sorted(history_dates):
        source = supplied_sources.get(key, LEGACY_PROXY_UNKNOWN)
        resolved_source = source
        if source == "pit":
            as_of = pd.Timestamp(key).date()
            metadata = metadata_by_as_of.get(key)
            tickers = tickers_by_date.get(key, [])
            if (
                not _metadata_matches(metadata, "pit", as_of, tickers)
                or not isinstance(metadata, Mapping)
                or not _verified_acquisition_matches(
                    acquisition_evidence,
                    metadata,
                    history_data=history_data,
                    provenance_by_as_of=supplied_sources,
                    metadata_by_as_of=metadata_by_as_of,
                    acquisition_manifest=acquisition_manifest,
                )
            ):
                resolved_source = LEGACY_PROXY_UNKNOWN
        elif source not in {"proxy_current", "mcap_proxy", LEGACY_PROXY_UNKNOWN}:
            resolved_source = LEGACY_PROXY_UNKNOWN
        resolved_sources[key] = resolved_source

    # Preserve non-date/aggregate evidence in the result so callers can see
    # why a supplied claim was rejected.  These entries also make the exact
    # per-date coverage check below fail closed.
    for key, source in supplied_sources.items():
        if key not in history_dates:
            resolved_sources[key] = (
                LEGACY_PROXY_UNKNOWN if source == "pit" else str(source)
            )

    provenance_by_as_of = resolved_sources
    unique_sources = set(provenance_by_as_of.values())
    if len(unique_sources) == 1:
        provenance = next(iter(unique_sources))
    elif len(unique_sources) > 1:
        provenance = "mixed"

    exact_date_coverage = (
        bool(history_dates)
        and set(supplied_sources) == history_dates
        and set(metadata_by_as_of) == history_dates
        and not invalid_as_of
        and not duplicate_source_keys
        and not duplicate_metadata_keys
        and not aggregate_pit_claim
    )
    pit_valid = exact_date_coverage and all(
        provenance_by_as_of.get(key) == "pit" for key in history_dates
    )
    if pit_valid:
        reason = "all constituent sets have PIT provenance"
    elif not provenance_by_as_of:
        reason = "universe provenance is missing"
    elif LEGACY_PROXY_UNKNOWN in unique_sources:
        reason = "universe provenance metadata is missing or untrusted"
    else:
        reason = "universe uses a current-list or market-cap proxy, not PIT data"

    return {
        "provenance": provenance,
        "source": provenance,
        "provenance_by_as_of": provenance_by_as_of,
        "provenance_metadata_by_as_of": metadata_by_as_of,
        "pit_valid": pit_valid,
        "reason": reason,
    }


def _get_provenance_contract(
    data: pd.DataFrame,
    provenance_contract: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    """Return the caller-supplied source/schema contract, if present.

    A parseable column is deliberately not a contract.  The canonical place
    for a contract is ``DataFrame.attrs[financial_provenance_contract]``.  A
    couple of explicit aliases are accepted for callers that already keep
    generic provenance metadata in ``attrs``; all of them still require a
    source and a schema declaration below.
    """
    if provenance_contract is not None:
        return provenance_contract

    attrs = getattr(data, "attrs", {})
    if not isinstance(attrs, Mapping):
        return None
    for key in (
        FINANCIAL_PROVENANCE_CONTRACT_ATTR,
        "provenance_contract",
        "financial_contract",
    ):
        candidate = attrs.get(key)
        if isinstance(candidate, Mapping):
            return candidate

    # Also support attrs that expose the two contract parts directly.  This
    # remains explicit: both ``source`` and ``schema`` must be present.
    if isinstance(attrs.get("source"), str) and isinstance(attrs.get("schema"), Mapping):
        return {
            "source": attrs["source"],
            "schema": attrs["schema"],
            "availability_policy": attrs.get("availability_policy"),
        }
    return None


def _contract_for_field(
    data: pd.DataFrame,
    field: str,
    provenance_contract: Mapping[str, Any] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Validate and describe the source/schema declaration for *field*."""
    contract = _get_provenance_contract(data, provenance_contract)
    if contract is None:
        return False, None, None

    source = contract.get("source")
    schema = contract.get("schema")
    if not isinstance(source, str) or not source.strip() or not isinstance(schema, Mapping):
        return False, None, None

    field_schema = schema.get(field)
    if isinstance(field_schema, Mapping):
        semantic_parts = [
            field_schema.get(name, "")
            for name in (
                "role", "semantic", "meaning", "description", "availability", "type",
            )
        ]
        semantic = " ".join(str(part) for part in semantic_parts if part)
    elif isinstance(field_schema, str):
        semantic = field_schema
    else:
        return False, None, None

    # This declaration is what distinguishes a true filing/publication
    # availability value from a fiscal/report period label.
    semantic_lower = semantic.casefold()
    availability_terms = ("filing", "filed", "publication", "published", "availability")
    schema_type_terms = ("timestamp", "datetime", "date")
    has_availability_semantics = any(term in semantic_lower for term in availability_terms)
    has_explicit_datetime_type = any(term in semantic_lower for term in schema_type_terms)
    known_non_period_field = field != "report_date" and (
        field in (*TIMESTAMP_FIELDS, *CUTOFF_FIELDS, *FILING_DATE_FIELDS)
    )
    if not has_availability_semantics and not (
        known_non_period_field and has_explicit_datetime_type
    ):
        return False, None, None

    raw_policy = contract.get("availability_policy", contract.get("date_policy"))
    if isinstance(raw_policy, Mapping):
        raw_policy = raw_policy.get("name", raw_policy.get("policy"))
    policy = str(raw_policy).strip().casefold() if raw_policy is not None else None
    policy_aliases = {
        "next-session": NEXT_SESSION_POLICY,
        "next_session": NEXT_SESSION_POLICY,
        "conservative_next_session": NEXT_SESSION_POLICY,
        "cutoff": "session_cutoff",
        "close_cutoff": "session_cutoff",
        "session_cutoff": "session_cutoff",
        "same_session": "session_cutoff",
    }
    normalized_policy = None if policy is None else policy_aliases.get(policy, policy)
    return True, normalized_policy, source.strip()


def normalize_filing_timestamp(value: Any) -> pd.Timestamp | None:
    """Normalize one supported date-like value to a scalar ``Timestamp``.

    Normalizing scalar values individually avoids pandas' mixed-naive/mixed-
    timezone inference differences between strings, ``datetime`` objects, and
    ``pandas.Timestamp`` objects.  Timezone-aware values are converted to UTC;
    naive values remain naive so validation can reject them unless the caller
    explicitly selected the conservative next-session policy.
    """
    if value is None or value is pd.NaT:
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is not None:
        try:
            parsed = parsed.tz_convert("UTC")
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed


def _parsed_filing_timestamp(value: Any) -> pd.Timestamp | None:
    """Parse a filing value without changing its source timezone."""
    if value is None or value is pd.NaT:
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if pd.isna(parsed) else parsed


def _is_unambiguous_timezone_aware(value: Any) -> bool:
    """Return whether a scalar has an explicit, non-ambiguous timezone."""
    parsed = _parsed_filing_timestamp(value)
    if parsed is None or parsed.tzinfo is None:
        return False
    try:
        python_timestamp = parsed.to_pydatetime()
        offsets = {
            python_timestamp.replace(fold=fold).utcoffset()
            for fold in (0, 1)
        }
        return len(offsets) <= 1
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _parse_cutoff_time(value: Any) -> time | None:
    """Parse a local exchange cutoff clock value."""
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value.strip()).replace(tzinfo=None)
    except ValueError:
        return None


def _valid_timezone(value: Any) -> str | None:
    """Return a validated IANA timezone name, if one was explicitly given."""
    if not isinstance(value, str) or not value.strip():
        return None
    timezone_name = value.strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return timezone_name


def _filing_contract_details(
    data: pd.DataFrame,
    field: str,
    provenance_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the timestamp safety details declared by a filing contract."""
    contract = _get_provenance_contract(data, provenance_contract)
    contract_valid, policy, source = _contract_for_field(
        data, field, provenance_contract,
    )
    if contract is None:
        return {
            "contract_valid": contract_valid,
            "policy": policy,
            "source": source,
            "source_timezone": None,
            "cutoff_time": None,
            "cutoff_policy_valid": False,
        }

    cutoff_policy = contract.get("cutoff_policy")
    cutoff_timezone = None
    cutoff_value: Any = contract.get("cutoff_time", contract.get("cutoff"))
    availability_declaration = contract.get(
        "availability_policy", contract.get("date_policy"),
    )
    if isinstance(availability_declaration, Mapping):
        cutoff_timezone = availability_declaration.get(
            "timezone", availability_declaration.get("source_timezone"),
        )
        cutoff_value = availability_declaration.get(
            "time",
            availability_declaration.get(
                "local_time",
                availability_declaration.get(
                    "cutoff_time", availability_declaration.get("cutoff", cutoff_value),
                ),
            ),
        )
    if isinstance(cutoff_policy, Mapping):
        cutoff_timezone = cutoff_policy.get(
            "timezone", cutoff_policy.get("source_timezone", cutoff_timezone),
        )
        cutoff_value = cutoff_policy.get(
            "time",
            cutoff_policy.get(
                "local_time",
                cutoff_policy.get("cutoff_time", cutoff_policy.get("cutoff", cutoff_value)),
            ),
        )
    elif isinstance(cutoff_policy, str) and cutoff_policy.casefold() not in {
        NEXT_SESSION_POLICY,
        "next-session",
        "conservative_next_session",
    }:
        cutoff_value = cutoff_policy

    timezone_value = contract.get("source_timezone", contract.get("timezone"))
    if isinstance(availability_declaration, Mapping):
        timezone_value = availability_declaration.get(
            "timezone", availability_declaration.get("source_timezone", timezone_value),
        )
    source_timezone = _valid_timezone(timezone_value)
    cutoff_timezone = _valid_timezone(cutoff_timezone) or source_timezone
    cutoff_time = _parse_cutoff_time(cutoff_value)
    cutoff_policy_valid = bool(source_timezone and cutoff_timezone == source_timezone and cutoff_time)

    # A source timezone plus a local exchange cutoff is an explicit policy for
    # deciding whether a filing can affect today's close.  Otherwise the only
    # accepted policy is the deliberately conservative next-session mapping.
    effective_policy = policy
    if effective_policy is None and cutoff_policy_valid:
        effective_policy = "session_cutoff"
    return {
        "contract_valid": contract_valid,
        "policy": effective_policy,
        "source": source,
        "source_timezone": source_timezone,
        "cutoff_time": cutoff_time,
        "cutoff_policy_valid": cutoff_policy_valid,
    }


def filing_to_trading_session(
    value: Any,
    all_dates: pd.DatetimeIndex,
    *,
    availability_policy: str | None,
    source_timezone: str | None = None,
    cutoff_time: time | str | None = None,
) -> pd.Timestamp | None:
    """Map a usable filing availability to a safe exchange session.

    ``next_session`` is strictly after the local availability date.  With an
    explicit ``session_cutoff`` contract, availability at or before the local
    cutoff may affect that day's close; after-cutoff availability moves to the
    next exchange session.
    """
    parsed = _parsed_filing_timestamp(value)
    if parsed is None or availability_policy not in {NEXT_SESSION_POLICY, "session_cutoff"}:
        return None
    if source_timezone:
        try:
            local = parsed.tz_convert(source_timezone) if parsed.tzinfo is not None else parsed
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        local = parsed

    session_values: list[pd.Timestamp] = []
    for value in all_dates:
        try:
            session = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if pd.isna(session):
            continue
        if session.tzinfo is not None:
            try:
                session = session.tz_convert("Asia/Seoul").tz_localize(None)
            except (TypeError, ValueError, OverflowError):
                continue
        session_values.append(pd.Timestamp(session.date()))
    sessions = pd.DatetimeIndex(session_values)
    sessions = sessions.unique().sort_values()
    if not len(sessions):
        return None
    local_date = pd.Timestamp(local.date())
    if availability_policy == NEXT_SESSION_POLICY:
        eligible = sessions[sessions > local_date]
    else:
        parsed_cutoff = _parse_cutoff_time(cutoff_time)
        if parsed_cutoff is None or not has_meaningful_filing_timestamp(parsed):
            return None
        local_clock = local.timetz().replace(tzinfo=None)
        if local_date in sessions and local_clock <= parsed_cutoff:
            eligible = sessions[sessions >= local_date]
        else:
            eligible = sessions[sessions > local_date]
    return pd.Timestamp(eligible[0]).normalize() if len(eligible) else None


def has_meaningful_filing_timestamp(value: Any) -> bool:
    """Return whether *value* contains a non-midnight time component."""
    # Inspect the source-local scalar rather than the UTC-normalized value.
    # A 09:00 Asia/Seoul filing is 00:00 UTC, but it is still an intraday
    # timestamp and must not be mistaken for a date-only value.
    timestamp = _parsed_filing_timestamp(value)
    if timestamp is None:
        return False
    return any((timestamp.hour, timestamp.minute, timestamp.second, timestamp.microsecond,
                timestamp.nanosecond))


def _availability_values(data: pd.DataFrame, field: str) -> list[pd.Timestamp | None]:
    """Normalize all values in an availability column consistently."""
    return [normalize_filing_timestamp(value) for value in data[field].tolist()]


def find_filing_date_field(data: pd.DataFrame) -> str | None:
    """Return one supported availability field; conflicts fail closed."""
    fields = list(dict.fromkeys(
        field for field in (*TIMESTAMP_FIELDS, *CUTOFF_FIELDS, *FILING_DATE_FIELDS)
        if field in data.columns
    ))
    return fields[0] if len(fields) == 1 else None


def has_usable_filing_dates(
    data: pd.DataFrame,
    *,
    provenance_contract: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether every financial row has an unambiguous availability key.

    Empty data, absent fields, and partially populated fields are not enough
    to establish PIT availability.  In particular, ``year``/``quarter``,
    fiscal period-end columns, and date-only filing fields are deliberately
    not accepted.  The selected field must have an explicit source/schema
    contract and either a meaningful time component or a documented
    conservative ``next_session`` policy.
    """
    return _has_usable_filing_dates(data, provenance_contract=provenance_contract)


def _has_usable_filing_dates(
    data: pd.DataFrame,
    *,
    provenance_contract: Mapping[str, Any] | None = None,
) -> bool:
    if data.empty:
        return False
    field = find_filing_date_field(data)
    if field is None:
        return False
    values = _availability_values(data, field)
    if not all(value is not None for value in values):
        return False
    details = _filing_contract_details(data, field, provenance_contract)
    if not details["contract_valid"]:
        return False
    meaningful_timestamp = all(has_meaningful_filing_timestamp(value) for value in data[field])
    timezone_safe = all(_is_unambiguous_timezone_aware(value) for value in data[field])
    return details["policy"] == NEXT_SESSION_POLICY or (
        details["policy"] == "session_cutoff"
        and meaningful_timestamp
        and timezone_safe
        and details["cutoff_policy_valid"]
    )


def get_filing_availability_policy(
    data: pd.DataFrame,
    *,
    provenance_contract: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the validated availability policy for the selected field."""
    field = find_filing_date_field(data)
    if field is None:
        return None
    details = _filing_contract_details(data, field, provenance_contract)
    return details["policy"] if details["contract_valid"] else None


def validate_financial_provenance(
    data: pd.DataFrame,
    *,
    filing_date_used: bool = False,
    provenance_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the financial data mode and PIT validity details.

    A date column is only sufficient when its source/schema contract declares
    filing availability and it has a meaningful timestamp or an explicit
    conservative next-session policy.  The caller must also confirm it was
    used as the availability date.  This prevents a parser from silently
    retaining a parseable column while still forward-filling from fiscal
    quarter ends.
    """
    field = find_filing_date_field(data)
    values = _availability_values(data, field) if field is not None and not data.empty else []
    parseable = bool(values) and all(value is not None for value in values)
    contract_valid = False
    policy: str | None = None
    contract_source: str | None = None
    meaningful_timestamp = False
    timezone_safe = False
    cutoff_policy_valid = False
    source_timezone: str | None = None
    cutoff_time: time | None = None
    if field is not None:
        details = _filing_contract_details(
            data, field, provenance_contract,
        )
        contract_valid = bool(details["contract_valid"])
        policy = details["policy"]
        contract_source = details["source"]
        source_timezone = details["source_timezone"]
        cutoff_time = details["cutoff_time"]
        cutoff_policy_valid = bool(details["cutoff_policy_valid"])
        meaningful_timestamp = parseable and all(
            has_meaningful_filing_timestamp(value) for value in data[field]
        )
        timezone_safe = parseable and all(
            _is_unambiguous_timezone_aware(value) for value in data[field]
        )
    available = (
        parseable
        and contract_valid
        and (
            policy == NEXT_SESSION_POLICY
            or (
                policy == "session_cutoff"
                and meaningful_timestamp
                and timezone_safe
                and cutoff_policy_valid
            )
        )
    )
    pit_valid = available and filing_date_used
    if pit_valid:
        mode = PIT_FINANCIAL_MODE
        reason = "contracted filing/publication availability dates are present and used"
    elif field is None:
        mode = NON_PIT_FINANCIAL_MODE
        reason = "no filing/publication date field is present"
    elif not parseable:
        mode = NON_PIT_FINANCIAL_MODE
        reason = "filing/publication date field is missing or not fully parseable"
    elif not contract_valid:
        mode = NON_PIT_FINANCIAL_MODE
        reason = "filing field lacks an explicit source/schema availability contract"
    elif not available:
        mode = NON_PIT_FINANCIAL_MODE
        reason = (
            "filing availability lacks an explicit timezone/cutoff contract or next-session policy"
        )
    else:
        mode = NON_PIT_FINANCIAL_MODE
        reason = "filing/publication dates are present but were not used"

    return {
        "mode": mode,
        "pit_valid": pit_valid,
        "filing_date_field": field,
        "filing_date_available": available,
        "filing_date_used": bool(filing_date_used),
        "availability_semantics": available,
        "availability_field": field,
        "source_schema_contract": contract_valid,
        "contract_source": contract_source,
        "availability_policy": policy,
        "meaningful_timestamp": meaningful_timestamp,
        "timezone_safe": timezone_safe,
        "source_timezone": source_timezone,
        "cutoff_time": cutoff_time.isoformat() if cutoff_time is not None else None,
        "cutoff_policy_valid": cutoff_policy_valid,
        "reason": reason,
    }


def is_pit_valid_financial_data(
    data: pd.DataFrame,
    *,
    filing_date_used: bool = False,
    provenance_contract: Mapping[str, Any] | None = None,
) -> bool:
    """Return ``True`` only when filing dates are available and used."""
    return bool(validate_financial_provenance(
        data,
        filing_date_used=filing_date_used,
        provenance_contract=provenance_contract,
    )["pit_valid"])


def classify_financial_provenance(
    data: pd.DataFrame,
    *,
    filing_date_used: bool = False,
    provenance_contract: Mapping[str, Any] | None = None,
) -> str:
    """Return the manifest-friendly financial quality data mode."""
    return str(validate_financial_provenance(
        data,
        filing_date_used=filing_date_used,
        provenance_contract=provenance_contract,
    )["mode"])


# Alternate validator-style names keep the small contract discoverable without
# duplicating any logic.
get_financial_provenance = validate_financial_provenance
validate_financial_pit = validate_financial_provenance
is_financial_pit_valid = is_pit_valid_financial_data
