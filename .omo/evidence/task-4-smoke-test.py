#!/usr/bin/env python3
"""Smoke test for yearly incremental price cache.

Phases:
  A: Basic incremental (forward extension)
  B: Partial-year backward extension (Momus blocker scenario)
"""

import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

# Configure logging to see cache messages
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)

from super_quality.data.loader import get_price_data

CACHE_DIR = Path("data/raw")
TICKERS = ["005930", "000660", "207940"]  # 삼성전자, SK하이닉스, 삼성바이오로직스
KOSDAQ_TICKERS = ["088390", "068760"]  # 코스닥 대표주

pass_count = 0
fail_count = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global pass_count, fail_count
    if condition:
        print(f"  ✅ {label}")
        pass_count += 1
    else:
        print(f"  ❌ {label} {detail}")
        fail_count += 1


def check_json_meta(expected_years: dict) -> None:
    meta_path = CACHE_DIR / "price_meta.json"
    check(f"price_meta.json exists", meta_path.exists())
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        check(f"cache_version == 1", meta.get("cache_version") == 1)
        for y_str, expected in expected_years.items():
            ydata = meta.get("years", {}).get(y_str, {})
            check(
                f"year {y_str} req_start == {expected['req_start']}",
                ydata.get("req_start") == expected["req_start"],
            )
            check(
                f"year {y_str} req_end == {expected['req_end']}",
                ydata.get("req_end") == expected["req_end"],
            )


def check_parquet_exists(year: str) -> bool:
    p = CACHE_DIR / f"price_{year}.parquet"
    exists = p.exists()
    check(f"price_{year}.parquet exists", exists)
    return exists


def clean_cache() -> None:
    print("\n--- Cleaning cache ---")
    for f in CACHE_DIR.glob("price_*.parquet"):
        f.unlink()
    for f in CACHE_DIR.glob("price_meta.json"):
        f.unlink()
    # Keep old prices_* files (no migration)
    print("  Cache cleaned")


print("=" * 60)
print("  PHASE A: Basic Incremental (Forward Extension)")
print("=" * 60)

# A1: Fresh start
clean_cache()

# A2: Short range (H1 2015 only)
print("\n--- A2: First run 2015-01-01 ~ 2015-06-30 ---")
df1 = get_price_data(KOSDAQ_TICKERS, "2015-01-01", "2015-06-30")
print(f"  Result shape: {df1.shape}")
check_parquet_exists("2015")
check_json_meta({"2015": {"req_start": "2015-01-01", "req_end": "2015-06-30"}})
check("Result is MultiIndex (ticker, date)", isinstance(df1.index, pd.MultiIndex))
check("Result has expected columns", set(df1.columns) == {"open", "high", "low", "close", "volume", "mcap"})

# A3: Re-run identical dates — should hit cache
print("\n--- A3: Re-run identical dates ---")
df1b = get_price_data(KOSDAQ_TICKERS, "2015-01-01", "2015-06-30")
check("Re-run produces same data", df1.equals(df1b))

# A4: Extend forward to 2016
print("\n--- A4: Extend forward to 2016-06-30 ---")
df2 = get_price_data(KOSDAQ_TICKERS, "2015-01-01", "2016-06-30")
print(f"  Result shape: {df2.shape}")
check_parquet_exists("2015")
check_parquet_exists("2016")
# 2015 was partially cached (H1 only), so it gets reloaded to full year
# req_end becomes 2015-12-31 after reload
check_json_meta({
    "2015": {"req_start": "2015-01-01", "req_end": "2015-12-31"},
    "2016": {"req_start": "2016-01-01", "req_end": "2016-06-30"},
})
# Use dates that exist in trading calendar (2015-01-01 is a holiday)
s5 = pd.Timestamp("2015-01-02")
e5 = pd.Timestamp("2015-06-30")
s6 = pd.Timestamp("2016-01-04")
e6 = pd.Timestamp("2016-06-30")
check("2015 H1 data still present", df2.loc["088390"].loc[s5:e5].shape[0] > 0)
check("2016 data present", df2.loc["088390"].loc[s6:e6].shape[0] > 0)

print("\n" + "=" * 60)
print("  PHASE B: Partial-Year Backward Extension (Momus Blocker)")
print("=" * 60)

# B1: Fresh start
clean_cache()

# B2: Download only H2 2015 (July~Dec)
print("\n--- B2: First run 2015-07-01 ~ 2015-12-31 ---")
df_b1 = get_price_data(KOSDAQ_TICKERS, "2015-07-01", "2015-12-31")
print(f"  Result shape: {df_b1.shape}")
check_parquet_exists("2015")
check_json_meta({"2015": {"req_start": "2015-07-01", "req_end": "2015-12-31"}})

# Verify meta shows req_start == 2015-07-01 (only H2)
meta_path = CACHE_DIR / "price_meta.json"
with open(meta_path) as f:
    meta_b1 = json.load(f)
check(
    "Meta req_start == 2015-07-01 (partial year)",
    meta_b1["years"]["2015"]["req_start"] == "2015-07-01",
)

# B3: Extend backward to Jan 2015
print("\n--- B3: Extend backward to 2015-01-01 ~ 2015-12-31 ---")
df_b2 = get_price_data(KOSDAQ_TICKERS, "2015-01-01", "2015-12-31")
print(f"  Result shape: {df_b2.shape}")

# After backward extension, meta should show full year
check_json_meta({"2015": {"req_start": "2015-01-01", "req_end": "2015-12-31"}})

# Verify full year data (2015-01-01 is a holiday, data starts 2015-01-02)
jan_start = pd.Timestamp("2015-01-02")
jan_end = pd.Timestamp("2015-01-31")
dec_start = pd.Timestamp("2015-12-01")
dec_end = pd.Timestamp("2015-12-30")
jan_data = df_b2.loc["088390"].loc[jan_start:jan_end]
check("Has January data (backward extension worked)", len(jan_data) > 0)

dec_data = df_b2.loc["088390"].loc[dec_start:dec_end]
check("Has December data (original H2 preserved)", len(dec_data) > 0)

# B4: Re-run same dates — cache hit
print("\n--- B4: Re-run same dates (cache hit check) ---")
df_b3 = get_price_data(KOSDAQ_TICKERS, "2015-01-01", "2015-12-31")
check("Final re-run produces same data", df_b2.equals(df_b3))

# B5: Delete meta.json → fresh re-download
print("\n--- B5: Delete meta.json → fresh re-download ---")
if meta_path.exists():
    meta_path.unlink()
df_b4 = get_price_data(KOSDAQ_TICKERS, "2015-01-01", "2015-06-30")
print(f"  Result shape after re-download: {df_b4.shape}")
check_parquet_exists("2015")
check_json_meta({"2015": {"req_start": "2015-01-01", "req_end": "2015-06-30"}})


print("\n" + "=" * 60)
print("  SUMMARY")
print("=" * 60)
print(f"  Passed: {pass_count}")
print(f"  Failed: {fail_count}")

if fail_count > 0:
    print("\n  ❌ SOME TESTS FAILED")
    sys.exit(1)
else:
    print("\n  ✅ ALL SMOKE TESTS PASSED")
