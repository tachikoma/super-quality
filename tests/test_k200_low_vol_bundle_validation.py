"""Small response-shaped fixtures for the production root validator."""

from datetime import datetime, timezone
import json
from typing import Any

import pandas as pd
import pytest

from k200_low_vol.data import (
    APPROVED_RAW_CODE_MAP,
    ACTION_MAPPING_VERSION,
    STATUS_MAPPING_VERSION,
    CorporateActionComponent,
    KPI200BenchmarkComponent,
    KRX_DATA_ENDPOINT,
    KRX_SNAPSHOT_BLD,
    PITUniverseComponent,
    ProductionBundleError,
    ProductionOHLCVComponent,
    ProductionSessionComponent,
    RawArtifact,
    SecurityIdentityComponent,
    build_production_bundle,
    validate_production_bundle,
)


SECURITY_ID = "ISU_CD:000001"
DATE = "2024-01-02"


def _artifact(role: str) -> RawArtifact:
    response: dict[str, Any]
    params: dict[str, str]
    if role == "universe":
        response = {
            "trdDd": "20240102",
            "transition_marker": {"as_of_date": DATE, "index_code": "028", "constituent_count": 1, "source": "KRX", "official": True},
            "output": [
                {"ISU_SRT_CD": "000001", "IDX_NM": "KOSPI 200", "IDX_CD": "028", "source_row_key": "row-0"}
            ]
        }
        params = {"bld": KRX_SNAPSHOT_BLD, "indIdx2": "028", "indIdx": "1", "trdDd": "20240102"}
    elif role == "actions":
        response = {
            "coverage": {"security_ids": ["000001"], "start_date": DATE, "end_date": DATE},
            "rows": [{"source_row_key": "row-0", "security_id": "000001", "action_date": DATE,
                       "raw_code": "DIV", "event_id": "event-1", "conflict_key": "conflict-1",
                       "source_identity": "KRX", "resumption_date": None, "resolved": True,
                       "confirmed": True, "ratio": None, "recovery_value": None,
                       "price_adjusted": False, "portfolio_cash": False}],
        }
        params = {"security_ids": ["000001"], "start_date": DATE, "end_date": DATE}
    elif role == "identities":
        response = {"rows": [{"source_row_key": "row-0", "date": DATE, "ISU_CD": "000001",
                               "ticker": "000001", "effective_from": "2020-01-01", "effective_to": None}]}
        params = {"date": DATE}
    elif role == "ohlcv":
        response = {"status_mapping_version": STATUS_MAPPING_VERSION, "rows": [{"source_row_key": "row-0", "security_id": "000001", "date": DATE,
                               "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100,
                               "observed": True, "suspended": False, "stale": False, "missing": False}]}
        params = {"security_ids": ["000001"], "start_date": DATE, "end_date": DATE}
    elif role == "benchmark":
        response = {"rows": [{"source_row_key": "row-0", "date": DATE, "benchmark_close": 2500}]}
        params = {"date": DATE}
    else:
        response = {"rows": [{"source_row_key": "row-0", "date": DATE}]}
        params = {"date": DATE}
    import json

    return RawArtifact(
        response_bytes=json.dumps(response).encode(),
        endpoint=KRX_DATA_ENDPOINT,
        build_identifier=f"fixture-{role}-build",
        query_params=params,
        retrieved_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        requested_observation_dates=(DATE,),
        response_date_evidence=(DATE,),
        row_count=1,
        schema_version="phase2-production-evidence-v1",
        role=role,
    )


def _raw_artifact(role: str, payload: dict[str, Any], params: dict[str, Any], row_count: int | None = None) -> RawArtifact:
    return RawArtifact(
        response_bytes=json.dumps(payload).encode(),
        endpoint=KRX_DATA_ENDPOINT,
        build_identifier=f"adversarial-{role}-build",
        query_params=params,
        retrieved_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
        requested_observation_dates=(DATE,),
        response_date_evidence=(DATE,),
        row_count=len(payload.get("rows", payload.get("output", []))) if row_count is None else row_count,
        schema_version="phase2-production-evidence-v1",
        role=role,
    )


