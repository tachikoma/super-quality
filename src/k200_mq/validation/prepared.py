"""Prepared inputs and in-memory interval execution for K200MQ.

The prepared-input object is the boundary between data preparation and a
portfolio simulation.  It owns copies of the pandas inputs supplied to it;
callers must treat those frames as read-only.  A fresh engine is constructed
for every interval, so no cash, positions, pending orders, or strategy state
can leak from one simulation to another.

This module deliberately contains no data-loader calls.  Preparation remains
in :mod:`k200_mq.main` so the existing provenance and manifest steps stay in
one place; the mechanical true walk-forward CLI reuses this adapter without
re-preparing inputs for candidates or folds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from types import MappingProxyType
from typing import Any

import pandas as pd


_CREDENTIAL_FIELD_NAMES = frozenset({
    "ACCESS_TOKEN",
    "API_KEY",
    "API_TOKEN",
    "AUTH_TOKEN",
    "CLIENT_SECRET",
    "DART_API_KEY",
    "KRX_ID",
    "KRX_PW",
    "PASSWD",
    "PASSWORD",
    "PRIVATE_KEY",
    "PWD",
    "SECRET",
    "SECRET_KEY",
    "TOKEN",
})

_UNAVAILABLE_RANKING_STATUSES = frozenset({"disabled", "unavailable", "missing"})


def _is_credential_field(name: object) -> bool:
    """Return whether *name* is an explicitly known credential field.

    This intentionally does not use substring matching.  Configuration names
    such as ``INITIAL_CAPITAL`` and future non-secret fields must survive the
    prepared-input boundary unchanged.
    """
    return str(name).upper() in _CREDENTIAL_FIELD_NAMES


def _is_pit_ranking(prepared: PreparedK200MQInputs) -> bool:
    """Return whether a prepared ranking has a validator-backed PIT contract.

    The prepared path currently carries only a static ticker tuple.  A static
    ordering has no effective date for each historical rebalance and therefore
    cannot be promoted to PIT by metadata supplied alongside the tuple.  Keep
    this unconditional until a date-indexed ranking artifact and its validator
    are part of the preparation contract.
    """
    del prepared
    return False


def _config_bool(value: Any, default: bool = False) -> bool:
    """Interpret a config boolean without treating ``"false"`` as true."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _validate_prepared_pit_provenance(prepared: PreparedK200MQInputs) -> None:
    """Fail closed when a strict interval lacks PIT preparation evidence.

    The interval adapter cannot load or recompute provenance.  It therefore
    revalidates the actual prepared universe frame and requires the raw
    financial frame to be present so the financial validator can be run again.
    Caller-supplied provenance mappings and booleans are audit metadata only;
    they are never evidence for strict execution.
    """
    from k200_mq.data.provenance import (
        has_usable_filing_dates,
        validate_financial_provenance,
        validate_universe_provenance,
    )

    try:
        universe = validate_universe_provenance(prepared.universe_history)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before interval execution: "
            "the prepared universe_history could not be validated by the "
            "universe provenance validator"
        ) from exc
    if universe.get("pit_valid") is not True:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before interval execution: "
            f"universe provenance is {universe.get('provenance', 'unknown')!r}. "
            "The prepared universe must have effective-date PIT provenance."
        )

    if prepared.financial_data is None or prepared.financial_data.empty:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before interval execution: "
            "no validator-backed financial provenance evidence is present. "
            "Provide the raw prepared financial data with validated filing-date "
            "provenance; caller-supplied pit_valid mappings are not accepted."
        )
    try:
        mapped_row_count = prepared.financial_filing_date_mapped_row_count
        if mapped_row_count is not None:
            # Hard evidence from the engine mapping pass: the measured number
            # of rows the engine actually mapped through filing dates.
            filing_date_used = mapped_row_count > 0
        else:
            # Legacy/test bundles that predate the measured counter keep the
            # conservative structural proxy so strict behavior is unchanged.
            filing_date_used = has_usable_filing_dates(prepared.financial_data)
        financials = validate_financial_provenance(
            prepared.financial_data,
            filing_date_used=filing_date_used,
        )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before interval execution: "
            "the prepared financial data could not be validated by the "
            "financial provenance validator"
        ) from exc
    if financials.get("pit_valid") is not True:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION failed before interval execution: "
            f"financial quality mode is {financials.get('mode', 'unknown')!r}. "
            "The prepared financial data must carry validated filing-date provenance."
        )


