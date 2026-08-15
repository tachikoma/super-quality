#!/usr/bin/env python3
"""Parse-only processor for a verified FY2014 XBRL receipt inventory.

This script never discovers or fetches inputs.  The inventory and its manifest
are the authority for the exact receipts and filenames that may be processed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from k200_mq.data.dart_xbrl import (
    XBRL_ENDPOINT,
    XBRLError,
    parse_xbrl_facts,
    verify_xbrl_acquisition,
    write_xbrl_artifact,
)

try:
    from scripts.xbrl_inventory_contract import (
        INVENTORY_MANIFEST_VERSION,
        INVENTORY_SOURCE_TYPE,
    )
except ModuleNotFoundError:
    from xbrl_inventory_contract import INVENTORY_MANIFEST_VERSION, INVENTORY_SOURCE_TYPE


PARSE_BATCH_VERSION = "k200mq-fy2014-xbrl-parse-batch-v1"
SELECTED_DISPOSITION = "selected_first_filing"
CORP_CODE_RE = re.compile(r"^[0-9]{8}$")
RCEPT_NO_RE = re.compile(r"^[0-9]{14}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class XBRLBatchError(ValueError):
    """Raised when inventory or batch provenance is not internally consistent."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XBRLBatchError(f"invalid {label}: {path}") from exc


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value.casefold()) is None:
        raise XBRLBatchError(f"{label} must be a lowercase SHA-256 digest")
    return value.casefold()


def _require_file_hash(path: Path, expected: Any, label: str) -> str:
    if not path.is_file():
        raise XBRLBatchError(f"{label} does not exist: {path}")
    actual = _sha256_file(path)
    if actual != _require_hash(expected, f"{label} hash"):
        raise XBRLBatchError(f"{label} hash does not match: {path}")
    return actual


def _resolved_manifest_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise XBRLBatchError(f"{label} path is missing")
    return Path(value).resolve()


def _expected_spec(row: Mapping[str, Any]) -> dict[str, Any]:
    corp_code = str(row.get("corp_code") or "")
    rcept_no = str(row.get("rcept_no") or "")
    return {
        "kind": "financial_xbrl",
        "source_url": XBRL_ENDPOINT,
        "request_params": [f"rcept_no={rcept_no}", "reprt_code=11011"],
        "output_name": f"financial_xbrl_{corp_code}_{rcept_no}.bin",
        "manifest_name": f"financial_xbrl_{corp_code}_{rcept_no}.bin.manifest.json",
    }


def _validate_inventory_contract(
    inventory_path: Path,
    inventory_manifest_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], Path, str]:
    manifest = _read_json(inventory_manifest_path, "inventory manifest")
    if not isinstance(manifest, dict):
        raise XBRLBatchError("inventory manifest must be a JSON object")
    if manifest.get("manifest_version") != INVENTORY_MANIFEST_VERSION:
        raise XBRLBatchError("unsupported inventory manifest version")
    if manifest.get("source_type") != INVENTORY_SOURCE_TYPE:
        raise XBRLBatchError("inventory manifest source_type is invalid")
    declared_inventory = _resolved_manifest_path(manifest.get("inventory_path"), "inventory")
    if declared_inventory != inventory_path.resolve():
        raise XBRLBatchError("inventory manifest path does not match input inventory")
    _require_file_hash(inventory_path, manifest.get("inventory_sha256"), "inventory")

    batch_path = _resolved_manifest_path(manifest.get("batch_spec_path"), "batch spec")
    _require_file_hash(batch_path, manifest.get("batch_spec_sha256"), "batch spec")

    source_path = _resolved_manifest_path(manifest.get("source_path"), "filing source")
    source_hash = _require_file_hash(source_path, manifest.get("source_sha256"), "filing source")
    if source_hash != _require_hash(manifest.get("filing_response_sha256"), "filing response"):
        raise XBRLBatchError("filing source and filing response hashes differ")
    filing_manifest_path = _resolved_manifest_path(
        manifest.get("filing_manifest_path"), "filing manifest",
    )
    _require_file_hash(
        filing_manifest_path,
        manifest.get("filing_manifest_sha256"),
        "filing manifest",
    )
    if manifest.get("selection_rules_version") != INVENTORY_MANIFEST_VERSION:
        raise XBRLBatchError("inventory selection rule version is unsupported")

    inventory_value = _read_json(inventory_path, "inventory")
    batch_value = _read_json(batch_path, "batch spec")
    if not isinstance(inventory_value, list) or not all(isinstance(row, dict) for row in inventory_value):
        raise XBRLBatchError("inventory must be a JSON array of objects")
    if not isinstance(batch_value, list) or not all(isinstance(spec, dict) for spec in batch_value):
        raise XBRLBatchError("batch spec must be a JSON array of objects")
    if "crtfc_key" in json.dumps(batch_value, ensure_ascii=False).casefold():
        raise XBRLBatchError("batch spec must not contain an API key")

    selected = [
        row for row in inventory_value
        if row.get("source_disposition") == SELECTED_DISPOSITION
    ]
    if len({str(row.get("rcept_no")) for row in selected}) != len(selected):
        raise XBRLBatchError("selected inventory receipts are not unique")
    if len({str(row.get("corp_code")) for row in selected}) != len(selected):
        raise XBRLBatchError("selected inventory corp_codes are not unique")
    if manifest.get("selected_receipt_count") != len(selected):
        raise XBRLBatchError("inventory selected receipt count does not match rows")
    if manifest.get("selected_unique_corp_count") != len({str(row.get("corp_code")) for row in selected}):
        raise XBRLBatchError("inventory selected corp count does not match rows")

    expected_specs: list[dict[str, Any]] = []
    for row in selected:
        corp_code = str(row.get("corp_code") or "")
        rcept_no = str(row.get("rcept_no") or "")
        if not CORP_CODE_RE.fullmatch(corp_code) or not RCEPT_NO_RE.fullmatch(rcept_no):
            raise XBRLBatchError("selected inventory contains an invalid receipt identity")
        expected_specs.append(_expected_spec(row))
    if batch_value != expected_specs:
        raise XBRLBatchError("batch spec does not exactly match selected inventory rows")
    return selected, manifest, batch_path, source_hash


