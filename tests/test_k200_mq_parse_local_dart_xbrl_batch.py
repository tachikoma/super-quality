from __future__ import annotations

from io import BytesIO
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

from k200_mq.data.dart_xbrl import XBRL_ENDPOINT, request_params_sha256

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _inventory_module():
    return _load_module(
        "build_local_dart_xbrl_inventory_for_batch_test",
        _SCRIPTS_DIR / "build_local_dart_xbrl_inventory.py",
    )


def _batch_module():
    return _load_module(
        "parse_local_dart_xbrl_batch",
        _SCRIPTS_DIR / "parse_local_dart_xbrl_batch.py",
    )


def _write_filing(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    filing = tmp_path / "filings.json"
    filing.write_text(json.dumps([{
        "corp_code": "00100001",
        "stock_code": "100001",
        "corp_name": "Example",
        "rcept_no": "20150331000001",
        "rcept_dt": "20150331",
        "report_nm": "사업보고서 (2014.12)",
        "pblntf_ty": "A",
        "pblntf_detail_ty": "B",
        "rm": "",
    }], ensure_ascii=False), encoding="utf-8")
    manifest = filing.with_suffix(filing.suffix + ".manifest.json")
    params = {"fixture_name": filing.name}
    digest = hashlib.sha256(filing.read_bytes()).hexdigest()
    manifest.write_text(json.dumps({
        "source_url": "https://opendart.fss.or.kr/api/list.json",
        "source_type": "opendartfilinglist",
        "request_params": params,
        "request_params_sha256": request_params_sha256(params),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2015-06-01T00:00:00+00:00",
        "response_sha256": digest,
    }), encoding="utf-8")
    return filing, manifest


def _xbrl_xml() -> bytes:
    return b'''<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:axis="http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full"
 xmlns:member="http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full">
 <xbrli:unit id="KRW"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>
 <xbrli:context id="duration"><xbrli:entity><xbrli:identifier scheme="http://opendart.fss.or.kr">00100001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2014-01-01</xbrli:startDate><xbrli:endDate>2014-12-31</xbrli:endDate></xbrli:period>
  <xbrli:scenario><xbrldi:explicitMember dimension="axis:ConsolidatedAndSeparateFinancialStatementsAxis">member:ConsolidatedMember</xbrldi:explicitMember></xbrli:scenario>
 </xbrli:context>
 <xbrli:context id="instant"><xbrli:entity><xbrli:identifier scheme="http://opendart.fss.or.kr">00100001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2014-12-31</xbrli:instant></xbrli:period>
  <xbrli:scenario><xbrldi:explicitMember dimension="axis:ConsolidatedAndSeparateFinancialStatementsAxis">member:ConsolidatedMember</xbrldi:explicitMember></xbrli:scenario>
 </xbrli:context>
 <ifrs-full:Revenue contextRef="duration" unitRef="KRW">10</ifrs-full:Revenue>
 <ifrs-full:CostOfSales contextRef="duration" unitRef="KRW">5</ifrs-full:CostOfSales>
 <ifrs-full:ProfitLoss contextRef="duration" unitRef="KRW">2</ifrs-full:ProfitLoss>
 <ifrs-full:CashFlowsFromUsedInOperatingActivities contextRef="duration" unitRef="KRW">3</ifrs-full:CashFlowsFromUsedInOperatingActivities>
 <ifrs-full:Assets contextRef="instant" unitRef="KRW">20</ifrs-full:Assets>
 <ifrs-full:Equity contextRef="instant" unitRef="KRW">12</ifrs-full:Equity>
</xbrli:xbrl>'''


def _write_raw(raw_dir: Path, *, status: str = "000", malformed: bool = False) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "financial_xbrl_00100001_20150331000001.bin"
    payload = b"not-a-zip" if malformed else _zip_bytes(_xbrl_xml())
    raw_path.write_bytes(payload)
    params = {"rcept_no": "20150331000001", "reprt_code": "11011"}
    digest = hashlib.sha256(payload).hexdigest()
    manifest = raw_dir / "financial_xbrl_00100001_20150331000001.bin.manifest.json"
    manifest.write_text(json.dumps({
        "source_url": XBRL_ENDPOINT,
        "source_type": "opendartfinancialxbrl",
        "request_params": params,
        "request_params_sha256": request_params_sha256(params),
        "api_status": status,
        "response_format": "zip",
        "response_status": "success" if status == "000" else "error",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2015-06-01T00:00:00+00:00",
        "response_sha256": digest,
        "raw_file_sha256": digest,
        "raw_payload_path": str(raw_path.resolve()),
        "verified": status == "000",
    }), encoding="utf-8")


def _zip_bytes(instance: bytes) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("statement.xbrl", instance)
    return buffer.getvalue()


def _build_fixture(tmp_path: Path):
    inventory_builder = _inventory_module()
    batch = _batch_module()
    filing, filing_manifest = _write_filing(tmp_path)
    inventory, specs, metadata = inventory_builder.build_receipt_inventory(filing, filing_manifest)
    inventory_path = tmp_path / "inventory.json"
    batch_spec_path = tmp_path / "batch.json"
    inventory_manifest_path = tmp_path / "inventory.manifest.json"
    inventory_builder.write_inventory_outputs(
        inventory, specs, metadata,
        inventory_path=inventory_path,
        batch_spec_path=batch_spec_path,
        inventory_manifest_path=inventory_manifest_path,
    )
    return batch, inventory_path, inventory_manifest_path, tmp_path / "raw", tmp_path / "out"


def test_parse_batch_accepts_selected_receipt_and_is_deterministic(tmp_path: Path) -> None:
    batch, inventory, inventory_manifest, raw_dir, output_dir = _build_fixture(tmp_path)
    _write_raw(raw_dir)
    first = batch.process_xbrl_inventory(inventory, inventory_manifest, raw_dir, output_dir)
    first_report = json.loads((output_dir / "parse_report.json").read_text(encoding="utf-8"))
    assert first["processing_complete"] is True
    assert first["all_receipts_accepted"] is True
    assert "valid" not in first
    assert first["accepted_count"] == 1
    assert first["outcome_counts"] == {"accepted": 1, "missing_raw": 0, "invalid_acquisition": 0, "parse_error": 0}
    assert first_report[0]["outcome"] == "accepted"
    assert Path(first_report[0]["artifact_path"]).is_file()
    second = batch.process_xbrl_inventory(inventory, inventory_manifest, raw_dir, output_dir)
    assert second == first
    assert json.loads((output_dir / "parse_report.json").read_text(encoding="utf-8")) == first_report


def test_builder_output_uses_shared_manifest_contract_end_to_end(tmp_path: Path) -> None:
    batch, inventory, inventory_manifest, raw_dir, output_dir = _build_fixture(tmp_path)
    _write_raw(raw_dir)
    manifest = json.loads(inventory_manifest.read_text(encoding="utf-8"))
    contract = _load_module(
        "xbrl_inventory_contract_for_batch_test",
        _SCRIPTS_DIR / "xbrl_inventory_contract.py",
    )
    assert manifest["manifest_version"] == contract.INVENTORY_MANIFEST_VERSION
    assert manifest["selection_rules_version"] == contract.INVENTORY_MANIFEST_VERSION
    summary = batch.process_xbrl_inventory(inventory, inventory_manifest, raw_dir, output_dir)
    assert summary["processing_complete"] is True
    assert summary["all_receipts_accepted"] is True
    assert summary["accepted_count"] == 1


@pytest.mark.parametrize("mode,expected", [("missing", "missing_raw"), ("malformed", "parse_error"), ("status", "invalid_acquisition")])
def test_parse_batch_reports_every_selected_receipt(tmp_path: Path, mode: str, expected: str) -> None:
    batch, inventory, inventory_manifest, raw_dir, output_dir = _build_fixture(tmp_path)
    raw_dir.mkdir()
    if mode == "malformed":
        _write_raw(raw_dir, malformed=True)
    elif mode == "status":
        _write_raw(raw_dir, status="020")
    summary = batch.process_xbrl_inventory(inventory, inventory_manifest, raw_dir, output_dir)
    report = json.loads((output_dir / "parse_report.json").read_text(encoding="utf-8"))
    assert summary["processing_complete"] is True
    assert summary["all_receipts_accepted"] is False
    assert summary["accepted_count"] == 0
    assert summary["outcome_counts"][expected] == 1
    assert report[0]["outcome"] == expected
    assert report[0]["error_class"]
    assert report[0]["error_message"]


def test_parse_batch_rejects_missing_or_duplicate_report_row() -> None:
    batch = _batch_module()
    selected = [{"corp_code": "00100001", "rcept_no": "20150331000001"}]
    with pytest.raises(batch.XBRLBatchError, match="exactly one row"):
        batch.validate_report_rows(selected, [])
    with pytest.raises(batch.XBRLBatchError, match="exactly one row"):
        batch.validate_report_rows(selected, [
            {"corp_code": "00100001", "rcept_no": "20150331000001", "outcome": "accepted"},
            {"corp_code": "00100001", "rcept_no": "20150331000001", "outcome": "accepted"},
        ])


def test_parse_batch_rejects_inventory_or_batch_hash_mutation(tmp_path: Path) -> None:
    batch, inventory, inventory_manifest, raw_dir, output_dir = _build_fixture(tmp_path)
    _write_raw(raw_dir)
    manifest = json.loads(inventory_manifest.read_text(encoding="utf-8"))
    manifest["inventory_sha256"] = "0" * 64
    inventory_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(batch.XBRLBatchError, match="inventory hash"):
        batch.process_xbrl_inventory(inventory, inventory_manifest, raw_dir, output_dir)

    _, inventory, inventory_manifest, raw_dir, output_dir = _build_fixture(tmp_path / "batch")
    _write_raw(raw_dir)
    batch_spec = Path(json.loads(inventory_manifest.read_text(encoding="utf-8"))["batch_spec_path"])
    batch_spec.write_text("[]", encoding="utf-8")
    with pytest.raises(batch.XBRLBatchError, match="batch spec hash"):
        batch.process_xbrl_inventory(inventory, inventory_manifest, raw_dir, output_dir)