def _is_secret_field(name: object) -> bool:
    return _is_credential_field(name)


def _strip_secret_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _strip_secret_values(item)
            for key, item in value.items()
            if not _is_secret_field(key)
        }
    if isinstance(value, list):
        return [_strip_secret_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_secret_values(item) for item in value)
    return value


def _ranking_fingerprint(ranking: tuple[str, ...]) -> str | None:
    """Return a stable fingerprint for the prepared ranking order."""
    if not ranking:
        return None
    payload = json.dumps(list(ranking), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedK200MQInputs:
    """Read-only-by-convention inputs shared by independent simulations.

    ``pandas.DataFrame`` does not provide deep immutability, so the dataclass
    takes private deep copies at construction and interval execution copies
    them again before handing them to the engine.  The frozen shell prevents
    replacing a prepared input or its metadata after construction.

    The optional ``runtime_config`` is a credential-free engine configuration
    snapshot.  API keys and other credentials are intentionally not accepted
    as part of this bundle.
    """

    price_data: pd.DataFrame
    factor_data: pd.DataFrame
    index_data: pd.DataFrame
    universe_history: pd.DataFrame
    regime_scale_map: Mapping[Any, float] | None = None
    sector_map_by_as_of: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    kospi_mcap_ranking: tuple[str, ...] | None = None
    manifest_context: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    measured_start: date | pd.Timestamp | None = None
    measured_end: date | pd.Timestamp | None = None
    warmup_start: date | pd.Timestamp | None = None
    warmup_end: date | pd.Timestamp | None = None
    measured_dates: tuple[pd.Timestamp, ...] = ()
    active_trading_start: date | pd.Timestamp | None = None
    runtime_config: Any = None
    ranking_status: str | None = None
    ranking_provenance: str | None = None
    ranking_effective_date: date | pd.Timestamp | None = None
    ranking_fingerprint: str | None = None
    ranking_pit_valid: bool = False
    financial_data: pd.DataFrame | None = None
    financial_filing_date_mapped_row_count: int | None = None

    def __post_init__(self) -> None:
        if self.financial_filing_date_mapped_row_count is not None:
            if isinstance(self.financial_filing_date_mapped_row_count, bool) or not isinstance(
                self.financial_filing_date_mapped_row_count, int
            ):
                raise TypeError(
                    "financial_filing_date_mapped_row_count must be an int or None"
                )
            if self.financial_filing_date_mapped_row_count < 0:
                raise ValueError(
                    "financial_filing_date_mapped_row_count must be non-negative"
                )
        for field_name in ("price_data", "factor_data", "index_data", "universe_history"):
            value = getattr(self, field_name)
            if not isinstance(value, pd.DataFrame):
                raise TypeError(f"{field_name} must be a pandas DataFrame")
            object.__setattr__(self, field_name, value.copy(deep=True))
        if self.financial_data is not None:
            if not isinstance(self.financial_data, pd.DataFrame):
                raise TypeError("financial_data must be a pandas DataFrame or None")
            object.__setattr__(self, "financial_data", self.financial_data.copy(deep=True))

        if self.regime_scale_map is not None:
            object.__setattr__(
                self,
                "regime_scale_map",
                MappingProxyType(dict(self.regime_scale_map)),
            )
        if not isinstance(self.sector_map_by_as_of, Mapping):
            raise TypeError("sector_map_by_as_of must be a mapping")
        normalised_sector_map: dict[str, MappingProxyType] = {}
        for as_of, ticker_map in self.sector_map_by_as_of.items():
            if not isinstance(ticker_map, Mapping):
                raise TypeError("sector_map_by_as_of values must be mappings")
            normalised_sector_map[str(as_of)] = MappingProxyType({
                str(ticker): str(sector)
                for ticker, sector in ticker_map.items()
            })
        object.__setattr__(
            self,
            "sector_map_by_as_of",
            MappingProxyType(normalised_sector_map),
        )
        ranking = tuple(str(ticker) for ticker in (self.kospi_mcap_ranking or ()))
        object.__setattr__(self, "kospi_mcap_ranking", ranking)
        if not isinstance(self.ranking_pit_valid, bool):
            raise TypeError("ranking_pit_valid must be an actual bool")
        # ``kospi_mcap_ranking`` is currently a static snapshot, so discard
        # all caller metadata that could make it look like historical PIT data.
        if ranking:
            object.__setattr__(self, "ranking_status", "non_pit_mechanical")
            object.__setattr__(self, "ranking_provenance", "current_market_cap_snapshot")
            object.__setattr__(self, "ranking_effective_date", None)
            object.__setattr__(self, "ranking_pit_valid", False)
        else:
            status = self.ranking_status
            if status is None:
                status = "unavailable"
            object.__setattr__(self, "ranking_status", str(status))
            provenance = self.ranking_provenance
            if provenance is None:
                provenance = "unavailable"
            object.__setattr__(self, "ranking_provenance", str(provenance))
            if self.ranking_effective_date is not None:
                object.__setattr__(
                    self,
                    "ranking_effective_date",
                    pd.Timestamp(self.ranking_effective_date).floor("D"),
                )
            object.__setattr__(self, "ranking_pit_valid", False)
        fingerprint = _ranking_fingerprint(ranking)
        object.__setattr__(self, "ranking_fingerprint", fingerprint)
        for field_name in ("manifest_context", "provenance", "coverage"):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{field_name} must be a mapping")
            copied = dict(value)
            if field_name in {"manifest_context", "provenance"}:
                copied.setdefault("ranking", self.ranking_context)
            if field_name == "manifest_context" and isinstance(
                copied.get("universe_history"), pd.DataFrame
            ):
                copied["universe_history"] = self.universe_history.copy(deep=True)
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(_strip_secret_values(copied)),
            )

        if self.runtime_config is not None:
            model_copy = getattr(self.runtime_config, "model_copy", None)
            if callable(model_copy):
                safe_config = model_copy(deep=True, update={
                    "DART_API_KEY": "",
                    "KRX_ID": "",
                    "KRX_PW": "",
                })
            elif isinstance(self.runtime_config, Mapping):
                safe_config = _strip_secret_values(dict(self.runtime_config))
            else:
                safe_config = self.runtime_config
            object.__setattr__(self, "runtime_config", safe_config)

        normalised_dates = tuple(
            pd.Timestamp(value).floor("D") for value in self.measured_dates
        )
        if not normalised_dates:
            if isinstance(self.price_data.index, pd.MultiIndex) and "date" in self.price_data.index.names:
                raw_dates = self.price_data.index.get_level_values("date")
                normalised_dates = tuple(
                    pd.DatetimeIndex(pd.to_datetime(raw_dates, errors="coerce"))
                    .dropna()
                    .floor("D")
                    .unique()
                    .sort_values()
                )
        object.__setattr__(self, "measured_dates", normalised_dates)

    @property
    def measured_price_data(self) -> pd.DataFrame:
        """Descriptive alias for the price frame without warmup rows."""
        return self.price_data

    @property
    def market_index_data(self) -> pd.DataFrame:
        """Descriptive alias for the prepared market-index frame."""
        return self.index_data

    @property
    def index_regime_map(self) -> Mapping[Any, float] | None:
        """Descriptive alias for the measured regime exposure map."""
        return self.regime_scale_map

    @property
    def exclusion_ranking_status(self) -> str:
        """Status of the ranking artifact used for top-N exclusion."""
        return str(self.ranking_status)

    @property
    def kospi_mcap_ranking_status(self) -> str:
        """Compatibility alias for the explicit ranking status."""
        return str(self.ranking_status)

    @property
    def kospi_mcap_ranking_provenance(self) -> str:
        """Compatibility alias for the explicit ranking provenance."""
        return str(self.ranking_provenance)

    @property
    def ranking_context(self) -> dict[str, Any]:
        """Return auditable ranking metadata without claiming historical PIT."""
        return {
            "status": self.ranking_status,
            "provenance": self.ranking_provenance,
            "effective_date": self.ranking_effective_date,
            "fingerprint": self.ranking_fingerprint,
            "pit_valid": _is_pit_ranking(self),
            "artifact_available": bool(self.kospi_mcap_ranking),
            "classification": (
                "non_pit_mechanical"
                if self.kospi_mcap_ranking
                else "not_required"
            ),
        }