def _failure_row(
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    outcome: str,
    error_class: str,
    error_message: str,
    raw_path: Path,
    acquisition_manifest_path: Path,
) -> dict[str, Any]:
    return {
        "corp_code": row["corp_code"],
        "rcept_no": row["rcept_no"],
        "rcept_date": row.get("rcept_date"),
        "report_nm": row.get("report_nm"),
        "source_disposition": row["source_disposition"],
        "outcome": outcome,
        "error_class": error_class,
        "error_message": error_message,
        "raw_path": str(raw_path),
        "acquisition_manifest_path": str(acquisition_manifest_path),
        "artifact_path": None,
        "artifact_sha256": None,
        "derivation_manifest_path": None,
    }


def validate_report_rows(
    selected: list[dict[str, Any]],
    report_rows: list[dict[str, Any]],
) -> None:
    """Require exactly one outcome row for every selected receipt."""
    expected = [
        (str(row["corp_code"]), str(row["rcept_no"]))
        for row in selected
    ]
    actual = [
        (str(row.get("corp_code")), str(row.get("rcept_no")))
        for row in report_rows
    ]
    if len(actual) != len(expected) or len(set(actual)) != len(actual) or sorted(actual) != sorted(expected):
        raise XBRLBatchError(
            "processing report rows must contain exactly one row per selected receipt"
        )
    allowed_outcomes = {"accepted", "missing_raw", "invalid_acquisition", "parse_error"}
    if any(row.get("outcome") not in allowed_outcomes for row in report_rows):
        raise XBRLBatchError("processing report contains an unknown outcome")


