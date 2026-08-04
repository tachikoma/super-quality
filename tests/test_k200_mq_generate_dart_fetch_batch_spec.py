from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_script_module(path: Path):
    spec = importlib.util.spec_from_file_location("generate_dart_fetch_batch_spec", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generate_both_mode_specs_from_inline_corp_codes(tmp_path: Path) -> None:
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/generate_dart_fetch_batch_spec.py"))
    output = tmp_path / "batch.json"

    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "generate_dart_fetch_batch_spec.py",
            "--mode", "both",
            "--corp-codes", "001,002",
            "--financial-start-year", "2023",
            "--financial-end-year", "2023",
            "--reprt-codes", "11011,11013",
            "--output-file", str(output),
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    specs = json.loads(output.read_text(encoding="utf-8"))
    # filing: 2, financial: 2 corp * 1 year * 2 reprt = 4
    assert len(specs) == 6
    assert specs[0]["kind"] == "filing"
    assert any(item["kind"] == "financial" for item in specs)


def test_generate_filing_specs_from_corp_code_file(tmp_path: Path) -> None:
    script = _load_script_module(Path("/Users/durkjaeyun/Documents/DjY/projects/investment/super-quality/scripts/generate_dart_fetch_batch_spec.py"))
    corp_file = tmp_path / "corp_codes.txt"
    corp_file.write_text("12345678\n87654321\n", encoding="utf-8")
    output = tmp_path / "filing_batch.json"

    argv_backup = sys.argv[:]
    try:
        sys.argv = [
            "generate_dart_fetch_batch_spec.py",
            "--mode", "filing",
            "--corp-codes-file", str(corp_file),
            "--filing-bgn-de", "20190101",
            "--filing-end-de", "20201231",
            "--output-file", str(output),
        ]
        script.main()
    finally:
        sys.argv = argv_backup

    specs = json.loads(output.read_text(encoding="utf-8"))
    assert len(specs) == 2
    assert all(item["kind"] == "filing" for item in specs)
    assert all("bgn_de=20190101" in item["request_params"] for item in specs)
    assert all("end_de=20201231" in item["request_params"] for item in specs)