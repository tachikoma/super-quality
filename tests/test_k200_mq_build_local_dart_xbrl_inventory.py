from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from k200_mq.data.dart_xbrl import request_params_sha256

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("build_local_dart_xbrl_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_filing_bundle(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[Path, Path]:
    filing = tmp_path / "filings.json"
    filing.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    manifest = filing.with_suffix(filing.suffix + ".manifest.json")
    manifest.write_text(json.dumps({
        "source_url": "https://opendart.fss.or.kr/api/list.json",
        "source_type": "opendartfilinglist",
        "request_params": {"fixture_name": filing.name},
        "request_params_sha256": request_params_sha256({"fixture_name": filing.name}),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2015-06-01T00:00:00+00:00",
        "response_sha256": hashlib.sha256(filing.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return filing, manifest


def _row(
    corp_code: str,
    rcept_no: str,
    report_nm: str,
    *,
    rcept_dt: str = "20150331",
    is_amendment: bool = False,
    is_withdrawn: bool = False,
) -> dict[str, object]:
    return {
        "corp_code": corp_code,
        "stock_code": corp_code[-6:],
        "corp_name": f"Corp {corp_code}",
        "rcept_no": rcept_no,
        "rcept_dt": rcept_dt,
        "report_nm": report_nm,
        "pblntf_ty": "A",
        "pblntf_detail_ty": "B",
        "rm": "",
        "is_amendment": is_amendment,
        "is_withdrawn": is_withdrawn,
    }


def test_inventory_dispositions_and_deterministic_specs(tmp_path: Path) -> None:
    script = _load_script_module(
        _SCRIPTS_DIR / "build_local_dart_xbrl_inventory.py"
    )
    rows = [
        _row("00100001", "20150331000002", "사업보고서 (2014.12)"),
        _row("00100001", "20150401000003", "[기재정정]사업보고서 (2014.12)", is_amendment=True),
        _row("00100001", "20150401000009", "[첨부정정]사업보고서 (2014.12)", is_amendment=True),
        _row("00100001", "20150402000004", "사업보고서 (2014.12)", is_withdrawn=True),
        _row("00100001", "20150331000005", "사업보고서 (2013.12)"),
        _row("00100001", "20150331000006", "사업보고서 (2014.12) 외국환거래"),
        _row("00100002", "20150331000001", "사업보고서 (2014.12)"),
        _row("00100003", "20150331000007", "사업보고서 (2014.12)", rcept_dt="20150530"),
        _row("00406046", "20150331000008", "[첨부추가]사업보고서 (2014.12)"),
    ]
    filing, manifest = _write_filing_bundle(tmp_path, rows)

    inventory, specs, metadata = script.build_receipt_inventory(filing, manifest)
    assert [row["source_disposition"] for row in inventory] == [
        "selected_first_filing",
        "unsupported_non_calendar",
        "unsupported_non_calendar",
        "excluded_amendment",
        "excluded_amendment",
        "excluded_withdrawn",
        "selected_first_filing",
        "selected_first_filing",
    ]
    assert [row["rcept_no"] for row in inventory] == [
        "20150331000002", "20150331000005", "20150331000006",
        "20150401000003", "20150401000009", "20150402000004", "20150331000001", "20150331000008",
    ]
    assert metadata["selected_receipt_count"] == 3
    assert metadata["selected_unique_corp_count"] == 3
    assert len(specs) == 3
    assert all(spec["kind"] == "financial_xbrl" for spec in specs)
    assert all(spec["source_url"].endswith("/api/fnlttXbrl.xml") for spec in specs)
    assert all("crtfc_key" not in json.dumps(spec) for spec in specs)
    assert specs[0]["request_params"] == ["rcept_no=20150331000002", "reprt_code=11011"]
    assert specs[-1]["request_params"] == ["rcept_no=20150331000008", "reprt_code=11011"]

    inventory_again, specs_again, metadata_again = script.build_receipt_inventory(filing, manifest)
    assert inventory_again == inventory
    assert specs_again == specs
    assert metadata_again == metadata


def test_inventory_requires_exactly_one_selected_original(tmp_path: Path) -> None:
    script = _load_script_module(
        _SCRIPTS_DIR / "build_local_dart_xbrl_inventory.py"
    )
    rows = [
        _row("00100001", "20150331000001", "사업보고서 (2014.12)"),
        _row("00100001", "20150331000002", "사업보고서 (2014.12)"),
    ]
    filing, manifest = _write_filing_bundle(tmp_path, rows)
    with pytest.raises(script.XBRLInventoryError, match="exactly one"):
        script.build_receipt_inventory(filing, manifest)


def test_inventory_requires_original_when_only_foreign_notice_exists(tmp_path: Path) -> None:
    script = _load_script_module(
        _SCRIPTS_DIR / "build_local_dart_xbrl_inventory.py"
    )
    filing, manifest = _write_filing_bundle(tmp_path, [
        _row("00100001", "20150331000001", "사업보고서 (2014.12) 외국환거래"),
    ])
    with pytest.raises(script.XBRLInventoryError, match="exactly one"):
        script.build_receipt_inventory(filing, manifest)


def test_inventory_rejects_unverified_filing_manifest(tmp_path: Path) -> None:
    script = _load_script_module(
        _SCRIPTS_DIR / "build_local_dart_xbrl_inventory.py"
    )
    filing, manifest = _write_filing_bundle(tmp_path, [
        _row("00100001", "20150331000001", "사업보고서 (2014.12)"),
    ])
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["response_sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(script.XBRLInventoryError, match="unable to load verified"):
        script.build_receipt_inventory(filing, manifest)


def test_inventory_outputs_bind_hashes_and_counts(tmp_path: Path) -> None:
    script = _load_script_module(
        _SCRIPTS_DIR / "build_local_dart_xbrl_inventory.py"
    )
    filing, filing_manifest = _write_filing_bundle(tmp_path, [
        _row("00100001", "20150331000001", "사업보고서 (2014.12)"),
    ])
    inventory, specs, metadata = script.build_receipt_inventory(filing, filing_manifest)
    inventory_path = tmp_path / "inventory.json"
    batch_path = tmp_path / "batch.json"
    output_manifest_path = tmp_path / "inventory.manifest.json"
    output = script.write_inventory_outputs(
        inventory,
        specs,
        metadata,
        inventory_path=inventory_path,
        batch_spec_path=batch_path,
        inventory_manifest_path=output_manifest_path,
    )
    assert output["source_sha256"] == hashlib.sha256(filing.read_bytes()).hexdigest()
    assert output["filing_manifest_sha256"] == hashlib.sha256(filing_manifest.read_bytes()).hexdigest()
    assert output["inventory_sha256"] == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    assert output["batch_spec_sha256"] == hashlib.sha256(batch_path.read_bytes()).hexdigest()
    assert output["count_by_disposition"]["selected_first_filing"] == 1
    assert output["selected_unique_corp_count"] == 1