def process_xbrl_inventory(
    inventory_path: str | Path,
    inventory_manifest_path: str | Path,
    raw_xbrl_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Process exactly the selected receipts and write deterministic reports."""
    inventory_file = Path(inventory_path).resolve()
    inventory_manifest_file = Path(inventory_manifest_path).resolve()
    raw_dir = Path(raw_xbrl_dir).resolve()
    output = Path(output_dir).resolve()
    if not raw_dir.is_dir():
        raise XBRLBatchError(f"raw XBRL directory does not exist: {raw_dir}")
    output.mkdir(parents=True, exist_ok=True)
    selected, inventory_manifest, batch_spec_path, _ = _validate_inventory_contract(
        inventory_file,
        inventory_manifest_file,
    )

    report_rows: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    for row in selected:
        spec = _expected_spec(row)
        raw_path = raw_dir / spec["output_name"]
        acquisition_manifest_path = raw_dir / spec["manifest_name"]
        if not raw_path.is_file():
            report_row = _failure_row(
                row, spec, outcome="missing_raw", error_class="MissingRaw",
                error_message=f"expected raw XBRL file is missing: {raw_path}",
                raw_path=raw_path, acquisition_manifest_path=acquisition_manifest_path,
            )
            report_rows.append(report_row)
            outcome_counts["missing_raw"] += 1
            continue
        if not acquisition_manifest_path.is_file():
            report_row = _failure_row(
                row, spec, outcome="invalid_acquisition", error_class="MissingAcquisitionManifest",
                error_message=f"expected acquisition manifest is missing: {acquisition_manifest_path}",
                raw_path=raw_path, acquisition_manifest_path=acquisition_manifest_path,
            )
            report_rows.append(report_row)
            outcome_counts["invalid_acquisition"] += 1
            continue
        try:
            verify_xbrl_acquisition(raw_path, acquisition_manifest_path)
        except XBRLError as exc:
            report_row = _failure_row(
                row, spec, outcome="invalid_acquisition", error_class=type(exc).__name__,
                error_message=str(exc), raw_path=raw_path,
                acquisition_manifest_path=acquisition_manifest_path,
            )
            report_rows.append(report_row)
            outcome_counts["invalid_acquisition"] += 1
            continue
        try:
            normalization = parse_xbrl_facts(
                raw_path,
                acquisition_manifest_path,
                corp_code=str(row["corp_code"]),
                rcept_no=str(row["rcept_no"]),
            )
            artifact_path = output / f"financial_xbrl_{row['corp_code']}_{row['rcept_no']}.json"
            derivation_manifest_path = output / f"financial_xbrl_{row['corp_code']}_{row['rcept_no']}.json.derived.manifest.json"
            write_xbrl_artifact(normalization, artifact_path, derivation_manifest_path)
            report_rows.append({
                "corp_code": row["corp_code"],
                "rcept_no": row["rcept_no"],
                "rcept_date": row.get("rcept_date"),
                "report_nm": row.get("report_nm"),
                "source_disposition": row["source_disposition"],
                "outcome": "accepted",
                "error_class": None,
                "error_message": None,
                "raw_path": str(raw_path),
                "raw_sha256": normalization.raw_xbrl_sha256,
                "acquisition_manifest_path": str(acquisition_manifest_path),
                "artifact_path": str(artifact_path),
                "artifact_sha256": _sha256_file(artifact_path),
                "derivation_manifest_path": str(derivation_manifest_path),
            })
            outcome_counts["accepted"] += 1
        except Exception as exc:
            report_rows.append(_failure_row(
                row, spec, outcome="parse_error", error_class=type(exc).__name__,
                error_message=str(exc), raw_path=raw_path,
                acquisition_manifest_path=acquisition_manifest_path,
            ))
            outcome_counts["parse_error"] += 1

    validate_report_rows(selected, report_rows)
    report_rows.sort(key=lambda row: (row["rcept_date"], row["corp_code"], row["rcept_no"]))
    report_path = output / "parse_report.json"
    summary_path = output / "parse_summary.json"
    report_bytes = (json.dumps(report_rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    report_path.write_bytes(report_bytes)
    expected_count = len(selected)
    accepted_count = outcome_counts["accepted"]
    summary = {
        "manifest_version": PARSE_BATCH_VERSION,
        "processing_complete": True,
        "all_receipts_accepted": accepted_count == expected_count,
        "inventory_path": str(inventory_file),
        "inventory_sha256": _sha256_file(inventory_file),
        "inventory_manifest_path": str(inventory_manifest_file),
        "inventory_manifest_sha256": _sha256_file(inventory_manifest_file),
        "batch_spec_path": str(batch_spec_path),
        "batch_spec_sha256": _sha256_file(batch_spec_path),
        "raw_xbrl_dir": str(raw_dir),
        "output_dir": str(output),
        "expected_selected_receipt_count": expected_count,
        "accepted_count": accepted_count,
        "outcome_counts": {key: outcome_counts[key] for key in (
            "accepted", "missing_raw", "invalid_acquisition", "parse_error",
        )},
        "report_path": str(report_path),
        "report_sha256": _sha256_bytes(report_bytes),
        "inventory_selection_rules_version": inventory_manifest["selection_rules_version"],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse selected FY2014 XBRL files without network access.")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--inventory-manifest", required=True)
    parser.add_argument("--raw-xbrl-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary = process_xbrl_inventory(
        args.inventory,
        args.inventory_manifest,
        args.raw_xbrl_dir,
        args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
