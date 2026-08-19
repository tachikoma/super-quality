from __future__ import annotations

import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import zipfile

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("fetch_local_dart_response", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_request_params_and_manifest_building() -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    params = script._normalize_request_params({
        "crtfc_key": "secret",
        "corp_code": "001",
        "bgn_de": "20240101",
        "empty": "",
    })
    assert params == {"bgn_de": "20240101", "corp_code": "001"}
    assert script._request_params_sha256(params) == hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    raw = json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8")
    manifest = script._build_manifest(
        endpoint=script.OPEN_DART_ENDPOINTS["filing"],
        request_params=params,
        response_bytes=raw,
        retrieved_at_utc="2024-01-03T00:00:00+00:00",
    )
    assert manifest["verified"] is True
    assert manifest["pagination"]["complete"] is True
    assert manifest["response_sha256"] == hashlib.sha256(raw).hexdigest()


def test_build_url_includes_api_key_and_preserves_request_params() -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    url = script._build_url(
        script.OPEN_DART_ENDPOINTS["financial"],
        {"corp_code": "001", "bsns_year": "2023"},
        "secret-key",
    )
    assert "crtfc_key=secret-key" in url
    assert "corp_code=001" in url
    assert "bsns_year=2023" in url


def test_financial_error_status_preserves_financial_facts_source_type() -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    manifest = script._build_manifest(
        endpoint=script.OPEN_DART_ENDPOINTS["financial"],
        request_params={"corp_code": "00126186", "bsns_year": "2014"},
        response_bytes=json.dumps({"status": "013", "message": "not available"}).encode("utf-8"),
        retrieved_at_utc="2024-04-01T00:00:00+00:00",
        kind="financial",
    )

    assert manifest["source_type"] == "opendartfinancialfacts"
    assert manifest["api_status"] == "013"
    assert manifest["verified"] is False


def _write_manifest_for_spec(
    script,
    output_dir: Path,
    *,
    kind: str,
    request_params: list[str],
    raw: bytes,
) -> dict[str, object]:
    output_file = output_dir / f"{kind}.raw"
    manifest_file = output_dir / f"{kind}.manifest.json"
    output_file.write_bytes(raw)
    params = script._normalize_request_params(script._parse_request_param(request_params))
    manifest = script._build_manifest(
        endpoint=script.OPEN_DART_ENDPOINTS[kind],
        request_params=params,
        response_bytes=raw,
        retrieved_at_utc="2024-04-01T00:00:00+00:00",
        kind=kind,
    )
    manifest["raw_payload_path"] = str(output_file)
    manifest["raw_file_sha256"] = manifest["response_sha256"]
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "kind": kind,
        "request_params": request_params,
        "output_name": output_file.name,
        "manifest_name": manifest_file.name,
    }


