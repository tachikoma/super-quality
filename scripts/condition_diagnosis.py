#!/usr/bin/env python3
"""A-H 조건별/조합별 수익률 진단 — 캐시만 사용, 샘플링"""

import logging
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from super_quality.config import SuperQualityConfig
from super_quality.data.cache import DataCache
from super_quality.data.loader import (
    get_price_data,
    get_market_index,
    get_krx_listings,
    date_to_financial_epoch,
    get_financial_snapshot,
    compute_ttm_snapshot,
)
from super_quality.factors.market_timing import KosdaqMAFactor
from super_quality.factors.quality import GPAFactor
from super_quality.factors.supply import RetailSupplyFactor
from super_quality.factors.value import PBRFactor
from super_quality.strategies.super_quality import SuperQualityStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
logger = logging.getLogger("condition_diagnosis")

config = SuperQualityConfig()
cache = DataCache()
START = "2015-01-01"
END = "2024-12-31"

# 1. Load cached financial data
logger.info("캐시된 재무 데이터 로드 중…")
fin_meta = cache.get_json("financial_meta") or {}
ticker_map = fin_meta.get("tickers", {})
cached_keys = sorted(ticker_map.keys(), key=lambda t: ticker_map[t][-1] if ticker_map[t] else 0)
logger.info("캐시된 재무 티커: %d", len(cached_keys))

fin_frames = []
for t in cached_keys:
    df = cache.get(f"financial_{t}")
    if df is not None and not df.empty:
        fin_frames.append(df)
financial_data = pd.concat(fin_frames, ignore_index=True) if fin_frames else pd.DataFrame()
logger.info("재무 데이터: %d행, %d 티커", len(financial_data), financial_data["ticker"].nunique() if not financial_data.empty else 0)

# 2. Load price data
logger.info("가격 데이터 로드 중…")
all_listings = get_krx_listings()
listings = all_listings[all_listings["market"].isin(["KOSPI", "KOSDAQ"])]
tickers = listings["ticker"].tolist()
price_data = get_price_data(tickers, START, END)
logger.info("가격 데이터: %d 티커", price_data.index.get_level_values("ticker").nunique())
all_dates = sorted(price_data.index.get_level_values("date").unique())
logger.info("거래일: %d", len(all_dates))

# 3. Market timing factor
logger.info("시장 타이밍 팩터 계산 중…")
idx_data = get_market_index(config.MARKET_TIMING_TICKER, START, END)
close_idx = idx_data["close"] if "close" in idx_data.columns else idx_data.iloc[:, 0]
ma = KosdaqMAFactor().compute(close_idx)
ma["date"] = pd.to_datetime(ma["date"])

# 4. Quality + Value factors
logger.info("GP/A + PBR 팩터 계산 중…")
gpa = GPAFactor()
pbr = PBRFactor()

# 5. 샘플링: 10 거래일마다 1회 분석
sample_step = 10
sample_dates = all_dates[::sample_step]
logger.info("샘플 날짜: %d (step=%d)", len(sample_dates), sample_step)

strategy = SuperQualityStrategy(config)
records = []

