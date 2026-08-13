#!/usr/bin/env python3
"""Build a deterministic FY2014 XBRL receipt inventory and fetch batch spec."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from k200_mq.data.dart_pit import load_filing_metadata
from k200_mq.data.dart_xbrl import XBRL_ENDPOINT

try:
    from scripts.xbrl_inventory_contract import INVENTORY_MANIFEST_VERSION
except ModuleNotFoundError:
    from xbrl_inventory_contract import INVENTORY_MANIFEST_VERSION


INVENTORY_VERSION = INVENTORY_MANIFEST_VERSION
ANNUAL_REPORT_NAME = "사업보고서 (2014.12)"
FY2014_ANNUAL_REPORT_RE = re.compile(
    r"^(?:\[(?:기재정정|첨부추가|첨부정정)\])?사업보고서 \(2014\.12\)$"
)
DISPOSITIONS = (
    "selected_first_filing",
    "excluded_amendment",
    "excluded_withdrawn",
    "unsupported_non_calendar",
)
CORP_CODE_RE = re.compile(r"^[0-9]{8}$")
RCEPT_NO_RE = re.compile(r"^[0-9]{14}$")


class XBRLInventoryError(ValueError):
    """Raised when a verified filing bundle cannot produce a safe inventory."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _parse_cutoff(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise XBRLInventoryError(f"cutoff date must be YYYY-MM-DD: {value!r}") from exc


def _bool_field(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "yes", "1", "정정", "철회", "withdrawn"}:
        return True
    if text in {"false", "no", "0", "정상", "아니오"}:
        return False
    raise XBRLInventoryError(f"filing status is unknown: {value!r}")