def _candidate_overrides(candidate_config: Any) -> dict[str, Any]:
    """Extract runtime overrides from a CandidateSpec or mapping."""
    if candidate_config is None:
        return {}
    parameters = getattr(candidate_config, "parameters", None)
    if isinstance(parameters, Mapping):
        return {str(key).upper(): value for key, value in parameters.items()}
    if isinstance(candidate_config, Mapping):
        nested = candidate_config.get("parameters")
        if isinstance(nested, Mapping):
            return {str(key).upper(): value for key, value in nested.items()}
        return {
            str(key).upper(): value
            for key, value in candidate_config.items()
            if str(key).upper() != "PARAMETERS"
        }
    return {}


# These fields only alter the simulation over already prepared inputs.  Any
# omitted field is rejected rather than allowing factors, schedules, or regime
# maps to become stale without an explicit error.
_SAFE_RUNTIME_FIELDS = frozenset({
    "TOP_N",
    "REGIME_FILTER_ENABLED",
    "REGIME_REDUCTION",
    "EXCLUDE_KOSPI_TOP_N",
    "WEIGHT_MOMENTUM",
    "WEIGHT_QUALITY",
    "WEIGHT_METHOD",
    "MAX_POSITION_WEIGHT",
    "ENABLE_ADV_FILTER",
    "MIN_ADV_RATIO",
    "ADV_LOOKBACK_DAYS",
    "ENABLE_CORRELATION_FILTER",
    "MAX_PAIR_CORRELATION",
    "CORRELATION_LOOKBACK_DAYS",
    "INITIAL_CAPITAL",
    "COMMISSION_RATE",
    "TAX_RATE",
    "SLIPPAGE",
    "SL_STOP_LOSS",
    "ENABLE_STOP_LOSS",
    "MIN_CASH_RATIO",
    "STRICT_PIT_VALIDATION",
    "CONTINUOUS_REGIME",
    "TARGET_VOL",
    "VOL_LOOKBACK",
    "QUALITY_PRIMARY",
    "MOMENTUM_WINDOW_LONG",
    "MOMENTUM_SKIP_DAYS",
})
_DIAGNOSTIC_ONLY_FIELDS = frozenset({"MOMENTUM_WINDOW_SHORT"})


