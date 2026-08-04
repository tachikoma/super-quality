from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("fetch_local_dart_response", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_request_params_and_manifest_building() -> None:
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/fetch_local_dart_response.py"))

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
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/fetch_local_dart_response.py"))

    url = script._build_url(
        script.OPEN_DART_ENDPOINTS["financial"],
        {"corp_code": "001", "bsns_year": "2023"},
        "secret-key",
    )
    assert "crtfc_key=secret-key" in url
    assert "corp_code=001" in url
    assert "bsns_year=2023" in url


def test_batch_mode_writes_multiple_responses_and_summary(tmp_path: Path, monkeypatch) -> None:
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/fetch_local_dart_response.py"))

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
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/fetch_local_dart_response.py"))

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