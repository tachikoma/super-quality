"""Data layer for KOSPI 200 Momentum + Quality strategy."""

from k200_mq.data.provenance import (
    FINANCIAL_PROVENANCE_CONTRACT_ATTR as FINANCIAL_PROVENANCE_CONTRACT_ATTR,
    NEXT_SESSION_POLICY as NEXT_SESSION_POLICY,
    classify_financial_provenance as classify_financial_provenance,
    find_filing_date_field as find_filing_date_field,
    filing_to_trading_session as filing_to_trading_session,
    get_financial_provenance as get_financial_provenance,
    get_filing_availability_policy as get_filing_availability_policy,
    has_usable_filing_dates as has_usable_filing_dates,
    has_meaningful_filing_timestamp as has_meaningful_filing_timestamp,
    is_pit_valid_financial_data as is_pit_valid_financial_data,
    normalize_filing_timestamp as normalize_filing_timestamp,
    validate_financial_provenance as validate_financial_provenance,
)
from k200_mq.data.dart_pit import (
    DART_AMENDMENT_POLICIES as DART_AMENDMENT_POLICIES,
    DART_EXPLICIT_TIMESTAMP_POLICY as DART_EXPLICIT_TIMESTAMP_POLICY,
    DART_FINANCIAL_FACT_COLUMNS as DART_FINANCIAL_FACT_COLUMNS,
    DART_FILING_METADATA_COLUMNS as DART_FILING_METADATA_COLUMNS,
    DART_OFFICIAL_FILING_TIMEZONE as DART_OFFICIAL_FILING_TIMEZONE,
    DART_OPEN_DART_ENDPOINTS as DART_OPEN_DART_ENDPOINTS,
    DART_RAW_FINANCIAL_FACT_COLUMNS as DART_RAW_FINANCIAL_FACT_COLUMNS,
    DART_RAW_FILING_METADATA_COLUMNS as DART_RAW_FILING_METADATA_COLUMNS,
    DARTPITError as DARTPITError,
    DARTPITValidationError as DARTPITValidationError,
    DARTPITValidationResult as DARTPITValidationResult,
    join_dart_financial_facts_to_filings as join_dart_financial_facts_to_filings,
    load_dart_financial_facts as load_dart_financial_facts,
    load_dart_filing_metadata as load_dart_filing_metadata,
    map_dart_filing_availability as map_dart_filing_availability,
    prepare_dart_financial_facts as prepare_dart_financial_facts,
    validate_dart_pit as validate_dart_pit,
    validate_dart_provenance as validate_dart_provenance,
)
from k200_mq.data.pit_universe import (
    EVENT_COLUMNS as EVENT_COLUMNS,
    INTERVAL_COLUMNS as INTERVAL_COLUMNS,
    INDEX_CODE as INDEX_CODE,
    SNAPSHOT_COLUMNS as SNAPSHOT_COLUMNS,
    ConstituentSnapshot as ConstituentSnapshot,
    AcquisitionManifest as AcquisitionManifest,
    MembershipInterval as MembershipInterval,
    PITUniverseError as PITUniverseError,
    PITUniverseValidationError as PITUniverseValidationError,
    PITValidationResult as PITValidationResult,
    TransitionExceptionPolicy as TransitionExceptionPolicy,
    fingerprint_dataframe as fingerprint_dataframe,
    import_local_pit_universe as import_local_pit_universe,
    intervals_to_history as intervals_to_history,
    load_constituent_snapshots as load_constituent_snapshots,
    load_membership_intervals as load_membership_intervals,
    sha256_file as sha256_file,
    snapshots_to_history as snapshots_to_history,
    validate_constituent_snapshots as validate_constituent_snapshots,
    validate_membership_intervals as validate_membership_intervals,
)


_UNIVERSE_EXPORTS = frozenset({
    "apply_exclusions",
    "exclude_kospi_top_n",
    "get_kospi200_constituents",
    "get_kospi200_history",
    "get_kospi200_history_with_provenance",
    "get_universe_provenance",
    "is_kospi200_constituent",
    "is_pit_valid_universe",
    "validate_universe_provenance",
})


def __getattr__(name: str):
    """Load cache-backed universe APIs only when a caller actually uses them."""
    if name not in _UNIVERSE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from k200_mq.data import universe

    value = getattr(universe, name)
    globals()[name] = value
    return value


__all__ = [
    "FINANCIAL_PROVENANCE_CONTRACT_ATTR",
    "NEXT_SESSION_POLICY",
    "classify_financial_provenance",
    "find_filing_date_field",
    "filing_to_trading_session",
    "get_financial_provenance",
    "get_filing_availability_policy",
    "has_usable_filing_dates",
    "has_meaningful_filing_timestamp",
    "is_pit_valid_financial_data",
    "normalize_filing_timestamp",
    "validate_financial_provenance",
    "DART_AMENDMENT_POLICIES",
    "DART_EXPLICIT_TIMESTAMP_POLICY",
    "DART_FINANCIAL_FACT_COLUMNS",
    "DART_FILING_METADATA_COLUMNS",
    "DART_OFFICIAL_FILING_TIMEZONE",
    "DART_OPEN_DART_ENDPOINTS",
    "DART_RAW_FINANCIAL_FACT_COLUMNS",
    "DART_RAW_FILING_METADATA_COLUMNS",
    "DARTPITError",
    "DARTPITValidationError",
    "DARTPITValidationResult",
    "join_dart_financial_facts_to_filings",
    "load_dart_financial_facts",
    "load_dart_filing_metadata",
    "map_dart_filing_availability",
    "prepare_dart_financial_facts",
    "validate_dart_pit",
    "validate_dart_provenance",
    "EVENT_COLUMNS",
    "INTERVAL_COLUMNS",
    "INDEX_CODE",
    "SNAPSHOT_COLUMNS",
    "ConstituentSnapshot",
    "AcquisitionManifest",
    "MembershipInterval",
    "PITUniverseError",
    "PITUniverseValidationError",
    "PITValidationResult",
    "TransitionExceptionPolicy",
    "fingerprint_dataframe",
    "import_local_pit_universe",
    "intervals_to_history",
    "load_constituent_snapshots",
    "load_membership_intervals",
    "sha256_file",
    "snapshots_to_history",
    "validate_constituent_snapshots",
    "validate_membership_intervals",
    *_UNIVERSE_EXPORTS,
]
