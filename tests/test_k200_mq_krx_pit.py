"""Mocked, local-only tests for the opt-in KRX PIT adapter."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from k200_mq.data.krx_pit import (
    KRXAuthenticationError,
    KRXClient,
    KRXCredentials,
    KRXManifestError,
    KRXResponseError,
    KRX_DATA_ENDPOINT,
    KRX_LOGIN_ENDPOINT,
    KRX_LOGIN_PAGE_ENDPOINT,
    KRX_SCHEMA_VERSION,
    download_krx_kospi200_snapshots,
    fetch_krx_kospi200_snapshot,
    load_krx_kospi200_snapshot,
    load_krx_kospi200_snapshots,
)
from k200_mq.data.pit_universe import snapshots_to_history
from k200_mq.data.pit_universe import validate_constituent_snapshots
from k200_mq.data.provenance import validate_universe_provenance


class FakeResponse:
    def __init__(self, payload: object, *, content: bytes | None = None, status_code: int = 200):
        self.status_code = status_code
        self._content = content if content is not None else json.dumps(payload).encode("utf-8")

    @property
    def content(self) -> bytes:
        return self._content

    @property
    def text(self) -> str:
        return self._content.decode("utf-8")

    def json(self) -> object:
        return json.loads(self._content.decode("utf-8"))

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")


class FakeSession:
    def __init__(self, login_codes: list[str] | None = None, payload: object | None = None):
        self.login_codes = list(login_codes or ["CD001"])
        self.payload = payload or {"output": _raw_rows()}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append(("get", url, kwargs))
        return FakeResponse({"bootstrap": True})

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append(("post", url, kwargs))
        if url == KRX_LOGIN_ENDPOINT:
            code = self.login_codes.pop(0) if self.login_codes else "CD001"
            return FakeResponse({"_error_code": code})
        return FakeResponse(self.payload)


class DistinctSnapshotSession(FakeSession):
    """Return different raw bytes for the second requested snapshot date."""

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        if url == KRX_DATA_ENDPOINT:
            params = cast(dict[str, Any], kwargs.get("data", {}))
            if params.get("trdDd") == "20240229":
                rows = _raw_rows()
                rows[0]["IDX_WGT"] = "3.21"
                return FakeResponse({"output": rows})
        return super().post(url, **kwargs)


class JsonMethodOnlySession(FakeSession):
    """Expose a useful json() object while persisting a different body."""

    class _Response(FakeResponse):
        def json(self) -> object:
            return {"output": _raw_rows()}

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        if url == KRX_DATA_ENDPOINT:
            return self._Response({"not": "a snapshot"})
        return super().post(url, **kwargs)


def _raw_rows() -> list[dict[str, str]]:
    return [
        {
            "ISU_SRT_CD": "5930",
            "ISU_ABBRV": "삼성전자",
            "TDD_CLSPRC": "70,000",
            "MKTCAP": "400,000,000",
            "LIST_SHRS": "5,969,782,550",
            "IDX_WGT": "3.20",
            "SECUGRP_NM": "전기전자",
        },
        {
            "ISU_SRT_CD": "000660",
            "ISU_ABBRV": "SK하이닉스",
            "TDD_CLSPRC": "130,000",
            "MKTCAP": "95,000,000",
            "LIST_SHRS": "728,002,365",
            "IDX_WGT": "1.10",
            "SECUGRP_NM": "전기전자",
        },
    ]


def _credentials() -> KRXCredentials:
    return KRXCredentials("test-id", "test-password")


def test_login_success_and_exact_snapshot_request_params() -> None:
    session = FakeSession()

    response = fetch_krx_kospi200_snapshot(
        "2024-01-31", _credentials(), session=session,
    )

    assert response.raw_bytes == json.dumps(session.payload).encode("utf-8")
    assert response.rows["security_code"].tolist() == ["005930", "000660"]
    assert response.rows.loc[0, "name"] == "삼성전자"
    assert response.rows.loc[0, "TDD_CLSPRC"] == "70,000"
    assert response.rows.loc[0, "MKTCAP"] == "400,000,000"
    assert session.calls[0][0:2] == ("get", KRX_LOGIN_PAGE_ENDPOINT)
    assert session.calls[1][0:2] == (
        "get",
        "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc",
    )
    assert session.calls[2][0:2] == ("post", KRX_LOGIN_ENDPOINT)
    assert session.calls[3][0:2] == ("post", KRX_DATA_ENDPOINT)
    assert session.calls[3][2]["data"] == {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
        "indIdx2": "028",
        "indIdx": "1",
        "trdDd": "20240131",
    }


def test_duplicate_login_retries_with_skip_dup() -> None:
    session = FakeSession(login_codes=["CD011", "CD001"])

    KRXClient(_credentials(), session=session).fetch_snapshot("20240131")

    login_calls = [call for call in session.calls if call[1] == KRX_LOGIN_ENDPOINT]
    assert len(login_calls) == 2
    first_data = cast(dict[str, Any], login_calls[0][2]["data"])
    retry_data = cast(dict[str, Any], login_calls[1][2]["data"])
    assert "skipDup" not in first_data
    assert retry_data["skipDup"] == "Y"


def test_login_failure_is_fail_closed_and_does_not_expose_credentials() -> None:
    session = FakeSession(login_codes=["CD999"])
    secret = "test-password"

    with pytest.raises(KRXAuthenticationError) as caught:
        KRXClient(KRXCredentials("private-user", secret), session=session).login()

    assert secret not in str(caught.value)
    assert "private-user" not in str(caught.value)
    assert "private-user" not in repr(KRXCredentials("private-user", secret))
    assert len(session.calls) == 3


def test_credentials_are_read_from_environment_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    client = KRXClient(session=session)
    monkeypatch.setenv("KRX_ID", "env-user")
    monkeypatch.setenv("KRX_PW", "env-secret")

    client.login()

    login_data = cast(dict[str, Any], session.calls[2][2]["data"])
    assert login_data["mbrId"] == "env-user"
    assert login_data["pw"] == "env-secret"


def test_empty_and_wrong_index_responses_fail_closed() -> None:
    empty = FakeSession(payload={"output": []})
    with pytest.raises(KRXResponseError, match="no rows"):
        KRXClient(_credentials(), session=empty).fetch_snapshot("20240131")

    wrong = FakeSession(payload={"indIdx2": "999", "output": _raw_rows()})
    with pytest.raises(KRXResponseError, match="not KOSPI 200"):
        KRXClient(_credentials(), session=wrong).fetch_snapshot("20240131")


@pytest.mark.parametrize(
    "payload",
    [
        {"trdDd": "20240229", "output": _raw_rows()},
        {"trdDd": "20240131", "date": "20240229", "output": _raw_rows()},
    ],
)
def test_raw_response_date_markers_must_match_and_agree(payload: dict[str, object]) -> None:
    with pytest.raises(KRXResponseError, match="snapshot date"):
        KRXClient(_credentials(), session=FakeSession(payload=payload)).fetch_snapshot("20240131")


def test_fetch_does_not_accept_a_shape_that_saved_loader_cannot_reload() -> None:
    with pytest.raises(KRXResponseError, match="output row list"):
        KRXClient(_credentials(), session=JsonMethodOnlySession()).fetch_snapshot("20240131")


def test_download_writes_exact_raw_bytes_and_sidecar_hash(tmp_path: Path) -> None:
    session = FakeSession()
    artifacts = download_krx_kospi200_snapshots(
        [date(2024, 1, 31)], tmp_path, _credentials(), session=session,
    )
    artifact = artifacts[0]
    raw = artifact.raw_path.read_bytes()
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))

    assert raw == json.dumps(session.payload).encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == manifest["response_sha256"]
    assert manifest["official_source_url"] == KRX_DATA_ENDPOINT
    assert manifest["query_params"]["indIdx2"] == "028"
    assert manifest["query_params"]["indIdx"] == "1"
    assert manifest["row_count"] == 2
    assert manifest["schema_version"] == KRX_SCHEMA_VERSION
    assert "response_sha256" not in json.loads(raw.decode("utf-8"))
    assert manifest["retrieved_at_seoul"].endswith("+09:00")
    assert manifest["retrieved_at_utc"].endswith("+00:00")


def test_saved_loader_requires_matching_manifest_and_delegates_pit_validation(
    tmp_path: Path,
) -> None:
    session = FakeSession()
    artifacts = download_krx_kospi200_snapshots(
        ["20240131"], tmp_path, _credentials(), session=session,
    )
    artifact = artifacts[0]

    loaded = load_krx_kospi200_snapshot(artifact.raw_path, target_size=2)
    assert loaded["security_code"].tolist() == ["005930", "000660"]
    assert loaded.attrs["acquisition_manifest_verified"] is True
    assert loaded.attrs["pit_candidate"] is True

    artifact.manifest_path.unlink()
    with pytest.raises(KRXManifestError, match="missing"):
        load_krx_kospi200_snapshot(artifact.raw_path, target_size=2)


def test_saved_loader_rejects_mismatched_manifest(tmp_path: Path) -> None:
    session = FakeSession()
    artifact = download_krx_kospi200_snapshots(
        ["20240131"], tmp_path, _credentials(), session=session,
    )[0]
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    manifest["response_sha256"] = "0" * 64
    artifact.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(KRXManifestError, match="SHA-256"):
        load_krx_kospi200_snapshot(artifact.raw_path, target_size=2)


def test_saved_loader_binds_date_when_raw_response_has_no_date_marker(tmp_path: Path) -> None:
    artifact = download_krx_kospi200_snapshots(
        ["20240131"], tmp_path, _credentials(), session=FakeSession(),
    )[0]
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    manifest["date_params"]["as_of"] = "2024-02-29"
    manifest["date_params"]["trdDd"] = "20240229"
    manifest["query_params"]["trdDd"] = "20240229"
    artifact.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(KRXManifestError, match="date"):
        load_krx_kospi200_snapshot(artifact.raw_path, target_size=2)


def test_saved_loader_rejects_conflicting_retrieval_timestamp_alias(tmp_path: Path) -> None:
    artifact = download_krx_kospi200_snapshots(
        ["20240131"], tmp_path, _credentials(), session=FakeSession(),
    )[0]
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    manifest["retrieval_timestamp"] = "2026-01-01T00:00:00+00:00"
    artifact.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(KRXManifestError, match="alias"):
        load_krx_kospi200_snapshot(artifact.raw_path, target_size=2)


def test_identical_raw_bytes_are_allowed_only_with_separate_date_bound_manifests(
    tmp_path: Path,
) -> None:
    download_krx_kospi200_snapshots(
        ["20240131", "20240229"], tmp_path, _credentials(), session=FakeSession(),
    )
    snapshots = load_krx_kospi200_snapshots(
        tmp_path, ["20240131", "20240229"], target_size=2,
    )
    assert len(set(snapshots["source_file_sha256"])) == 1
    assert validate_constituent_snapshots(
        snapshots,
        requested_rebalance_dates=["20240131", "20240229"],
        target_size=2,
    ).pit_valid is True
    history = snapshots_to_history(
        snapshots,
        requested_rebalance_dates=["20240131", "20240229"],
        target_size=2,
    )
    assert validate_universe_provenance(history)["pit_valid"] is True

    second_manifest_path = tmp_path / "krx_kospi200_20240229.json.manifest.json"
    second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
    first_manifest_path = tmp_path / "krx_kospi200_20240131.json.manifest.json"
    first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
    second_manifest["date_params"] = dict(first_manifest["date_params"])
    second_manifest["query_params"] = dict(first_manifest["query_params"])
    second_manifest_path.write_text(json.dumps(second_manifest), encoding="utf-8")
    with pytest.raises(KRXManifestError, match="date"):
        load_krx_kospi200_snapshots(tmp_path, ["20240131", "20240229"], target_size=2)


def test_saved_multi_loader_preserves_verified_attrs_for_history(tmp_path: Path) -> None:
    session = DistinctSnapshotSession()
    download_krx_kospi200_snapshots(
        ["20240131", "20240229"], tmp_path, _credentials(), session=session,
    )

    snapshots = load_krx_kospi200_snapshots(
        tmp_path, ["20240131", "20240229"], target_size=2,
    )

    assert snapshots.attrs["provenance"] == "pit"
    assert isinstance(snapshots.attrs["acquisition_manifest"], dict)
    assert snapshots.attrs["acquisition_manifest_verified"] is True
    assert snapshots.attrs["pit_candidate"] is True
    assert snapshots.attrs["_verified_acquisition"] is not None
    source_hashes = set(snapshots["source_file_sha256"])
    assert len(source_hashes) == 2
    manifests_by_date = snapshots.attrs["acquisition_manifests_by_as_of"]
    assert set(manifests_by_date) == {"2024-01-31", "2024-02-29"}
    assert {
        manifest["raw_file_sha256"] for manifest in manifests_by_date.values()
    } == source_hashes

    history = snapshots_to_history(
        snapshots,
        requested_rebalance_dates=["2024-01-31", "2024-02-29"],
        target_size=2,
    )

    assert history.attrs["acquisition_manifest_verified"] is True
    assert history.attrs["_verified_acquisition"] is not None
    assert history.attrs["pit_valid"] is True
    metadata_by_date = history.attrs["provenance_metadata_by_as_of"]
    assert {
        metadata["source_file_sha256"] for metadata in metadata_by_date.values()
    } == source_hashes
    assert {
        metadata["acquisition_manifest"]["raw_file_sha256"]
        for metadata in metadata_by_date.values()
    } == source_hashes
    assert validate_universe_provenance(history)["pit_valid"] is True


def test_multi_snapshot_token_date_coverage_is_not_a_hash_set(tmp_path: Path) -> None:
    session = DistinctSnapshotSession()
    download_krx_kospi200_snapshots(
        ["20240131", "20240229"], tmp_path, _credentials(), session=session,
    )
    snapshots = load_krx_kospi200_snapshots(
        tmp_path, ["20240131", "20240229"], target_size=2,
    )
    tokens = snapshots.attrs["_verified_acquisition"]
    snapshots.attrs["_verified_acquisition"] = (tokens[0], tokens[0])

    report = validate_constituent_snapshots(
        snapshots,
        requested_rebalance_dates=["20240131", "20240229"],
        target_size=2,
    )
    assert report.pit_valid is False
    assert any("each date" in error for error in report.errors)


def test_snapshots_to_history_fails_when_final_aggregate_provenance_is_false(
    tmp_path: Path,
) -> None:
    session = DistinctSnapshotSession()
    download_krx_kospi200_snapshots(
        ["20240131", "20240229"], tmp_path, _credentials(), session=session,
    )
    snapshots = load_krx_kospi200_snapshots(
        tmp_path, ["20240131", "20240229"], target_size=2,
    )
    snapshots.attrs["acquisition_manifest"] = {
        "manifest_type": "multiple_verified_raw_snapshots",
        "manifests_by_as_of": {
            "2024-01-31": snapshots.attrs["acquisition_manifests_by_as_of"]["2024-01-31"],
        },
    }

    with pytest.raises(ValueError, match="final universe provenance"):
        snapshots_to_history(
            snapshots,
            requested_rebalance_dates=["20240131", "20240229"],
            target_size=2,
        )
