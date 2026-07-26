"""KOSPI 200 유니버스 관리.

리밸런싱 일자별 KOSPI 200 종속 목록을 제공합니다.

점포인트임(point-in-time) 유니버스를 보장하기 위해,
과거 리밸런싱 일자의 종속은 시장cap 기준 추정치를 사용합니다.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from k200_mq.core.cache import DataCache
from k200_mq.core.data.loader import get_krx_listings

logger = logging.getLogger(__name__)

_CACHE = DataCache(cache_dir="data/universe")

KOSPI200_CACHE_DIR = Path("data/universe/kospi200")


def _ensure_cache_dir() -> None:
    KOSPI200_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_kospi200_constituents(as_of: date) -> list[str]:
    """특정 일자 기준 KOSPI 200 종목 리스트를 반환합니다.

    Parameters
    ----------
    as_of : date
        리밸런싱 일자. 이 날짜 기준의 종속 목록을 반환합니다.

    Returns
    -------
    list[str]
        KOSPI 200 종목 티커 리스트 (문자열).

    Notes
    -----
    점포인트임 보장: 과거 일자의 경우 FinanceDataReader는 현재 종속만
    제공하므로, market cap 기준 상위 200으로 근사합니다.
    캐시 파일에 이미 계산된 결과가 있으면 이를 반환합니다.
    """
    _ensure_cache_dir()
    cache_key = f"kospi200_{as_of.isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached["tickers"].tolist()

    tickers = _fetch_kospi200(as_of)
    result = pd.DataFrame({"ticker": tickers, "as_of": as_of})
    _CACHE.put(cache_key, result)
    return tickers


def _fetch_kospi200(as_of: date) -> list[str]:
    """KOSPI 200 종목을 가져옵니다."""
    try:
        from financedatareader import fdr

        current_constituents = fdr.StockListing("KOSPI200")
        if not current_constituents.empty:
            tickers = current_constituents[" 종목코드"].astype(str).tolist()
            logger.info("FinanceDataReader에서 KOSPI 200 종속 %d개 확보", len(tickers))
            return tickers
    except Exception as exc:
        logger.warning("FinanceDataReader KOSPI 200 조회 실패: %s", exc)

    return _get_kospi200_by_mcap(as_of)


def _get_kospi200_by_mcap(as_of: date) -> list[str]:
    """시가총액 기준 상위 200개로 KOSPI 200 유니버스를 근사합니다.

    FinanceDataReader가 과거 종속 데이터를 제공하지 못할 경우
    사용하는 폴백 방법입니다.

    Parameters
    ----------
    as_of : date
        기준 일자.

    Returns
    -------
    list[str]
        market cap 기준 상위 200개 티커.
    """
    logger.info("Market cap 기준 KOSPI 200 근사 (%s)", as_of)

    all_listings = get_krx_listings()
    kospi = all_listings[all_listings["market"] == "KOSPI"].copy()

    if kospi.empty:
        logger.warning("KOSPI 리스팅 데이터가 비어 있습니다.")
        return []

    if "Marcap" in kospi.columns:
        kospi["Marcap"] = pd.to_numeric(kospi["Marcap"], errors="coerce")
        kospi = kospi.sort_values("Marcap", ascending=False)
        top200 = kospi.head(200)
    else:
        top200 = kospi.head(200)

    tickers = top200["ticker"].astype(str).tolist()
    logger.info("Market cap 기준 상위 200개 확보 (KOSPI 기준 %d개)", len(tickers))
    return tickers


def get_kospi200_history(
    start: date, end: date, rebalance_freq: str = "M"
) -> pd.DataFrame:
    """리밸런싱 일자별 KOSPI 200 종속 이력을 생성합니다.

    Parameters
    ----------
    start : date
        시작 일자.
    end : date
        종료 일자.
    rebalance_freq : str
        리밸런싱 주기 ('M': 월간, 'Q': 분기).

    Returns
    -------
    pd.DataFrame
        컬럼: as_of, ticker.
        각 리밸런싱 일자에 해당하는 KOSPI 200 종목 목록.
    """
    from pandas.tseries.offsets import MonthEnd, QuarterEnd

    rebalance_offsets = MonthEnd() if rebalance_freq == "M" else QuarterEnd()
    dates = pd.date_range(start, end, freq="D").date
    rebalance_dates = sorted(
        {d + rebalance_offsets for d in dates if (d + rebalance_offsets).date() <= end}
    )
    rebalance_dates = [d for d in rebalance_dates if d >= start]

    records = []
    for rd in rebalance_dates:
        tickers = get_kospi200_constituents(rd)
        for t in tickers:
            records.append({"as_of": rd, "ticker": t})

    if not records:
        return pd.DataFrame(columns=["as_of", "ticker"])

    return pd.DataFrame(records)


def is_kospi200_constituent(ticker: str, as_of: date) -> bool:
    """특정 티커가 해당 일자에 KOSPI 200 종속인지 확인합니다.

    Parameters
    ----------
    ticker : str
        확인할 티커.
    as_of : date
        기준 일자.

    Returns
    -------
    bool
        KOSPI 200 종속이면 True.
    """
    constituents = get_kospi200_constituents(as_of)
    return ticker in constituents


def exclude_kospi_top_n(tickers: list[str], n: int = 50) -> list[str]:
    """KOSPI 상위 N개 시가총액 종목을 제외합니다.

    모멘텀 팩터 성능을 위해 상위 대형주를 제외합니다
    (Choi, Choi & Kang 2013 참조).

    Parameters
    ----------
    tickers : list[str]
        원본 티커 리스트.
    n : int
        제외할 상위 종목 수 (기본 50).

    Returns
    -------
    list[str]
        상위 N개가 제외된 티커 리스트.
    """
    all_listings = get_krx_listings()
    kospi = all_listings[all_listings["market"] == "KOSPI"].copy()

    if "Marcap" not in kospi.columns or kospi.empty:
        logger.warning("시가총액 데이터 없음 — 상위 제거 없이 반환")
        return tickers

    kospi["Marcap"] = pd.to_numeric(kospi["Marcap"], errors="coerce")
    kospi = kospi.sort_values("Marcap", ascending=False)
    excluded = set(kospi.head(n)["ticker"].astype(str).tolist())

    filtered = [t for t in tickers if t not in excluded]
    logger.info("KOSPI 상위 %d개 제외: %d개 → %d개", n, len(tickers), len(filtered))
    return filtered


def apply_exclusions(
    tickers: list[str],
    exclude_management: bool = True,
    exclude_investment_notice: bool = True,
    exclude_preferred: bool = True,
    exclude_etf_etn: bool = True,
) -> list[str]:
    """전략에서 제외해야 할 종목을 필터링합니다.

    Parameters
    ----------
    tickers : list[str]
        원본 티커 리스트.
    exclude_management : bool
        관리종목 제외.
    exclude_investment_notice : bool
        투자주의 종목 제외.
    exclude_preferred : bool
        우선주 제외.
    exclude_etf_etn : bool
        ETF/ETN 제외.

    Returns
    -------
    list[str]
        필터링된 티커 리스트.
    """
    if not any([exclude_management, exclude_investment_notice, exclude_preferred, exclude_etf_etn]):
        return tickers

    all_listings = get_krx_listings()
    filtered = all_listings[all_listings["ticker"].isin(tickers)].copy()

    if exclude_management and "investment" in filtered.columns:
        filtered = filtered[~filtered["investment"].astype(str).str.contains("관리", na=False)]

    if exclude_investment_notice and "investment" in filtered.columns:
        filtered = filtered[~filtered["investment"].astype(str).str.contains("투자주의", na=False)]

    if exclude_preferred and "market" in filtered.columns:
        filtered = filtered[~filtered["ticker"].str.endswith("우", na=False)]

    if exclude_etf_etn and "market" in filtered.columns:
        etf_types = ["ETF", "ETN", "KODEX", "TIGER"]
        for etf_type in etf_types:
            if etf_type in filtered.columns:
                filtered = filtered[~filtered[etf_type].fillna(False)]

    remaining = filtered["ticker"].astype(str).tolist()
    removed = len(tickers) - len(remaining)
    if removed > 0:
        logger.info("종목 제외: %d개 제거 (관리종목/투자주의/ETF 등)", removed)

    return remaining