def _bundle(**changes: Any):
    session_artifact = _artifact("sessions")
    identity_artifact = _artifact("identities")
    universe_artifact = _artifact("universe")
    ohlcv_artifact = _artifact("ohlcv")
    action_artifact = _artifact("actions")
    benchmark_artifact = _artifact("benchmark")
    common = {"security_scope": (SECURITY_ID,)}
    identities = SecurityIdentityComponent(
        pd.DataFrame(
            [{
                "security_id": SECURITY_ID,
                "ticker": "000001",
                "effective_from": "2020-01-01",
                "effective_to": None,
                "identity_source": "KRX_ISU_CD",
                "source_artifact_sha256": identity_artifact.raw_sha256,
                "source_row_key": "row-0",
            }]
        ),
        artifacts=(identity_artifact,),
        **common,
    )
    sessions = ProductionSessionComponent(
        pd.DataFrame([{"date": DATE, "source_artifact_sha256": session_artifact.raw_sha256, "source_row_key": "row-0"}]),
        artifacts=(session_artifact,),
        **common,
    )
    universe = PITUniverseComponent(
        pd.DataFrame([{
            "as_of_date": DATE,
            "security_id": SECURITY_ID,
            "ticker": "000001",
            "source_artifact_sha256": universe_artifact.raw_sha256,
            "source_row_key": "row-0",
        }]),
        artifacts=(universe_artifact,),
        coverage={
            "requested_as_of_dates": [DATE],
            "transition_exceptions": {
                DATE: {"allowed_size": 1, "reason": "official transition fixture", "artifact_sha256": universe_artifact.raw_sha256}
            },
        },
        **common,
    )
    ohlcv = ProductionOHLCVComponent(
        pd.DataFrame([{
            "security_id": SECURITY_ID,
            "ticker": "000001",
            "date": DATE,
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 100,
            "observed": True,
            "suspended": False,
            "stale": False,
            "missing": False,
            "source_artifact_sha256": ohlcv_artifact.raw_sha256,
            "source_row_key": "row-0",
        }]),
        artifacts=(ohlcv_artifact,),
        **common,
    )
    action_coverage = {
        "coverage_version": "actions-coverage-v1",
        "security_scope": [SECURITY_ID],
        "date_range": {"start": DATE, "end": DATE},
        "raw_code_mapping_version": ACTION_MAPPING_VERSION,
        "raw_code_mapping": APPROVED_RAW_CODE_MAP,
    }
    actions = CorporateActionComponent(
        pd.DataFrame([{
            "security_id": SECURITY_ID,
            "ticker": "000001",
            "action_date": DATE,
            "action_type": "cash_dividend",
            "raw_code": "DIV",
            "resolved": True,
            "confirmed": True,
            "ratio": None,
            "recovery_value": None,
            "price_adjusted": False,
            "portfolio_cash": False,
            "event_id": "event-1",
            "source_identity": "KRX",
            "conflict_key": "conflict-1",
            "source_artifact_sha256": action_artifact.raw_sha256,
            "source_row_key": "row-0",
        }]),
        artifacts=(action_artifact,),
        coverage=action_coverage,
        **common,
    )
    benchmark = KPI200BenchmarkComponent(
        pd.DataFrame([{"date": DATE, "benchmark_close": 2500, "source_artifact_sha256": benchmark_artifact.raw_sha256, "source_row_key": "row-0"}]),
        artifacts=(benchmark_artifact,),
        **common,
    )
    values: dict[str, Any] = dict(
        sessions=sessions,
        identities=identities,
        universe=universe,
        ohlcv=ohlcv,
        actions=actions,
        benchmark=benchmark,
    )
    values.update(changes)
    return build_production_bundle(**values)


def test_affirmative_root_validation() -> None:
    assert validate_production_bundle(_bundle()).manifest.component_hashes["ohlcv"]


@pytest.mark.parametrize("change", ["duplicate", "adjusted", "bad_range"])
def test_ohlcv_rejections(change: str) -> None:
    bundle = _bundle()
    rows = bundle.ohlcv.rows.copy()
    if change == "duplicate":
        rows = pd.concat([rows, rows], ignore_index=True)
    elif change == "adjusted":
        rows["adjusted_close"] = rows["close"]
    else:
        rows.loc[0, "high"] = 1
    bad = ProductionOHLCVComponent(rows, artifacts=bundle.ohlcv.artifacts, security_scope=bundle.ohlcv.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(ohlcv=bad))