def _validate_candidate_overrides(
    prepared: PreparedK200MQInputs,
    overrides: Mapping[str, Any],
) -> None:
    """Reject candidate values that would invalidate prepared data."""
    credential_fields = {str(key).upper() for key in _CREDENTIAL_FIELD_NAMES}
    normalised = {str(key).upper(): value for key, value in overrides.items()}
    ignored_credentials = credential_fields.intersection(normalised)
    requested = set(normalised).difference(ignored_credentials)
    diagnostic_only = sorted(requested.intersection(_DIAGNOSTIC_ONLY_FIELDS))
    if diagnostic_only:
        fields = ", ".join(diagnostic_only)
        raise ValueError(
            f"diagnostic-only field(s) are not runtime candidate dimensions: {fields}"
        )
    unsafe = sorted(requested.difference(_SAFE_RUNTIME_FIELDS))
    if unsafe:
        fields = ", ".join(unsafe)
        raise ValueError(
            "candidate override(s) require prepared-data recomputation and are "
            f"not permitted for an interval: {fields}"
        )

    if "EXCLUDE_KOSPI_TOP_N" in requested:
        try:
            exclusion_enabled = int(normalised["EXCLUDE_KOSPI_TOP_N"]) > 0
        except (TypeError, ValueError) as exc:
            raise ValueError("EXCLUDE_KOSPI_TOP_N must be an integer") from exc
        if exclusion_enabled and (
            not prepared.kospi_mcap_ranking
            or str(prepared.ranking_status).lower() in _UNAVAILABLE_RANKING_STATUSES
        ):
            raise ValueError(
                "EXCLUDE_KOSPI_TOP_N requires a prepared KOSPI market-cap "
                "ranking artifact; interval execution will not load one"
            )


