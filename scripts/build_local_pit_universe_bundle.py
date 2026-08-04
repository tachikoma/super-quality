#!/usr/bin/env python3
"""Build a directory bundle of per-date local PIT snapshot files.

The bundle format is one data file per as-of date plus a sibling manifest for
that file. The directory importer can then preserve per-date acquisition
identity instead of collapsing many dates into one snapshot token.

This utility is a format/packaging helper only. It does not certify that the
source evidence is historically PIT-valid.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import shutil
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class BundleMember:
    path: Path
    as_of: str
    tickers: list[str]
    sha256: str
    snapshot_identity_sha256: str


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


def _identity_fingerprint(tickers: Iterable[str]) -> str:
    payload = json.dumps(list(tickers), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_glob_members(files: Iterable[Path]) -> list[BundleMember]:
    members: list[BundleMember] = []
    for path in sorted(files):
        raw = path.read_bytes()
        frame = _read_frame(path)
        as_of = _as_of_from_frame_or_name(frame, path)
        tickers = _tickers_from_frame(frame, path)
        members.append(
            BundleMember(
                path=path,
                as_of=as_of,
                tickers=tickers,
                sha256=_sha256_bytes(raw),
                snapshot_identity_sha256=_identity_fingerprint(tickers),
            )
        )
    if not members:
        raise ValueError("no source files were found")
    return members


def _write_manifest(
    manifest_path: Path,
    *,
    source_url: str,
    source_is_krx: bool,
    source_type: str,
    retrieved_at_utc: str,
    input_glob: str,
    input_source_path: Path,
    input_source_sha256: str,
    output_dir: Path,
    members: list[BundleMember],
) -> None:
    manifests_by_as_of: dict[str, dict[str, object]] = {}
    transition_exceptions_by_as_of: dict[str, dict[str, object]] = {}
    for member in members:
        manifests_by_as_of[member.as_of] = {
            "official_source_url": source_url,
            "query_params": {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
                "indIdx": "1",
                "indIdx2": "028",
            },
            "date_params": {"trdDd": member.as_of},
            "retrieved_at_utc": retrieved_at_utc,
            "raw_file_sha256": member.sha256,
            "source_type": source_type,
            "source_is_krx": bool(source_is_krx),
            "snapshot_identity_sha256": member.snapshot_identity_sha256,
        }
        if len(member.tickers) != 200:
            transition_exceptions_by_as_of[member.as_of] = {
                "allowed_sizes": [len(member.tickers)],
                "reason": f"documented historical transition size {len(member.tickers)}",
            }
    payload = {
        "official_source_url": source_url,
        "query_params": {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
            "indIdx": "1",
            "indIdx2": "028",
        },
        "date_params": {
            "start": min(member.as_of for member in members),
            "end": max(member.as_of for member in members),
        },
        "retrieved_at_utc": retrieved_at_utc,
        "source_type": source_type,
        "source_is_krx": bool(source_is_krx),
        "output_dir": str(output_dir),
        "input_glob": input_glob,
        "input_source_path": str(input_source_path),
        "input_source_sha256": input_source_sha256,
        "bundle_csv_sha256": None,
        "transition_exceptions_by_as_of": transition_exceptions_by_as_of,
        "manifests_by_as_of": manifests_by_as_of,
        "note": "bundle directory with per-date acquisition manifests",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy per-date universe files into a bundle directory with sidecar manifests.",
    )
    parser.add_argument(
        "--input-glob",
        default="data/universe/kospi200_*.parquet",
        help="Glob for per-date source files (default: data/universe/kospi200_*.parquet)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/universe/kospi200_bundle",
        help="Output directory for copied files and manifests",
    )
    parser.add_argument(
        "--bundle-manifest",
        default="data/universe/kospi200_bundle/bundle.manifest.json",
        help="Bundle index manifest path",
    )
    parser.add_argument(
        "--source-url",
        default="https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        help="Official KRX source URL",
    )
    parser.add_argument(
        "--source-type",
        default="krx_official_snapshot",
        choices=["krx_official_snapshot", "krx_official_event"],
        help="Source type written to each manifest",
    )
    parser.add_argument(
        "--source-is-krx",
        action="store_true",
        help="Mark manifests as explicitly attested KRX sources",
    )
    parser.add_argument(
        "--retrieved-at-utc",
        default="",
        help="UTC timestamp (ISO-8601). Default: now in UTC",
    )

    args = parser.parse_args()

    files = [Path(path) for path in sorted(Path().glob(args.input_glob))]
    members = _source_glob_members(files)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.iterdir():
        if existing.is_file():
            existing.unlink()
    retrieved_at_utc = args.retrieved_at_utc.strip() or datetime.now(timezone.utc).isoformat()

    for member in members:
        frame = _read_frame(member.path)
        canonical = pd.DataFrame({
            "index_code": ["KOSPI200"] * len(member.tickers),
            "as_of_date": [member.as_of] * len(member.tickers),
            "security_code": member.tickers,
            "source_type": [args.source_type] * len(member.tickers),
            "source_url": [args.source_url] * len(member.tickers),
            "retrieved_at_utc": [retrieved_at_utc] * len(member.tickers),
        })
        copied_path = output_dir / f"{member.path.stem}.csv"
        canonical.to_csv(copied_path, index=False)
        manifest_path = copied_path.with_suffix(".manifest.json")
        manifest = {
            "official_source_url": args.source_url,
            "query_params": {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
                "indIdx": "1",
                "indIdx2": "028",
            },
            "date_params": {"trdDd": member.as_of},
            "retrieved_at_utc": retrieved_at_utc,
            "raw_file_sha256": _sha256_bytes(copied_path.read_bytes()),
            "source_type": args.source_type,
            "source_is_krx": bool(args.source_is_krx),
            "snapshot_identity_sha256": member.snapshot_identity_sha256,
            "input_source_path": str(member.path),
            "input_source_sha256": member.sha256,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")

    bundle_manifest_path = Path(args.bundle_manifest)
    _write_manifest(
        bundle_manifest_path,
        source_url=args.source_url,
        source_is_krx=bool(args.source_is_krx),
        source_type=args.source_type,
        retrieved_at_utc=retrieved_at_utc,
        input_glob=args.input_glob,
        input_source_path=files[0],
        input_source_sha256=_sha256_bytes(files[0].read_bytes()),
        output_dir=output_dir,
        members=members,
    )

    print(f"bundled {len(members)} files -> {output_dir}")
    print(f"bundle manifest written -> {bundle_manifest_path}")
    print("warning: this utility does not certify PIT validity of the source evidence")


if __name__ == "__main__":
    main()
