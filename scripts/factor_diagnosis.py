#!/usr/bin/env python3
"""단일 팩터 진단 — 캐시된 데이터만 사용 (DART API 호출 없음)"""

import logging
import sys
from datetime import date
from typing import Any

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
from super_quality.backtest.engine import BacktestEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("factor_diagnosis")

config = SuperQualityConfig()
cache = DataCache()

TEST_START = "2023-01-01"
TEST_END = "2024-12-31"

logger.info("=" * 60)
logger.info("단일 팩터 진단 (캐시 전용)")
logger.info("기간: %s ~ %s", TEST_START, TEST_END)
logger.info("=" * 60)

# ── 1. 데이터 로드 (캐시 전용) ─────────────────────────────
logger.info("데이터 로드 중…")

all_listings = get_krx_listings()
listings = all_listings[all_listings["market"].isin(["KOSPI", "KOSDAQ"])].copy()
if "Marcap" in listings.columns:
    listings = listings.sort_values("Marcap", ascending=False)
tickers: list[str] = listings["ticker"].tolist()
fin_tickers: list[str] = tickers[400:600]

price_data = get_price_data(tickers, TEST_START, TEST_END)
index_data = get_market_index(config.MARKET_TIMING_TICKER, TEST_START, TEST_END)

# 캐시된 financial data 직접 로드
fin_frames = []
for t in fin_tickers:
    df = cache.get(f"financial_{t}")
    if df is not None and not df.empty:
        fin_frames.append(df)
financial_data = pd.concat(fin_frames, ignore_index=True) if fin_frames else pd.DataFrame()
logger.info("Financial data: %d행, %d tickers", len(financial_data), financial_data["ticker"].nunique())

# 캐시된 retail buy 데이터 로드
retail_frames = []
for t in fin_tickers:
    df = cache.get(f"retail_{t}_{TEST_START}_{TEST_END}")
    if df is not None and not df.empty:
        if isinstance(df, pd.DataFrame):
            df["ticker"] = t
            retail_frames.append(df)
# retail buy is also stored under different cache key format: check
# Alternative: iterate date-by-date or use key pattern
retail_buy = pd.concat(retail_frames, ignore_index=False) if retail_frames else pd.DataFrame()
logger.info("Retail data: %d행", len(retail_buy))

# 캐시된 유상증자 데이터 로드
capital_increases: dict[str, list[date]] = {}
meta = cache.get_json("financial_meta") or {}
cached_tickers = meta.get("tickers", {})
for t in fin_tickers:
    key = f"paid_capital_{t}"
    data = cache.get_json(key)
    if data and isinstance(data, list):
        capital_increases[t] = [pd.Timestamp(d).date() if isinstance(d, str) else d for d in data]
logger.info("유상증자 티커: %d", sum(1 for v in capital_increases.values() if v))

# ── 2. 팩터 계산 ────────────────────────────────────────
logger.info("팩터 계산 중…")

factor_data = price_data.reset_index()[["ticker", "date"]].copy()
factor_data["date"] = pd.to_datetime(factor_data["date"])

all_epoch_dates = sorted(factor_data["date"].unique())
date_epoch_map: dict = {}
for d in all_epoch_dates:
    d_date = d.date() if hasattr(d, "date") else pd.Timestamp(d).date()
    ey, eq = date_to_financial_epoch(d_date)
    date_epoch_map[d] = ey * 10 + eq
factor_data["_epoch"] = factor_data["date"].map(date_epoch_map)

unique_epochs = sorted(factor_data["_epoch"].unique())
epoch_factor_frames: list[pd.DataFrame] = []