def test_role_specific_artifact_and_root_fingerprint_reject() -> None:
    bundle = _bundle()
    wrong = ProductionSessionComponent(
        bundle.sessions.rows, artifacts=(bundle.identities.artifacts[0],), security_scope=bundle.sessions.security_scope
    )
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(sessions=wrong))
    object.__setattr__(bundle.manifest, "coverage", {**bundle.manifest.coverage, "security_scope": []})
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(bundle)
    bundle = _bundle()
    object.__setattr__(bundle.sessions.artifacts[0], "retrieved_at", datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(bundle)


def test_universe_contract_and_target_size_rejections() -> None:
    bundle = _bundle()
    artifact = bundle.universe.artifacts[0]
    with pytest.raises(ProductionBundleError):
        RawArtifact(
            response_bytes=artifact.response_bytes,
            endpoint=artifact.endpoint,
            build_identifier=artifact.build_identifier,
            query_params={**artifact.query_params, "indIdx2": "999"},
            retrieved_at=artifact.retrieved_at,
            requested_observation_dates=artifact.requested_observation_dates,
            response_date_evidence=artifact.response_date_evidence,
            row_count=1,
            schema_version=artifact.schema_version,
            role="universe",
        )


@pytest.mark.parametrize("change", ["scope", "duplicate", "relabel", "suspension"])
def test_action_coverage_conflict_mapping_and_resumption_rejections(change: str) -> None:
    bundle = _bundle()
    rows = bundle.actions.rows.copy()
    coverage = dict(bundle.actions.coverage)
    if change == "scope":
        coverage["security_scope"] = []
    elif change == "duplicate":
        rows = pd.concat([rows, rows], ignore_index=True)
    elif change == "relabel":
        mapping = dict(APPROVED_RAW_CODE_MAP)
        mapping["DIV"] = "split"
        coverage["raw_code_mapping"] = mapping
    else:
        rows.loc[0, "action_type"] = "suspension"
        rows.loc[0, "raw_code"] = "SUSP"
    bad = CorporateActionComponent(rows, artifacts=bundle.actions.artifacts, coverage=coverage, security_scope=bundle.actions.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(actions=bad))


def test_identity_and_session_rejections() -> None:
    bundle = _bundle()
    rows = bundle.identities.rows.copy()
    rows.loc[0, "security_id"] = "000001"
    bad_identity = SecurityIdentityComponent(rows, artifacts=bundle.identities.artifacts, security_scope=bundle.identities.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(identities=bad_identity))
    rows = bundle.sessions.rows.copy()
    rows.loc[0, "date"] = "2024-01-03"
    bad_sessions = ProductionSessionComponent(rows, artifacts=bundle.sessions.artifacts, security_scope=bundle.sessions.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(sessions=bad_sessions))


@pytest.mark.parametrize("role", ["identities", "ohlcv", "actions", "benchmark"])
def test_canonical_values_cannot_disagree_with_bound_raw_rows(role: str) -> None:
    bundle = _bundle()
    if role == "identities":
        rows = bundle.identities.rows.copy()
        rows.loc[0, "ticker"] = "999999"
        bad = SecurityIdentityComponent(rows, artifacts=bundle.identities.artifacts, security_scope=bundle.identities.security_scope)
        replacement = {"identities": bad}
    elif role == "ohlcv":
        rows = bundle.ohlcv.rows.copy()
        rows.loc[0, "close"] = 99
        bad = ProductionOHLCVComponent(rows, artifacts=bundle.ohlcv.artifacts, security_scope=bundle.ohlcv.security_scope)
        replacement = {"ohlcv": bad}
    elif role == "actions":
        rows = bundle.actions.rows.copy()
        rows.loc[0, "event_id"] = "canonical-only"
        bad = CorporateActionComponent(rows, artifacts=bundle.actions.artifacts, coverage=dict(bundle.actions.coverage), security_scope=bundle.actions.security_scope)
        replacement = {"actions": bad}
    else:
        rows = bundle.benchmark.rows.copy()
        rows.loc[0, "benchmark_close"] = 99
        bad = KPI200BenchmarkComponent(rows, artifacts=bundle.benchmark.artifacts, security_scope=bundle.benchmark.security_scope)
        replacement = {"benchmark": bad}
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(**replacement))


def test_status_flags_and_action_economics_are_not_caller_authored() -> None:
    bundle = _bundle()
    status_rows = bundle.ohlcv.rows.copy()
    status_rows.loc[0, "observed"] = False
    bad_status = ProductionOHLCVComponent(status_rows, artifacts=bundle.ohlcv.artifacts, security_scope=bundle.ohlcv.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(ohlcv=bad_status))

    economics_rows = bundle.actions.rows.copy()
    economics_rows.loc[0, "resolved"] = False
    bad_economics = CorporateActionComponent(economics_rows, artifacts=bundle.actions.artifacts, coverage=dict(bundle.actions.coverage), security_scope=bundle.actions.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(actions=bad_economics))


def test_date_only_raw_rows_and_mismatched_row_count_reject() -> None:
    with pytest.raises(ProductionBundleError):
        _raw_artifact("ohlcv", {"rows": [{"source_row_key": "row-0", "date": DATE}]}, {"date": DATE})
    with pytest.raises(ProductionBundleError):
        _raw_artifact("sessions", {"rows": [{"source_row_key": "row-0", "date": DATE}]}, {"date": DATE}, row_count=2)


