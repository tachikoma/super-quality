#!/usr/bin/env python3
"""Build a canonical local DART filing or financial bundle with a sidecar manifest.

The input is a raw local DART response file plus its sidecar manifest. The
script validates the source with the existing importer, writes the normalized
canonical CSV, and emits a new manifest whose response SHA-256 matches the
canonical output bytes.

This utility is a packaging helper only. It does not call OpenDART.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from k200_mq.data.dart_pit import (
    load_financial_facts,
    load_filing_metadata,
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _default_source_url(kind: str) -> str:
    if kind == "filing":
        return "https://opendart.fss.or.kr/api/list.json"
    return "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"


def _output_source_type(kind: str) -> str:
    return "opendartfilinglist" if kind == "filing" else "opendartfinancialfacts"


def _load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return value


def _load_canonical_frame(kind: str, source: Path, manifest: Path) -> tuple[Any, dict[str, Any]]:
    loader = load_filing_metadata if kind == "filing" else load_financial_facts
    frame = loader(source, manifest=manifest)
    return frame, dict(frame.attrs.get("response_manifest", {}))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package a verified local DART response into a canonical CSV bundle.",
    )
    parser.add_argument(
        "--kind",
        choices=("filing", "financial"),
        required=True,
        help="DART response kind to package",
    )
    parser.add_argument(
        "--input-file",
        required=True,
        help="Raw DART response file path",
    )
    parser.add_argument(
        "--input-manifest",
        default="",
        help="Sidecar manifest for the raw DART response (default: sibling .manifest.json)",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="Canonical CSV output file (default: sibling canonical CSV)",
    )
    parser.add_argument(
        "--manifest-file",
        default="",
        help="Canonical output manifest file (default: sibling .manifest.json)",
    )
    parser.add_argument(
        "--source-url",
        default="",
        help="Override source URL recorded in the output manifest",
    )
    parser.add_argument(
        "--retrieved-at-utc",
        default="",
        help="UTC retrieval timestamp (ISO-8601). Default: now in UTC",
    )

    args = parser.parse_args()

    source = Path(args.input_file)
    manifest_path = Path(args.input_manifest) if args.input_manifest else source.with_suffix(
        source.suffix + ".manifest.json"
    )
    output_file = Path(args.output_file) if args.output_file else source.with_suffix(".canonical.csv")
    output_manifest = Path(args.manifest_file) if args.manifest_file else output_file.with_suffix(
        ".manifest.json"
    )

    frame, raw_manifest = _load_canonical_frame(args.kind, source, manifest_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_file, index=False)
    output_sha = _sha256_bytes(output_file.read_bytes())
    input_sha = _sha256_bytes(source.read_bytes())
    retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()
    source_url = args.source_url.strip() or str(
        raw_manifest.get("source_url")
        or raw_manifest.get("official_source_url")
        or _default_source_url(args.kind)
    )

    payload = {
        "source_url": source_url,
        "source_type": _output_source_type(args.kind),
        "request_params": raw_manifest.get("request_params", {}),
        "request_params_sha256": raw_manifest.get("request_params_sha256"),
        "api_status": raw_manifest.get("api_status", "000"),
        "pagination": raw_manifest.get("pagination", {"complete": True}),
        "retrieved_at_utc": retrieved_at_utc,
        "response_sha256": output_sha,
        "verified": True,
        "input_source_path": str(source),
        "input_source_sha256": input_sha,
        "input_manifest_path": str(manifest_path),
        "input_manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
        "note": "canonical local DART bundle with verified raw provenance",
    }
    output_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"bundled 1 DART {args.kind} file -> {output_file}")
    print(f"bundle manifest written -> {output_manifest}")


if __name__ == "__main__":
    main()