def _runtime_config(
    prepared: PreparedK200MQInputs,
    candidate_config: Any,
) -> Any:
    """Build a fresh engine config without modifying the prepared bundle."""
    from k200_mq.config import K200MQConfig

    base_config = prepared.runtime_config
    overrides = _candidate_overrides(candidate_config)
    if isinstance(candidate_config, K200MQConfig):
        candidate_values = candidate_config.model_dump()
        if base_config is None:
            overrides = candidate_values
        else:
            if hasattr(base_config, "model_dump"):
                base_values = base_config.model_dump()
            elif isinstance(base_config, Mapping):
                base_values = dict(base_config)
            else:
                raise TypeError("prepared runtime_config must be a K200MQConfig or mapping")
            overrides = {
                key: value
                for key, value in candidate_values.items()
                if value != base_values.get(key)
            }
    if isinstance(base_config, Mapping):
        base_strict_value = base_config.get("STRICT_PIT_VALIDATION")
    else:
        base_strict_value = getattr(base_config, "STRICT_PIT_VALIDATION", None)
    base_strict_pit = _config_bool(base_strict_value)
    if base_strict_pit and "STRICT_PIT_VALIDATION" in overrides:
        # Strict preparation is a run-level safety contract.  A candidate may
        # enable it, but must not turn it off for an interval derived from that
        # preparation.
        overrides = dict(overrides)
        overrides["STRICT_PIT_VALIDATION"] = True
    _validate_candidate_overrides(prepared, overrides)

    def _without_credentials(config: K200MQConfig) -> K200MQConfig:
        return config.model_copy(deep=True, update={
            "DART_API_KEY": "",
            "KRX_ID": "",
            "KRX_PW": "",
        })

    if isinstance(candidate_config, K200MQConfig):
        if base_strict_pit and not candidate_config.STRICT_PIT_VALIDATION:
            candidate_config = candidate_config.model_copy(
                update={"STRICT_PIT_VALIDATION": True},
            )
        return _without_credentials(candidate_config)

    if base_config is None:
        values = K200MQConfig.model_construct().model_dump()
        values.update(overrides)
        return _without_credentials(K200MQConfig.model_validate(values))
    model_dump = getattr(base_config, "model_dump", None)
    if callable(model_dump):
        values = K200MQConfig.model_construct().model_dump()
        dumped = model_dump()
        if not isinstance(dumped, Mapping):
            raise TypeError("prepared runtime_config model_dump must return a mapping")
        values.update(dumped)
        values.update(overrides)
        return _without_credentials(K200MQConfig.model_validate(values))
    if not isinstance(base_config, Mapping):
        raise TypeError("prepared runtime_config must be a K200MQConfig or mapping")
    values = K200MQConfig.model_construct().model_dump()
    values.update(dict(base_config))
    values.update(overrides)
    return _without_credentials(K200MQConfig.model_validate(values))


def _interval_price_data(
    price_data: pd.DataFrame,
    measured_start: date | pd.Timestamp | None,
    measured_end: date | pd.Timestamp | None,
) -> pd.DataFrame:
    """Return a detached measured-only price frame for one interval."""
    if price_data.empty or not isinstance(price_data.index, pd.MultiIndex):
        return price_data.copy(deep=True)
    if "date" not in price_data.index.names:
        return price_data.copy(deep=True)

    dates = pd.DatetimeIndex(
        pd.to_datetime(price_data.index.get_level_values("date"), errors="coerce")
    )
    mask = pd.Series(True, index=price_data.index).to_numpy()
    if measured_start is not None:
        mask &= dates >= pd.Timestamp(measured_start).floor("D")
    if measured_end is not None:
        mask &= dates <= pd.Timestamp(measured_end).floor("D")
    return price_data.loc[mask].copy(deep=True)


def _interval_frame_data(
    frame: pd.DataFrame,
    measured_start: date | pd.Timestamp | None,
    measured_end: date | pd.Timestamp | None,
    date_columns: tuple[str, ...],
) -> pd.DataFrame:
    """Slice a date-bearing prepared frame to one engine interval."""
    copied = frame.copy(deep=True)
    if copied.empty or (measured_start is None and measured_end is None):
        return copied

    raw_dates: Any = None
    for column in date_columns:
        if column in copied.columns:
            raw_dates = copied[column]
            break
    if raw_dates is None and isinstance(copied.index, pd.MultiIndex):
        for level_name in date_columns:
            if level_name in copied.index.names:
                raw_dates = copied.index.get_level_values(level_name)
                break
    if raw_dates is None and isinstance(copied.index, pd.DatetimeIndex):
        raw_dates = copied.index
    if raw_dates is None:
        return copied

    dates = pd.DatetimeIndex(pd.to_datetime(raw_dates, errors="coerce")).floor("D")
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    mask = ~dates.isna()
    if measured_start is not None:
        mask &= dates >= pd.Timestamp(measured_start).floor("D")
    if measured_end is not None:
        mask &= dates <= pd.Timestamp(measured_end).floor("D")
    return copied.loc[mask].copy(deep=True)


def _interval_regime_map(
    regime_scale_map: Mapping[Any, float] | None,
    measured_start: date | pd.Timestamp | None,
    measured_end: date | pd.Timestamp | None,
) -> dict[Any, float] | None:
    """Return only regime observations inside one measured interval."""
    if regime_scale_map is None:
        return None
    start = pd.Timestamp(measured_start).floor("D") if measured_start is not None else None
    end = pd.Timestamp(measured_end).floor("D") if measured_end is not None else None
    result: dict[Any, float] = {}
    for raw_date, value in regime_scale_map.items():
        try:
            point_date = pd.Timestamp(raw_date).floor("D")
        except (TypeError, ValueError):
            continue
        if point_date.tzinfo is not None:
            point_date = point_date.tz_localize(None)
        if start is not None and point_date < start:
            continue
        if end is not None and point_date > end:
            continue
        result[raw_date] = value
    return result


