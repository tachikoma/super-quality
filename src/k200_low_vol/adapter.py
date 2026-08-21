"""The guarded low-volatility execution boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Callable

import pandas as pd

from k200_low_vol.contract import (
    PriceActionBundle,
    SyntheticBundleError,
    construct_prices,
    dataframe_sha256,
    validate_development_cutoff,
    validate_price_action_bundle,
    validate_rows_cutoff,
)
from k200_low_vol.factor import LowVolatilityFactor
from k200_low_vol.selector import LowVolatilitySelector
from k200_low_vol.schedule import krx_quarterly_schedule
from k200_low_vol.spec import LowVolSpec


@dataclass(frozen=True, slots=True)
class CutoffValidationEvidence:
    """Immutable proof that the complete development request was cutoff-safe."""

    request_end_date: date
    validated: bool = True

    def __post_init__(self) -> None:
        cutoff = validate_development_cutoff(self.request_end_date, label="request end date")
        if cutoff != self.request_end_date:
            raise SyntheticBundleError("cutoff evidence must use a normalized date")
        if self.validated is not True:
            raise SyntheticBundleError("cutoff validation evidence is not verified")


@dataclass(frozen=True, slots=True)
class PITUniverseEvidence:
    """PIT universe rows and their synthetic source attestation."""

    rows: pd.DataFrame
    source_manifest: dict[str, Any]
    validated: bool = True

    def __post_init__(self) -> None:
        if self.validated is not True:
            raise SyntheticBundleError("PIT universe evidence is not verified")
        if not isinstance(self.rows, pd.DataFrame):
            raise SyntheticBundleError("PIT universe evidence rows must be a DataFrame")
        if not isinstance(self.source_manifest, dict) or self.source_manifest.get("synthetic") is not True:
            raise SyntheticBundleError("synthetic PIT universe source manifest is required")
        if self.source_manifest.get("sha256") != dataframe_sha256(self.rows):
            raise SyntheticBundleError("PIT universe evidence hash does not match rows")
        if not {"as_of", "ticker"}.issubset(self.rows.columns):
            raise SyntheticBundleError("PIT universe evidence requires as_of and ticker")
        validate_rows_cutoff(self.rows, date_column="as_of")


@dataclass(frozen=True, slots=True)
class ValidatedPriceActionEvidence:
    """A price/action bundle after the complete fail-closed validation pass."""

    bundle: PriceActionBundle
    cutoff: CutoffValidationEvidence
    validated: bool = True

    def __post_init__(self) -> None:
        if self.validated is not True:
            raise SyntheticBundleError("raw price/action evidence is not verified")
        validate_price_action_bundle(self.bundle)
        validate_rows_cutoff(self.bundle.prices)
        if self.bundle.cache_rows is not None:
            validate_rows_cutoff(self.bundle.cache_rows)
        if pd.to_datetime(self.bundle.prices["date"]).dt.date.max() > self.cutoff.request_end_date:
            raise SyntheticBundleError("raw price rows exceed cutoff evidence")


ValidatedRawPriceActionBundle = ValidatedPriceActionEvidence


def build_cutoff_validation_evidence(
    request_end_date: Any,
    *,
    price_rows: pd.DataFrame,
    universe_rows: pd.DataFrame,
    cache_rows: pd.DataFrame | None = None,
) -> CutoffValidationEvidence:
    """Validate every execution-boundary date before constructing evidence."""
    cutoff = validate_development_cutoff(request_end_date, label="request end date")
    validate_rows_cutoff(price_rows)
    validate_rows_cutoff(universe_rows, date_column="as_of")
    if cache_rows is not None:
        validate_rows_cutoff(cache_rows)
    if pd.to_datetime(price_rows["date"]).dt.date.max() > cutoff:
        raise SyntheticBundleError("price rows exceed requested cutoff")
    if pd.to_datetime(universe_rows["as_of"]).dt.date.max() > cutoff:
        raise SyntheticBundleError("universe rows exceed requested cutoff")
    return CutoffValidationEvidence(cutoff)


def build_pit_universe_evidence(rows: pd.DataFrame) -> PITUniverseEvidence:
    """Create synthetic PIT evidence; this function does not load data."""
    if not {"as_of", "ticker"}.issubset(rows.columns):
        raise SyntheticBundleError("PIT universe evidence requires as_of and ticker")
    return PITUniverseEvidence(
        rows=rows.copy(),
        source_manifest={"synthetic": True, "sha256": dataframe_sha256(rows)},
    )


def build_validated_price_action_evidence(
    bundle: PriceActionBundle,
    cutoff: CutoffValidationEvidence,
) -> ValidatedPriceActionEvidence:
    """Return evidence only after validating the entire synthetic bundle."""
    return ValidatedPriceActionEvidence(bundle=bundle, cutoff=cutoff)


def validate_low_vol_execution_config(config: Any, *, pit_universe_size: int) -> None:
    """Reject MQ settings that could change a frozen low-vol target."""
    required_neutral = {
        "REGIME_FILTER_ENABLED": False,
        "CONTINUOUS_REGIME": False,
        "QUALITY_PRIMARY": False,
        "ENABLE_ADV_FILTER": False,
        "ENABLE_CORRELATION_FILTER": False,
        "ENABLE_SECTOR_CAP": False,
        "ENABLE_STOP_LOSS": False,
        "ENABLE_DELISTING_DETECTION": False,
        "EXCLUDE_KOSPI_TOP_N": 0,
        "REGIME_REDUCTION": 0.0,
        "MIN_CASH_RATIO": 0.0,
        "MAX_POSITION_WEIGHT": 1.0,
    }
    failures = [
        f"{name}={getattr(config, name, None)!r} (expected {expected!r})"
        for name, expected in required_neutral.items()
        if getattr(config, name, None) != expected
    ]
    max_holdings = getattr(config, "MAX_HOLDINGS", None)
    if not isinstance(max_holdings, int) or max_holdings < pit_universe_size:
        failures.append("MAX_HOLDINGS must not cap the PIT universe")
    if failures:
        raise SyntheticBundleError(
            "low-vol execution requires neutral MQ controls: " + "; ".join(failures)
        )


class LowVolatilityExecutionAdapter:
    """Run only the frozen selector through the generic engine boundary.

    The adapter deliberately exposes no factor-data or universe-data execution
    arguments.  Both are derived from validator-backed evidence held by this
    object, preventing an arbitrary DataFrame from entering execution.
    """

    def __init__(
        self,
        selector: LowVolatilitySelector,
        spec: LowVolSpec,
        price_action: ValidatedPriceActionEvidence,
        universe: PITUniverseEvidence,
        cutoff: CutoffValidationEvidence,
    ) -> None:
        if type(selector) is not LowVolatilitySelector:
            raise TypeError("low-vol adapter accepts only LowVolatilitySelector")
        if type(spec) is not LowVolSpec:
            raise TypeError("low-vol adapter accepts only the frozen LowVolSpec")
        if selector.spec != spec:
            raise SyntheticBundleError("selector and adapter specifications differ")
        if type(price_action) is not ValidatedPriceActionEvidence:
            raise TypeError("validated raw price/action evidence is required")
        if type(universe) is not PITUniverseEvidence:
            raise TypeError("validated PIT universe evidence is required")
        if type(cutoff) is not CutoffValidationEvidence:
            raise TypeError("cutoff validation evidence is required")
        if price_action.cutoff != cutoff:
            raise SyntheticBundleError("price/action and adapter cutoff evidence differ")
        self.selector = selector
        self.spec = spec
        self.price_action = price_action
        self.universe = universe
        self.cutoff = cutoff

    def validate_engine_config(self, config: Any) -> None:
        """Reject every MQ control that could alter the registered portfolio."""
        universe_size = self.universe.rows["ticker"].astype(str).nunique()
        validate_low_vol_execution_config(config, pit_universe_size=universe_size)

    def run(self, engine: Any) -> dict[str, Any]:
        """Execute validator-backed synthetic inputs through an injected engine."""
        if not callable(getattr(engine, "run", None)):
            raise TypeError("engine must expose run()")
        if not callable(getattr(engine, "set_target_provider", None)):
            raise TypeError("engine must expose set_target_provider()")
        self.validate_engine_config(engine.config)
        engine.set_target_provider(self.selector)
        checked = validate_price_action_bundle(self.price_action.bundle)
        prices = construct_prices(checked)
        factor = LowVolatilityFactor(self.spec).compute(prices)
        price_data = self._engine_prices(prices)
        quarterly_dates = tuple(
            krx_quarterly_schedule(checked.manifest["session_calendar"], spec=self.spec)
        )
        factor_dates = {
            value.date()
            for value in pd.to_datetime(factor["date"], errors="coerce").dropna().tolist()
        }
        eligible_dates = tuple(value for value in quarterly_dates if value in factor_dates)
        universe_data = self._quarterly_universe(quarterly_dates, eligible_dates)
        self._validate_execution_dates(price_data, factor, universe_data)
        return engine.run(
            price_data=price_data,
            index_data=pd.DataFrame(),
            factor_data=factor,
            universe_data=universe_data,
            measured_end=pd.Timestamp(self.cutoff.request_end_date),
            corporate_action_hook=self._corporate_action_hook(checked),
        )

    execute = run

    @staticmethod
    def _engine_prices(prices: pd.DataFrame) -> pd.DataFrame:
        frame = prices.copy()
        # The engine consumes raw execution prices.  Split quantities and
        # position references are adjusted by the hook before valuation; using
        # constructed prices here as well would apply a split twice.
        return frame.set_index(["ticker", "date"]).sort_index()

    def _validate_execution_dates(
        self,
        price_data: pd.DataFrame,
        factor_data: pd.DataFrame,
        universe_data: pd.DataFrame,
    ) -> None:
        for frame, column in (
            (price_data.reset_index(), "date"),
            (factor_data, "date"),
            (universe_data, "as_of"),
        ):
            validate_rows_cutoff(frame, date_column=column)
            if pd.to_datetime(frame[column]).dt.date.max() > self.cutoff.request_end_date:
                raise SyntheticBundleError(f"execution {column} exceeds cutoff evidence")

    def _quarterly_universe(
        self,
        quarterly_dates: tuple[date, ...],
        eligible_dates: tuple[date, ...],
    ) -> pd.DataFrame:
        rows = self.universe.rows[["as_of", "ticker"]].copy()
        rows["as_of"] = pd.to_datetime(rows["as_of"], errors="coerce").dt.normalize()
        allowed = {pd.Timestamp(value) for value in quarterly_dates}
        filtered = rows[rows["as_of"].isin(allowed)].copy()
        available = set(filtered["as_of"].dt.date)
        if not eligible_dates:
            raise SyntheticBundleError("no eligible quarterly low-volatility signal date exists")
        missing = [value.isoformat() for value in eligible_dates if value not in available]
        if missing:
            raise SyntheticBundleError(
                "PIT universe lacks quarterly snapshots for eligible dates: " + ", ".join(missing)
            )
        if filtered.empty:
            raise SyntheticBundleError("no valid quarterly PIT universe snapshot is available")
        return filtered

    @staticmethod
    def _corporate_action_hook(
        bundle: PriceActionBundle,
    ) -> Callable[[dict[str, dict[str, Any]], pd.Timestamp, pd.DataFrame | None], dict[str, Any]]:
        actions = bundle.actions.copy()
        actions["action_date"] = pd.to_datetime(actions["action_date"]).dt.normalize()
        applied: set[tuple[int, str]] = set()

        def apply(
            positions: dict[str, dict[str, Any]],
            current_date: pd.Timestamp,
            price_data: pd.DataFrame | None = None,
        ) -> dict[str, Any]:
            current = pd.Timestamp(current_date).normalize()
            blocked_tickers: set[str] = set()
            cash_delta = 0.0
            for index, action in actions.iterrows():
                key = (int(index), str(action["action_type"]).casefold())
                if key in applied or action["action_date"] != current:
                    continue
                action_type = key[1]
                if action_type in {"split", "reverse_split"}:
                    ratio = float(action["ratio"])
                    ticker = str(action["ticker"])
                    position = positions.get(ticker)
                    if position is not None:
                        new_shares = float(position["shares"]) * ratio
                        if not math.isfinite(new_shares) or not new_shares.is_integer():
                            raise SyntheticBundleError(
                                "corporate action would create non-integral shares"
                            )
                        position["shares"] = int(new_shares)
                        position["entry_price"] /= ratio
                        position["peak_price"] /= ratio
                        if "reference_price" in position:
                            position["reference_price"] /= ratio
                elif action_type == "suspension":
                    ticker = str(action["ticker"])
                    blocked_tickers.add(ticker)
                    position = positions.get(ticker)
                    if position is not None:
                        position["trading_blocked"] = True
                elif action_type == "delisting":
                    ticker = str(action["ticker"])
                    blocked_tickers.add(ticker)
                    position = positions.pop(ticker, None)
                    if position is not None:
                        recovery = (
                            float(action["recovery_value"])
                            if action.get("confirmed") is True
                            else 0.0
                        )
                        cash_delta += float(position["shares"]) * recovery
                applied.add(key)
            return {
                "cash_delta": cash_delta,
                "blocked_tickers": tuple(sorted(blocked_tickers)),
            }

        return apply


__all__ = [
    "CutoffValidationEvidence",
    "LowVolatilityExecutionAdapter",
    "PITUniverseEvidence",
    "ValidatedPriceActionEvidence",
    "ValidatedRawPriceActionBundle",
    "build_cutoff_validation_evidence",
    "build_pit_universe_evidence",
    "build_validated_price_action_evidence",
    "validate_low_vol_execution_config",
]
