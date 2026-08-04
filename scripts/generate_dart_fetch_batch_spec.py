#!/usr/bin/env python3
"""Generate batch specs for fetch_local_dart_response.py.

This helper builds repeatable JSON request specs for historical OpenDART
collection. The output format is directly consumable by
`scripts/fetch_local_dart_response.py --batch-file ...`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_REPRT_CODES = ("11011",)


def _load_corp_codes(codes_text: str, codes_file: str) -> list[str]:
    values: list[str] = []
    if codes_text.strip():
        values.extend(part.strip() for part in codes_text.split(","))
    if codes_file.strip():
        path = Path(codes_file)
        values.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    normalized = sorted({value for value in values if value})
    if not normalized:
        raise RuntimeError("at least one corp_code is required")
    return normalized


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