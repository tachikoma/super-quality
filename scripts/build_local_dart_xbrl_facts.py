#!/usr/bin/env python3
"""Build canonical FY2014 facts from one verified OpenDART XBRL ZIP."""

from __future__ import annotations

import argparse
from pathlib import Path

from k200_mq.data.dart_xbrl import parse_xbrl_facts, write_xbrl_artifact


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize one verified FY2014 OpenDART fnlttXbrl.xml ZIP.",
    )
    parser.add_argument("--raw-xbrl", required=True, help="Downloaded XBRL ZIP path")
    parser.add_argument("--acquisition-manifest", required=True, help="XBRL fetch sidecar manifest")
    parser.add_argument("--corp-code", required=True, help="Expected OpenDART corp_code")
    parser.add_argument("--rcept-no", default="", help="Expected receipt number (defaults to manifest)")
    parser.add_argument("--output-file", required=True, help="Canonical JSON fact artifact")
    parser.add_argument(
        "--manifest-file",
        default="",
        help="Derived artifact manifest (default: sibling .derived.manifest.json)",
    )
    args = parser.parse_args()
    output = Path(args.output_file)
    manifest = Path(args.manifest_file) if args.manifest_file else output.with_suffix(
        output.suffix + ".derived.manifest.json"
    )
    normalization = parse_xbrl_facts(
        args.raw_xbrl,
        args.acquisition_manifest,
        corp_code=args.corp_code,
        rcept_no=args.rcept_no or None,
    )
    write_xbrl_artifact(normalization, output, manifest)
    print(f"normalized {len(normalization.facts)} FY2014 XBRL facts -> {output}")
    print(f"derived manifest written -> {manifest}")


if __name__ == "__main__":
    main()
