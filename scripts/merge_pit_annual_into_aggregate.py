#!/usr/bin/env python3
"""Merge newly fetched PIT annual financial facts into the FY2014 aggregate.

The extended aggregate (`dart_aggregated_day4_extended_fy2014`) lacks DART
facts for 97 PIT-universe corps (2015-05-29 universe members). This script:

1. Loads the existing FY2014-merged facts CSV (validated against its manifest).
2. Loads every successful (api_status=000) annual financial response from
   `data/raw/dart_batch_pit_annual/` through the local loader contract.
3. Concatenates, deduplicates, writes a new aggregate directory
   `data/raw/dart_aggregated_day4_extended_fy2014_pit/` with a merged manifest.
4. Copies the filings CSV + manifest unchanged.

Usage:
    uv run python scripts/merge_pit_annual_into_aggregate.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from k200_mq.data.dart_pit import load_financial_facts

EXTENDED_DIR = Path("data/raw/dart_aggregated_day4_extended_fy2014")
NEW_BATCH_DIR = Path("data/raw/dart_batch_pit_annual")
NEW_FILING_DIRS = [
    Path("data/raw/dart_batch_pit_filing_2015_2017"),
    Path("data/raw/dart_batch_pit_filing_2018_2020"),
    Path("data/raw/dart_batch_pit_filing_2021_2024"),
    Path("data/raw/dart_batch_pit_filing_paginated"),
    Path("data/raw/dart_batch_pit_filing_paginated_2026"),
]
OUT_DIR = Path("data/raw/dart_aggregated_day4_extended_fy2014_pit")

FACTS_CSV = "dart_facts_merged.csv"
FILINGS_CSV = "dart_filings_merged.csv"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request_params_hash(params: dict[str, str]) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    if not OUT_DIR.exists():
        OUT_DIR.mkdir(parents=True)
    elif any(OUT_DIR.iterdir()):
        raise RuntimeError(f"output directory already has contents: {OUT_DIR}")

    facts_csv = EXTENDED_DIR / FACTS_CSV
    facts_manifest = facts_csv.with_suffix(".manifest.json")
    filings_csv = EXTENDED_DIR / FILINGS_CSV
    filings_manifest = filings_csv.with_suffix(".manifest.json")
    for path in (facts_csv, facts_manifest, filings_csv, filings_manifest):
        if not path.is_file():
            raise RuntimeError(f"missing input: {path}")

    # 1. Load existing aggregate facts (validated).
    extended = load_financial_facts(facts_csv, manifest=facts_manifest)
    print(f"existing facts: {len(extended)} rows")

    # 2. Load every successful annual response from the new batch.
    frames: list[pd.DataFrame] = []
    inputs: list[dict[str, Any]] = []
    skipped_status: dict[str, int] = {}
    for artifact in sorted(NEW_BATCH_DIR.glob("financial_*.json")):
        if artifact.name.endswith(".manifest.json"):
            continue
        manifest_path = artifact.with_suffix(".manifest.json")
        if not manifest_path.is_file():
            raise RuntimeError(f"missing sidecar manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        status = str(manifest.get("api_status", manifest.get("status", "")))
        if status not in {"000", "0"}:
            skipped_status[status] = skipped_status.get(status, 0) + 1
            continue
        frame = load_financial_facts(artifact, manifest=manifest_path)
        frames.append(frame)
        inputs.append({
            "path": str(artifact),
            "manifest": str(manifest_path),
            "source_sha256": _sha256_bytes(artifact.read_bytes()),
            "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
        })
    if not frames:
        raise RuntimeError("no successful financial responses to merge")
    new_facts = pd.concat(frames, ignore_index=True)
    print(f"new PIT annual facts: {len(new_facts)} rows ({len(frames)} files)")
    print(f"skipped by status: {skipped_status}")

    # 3. Concatenate and deduplicate.
    merged = pd.concat([extended, new_facts], ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates().reset_index(drop=True)
    print(f"merged rows: {before} -> {len(merged)} (dedup removed {before - len(merged)})")

    # 4. Write merged facts CSV + merged manifest.
    merged_csv = OUT_DIR / FACTS_CSV
    merged_manifest = merged_csv.with_suffix(".manifest.json")
    merged.to_csv(merged_csv, index=False)

    extended_inputs = json.loads(facts_manifest.read_text(encoding="utf-8")).get("input_sources", [])
    request_params = {
        "batch_source_dir": "data/raw/dart_aggregated_day4_extended_fy2014_pit",
        "kind": "financial",
        "input_count": str(len(extended_inputs) + len(inputs)),
    }
    payload = {
        "source_url": "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        "source_type": "opendartfinancialfacts",
        "request_params": request_params,
        "request_params_sha256": _request_params_hash(request_params),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "response_sha256": _sha256_bytes(merged_csv.read_bytes()),
        "verified": True,
        "input_sources": extended_inputs + inputs,
        "note": (
            "merged local DART artifact from day4 extended + FY2014 XBRL + "
            "PIT annual backfill (2015-2024)"
        ),
    }
    merged_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. Merge filings: existing filings CSV + newly fetched filing responses.
    from k200_mq.data.dart_pit import load_filing_metadata

    existing_filings = load_filing_metadata(filings_csv, manifest=filings_manifest)
    print(f"existing filings: {len(existing_filings)} rows")
    filing_frames: list[pd.DataFrame] = []
    filing_inputs: list[dict[str, Any]] = []
    for filing_dir in NEW_FILING_DIRS:
        for artifact in sorted(filing_dir.glob("filing_*.json")):
            if artifact.name.endswith(".manifest.json"):
                continue
            manifest_path = artifact.with_suffix(".manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            status = str(manifest.get("api_status", manifest.get("status", "")))
            if status not in {"000", "0"}:
                continue
            filing_frames.append(load_filing_metadata(artifact, manifest=manifest_path))
            filing_inputs.append({
                "path": str(artifact),
                "manifest": str(manifest_path),
                "source_sha256": _sha256_bytes(artifact.read_bytes()),
                "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
            })
    new_filings = pd.concat(filing_frames, ignore_index=True) if filing_frames else pd.DataFrame()
    merged_filings = pd.concat([existing_filings, new_filings], ignore_index=True)
    # (corp_code, rcept_no) is the strict join key; paginated OpenDART responses
    # repeat identical receipts, so dedupe on that key (not full-row dedup,
    # which differs by raw_payload_path).
    merged_filings = merged_filings.drop_duplicates(
        subset=["corp_code", "rcept_no"]
    ).reset_index(drop=True)
    print(f"merged filings: {len(merged_filings)} rows (new={len(new_filings)})")

    merged_filings_csv = OUT_DIR / FILINGS_CSV
    merged_filings.to_csv(merged_filings_csv, index=False)
    merged_filings_manifest = merged_filings_csv.with_suffix(".manifest.json")
    existing_filing_inputs = json.loads(
        filings_manifest.read_text(encoding="utf-8")
    ).get("input_sources", [])
    filing_request_params = {
        "batch_source_dir": "data/raw/dart_aggregated_day4_extended_fy2014_pit",
        "kind": "filing",
        "input_count": str(len(existing_filing_inputs) + len(filing_inputs)),
    }
    filing_payload = {
        "source_url": "https://opendart.fss.or.kr/api/list.json",
        "source_type": "opendartfilinglist",
        "request_params": filing_request_params,
        "request_params_sha256": _request_params_hash(filing_request_params),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "response_sha256": _sha256_bytes(merged_filings_csv.read_bytes()),
        "verified": True,
        "input_sources": existing_filing_inputs + filing_inputs,
        "note": (
            "merged local DART filing metadata from day4 extended + "
            "PIT filing backfill (2015-2024)"
        ),
    }
    merged_filings_manifest.write_text(
        json.dumps(filing_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 6. Verify the merged aggregate loads through the same loader contract.
    reloaded = load_financial_facts(merged_csv, manifest=merged_manifest)
    print(f"reload verification: {len(reloaded)} rows, verified="
          f"{reloaded.attrs.get('raw_hash_verified')}")
    print(f"merged aggregate written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