def test_action_query_scope_and_range_are_raw_evidence() -> None:
    bundle = _bundle()
    for params in (
        {"security_ids": ["000002"], "start_date": DATE, "end_date": DATE},
        {"security_ids": ["000001"], "start_date": "2024-01-03", "end_date": "2024-01-02"},
    ):
        raw = _raw_artifact("actions", {
            "coverage": {"security_ids": ["000001"], "start_date": DATE, "end_date": DATE},
            "rows": [{"source_row_key": "row-0", "security_id": "000001", "action_date": DATE,
                      "raw_code": "DIV", "event_id": "event-1", "conflict_key": "conflict-1",
                      "source_identity": "KRX", "resumption_date": None, "resolved": True,
                      "confirmed": True, "ratio": None, "recovery_value": None,
                      "price_adjusted": False, "portfolio_cash": False}],
        }, params)
        rows = bundle.actions.rows.copy()
        rows["source_artifact_sha256"] = raw.raw_sha256
        bad = CorporateActionComponent(rows, artifacts=(raw,), coverage=dict(bundle.actions.coverage), security_scope=bundle.actions.security_scope)
        with pytest.raises(ProductionBundleError):
            validate_production_bundle(_bundle(actions=bad))


def test_zero_event_action_coverage_can_pass_only_from_raw_envelope() -> None:
    bundle = _bundle()
    raw = _raw_artifact("actions", {
        "coverage": {"security_ids": ["000001"], "start_date": DATE, "end_date": DATE},
        "rows": [],
    }, {"security_ids": ["000001"], "start_date": DATE, "end_date": DATE})
    empty = pd.DataFrame(columns=[
        "security_id", "ticker", "action_date", "action_type", "raw_code", "resolved", "confirmed",
        "ratio", "recovery_value", "price_adjusted", "portfolio_cash", "event_id", "source_identity",
        "conflict_key", "source_artifact_sha256", "source_row_key",
    ])
    actions = CorporateActionComponent(empty, artifacts=(raw,), coverage=dict(bundle.actions.coverage), security_scope=bundle.actions.security_scope)
    assert validate_production_bundle(_bundle(actions=actions)).manifest.component_hashes["actions"]

    with pytest.raises(ProductionBundleError):
        _raw_artifact("actions", {
            "coverage": {"security_ids": ["000001"], "start_date": "2024-01-03", "end_date": "2024-01-02"},
            "rows": [],
        }, {"security_ids": ["000001"], "start_date": "2024-01-03", "end_date": "2024-01-02"})


def test_universe_requires_exact_raw_constituents_and_proven_transition() -> None:
    bundle = _bundle()
    raw = _raw_artifact("universe", {"trdDd": "20240102", "output": [{"ISU_SRT_CD": "000002", "IDX_CD": "028", "source_row_key": "row-0"}]},
                        {"bld": KRX_SNAPSHOT_BLD, "indIdx2": "028", "indIdx": "1", "trdDd": "20240102"})
    rows = bundle.universe.rows.copy()
    rows["source_artifact_sha256"] = raw.raw_sha256
    bad = PITUniverseComponent(rows, artifacts=(raw,), coverage=dict(bundle.universe.coverage), security_scope=bundle.universe.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(universe=bad))
    coverage = dict(bundle.universe.coverage)
    transition = dict(coverage["transition_exceptions"][DATE])
    transition["artifact_sha256"] = "0" * 64
    coverage["transition_exceptions"] = {DATE: transition}
    no_marker = _raw_artifact("universe", {"trdDd": "20240102", "output": [{"ISU_SRT_CD": "000001", "IDX_CD": "028", "source_row_key": "row-0"}]},
                              {"bld": KRX_SNAPSHOT_BLD, "indIdx2": "028", "indIdx": "1", "trdDd": "20240102"})
    no_marker_rows = bundle.universe.rows.copy()
    no_marker_rows["source_artifact_sha256"] = no_marker.raw_sha256
    unproven = PITUniverseComponent(no_marker_rows, artifacts=(no_marker,), coverage=coverage, security_scope=bundle.universe.security_scope)
    with pytest.raises(ProductionBundleError):
        validate_production_bundle(_bundle(universe=unproven))


def test_non_krx_universe_envelope_rejects() -> None:
    with pytest.raises(ProductionBundleError):
        _raw_artifact("universe", {"rows": [{"ISU_SRT_CD": "000001", "IDX_CD": "028", "source_row_key": "row-0"}]},
                      {"bld": KRX_SNAPSHOT_BLD, "indIdx2": "028", "indIdx": "1", "trdDd": "20240102"})


def test_separate_transition_artifact_is_not_supported() -> None:
    with pytest.raises(ProductionBundleError):
        _raw_artifact("universe_transition", {
            "transition_marker": {"as_of_date": DATE, "index_code": "028", "constituent_count": 1,
                                   "source": "KRX", "official": True},
        }, {"date": DATE})
