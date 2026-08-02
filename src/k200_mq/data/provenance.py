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
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from typing import Any
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

    sessions = pd.DatetimeIndex(pd.to_datetime(all_dates, errors="coerce")).dropna()
    if sessions.tz is not None:
        sessions = sessions.tz_convert(None)
    sessions = sessions.normalize()
    sessions = sessions.unique().sort_values()
    if not len(sessions):
        return None
    local_date = pd.Timestamp(local.date())
    if availability_policy == NEXT_SESSION_POLICY:
        eligible = sessions[sessions > local_date]
    else:
        parsed_cutoff = _parse_cutoff_time(cutoff_time)
        if parsed_cutoff is None:
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
    """Return the first supported availability field, if any."""
    for field in (*TIMESTAMP_FIELDS, *CUTOFF_FIELDS, *FILING_DATE_FIELDS):
        if field in data.columns:
            return field
    return None


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
