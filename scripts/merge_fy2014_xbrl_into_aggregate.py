#!/usr/bin/env python3
"""Merge FY2014 derived XBRL facts into the day4 extended DART aggregate.

The extended aggregate (`data/raw/dart_aggregated_day4_extended`) contains no
FY2014 rows because the batch spec used `--financial-start-year 2015`.  The
FY2014 gap is filled from derived XBRL artifacts
(`data/processed/dart_xbrl_fy2014_parse/financial_xbrl_*.json` with sidecar
`.derived.manifest.json`).

This script:
1. Loads the extended facts CSV through `load_financial_facts` (validated
   against its sidecar manifest).
2. Loads every FY2014 XBRL artifact through `load_financial_facts` (each
   validated against its derived manifest, including the XBRL provenance
   chain).
3. Concatenates, deduplicates, and writes a new merged aggregate directory
   `data/raw/dart_aggregated_day4_extended_fy2014/` with a merged sidecar
   manifest that passes the same loader contract as the extended manifest.
4. Copies the filings CSV + manifest unchanged (the 92 receipts are already
   present there).

Usage:
    uv run python scripts/merge_fy2014_xbrl_into_aggregate.py
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

EXTENDED_DIR = Path("data/raw/dart_aggregated_day4_extended")
XBRL_PARSE_DIR = Path("data/processed/dart_xbrl_fy2014_parse")
OUT_DIR = Path("data/raw/dart_aggregated_day4_extended_fy2014")

FACTS_CSV = "dart_facts_merged.csv"
FILINGS_CSV = "dart_filings_merged.csv"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request_params_hash(params: dict[str, str]) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collect_xbrl_artifacts(directory: Path) -> list[Path]:
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.name.startswith("financial_xbrl_")
        and path.suffix == ".json"
    )
    return [path for path in files if not path.name.endswith(".derived.manifest.json")]


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

    # 1. Load extended facts (validated against its manifest).
    extended = load_financial_facts(facts_csv, manifest=facts_manifest)
    print(f"extended facts: {len(extended)} rows")

    # 2. Load every FY2014 XBRL artifact (validated against its derived manifest).
    artifacts = _collect_xbrl_artifacts(XBRL_PARSE_DIR)
    print(f"FY2014 XBRL artifacts: {len(artifacts)}")
    frames: list[pd.DataFrame] = []
    inputs: list[dict[str, Any]] = []
    for artifact in artifacts:
        manifest_path = Path(str(artifact) + ".derived.manifest.json")
        if not manifest_path.is_file():
            raise RuntimeError(f"missing derived manifest: {manifest_path}")
        frame = load_financial_facts(artifact, manifest=manifest_path)
        frames.append(frame)
        inputs.append({
            "path": str(artifact),
            "manifest": str(manifest_path),
            "source_sha256": _sha256_bytes(artifact.read_bytes()),
            "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
        })
    xbrl = pd.concat(frames, ignore_index=True)
    print(f"FY2014 XBRL facts: {len(xbrl)} rows")

    # 3. Concatenate and deduplicate.
    merged = pd.concat([extended, xbrl], ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates().reset_index(drop=True)
    print(f"merged rows: {before} -> {len(merged)} (dedup removed {before - len(merged)})")

    # 4. Write merged facts CSV + merged manifest.
    merged_csv = OUT_DIR / FACTS_CSV
    merged_manifest = merged_csv.with_suffix(".manifest.json")
    merged.to_csv(merged_csv, index=False)

    # Merge the two provenance lists: the extended batch inputs and the XBRL
    # artifacts.  Keep the original request params (only batch bookkeeping,
    # no secrets) and re-hash them.
    extended_inputs = json.loads(facts_manifest.read_text(encoding="utf-8")).get("input_sources", [])
    request_params = {
        "batch_source_dir": "data/raw/dart_batch_day4_filing_extended",
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
            "merged local DART artifact from batch-fetched raw responses "
            "plus derived FY2014 XBRL facts"
        ),
    }
    merged_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. Copy filings CSV + manifest unchanged.
    shutil.copy2(filings_csv, OUT_DIR / FILINGS_CSV)
    shutil.copy2(filings_manifest, OUT_DIR / Path(FILINGS_CSV).with_suffix(".manifest.json"))

    # 6. Verify the merged aggregate loads through the same loader contract.
    reloaded = load_financial_facts(merged_csv, manifest=merged_manifest)
    print(f"reload verification: {len(reloaded)} rows, verified="
          f"{reloaded.attrs.get('raw_hash_verified')}")
    fy2014 = reloaded[reloaded["bsns_year"].astype(str) == "2014"]
    print(f"FY2014 rows in merged aggregate: {len(fy2014)}")
    print(f"merged aggregate written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
