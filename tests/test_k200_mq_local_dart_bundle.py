from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("build_local_dart_bundle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_raw(tmp_path: Path, name: str, rows: list[dict[str, object]]) -> tuple[Path, Path]:
    source = tmp_path / name
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    manifest = source.with_suffix(source.suffix + ".manifest.json")
    manifest.write_text(json.dumps({
        "response_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_url": (
            "https://opendart.fss.or.kr/api/list.json"
            if "filing" in name else "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        ),
        "request_params": {"fixture": name},
        "request_params_sha256": hashlib.sha256(
            json.dumps({"fixture": name}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2024-01-03T00:00:00+00:00",
    }, ensure_ascii=False), encoding="utf-8")
    return source, manifest


def test_local_dart_bundle_script_writes_canonical_outputs(tmp_path: Path) -> None:
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/build_local_dart_bundle.py"))

    filing_source, filing_manifest = _write_raw(tmp_path, "filing.json", [{
        "corp_code": "001",
        "stock_code": "005930",
        "corp_name": "Example",
        "rcept_no": "R1",
        "rcept_dt": "20240102",
        "report_nm": "사업보고서",
        "pblntf_ty": "A",
        "pblntf_detail_ty": "B",
        "rm": "",
    }])
    filing_output = tmp_path / "canonical_filing.csv"
    filing_output_manifest = tmp_path / "canonical_filing.manifest.json"

    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "build_local_dart_bundle.py",
            "--kind", "filing",
            "--input-file", str(filing_source),
            "--input-manifest", str(filing_manifest),
            "--output-file", str(filing_output),
            "--manifest-file", str(filing_output_manifest),
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    assert filing_output.is_file()
    assert filing_output_manifest.is_file()
    filing_manifest_data = json.loads(filing_output_manifest.read_text(encoding="utf-8"))
    assert filing_manifest_data["response_sha256"] == hashlib.sha256(filing_output.read_bytes()).hexdigest()
    assert filing_manifest_data["source_type"] == "opendartfilinglist"

    fact_source, fact_manifest = _write_raw(tmp_path, "facts.json", [{
        "rcept_no": "R1",
        "corp_code": "001",
        "bsns_year": "2023",
        "reprt_code": "11011",
        "fs_div": "CFS",
        "sj_div": "BS",
        "account_id": "ifrs-full_Revenue",
        "account_nm": "Revenue",
        "account_detail": "consolidated",
        "period_end": "20231231",
        "thstrm_amount": "1,000",
        "currency": "KRW",
    }])
    fact_output = tmp_path / "canonical_facts.csv"
    fact_output_manifest = tmp_path / "canonical_facts.manifest.json"

    try:
        sys.argv = [
            "build_local_dart_bundle.py",
            "--kind", "financial",
            "--input-file", str(fact_source),
            "--input-manifest", str(fact_manifest),
            "--output-file", str(fact_output),
            "--manifest-file", str(fact_output_manifest),
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    assert fact_output.is_file()
    assert fact_output_manifest.is_file()
    fact_manifest_data = json.loads(fact_output_manifest.read_text(encoding="utf-8"))
    assert fact_manifest_data["response_sha256"] == hashlib.sha256(fact_output.read_bytes()).hexdigest()
    assert fact_manifest_data["source_type"] == "opendartfinancialfacts"