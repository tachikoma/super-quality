"""Pure orchestration for expanding-window walk-forward validation.

The runner intentionally stops at an in-memory result.  It does not know how
prices, fundamentals, or a portfolio engine are loaded.  A caller supplies
evaluators for one fold and one candidate; the runner supplies the fold,
candidate, and an immutable configuration mapping.  The true-walkforward CLI
adapter supplies those evaluators from one prepared K200MQ bundle.

The separation between :mod:`walk_forward` and this module is deliberate:
``walk_forward`` owns the schedule and train-only selector, while this module
owns the order in which those pure pieces are called.

This is orchestration only and remains independent of the live data loaders.
The CLI integration supplies prepared K200MQ inputs, while the runner still
emits only the mechanical non-PIT classification until actual universe and
financial provenance validator outputs support a validated PIT result.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from datetime import date, datetime
import json
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, TypeAlias, cast

from k200_mq.validation.walk_forward import (
    MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
    VALIDATED_EXPANDING_WALK_FORWARD_PIT,
    CandidateScore,
    CandidateSpec,
    FoldSpec,
    SelectionResult,
    candidate_config_hash,
    select_candidate,
)


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


def _is_secret_field(name: object) -> bool:
    return str(name).upper() in _CREDENTIAL_FIELD_NAMES


def _public_mapping(value: Any) -> dict[str, Any]:
    """Return a secret-free mapping suitable for config provenance."""
    if value is None:
        return {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
    elif isinstance(value, Mapping):
        value = dict(value)
    else:
        return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _public_value(item)
        for key, item in value.items()
        if not _is_secret_field(key)
    }


def _public_value(value: Any) -> Any:
    """Recursively remove credential fields from nested provenance values."""
    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if not _is_secret_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value


def _config_hash(config: Mapping[str, Any]) -> str:
    """Hash the complete public runtime configuration, not just candidate params."""
    frozen = _freeze_json_value(_public_mapping(config))
    encoded = json.dumps(
        _thaw_json_value(frozen),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PITValidContext:
    """Deprecated placeholder for a future validator-backed PIT context.

    This record is intentionally not accepted by :func:`run_walk_forward`.
    A boolean and descriptive evidence are not enough to establish PIT
    validity; the actual universe and financial provenance validator outputs
    must be connected before the validated label can be emitted.
    """

    pit_valid: bool
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.pit_valid, bool):
            raise TypeError("PITValidContext.pit_valid must be a bool")
        object.__setattr__(self, "evidence", _freeze_json_value(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pit_valid": self.pit_valid,
            "evidence": _thaw_json_value(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class TrainEvaluation:
    """Optional convenience return type for a train evaluator."""

    train_sharpe: float | None
    n_exits: int | None
    valid: bool = True
    status: str = "valid"
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise TypeError("TrainEvaluation.valid must be an actual bool")
        object.__setattr__(self, "metrics", _freeze_json_value(self.metrics))

    def to_score(self, candidate_id: str) -> CandidateScore:
        return CandidateScore(
            candidate_id=candidate_id,
            train_sharpe=self.train_sharpe,
            n_exits=self.n_exits,
            valid=self.valid,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class TestEvaluation:
    """Optional convenience return type for a test evaluator.

    ``returns`` may be a mapping, a pandas-like ``Series`` (the runner does
    not import pandas), or an iterable of ``(date, return)`` pairs.  It is
    normalized and validated by :func:`run_walk_forward`.
    """

    returns: object
    metrics: Mapping[str, Any] = field(default_factory=dict)
    results: Mapping[str, Any] = field(default_factory=dict)
    valid: bool = True
    status: str = "valid"
    reason: str | None = None
    __test__ = False

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise TypeError("TestEvaluation.valid must be an actual bool")
        object.__setattr__(self, "metrics", _freeze_json_value(self.metrics))
        object.__setattr__(self, "results", _freeze_json_value(self.results))


@dataclass(frozen=True, slots=True)
class OOSReturnPoint:
    """One validated out-of-sample daily return."""

    date: date
    daily_return: float
    fold_number: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "daily_return": self.daily_return,
            "fold": self.fold_number,
        }


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Deterministic report for one fold, including invalid folds."""

    fold_number: int
    fold: FoldSpec
    classification: str
    candidate_config_hashes: Mapping[str, str]
    train_scores: tuple[CandidateScore, ...]
    selection: SelectionResult | None
    selected_candidate: CandidateSpec | None
    selected_config_hash: str | None
    test_metrics: Mapping[str, Any]
    test_results: Mapping[str, Any]
    test_returns: tuple[OOSReturnPoint, ...]
    valid: bool
    status: str
    error: str | None = None
    effective_candidate_configs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    effective_candidate_config_hashes: Mapping[str, str] = field(default_factory=dict)
    selected_effective_config: Mapping[str, Any] | None = None
    selected_effective_config_hash: str | None = None
    expected_oos_dates: tuple[date, ...] = ()
    returned_oos_dates: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_config_hashes",
            _freeze_json_value(self.candidate_config_hashes),
        )
        object.__setattr__(self, "test_metrics", _freeze_json_value(self.test_metrics))
        object.__setattr__(self, "test_results", _freeze_json_value(self.test_results))
        object.__setattr__(
            self,
            "effective_candidate_configs",
            _freeze_json_value(self.effective_candidate_configs),
        )
        object.__setattr__(
            self,
            "effective_candidate_config_hashes",
            _freeze_json_value(self.effective_candidate_config_hashes),
        )
        if self.selected_effective_config is not None:
            object.__setattr__(
                self,
                "selected_effective_config",
                _freeze_json_value(self.selected_effective_config),
            )

    @property
    def selected_candidate_id(self) -> str | None:
        return self.selected_candidate.candidate_id if self.selected_candidate else None

    def to_dict(self) -> dict[str, Any]:
        selection = self.selection.to_dict() if self.selection is not None else None
        selected = self.selected_candidate.to_dict() if self.selected_candidate else None
        scores = [score.to_dict() for score in self.train_scores]
        test = {
            "valid": self.valid and bool(self.test_returns),
            "status": "valid" if self.valid else self.status,
            "metrics": _thaw_json_value(self.test_metrics),
            "results": _thaw_json_value(self.test_results),
            "returns": [point.to_dict() for point in self.test_returns],
        }
        return {
            "fold_number": self.fold_number,
            "fold": self.fold.to_dict(),
            "classification": self.classification,
            "valid": self.valid,
            "status": self.status,
            "error": self.error,
            "candidate_config_hashes": _thaw_json_value(self.candidate_config_hashes),
            "effective_candidate_config_hashes": _thaw_json_value(
                self.effective_candidate_config_hashes
            ),
            "effective_config_hashes": _thaw_json_value(
                self.effective_candidate_config_hashes
            ),
            "effective_candidate_configs": _thaw_json_value(
                self.effective_candidate_configs
            ),
            "effective_configs": _thaw_json_value(self.effective_candidate_configs),
            "train_scores": scores,
            "selection": selection,
            "selected_candidate": selected,
            "selected_config_hash": self.selected_config_hash,
            "candidate_config_hash": self.selected_config_hash,
            "effective_config_hash": self.selected_effective_config_hash,
            "selected_effective_config_hash": self.selected_effective_config_hash,
            "selected_effective_config": (
                _thaw_json_value(self.selected_effective_config)
                if self.selected_effective_config is not None
                else None
            ),
            "train": {
                "scores": scores,
                "selection": selection,
            },
            "test": test,
            "test_metrics": _thaw_json_value(self.test_metrics),
            "test_results": _thaw_json_value(self.test_results),
            "test_returns": [point.to_dict() for point in self.test_returns],
            "expected_oos_dates": [
                point_date.isoformat() for point_date in self.expected_oos_dates
            ],
            "returned_oos_dates": [
                point_date.isoformat() for point_date in self.returned_oos_dates
            ],
        }


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """Complete in-memory walk-forward report and stitched OOS series."""

    classification: str
    candidate_library_version: str
    candidate_config_hashes: Mapping[str, str]
    folds: tuple[FoldResult, ...]
    stitched_oos_returns: tuple[OOSReturnPoint, ...]
    valid: bool
    status: str
    pit_valid_context: Mapping[str, Any] | None = None
    base_runtime_config: Mapping[str, Any] = field(default_factory=dict)
    base_runtime_config_hash: str | None = None
    effective_candidate_configs_by_fold: Mapping[str, Mapping[str, Mapping[str, Any]]] = (
        field(default_factory=dict)
    )
    effective_candidate_config_hashes_by_fold: Mapping[str, Mapping[str, str]] = (
        field(default_factory=dict)
    )
    preparation_manifest_context: Mapping[str, Any] = field(default_factory=dict)
    git_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_config_hashes",
            _freeze_json_value(self.candidate_config_hashes),
        )
        object.__setattr__(self, "base_runtime_config", _freeze_json_value(
            _public_mapping(self.base_runtime_config),
        ))
        object.__setattr__(
            self,
            "effective_candidate_configs_by_fold",
            _freeze_json_value(self.effective_candidate_configs_by_fold),
        )
        object.__setattr__(
            self,
            "effective_candidate_config_hashes_by_fold",
            _freeze_json_value(self.effective_candidate_config_hashes_by_fold),
        )
        object.__setattr__(
            self,
            "preparation_manifest_context",
            _freeze_json_value(self.preparation_manifest_context),
        )
        object.__setattr__(self, "git_state", _freeze_json_value(self.git_state))
        if self.pit_valid_context is not None:
            object.__setattr__(
                self,
                "pit_valid_context",
                _freeze_json_value(self.pit_valid_context),
            )

    @property
    def stitched_oos_dates(self) -> tuple[date, ...]:
        return tuple(point.date for point in self.stitched_oos_returns)

    @property
    def oos_returns(self) -> tuple[OOSReturnPoint, ...]:
        """Compatibility-friendly alias for the stitched return points."""
        return self.stitched_oos_returns

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "candidate_library_version": self.candidate_library_version,
            "candidate_config_hashes": _thaw_json_value(self.candidate_config_hashes),
            "base_runtime_config": _thaw_json_value(self.base_runtime_config),
            "base_runtime_config_hash": self.base_runtime_config_hash,
            "effective_candidate_configs_by_fold": _thaw_json_value(
                self.effective_candidate_configs_by_fold
            ),
            "effective_candidate_config_hashes_by_fold": _thaw_json_value(
                self.effective_candidate_config_hashes_by_fold
            ),
            "effective_config_hashes_by_fold": _thaw_json_value(
                self.effective_candidate_config_hashes_by_fold
            ),
            "effective_configs_by_fold": _thaw_json_value(
                self.effective_candidate_configs_by_fold
            ),
            "preparation_manifest_context": _thaw_json_value(
                self.preparation_manifest_context
            ),
            "git": _thaw_json_value(self.git_state),
            "pit_valid_context": (
                _thaw_json_value(self.pit_valid_context)
                if self.pit_valid_context is not None
                else None
            ),
            "valid": self.valid,
            "status": self.status,
            "folds": [fold.to_dict() for fold in self.folds],
            "stitched_oos_returns": [
                point.to_dict() for point in self.stitched_oos_returns
            ],
            "oos_dates": [point.date.isoformat() for point in self.stitched_oos_returns],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def to_csv_rows(self) -> list[dict[str, Any]]:
        """Return deterministic rows suitable for a CSV writer."""
        return [
            {
                "fold": point.fold_number,
                "date": point.date.isoformat(),
                "daily_return": point.daily_return,
            }
            for point in self.stitched_oos_returns
        ]


