"""Focused offline tests for Phase 2 raw-evidence contracts."""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import pytest

from k200_low_vol.data import ProductionBundleError, RawArtifact, capture_raw_artifact, validate_raw_artifact


ENDPOINT = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"


def _artifact(**changes: Any) -> RawArtifact:
    values: dict[str, Any] = {
        "response_bytes": b'{"rows":[{"date":"2024-12-31"}]}',
        "endpoint": ENDPOINT,
        "build_identifier": "fixture-build",
        "query_params": {"start_date": "2024-12-31", "end_date": "2024-12-31"},
        "retrieved_at": datetime(2026, 8, 22, tzinfo=timezone.utc),
        "requested_observation_dates": ("2024-12-31",),
        "response_date_evidence": ("2024-12-31",),
        "row_count": 1,
        "schema_version": "phase2-production-evidence-v1",
        "role": "sessions",
    }
    values.update(changes)
    return RawArtifact(**values)


def test_capture_preserves_exact_request_and_recomputes_bytes_hash() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def transport(endpoint: str, params: Mapping[str, Any]) -> bytes:
        calls.append((endpoint, dict(params)))
        return b'{"rows":[{"date":"2024-12-31"}]}'

    artifact = capture_raw_artifact(
        transport,
        endpoint=ENDPOINT,
        build_identifier="fixture-build",
        query_params={"start_date": "2024-12-31", "end_date": "2024-12-31"},
        retrieved_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        requested_observation_dates=["2024-12-31"],
        response_date_evidence=["2024-12-31"],
        row_count=1,
        role="sessions",
    )
    assert calls == [(ENDPOINT, {"start_date": "2024-12-31", "end_date": "2024-12-31"})]
    assert validate_raw_artifact(artifact).raw_sha256 is not None
    assert artifact.retrieved_at_seoul is not None
    assert artifact.retrieved_at_utc is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_observation_dates", ("2025-01-01",)),
        ("response_date_evidence", ("2025-01-01",)),
        ("cache_dates", ("2025-01-01",)),
    ],
)
def test_observation_response_and_cache_cutoff_rejects_post_cutoff(field: str, value: tuple[str, ...]) -> None:
    with pytest.raises(ProductionBundleError):
        _artifact(**{field: value})


def test_query_cutoff_is_checked_before_transport() -> None:
    called = False

    def transport(_: str, __: Mapping[str, Any]) -> bytes:
        nonlocal called
        called = True
        return b'{"rows":[{"date":"2024-01-02"}]}'

    with pytest.raises(ProductionBundleError):
        capture_raw_artifact(
            transport,
            endpoint=ENDPOINT,
            build_identifier="fixture-build",
            query_params={"start_date": "2025-01-01"},
            retrieved_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
            requested_observation_dates=["2024-01-02"],
            response_date_evidence=["2024-01-02"],
            row_count=1,
            role="sessions",
        )
    assert called is False


@pytest.mark.parametrize("endpoint", ["krx/fixture", "https://evil.example/raw"])
def test_arbitrary_endpoint_rejected(endpoint: str) -> None:
    with pytest.raises(ProductionBundleError):
        _artifact(endpoint=endpoint)


def test_naive_retrieval_timestamp_and_parsed_response_mismatch_reject() -> None:
    with pytest.raises(ProductionBundleError):
        _artifact(retrieved_at=datetime(2026, 8, 22))
    with pytest.raises(ProductionBundleError):
        _artifact(response_date_evidence=("2024-01-02",))


def test_retrieval_timestamp_may_be_after_cutoff_and_tampering_is_rejected() -> None:
    artifact = _artifact(retrieved_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    object.__setattr__(artifact, "response_bytes", b'{"rows":[{"date":"2024-12-31"}]}' + b"x")
    with pytest.raises(ProductionBundleError):
        validate_raw_artifact(artifact)