for i, d in enumerate(sample_dates):
    epoch = date_to_financial_epoch(d)
    snap = get_financial_snapshot(financial_data, epoch, cached_keys)
    if snap is None or snap.empty:
        continue

    snap = snap.reset_index()

    # current close (precompute for all dates to avoid xs per ticker)
    d_prices = price_data.xs(d, level="date")

    tickers_in_snap = [t for t in snap["ticker"] if t in d_prices.index and d_prices.loc[t, "close"] > 0]
    if not tickers_in_snap:
        continue

    df = snap[snap["ticker"].isin(tickers_in_snap)].copy()
    df = df.set_index("ticker")
    df["close"] = d_prices.loc[df.index, "close"]

    # Percentile ranks
    df["pbr_percentile"] = df["pbr"].rank(pct=True) * 100.0
    df["mcap"] = df["close"] * df["shares"]
    df["mcap_percentile"] = df["mcap"].rank(pct=True) * 100.0

    # GP/A
    df["gpa"] = gpa.compute(df.reset_index())
    df["gpa_percentile"] = df["gpa"].rank(pct=True) * 100.0

    # Market timing
    d_idx_ma = ma["date"].searchsorted(d, side="right") - 1
    bs = ma.iloc[d_idx_ma]["buy_signal"] if d_idx_ma >= 0 else False
    df["buy_signal"] = bs

    # Supply (skip — too slow)
    df["supply_percentile"] = 0.0

    # Conditions
    cond = strategy.evaluate_buy_conditions(df.reset_index())

    for _, row in cond.iterrows():
        ticker = row["ticker"]
        r = {"date": d, "ticker": ticker}
        for c in ["a_pass", "b_pass", "c_pass", "d_pass", "e_pass", "f_pass", "g_pass", "h_pass"]:
            r[c] = row[c]
        r["all_buy"] = row["all_buy_conditions"]
        if ticker in df.index:
            r["mcap"] = df.loc[ticker, "mcap"]

        # Forward returns
        d_idx = all_dates.index(d)
        for fwd, label in [(7, "ret_7d"), (14, "ret_14d")]:
            if d_idx + fwd < len(all_dates):
                fwd_d = all_dates[d_idx + fwd]
                try:
                    fwd_close = price_data.loc[(ticker, fwd_d), "close"]
                    r[label] = fwd_close / d_prices.loc[ticker, "close"] - 1.0
                except (KeyError, ValueError):
                    r[label] = None
            else:
                r[label] = None
        records.append(r)

    if (i + 1) % 50 == 0:
        count_all = sum(1 for r in records if r["all_buy"])
        logger.info("  %d/%d 날짜, %d records (all_buy=%d)", i + 1, len(sample_dates), len(records), count_all)

result = pd.DataFrame(records)
logger.info("\n총 레코드: %d", len(result))

# Analysis
checks = ["a_pass", "g_pass", "e_pass", "f_pass", "h_pass"]

logger.info("\n=== 조건별 통과율 및 7일 수익률 ===")
for c in checks:
    true_ret = result.loc[result[c], "ret_7d"].dropna()
    false_ret = result.loc[~result[c], "ret_7d"].dropna()
    if len(true_ret) > 0:
        logger.info("  %s= True: %.1f%% (n=%d, median %+.4f, mean %+.4f)",
                     c, result[c].mean()*100, len(true_ret), true_ret.median(), true_ret.mean())
    if len(false_ret) > 0:
        logger.info("  %s=False: %.1f%% (n=%d, median %+.4f, mean %+.4f)",
                     c, (1-result[c].mean())*100, len(false_ret), false_ret.median(), false_ret.mean())

import itertools
all_checks = ["a_pass", "b_pass", "c_pass", "d_pass", "e_pass", "f_pass", "g_pass", "h_pass"]
logger.info("\n=== 조건 조합별 7일 수익률 (Top 30, n>=100) ===")
combos = []
for r in range(1, len(all_checks) + 1):
    for combo in itertools.combinations(all_checks, r):
        mask = result[list(combo)].all(axis=1)
        ret = result.loc[mask, "ret_7d"].dropna()
        if len(ret) >= 100:
            combos.append(("+".join(c[0] for c in combo), len(ret), ret.median(), ret.mean()))
combos.sort(key=lambda x: x[3], reverse=True)
for label, n, med, mean in combos[:30]:
    logger.info("  %-40s n=%-6d median=%+.4f mean=%+.4f", label, n, med, mean)

logger.info("\n=== ALL 조건 통과 연도별 수익률 ===")
result["year"] = pd.to_datetime(result["date"]).dt.year
all_mask = result["all_buy"]
for yr in sorted(result["year"].unique()):
    yr_mask = all_mask & (result["year"] == yr)
    ret = result.loc[yr_mask, "ret_7d"].dropna()
    if len(ret) >= 5:
        logger.info("  %d: n=%d, median %+.4f, mean %+.4f", yr, len(ret), ret.median(), ret.mean())

logger.info("\n=== ALL 조건 MCAP 구간별 수익률 ===")
result["mcap_decile"] = pd.qcut(result["mcap"], 5, labels=False, duplicates="drop")
for dec in range(5):
    dec_mask = all_mask & (result["mcap_decile"] == dec)
    ret = result.loc[dec_mask, "ret_7d"].dropna()
    if len(ret) >= 5:
        lo = result.loc[dec_mask, "mcap"].min()
        hi = result.loc[dec_mask, "mcap"].max()
        logger.info("  Quintile %d (%.0e~%.0e): n=%d, median %+.4f, mean %+.4f",
                     dec, lo, hi, len(ret), ret.median(), ret.mean())
