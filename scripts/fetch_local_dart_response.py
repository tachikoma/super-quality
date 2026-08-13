#!/usr/bin/env python3
"""Fetch one OpenDART response into a local file plus sanitized manifest.

This helper is for data acquisition only. It records the raw response bytes,
omits secrets such as API keys from the manifest, and writes request metadata
that can later be verified by the local DART importer or packaging helper.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile


OPEN_DART_ENDPOINTS = {
    "filing": "https://opendart.fss.or.kr/api/list.json",
    "financial": "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
    "financial_xbrl": "https://opendart.fss.or.kr/api/fnlttXbrl.xml",
}


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalize_request_params(params: dict[str, str]) -> dict[str, str]:
    secret_keys = {"api_key", "crtfc_key", "apikey", "token", "key"}
    return {
        key: value
        for key, value in sorted(params.items(), key=lambda item: item[0])
        if key.casefold() not in secret_keys and value != ""
    }


def _request_params_sha256(params: dict[str, str]) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_xbrl_request(*, kind: str, endpoint: str) -> bool:
    return kind == "financial_xbrl" or endpoint == OPEN_DART_ENDPOINTS["financial_xbrl"]


def _source_type_for_request(*, kind: str, endpoint: str) -> str:
    if _is_xbrl_request(kind=kind, endpoint=endpoint):
        return "opendartfinancialxbrl"
    if kind == "filing":
        return "opendartfilinglist"
    if kind == "financial":
        return "opendartfinancialfacts"
    return "opendartfilinglist" if endpoint.endswith("list.json") else "opendartfinancialfacts"


def _is_success_status(value: Any) -> bool:
    return value in {"000", "0", 0}


def _classify_xbrl_response(response_bytes: bytes) -> tuple[str, str, str]:
    """Return response format, API status, and response status for an XBRL response."""
    try:
        payload = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        api_status = payload.get("status", payload.get("api_status", "unknown"))
        response_status = "success" if _is_success_status(api_status) else "error"
        return "json", str(api_status), response_status

    try:
        if zipfile.is_zipfile(BytesIO(response_bytes)):
            return "zip", "000", "success"
    except (OSError, zipfile.BadZipFile):
        pass

    try:
        root = ET.fromstring(response_bytes)
    except (ET.ParseError, UnicodeDecodeError):
        return "unknown", "unknown", "unknown"
    status_node = root.find(".//status")
    api_status = status_node.text.strip() if status_node is not None and status_node.text else "000"
    response_status = "success" if _is_success_status(api_status) else "error"
    return "xml", api_status, response_status


def _parse_request_param(values: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"request param must be KEY=VALUE: {value!r}")
        key, raw = value.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            raise ValueError(f"request param key is empty: {value!r}")
        params[key] = raw
    return params


def _build_url(endpoint: str, params: dict[str, str], api_key: str) -> str:
    request_params = dict(params)
    request_params["crtfc_key"] = api_key
    return f"{endpoint}?{urlencode(request_params)}"


def _fetch_response_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "k200mq-local-dart-fetch/1.0"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def _pagination_complete(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    page_no = payload.get("page_no")
    total_page = payload.get("total_page")
    if page_no is None or total_page is None:
        pagination = payload.get("pagination")
        if isinstance(pagination, dict):
            complete = pagination.get("complete", pagination.get("is_complete", pagination.get("completed")))
            if isinstance(complete, bool):
                return complete
        return True
    try:
        return int(page_no) >= int(total_page)
    except (TypeError, ValueError):
        return True


def _build_manifest(
    *,
    endpoint: str,
    request_params: dict[str, str],
    response_bytes: bytes,
    retrieved_at_utc: str,
    kind: str = "",
) -> dict[str, Any]:
    xbrl_response = _is_xbrl_request(kind=kind, endpoint=endpoint)
    sanitized_params = _normalize_request_params(request_params)
    api_status: Any = "000"
    response_status = "success"
    if xbrl_response:
        response_format, api_status, response_status = _classify_xbrl_response(response_bytes)
        payload: Any = {}
        if response_format == "json":
            try:
                payload = json.loads(response_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
    else:
        try:
            payload = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        response_format = "json"
    payload_dict = payload if isinstance(payload, dict) else {}
    if not xbrl_response:
        api_status = payload_dict.get("status", payload_dict.get("api_status", "000"))
    manifest = {
        "source_url": endpoint,
        "source_type": _source_type_for_request(kind=kind, endpoint=endpoint),
        "request_params": sanitized_params,
        "request_params_sha256": _request_params_sha256(sanitized_params),
        "api_status": api_status,
        "pagination": {"complete": _pagination_complete(payload_dict)},
        "retrieved_at_utc": retrieved_at_utc,
        "response_sha256": _sha256_bytes(response_bytes),
        "verified": _is_success_status(api_status),
        "note": "sanitized local DART acquisition manifest",
    }
    if xbrl_response:
        manifest["response_format"] = response_format
        manifest["response_status"] = response_status
    return manifest


def _fetch_one(
    *,
    kind: str,
    api_key: str,
    output_file: Path,
    manifest_file: Path,
    request_params: dict[str, str],
    source_url: str,
    retrieved_at_utc: str,
) -> dict[str, Any]:
    sanitized_params = _normalize_request_params(request_params)
    if kind == "financial_xbrl":
        missing = [name for name in ("rcept_no", "reprt_code") if not sanitized_params.get(name)]
        if missing:
            raise RuntimeError(
                "financial_xbrl requires request params: " + ", ".join(missing)
            )
    url = _build_url(source_url, sanitized_params, api_key)
    response_bytes = _fetch_response_bytes(url)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(response_bytes)
    manifest = _build_manifest(
        endpoint=source_url,
        request_params=sanitized_params,
        response_bytes=response_bytes,
        retrieved_at_utc=retrieved_at_utc,
        kind=kind,
    )
    manifest["raw_payload_path"] = str(output_file)
    manifest["raw_file_sha256"] = manifest["response_sha256"]
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "kind": kind,
        "output_file": str(output_file),
        "manifest_file": str(manifest_file),
        "response_sha256": manifest["response_sha256"],
        "request_params_sha256": manifest["request_params_sha256"],
    }


def _load_batch_spec(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("batch file must be a JSON array of request specs")
    specs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("batch file entries must be JSON objects")
        specs.append(dict(item))
    return specs


def _is_verified_spec(
    *,
    spec: dict[str, Any],
    output_dir: Path,
) -> bool:
    try:
        kind = spec.get("kind")
        if not isinstance(kind, str) or kind not in OPEN_DART_ENDPOINTS:
            return False
        output_name = spec.get("output_name")
        if not isinstance(output_name, str) or not output_name:
            return False
        manifest_name = spec.get("manifest_name")
        if manifest_name is None or manifest_name == "":
            manifest_name = f"{Path(output_name).stem}.manifest.json"
        if not isinstance(manifest_name, str):
            return False
        raw_file = output_dir / output_name
        manifest_file = output_dir / manifest_name
        if not raw_file.is_file() or not manifest_file.is_file():
            return False
        raw_bytes = raw_file.read_bytes()
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return False
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False

    expected_source_url = OPEN_DART_ENDPOINTS[kind]
    if "source_url" in spec:
        explicit_source_url = spec.get("source_url")
        if not isinstance(explicit_source_url, str) or not explicit_source_url.strip():
            return False
        expected_source_url = explicit_source_url.strip()
    if manifest.get("source_url") != expected_source_url:
        return False
    if manifest.get("source_type") != _source_type_for_request(
        kind=kind,
        endpoint=expected_source_url,
    ):
        return False
    if manifest.get("api_status") not in {"000", "0", 0}:
        return False
    if manifest.get("verified") is not True:
        return False
    if kind == "financial_xbrl" and manifest.get("response_status") != "success":
        return False

    if kind == "financial_xbrl":
        _, raw_api_status, raw_response_status = _classify_xbrl_response(raw_bytes)
        if not _is_success_status(raw_api_status) or raw_response_status != "success":
            return False
    else:
        try:
            raw_payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(raw_payload, dict):
            return False
        raw_api_status = raw_payload.get("status", raw_payload.get("api_status"))
        if not _is_success_status(raw_api_status):
            return False

    raw_sha256 = _sha256_bytes(raw_bytes)
    if manifest.get("raw_file_sha256") != raw_sha256:
        return False
    if manifest.get("response_sha256") != raw_sha256:
        return False

    request_params = spec.get("request_params")
    if not isinstance(request_params, list) or not all(
        isinstance(value, str) for value in request_params
    ):
        return False
    try:
        expected_params = _normalize_request_params(_parse_request_param(request_params))
    except (TypeError, ValueError):
        return False
    manifest_params = manifest.get("request_params")
    if not isinstance(manifest_params, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in manifest_params.items()
    ):
        return False
    if manifest_params != expected_params:
        return False
    if kind == "financial_xbrl" and not all(
        expected_params.get(name) for name in ("rcept_no", "reprt_code")
    ):
        return False
    return manifest.get("request_params_sha256") == _request_params_sha256(expected_params)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch one OpenDART response into a local raw file plus manifest.",
    )
    parser.add_argument("--kind", choices=("filing", "financial", "financial_xbrl"), default="")
    parser.add_argument("--api-key", default="", help="OpenDART API key")
    parser.add_argument("--output-file", default="", help="Raw response output file")
    parser.add_argument(
        "--manifest-file",
        default="",
        help="Manifest output file (default: sibling .manifest.json)",
    )
    parser.add_argument(
        "--request-param",
        action="append",
        default=[],
        help="Request parameter in KEY=VALUE form; may be repeated",
    )
    parser.add_argument(
        "--source-url",
        default="",
        help="Override the OpenDART endpoint URL",
    )
    parser.add_argument(
        "--batch-file",
        default="",
        help="JSON file containing an array of request specs to fetch",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Batch mode output directory (required with --batch-file)",
    )
    parser.add_argument(
        "--output-prefix",
        default="dart",
        help="Batch mode file prefix",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="Batch mode 1-based start index (default: 1)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=0,
        help="Batch mode max number of requests to process (0 means all)",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Batch mode: continue processing remaining specs when a request fails",
    )
    parser.add_argument(
        "--skip-verified",
        action="store_true",
        help="Batch mode: skip specs whose output file already exists and is verified (api_status 000/0)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Batch mode: sleep this many seconds between requests to respect rate limits",
    )
    parser.add_argument(
        "--retrieved-at-utc",
        default="",
        help="UTC retrieval timestamp (ISO-8601). Default: now in UTC",
    )

    args = parser.parse_args()

    api_key = args.api_key.strip() or os.getenv("DART_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("an OpenDART API key is required for fetch operations (--api-key or DART_API_KEY)")

    if args.batch_file:
        batch_path = Path(args.batch_file)
        output_dir = Path(args.output_dir) if args.output_dir else None
        if output_dir is None:
            raise RuntimeError("--output-dir is required when --batch-file is used")
        retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()
        specs = _load_batch_spec(batch_path)
        if args.start_index < 1:
            raise RuntimeError("--start-index must be >= 1")
        if args.max_requests < 0:
            raise RuntimeError("--max-requests must be >= 0")
        start_at = args.start_index - 1
        selected_specs = specs[start_at:]
        if args.max_requests > 0:
            selected_specs = selected_specs[:args.max_requests]
        outputs: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        skipped: list[int] = []
        for index, spec in enumerate(selected_specs, start=args.start_index):
            if not isinstance(spec.get("kind"), str) or spec["kind"] not in OPEN_DART_ENDPOINTS:
                raise RuntimeError(f"invalid batch request kind at item {index}")
            item_kind = str(spec["kind"])
            if args.skip_verified and _is_verified_spec(spec=spec, output_dir=output_dir):
                skipped.append(index)
                continue
            item_params = _normalize_request_params(
                _parse_request_param(list(spec.get("request_params", [])))
            )
            item_source_url = str(spec.get("source_url") or OPEN_DART_ENDPOINTS[item_kind])
            item_output_name = str(spec.get("output_name") or f"{args.output_prefix}_{index}.json")
            item_manifest_name = str(
                spec.get("manifest_name") or f"{Path(item_output_name).stem}.manifest.json"
            )
            try:
                outputs.append(
                    _fetch_one(
                        kind=item_kind,
                        api_key=api_key,
                        output_file=output_dir / item_output_name,
                        manifest_file=output_dir / item_manifest_name,
                        request_params=item_params,
                        source_url=item_source_url,
                        retrieved_at_utc=retrieved_at_utc,
                    )
                )
            except Exception as exc:
                failures.append({
                    "index": index,
                    "kind": item_kind,
                    "error": str(exc),
                    "spec": spec,
                })
                if not args.continue_on_error:
                    raise
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)
        batch_summary = output_dir / "batch_summary.json"
        batch_summary.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
        if failures:
            failure_report = output_dir / "batch_failures.json"
            failure_report.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"batch failures: {len(failures)} -> {failure_report}")
        if skipped:
            print(f"batch skipped verified: {len(skipped)}")
        print(f"fetched {len(outputs)} DART responses -> {output_dir}")
        print(f"batch summary written -> {batch_summary}")
        return

    if not args.kind:
        raise RuntimeError("--kind is required unless --batch-file is used")
    if not args.output_file:
        raise RuntimeError("--output-file is required unless --batch-file is used")

    endpoint = args.source_url.strip() or OPEN_DART_ENDPOINTS[args.kind]
    request_params = _normalize_request_params(_parse_request_param(list(args.request_param)))
    retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()
    output_file = Path(args.output_file)
    manifest_file = Path(args.manifest_file) if args.manifest_file else output_file.with_suffix(
        output_file.suffix + ".manifest.json"
    )

    _fetch_one(
        kind=args.kind,
        api_key=api_key,
        output_file=output_file,
        manifest_file=manifest_file,
        request_params=request_params,
        source_url=endpoint,
        retrieved_at_utc=retrieved_at_utc,
    )

    print(f"fetched {args.kind} DART response -> {output_file}")
    print(f"manifest written -> {manifest_file}")


if __name__ == "__main__":
    main()
