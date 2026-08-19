"""Tests for the pure K200MQ expanding-window validation core."""

from datetime import date, datetime
import json
import math

import pytest

from k200_mq.validation.walk_forward import (
    BASE_CANDIDATE,
    DEFAULT_CANDIDATE_LIBRARY,
    MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT,
    VALIDATED_EXPANDING_WALK_FORWARD_PIT,
    CandidateScore,
    CandidateSpec,
    FoldSpec,
    candidate_config_hash,
    classify_walk_forward_result,
    get_expanding_window_folds,
    select_candidate,
)


def test_expanding_window_schedule_is_exact_and_immutable() -> None:
    assert get_expanding_window_folds() == (
        FoldSpec(date(2015, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 12, 31)),
        FoldSpec(date(2015, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31)),
        FoldSpec(date(2015, 1, 1), date(2021, 12, 31), date(2022, 1, 1), date(2022, 12, 31)),
        FoldSpec(date(2015, 1, 1), date(2022, 12, 31), date(2023, 1, 1), date(2023, 12, 31)),
        FoldSpec(date(2015, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
    )
    with pytest.raises((AttributeError, TypeError)):
        get_expanding_window_folds()[0].train_end = date(2020, 1, 1)  # type: ignore[misc]


def test_fold_spec_rejects_datetime_boundaries_and_serializes_dates() -> None:
    fold = FoldSpec(date(2015, 1, 1), date(2019, 12, 31), date(2020, 1, 1), date(2020, 12, 31))

    assert fold.to_dict() == {
        "train_start": "2015-01-01",
        "train_end": "2019-12-31",
        "test_start": "2020-01-01",
        "test_end": "2020-12-31",
    }
    with pytest.raises(TypeError, match="datetime.date"):
        FoldSpec(
            datetime(2015, 1, 1),
            date(2019, 12, 31),
            date(2020, 1, 1),
            date(2020, 12, 31),
        )


def test_candidate_library_is_versioned_and_conservative() -> None:
    ids = [candidate.candidate_id for candidate in DEFAULT_CANDIDATE_LIBRARY]

    assert ids == [
        "BASE", "MOM60", "MOM70", "SL20", "SL20_CASH10",
        "TOP_N_10", "TOP_N_30", "REGIME_OFF",
        "REGIME_70", "REGIME_50", "REGIME_30",
    ]
    assert all(
        candidate.library_version == "k200mq-wf-candidates-v4"
        for candidate in DEFAULT_CANDIDATE_LIBRARY
    )
    assert all("MAX_HOLDINGS" not in candidate.parameters for candidate in DEFAULT_CANDIDATE_LIBRARY)
    assert all("QUALITY_WEIGHT_ROE" not in candidate.parameters for candidate in DEFAULT_CANDIDATE_LIBRARY)
    # The weight-axis candidates carry runtime-safe momentum/quality overrides
    # so the train pass can select them instead of post-hoc sensitivity search.
    weights = {
        candidate.candidate_id: (
            candidate.parameters.get("WEIGHT_MOMENTUM"),
            candidate.parameters.get("WEIGHT_QUALITY"),
        )
        for candidate in DEFAULT_CANDIDATE_LIBRARY
    }
    assert weights["MOM60"] == (0.6, 0.4)
    assert weights["MOM70"] == (0.7, 0.3)
    assert weights["SL20"] == (0.7, 0.3)
    assert weights["SL20_CASH10"] == (0.7, 0.3)
    assert weights["BASE"] == (None, None)
    # Grid-derived stop-loss/cash-buffer candidates carry runtime-only engine
    # settings that must not be confused with factor/schedule dimensions.
    by_id = {candidate.candidate_id: candidate.parameters for candidate in DEFAULT_CANDIDATE_LIBRARY}
    assert by_id["SL20"]["SL_STOP_LOSS"] == -0.20
    assert "MIN_CASH_RATIO" not in by_id["SL20"]
    assert by_id["SL20_CASH10"]["SL_STOP_LOSS"] == -0.20
    assert by_id["SL20_CASH10"]["MIN_CASH_RATIO"] == 0.10
    # Regime reduction-axis candidates carry runtime-safe reduction ratios so
    # the train pass can choose an optimal ratio instead of binary on/off.
    assert by_id["REGIME_70"] == {
        "TOP_N": 20, "REGIME_FILTER_ENABLED": True, "REGIME_REDUCTION": 0.70,
    }
    assert by_id["REGIME_50"] == {
        "TOP_N": 20, "REGIME_FILTER_ENABLED": True, "REGIME_REDUCTION": 0.50,
    }
    assert by_id["REGIME_30"] == {
        "TOP_N": 20, "REGIME_FILTER_ENABLED": True, "REGIME_REDUCTION": 0.30,
    }
    assert by_id["REGIME_OFF"]["REGIME_FILTER_ENABLED"] is False
    assert "REGIME_REDUCTION" not in by_id["REGIME_OFF"]


def _scores(**sharpes: float) -> dict[str, dict[str, object]]:
    return {
        candidate_id: {"train_sharpe": sharpe, "n_exits": 10, "valid": True}
        for candidate_id, sharpe in sharpes.items()
    }


def test_train_only_selection_is_deterministic() -> None:
    scores = _scores(BASE=0.40, TOP_N_10=0.70, TOP_N_30=0.20, REGIME_OFF=0.30)
    first = select_candidate(scores, "2019-12-31")
    second = select_candidate(dict(reversed(list(scores.items()))), "2019-12-31")

    assert first.selected_candidate_id == "TOP_N_10"
    assert first.to_json() == second.to_json()
    assert first.objective_name == "train_sharpe"
    assert first.train_cutoff == "2019-12-31"


def test_tie_within_five_hundredths_prefers_base_then_stable_id() -> None:
    base_tie = select_candidate(_scores(BASE=0.50, TOP_N_10=0.54), min_exits=5)
    assert base_tie.selected_candidate_id == "BASE"

    without_base = (
        CandidateSpec("Z_CANDIDATE", {"TOP_N": 20}),
        CandidateSpec("A_CANDIDATE", {"TOP_N": 20}),
    )
    stable_tie = select_candidate(
        {
            "Z_CANDIDATE": {"train_sharpe": 0.50, "n_exits": 5},
            "A_CANDIDATE": {"train_sharpe": 0.54, "n_exits": 5},
        },
        candidate_library=without_base,
    )
    assert stable_tie.selected_candidate_id == "A_CANDIDATE"

    outside_tie = select_candidate(_scores(BASE=0.50, TOP_N_10=0.56))
    assert outside_tie.selected_candidate_id == "TOP_N_10"


def test_invalid_scores_and_minimum_exits_are_not_eligible() -> None:
    result = select_candidate(
        {
            "BASE": {"train_sharpe": math.nan, "n_exits": 100},
            "TOP_N_10": {"train_sharpe": 1.0, "n_exits": 2},
            "TOP_N_30": {"train_sharpe": 0.2, "n_exits": 5, "valid": True},
        },
        minimum_exits=5,
    )
    statuses = {score.candidate_id: score.status for score in result.train_scores}

    assert result.selected_candidate_id == "TOP_N_30"
    assert statuses["BASE"] == "invalid_non_finite_train_sharpe"
    assert statuses["TOP_N_10"] == "insufficient_exits"

    with pytest.raises(ValueError, match="no candidate"):
        select_candidate({"BASE": {"train_sharpe": None, "n_exits": 5}})


def test_test_scores_cannot_influence_selection() -> None:
    first = select_candidate(
        {
            "BASE": {"train_sharpe": 0.50, "test_sharpe": -10.0, "n_exits": 5},
            "TOP_N_10": {"train_sharpe": 0.60, "test_sharpe": 10.0, "n_exits": 5},
        }
    )
    second = select_candidate(
        {
            "BASE": {"train_sharpe": 0.50, "test_sharpe": 10_000.0, "n_exits": 5},
            "TOP_N_10": {"train_sharpe": 0.60, "test_sharpe": -10_000.0, "n_exits": 5},
        }
    )

    assert first.selected_candidate_id == second.selected_candidate_id == "TOP_N_10"
    assert all("test" not in key for key in first.to_dict())


@pytest.mark.parametrize("alias", ["sharpe", "sharpe_ratio", "score", "test_sharpe"])
def test_ambiguous_score_aliases_are_rejected(alias: str) -> None:
    with pytest.raises(TypeError, match="explicit train_sharpe"):
        select_candidate(
            {
                "BASE": {alias: 10_000.0, "n_exits": 5},
                "TOP_N_10": {"train_sharpe": 0.5, "n_exits": 5},
            }
        )


def test_score_mappings_require_explicit_train_metric_and_exit_count() -> None:
    with pytest.raises(TypeError, match="n_exits"):
        select_candidate({"BASE": {"train_sharpe": 0.5}})
    with pytest.raises(TypeError, match="train_sharpe"):
        select_candidate({"BASE": {"n_exits": 5}})


def test_train_cutoff_rejects_trailing_date_text() -> None:
    with pytest.raises(ValueError, match="invalid ISO train cutoff"):
        select_candidate(
            {"BASE": {"train_sharpe": 0.5, "n_exits": 5}},
            "2019-12-31 trailing",
        )


def test_invalid_train_metrics_serialize_as_null_in_strict_json() -> None:
    result = select_candidate(
        {
            "BASE": {"train_sharpe": math.nan, "n_exits": 5},
            "TOP_N_10": {"train_sharpe": math.inf, "n_exits": 5},
            "TOP_N_30": {"train_sharpe": 0.5, "n_exits": 5},
        }
    )

    payload = result.to_json()
    decoded = json.loads(payload)
    scores = {score["candidate_id"]: score for score in decoded["train_scores"]}

    assert result.selected_candidate_id == "TOP_N_30"
    assert scores["BASE"]["train_sharpe"] is None
    assert scores["TOP_N_10"]["train_sharpe"] is None
    assert scores["TOP_N_30"]["train_sharpe"] == 0.5
    assert "NaN" not in payload
    assert "Infinity" not in payload
    assert json.loads(result.from_json(payload).to_json()) == decoded


def test_selection_serialization_and_hash_are_stable() -> None:
    first = CandidateSpec("CUSTOM", {"REGIME_FILTER_ENABLED": True, "TOP_N": 20})
    second = CandidateSpec("CUSTOM", {"TOP_N": 20, "REGIME_FILTER_ENABLED": True})

    assert candidate_config_hash(first) == candidate_config_hash(second)
    assert first.to_dict() == second.to_dict()

    result = select_candidate(
        {"BASE": {"train_sharpe": 0.5, "n_exits": 5}},
        date(2019, 12, 31),
    )
    restored = result.from_json(result.to_json())

    assert json.loads(result.to_json()) == json.loads(restored.to_json())
    assert result.config_hash == BASE_CANDIDATE.config_hash()
    assert result.to_dict()["candidate_library_version"] == BASE_CANDIDATE.library_version


def test_deserialization_rejects_non_string_or_malformed_library_versions() -> None:
    candidate_payload = BASE_CANDIDATE.to_dict()
    for invalid_version in (None, 1, "", "version with spaces"):
        candidate_payload["library_version"] = invalid_version
        with pytest.raises((TypeError, ValueError)):
            CandidateSpec.from_dict(candidate_payload)

    result = select_candidate(
        {"BASE": {"train_sharpe": 0.5, "n_exits": 5}},
        date(2019, 12, 31),
    )
    selection_payload = result.to_dict()
    for invalid_version in (None, 1, "", "version with spaces"):
        selection_payload["candidate_library_version"] = invalid_version
        with pytest.raises((TypeError, ValueError)):
            result.from_dict(selection_payload)


def test_pit_classification_is_explicit() -> None:
    assert classify_walk_forward_result(pit_valid=False) == (
        MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT
    )
    # A boolean is not validator output and must never promote the pure core.
    assert classify_walk_forward_result(pit_valid=True) == (
        MECHANICAL_EXPANDING_WALK_FORWARD_NON_PIT
    )
    # A bare string classification is not validator evidence either.
    with pytest.raises(ValueError, match="validator evidence"):
        select_candidate(
            {"BASE": {"train_sharpe": 0.5, "n_exits": 5}},
            classification=VALIDATED_EXPANDING_WALK_FORWARD_PIT,
        )
    # Actual validator outputs authorize the validated label.
    validated = select_candidate(
        {"BASE": {"train_sharpe": 0.5, "n_exits": 5}},
        classification=VALIDATED_EXPANDING_WALK_FORWARD_PIT,
        pit_valid_evidence={"universe_pit_valid": True, "financial_pit_valid": True},
    )
    assert validated.classification == VALIDATED_EXPANDING_WALK_FORWARD_PIT
    assert validated.selected_candidate_id == "BASE"


def test_valid_mapping_flag_is_not_truthiness_coerced() -> None:
    with pytest.raises(TypeError, match="actual bool"):
        select_candidate(
            {
                "BASE": {"train_sharpe": 0.5, "n_exits": 5, "valid": "false"},
                "TOP_N_10": {"train_sharpe": 0.4, "n_exits": 5},
            }
        )


def test_candidate_score_is_accepted_directly() -> None:
    result = select_candidate([CandidateScore("BASE", 0.5, 5)])

    assert result.selected_candidate == BASE_CANDIDATE