TrainEvaluator: TypeAlias = Callable[[FoldSpec, CandidateSpec, Mapping[str, Any]], object]
TestEvaluator: TypeAlias = Callable[[FoldSpec, CandidateSpec, Mapping[str, Any]], object]
TrainSelector: TypeAlias = Callable[..., SelectionResult]


def _freeze_json_value(value: Any) -> Any:
    """Recursively copy JSON-like values into immutable containers."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Mapping):
        items = sorted(((str(key), item) for key, item in value.items()), key=lambda pair: pair[0])
        return MappingProxyType({key: _freeze_json_value(item) for key, item in items})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    raise TypeError(f"value of type {type(value).__name__} is not JSON-compatible")


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _normalise_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    date_method = getattr(value, "date", None)
    if callable(date_method):
        result = date_method()
        if isinstance(result, date) and not isinstance(result, datetime):
            return result
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid OOS date: {value!r}") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"invalid OOS date: {value!r}")
        return parsed
    raise TypeError(f"OOS dates must be date-like, got {type(value).__name__}")


def _normalise_train_score(candidate: CandidateSpec, raw: object) -> CandidateScore:
    if isinstance(raw, TrainEvaluation):
        return raw.to_score(candidate.candidate_id)
    if isinstance(raw, CandidateScore):
        if raw.candidate_id != candidate.candidate_id:
            return CandidateScore(
                candidate.candidate_id,
                raw.train_sharpe,
                raw.n_exits,
                valid=False,
                status="candidate_id_mismatch",
            )
        return raw
    if isinstance(raw, Mapping):
        valid = _require_bool(raw.get("valid", True), "valid")
        candidate_id = str(raw.get("candidate_id", candidate.candidate_id))
        if candidate_id != candidate.candidate_id:
            return CandidateScore(
                candidate.candidate_id,
                raw.get("train_sharpe"),
                raw.get("n_exits"),
                valid=False,
                status="candidate_id_mismatch",
            )
        return CandidateScore(
            candidate.candidate_id,
            raw.get("train_sharpe"),
            raw.get("n_exits"),
            valid=valid,
            status=str(raw.get("status", "valid")),
        )
    return CandidateScore(
        candidate.candidate_id,
        None,
        None,
        valid=False,
        status="invalid_train_result",
    )


def _candidate_config(
    candidate: CandidateSpec,
    classification: str,
    phase: str,
    base_runtime_config: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Build a fresh immutable evaluator config for a candidate.

    ``config_hash`` remains the candidate-library hash used to validate the
    train selection.  ``effective_config_hash`` is the hash of the complete
    secret-free runtime configuration after candidate overrides and is the
    provenance hash used for execution.
    """
    effective_config = _public_mapping(base_runtime_config)
    effective_config.update(_public_mapping(candidate.parameters))
    payload: dict[str, Any] = _public_mapping(candidate.parameters)
    payload.update(
        {
            "candidate_id": candidate.candidate_id,
            "parameters": _public_mapping(candidate.parameters),
            "config_hash": candidate_config_hash(candidate),
            "classification": classification,
            "phase": phase,
            "effective_config": effective_config,
            "effective_config_hash": _config_hash(effective_config),
        }
    )
    return _freeze_json_value(payload)


