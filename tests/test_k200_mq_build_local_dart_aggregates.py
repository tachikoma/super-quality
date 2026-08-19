from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("build_local_dart_aggregates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_raw(tmp_path: Path, name: str, rows: list[dict[str, object]], source_url: str, source_type: str) -> None:
    source = tmp_path / name
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    manifest = source.with_suffix(source.suffix + ".manifest.json")
    request_params = {"fixture_name": name}
    request_hash = hashlib.sha256(
        json.dumps(request_params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest.write_text(json.dumps({
        "response_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_url": source_url,
        "source_type": source_type,
        "request_params": request_params,
        "request_params_sha256": request_hash,
        "api_status": "000",
        "pagination": {"complete": True},
        "retrieved_at_utc": "2024-01-03T00:00:00+00:00",
    }, ensure_ascii=False), encoding="utf-8")


def test_build_local_dart_aggregates_from_batch_dir(tmp_path: Path) -> None:
    script = _load_script_module(_SCRIPTS_DIR / "build_local_dart_aggregates.py")

    _write_raw(
        tmp_path,
        "filing_001.json",
        [{
            "corp_code": "001",
            "stock_code": "005930",
            "corp_name": "Example",
            "rcept_no": "R1",
            "rcept_dt": "20240102",
            "report_nm": "사업보고서",
            "pblntf_ty": "A",
            "pblntf_detail_ty": "B",
            "rm": "",
        }],
        "https://opendart.fss.or.kr/api/list.json",
        "opendartfilinglist",
    )
    _write_raw(
        tmp_path,
        "financial_001_2023_11011.json",
        [{
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
        }],
        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        "opendartfinancialfacts",
    )

    out_dir = tmp_path / "out"
    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "build_local_dart_aggregates.py",
            "--input-dir", str(tmp_path),
            "--output-dir", str(out_dir),
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    filing_out = out_dir / "dart_filings_merged.csv"
    facts_out = out_dir / "dart_facts_merged.csv"
    filing_manifest = out_dir / "dart_filings_merged.manifest.json"
    facts_manifest = out_dir / "dart_facts_merged.manifest.json"

    assert filing_out.is_file()
    assert facts_out.is_file()
    assert filing_manifest.is_file()
    assert facts_manifest.is_file()

    filing_manifest_data = json.loads(filing_manifest.read_text(encoding="utf-8"))
    facts_manifest_data = json.loads(facts_manifest.read_text(encoding="utf-8"))
    assert filing_manifest_data["response_sha256"] == hashlib.sha256(filing_out.read_bytes()).hexdigest()
    assert facts_manifest_data["response_sha256"] == hashlib.sha256(facts_out.read_bytes()).hexdigest()
    assert filing_manifest_data["source_type"] == "opendartfilinglist"
    assert facts_manifest_data["source_type"] == "opendartfinancialfacts"