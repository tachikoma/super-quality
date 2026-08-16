"""Pure orchestration tests for true expanding-window walk-forward runs."""

import json
import math
from dataclasses import replace
from datetime import date

import pytest

from k200_mq.validation.runner import (
    PITValidContext,
    TestEvaluation,
    TrainEvaluation,
    run_walk_forward,
)
from k200_mq.validation.walk_forward import (
    DEFAULT_CANDIDATE_LIBRARY,
    EXPANDING_WINDOW_FOLDS,
    VALIDATED_EXPANDING_WALK_FORWARD_PIT,
    CandidateSpec,
    FoldSpec,
    select_candidate,
)


def _folds() -> tuple[FoldSpec, ...]:
    return EXPANDING_WINDOW_FOLDS


def test_all_candidates_train_once_and_only_selected_candidate_tests() -> None:
    train_calls: list[tuple[int, str]] = []
    test_calls: list[tuple[int, str]] = []

    def train(fold, candidate, config):
        train_calls.append((fold.test_start.year, candidate.candidate_id))
        return {"train_sharpe": 0.5 if candidate.candidate_id == "BASE" else 0.4, "n_exits": 5}

    def test(fold, candidate, config):
        test_calls.append((fold.test_start.year, candidate.candidate_id))
        return {"returns": {fold.test_start: 0.01}, "metrics": {"test_sharpe": 0.2}}

    result = run_walk_forward(_folds(), DEFAULT_CANDIDATE_LIBRARY, train, test)

    assert len(train_calls) == len(_folds()) * len(DEFAULT_CANDIDATE_LIBRARY)
    assert len(test_calls) == len(_folds())
    assert all(candidate_id == "BASE" for _, candidate_id in test_calls)
    assert result.valid is True


def test_test_scores_cannot_change_the_train_selected_candidate() -> None:
    candidates = (
        CandidateSpec("A", {"TOP_N": 10}),
        CandidateSpec("B", {"TOP_N": 20}),
    )
    observed: list[str] = []

    def train(fold, candidate, config):
        return {"train_sharpe": 1.0 if candidate.candidate_id == "A" else 0.5, "n_exits": 5}

    def test(fold, candidate, config):
        observed.append(candidate.candidate_id)
        # This deliberately favors B, but arrives after selection.
        return {"returns": {fold.test_start: -100.0 if candidate.candidate_id == "A" else 100.0}}

    result = run_walk_forward(_folds()[:1], candidates, train, test)

    assert observed == ["A"]
    assert result.folds[0].selected_candidate_id == "A"


def test_all_train_folds_finish_before_any_test_callback() -> None:
    events: list[str] = []
    test_started = False
    train_after_test: list[int] = []

    def train(fold, candidate, config):
        if test_started:
            train_after_test.append(fold.test_start.year)
        events.append(f"train-{fold.test_start.year}")
        return {"train_sharpe": 0.5, "n_exits": 5}

    def test(fold, candidate, config):
        nonlocal test_started
        test_started = True
        events.append(f"test-{fold.test_start.year}")
        return {"returns": {fold.test_start: 0.01}}

    result = run_walk_forward(_folds(), DEFAULT_CANDIDATE_LIBRARY[:1], train, test)

    assert result.valid is True
    assert train_after_test == []
    assert events == [
        *(f"train-{fold.test_start.year}" for fold in _folds()),
        *(f"test-{fold.test_start.year}" for fold in _folds()),
    ]


def test_selection_is_serialized_and_visible_before_test_callback() -> None:
    seen: list[object] = []

    def train(fold, candidate, config):
        return {"train_sharpe": 1.0, "n_exits": 5}

    def test(fold, candidate, config):
        seen.append(config["selection"])
        assert config["selection_json"] == json.dumps(
            json.loads(config["selection_json"]),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with pytest.raises(TypeError):
            config["selection"]["selected_candidate"] = {}  # type: ignore[index]
        return {"returns": {fold.test_start: 0.01}}

    run_walk_forward(_folds()[:1], DEFAULT_CANDIDATE_LIBRARY, train, test)

    assert seen[0]["selected_candidate"]["candidate_id"] == "BASE"  # type: ignore[index]


def test_train_state_is_not_passed_to_test_and_configs_are_immutable() -> None:
    train_configs: list[object] = []
    test_configs: list[object] = []

    def train(fold, candidate, config):
        train_configs.append(config)
        with pytest.raises(TypeError):
            config["mutate"] = True  # type: ignore[index]
        return {"train_sharpe": 0.5, "n_exits": 5, "mutable_train_payload": []}

    def test(fold, candidate, config):
        test_configs.append(config)
        assert "mutable_train_payload" not in config
        assert "selection" in config
        with pytest.raises(TypeError):
            config["mutate"] = True  # type: ignore[index]
        return {"returns": {fold.test_start: 0.01}}

    run_walk_forward(_folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:2], train, test)

    assert len(train_configs) == 2
    assert len(test_configs) == 1
    assert train_configs[0] is not test_configs[0]