def _test_config(
    candidate: CandidateSpec,
    classification: str,
    selection_json: str,
    base_runtime_config: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    payload = dict(
        _candidate_config(candidate, classification, "test", base_runtime_config)
    )
    selection_payload = json.loads(selection_json)
    payload.update(
        {
            "selection": selection_payload,
            "selection_json": selection_json,
        }
    )
    return _freeze_json_value(payload)


def _extract_test_evaluation(raw: object) -> TestEvaluation:
    if isinstance(raw, TestEvaluation):
        return raw
    if isinstance(raw, Mapping):
        return_key = next(
            (
                key
                for key in (
                    "returns",
                    "test_returns",
                    "daily_returns",
                    "return_series",
                    "oos_returns",
                )
                if key in raw
            ),
            None,
        )
        if return_key is None:
            # A date-keyed mapping is itself a return series.  Otherwise the
            # result is malformed and will receive an explicit invalid status.
            if raw and all(_is_date_key(key) for key in raw):
                returns = raw
                metrics: Mapping[str, Any] = {}
            else:
                returns = None
                metrics = {}
        else:
            returns = raw[return_key]
            metrics_value = raw.get("metrics", raw.get("test_metrics", {}))
            metrics = metrics_value if isinstance(metrics_value, Mapping) else {}
        results_value = raw.get("results", raw.get("test_results", {}))
        results = results_value if isinstance(results_value, Mapping) else {}
        if (
            return_key is not None
            and "metrics" not in raw
            and "test_metrics" not in raw
            and "results" not in raw
            and "test_results" not in raw
        ):
            reserved = {
                "returns",
                "test_returns",
                "daily_returns",
                "return_series",
                "oos_returns",
                "valid",
                "status",
                "reason",
                "test_metrics",
                "test_results",
            }
            metrics = {str(key): value for key, value in raw.items() if key not in reserved}
        return TestEvaluation(
            returns=returns,
            metrics=metrics,
            results=results,
            valid=_require_bool(raw.get("valid", True), "valid"),
            status=str(raw.get("status", "valid")),
            reason=(str(raw["reason"]) if raw.get("reason") is not None else None),
        )
    return TestEvaluation(returns=raw, valid=True)


def _normalise_returns(
    returns: object,
    fold: FoldSpec,
    fold_number: int,
) -> tuple[OOSReturnPoint, ...]:
    if returns is None:
        return ()
    if isinstance(returns, Mapping):
        items: Iterable[tuple[Any, Any]] = returns.items()
    else:
        items_method = getattr(returns, "items", None)
        if callable(items_method):
            items = cast(Iterable[tuple[Any, Any]], items_method())
        else:
            try:
                items = cast(Iterable[tuple[Any, Any]], iter(cast(Any, returns)))
            except TypeError as exc:
                raise TypeError("OOS returns must be a mapping or date/value iterable") from exc

    points: list[OOSReturnPoint] = []
    seen: set[date] = set()
    for item in items:
        try:
            raw_date, raw_return = item
        except (TypeError, ValueError) as exc:
            raise ValueError("OOS return entries must be (date, value) pairs") from exc
        point_date = _normalise_date(raw_date)
        if point_date in seen:
            raise ValueError(f"duplicate OOS date in fold {fold_number}: {point_date.isoformat()}")
        seen.add(point_date)
        if not fold.test_start <= point_date <= fold.test_end:
            raise ValueError(
                f"OOS date {point_date.isoformat()} is outside fold {fold_number} test period"
            )
        if isinstance(raw_return, bool):
            raise ValueError(f"non-finite OOS return on {point_date.isoformat()}")
        try:
            numeric_return = float(raw_return)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"non-finite OOS return on {point_date.isoformat()}") from exc
        if not math.isfinite(numeric_return):
            raise ValueError(f"non-finite OOS return on {point_date.isoformat()}")
        points.append(OOSReturnPoint(point_date, numeric_return, fold_number))
    return tuple(sorted(points, key=lambda point: point.date))