def test_is_verified_spec_rejects_mutated_raw_hash_and_request_params(tmp_path: Path) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")
    raw = json.dumps({"status": "000"}).encode("utf-8")
    spec = _write_manifest_for_spec(
        script,
        tmp_path,
        kind="financial",
        request_params=["corp_code=00126186", "bsns_year=2023"],
        raw=raw,
    )
    assert script._is_verified_spec(spec=spec, output_dir=tmp_path) is True

    manifest_path = tmp_path / "financial.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["raw_file_sha256"] = "bad-hash"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert script._is_verified_spec(spec=spec, output_dir=tmp_path) is False

    manifest["raw_file_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["request_params"] = {"corp_code": "00999999", "bsns_year": "2023"}
    manifest["request_params_sha256"] = script._request_params_sha256(manifest["request_params"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert script._is_verified_spec(spec=spec, output_dir=tmp_path) is False


@pytest.mark.parametrize("mutation", ["source_url", "source_type"])
def test_is_verified_spec_rejects_mismatched_source_metadata(tmp_path: Path, mutation: str) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")
    raw = json.dumps({"status": "000"}).encode("utf-8")
    spec = _write_manifest_for_spec(
        script,
        tmp_path,
        kind="filing",
        request_params=["corp_code=00126186", "bgn_de=20240101"],
        raw=raw,
    )
    manifest_path = tmp_path / "filing.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[mutation] = "https://example.invalid/changed" if mutation == "source_url" else "opendartfinancialfacts"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert script._is_verified_spec(spec=spec, output_dir=tmp_path) is False


def test_is_verified_spec_accepts_valid_xbrl_success(tmp_path: Path) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")
    raw_buffer = BytesIO()
    with zipfile.ZipFile(raw_buffer, "w") as archive:
        archive.writestr("statement.xml", "<statement />")
    raw = raw_buffer.getvalue()
    spec = _write_manifest_for_spec(
        script,
        tmp_path,
        kind="financial_xbrl",
        request_params=["rcept_no=20240329000123", "reprt_code=11011"],
        raw=raw,
    )

    assert script._is_verified_spec(spec=spec, output_dir=tmp_path) is True


def test_financial_xbrl_endpoint_and_manifest_metadata(tmp_path: Path, monkeypatch) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    assert script.OPEN_DART_ENDPOINTS["financial_xbrl"] == (
        "https://opendart.fss.or.kr/api/fnlttXbrl.xml"
    )
    raw_buffer = BytesIO()
    with zipfile.ZipFile(raw_buffer, "w") as archive:
        archive.writestr("statement.xml", "<statement />")
    raw = raw_buffer.getvalue()
    requested_urls: list[str] = []
    monkeypatch.setattr(
        script,
        "_fetch_response_bytes",
        lambda url: requested_urls.append(url) or raw,
    )

    output = tmp_path / "xbrl.zip"
    manifest_file = tmp_path / "xbrl.manifest.json"
    result = script._fetch_one(
        kind="financial_xbrl",
        api_key="secret-key",
        output_file=output,
        manifest_file=manifest_file,
        request_params={
            "rcept_no": "20240329000123",
            "reprt_code": "11011",
            "crtfc_key": "secret-key",
        },
        source_url=script.OPEN_DART_ENDPOINTS["financial_xbrl"],
        retrieved_at_utc="2024-04-01T00:00:00+00:00",
    )

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert requested_urls == [
        "https://opendart.fss.or.kr/api/fnlttXbrl.xml?rcept_no=20240329000123&reprt_code=11011&crtfc_key=secret-key"
    ]
    assert manifest["source_type"] == "opendartfinancialxbrl"
    assert manifest["request_params"] == {
        "reprt_code": "11011",
        "rcept_no": "20240329000123",
    }
    assert "secret-key" not in manifest_file.read_text(encoding="utf-8")
    assert manifest["raw_payload_path"] == str(output)
    assert manifest["raw_file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["retrieved_at_utc"] == "2024-04-01T00:00:00+00:00"
    assert manifest["response_format"] == "zip"
    assert manifest["response_status"] == "success"
    assert manifest["api_status"] == "000"
    assert manifest["verified"] is True
    assert result["kind"] == "financial_xbrl"


@pytest.mark.parametrize(
    ("raw", "response_format"),
    [
        (b"<?xml version='1.0'?><result><status>000</status></result>", "xml"),
        (b"zip-payload", "zip"),
    ],
)
def test_financial_xbrl_success_formats_are_verified(raw: bytes, response_format: str) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")
    if response_format == "zip":
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("statement.xml", "<statement />")
        raw = buffer.getvalue()

    manifest = script._build_manifest(
        endpoint=script.OPEN_DART_ENDPOINTS["financial_xbrl"],
        request_params={"rcept_no": "20240329000123", "reprt_code": "11011"},
        response_bytes=raw,
        retrieved_at_utc="2024-04-01T00:00:00+00:00",
        kind="financial_xbrl",
    )

    assert manifest["response_format"] == response_format
    assert manifest["response_status"] == "success"
    assert manifest["api_status"] == "000"
    assert manifest["verified"] is True


def test_financial_xbrl_json_error_is_not_verified() -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")
    manifest = script._build_manifest(
        endpoint=script.OPEN_DART_ENDPOINTS["financial_xbrl"],
        request_params={"rcept_no": "20240329000123", "reprt_code": "11011"},
        response_bytes=json.dumps({"status": "020", "message": "quota"}).encode("utf-8"),
        retrieved_at_utc="2024-04-01T00:00:00+00:00",
        kind="financial_xbrl",
    )

    assert manifest["response_format"] == "json"
    assert manifest["response_status"] == "error"
    assert manifest["api_status"] == "020"
    assert manifest["verified"] is False


def test_batch_mode_writes_multiple_responses_and_summary(tmp_path: Path, monkeypatch) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    payloads = {
        "https://opendart.fss.or.kr/api/list.json?bgn_de=20240101&corp_code=001&crtfc_key=secret":
            json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8"),
        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?bsns_year=2023&corp_code=001&crtfc_key=secret":
            json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8"),
    }

    monkeypatch.setattr(script, "_fetch_response_bytes", lambda url: payloads[url])

    batch_spec = tmp_path / "batch.json"
    batch_spec.write_text(json.dumps([
        {
            "kind": "filing",
            "request_params": ["corp_code=001", "bgn_de=20240101"],
            "output_name": "filing.json",
            "manifest_name": "filing.manifest.json",
        },
        {
            "kind": "financial",
            "request_params": ["corp_code=001", "bsns_year=2023"],
            "output_name": "facts.json",
            "manifest_name": "facts.manifest.json",
        },
    ], ensure_ascii=False), encoding="utf-8")

    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "fetch_local_dart_response.py",
            "--api-key", "secret",
            "--batch-file", str(batch_spec),
            "--output-dir", str(tmp_path / "out"),
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    output_dir = tmp_path / "out"
    assert (output_dir / "filing.json").is_file()
    assert (output_dir / "facts.json").is_file()
    assert (output_dir / "filing.manifest.json").is_file()
    assert (output_dir / "facts.manifest.json").is_file()
    summary = json.loads((output_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert len(summary) == 2
    assert summary[0]["kind"] == "filing"
    assert summary[1]["kind"] == "financial"


def test_batch_mode_supports_chunking_and_continue_on_error(tmp_path: Path, monkeypatch) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    payloads = {
        "https://opendart.fss.or.kr/api/list.json?bgn_de=20240101&corp_code=001&crtfc_key=secret":
            json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8"),
        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?bsns_year=2023&corp_code=001&crtfc_key=secret":
            json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8"),
    }

    def _fake_fetch(url: str) -> bytes:
        if "bsns_year=2024" in url:
            raise RuntimeError("synthetic network failure")
        return payloads[url]

    monkeypatch.setattr(script, "_fetch_response_bytes", _fake_fetch)

    batch_spec = tmp_path / "batch.json"
    batch_spec.write_text(json.dumps([
        {
            "kind": "filing",
            "request_params": ["corp_code=001", "bgn_de=20240101"],
            "output_name": "filing.json",
            "manifest_name": "filing.manifest.json",
        },
        {
            "kind": "financial",
            "request_params": ["corp_code=001", "bsns_year=2024"],
            "output_name": "facts_fail.json",
            "manifest_name": "facts_fail.manifest.json",
        },
        {
            "kind": "financial",
            "request_params": ["corp_code=001", "bsns_year=2023"],
            "output_name": "facts_ok.json",
            "manifest_name": "facts_ok.manifest.json",
        },
    ], ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "out"
    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "fetch_local_dart_response.py",
            "--api-key", "secret",
            "--batch-file", str(batch_spec),
            "--output-dir", str(out_dir),
            "--start-index", "2",
            "--max-requests", "2",
            "--continue-on-error",
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    summary = json.loads((out_dir / "batch_summary.json").read_text(encoding="utf-8"))
    failures = json.loads((out_dir / "batch_failures.json").read_text(encoding="utf-8"))

    assert len(summary) == 1
    assert summary[0]["kind"] == "financial"
    assert len(failures) == 1
    assert failures[0]["index"] == 2
    assert "synthetic network failure" in failures[0]["error"]


def test_batch_mode_skip_verified_preserves_good_files(tmp_path: Path, monkeypatch) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    good_payload = json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    _write_manifest_for_spec(
        script,
        out_dir,
        kind="filing",
        request_params=["corp_code=001", "bgn_de=20240101"],
        raw=good_payload,
    )
    (out_dir / "filing.raw").rename(out_dir / "filing_good.json")
    (out_dir / "filing.manifest.json").rename(out_dir / "filing_good.manifest.json")

    calls: list[str] = []

    def _fake_fetch(url: str) -> bytes:
        calls.append(url)
        if "corp_code=002" in url:
            return good_payload
        return json.dumps({"status": "020", "message": "quota"}, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(script, "_fetch_response_bytes", _fake_fetch)

    batch_spec = tmp_path / "batch.json"
    batch_spec.write_text(json.dumps([
        {
            "kind": "filing",
            "request_params": ["corp_code=001", "bgn_de=20240101"],
            "output_name": "filing_good.json",
            "manifest_name": "filing_good.manifest.json",
        },
        {
            "kind": "filing",
            "request_params": ["corp_code=002", "bgn_de=20240101"],
            "output_name": "filing_new.json",
            "manifest_name": "filing_new.manifest.json",
        },
    ], ensure_ascii=False), encoding="utf-8")

    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "fetch_local_dart_response.py",
            "--api-key", "secret",
            "--batch-file", str(batch_spec),
            "--output-dir", str(out_dir),
            "--skip-verified",
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    assert len(calls) == 1, "verified spec must not be re-fetched"
    assert "corp_code=002" in calls[0]
    assert (out_dir / "filing_good.json").is_file()
    assert (out_dir / "filing_new.json").is_file()
    summary = json.loads((out_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert len(summary) == 1
    assert summary[0]["output_file"].endswith("filing_new.json")


def test_batch_mode_delay_seconds_pauses_between_requests(tmp_path: Path, monkeypatch) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "fetch_local_dart_response.py")

    payload = json.dumps({"status": "000", "page_no": 1, "total_page": 1}, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(script, "_fetch_response_bytes", lambda url: payload)
    sleeps: list[float] = []
    monkeypatch.setattr(script, "time", type("Time", (), {"sleep": lambda sec: sleeps.append(sec)}))

    batch_spec = tmp_path / "batch.json"
    batch_spec.write_text(json.dumps([
        {
            "kind": "filing",
            "request_params": ["corp_code=001", "bgn_de=20240101"],
            "output_name": f"filing_{i}.json",
            "manifest_name": f"filing_{i}.manifest.json",
        }
        for i in range(3)
    ], ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "out"
    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "fetch_local_dart_response.py",
            "--api-key", "secret",
            "--batch-file", str(batch_spec),
            "--output-dir", str(out_dir),
            "--delay-seconds", "0.25",
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    assert sleeps == [0.25, 0.25, 0.25]
    summary = json.loads((out_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert len(summary) == 3