def test_duplicate_and_non_finite_oos_dates_are_rejected() -> None:
    def train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def duplicate_test(fold, candidate, config):
        return {"returns": [(fold.test_start, 0.01), (fold.test_start, 0.02)]}

    with pytest.raises(ValueError, match="duplicate OOS date"):
        run_walk_forward(_folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, duplicate_test)

    def non_finite_test(fold, candidate, config):
        return {"returns": {fold.test_start: math.nan}}

    with pytest.raises(ValueError, match="non-finite OOS return"):
        run_walk_forward(_folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, non_finite_test)

    def trailing_garbage_test(fold, candidate, config):
        return {"returns": {"2020-01-01garbage": 0.01}}

    with pytest.raises(ValueError, match="invalid OOS date"):
        run_walk_forward(_folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, trailing_garbage_test)


def test_stitched_oos_dates_match_the_2020_to_2024_fold_test_dates() -> None:
    def train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def test(fold, candidate, config):
        return TestEvaluation(
            returns={fold.test_start: 0.01},
            metrics={"test_sharpe": 0.1},
            results={"candidate": candidate.candidate_id},
        )

    result = run_walk_forward(_folds(), DEFAULT_CANDIDATE_LIBRARY[:1], train, test)

    assert result.stitched_oos_dates == tuple(
        fold.test_start for fold in _folds()
    )
    assert [row["date"] for row in result.to_csv_rows()] == [
        fold.test_start.isoformat() for fold in _folds()
    ]


def test_invalid_train_and_test_results_are_explicit() -> None:
    def invalid_train(fold, candidate, config):
        return {"train_sharpe": None, "n_exits": 0, "valid": False, "status": "no_data"}

    def should_not_run(fold, candidate, config):
        raise AssertionError("test must not run after invalid train selection")

    train_invalid = run_walk_forward(
        _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:2], invalid_train, should_not_run
    )
    assert train_invalid.valid is False
    assert train_invalid.folds[0].status == "invalid_train_selection"
    assert train_invalid.folds[0].selection is None
    assert all(not score.valid for score in train_invalid.folds[0].train_scores)

    def valid_train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def invalid_test(fold, candidate, config):
        return {"valid": False, "status": "missing_test_data", "reason": "no OOS rows"}

    test_invalid = run_walk_forward(
        _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], valid_train, invalid_test
    )
    assert test_invalid.valid is False
    assert test_invalid.folds[0].status == "missing_test_data"
    assert test_invalid.folds[0].error == "no OOS rows"
    assert test_invalid.stitched_oos_returns == ()

    empty_test = run_walk_forward(
        _folds()[:1],
        DEFAULT_CANDIDATE_LIBRARY[:1],
        valid_train,
        lambda fold, candidate, config: {"returns": {}},
    )
    assert empty_test.folds[0].status == "invalid_empty_test_returns"
    assert empty_test.folds[0].valid is False


def test_validated_classification_requires_mapping_evidence() -> None:
    def train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def test(fold, candidate, config):
        return {"returns": {fold.test_start: 0.01}}

    # A non-mapping context is not validator evidence.
    with pytest.raises(ValueError, match="pit_valid_context"):
        run_walk_forward(
            _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, test,
            pit_valid_context=True,  # type: ignore[arg-type]
        )

    # The validated classification cannot be requested without evidence.
    with pytest.raises(ValueError, match="validator evidence"):
        run_walk_forward(
            _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, test,
            classification=VALIDATED_EXPANDING_WALK_FORWARD_PIT,
        )

    # The deprecated wrapper is still rejected: it is not a raw validator map.
    with pytest.raises(ValueError, match="pit_valid_context"):
        run_walk_forward(
            _folds()[:1],
            DEFAULT_CANDIDATE_LIBRARY[:1],
            train,
            test,
            pit_valid_context=PITValidContext(True, {"source": "synthetic"}),  # type: ignore[arg-type]
        )


def test_validated_classification_passes_with_validator_evidence() -> None:
    def train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def test(fold, candidate, config):
        return {"returns": {fold.test_start: 0.01}}

    result = run_walk_forward(
        _folds()[:1],
        DEFAULT_CANDIDATE_LIBRARY[:1],
        train,
        test,
        classification=VALIDATED_EXPANDING_WALK_FORWARD_PIT,
        pit_valid_context={
            "universe_pit_valid": True,
            "financial_pit_valid": True,
        },
    )

    assert result.valid is True
    assert result.classification == VALIDATED_EXPANDING_WALK_FORWARD_PIT
    assert result.pit_valid_context == {
        "universe_pit_valid": True,
        "financial_pit_valid": True,
    }
    assert result.folds[0].classification == VALIDATED_EXPANDING_WALK_FORWARD_PIT