def _normalise_expected_dates(
    expected_dates: Iterable[Any],
    fold: FoldSpec,
    fold_number: int,
) -> tuple[date, ...]:
    """Normalize the prepared trading calendar used for exact OOS coverage."""
    seen: set[date] = set()
    for raw_date in expected_dates:
        point_date = _normalise_date(raw_date)
        if point_date in seen:
            raise ValueError(
                f"duplicate expected OOS date in fold {fold_number}: "
                f"{point_date.isoformat()}"
            )
        if not fold.test_start <= point_date <= fold.test_end:
            raise ValueError(
                f"expected OOS date {point_date.isoformat()} is outside fold "
                f"{fold_number} test period"
            )
        seen.add(point_date)
    return tuple(sorted(seen))


def _coverage_error(
    expected_dates: tuple[date, ...],
    returned_dates: tuple[date, ...],
) -> str | None:
    """Describe an exact expected/returned OOS date mismatch."""
    if not expected_dates:
        return None
    expected = set(expected_dates)
    returned = set(returned_dates)
    missing = sorted(expected - returned)
    extra = sorted(returned - expected)
    if not missing and not extra:
        return None
    details: list[str] = []
    if missing:
        details.append("missing=" + ",".join(item.isoformat() for item in missing))
    if extra:
        details.append("unexpected=" + ",".join(item.isoformat() for item in extra))
    return "OOS date coverage mismatch (" + "; ".join(details) + ")"