def execute_engine_interval(
    prepared: PreparedK200MQInputs,
    candidate_config: Any,
    measured_start: date | pd.Timestamp | None = None,
    measured_end: date | pd.Timestamp | None = None,
    active_trading_start: date | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Execute one independent measured interval entirely in memory.

    No loader, cache, file, or network operation is performed here.  The
    returned engine result is detached from the prepared frames, and a fresh
    :class:`PortfolioRebalanceEngine` is created for every invocation.
    """
    from k200_mq.backtest.portfolio_engine import PortfolioRebalanceEngine

    if not isinstance(prepared, PreparedK200MQInputs):
        raise TypeError("prepared must be a PreparedK200MQInputs instance")

    config = _runtime_config(prepared, candidate_config)
    strict_pit = _config_bool(getattr(config, "STRICT_PIT_VALIDATION", False))
    if strict_pit:
        _validate_prepared_pit_provenance(prepared)
    start = measured_start if measured_start is not None else prepared.measured_start
    end = measured_end if measured_end is not None else prepared.measured_end
    active_start = (
        active_trading_start
        if active_trading_start is not None
        else prepared.active_trading_start
    )

    try:
        excluded_top_n = int(getattr(config, "EXCLUDE_KOSPI_TOP_N", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("EXCLUDE_KOSPI_TOP_N must be an integer") from exc
    if excluded_top_n > 0:
        if (
            not prepared.kospi_mcap_ranking
            or str(prepared.ranking_status).lower() in _UNAVAILABLE_RANKING_STATUSES
        ):
            raise RuntimeError(
                "EXCLUDE_KOSPI_TOP_N requires a prepared KOSPI market-cap "
                "ranking artifact; no ranking is available and interval execution "
                "will not perform a loader/cache fallback"
            )
        if strict_pit and not _is_pit_ranking(prepared):
            raise RuntimeError(
                "STRICT_PIT_VALIDATION rejects the prepared KOSPI ranking: "
                f"status={prepared.ranking_status!r}, provenance="
                f"{prepared.ranking_provenance!r}; provide effective-date PIT rank data"
            )

    price_data = _interval_price_data(prepared.price_data, start, end)
    factor_data = _interval_frame_data(
        prepared.factor_data,
        start,
        end,
        ("date", "as_of"),
    )
    index_data = _interval_frame_data(
        prepared.index_data,
        start,
        end,
        ("date", "as_of"),
    )
    universe_history = _interval_frame_data(
        prepared.universe_history,
        start,
        end,
        ("as_of", "date"),
    )
    regime_enabled = _config_bool(
        getattr(config, "REGIME_FILTER_ENABLED", True),
        default=True,
    )
    if regime_enabled and prepared.regime_scale_map is None:
        raise ValueError(
            "REGIME_FILTER_ENABLED=True requires a prepared regime scale map"
        )
    regime_map = (
        _interval_regime_map(prepared.regime_scale_map, start, end)
        if regime_enabled
        else None
    )

    if excluded_top_n > 0:
        try:
            engine = PortfolioRebalanceEngine(
                config,
                kospi_mcap_ranking=prepared.kospi_mcap_ranking,
                sector_map_by_as_of=prepared.sector_map_by_as_of,
            )
        except TypeError as exc:
            # Keep compatibility with lightweight one-argument test adapters;
            # the production engine accepts the prepared ranking explicitly.
            if "kospi_mcap_ranking" not in str(exc):
                raise
            engine = PortfolioRebalanceEngine(config)
    else:
        try:
            engine = PortfolioRebalanceEngine(
                config,
                sector_map_by_as_of=prepared.sector_map_by_as_of,
            )
        except TypeError as exc:
            if "sector_map_by_as_of" not in str(exc):
                raise
            engine = PortfolioRebalanceEngine(config)
    return engine.run(
        price_data,
        index_data,
        factor_data,
        universe_history,
        regime_scale_map=regime_map,
        measured_start=start,
        measured_end=end,
        active_trading_start=active_start,
    )


__all__ = ["PreparedK200MQInputs", "execute_engine_interval"]