for epoch_key in unique_epochs:
    epoch_dates = factor_data.loc[factor_data["_epoch"] == epoch_key, "date"]
    as_of_ts = epoch_dates.min()
    as_of_date = as_of_ts.date() if hasattr(as_of_ts, "date") else pd.Timestamp(as_of_ts).date()

    fin_snap = get_financial_snapshot(financial_data, as_of_date)
    if fin_snap.empty:
        continue

    try:
        mcap_at_epoch = (
            price_data.xs(as_of_ts, level="date")["mcap"]
            .reset_index()
            .rename(columns={"mcap": "mcap_epoch"})
        )
        fin_snap = fin_snap.merge(mcap_at_epoch, on="ticker", how="left")
    except KeyError:
        continue

    pbr_df = PBRFactor().compute(fin_snap.rename(columns={"mcap_epoch": "mcap"}))
    gpa_df = GPAFactor().compute(fin_snap)
    ttm_df = compute_ttm_snapshot(financial_data, as_of_date)

    epoch_df = pbr_df.merge(gpa_df, on="ticker", how="left")
    if not ttm_df.empty:
        epoch_df = epoch_df.merge(ttm_df, on="ticker", how="left")
    else:
        epoch_df["trailing_ni"] = 0.0
        epoch_df["trailing_ocf"] = 0.0

    epoch_df["_epoch"] = epoch_key
    epoch_factor_frames.append(epoch_df)

if epoch_factor_frames:
    epoch_factors_all = pd.concat(epoch_factor_frames, ignore_index=True)
    factor_data = factor_data.merge(epoch_factors_all, on=["ticker", "_epoch"], how="left")
    factor_data = factor_data.sort_values(["ticker", "date"])
    for ck in ["pbr_percentile", "gpa_percentile", "trailing_ni", "trailing_ocf"]:
        if ck in factor_data.columns:
            factor_data[ck] = factor_data.groupby("ticker")[ck].ffill()

daily_mcap = price_data["mcap"].reset_index()
daily_mcap["date"] = pd.to_datetime(daily_mcap["date"])
factor_data = factor_data.merge(daily_mcap[["ticker", "date", "mcap"]], on=["ticker", "date"], how="left")
factor_data["mcap_percentile"] = (
    factor_data.groupby("date")["mcap"].rank(pct=True, ascending=True).fillna(50.0) * 100.0
)
fin_ticker_set = set(factor_data.loc[factor_data["pbr_percentile"].notna(), "ticker"])
if fin_ticker_set:
    fin_mask = factor_data["ticker"].isin(fin_ticker_set)
    factor_data.loc[fin_mask, "mcap_percentile"] = (
        factor_data.loc[fin_mask].groupby("date")["mcap"].rank(pct=True, ascending=True).fillna(50.0) * 100.0
    )

if not retail_buy.empty:
    supply_df = RetailSupplyFactor(supply_days=config.SUPPLY_SCORE_DAYS).compute(retail_buy)
else:
    supply_df = pd.DataFrame(columns=["ticker", "supply_score", "supply_percentile"])

if not supply_df.empty:
    supply_merge = supply_df.rename(columns={"supply_score": "supply_score", "supply_percentile": "supply_percentile"})
    supply_merge["ticker"] = supply_merge["ticker"].astype(str)
    factor_data["ticker"] = factor_data["ticker"].astype(str)
    factor_data = factor_data.merge(supply_merge[["ticker", "supply_score", "supply_percentile"]], on="ticker", how="left")
else:
    factor_data["supply_score"] = 0.0
    factor_data["supply_percentile"] = 50.0

market_ma = KosdaqMAFactor().compute(index_data["close"])
market_signals = market_ma[["date", "buy_signal", "sell_signal"]].copy()

for col, default in [
    ("pbr_percentile", 50.0),
    ("gpa_percentile", 50.0),
    ("supply_percentile", 50.0),
    ("pbr", 1.0),
]:
    factor_data[col] = factor_data[col].fillna(default) if col in factor_data.columns else default

factor_data["share_change_now"] = 0
factor_data["share_change_5mo_ago"] = 0
for tkr, dts in capital_increases.items():
    if not dts:
        continue
    mask = factor_data["ticker"] == tkr
    if not mask.any():
        continue
    dates = factor_data.loc[mask, "date"]
    dt_series = pd.to_datetime(dts)
    now_mask = pd.Series(False, index=dates.index)
    ago_mask = pd.Series(False, index=dates.index)
    for dt in dt_series:
        diff = (dates - dt).dt.days
        now_mask |= (diff >= 0) & (diff <= 90)
        ago_mask |= (diff >= 120) & (diff <= 180)
    factor_data.loc[dates[now_mask].index, "share_change_now"] = 1
    factor_data.loc[dates[ago_mask].index, "share_change_5mo_ago"] = 1