def _is_date_key(value: object) -> bool:
    try:
        _normalise_date(value)
    except (TypeError, ValueError):
        return False
    return True


def _require_bool(value: object, field_name: str) -> bool:
    """Require an actual bool instead of accepting truthy values."""
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be an actual bool")
    return value


def _validate_inputs(
    fold_specs: Sequence[FoldSpec],
    candidate_specs: Sequence[CandidateSpec],
    classification: str,
) -> tuple[tuple[FoldSpec, ...], tuple[CandidateSpec, ...]]:
    folds = tuple(fold_specs)
    candidates = tuple(candidate_specs)
    if not folds:
        raise ValueError("fold_specs must not be empty")
    if not candidates:
        raise ValueError("candidate_specs must not be empty")
    if not all(isinstance(fold, FoldSpec) for fold in folds):
        raise TypeError("fold_specs must contain FoldSpec values")
    if not all(isinstance(candidate, CandidateSpec) for candidate in candidates):
        raise TypeError("candidate_specs must contain CandidateSpec values")
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_specs contains duplicate candidate ids")
    library_versions = {candidate.library_version for candidate in candidates}
    if len(library_versions) != 1:
        raise ValueError("candidate_specs contains mixed library versions")
    for previous, current in zip(folds, folds[1:]):
        if current.train_start != previous.train_start:
            raise ValueError("fold train_start values must be identical for fixed-origin folds")
        if current.train_end <= previous.train_end:
            raise ValueError("fold train_end values must strictly increase")
        if current.train_end < previous.test_end:
            raise ValueError("each expanding train period must follow the prior test period")
        if current.test_start <= previous.test_start:
            raise ValueError("fold test_start values must strictly increase")
        if current.test_end <= previous.test_end:
            raise ValueError("fold test_end values must strictly increase")
        if current.test_start <= previous.test_end:
            raise ValueError("fold test periods must be chronological and non-overlapping")
    if classification == VALIDATED_EXPANDING_WALK_FORWARD_PIT:
        raise ValueError(
            "validated PIT classification is deferred until provenance validators are wired"
        )
    if classification != MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT:
        raise ValueError(f"unknown walk-forward classification: {classification!r}")
    return folds, candidates


@dataclass(frozen=True, slots=True)
class _FrozenTrainFold:
    """Train-phase state held until every fold has been selected."""

    fold_number: int
    fold: FoldSpec
    train_scores: tuple[CandidateScore, ...]
    selection: SelectionResult | None
    selection_json: str | None
    effective_candidate_configs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    effective_candidate_config_hashes: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None


