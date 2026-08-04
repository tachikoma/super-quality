#!/usr/bin/env python3
"""Build one local PIT snapshot file from monthly universe files.

This utility compiles per-date universe files (for example parquet snapshots)
into a single canonical snapshot CSV that the local PIT importer accepts.
It does not assert that the source itself is PIT-valid evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

_DATE_RE = re.compile(r"(\\d{4}-\\d{2}-\\d{2})")


@dataclass(frozen=True)
class SnapshotSource:
    path: Path
    as_of: str
    tickers: list[str]
    sha256: str


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported file format: {path}")


def _as_of_from_frame_or_name(frame: pd.DataFrame, path: Path) -> str:
    for column in ("as_of_date", "as_of", "date"):
        if column in frame.columns:
            values = pd.to_datetime(frame[column], errors="coerce").dropna().dt.date.unique().tolist()
            if len(values) == 1:
                return values[0].isoformat()
            if len(values) > 1:
                raise ValueError(f"{path} has multiple as-of dates in column {column}")
    match = _DATE_RE.search(path.name)
    if not match:
        raise ValueError(f"could not infer as-of date from columns or filename: {path}")
    return match.group(1)


def _tickers_from_frame(frame: pd.DataFrame, path: Path) -> list[str]:
    column = None
    for candidate in ("security_code", "ticker"):
        if candidate in frame.columns:
            column = candidate
            break
    if column is None:
        raise ValueError(f"missing ticker/security_code column: {path}")
    tickers = sorted({str(value).strip() for value in frame[column].tolist() if str(value).strip()})
    tickers = [ticker for ticker in tickers if re.fullmatch(r"\d{6}", ticker)]
    if not tickers:
        raise ValueError(f"no tickers found in {path}")
    return tickers


def _collect_sources(files: Iterable[Path]) -> list[SnapshotSource]:
    sources: list[SnapshotSource] = []
    for path in sorted(files):
        raw = path.read_bytes()
        frame = _read_frame(path)
        as_of = _as_of_from_frame_or_name(frame, path)
        tickers = _tickers_from_frame(frame, path)
        sources.append(
            SnapshotSource(path=path, as_of=as_of, tickers=tickers, sha256=_sha256_bytes(raw))
        )
    if not sources:
        raise ValueError("no source files were found")
    return sources


def _build_snapshot_frame(
    sources: list[SnapshotSource],
    *,
    index_code: str,
    source_type: str,
    source_url: str,
    retrieved_at_utc: str,
) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for item in sources:
        for ticker in item.tickers:
            rows.append(
                {
                    "index_code": index_code,
                    "as_of_date": item.as_of,
                    "security_code": ticker,
                    "source_type": source_type,
                    "source_url": source_url,
                    "source_file_sha256": item.sha256,
                    "retrieved_at_utc": retrieved_at_utc,
                }
            )
    frame = pd.DataFrame.from_records(rows)
    frame = frame.sort_values(["as_of_date", "security_code"], kind="mergesort").reset_index(drop=True)
    return frame


def _write_manifest(
    manifest_path: Path,
    *,
    output_file: Path,
    output_sha256: str,
    retrieved_at_utc: str,
    source_type: str,
    source_url: str,
    source_is_krx: bool,
    input_glob: str,
    start_date: str,
    end_date: str,
) -> None:
    payload = {
        "official_source_url": source_url,
        "query_params": {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
            "indIdx": "1",
            "indIdx2": "028",
        },
        "date_params": {"start": start_date, "end": end_date},
        "retrieved_at_utc": retrieved_at_utc,
        "raw_file_sha256": output_sha256,
        "source_type": source_type,
        "source_is_krx": bool(source_is_krx),
        "output_file": str(output_file),
        "input_glob": input_glob,
        "note": "compiled local snapshot; provenance must be audited separately",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile monthly universe files into one canonical local PIT snapshot CSV.",
    )
    parser.add_argument(
        "--input-glob",
        default="data/universe/kospi200_*.parquet",
        help="Glob for per-date source files (default: data/universe/kospi200_*.parquet)",
    )
    parser.add_argument(
        "--output-file",
        default="data/universe/local_pit_snapshots.csv",
        help="Output canonical snapshot CSV path",
    )
    parser.add_argument(
        "--manifest-file",
        default="data/universe/local_pit_snapshots.manifest.json",
        help="Output acquisition manifest path",
    )
    parser.add_argument(
        "--index-code",
        default="KOSPI200",
        help="Index code to write into snapshot rows",
    )
    parser.add_argument(
        "--source-type",
        default="krx_official_snapshot",
        choices=["krx_official_snapshot", "krx_official_event"],
        help="source_type value for snapshot rows and manifest",
    )
    parser.add_argument(
        "--source-url",
        default="https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        help="source_url value for snapshot rows and manifest",
    )
    parser.add_argument(
        "--retrieved-at-utc",
        default="",
        help="UTC timestamp (ISO-8601). Default: now in UTC",
    )
    parser.add_argument(
        "--source-is-krx",
        action="store_true",
        help="Set source_is_krx=true in manifest (required for strict KRX attestation gate)",
    )

    args = parser.parse_args()

    files = [Path(path) for path in sorted(Path().glob(args.input_glob))]
    sources = _collect_sources(files)

    retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()
    frame = _build_snapshot_frame(
        sources,
        index_code=args.index_code,
        source_type=args.source_type,
        source_url=args.source_url,
        retrieved_at_utc=retrieved_at_utc,
    )

    output_file = Path(args.output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_file, index=False)
    output_sha256 = _sha256_bytes(output_file.read_bytes())

    _write_manifest(
        Path(args.manifest_file),
        output_file=output_file,
        output_sha256=output_sha256,
        retrieved_at_utc=retrieved_at_utc,
        source_type=args.source_type,
        source_url=args.source_url,
        source_is_krx=bool(args.source_is_krx),
        input_glob=args.input_glob,
        start_date=min(item.as_of for item in sources),
        end_date=max(item.as_of for item in sources),
    )

    print(f"compiled {len(sources)} files -> {output_file} ({len(frame)} rows)")
    print(f"manifest written -> {args.manifest_file}")
    print("warning: this utility does not certify PIT validity of the source evidence")


if __name__ == "__main__":
    main()
