#!/usr/bin/env python3
"""Generate batch specs for fetch_local_dart_response.py.

This helper builds repeatable JSON request specs for historical OpenDART
collection. The output format is directly consumable by
`scripts/fetch_local_dart_response.py --batch-file ...`.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_REPRT_CODES = ("11011",)


def _normal_label(value: str) -> str:
    return "".join(char for char in value.strip().casefold() if char.isalnum())


def _normalize_corp_code(value: str, *, source: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise RuntimeError(
            f"{source} is empty; provide an OpenDART corp_code containing 1-8 decimal digits"
        )
    if not stripped.isascii() or not stripped.isdigit():
        raise RuntimeError(
            f"{source}={value!r} is invalid; OpenDART corp_code must contain only decimal digits"
        )
    if len(stripped) > 8:
        raise RuntimeError(
            f"{source}={value!r} is invalid; OpenDART corp_code must be at most 8 digits"
        )
    return stripped.zfill(8)


def _load_tickers(path_text: str) -> list[str]:
    if not path_text.strip():
        return []
    path = Path(path_text)
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return sorted({value for value in values if value})


def _load_mapping_records(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise RuntimeError("corp-map-file JSON must be an array of objects")
        records: list[dict[str, str]] = []
        for row in value:
            if isinstance(row, dict):
                records.append({str(k): str(v) for k, v in row.items()})
        return records
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{str(k): str(v) for k, v in row.items()} for row in reader if isinstance(row, dict)]


def _map_tickers_to_corp_codes(tickers: list[str], map_file_text: str) -> list[str]:
    if not tickers:
        return []
    if not map_file_text.strip():
        raise RuntimeError("--corp-map-file is required when --tickers-file is used")
    path = Path(map_file_text)
    records = _load_mapping_records(path)
    if not records:
        raise RuntimeError("corp-map-file contains no usable rows")

    ticker_keys = {
        "stockcode", "stock_code", "stock", "ticker", "securitycode", "security_code",
    }
    corp_keys = {"corpcode", "corp_code"}
    resolved: dict[str, str] = {}

    for row in records:
        ticker_value = ""
        corp_value = ""
        for key, value in row.items():
            label = _normal_label(key)
            if label in {_normal_label(name) for name in ticker_keys} and value.strip():
                ticker_value = value.strip()
            if label in {_normal_label(name) for name in corp_keys} and value.strip():
                corp_value = value.strip()
        if ticker_value and corp_value:
            resolved[ticker_value] = _normalize_corp_code(
                corp_value,
                source=f"corp-map-file ticker {ticker_value!r}",
            )

    missing = [ticker for ticker in tickers if ticker not in resolved]
    if missing:
        raise RuntimeError(
            "corp-map-file is missing corp_code rows for tickers: " + ", ".join(missing[:20])
        )
    return sorted({resolved[ticker] for ticker in tickers})


def _load_corp_codes(codes_text: str, codes_file: str) -> list[str]:
    normalized: set[str] = set()
    if codes_text.strip():
        for value in codes_text.split(","):
            if value.strip():
                normalized.add(_normalize_corp_code(value, source="--corp-codes"))
    if codes_file.strip():
        path = Path(codes_file)
        for line_number, value in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if value.strip():
                normalized.add(
                    _normalize_corp_code(
                        value,
                        source=f"--corp-codes-file {path} line {line_number}",
                    )
                )
    return sorted(normalized)


def _load_reprt_codes(text: str) -> list[str]:
    if not text.strip():
        return list(DEFAULT_REPRT_CODES)
    codes = sorted({part.strip() for part in text.split(",") if part.strip()})
    if not codes:
        raise RuntimeError("reprt_code list cannot be empty")
    return codes


def _filing_specs(
    corp_codes: list[str],
    *,
    bgn_de: str,
    end_de: str,
) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for corp_code in corp_codes:
        specs.append({
            "kind": "filing",
            "request_params": [
                f"corp_code={corp_code}",
                f"bgn_de={bgn_de}",
                f"end_de={end_de}",
            ],
            "output_name": f"filing_{corp_code}_{bgn_de}_{end_de}.json",
            "manifest_name": f"filing_{corp_code}_{bgn_de}_{end_de}.manifest.json",
        })
    return specs


def _financial_specs(
    corp_codes: list[str],
    *,
    start_year: int,
    end_year: int,
    reprt_codes: list[str],
    fs_div: str,
) -> list[dict[str, object]]:
    if start_year < 2015:
        raise RuntimeError(
            "financial-start-year must be >= 2015: FY2014 is unavailable through "
            "the OpenDART fnlttSinglAcntAll endpoint and requires original filing/XBRL extraction"
        )
    specs: list[dict[str, object]] = []
    for corp_code in corp_codes:
        for year in range(start_year, end_year + 1):
            for reprt_code in reprt_codes:
                specs.append({
                    "kind": "financial",
                    "request_params": [
                        f"corp_code={corp_code}",
                        f"bsns_year={year}",
                        f"reprt_code={reprt_code}",
                        f"fs_div={fs_div}",
                    ],
                    "output_name": f"financial_{corp_code}_{year}_{reprt_code}.json",
                    "manifest_name": f"financial_{corp_code}_{year}_{reprt_code}.manifest.json",
                })
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a batch JSON spec for local OpenDART fetches.",
    )
    parser.add_argument(
        "--mode",
        choices=("filing", "financial", "both"),
        default="both",
        help="Which request specs to generate",
    )
    parser.add_argument(
        "--corp-codes",
        default="",
        help="Comma-separated corp_code list",
    )
    parser.add_argument(
        "--corp-codes-file",
        default="",
        help="Path to newline-separated corp_code file",
    )
    parser.add_argument(
        "--tickers-file",
        default="",
        help="Path to newline-separated ticker list used with --corp-map-file",
    )
    parser.add_argument(
        "--corp-map-file",
        default="",
        help="CSV/JSON mapping file containing ticker and corp_code columns",
    )
    parser.add_argument(
        "--filing-bgn-de",
        default="20150101",
        help="Filing request bgn_de (YYYYMMDD)",
    )
    parser.add_argument(
        "--filing-end-de",
        default="20241231",
        help="Filing request end_de (YYYYMMDD)",
    )
    parser.add_argument(
        "--financial-start-year",
        type=int,
        default=2015,
        help="Financial request start bsns_year",
    )
    parser.add_argument(
        "--financial-end-year",
        type=int,
        default=2024,
        help="Financial request end bsns_year",
    )
    parser.add_argument(
        "--reprt-codes",
        default=",".join(DEFAULT_REPRT_CODES),
        help="Comma-separated reprt_code list (e.g. 11011,11013)",
    )
    parser.add_argument(
        "--fs-div",
        default="CFS",
        help="Financial statement division (default: CFS)",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output JSON file path",
    )

    args = parser.parse_args()

    corp_codes = _load_corp_codes(args.corp_codes, args.corp_codes_file)
    ticker_codes = _load_tickers(args.tickers_file)
    mapped_codes = _map_tickers_to_corp_codes(ticker_codes, args.corp_map_file)
    corp_codes = sorted(set(corp_codes) | set(mapped_codes))
    if not corp_codes:
        raise RuntimeError("at least one corp_code is required")
    reprt_codes = _load_reprt_codes(args.reprt_codes)

    if args.financial_start_year > args.financial_end_year:
        raise RuntimeError("financial-start-year must be <= financial-end-year")

    specs: list[dict[str, object]] = []
    if args.mode in {"filing", "both"}:
        specs.extend(
            _filing_specs(
                corp_codes,
                bgn_de=args.filing_bgn_de,
                end_de=args.filing_end_de,
            )
        )
    if args.mode in {"financial", "both"}:
        specs.extend(
            _financial_specs(
                corp_codes,
                start_year=args.financial_start_year,
                end_year=args.financial_end_year,
                reprt_codes=reprt_codes,
                fs_div=args.fs_div,
            )
        )

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"generated {len(specs)} request specs -> {output_path}")


if __name__ == "__main__":
    main()
