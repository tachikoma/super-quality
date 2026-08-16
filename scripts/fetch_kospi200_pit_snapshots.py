#!/usr/bin/env python3
"""Fetch historical KOSPI 200 constituents via pykrx into per-date parquet files.

Uses pykrx `get_index_portfolio_deposit_file` (KRX official source, historical
date support from 2014-05-02) to fetch the actual KOSPI 200 membership at each
of the 120 rebalance as-of dates (2015-01-30 .. 2024-12-31).

Writes `data/universe/kospi200_bundle_pit_src/kospi200_YYYY-MM-DD.parquet`
(ticker, as_of) so the existing `scripts/build_local_pit_universe_bundle.py`
can compile the PIT bundle. KRX_ID/KRX_PW are read from the environment.

Usage:
    set -a; source .env; set +a
    uv run python scripts/fetch_kospi200_pit_snapshots.py
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock

OUTPUT_DIR = Path("data/universe/kospi200_bundle_pit_src")
DATE_LIST = Path("/var/folders/q0/lsmgmb2j143990vrm9wszm3c0000gn/T/opencode/kospi200_dates.txt")


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year, 12, 31)
    return date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)


def _as_of_dates() -> list[date]:
    if DATE_LIST.is_file():
        raw = [line.strip() for line in DATE_LIST.read_text().splitlines() if line.strip()]
        if raw:
            return sorted({date.fromisoformat(value) for value in raw})
    # Fallback: month-end dates 2015-01-31 .. 2024-12-31 (pykrx alternative=True).
    dates: list[date] = []
    current = date(2015, 1, 1)
    while current <= date(2024, 12, 31):
        dates.append(_month_end(current))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return dates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--delay-seconds", type=float, default=1.0,
                        help="Delay between KRX calls to respect rate limits")
    args = parser.parse_args()

    if not os.environ.get("KRX_ID") or not os.environ.get("KRX_PW"):
        raise SystemExit("KRX_ID/KRX_PW environment variables are required")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    dates = _as_of_dates()
    print(f"fetching {len(dates)} as-of dates -> {output_dir}")
    fetched = 0
    skipped = 0
    for as_of in dates:
        target = output_dir / f"kospi200_{as_of.isoformat()}.parquet"
        if target.is_file():
            skipped += 1
            continue
        tickers = stock.get_index_portfolio_deposit_file(
            ticker="1028",  # KOSPI 200
            date=as_of.strftime("%Y%m%d"),
            alternative=True,
        )
        if not tickers:
            print(f"WARNING: empty constituent list for {as_of}")
            continue
        tickers = sorted({str(ticker).zfill(6) for ticker in tickers})
        frame = pd.DataFrame({"ticker": tickers, "as_of": [as_of.isoformat()] * len(tickers)})
        frame.to_parquet(target, index=False)
        fetched += 1
        print(f"{as_of}: {len(tickers)} tickers -> {target.name}")
        if args.delay_seconds > 0:
            import time

            time.sleep(args.delay_seconds)

    print(f"done: fetched={fetched}, already present={skipped}")
    # Write a fetch log for provenance.
    log = {
        "source": "pykrx stock.get_index_portfolio_deposit_file (KRX official)",
        "index": "KOSPI 200 (1028)",
        "as_of_count": len(dates),
        "fetched": fetched,
        "skipped_existing": skipped,
        "output_dir": str(output_dir),
        "retrieved_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    (output_dir / "fetch_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
