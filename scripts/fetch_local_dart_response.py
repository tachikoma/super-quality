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
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OPEN_DART_ENDPOINTS = {
    "filing": "https://opendart.fss.or.kr/api/list.json",
    "financial": "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
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
) -> dict[str, Any]:
    try:
        payload = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    payload_dict = payload if isinstance(payload, dict) else {}
    api_status = payload_dict.get("status", payload_dict.get("api_status", "000"))
    return {
        "source_url": endpoint,
        "source_type": "opendartfilinglist" if endpoint.endswith("list.json") else "opendartfinancialfacts",
        "request_params": request_params,
        "request_params_sha256": _request_params_sha256(request_params),
        "api_status": api_status,
        "pagination": {"complete": _pagination_complete(payload_dict)},
        "retrieved_at_utc": retrieved_at_utc,
        "response_sha256": _sha256_bytes(response_bytes),
        "verified": api_status in {"000", 0, "0"},
        "note": "sanitized local DART acquisition manifest",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch one OpenDART response into a local raw file plus manifest.",
    )
    parser.add_argument("--kind", choices=("filing", "financial"), required=True)
    parser.add_argument("--api-key", default="", help="OpenDART API key")
    parser.add_argument("--output-file", required=True, help="Raw response output file")
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
        "--retrieved-at-utc",
        default="",
        help="UTC retrieval timestamp (ISO-8601). Default: now in UTC",
    )

    args = parser.parse_args()

    api_key = args.api_key.strip()
    if not api_key:
        raise RuntimeError("an OpenDART API key is required for fetch operations")

    endpoint = args.source_url.strip() or OPEN_DART_ENDPOINTS[args.kind]
    request_params = _normalize_request_params(_parse_request_param(list(args.request_param)))
    retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()
    url = _build_url(endpoint, request_params, api_key)
    response_bytes = _fetch_response_bytes(url)

    output_file = Path(args.output_file)
    manifest_file = Path(args.manifest_file) if args.manifest_file else output_file.with_suffix(
        output_file.suffix + ".manifest.json"
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(response_bytes)

    manifest = _build_manifest(
        endpoint=endpoint,
        request_params=request_params,
        response_bytes=response_bytes,
        retrieved_at_utc=retrieved_at_utc,
    )
    manifest["raw_payload_path"] = str(output_file)
    manifest["raw_file_sha256"] = manifest["response_sha256"]
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"fetched {args.kind} DART response -> {output_file}")
    print(f"manifest written -> {manifest_file}")


if __name__ == "__main__":
    main()