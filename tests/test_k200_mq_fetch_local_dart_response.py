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