def run_walk_forward(
    fold_specs: Sequence[FoldSpec],
    candidate_specs: Sequence[CandidateSpec],
    train_evaluator: TrainEvaluator,
    test_evaluator: TestEvaluator,
    *,
    selector: TrainSelector = select_candidate,
    classification: str = MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
    pit_valid_context: object | None = None,
    base_runtime_config: Mapping[str, Any] | object | None = None,
    preparation_manifest_context: Mapping[str, Any] | None = None,
    git_state: Mapping[str, Any] | None = None,
    expected_test_dates: Mapping[int, Iterable[Any]] | None = None,
) -> WalkForwardResult:
    """Run true expanding-window orchestration without data-side effects.

    The runner has two strict phases.  First every candidate is evaluated and
    selected on every fold's train period; only after all selections are
    serialized and frozen are selected candidates evaluated on test.  Test
    callbacks therefore cannot mutate state that a later train callback could
    observe.

    Evaluator exceptions and explicitly invalid evaluator results are recorded
    as invalid fold results.  Structural OOS violations (duplicate dates,
    dates outside the fold, or non-finite returns) raise ``ValueError`` because
    silently stitching such data would make the report unsafe.
    """
    if not callable(train_evaluator) or not callable(test_evaluator):
        raise TypeError("train_evaluator and test_evaluator must be callable")
    if not callable(selector):
        raise TypeError("selector must be callable")
    folds, candidates = _validate_inputs(fold_specs, candidate_specs, classification)

    if pit_valid_context is not None:
        raise ValueError(
            "pit_valid_context is unsupported until actual universe and financial "
            "provenance validator outputs are wired"
        )

    candidate_hashes = {
        candidate.candidate_id: candidate_config_hash(candidate)
        for candidate in sorted(candidates, key=lambda item: item.candidate_id)
    }
    candidate_library_version = candidates[0].library_version
    public_base_config = _public_mapping(base_runtime_config)
    base_config_hash = _config_hash(public_base_config)
    frozen_preparation_context = (
        preparation_manifest_context if preparation_manifest_context is not None else {}
    )
    frozen_git_state = git_state if git_state is not None else {}
    expected_dates_by_fold: dict[int, tuple[date, ...]] = {}
    for fold_number, fold in enumerate(folds, start=1):
        raw_expected = (
            expected_test_dates.get(fold_number)
            if expected_test_dates is not None
            else None
        )
        if raw_expected is not None:
            expected_dates_by_fold[fold_number] = _normalise_expected_dates(
                raw_expected, fold, fold_number
            )
    train_phase: list[_FrozenTrainFold] = []

    # Pass 1: evaluate and freeze every train selection before any test callback
    # is allowed to run.  In particular, do not move test evaluation back into
    # this loop: external evaluator state is outside the runner's control.
    for fold_number, fold in enumerate(folds, start=1):
        effective_configs = {
            candidate.candidate_id: _thaw_json_value(
                _candidate_config(
                    candidate,
                    classification,
                    "train",
                    public_base_config,
                )
            )["effective_config"]
            for candidate in candidates
        }
        effective_hashes = {
            candidate_id: _config_hash(config)
            for candidate_id, config in effective_configs.items()
        }
        train_scores: list[CandidateScore] = []
        for candidate in candidates:
            config = _candidate_config(
                candidate,
                classification,
                "train",
                public_base_config,
            )
            try:
                raw_train = train_evaluator(fold, candidate, config)
            except Exception:  # evaluator failures are explicit fold data
                score = CandidateScore(
                    candidate.candidate_id,
                    None,
                    None,
                    valid=False,
                    status="train_evaluator_error",
                )
            else:
                # Normalization is a data-contract boundary.  Do not swallow a
                # malformed ``valid`` flag as an evaluator failure.
                score = _normalise_train_score(candidate, raw_train)
            train_scores.append(score)

        deterministic_scores = tuple(sorted(train_scores, key=lambda score: score.candidate_id))
        try:
            selected = selector(
                deterministic_scores,
                fold.train_end,
                candidate_library=candidates,
                classification=classification,
            )
            if isinstance(selected, Mapping):
                selected = SelectionResult.from_dict(selected)
            if not isinstance(selected, SelectionResult):
                raise TypeError("selector must return SelectionResult")
            if selected.selected_candidate_id not in candidate_hashes:
                raise ValueError("selector returned a candidate outside candidate_specs")
            # Freeze through the exact JSON boundary before constructing test
            # config.  This prevents mutable train-side objects from crossing
            # into test evaluation.
            selection_json = selected.to_json()
            frozen_selection = SelectionResult.from_json(selection_json)
            if frozen_selection.selected_candidate_id not in candidate_hashes:
                raise ValueError("frozen selection returned a candidate outside candidate_specs")
            library_config_hash = candidate_hashes[frozen_selection.selected_candidate_id]
            if frozen_selection.selected_candidate.config_hash() != library_config_hash:
                raise ValueError("frozen selection returned a mismatched candidate config hash")
            expected_cutoff = fold.train_end.isoformat()
            if frozen_selection.train_cutoff != expected_cutoff:
                raise ValueError(
                    "selector returned a missing or mismatched train_cutoff"
                )
            if frozen_selection.classification != classification:
                raise ValueError("selector returned a mismatched walk-forward classification")
            if frozen_selection.candidate_library_version != candidate_library_version:
                raise ValueError("selector returned a mismatched candidate library version")
            if frozen_selection.selected_candidate.library_version != candidate_library_version:
                raise ValueError("selector returned a mismatched candidate library version")
            if (
                frozen_selection.selected_config_hash
                != library_config_hash
            ):
                raise ValueError("selector returned a mismatched selected config hash")
        except Exception as exc:
            train_phase.append(
                _FrozenTrainFold(
                    fold_number=fold_number,
                    fold=fold,
                    train_scores=deterministic_scores,
                    selection=None,
                    selection_json=None,
                    effective_candidate_configs=effective_configs,
                    effective_candidate_config_hashes=effective_hashes,
                    error=str(exc),
                )
            )
        else:
            train_phase.append(
                _FrozenTrainFold(
                    fold_number=fold_number,
                    fold=fold,
                    train_scores=frozen_selection.train_scores,
                    selection=frozen_selection,
                    selection_json=selection_json,
                    effective_candidate_configs=effective_configs,
                    effective_candidate_config_hashes=effective_hashes,
                )
            )

    fold_results: list[FoldResult] = []
    stitched: list[OOSReturnPoint] = []
    stitched_dates: set[date] = set()

    # Pass 2: test only the selections that survived the complete train pass.
    for state in train_phase:
        fold_number = state.fold_number
        fold = state.fold
        frozen_selection = state.selection
        if frozen_selection is None or state.selection_json is None:
            fold_results.append(
                FoldResult(
                    fold_number=fold_number,
                    fold=fold,
                    classification=classification,
                    candidate_config_hashes=candidate_hashes,
                    train_scores=state.train_scores,
                    selection=None,
                    selected_candidate=None,
                    selected_config_hash=None,
                    test_metrics={},
                    test_results={},
                    test_returns=(),
                    valid=False,
                    status="invalid_train_selection",
                    error=state.error,
                    effective_candidate_configs=state.effective_candidate_configs,
                    effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                    expected_oos_dates=expected_dates_by_fold.get(fold_number, ()),
                )
            )
            continue

        selected_candidate = frozen_selection.selected_candidate
        test_config = _test_config(
            selected_candidate,
            classification,
            state.selection_json,
            public_base_config,
        )
        selected_effective_config = state.effective_candidate_configs.get(
            selected_candidate.candidate_id,
        )
        selected_effective_hash = state.effective_candidate_config_hashes.get(
            selected_candidate.candidate_id,
        )
        expected_dates = expected_dates_by_fold.get(fold_number, ())
        try:
            raw_test = test_evaluator(fold, selected_candidate, test_config)
        except Exception as exc:
            fold_results.append(
                FoldResult(
                    fold_number=fold_number,
                    fold=fold,
                    classification=classification,
                    candidate_config_hashes=candidate_hashes,
                    train_scores=frozen_selection.train_scores,
                    selection=frozen_selection,
                    selected_candidate=selected_candidate,
                    selected_config_hash=selected_candidate.config_hash(),
                    test_metrics={},
                    test_results={},
                    test_returns=(),
                    valid=False,
                    status="test_evaluator_error",
                    error=str(exc),
                    effective_candidate_configs=state.effective_candidate_configs,
                    effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                    selected_effective_config=selected_effective_config,
                    selected_effective_config_hash=selected_effective_hash,
                    expected_oos_dates=expected_dates,
                )
            )
            continue

        try:
            test_evaluation = _extract_test_evaluation(raw_test)
            if not test_evaluation.valid:
                invalid_status = (
                    test_evaluation.status
                    if test_evaluation.status != "valid"
                    else "invalid_test_result"
                )
                fold_results.append(
                    FoldResult(
                        fold_number=fold_number,
                        fold=fold,
                        classification=classification,
                        candidate_config_hashes=candidate_hashes,
                        train_scores=frozen_selection.train_scores,
                        selection=frozen_selection,
                        selected_candidate=selected_candidate,
                        selected_config_hash=selected_candidate.config_hash(),
                        test_metrics=test_evaluation.metrics,
                        test_results=test_evaluation.results,
                        test_returns=(),
                        valid=False,
                        status=invalid_status,
                        error=test_evaluation.reason or "test evaluator returned invalid result",
                        effective_candidate_configs=state.effective_candidate_configs,
                        effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                        selected_effective_config=selected_effective_config,
                        selected_effective_config_hash=selected_effective_hash,
                        expected_oos_dates=expected_dates,
                    )
                )
                continue
            test_returns = _normalise_returns(test_evaluation.returns, fold, fold_number)
            if not test_returns:
                fold_results.append(
                    FoldResult(
                        fold_number=fold_number,
                        fold=fold,
                        classification=classification,
                        candidate_config_hashes=candidate_hashes,
                        train_scores=frozen_selection.train_scores,
                        selection=frozen_selection,
                        selected_candidate=selected_candidate,
                        selected_config_hash=selected_candidate.config_hash(),
                        test_metrics=test_evaluation.metrics,
                        test_results=test_evaluation.results,
                        test_returns=(),
                        valid=False,
                        status="invalid_empty_test_returns",
                        error=test_evaluation.reason or "test evaluator returned no OOS rows",
                        effective_candidate_configs=state.effective_candidate_configs,
                        effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                        selected_effective_config=selected_effective_config,
                        selected_effective_config_hash=selected_effective_hash,
                        expected_oos_dates=expected_dates,
                        returned_oos_dates=tuple(point.date for point in test_returns),
                    )
                )
                continue
            coverage_error = _coverage_error(
                expected_dates,
                tuple(point.date for point in test_returns),
            )
            if coverage_error is not None:
                fold_results.append(
                    FoldResult(
                        fold_number=fold_number,
                        fold=fold,
                        classification=classification,
                        candidate_config_hashes=candidate_hashes,
                        train_scores=frozen_selection.train_scores,
                        selection=frozen_selection,
                        selected_candidate=selected_candidate,
                        selected_config_hash=selected_candidate.config_hash(),
                        test_metrics=test_evaluation.metrics,
                        test_results=test_evaluation.results,
                        test_returns=(),
                        valid=False,
                        status="invalid_oos_coverage",
                        error=coverage_error,
                        effective_candidate_configs=state.effective_candidate_configs,
                        effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                        selected_effective_config=selected_effective_config,
                        selected_effective_config_hash=selected_effective_hash,
                        expected_oos_dates=expected_dates,
                        returned_oos_dates=tuple(point.date for point in test_returns),
                    )
                )
                continue
            duplicate = next((point.date for point in test_returns if point.date in stitched_dates), None)
            if duplicate is not None:
                raise ValueError(f"duplicate stitched OOS date: {duplicate.isoformat()}")
            stitched_dates.update(point.date for point in test_returns)
            stitched.extend(test_returns)
            fold_results.append(
                FoldResult(
                    fold_number=fold_number,
                    fold=fold,
                    classification=classification,
                    candidate_config_hashes=candidate_hashes,
                    train_scores=frozen_selection.train_scores,
                    selection=frozen_selection,
                    selected_candidate=selected_candidate,
                    selected_config_hash=selected_candidate.config_hash(),
                    test_metrics=test_evaluation.metrics,
                    test_results=test_evaluation.results,
                    test_returns=test_returns,
                    valid=True,
                    status="valid",
                    effective_candidate_configs=state.effective_candidate_configs,
                    effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                    selected_effective_config=selected_effective_config,
                    selected_effective_config_hash=selected_effective_hash,
                    expected_oos_dates=expected_dates,
                    returned_oos_dates=tuple(point.date for point in test_returns),
                )
            )
        except (ValueError, TypeError):
            # Data-contract violations are deliberately not converted into an
            # apparently valid report; callers must fix the evaluator output.
            raise
        except Exception as exc:
            fold_results.append(
                FoldResult(
                    fold_number=fold_number,
                    fold=fold,
                    classification=classification,
                    candidate_config_hashes=candidate_hashes,
                    train_scores=frozen_selection.train_scores,
                    selection=frozen_selection,
                    selected_candidate=selected_candidate,
                    selected_config_hash=selected_candidate.config_hash(),
                    test_metrics={},
                    test_results={},
                    test_returns=(),
                    valid=False,
                    status="test_evaluator_error",
                    error=str(exc),
                    effective_candidate_configs=state.effective_candidate_configs,
                    effective_candidate_config_hashes=state.effective_candidate_config_hashes,
                    selected_effective_config=selected_effective_config,
                    selected_effective_config_hash=selected_effective_hash,
                    expected_oos_dates=expected_dates,
                )
            )

    stitched.sort(key=lambda point: point.date)
    all_valid = bool(fold_results) and all(fold.valid for fold in fold_results)
    return WalkForwardResult(
        classification=classification,
        candidate_library_version=candidate_library_version,
        candidate_config_hashes=candidate_hashes,
        folds=tuple(fold_results),
        stitched_oos_returns=tuple(stitched),
        valid=all_valid,
        status="valid" if all_valid else "invalid",
        pit_valid_context=None,
        base_runtime_config=public_base_config,
        base_runtime_config_hash=base_config_hash,
        effective_candidate_configs_by_fold={
            str(state.fold_number): state.effective_candidate_configs
            for state in train_phase
        },
        effective_candidate_config_hashes_by_fold={
            str(state.fold_number): state.effective_candidate_config_hashes
            for state in train_phase
        },
        preparation_manifest_context=frozen_preparation_context,
        git_state=frozen_git_state,
    )


# Descriptive aliases make the orchestration layer discoverable without
# duplicating any implementation or introducing side effects.
run_true_walk_forward = run_walk_forward
orchestrate_walk_forward = run_walk_forward


__all__ = [
    "FoldResult",
    "OOSReturnPoint",
    "PITValidContext",
    "TestEvaluation",
    "TrainEvaluation",
    "WalkForwardResult",
    "orchestrate_walk_forward",
    "run_true_walk_forward",
    "run_walk_forward",
]
