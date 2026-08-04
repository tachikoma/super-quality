#!/usr/bin/env python3
"""Build merged local DART filing/facts artifacts from batch fetch outputs.

This script reads batch-fetched raw responses and sidecar manifests, validates
each file through the existing local DART loaders, and writes merged canonical
CSV artifacts with new sidecar manifests.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from k200_mq.data.dart_pit import (
    load_financial_facts,
    load_filing_metadata,
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request_params_hash(params: dict[str, str]) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collect_raw_files(directory: Path, prefix: str) -> list[Path]:
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.name.startswith(prefix) and path.suffix == ".json")
    return [path for path in files if not path.name.endswith(".manifest.json")]


def _load_frame_bundle(kind: str, files: list[Path]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    inputs: list[dict[str, Any]] = []
    loader = load_filing_metadata if kind == "filing" else load_financial_facts
    for path in files:
        manifest = path.with_suffix(path.suffix + ".manifest.json")
        if not manifest.is_file():
            raise RuntimeError(f"missing sidecar manifest for {path.name}")
        frame = loader(path, manifest=manifest)
        frames.append(frame)
        inputs.append({
            "path": str(path),
            "manifest": str(manifest),
            "source_sha256": _sha256_bytes(path.read_bytes()),
            "manifest_sha256": _sha256_bytes(manifest.read_bytes()),
        })
    if not frames:
        raise RuntimeError(f"no valid {kind} raw files were found")
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates().reset_index(drop=True)
    return merged, inputs


def _write_output_manifest(
    *,
    kind: str,
    output_file: Path,
    manifest_file: Path,
    retrieved_at_utc: str,
    source_dir: Path,
    inputs: list[dict[str, Any]],
) -> None:
    endpoint = (
        "https://opendart.fss.or.kr/api/list.json"
        if kind == "filing"
        else "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    )
    source_type = "opendartfilinglist" if kind == "filing" else "opendartfinancialfacts"
    request_params = {
        "batch_source_dir": str(source_dir),
        "kind": kind,
        "input_count": str(len(inputs)),
    }
    payload = {
        "source_url": endpoint,
        "source_type": source_type,
        "request_params": request_params,
        "request_params_sha256": _request_params_hash(request_params),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": retrieved_at_utc,
        "response_sha256": _sha256_bytes(output_file.read_bytes()),
        "verified": True,
        "input_sources": inputs,
        "note": "merged local DART artifact from batch-fetched raw responses",
    }
    manifest_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge batch-fetched local DART files into canonical artifacts.",
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing batch-fetched raw DART files and sidecar manifests",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where merged artifacts are written",
    )
    parser.add_argument(
        "--filing-output",
        default="dart_filings_merged.csv",
        help="Merged filing CSV filename",
    )
    parser.add_argument(
        "--financial-output",
        default="dart_facts_merged.csv",
        help="Merged financial CSV filename",
    )
    parser.add_argument(
        "--retrieved-at-utc",
        default="",
        help="UTC timestamp recorded in output manifests (default: now UTC)",
    )

    args = parser.parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()

    filing_files = _collect_raw_files(input_dir, "filing_")
    financial_files = _collect_raw_files(input_dir, "financial_")

    filing_frame, filing_inputs = _load_frame_bundle("filing", filing_files)
    financial_frame, financial_inputs = _load_frame_bundle("financial", financial_files)

    filing_output = output_dir / args.filing_output
    financial_output = output_dir / args.financial_output
    filing_manifest = filing_output.with_suffix(".manifest.json")
    financial_manifest = financial_output.with_suffix(".manifest.json")

    filing_frame.to_csv(filing_output, index=False)
    financial_frame.to_csv(financial_output, index=False)

    _write_output_manifest(
        kind="filing",
        output_file=filing_output,
        manifest_file=filing_manifest,
        retrieved_at_utc=retrieved_at_utc,
        source_dir=input_dir,
        inputs=filing_inputs,
    )
    _write_output_manifest(
        kind="financial",
        output_file=financial_output,
        manifest_file=financial_manifest,
        retrieved_at_utc=retrieved_at_utc,
        source_dir=input_dir,
        inputs=financial_inputs,
    )

    print(f"merged filing rows: {len(filing_frame)} -> {filing_output}")
    print(f"merged financial rows: {len(financial_frame)} -> {financial_output}")


if __name__ == "__main__":
    main()