def _report_name(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _source_lineage(
    filings: pd.DataFrame,
    filing_path: Path,
    filing_manifest_path: Path,
) -> dict[str, Any]:
    if filings.attrs.get("manifest_verified") is not True or filings.attrs.get("raw_hash_verified") is not True:
        raise XBRLInventoryError("filing bundle must have verified raw-file manifest provenance")
    raw_path_value = filings.attrs.get("raw_source_path")
    if not isinstance(raw_path_value, str) or Path(raw_path_value).resolve() != filing_path.resolve():
        raise XBRLInventoryError("filing loader lineage does not match filing path")
    if not filing_path.is_file() or not filing_manifest_path.is_file():
        raise XBRLInventoryError("filing source and manifest must be local files")
    manifest_value = filings.attrs.get("response_manifest")
    if not isinstance(manifest_value, dict):
        raise XBRLInventoryError("verified filing manifest content is missing")
    response_hash = manifest_value.get("response_sha256")
    if not isinstance(response_hash, str) or len(response_hash) != 64:
        raise XBRLInventoryError("verified filing manifest response hash is missing")
    source_hash = _sha256_file(filing_path)
    if source_hash.casefold() != response_hash.casefold():
        raise XBRLInventoryError("filing source hash does not match verified manifest")
    return {
        "source_path": str(filing_path.resolve()),
        "source_sha256": source_hash,
        "filing_manifest_path": str(filing_manifest_path.resolve()),
        "filing_manifest_sha256": _sha256_file(filing_manifest_path),
        "filing_response_sha256": response_hash.casefold(),
    }


def _disposition(report_name: str, is_amendment: bool, is_withdrawn: bool) -> str:
    if FY2014_ANNUAL_REPORT_RE.fullmatch(report_name):
        if is_withdrawn:
            return "excluded_withdrawn"
        if is_amendment:
            return "excluded_amendment"
        return "selected_first_filing"
    return "unsupported_non_calendar"


def _validate_receipt_identity(row: dict[str, Any]) -> None:
    corp_code = str(row.get("corp_code") or "").strip()
    rcept_no = str(row.get("rcept_no") or "").strip()
    if not CORP_CODE_RE.fullmatch(corp_code):
        raise XBRLInventoryError(f"selected filing has invalid 8-digit corp_code: {corp_code!r}")
    if not RCEPT_NO_RE.fullmatch(rcept_no):
        raise XBRLInventoryError(f"selected filing has invalid 14-digit rcept_no: {rcept_no!r}")
    try:
        parsed_date = date.fromisoformat(str(row.get("rcept_date")))
    except ValueError as exc:
        raise XBRLInventoryError(
            f"selected filing has invalid rcept_date: {row.get('rcept_date')!r}"
        ) from exc
    if parsed_date.isoformat() != str(row.get("rcept_date")):
        raise XBRLInventoryError(f"selected filing has invalid rcept_date: {row.get('rcept_date')!r}")


def build_receipt_inventory(
    filing_path: str | Path,
    filing_manifest_path: str | Path,
    *,
    cutoff_date: str | date = "2015-05-29",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Load a verified filing bundle and build inventory rows and XBRL specs."""
    source_path = Path(filing_path).resolve()
    manifest_path = Path(filing_manifest_path).resolve()
    cutoff = _parse_cutoff(cutoff_date)
    try:
        filings = load_filing_metadata(source_path, manifest=manifest_path)
    except Exception as exc:
        raise XBRLInventoryError(f"unable to load verified filing bundle: {exc}") from exc
    lineage = _source_lineage(filings, source_path, manifest_path)
    required = {"corp_code", "rcept_no", "rcept_date", "report_nm", "is_amendment", "is_withdrawn"}
    missing = sorted(required.difference(filings.columns))
    if missing:
        raise XBRLInventoryError("filing bundle is missing columns: " + ", ".join(missing))

    scoped: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()
    for row in filings.to_dict(orient="records"):
        report_name = _report_name(row.get("report_nm"))
        receipt_date = row.get("rcept_date")
        if "사업보고서" not in report_name or not isinstance(receipt_date, date) or receipt_date > cutoff:
            continue
        record = {
            "corp_code": str(row.get("corp_code") or "").strip(),
            "rcept_no": str(row.get("rcept_no") or "").strip(),
            "rcept_date": receipt_date.isoformat(),
            "report_nm": report_name,
            "is_amendment": _bool_field(row.get("is_amendment")),
            "is_withdrawn": _bool_field(row.get("is_withdrawn")),
        }
        if record["rcept_no"] in seen_receipts:
            raise XBRLInventoryError(f"filing bundle contains duplicate source receipt: {record['rcept_no']}")
        seen_receipts.add(record["rcept_no"])
        record["source_disposition"] = _disposition(
            report_name,
            record["is_amendment"],
            record["is_withdrawn"],
        )
        scoped.append(record)

    scoped.sort(key=lambda item: (
        item["rcept_date"], item["corp_code"], item["rcept_no"], item["report_nm"],
    ))
    selected = [row for row in scoped if row["source_disposition"] == "selected_first_filing"]
    by_corp: dict[str, list[dict[str, Any]]] = {}
    for row in scoped:
        by_corp.setdefault(row["corp_code"], []).append(row)
    for corp_code, rows in sorted(by_corp.items()):
        selected_for_corp = [row for row in rows if row["source_disposition"] == "selected_first_filing"]
        if len(selected_for_corp) != 1:
            raise XBRLInventoryError(
                f"corp_code {corp_code!r} requires exactly one original FY2014 filing; "
                f"found {len(selected_for_corp)}"
            )
    selected_corps = [row["corp_code"] for row in selected]
    if len(selected_corps) != len(set(selected_corps)):
        raise XBRLInventoryError("selected FY2014 filings contain duplicate corp_code values")
    for row in selected:
        _validate_receipt_identity(row)

    specs = [
        {
            "kind": "financial_xbrl",
            "source_url": XBRL_ENDPOINT,
            "request_params": [f"rcept_no={row['rcept_no']}", "reprt_code=11011"],
            "output_name": f"financial_xbrl_{row['corp_code']}_{row['rcept_no']}.bin",
            "manifest_name": f"financial_xbrl_{row['corp_code']}_{row['rcept_no']}.bin.manifest.json",
        }
        for row in selected
    ]
    counts = {disposition: 0 for disposition in DISPOSITIONS}
    counts.update(Counter(row["source_disposition"] for row in scoped))
    metadata = {
        **lineage,
        "cutoff_date": cutoff.isoformat(),
        "selection_rules_version": INVENTORY_VERSION,
        "count_by_disposition": counts,
        "selected_receipt_count": len(selected),
        "selected_unique_corp_count": len(set(selected_corps)),
    }
    return scoped, specs, metadata


def write_inventory_outputs(
    inventory: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    inventory_path: str | Path,
    batch_spec_path: str | Path,
    inventory_manifest_path: str | Path,
) -> dict[str, Any]:
    """Write deterministic inventory/spec JSON and a lineage-bound manifest."""
    inventory_file = Path(inventory_path).resolve()
    batch_file = Path(batch_spec_path).resolve()
    manifest_file = Path(inventory_manifest_path).resolve()
    for path in (inventory_file, batch_file, manifest_file):
        path.parent.mkdir(parents=True, exist_ok=True)
    inventory_bytes = (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    batch_bytes = (json.dumps(specs, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    inventory_file.write_bytes(inventory_bytes)
    batch_file.write_bytes(batch_bytes)
    manifest = {
        "manifest_version": INVENTORY_VERSION,
        "source_type": "k200mq_verified_fy2014_filing_inventory",
        **metadata,
        "inventory_path": str(inventory_file),
        "inventory_sha256": _sha256_bytes(inventory_bytes),
        "batch_spec_path": str(batch_file),
        "batch_spec_sha256": _sha256_bytes(batch_bytes),
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FY2014 XBRL receipt inventory and batch specs.")
    parser.add_argument("--filing-path", required=True)
    parser.add_argument("--filing-manifest", required=True)
    parser.add_argument("--cutoff-date", default="2015-05-29")
    parser.add_argument("--inventory-output", required=True)
    parser.add_argument("--batch-spec-output", required=True)
    parser.add_argument("--inventory-manifest-output", required=True)
    args = parser.parse_args()
    inventory, specs, metadata = build_receipt_inventory(
        args.filing_path,
        args.filing_manifest,
        cutoff_date=args.cutoff_date,
    )
    write_inventory_outputs(
        inventory,
        specs,
        metadata,
        inventory_path=args.inventory_output,
        batch_spec_path=args.batch_spec_output,
        inventory_manifest_path=args.inventory_manifest_output,
    )
    print(f"inventory rows: {len(inventory)}; selected XBRL specs: {len(specs)}")


if __name__ == "__main__":
    main()