for col in ["trailing_ni", "trailing_ocf"]:
    if col in factor_data.columns:
        factor_data[col] = factor_data[col].fillna(0.0)
    else:
        factor_data[col] = 0.0

factor_data = factor_data.drop(columns=["_epoch", "mcap"], errors="ignore")

signal_merge = market_signals.copy()
signal_merge["date"] = pd.to_datetime(signal_merge["date"])
factor_data = factor_data.merge(signal_merge[["date", "buy_signal", "sell_signal"]], on="date", how="left")
factor_data["buy_signal"] = factor_data["buy_signal"].fillna(False)
factor_data["sell_signal"] = factor_data["sell_signal"].fillna(False)

logger.info("팩터 데이터 생성 완료: %d행 × %d열", len(factor_data), len(factor_data.columns))

# ── 3. 테스트 케이스 ─────────────────────────────────────
test_cases: list[tuple[str, set[str] | None, str]] = [
    ("ALL", None, "모든 조건 활성 (기준)"),
    ("G", {"g"}, "Size: MCAP ≤ 40%ile (소형주)"),
    ("A", {"a"}, "Value: PBR ≤ 20%ile (저PBR)"),
    ("E+F", {"e", "f"}, "Quality: NI>0 AND OCF>0"),
    ("H", {"h"}, "Timing: buy_signal=True (KOSDAQ>MA)"),
    ("NONE", set(), "조건 없음 (always buy)"),
]

def run_single_test(
    label: str,
    active_conditions: set[str] | None,
    desc: str,
    price_data: pd.DataFrame,
    index_data: pd.DataFrame,
    factor_data: pd.DataFrame,
) -> dict[str, Any]:
    strategy = SuperQualityStrategy(config, active_conditions=active_conditions)
    engine = BacktestEngine(config)
    engine.set_strategy(strategy)
    # financial_data is unused in engine (reserved arg)
    return engine.run(price_data, index_data, factor_data, pd.DataFrame())

results: list[dict[str, Any]] = []

for label, ac, desc in test_cases:
    logger.info("")
    logger.info("▶ 테스트: %s — %s", label, desc)
    try:
        result = run_single_test(label, ac, desc, price_data, index_data, factor_data)
        snapshots = result.get("portfolio_snapshots", pd.DataFrame())
        if not snapshots.empty:
            total_ret = (snapshots["nav"].iloc[-1] / snapshots["nav"].iloc[0] - 1) * 100
        else:
            total_ret = 0.0
        trade_log = result.get("trade_log", pd.DataFrame())
        trades = len(trade_log)
        closed = trade_log["return_pct"].notna().sum()
        avg_ret = trade_log.loc[trade_log["return_pct"].notna(), "return_pct"].mean() if closed > 0 else 0.0
        win_rate = (trade_log.loc[trade_log["return_pct"].notna(), "return_pct"] > 0).mean() * 100 if closed > 0 else 0.0

        results.append({
            "label": label,
            "desc": desc,
            "total_return": total_ret,
            "trades": trades,
            "closed": closed,
            "avg_trade_return": avg_ret,
            "win_rate": win_rate,
        })
        logger.info("  결과: return=%.2f%%  trades=%d  closed=%d  avg=%.4f  win=%.1f%%",
                     total_ret, trades, closed, avg_ret, win_rate)
    except Exception as e:
        logger.error("  실패: %s", e)
        import traceback
        traceback.print_exc()

# ── 4. 결과 출력 ─────────────────────────────────────────
print()
print("=" * 90)
print("단일 팩터 진단 결과  (2023-01-01 ~ 2024-12-31)")
print("=" * 90)
print(f"{'Label':<10} {'Return%':>10} {'Trades':>8} {'Closed':>8} {'Avg/Trade':>10} {'WinRate':>8}  Description")
print("-" * 90)
for r in results:
    print(f"{r['label']:<10} {r['total_return']:>9.2f}% {r['trades']:>8} {r['closed']:>8} {r['avg_trade_return']:>+9.4f} {r['win_rate']:>7.1f}%  {r['desc']}")
print("=" * 90)