def test_valid_flags_require_actual_booleans() -> None:
    with pytest.raises(TypeError, match="TrainEvaluation.valid"):
        TrainEvaluation(train_sharpe=0.5, n_exits=5, valid="false")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="TestEvaluation.valid"):
        TestEvaluation(returns={}, valid="false")  # type: ignore[arg-type]

    def invalid_train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5, "valid": "false"}

    with pytest.raises(TypeError, match="valid must be an actual bool"):
        run_walk_forward(
            _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], invalid_train,
            lambda fold, candidate, config: {"returns": {fold.test_start: 0.01}},
        )

    def valid_train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def invalid_test(fold, candidate, config):
        return {"returns": {fold.test_start: 0.01}, "valid": "false"}

    with pytest.raises(TypeError, match="valid must be an actual bool"):
        run_walk_forward(_folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], valid_train, invalid_test)


def test_fold_schedule_and_candidate_versions_are_validated() -> None:
    first = FoldSpec(
        train_start=_folds()[0].train_start,
        train_end=_folds()[0].train_end,
        test_start=_folds()[0].test_start,
        test_end=_folds()[0].test_end,
    )
    bad_train_end = FoldSpec(
        train_start=first.train_start,
        train_end=first.train_end,
        test_start=date(2021, 1, 1),
        test_end=date(2021, 12, 31),
    )

    with pytest.raises(ValueError, match="train_end"):
        run_walk_forward(
            (first, bad_train_end), DEFAULT_CANDIDATE_LIBRARY[:1],
            lambda fold, candidate, config: {"train_sharpe": 0.5, "n_exits": 5},
            lambda fold, candidate, config: {"returns": {fold.test_start: 0.01}},
        )

    mixed_versions = (
        CandidateSpec("A", {"TOP_N": 10}, library_version="v1"),
        CandidateSpec("B", {"TOP_N": 20}, library_version="v2"),
    )
    with pytest.raises(ValueError, match="mixed library versions"):
        run_walk_forward(
            _folds()[:1], mixed_versions,
            lambda fold, candidate, config: {"train_sharpe": 0.5, "n_exits": 5},
            lambda fold, candidate, config: {"returns": {fold.test_start: 0.01}},
        )


def test_fixed_origin_rejects_a_later_train_start() -> None:
    later_origin = FoldSpec(
        train_start=date(2016, 1, 1),
        train_end=date(2020, 12, 31),
        test_start=date(2021, 1, 1),
        test_end=date(2021, 12, 31),
    )

    with pytest.raises(ValueError, match="train_start values must be identical"):
        run_walk_forward(
            (_folds()[0], later_origin),
            DEFAULT_CANDIDATE_LIBRARY[:1],
            lambda fold, candidate, config: {"train_sharpe": 0.5, "n_exits": 5},
            lambda fold, candidate, config: {"returns": {fold.test_start: 0.01}},
        )


@pytest.mark.parametrize("cutoff", [None, date(2019, 12, 30)])
def test_selection_cutoff_must_match_fold_train_end(cutoff) -> None:
    test_calls: list[object] = []

    def train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def selector(scores, fold_cutoff, *, candidate_library, classification):
        return select_candidate(
            scores,
            cutoff,
            candidate_library=candidate_library,
            classification=classification,
        )

    def test(fold, candidate, config):
        test_calls.append(candidate.candidate_id)
        return {"returns": {fold.test_start: 0.01}}

    result = run_walk_forward(
        _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, test, selector=selector
    )

    assert result.valid is False
    assert result.folds[0].status == "invalid_train_selection"
    assert "train_cutoff" in (result.folds[0].error or "")
    assert test_calls == []


def test_altered_frozen_candidate_parameters_do_not_reach_test_callback() -> None:
    test_calls: list[str] = []

    def train(fold, candidate, config):
        return {"train_sharpe": 0.5, "n_exits": 5}

    def selector(scores, fold_cutoff, *, candidate_library, classification):
        selected = select_candidate(
            scores,
            fold_cutoff,
            candidate_library=candidate_library,
            classification=classification,
        )
        altered_parameters = dict(selected.selected_candidate.parameters)
        altered_parameters["TOP_N"] = 999
        altered_candidate = CandidateSpec(
            selected.selected_candidate.candidate_id,
            altered_parameters,
            library_version=selected.selected_candidate.library_version,
        )
        # Keep the library hash in the manifest while changing the frozen
        # candidate payload, which is the integrity gap this guards.
        return replace(selected, selected_candidate=altered_candidate)

    def test(fold, candidate, config):
        test_calls.append(candidate.candidate_id)
        return {"returns": {fold.test_start: 0.01}}

    result = run_walk_forward(
        _folds()[:1], DEFAULT_CANDIDATE_LIBRARY[:1], train, test, selector=selector
    )

    assert result.valid is False
    assert result.folds[0].status == "invalid_train_selection"
    assert "mismatched candidate config hash" in (result.folds[0].error or "")
    assert test_calls == []
