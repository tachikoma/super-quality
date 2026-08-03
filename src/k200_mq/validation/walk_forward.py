"""Pure, train-only primitives for K200MQ expanding-window validation.

This module deliberately does not run a backtest or load data.  It defines the
fixed fold schedule, the small candidate library whose parameters have current
runtime semantics, and deterministic selection/serialization for a later
pipeline integration.  Stop-loss enable/disable and threshold settings have
explicit runtime semantics, but are not included in the default candidate
library.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Self


MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT = (
    "mechanical_expanding_walk_forward_non_pit"
)
VALIDATED_EXPANDING_WALK_FORWARD_PIT = "validated_expanding_walk_forward_pit"
# Lower-case aliases mirror the serialized classification values for callers
# that prefer to import the names exactly as they appear in result manifests.
mechanical_expanding_walk_forward_non_pit = MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT
validated_expanding_walk_forward_pit = VALIDATED_EXPANDING_WALK_FORWARD_PIT

CANDIDATE_LIBRARY_VERSION = "k200mq-wf-candidates-v2"
DEFAULT_MIN_EXITS = 5
DEFAULT_TIE_TOLERANCE = 0.05
OBJECTIVE_TRAIN_SHARPE = "train_sharpe"


def classify_walk_forward_result(*, pit_valid: bool) -> str:
    """Return the conservative result label for the pure validation core.

    A bare boolean is not provenance evidence.  The pure core therefore keeps
    the mechanical label even when a caller passes ``True``; promotion to a
    validated PIT label is deferred until the actual validator outputs are
    connected to the runner.
    """
    if not isinstance(pit_valid, bool):
        raise TypeError("pit_valid must be an actual bool")
    return MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT


# A short alias is useful to callers that do not need the longer function name.
classify_result = classify_walk_forward_result


@dataclass(frozen=True, slots=True)
class FoldSpec:
    """One expanding-window train/test split."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        values = (self.train_start, self.train_end, self.test_start, self.test_end)
        if not all(isinstance(value, date) and not isinstance(value, datetime) for value in values):
            raise TypeError("FoldSpec boundaries must be datetime.date values")
        if not self.train_start <= self.train_end < self.test_start <= self.test_end:
            raise ValueError("FoldSpec boundaries must be ordered train then test")

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-ready representation of the fold."""
        return {
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
        }


EXPANDING_WINDOW_FOLDS: tuple[FoldSpec, ...] = (
    FoldSpec(date(2015, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 12, 31)),
    FoldSpec(date(2015, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31)),
    FoldSpec(date(2015, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 12, 31)),
    FoldSpec(date(2015, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 12, 31)),
    FoldSpec(date(2015, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
)


def get_expanding_window_folds() -> tuple[FoldSpec, ...]:
    """Return the immutable five-fold 2015-2024 schedule."""
    return EXPANDING_WINDOW_FOLDS


# Compatibility-friendly names for callers that prefer noun-style constants.
FOLD_SPECS = EXPANDING_WINDOW_FOLDS
get_expanding_folds = get_expanding_window_folds


def _freeze_parameters(parameters: Mapping[str, Any]) -> MappingProxyType:
    """Copy and freeze a candidate's simple config payload."""
    if not isinstance(parameters, Mapping):
        raise TypeError("candidate parameters must be a mapping")
    frozen = dict(sorted(parameters.items()))
    for key, value in frozen.items():
        if not isinstance(key, str) or not isinstance(value, (bool, int, float, str)):
            raise TypeError("candidate parameters must contain JSON scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"candidate parameter {key!r} must be finite")
    return MappingProxyType(frozen)


_LIBRARY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_library_version(value: Any, field_name: str = "library_version") -> str:
    """Require a non-coerced, serialized library-version token."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not _LIBRARY_VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is malformed")
    return value


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    """Immutable versioned candidate configuration.

    ``parameters`` contains only overrides with current runtime semantics.  It
    intentionally excludes quality sub-weights, unsupported portfolio/liquidity
    settings, ``MAX_HOLDINGS`` and the short momentum window because those are
    not safe candidate dimensions here.
    """

    candidate_id: str
    parameters: Mapping[str, Any]
    library_version: str = CANDIDATE_LIBRARY_VERSION

    def __post_init__(self) -> None:
        if not self.candidate_id or not isinstance(self.candidate_id, str):
            raise ValueError("candidate_id must be a non-empty string")
        _validate_library_version(self.library_version)
        object.__setattr__(self, "parameters", _freeze_parameters(self.parameters))

    @property
    def id(self) -> str:
        """Short alias for the stable candidate identifier."""
        return self.candidate_id

    @property
    def config(self) -> Mapping[str, Any]:
        """Return the immutable parameter payload."""
        return self.parameters

    @property
    def params(self) -> Mapping[str, Any]:
        """Alias for the immutable parameter payload."""
        return self.parameters

    def payload(self) -> dict[str, Any]:
        """Return the stable candidate payload used in manifests."""
        return {
            "candidate_id": self.candidate_id,
            "library_version": self.library_version,
            "parameters": dict(self.parameters),
        }

    def config_hash(self) -> str:
        """Return a stable SHA-256 hash of the parameter payload."""
        return _stable_hash(dict(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready candidate representation."""
        return self.payload()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        """Reconstruct a candidate from :meth:`to_dict` output."""
        return cls(
            candidate_id=str(payload["candidate_id"]),
            parameters=dict(payload.get("parameters", {})),
            library_version=_validate_library_version(
                payload.get("library_version", CANDIDATE_LIBRARY_VERSION)
            ),
        )


BASE_CANDIDATE = CandidateSpec(
    "BASE",
    {"TOP_N": 20, "REGIME_FILTER_ENABLED": True},
)

DEFAULT_CANDIDATE_LIBRARY: tuple[CandidateSpec, ...] = (
    BASE_CANDIDATE,
    CandidateSpec("TOP_N_10", {"TOP_N": 10, "REGIME_FILTER_ENABLED": True}),
    CandidateSpec("TOP_N_30", {"TOP_N": 30, "REGIME_FILTER_ENABLED": True}),
    CandidateSpec("REGIME_OFF", {"TOP_N": 20, "REGIME_FILTER_ENABLED": False}),
)
CANDIDATE_LIBRARY = DEFAULT_CANDIDATE_LIBRARY


def get_candidate_library() -> tuple[CandidateSpec, ...]:
    """Return the immutable conservative candidate library."""
    return DEFAULT_CANDIDATE_LIBRARY


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def candidate_config_hash(candidate: CandidateSpec | Mapping[str, Any]) -> str:
    """Hash a candidate's stable parameter payload."""
    if isinstance(candidate, CandidateSpec):
        return candidate.config_hash()
    return _stable_hash(dict(candidate))


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """One candidate's training-only score and validity status."""

    candidate_id: str
    train_sharpe: float | None
    n_exits: int | None
    valid: bool = True
    status: str = "valid"

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise TypeError("CandidateScore.valid must be an actual bool")

    @property
    def score(self) -> float | None:
        """Alias for callers that use the generic score name."""
        return self.train_sharpe

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "train_sharpe": _serializable_train_sharpe(self.train_sharpe),
            "n_exits": _serializable_exit_count(self.n_exits),
            "valid": self.valid,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Serializable output of a train-only candidate selection."""

    candidate_library_version: str
    train_cutoff: str | None
    train_scores: tuple[CandidateScore, ...]
    selected_candidate: CandidateSpec
    objective_name: str
    selected_config_hash: str
    classification: str = MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT
    minimum_exits: int = DEFAULT_MIN_EXITS
    tie_tolerance: float = DEFAULT_TIE_TOLERANCE

    def __post_init__(self) -> None:
        _validate_library_version(self.candidate_library_version)
        object.__setattr__(self, "train_cutoff", _normalise_cutoff(self.train_cutoff))

    @property
    def all_train_scores(self) -> tuple[CandidateScore, ...]:
        """Explicit alias emphasizing that no test scores are included."""
        return self.train_scores

    @property
    def selected_candidate_id(self) -> str:
        return self.selected_candidate.candidate_id

    @property
    def selected(self) -> CandidateSpec:
        """Alias for the selected candidate."""
        return self.selected_candidate

    @property
    def config_hash(self) -> str:
        return self.selected_config_hash

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready, deterministic selection manifest."""
        return {
            "candidate_library_version": self.candidate_library_version,
            "train_cutoff": self.train_cutoff,
            "train_scores": [score.to_dict() for score in self.train_scores],
            "selected_candidate": self.selected_candidate.to_dict(),
            "objective_name": self.objective_name,
            "config_hash": self.selected_config_hash,
            "classification": self.classification,
            "minimum_exits": self.minimum_exits,
            "tie_tolerance": self.tie_tolerance,
        }

    def to_json(self) -> str:
        """Serialize this result with stable key and list ordering."""
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        """Reconstruct a result from :meth:`to_dict` output."""
        scores_payload = payload.get("train_scores", [])
        scores = tuple(
            CandidateScore(
                candidate_id=str(item["candidate_id"]),
                train_sharpe=item.get("train_sharpe"),
                n_exits=item.get("n_exits"),
                valid=_strict_bool(item.get("valid", True), "valid"),
                status=str(item.get("status", "valid")),
            )
            for item in scores_payload
        )
        selected = CandidateSpec.from_dict(payload["selected_candidate"])
        return cls(
            candidate_library_version=_validate_library_version(
                payload["candidate_library_version"], "candidate_library_version"
            ),
            train_cutoff=_normalise_cutoff(payload.get("train_cutoff")),
            train_scores=scores,
            selected_candidate=selected,
            objective_name=str(payload["objective_name"]),
            selected_config_hash=str(payload.get("config_hash", selected.config_hash())),
            classification=str(
                payload.get(
                    "classification",
                    MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
                )
            ),
            minimum_exits=int(payload.get("minimum_exits", DEFAULT_MIN_EXITS)),
            tie_tolerance=float(payload.get("tie_tolerance", DEFAULT_TIE_TOLERANCE)),
        )

    @classmethod
    def from_json(cls, payload: str) -> Self:
        return cls.from_dict(json.loads(payload))


def _normalise_cutoff(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid ISO train cutoff: {value!r}") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"invalid ISO train cutoff: {value!r}")
        return parsed.isoformat()
    raise TypeError("train_cutoff must be date-like, an ISO date string, or None")


def _strict_bool(value: Any, field_name: str) -> bool:
    """Require a real JSON/Python boolean instead of truthiness coercion."""
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be an actual bool")
    return value


def _serializable_train_sharpe(value: Any) -> float | None:
    """Return a finite JSON number for a train metric, or JSON ``null``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        metric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return metric if math.isfinite(metric) else None


def _serializable_exit_count(value: Any) -> int | None:
    """Return a JSON-safe exit count while preserving valid integer counts."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


_AMBIGUOUS_SCORE_FIELDS = frozenset({"sharpe", "sharpe_ratio", "score"})
_SCORE_MAPPING_FIELDS = frozenset(
    {
        "candidate_id",
        "train_sharpe",
        "n_exits",
        "test_sharpe",
        "valid",
        "status",
        *_AMBIGUOUS_SCORE_FIELDS,
    }
)


def _score_from_mapping(
    candidate_id: str,
    payload: Mapping[str, Any],
) -> CandidateScore:
    missing_fields = [
        field for field in ("train_sharpe", "n_exits") if field not in payload
    ]
    ambiguous_fields = sorted(_AMBIGUOUS_SCORE_FIELDS.intersection(payload))
    if "test_sharpe" in payload and "train_sharpe" not in payload:
        ambiguous_fields.append("test_sharpe")
    if ambiguous_fields:
        fields = ", ".join(ambiguous_fields)
        raise TypeError(
            "candidate score mappings must use explicit train_sharpe; "
            f"ambiguous score field(s): {fields}"
        )
    if missing_fields:
        fields = ", ".join(missing_fields)
        raise TypeError(f"candidate score mappings require explicit field(s): {fields}")

    valid = _strict_bool(payload.get("valid", True), "valid")
    return CandidateScore(
        candidate_id=str(payload.get("candidate_id", candidate_id)),
        train_sharpe=payload["train_sharpe"],
        n_exits=payload["n_exits"],
        valid=valid,
        status=str(payload.get("status", "valid")),
    )


def _normalise_scores(
    candidate_scores: CandidateScore
    | Mapping[str, Any]
    | Iterable[CandidateScore | Mapping[str, Any]],
) -> list[CandidateScore]:
    if isinstance(candidate_scores, CandidateScore):
        return [candidate_scores]
    if isinstance(candidate_scores, Mapping):
        if _SCORE_MAPPING_FIELDS.intersection(candidate_scores):
            payload = {str(key): value for key, value in candidate_scores.items()}
            return [_score_from_mapping("", payload)]
        normalised: list[CandidateScore] = []
        for candidate_id, payload in candidate_scores.items():
            if isinstance(payload, CandidateScore):
                normalised.append(payload)
            elif isinstance(payload, Mapping):
                normalised.append(_score_from_mapping(str(candidate_id), payload))
            else:
                raise TypeError(
                    "candidate score mappings must contain CandidateScore or a mapping "
                    "with explicit train_sharpe and n_exits fields"
                )
        return normalised

    normalised = []
    for item in candidate_scores:
        if isinstance(item, CandidateScore):
            normalised.append(item)
        elif isinstance(item, Mapping):
            normalised.append(_score_from_mapping("", item))
        else:
            raise TypeError("candidate scores must contain CandidateScore or mappings")
    return normalised


def _validated_score(
    score: CandidateScore,
    candidate_ids: set[str],
    minimum_exits: int,
) -> CandidateScore:
    """Apply selection eligibility rules without inspecting any test metric."""
    if score.candidate_id not in candidate_ids:
        return CandidateScore(
            score.candidate_id,
            score.train_sharpe,
            score.n_exits,
            valid=False,
            status="unknown_candidate",
        )
    if not score.valid:
        return CandidateScore(
            score.candidate_id,
            score.train_sharpe,
            score.n_exits,
            valid=False,
            status=score.status if score.status != "valid" else "invalid_candidate",
        )
    if score.status.lower() in {"invalid", "failed", "ineligible"}:
        return CandidateScore(
            score.candidate_id,
            score.train_sharpe,
            score.n_exits,
            valid=False,
            status=score.status,
        )
    try:
        train_sharpe = float(score.train_sharpe)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        train_sharpe = math.nan
    if not math.isfinite(train_sharpe):
        return CandidateScore(
            score.candidate_id,
            score.train_sharpe,
            score.n_exits,
            valid=False,
            status="invalid_non_finite_train_sharpe",
        )
    if isinstance(score.n_exits, bool) or not isinstance(score.n_exits, int):
        return CandidateScore(
            score.candidate_id,
            score.train_sharpe,
            score.n_exits,
            valid=False,
            status="invalid_exit_count",
        )
    if score.n_exits < minimum_exits:
        return CandidateScore(
            score.candidate_id,
            score.train_sharpe,
            score.n_exits,
            valid=False,
            status="insufficient_exits",
        )
    return CandidateScore(
        score.candidate_id,
        train_sharpe,
        score.n_exits,
        valid=True,
        status="valid",
    )


def select_candidate(
    candidate_scores: CandidateScore
    | Mapping[str, Any]
    | Iterable[CandidateScore | Mapping[str, Any]],
    train_cutoff: date | datetime | str | None = None,
    *,
    candidate_library: Sequence[CandidateSpec] = DEFAULT_CANDIDATE_LIBRARY,
    minimum_exits: int = DEFAULT_MIN_EXITS,
    min_exits: int | None = None,
    tie_tolerance: float = DEFAULT_TIE_TOLERANCE,
    classification: str = MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
) -> SelectionResult:
    """Select a candidate using only finite, valid training Sharpe values.

    Values named ``test_*`` are intentionally not part of the input contract
    and are never read.  A score is eligible only when its candidate is in the
    supplied library, ``valid`` is true, Sharpe is finite, and the exit count
    meets the configured minimum.  Among scores within ``tie_tolerance`` of
    the best score, ``BASE`` wins; otherwise the candidate id is the stable
    secondary key.
    """
    if min_exits is not None:
        if minimum_exits != DEFAULT_MIN_EXITS and minimum_exits != min_exits:
            raise ValueError("minimum_exits and min_exits disagree")
        minimum_exits = min_exits
    if minimum_exits < 0:
        raise ValueError("minimum_exits must be non-negative")
    if not math.isfinite(tie_tolerance) or tie_tolerance < 0:
        raise ValueError("tie_tolerance must be finite and non-negative")
    if not candidate_library:
        raise ValueError("candidate_library must not be empty")
    candidate_ids = [candidate.candidate_id for candidate in candidate_library]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_library contains duplicate candidate ids")
    library_versions = {candidate.library_version for candidate in candidate_library}
    if len(library_versions) != 1:
        raise ValueError("candidate_library contains mixed library versions")
    if classification == VALIDATED_EXPANDING_WALK_FORWARD_PIT:
        raise ValueError(
            "validated PIT classification is deferred until provenance validators are wired"
        )
    if classification != MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT:
        raise ValueError(f"unknown walk-forward classification: {classification!r}")

    raw_scores = _normalise_scores(candidate_scores)
    if len({score.candidate_id for score in raw_scores}) != len(raw_scores):
        raise ValueError("candidate_scores contains duplicate candidate ids")
    records = [
        _validated_score(score, set(candidate_ids), minimum_exits)
        for score in raw_scores
    ]
    by_id = {record.candidate_id: record for record in records}
    # Include absent library members so the serialized result records every
    # candidate's training status, independent of input mapping order.
    for candidate_id in candidate_ids:
        if candidate_id not in by_id:
            by_id[candidate_id] = CandidateScore(
                candidate_id,
                None,
                None,
                valid=False,
                status="missing_train_score",
            )
    records = [by_id[candidate_id] for candidate_id in sorted(by_id)]
    eligible = [record for record in records if record.valid]
    if not eligible:
        raise ValueError("no candidate has a finite valid train Sharpe and enough exits")

    eligible_values = [
        (record, float(record.train_sharpe))
        for record in eligible
        if record.train_sharpe is not None
    ]
    best_score = max(value for _, value in eligible_values)
    tied = [
        record
        for record, value in eligible_values
        if best_score - value <= tie_tolerance + 1e-12
    ]
    selected_record = next(
        (record for record in tied if record.candidate_id == "BASE"),
        min(tied, key=lambda record: record.candidate_id),
    )
    selected = next(
        candidate
        for candidate in candidate_library
        if candidate.candidate_id == selected_record.candidate_id
    )
    return SelectionResult(
        candidate_library_version=selected.library_version,
        train_cutoff=_normalise_cutoff(train_cutoff),
        train_scores=tuple(records),
        selected_candidate=selected,
        objective_name=OBJECTIVE_TRAIN_SHARPE,
        selected_config_hash=selected.config_hash(),
        classification=classification,
        minimum_exits=minimum_exits,
        tie_tolerance=tie_tolerance,
    )


# A descriptive alias for callers that want to make the training-only nature
# explicit at the call site.
select_best_train_candidate = select_candidate


__all__ = [
    "BASE_CANDIDATE",
    "CANDIDATE_LIBRARY_VERSION",
    "CANDIDATE_LIBRARY",
    "CandidateScore",
    "CandidateSpec",
    "DEFAULT_CANDIDATE_LIBRARY",
    "DEFAULT_MIN_EXITS",
    "DEFAULT_TIE_TOLERANCE",
    "EXPANDING_WINDOW_FOLDS",
    "FOLD_SPECS",
    "FoldSpec",
    "MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT",
    "OBJECTIVE_TRAIN_SHARPE",
    "SelectionResult",
    "VALIDATED_EXPANDING_WALK_FORWARD_PIT",
    "candidate_config_hash",
    "classify_result",
    "classify_walk_forward_result",
    "get_candidate_library",
    "get_expanding_folds",
    "get_expanding_window_folds",
    "select_best_train_candidate",
    "select_candidate",
    "mechanical_expanding_walk_forward_non_pit",
    "validated_expanding_walk_forward_pit",
]
