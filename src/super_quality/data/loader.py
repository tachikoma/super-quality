"""한국 시장의 여러 소스에서 데이터를 가져오는 데이터 로더 모듈.

소스
-------
- **FinanceDataReader**: 주식 종목 리스트, 가격 데이터, 지수 데이터
- **pykrx**: 개인 투자자 거래량
- **OpenDartReader**: 재무제표 (K-IFRS)

모든 네트워크 기반 함수는 :class:`DataCache`를 통해 적극적으로 캐싱합니다.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from super_quality.data.cache import DataCache

logger = logging.getLogger(__name__)

# ── 모든 로더 함수에서 공유하는 전역 캐시 인스턴스 ──────────────────
_cache = DataCache()

# ── DART 호출 제한 (일일 약 25,000회 후 020, 캐시로 영속화) ──────
_DART_RATE_LIMITED: bool = False

def _check_dart_rate_limited() -> bool:
    global _DART_RATE_LIMITED
    if _DART_RATE_LIMITED:
        return True
    _DART_RATE_LIMITED = _cache.get_json("_dart_rate_limited", default=False)
    return _DART_RATE_LIMITED

def _set_dart_rate_limited() -> None:
    global _DART_RATE_LIMITED
    _DART_RATE_LIMITED = True
    _cache.put_json("_dart_rate_limited", True)

# 연속 빈 응답 탐지 (020 감지용)
_consecutive_empty: list[int] = [0]

# ── 헬퍼 함수 ─────────────────────────────────────────────────────


def _to_date(d: str | date) -> date:
    """*d*를 :class:`datetime.date`로 정규화합니다."""
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def _to_str(d: str | date) -> str:
    """*d*를 ``YYYY-MM-DD`` 문자열로 정규화합니다."""
    if isinstance(d, date):
        return d.isoformat()
    return d


# ═══════════════════════════════════════════════════════════════════
# 1.  KRX 종목 리스트
# ═══════════════════════════════════════════════════════════════════


def get_krx_listings() -> pd.DataFrame:
    """FinanceDataReader를 통해 모든 KRX 주식 종목 리스트를 가져옵니다.

    Returns
    -------
    pd.DataFrame
        컬럼:

        - ``ticker``   — 6자리 종목 코드
        - ``name``     — 회사명
        - ``market``   — ``"KOSPI"`` 또는 ``"KOSDAQ"``
        - ``sector``   — 업종 분류
        - ``industry`` — 산업 분류
    """
    import FinanceDataReader as fdr  # type: ignore[import-untyped]

    key = "krx_listings"
    cached = _cache.get(key)
    if cached is not None:
        return cached

    raw = fdr.StockListing("KRX")

    result = pd.DataFrame()
    # FinanceDataReader 버전에 따라 컬럼명이 달라질 수 있음
    result["ticker"] = raw.get("Symbol", raw.get("Code", ""))
    result["name"] = raw.get("Name", "")
    result["market"] = raw.get("Market", "")
    result["sector"] = raw.get("Sector", "")
    result["industry"] = raw.get("Industry", "")

    # 다운스트림 함수에서 사용할 수 있는 원본 컬럼도 함께 전파
    for col in ("Shares", "Marcap"):
        if col in raw.columns:
            result[col] = raw[col]

    # 시장명을 일관된 이름으로 정규화
    market_map: dict[str, str] = {
        "KOSPI": "KOSPI",
        "유가증권": "KOSPI",
        "KOSDAQ": "KOSDAQ",
        "코스닥": "KOSDAQ",
        "KONEX": "KONEX",
        "코넥스": "KONEX",
    }
    result["market"] = result["market"].map(market_map).fillna(result["market"])

    _cache.put(key, result)
    return result


# ═══════════════════════════════════════════════════════════════════
# 2.  가격 데이터 (OHLCV + 시가총액)
# ═══════════════════════════════════════════════════════════════════


def _download_ticker_batch(
    tickers: list[str],
    start: str | date,
    end: str | date,
    shares_map: dict[str, float],
) -> pd.DataFrame:
    """단일 날짜 범위에 대해 여러 ticker의 가격 데이터를 다운로드합니다.

    Parameters
    ----------
    tickers : list[str]
        주식 ticker 심볼.
    start : str or date
        시작일.
    end : str or date
        종료일.
    shares_map : dict[str, float]
        ticker → 발행주식수 매핑 (시가총액 계산용).

    Returns
    -------
    pd.DataFrame
        ``date``가 컬럼(인덱스 아님)인 DataFrame,
        컬럼: ``[ticker, date, open, high, low, close, volume, mcap]``.
    """
    import FinanceDataReader as fdr  # type: ignore[import-untyped]

    start_str = _to_str(start)
    end_str = _to_str(end)

    frames: list[pd.DataFrame] = []
    total = len(tickers)
    errors = 0
    for idx, ticker in enumerate(tickers):
        try:
            raw = fdr.DataReader(ticker, start_str, end_str)
        except Exception:
            errors += 1
            if errors <= 5 or errors % 50 == 0:
                logger.debug("가격 데이터 오류 (%s): %d/%d", ticker, errors, total)
            continue
        if raw.empty:
            continue

        # 진행 상황 로깅 (50개마다)
        if (idx + 1) % 50 == 0 or idx == 0:
            logger.info("가격 데이터 다운로드 중… %d/%d (오류 %d건)", idx + 1, total, errors)

        # Naver rate-limit 회피를 위한 지연
        time.sleep(0.05)

        df = raw.copy()
        # 컬럼명을 소문자 영어로 정규화
        col_map: dict[str, str] = {}
        for c in df.columns:
            name = c.lower().strip()
            # 한글 → 영어 매핑
            kr_map = {
                "시가": "open",
                "고가": "high",
                "저가": "low",
                "종가": "close",
                "거래량": "volume",
                "등락률": "change",
                "시가총액": "marketcap",
            }
            name = kr_map.get(name, name)
            col_map[c] = name
        df = df.rename(columns=col_map)

        # change 컬럼 제거 — 스키마에 포함되지 않음
        if "change" in df.columns:
            df = df.drop(columns=["change"])

        # 필수 컬럼이 모두 존재하는지 확인
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                df[col] = 0.0

        # ── 시가총액 ──────────────────────────────────────────────
        if "marketcap" in df.columns:
            df["mcap"] = pd.to_numeric(df["marketcap"], errors="coerce").fillna(0.0)
        elif ticker in shares_map and shares_map[ticker] > 0:
            df["mcap"] = df["close"].astype(float) * shares_map[ticker]
        else:
            df["mcap"] = 0.0

        # 대상 컬럼만 유지
        df = df[["open", "high", "low", "close", "volume", "mcap"]].copy()
        df["ticker"] = ticker
        # DatetimeIndex를 리셋하여 concat을 위한 컬럼으로 변환
        df.index.name = "date"
        df = df.reset_index()
        frames.append(df)

    if errors:
        logger.info("가격 데이터 다운로드 완료: %d/%d 성공, 오류 %d건", total - errors, total, errors)
    else:
        logger.info("가격 데이터 다운로드 완료: %d/%d", total, total)

    if not frames:
        return pd.DataFrame(
            columns=["ticker", "date", "open", "high", "low", "close", "volume", "mcap"],
        )

    result = pd.concat(frames, ignore_index=True)
    result["date"] = pd.to_datetime(result["date"])
    return result


def _check_old_cache(start: str | date, end: str | date) -> None:
    """이전 ``prices_*`` 캐시 키가 존재하면 디버그 로그를 남깁니다."""
    old_key = f"prices_{_to_str(start)}_{_to_str(end)}"
    if _cache.exists(old_key):
        logger.debug("이전 캐시 키 발견: %s (마이그레이션되지 않음)", old_key)


def get_price_data(
    tickers: list[str],
    start: str | date,
    end: str | date,
) -> pd.DataFrame:
    """여러 ticker의 일별 OHLCV + 시가총액을 다운로드합니다.

    연도별 Parquet 캐시 (``price_YYYY.parquet``)를 사용하며,
    누락된 연도만 증분 다운로드합니다.

    Parameters
    ----------
    tickers : list[str]
        주식 ticker 심볼.
    start : str or date
        시작일 (``YYYY-MM-DD`` 또는 :class:`datetime.date`).
    end : str or date
        종료일.

    Returns
    -------
    pd.DataFrame
        MultiIndex ``(ticker, date)`` DataFrame, 컬럼:

        - ``open``, ``high``, ``low``, ``close``, ``volume``
        - ``mcap`` — 시가총액 (KRW)
    """
    start_date = _to_date(start)
    end_date = _to_date(end)

    # 이전 캐시 키 확인 (로깅 전용)
    _check_old_cache(start, end)

    # 시가총액 계산을 위해 발행주식수를 미리 로드
    listing = get_krx_listings()
    shares_map: dict[str, float] = {}
    if "Shares" in listing.columns:
        for _, row in listing.iterrows():
            try:
                shares_map[str(row["ticker"])] = float(row["Shares"])
            except (ValueError, TypeError, KeyError):
                pass
    # Shares 컬럼이 없으면 Marcap(시가총액 스냅샷)에서 역산
    # (첫 번째 close가 있을 때 shhres로 변환)
    shares_from_marcap: dict[str, float] = {}
    if not shares_map and "Marcap" in listing.columns:
        for _, row in listing.iterrows():
            try:
                val = float(row["Marcap"])
                if val > 0:
                    shares_from_marcap[str(row["ticker"])] = val
            except (ValueError, TypeError, KeyError):
                pass

    # ── 1. Discovery ──────────────────────────────────────────────
    years_needed = {str(y) for y in range(start_date.year, end_date.year + 1)}

    meta = _cache.get_json("price_meta")
    if meta is None:
        meta = {"cache_version": 1, "years": {}}

    download_years: set[str] = set()
    reload_years: set[str] = set()

    for y_str in sorted(years_needed):
        y = int(y_str)
        y_start = max(start_date, date(y, 1, 1))
        y_end = min(end_date, date(y, 12, 31))

        if y_str not in meta["years"]:
            download_years.add(y_str)
        else:
            info = meta["years"][y_str]
            # req_start/req_end = 이전에 다운로드한 요청 범위
            # 캐시가 현재 요청 범위를 완전히 커버하지 않으면 reload
            cached_start = _to_date(info["req_start"])
            cached_end = _to_date(info["req_end"])
            if cached_start > y_start or cached_end < y_end:
                reload_years.add(y_str)

    # ── 2. Download ───────────────────────────────────────────────
    years_to_fetch = download_years | reload_years

    if download_years == years_needed:
        # First download: full range at once → split by year
        logger.info("전체 범위 다운로드 중… (%d년)", len(years_needed))
        full = _download_ticker_batch(tickers, start_date, end_date, shares_map)
        if not full.empty:
            full["_year"] = full["date"].dt.year.astype(str)
            for y_str, grp in full.groupby("_year"):
                grp = grp.drop(columns=["_year"])
                y = int(y_str)
                y_req_start = max(start_date, date(y, 1, 1))
                y_req_end = min(end_date, date(y, 12, 31))
                _cache.put(f"price_{y_str}", grp)
                meta["years"][y_str] = {
                    "req_start": y_req_start.isoformat(),
                    "req_end": y_req_end.isoformat(),
                }
                logger.info("  %s → %s일 캐시됨", y_str, len(grp))
            del full
    elif years_to_fetch:
        # Incremental: download missing / incomplete years
        for y_str in sorted(years_to_fetch):
            y = int(y_str)
            if y_str in reload_years:
                y_start = date(y, 1, 1)
                y_end = date(y, 12, 31)
                reason = "재다운로드 (부분 캐시)"
            else:
                y_start = max(start_date, date(y, 1, 1))
                y_end = min(end_date, date(y, 12, 31))
                reason = "신규"

            logger.info(
                "  %s 다운로드 중… %s (%s ~ %s)",
                y_str, reason, y_start.isoformat(), y_end.isoformat(),
            )
            year_data = _download_ticker_batch(tickers, y_start, y_end, shares_map)
            if year_data.empty:
                logger.warning("  %s: 데이터 없음, 건너뜀", y_str)
                continue
            _cache.put(f"price_{y_str}", year_data)
            meta["years"][y_str] = {
                "req_start": y_start.isoformat(),
                "req_end": y_end.isoformat(),
            }

    # Cache hit: nothing to download
    if not years_to_fetch:
        logger.info("캐시 적중: %s — 모든 데이터가 캐시되어 있습니다", ", ".join(sorted(years_needed)))

    # Save metadata
    _cache.put_json("price_meta", meta)

    # ── 3. Assembly ───────────────────────────────────────────────
    frames: list[pd.DataFrame] = []
    for y_str in sorted(years_needed):
        df = _cache.get(f"price_{y_str}")
        if df is not None:
            frames.append(df)

    if not frames:
        result = pd.DataFrame(
            columns=["ticker", "date", "open", "high", "low", "close", "volume", "mcap"],
        )
        result = result.set_index(["ticker", "date"])
    else:
        result = pd.concat(frames, ignore_index=True)
        result["date"] = pd.to_datetime(result["date"])
        result = result.set_index(["ticker", "date"]).sort_index()
        # Filter by requested range (cache may have wider data)
        result = result.loc[(slice(None), slice(start_date, end_date)), :]

        # 캐시된 mcap이 모두 0이면 발행주식수 × 종가로 재계산
        mcap_sum = int(result["mcap"].sum()) if "mcap" in result.columns else 0
        if mcap_sum == 0:
            if not shares_map and shares_from_marcap:
                logger.info("Marcap → 발행주식수 역산 중…")
                tickers_in_data = result.index.get_level_values("ticker").unique()
                for tkr in tickers_in_data:
                    tkr_str = str(tkr)
                    marcap = shares_from_marcap.get(tkr_str)
                    if marcap and marcap > 0:
                        idx = pd.IndexSlice[tkr_str, :]
                        closes = result.loc[idx, "close"].astype(float)
                        first_close = closes[closes > 0]
                        if not first_close.empty:
                            shares_map[tkr_str] = marcap / float(first_close.iloc[0])
                logger.info("  발행주식수 계산 완료: %d개 티커", len(shares_map))
            if shares_map:
                logger.info("mcap 재계산: close × 발행주식수")
                tickers_in_data = result.index.get_level_values("ticker").unique()
                updated = 0
                for tkr in tickers_in_data:
                    tkr_str = str(tkr)
                    shares = shares_map.get(tkr_str, 0.0)
                    if shares > 0:
                        idx = pd.IndexSlice[tkr_str, :]
                        result.loc[idx, "mcap"] = result.loc[idx, "close"].astype(float) * shares
                        updated += 1
                logger.info("mcap 재계산 완료: %d개 티커 업데이트", updated)

    return result


# ═══════════════════════════════════════════════════════════════════
# 3.  시장 지수
# ═══════════════════════════════════════════════════════════════════


def get_market_index(ticker: str, start: str | date, end: str | date) -> pd.DataFrame:
    """지수 (``ticker``) 종가를 다운로드합니다.

    Parameters
    ----------
    ticker : str
        FinanceDataReader 지수 티커 (예: ``"KQ11"`` KOSDAQ, ``"KS11"`` KOSPI).
    start : str or date
        시작일.
    end : str or date
        종료일.

    Returns
    -------
    pd.DataFrame
        ``date``로 인덱싱되며, 단일 컬럼 ``close``를 가집니다.
    """
    import FinanceDataReader as fdr  # type: ignore[import-untyped]

    start_str = _to_str(start)
    end_str = _to_str(end)
    cache_key = f"index_{ticker}_{start_str}_{end_str}"

    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    raw = fdr.DataReader(ticker, start_str, end_str)
    if raw.empty:
        result = pd.DataFrame(columns=["close"])
    else:
        # 컬럼명 정규화 ('Close', '종가', … 등)
        col_map = {}
        for c in raw.columns:
            name = c.lower().strip()
            kr_map = {"종가": "close"}
            name = kr_map.get(name, name)
            col_map[c] = name
        df = raw.rename(columns=col_map)
        result = df[["close"]].copy() if "close" in df.columns else pd.DataFrame({"close": df.iloc[:, 0]})

    result.index.name = "date"
    _cache.put(cache_key, result)
    return result


# ═══════════════════════════════════════════════════════════════════
# 4.  재무제표 (OpenDartReader)
# ═══════════════════════════════════════════════════════════════════


def get_financial_data(
    tickers: list[str],
    years: list[int],
    api_key: str | None = None,
) -> pd.DataFrame:
    """OpenDartReader를 통해 연간 및 분기별 K-IFRS 재무 데이터를 가져옵니다.

    티커별로 개별 캐싱하여 증분 다운로드를 지원합니다.
    ``financial_meta.json``에 각 티커의 캐시된 연도를 기록합니다.

    Parameters
    ----------
    tickers : list[str]
        6자리 종목 코드.
    years : list[int]
        가져올 회계연도.

    Returns
    -------
    pd.DataFrame
        컬럼:

        - ``ticker``, ``year``, ``quarter`` (1=Q1, 2=반기,
          3=Q3, 4=연간)
        - ``revenue``, ``cogs``, ``net_income``, ``operating_cf``
        - ``total_assets``, ``total_equity``, ``shares_outstanding``

    Raises
    ------
    ValueError
        ``DART_API_KEY``가 설정되지 않은 경우.
    """
    if not api_key:
        from super_quality.config import SuperQualityConfig

        config = SuperQualityConfig()
        api_key = config.DART_API_KEY
        if not api_key:
            raise ValueError(
                "DART_API_KEY is not set. Obtain one from "
                "https://opendart.fss.or.kr and set it in your .env "
                "file or environment variables."
            )

    requested_years = set(years)

    # 1. 메타데이터 로드 — 캐시된 티커/연도 확인
    meta: dict[str, Any] = _cache.get_json("financial_meta") or {
        "cache_version": 1,
        "tickers": {},
    }
    cached_tickers: dict[str, list[int]] = meta.get("tickers", {})

    # 2. 누락된 (티커, 연도) 결정
    missing: dict[str, list[int]] = {}
    for ticker in tickers:
        cached_years = set(cached_tickers.get(ticker, []))
        need = requested_years - cached_years
        if need:
            missing[ticker] = sorted(need)

    # 3. 누락된 데이터만 다운로드
    if missing:
        # 3a. 이미 알려진 배드 티커 제외
        known_bad: set[str] = set(_cache.get_json("financial_bad_tickers") or [])
        bad_in_missing = known_bad & set(missing.keys())
        if bad_in_missing:
            for t in bad_in_missing:
                del missing[t]
            logger.info("알려진 배드 티커 제외: %d개", len(bad_in_missing))

        import OpenDartReader  # type: ignore[import-untyped]

        dart = OpenDartReader(api_key)

        account_mapping: dict[str, list[str]] = {
            "revenue": [
                "ifrs-full_Revenue",
                "ifrs_Revenue",
                "매출액",
            ],
            "cogs": [
                "ifrs-full_CostOfSales",
                "ifrs_CostOfSales",
                "매출원가",
            ],
            "net_income": [
                "ifrs-full_ProfitLoss",
                "ifrs_ProfitLoss",
                "당기순이익",
            ],
            "operating_cf": [
                "ifrs-full_CashFlowsFromOperatingActivities",
                "ifrs_CashFlowsFromOperatingActivities",
                "영업활동현금흐름",
                "영업현금흐름",
            ],
            "total_assets": [
                "ifrs-full_Assets",
                "ifrs_Assets",
                "자산총계",
            ],
            "total_equity": [
                "ifrs-full_Equity",
                "ifrs_Equity",
                "자본총계",
            ],
        }

        def _find_account(
            data: list[dict[str, Any]] | pd.DataFrame,
            possible_names: list[str],
            column_name: str | None = None,
        ) -> float | None:
            def _get_name(item: Any) -> str:
                return str(item.get("account_nm", item.get("account_id", "")))

            def _get_val(item: Any) -> float | None:
                for col in ("thstrm_amount", "amt", "amount"):
                    val = item.get(col)
                    if val is not None:
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            continue
                return None

            # Collect all rows
            rows: list[dict[str, Any]] = []
            if isinstance(data, pd.DataFrame):
                for _, r in data.iterrows():
                    rows.append(r.to_dict())
            elif isinstance(data, list):
                rows = list(data)
            elif isinstance(data, dict):
                for name in possible_names:
                    if name in data:
                        return _get_val(data)
                return None
            else:
                return None

            # Pass 1: exact match
            for row in rows:
                name = _get_name(row)
                if name in possible_names:
                    val = _get_val(row)
                    if val is not None:
                        return val

            # Pass 2: containment match (possible name is substring of account name, or vice versa)
            for row in rows:
                name = _get_name(row)
                for pn in possible_names:
                    if pn in name or name in pn:
                        val = _get_val(row)
                        if val is not None:
                            return val

            # Pass 3: keyword-based fallback
            if column_name == "net_income":
                for row in rows:
                    name = _get_name(row)
                    if "순이익" in name and "주당" not in name and "희석" not in name:
                        val = _get_val(row)
                        if val is not None:
                            return val
            elif column_name == "operating_cf":
                for row in rows:
                    name = _get_name(row)
                    if "영업" in name and "현금흐름" in name and "재무" not in name and "투자" not in name:
                        val = _get_val(row)
                        if val is not None:
                            return val

            return None

        def _get_shares_from_dart(
            dart_client: Any, ticker: str, year: int
        ) -> float | None:
            try:
                shares_data = dart_client.ps_blsstu(ticker, year)
                time.sleep(0.15)
                if isinstance(shares_data, pd.DataFrame) and not shares_data.empty:
                    for col in ("stk_cnt", "istc_totqy", "발행주식수"):
                        if col in shares_data.columns:
                            return float(shares_data[col].iloc[0])
                    for col in shares_data.columns:
                        try:
                            val = float(shares_data[col].iloc[0])
                            if val > 0:
                                return val
                        except (ValueError, TypeError):
                            continue
                elif isinstance(shares_data, (list, dict)):
                    items = shares_data if isinstance(shares_data, list) else [shares_data]
                    for item in items:
                        for key in ("stk_cnt", "istc_totqy", "발행주식수"):
                            if key in item:
                                return float(item[key])
            except Exception:
                pass
            return None

        # Pre-screen: skip tickers with no DART financial data
        skip_tickers_raw: set[str] = set()
        for ticker in list(missing.keys()):
            need_years = missing[ticker]
            # 3b. find_corp_code 검증 (ValueError 방지)
            try:
                corp_code = dart.find_corp_code(ticker)
            except Exception:
                corp_code = None
            if not corp_code:
                skip_tickers_raw.add(ticker)
                del missing[ticker]
                logger.info("  DART corp_code 없음: %s — 건너뜀", ticker)
                continue
            # 3c. 1회 테스트 호출 ('013' 방지 — REIT 등)
            try:
                test = dart.finstate_all(ticker, max(need_years), "11011", fs_div="CFS")
                time.sleep(0.15)
            except Exception:
                test = None
            if test is None or (isinstance(test, pd.DataFrame) and test.empty):
                skip_tickers_raw.add(ticker)
                del missing[ticker]
                logger.info("  DART 재무 데이터 없음: %s — 건너뜀", ticker)
                continue
        if skip_tickers_raw:
            _cache.put_json("financial_bad_tickers", sorted(skip_tickers_raw))
            logger.info("스킵된 티커 저장: %d개", len(skip_tickers_raw))

        for ticker, need_years in missing.items():
            existing = _cache.get(f"financial_{ticker}")
            existing_records: list[dict[str, Any]] = []
            if existing is not None and not existing.empty:
                existing_records = existing.to_dict("records")

            new_records: list[dict[str, Any]] = []
            ticker_cache: dict[int, float | None] = {}

            for year in need_years:
                if year not in ticker_cache:
                    ticker_cache[year] = _get_shares_from_dart(dart, ticker, year)

                available = _get_available_quarters(year)
                if not available:
                    logger.debug("  연도 %d에 사용 가능한 분기 없음 (as_of=%s)", year, date.today())
                    continue

                for quarter, reprt_code in available.items():
                    fin_data = None
                    for fs_div in ("CFS", "OFS"):
                        try:
                            fin_data = dart.finstate_all(ticker, year, reprt_code, fs_div=fs_div)
                            time.sleep(0.15)
                        except Exception:
                            continue

                        if fin_data is not None and (
                            isinstance(fin_data, pd.DataFrame) and not fin_data.empty
                        ):
                            break

                    if fin_data is None or (
                        isinstance(fin_data, pd.DataFrame) and fin_data.empty
                    ):
                        logger.debug(
                            "  재무 데이터 없음: %s %d Q%d (CFS/OFS 모두 실패)",
                            ticker, year, quarter,
                        )
                        continue

                    record: dict[str, Any] = {
                        "ticker": ticker,
                        "year": year,
                        "quarter": quarter,
                        "shares_outstanding": ticker_cache[year],
                    }
                    for col, names in account_mapping.items():
                        record[col] = _find_account(fin_data, names, column_name=col)
                    new_records.append(record)

            if new_records:
                combined = pd.concat(
                    [pd.DataFrame(existing_records), pd.DataFrame(new_records)],
                    ignore_index=True,
                )
                combined = combined.sort_values(
                    ["ticker", "year", "quarter"]
                ).drop_duplicates(
                    subset=["ticker", "year", "quarter"], keep="last"
                ).reset_index(drop=True)
                _cache.put(f"financial_{ticker}", combined)

                # 메타데이터 업데이트 — 실제 데이터를 가져온 경우에만
                cached_years_set = set(cached_tickers.get(ticker, []))
                cached_years_set.update(need_years)
                cached_tickers[ticker] = sorted(cached_years_set)

            # 각 티커 완료 시 즉시 메타 저장 (중단 시 재시작 가능)
            meta["tickers"] = cached_tickers
            _cache.put_json("financial_meta", meta)

    # 4. 요청된 티커/연도에 맞게 조합
    frames: list[pd.DataFrame] = []
    for ticker in tickers:
        df = _cache.get(f"financial_{ticker}")
        if df is not None and not df.empty:
            subset = df[df["year"].isin(years)].copy()
            if not subset.empty:
                frames.append(subset)

    if not frames:
        result = pd.DataFrame(
            columns=[
                "ticker",
                "year",
                "quarter",
                "revenue",
                "cogs",
                "net_income",
                "operating_cf",
                "total_assets",
                "total_equity",
                "shares_outstanding",
            ],
        )
    else:
        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values(["ticker", "year", "quarter"]).reset_index(drop=True)

    if not result.empty:
        key_cols = ["revenue", "net_income", "operating_cf", "total_assets", "total_equity"]
        non_null_counts = {col: result[col].notna().sum() for col in key_cols if col in result.columns}
        logger.info(
            "재무 데이터 요약: %d행, 티커 %d개",
            len(result), result["ticker"].nunique(),
        )
        logger.info("  NaN이 아닌 값: %s", non_null_counts)

    return result


# ═══════════════════════════════════════════════════════════════════
# 5.  개인 순매수 (pykrx)
# ═══════════════════════════════════════════════════════════════════


def get_retail_net_buy(
    ticker: str,
    start: str | date,
    end: str | date,
) -> pd.DataFrame:
    """pykrx를 통해 개인 투자자 순매수 금액을 가져옵니다.

    Parameters
    ----------
    ticker : str
        6자리 종목 코드.
    start : str or date
        시작일.
    end : str or date
        종료일.

    Returns
    -------
    pd.DataFrame
        ``date``로 인덱싱되며, 단일 컬럼 ``retail_net_buy``
        (KRW)를 가집니다.
    """
    from pykrx import stock  # type: ignore[import-untyped]

    start_str = _to_str(start)
    end_str = _to_str(end)
    cache_key = f"retail_net_buy_{ticker}_{start_str}_{end_str}"

    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    df = stock.get_market_trading_value_by_date(start_str, end_str, ticker)
    time.sleep(0.15)

    if df.empty:
        result = pd.DataFrame(columns=["retail_net_buy"])
        _cache.put(cache_key, result)
        return result

    # pykrx는 MultiIndex 컬럼을 반환: (investor_type, metric)
    # 투자자 유형: '개인', '외국인', '기관계', '기타법인', '전체'
    # 지표: '매도거래량', '매수거래량', '순매수거래량', '매도금액', '매수금액', '순매수금액'
    try:
        if isinstance(df.columns, pd.MultiIndex):
            retail_series = df.loc[:, ("개인", "순매수금액")]
        else:
            # 대체: '개인'과 '순매수금액'을 모두 포함하는 컬럼 찾기
            target_col = None
            for col in df.columns:
                col_str = str(col)
                if "개인" in col_str and "순매수금액" in col_str:
                    target_col = col
                    break
            if target_col is None:
                retail_series = pd.Series(0.0, index=df.index, name="retail_net_buy")
            else:
                retail_series = df[target_col]
    except (KeyError, ValueError):
        retail_series = pd.Series(0.0, index=df.index, name="retail_net_buy")

    result = retail_series.to_frame(name="retail_net_buy")
    result.index.name = "date"
    _cache.put(cache_key, result)
    return result


# ═══════════════════════════════════════════════════════════════════
# 6.  발행주식수
# ═══════════════════════════════════════════════════════════════════


def get_shares_outstanding(
    ticker: str,
    dates: list[str | date],
    api_key: str | None = None,
) -> pd.Series:
    """특정 ticker의 일자별 발행주식수를 조회합니다.

    출처 (순서대로 시도):

    1. OpenDartReader ``ps_blsstu`` (연간 데이터, forward-filled)
    2. FinanceDataReader ``StockListing`` (현재 주식수, 상수)

    티커별로 캐싱되어 증분 조회를 지원합니다 (``shares_{ticker}``).

    Parameters
    ----------
    ticker : str
        6자리 종목 코드.
    dates : list of str or date
        발행주식수가 필요한 일자들.

    Returns
    -------
    pd.Series
        일자로 인덱싱되며, 값 = 발행주식수.
    """
    if not api_key:
        from super_quality.config import SuperQualityConfig

        config = SuperQualityConfig()
        api_key = config.DART_API_KEY

    date_objs = sorted(_to_date(d) for d in dates)
    years_needed = sorted({d.year for d in date_objs})

    # 캐시 확인
    cached_years: dict[int, float] = _cache.get_json(f"shares_{ticker}") or {}
    missing_years = [y for y in years_needed if y not in cached_years]

    # 전략 1: OpenDartReader ps_blsstu (캐시에 없는 연도만)
    if missing_years and api_key:
        import OpenDartReader  # type: ignore[import-untyped]

        dart = OpenDartReader(api_key)
        for yr in missing_years:
            try:
                shares_data = dart.ps_blsstu(ticker, yr)
                time.sleep(0.15)
                if isinstance(shares_data, pd.DataFrame) and not shares_data.empty:
                    for col in ("stk_cnt", "istc_totqy", "발행주식수"):
                        if col in shares_data.columns:
                            cached_years[yr] = float(shares_data[col].iloc[0])
                            break
            except Exception:
                continue

        if cached_years:
            _cache.put_json(f"shares_{ticker}", cached_years)

    if cached_years:
        values = []
        for d in date_objs:
            applicable_year = max(
                (y for y in cached_years if y <= d.year),
                default=None,
            )
            if applicable_year is not None:
                values.append(cached_years[applicable_year])
            else:
                values.append(float("nan"))
        result = pd.Series(values, index=pd.DatetimeIndex(date_objs), name="shares_outstanding")
        return result

    # 전략 2: FinanceDataReader StockListing (현재 발행주식수)
    listing = get_krx_listings()
    ticker_row = listing[listing["ticker"] == ticker]
    if not ticker_row.empty and "Shares" in ticker_row.columns:
        try:
            shares = float(ticker_row["Shares"].iloc[0])
            values = [shares] * len(date_objs)
            result = pd.Series(values, index=pd.DatetimeIndex(date_objs), name="shares_outstanding")
            return result
        except (ValueError, TypeError):
            pass

    # 대체: 모두 NaN
    result = pd.Series(
        [float("nan")] * len(date_objs),
        index=pd.DatetimeIndex(date_objs),
        name="shares_outstanding",
    )
    return result


# ═══════════════════════════════════════════════════════════════════
# 6.5  유상증자 일정
# ═══════════════════════════════════════════════════════════════════


def get_paid_in_capital_increases(
    ticker: str,
    years: list[int],
    api_key: str | None = None,
) -> list[date]:
    """OpenDartReader ``report`` 키워드 검색을 통해 유상증자 일정을 조회합니다.

    증권신고서(reprt_code=11011)에서 '유상증자' 키워드로 검색한 뒤,
    증자 방식(``isu_dcrs_stle``)에 '유상'이 포함된 건의 일자를 반환합니다.

    티커별로 캐싱되어 증분 조회를 지원합니다 (``paid_capital_{ticker}``).

    Parameters
    ----------
    ticker : str
        6자리 종목 코드.
    years : list[int]
        조회할 회계연도 리스트.
    api_key : str or None
        DART API 키. ``None``이면 환경 변수/설정에서 읽습니다.

    Returns
    -------
    list[date]
        유상증자 발생일 리스트. 조회 불가 또는 해당 건이 없으면 빈 리스트.
    """
    if not api_key:
        from super_quality.config import SuperQualityConfig

        config = SuperQualityConfig()
        api_key = config.DART_API_KEY
        if not api_key:
            return []

    requested_years = set(years)

    # 캐시 확인
    cached: dict[str, list[str]] = _cache.get_json(f"paid_capital_{ticker}") or {}
    cached_years: set[int] = set(int(k) for k in cached)
    missing_years = sorted(requested_years - cached_years)

    if not missing_years:
        # 캐시에서만 조합
        result: list[date] = []
        for y in sorted(years):
            result.extend(
                datetime.strptime(d, "%Y-%m-%d").date()
                for d in cached.get(str(y), [])
            )
        return sorted(set(result))

    if _check_dart_rate_limited():
        logger.warning("  DART 020 — 유상증자 캐시 skip: %s", ticker)
        return []

    import OpenDartReader  # type: ignore[import-untyped]

    dart = OpenDartReader(api_key)

    for year in missing_years:
        dates_str: list[str] = []
        try:
            sd = dart.report(ticker, "증자", year, reprt_code="11011")
            time.sleep(0.15)
        except Exception:
            continue
        if sd is None or (isinstance(sd, pd.DataFrame) and sd.empty):
            _consecutive_empty[0] += 1
            if _consecutive_empty[0] >= 50:
                _set_dart_rate_limited()
                cached[str(year)] = []
                _cache.put_json(f"paid_capital_{ticker}", cached)
                raise RuntimeError("020")
            continue
        if isinstance(sd, pd.DataFrame):
            for _, row in sd.iterrows():
                style = str(row.get("isu_dcrs_stle", ""))
                if "유상" not in style:
                    continue
                raw = str(row.get("isu_dcrs_de", ""))
                for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y%m%d"):
                    try:
                        dates_str.append(datetime.strptime(raw, fmt).date().isoformat())
                        break
                    except ValueError:
                        continue
        elif isinstance(sd, dict):
            style = str(sd.get("isu_dcrs_stle", ""))
            if "유상" not in style:
                continue
            raw = str(sd.get("isu_dcrs_de", ""))
            for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y%m%d"):
                try:
                    dates_str.append(datetime.strptime(raw, fmt).date().isoformat())
                    break
                except ValueError:
                    continue

        _consecutive_empty[0] = 0
        cached[str(year)] = dates_str

    _cache.put_json(f"paid_capital_{ticker}", cached)

    result = []
    for y in sorted(years):
        result.extend(
            datetime.strptime(d, "%Y-%m-%d").date()
            for d in cached.get(str(y), [])
        )
    return sorted(set(result))


# ═══════════════════════════════════════════════════════════════════
# 7.  TTM 계산
# ═══════════════════════════════════════════════════════════════════


def calculate_ttm(
    financial_df: pd.DataFrame,
    account_col: str,
    date_col: str = "date",
) -> pd.Series:
    r"""K-IFRS 누적 데이터로부터 trailing twelve months (TTM)을 계산합니다.

    K-IFRS 재무 데이터는 각 회계연도 내에서 누적으로 보고됩니다:

    - Q1 (3월)           = 단일 분기
    - 반기 (6월)          = Q1 + Q2
    - Q3 (9월)           = Q1 + Q2 + Q3
    - 연간 (12월)         = 전체 연도

    이 함수는:

    1. 각 누적 항목에서 단일 분기 값을 도출합니다.
    2. 가장 최근 4개 단일 분기 값을 합산합니다.

    Parameters
    ----------
    financial_df : pd.DataFrame
        **단일 ticker**에 대한 누적 재무 데이터를 담은 DataFrame.
        날짜형 컬럼과 *account_col*이 있어야 합니다.
    account_col : str
        누적 재무 지표 컬럼명 (예: ``"revenue"``).
    date_col : str
        기간 종료일을 나타내는 컬럼명.
        기본값 ``"date"``.

    Returns
    -------
    pd.Series
        기간 종료일로 인덱싱된 TTM 값 (4개 미만의 trailing
        분기가 있는 행은 제외됩니다).
    """
    df = financial_df.copy()

    # date_col이 없으면 year/quarter로부터 생성 시도
    if date_col not in df.columns:
        if "year" in df.columns and "quarter" in df.columns:
            q_to_month = {1: 3, 2: 6, 3: 9, 4: 12}
            df["_date"] = df.apply(
                lambda r: date(int(r["year"]), q_to_month.get(int(r["quarter"]), 12), 1),  # noqa: DTZ001
                axis=1,
            )
            date_col = "_date"
        else:
            raise ValueError(
                f"DataFrame must contain '{date_col}' column or "
                "'year' and 'quarter' columns."
            )

    # 정렬을 위해 date 컬럼이 datetime 타입인지 확인
    if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        df[date_col] = pd.to_datetime(df[date_col])

    df = df.sort_values(date_col).reset_index(drop=True)

    # 보고 기간을 식별하기 위해 월 추출
    df["_month"] = df[date_col].dt.month
    month_to_q = {3: 1, 6: 2, 9: 3, 12: 4}
    df["_q"] = df["_month"].map(month_to_q).fillna(0).astype(int)

    # ── 단일 분기 값 도출 ──────────────────────────────────────
    # Q1 (month=3): 이미 단일 분기, 그대로 유지
    # 나머지: 단일 = 현재 누적값 − 이전 누적값
    df["_prev_cum"] = df[account_col].shift(1)
    df["_single"] = df[account_col]
    mask = df["_q"] > 1
    df.loc[mask, "_single"] = df.loc[mask, account_col] - df.loc[mask, "_prev_cum"]

    # ── 롤링 TTM (최근 4개 단일 분기 값) ────────────────────────
    df["ttm"] = df["_single"].rolling(window=4, min_periods=4).sum()

    result = df.set_index(date_col)["ttm"].dropna()
    result.name = "ttm"
    return result


# ═══════════════════════════════════════════════════════════════════
# 8.  사용 가능한 재무 데이터 시차
# ═══════════════════════════════════════════════════════════════════


DART_MIN_YEAR = 2015
"""OpenDartReader ``finstate_all``는 2015년 이후 데이터만 제공합니다."""


def _get_available_quarters(
    target_year: int,
    as_of_date: date | None = None,
) -> dict[int, str]:
    """*as_of_date* 시점에 조회 가능한 분기별 보고서 코드를 반환합니다.

    12월 결산 기업 기준:
    - Q1 (11013): 5월 15일 이후
    - 반기 (11012): 8월 15일 이후
    - Q3 (11014): 11월 15일 이후
    - 연간 (11011): 다음 해 3월 31일 이후

    DART API는 2015년 이후 데이터만 제공하므로,
    ``target_year``가 2015 미만이면 빈 딕셔너리를 반환합니다.
    """
    if target_year < DART_MIN_YEAR:
        return {}

    as_of_date = as_of_date or date.today()

    deadlines: dict[int, date] = {
        1: date(target_year, 5, 15),
        2: date(target_year, 8, 15),
        3: date(target_year, 11, 15),
        4: date(target_year + 1, 3, 31),
    }
    codes: dict[int, str] = {
        1: "11013",
        2: "11012",
        3: "11014",
        4: "11011",
    }
    return {q: codes[q] for q, d in deadlines.items() if as_of_date >= d}


def date_to_financial_epoch(d: date) -> tuple[int, int]:
    """해당 날짜 기준 가장 최근에 이용 가능한 (연도, 분기)를 반환합니다.

    12월 결산 기업의 K-IFRS 제출 마감일을 기준으로 합니다:

    ==================  =============  =============
    As-of 날짜 범위       최신 분기       (year, quarter)
    ==================  =============  =============
    1/1 ~ 3/30          전년도 Q3      (y-1, 3)
    3/31 ~ 5/14         전년도 연간    (y-1, 4)
    5/15 ~ 8/14         당해 Q1        (y, 1)
    8/15 ~ 11/14        당해 반기      (y, 2)
    11/15 ~ 12/31       당해 Q3        (y, 3)
    ==================  =============  =============
    """
    y = d.year
    if d >= date(y, 11, 15):
        return (y, 3)
    if d >= date(y, 8, 15):
        return (y, 2)
    if d >= date(y, 5, 15):
        return (y, 1)
    if d >= date(y, 3, 31):
        return (y - 1, 4)
    return (y - 1, 3)


def _get_available_year_quarter_set(
    target_year: int,
    as_of_date: date,
) -> set[tuple[int, int]]:
    """*as_of_date* 시점에 조회 가능한 (year, quarter) 셋을 반환합니다."""
    quarters = _get_available_quarters(target_year, as_of_date)
    return {(target_year, q) for q in quarters}


def get_financial_snapshot(
    financial_df: pd.DataFrame,
    as_of_date: date,
) -> pd.DataFrame:
    """*as_of_date* 시점에 이용 가능한 최신 재무 데이터를 ticker별로 반환합니다.

    Parameters
    ----------
    financial_df : pd.DataFrame
        ``ticker``, ``year``, ``quarter`` 컬럼 포함.
    as_of_date : date
        평가 기준일.

    Returns
    -------
    pd.DataFrame
        각 ticker의 가장 최근 분기 데이터 (1행/ticker).
        빈 입력이면 빈 DataFrame 반환.
    """
    if financial_df.empty:
        return pd.DataFrame()

    # 결정: as_of_date 기준 사용 가능한 분기들
    available: set[tuple[int, int]] = set()
    for y in range(as_of_date.year, as_of_date.year - 5, -1):
        available.update(_get_available_year_quarter_set(y, as_of_date))

    # 재무 데이터 필터링
    mask = financial_df.apply(
        lambda r: (int(r["year"]), int(r["quarter"])) in available,
        axis=1,
    )
    eligible = financial_df[mask]
    if eligible.empty:
        return pd.DataFrame()

    # ticker별 최신 분기
    return (
        eligible.sort_values(["ticker", "year", "quarter"])
        .groupby("ticker")
        .last()
        .reset_index()
    )


def compute_ttm_snapshot(
    financial_df: pd.DataFrame,
    as_of_date: date,
) -> pd.DataFrame:
    """*as_of_date* 시점의 TTM (trailing_ni, trailing_ocf)를 ticker별로 계산합니다.

    K-IFRS 누적 데이터에서 단일 분기 값을 역산한 후
    최근 4개 단일 분기 값을 합산합니다.
    """
    if financial_df.empty:
        return pd.DataFrame()

    available: set[tuple[int, int]] = set()
    for y in range(as_of_date.year, as_of_date.year - 5, -1):
        available.update(_get_available_year_quarter_set(y, as_of_date))

    mask = financial_df.apply(
        lambda r: (int(r["year"]), int(r["quarter"])) in available,
        axis=1,
    )
    eligible = financial_df[mask]
    if eligible.empty:
        return pd.DataFrame()

    fin_sorted = eligible.sort_values(["ticker", "year", "quarter"])
    ttm_rows: list[dict[str, Any]] = []

    for ticker, grp in fin_sorted.groupby("ticker"):
        grp = grp.tail(5) if len(grp) >= 5 else grp.tail(len(grp))
        if len(grp) < 4:
            continue

        vals = grp[["net_income", "operating_cf"]].values.astype(float)
        single_vals = vals.copy()
        for i in range(1, len(vals)):
            single_vals[i] = vals[i] - vals[i - 1]
        last4 = single_vals[-4:]
        ttm_rows.append({
            "ticker": ticker,
            "trailing_ni": float(np.nansum(last4[:, 0])),
            "trailing_ocf": float(np.nansum(last4[:, 1])),
        })

    if not ttm_rows:
        return pd.DataFrame()

    return pd.DataFrame(ttm_rows)


def get_available_lag(rebalance_date: date) -> date:
    """가장 최근에 이용 가능한 재무제표 기간 종료일을 반환합니다.

    12월 결산 기업의 K-IFRS 제출 마감일을 반영합니다:

    ==================  =================  =============
    기간                종료일              제출 마감일
    ==================  =================  =============
    Q1                  3월 31일            5월 15일
    반기                6월 30일            8월 15일
    Q3                  9월 30일            11월 15일
    연간 (전년도)       12월 31일           3월 31일
    ==================  =================  =============

    Parameters
    ----------
    rebalance_date : date
        포트폴리오 리밸런싱 일자.

    Returns
    -------
    date
        *rebalance_date*까지 제출되었을 가장 최근 재무제표의
        기간 종료 *일자*.

    Examples
    --------
    >>> get_available_lag(date(2024, 6, 1))
    datetime.date(2024, 3, 31)   # 2024 Q1

    >>> get_available_lag(date(2024, 4, 1))
    datetime.date(2023, 12, 31)  # 2023 연간 (2024 Q1 아직 미제출)
    """
    y = rebalance_date.year

    # 해당 연도의 제출 마감일
    q1_deadline = date(y, 5, 15)
    semi_deadline = date(y, 8, 15)
    q3_deadline = date(y, 11, 15)
    annual_deadline = date(y, 3, 31)  # 전년도 연간 보고서

    # 최신순 → 오래된순으로 확인
    if rebalance_date >= q3_deadline:
        return date(y, 9, 30)
    if rebalance_date >= semi_deadline:
        return date(y, 6, 30)
    if rebalance_date >= q1_deadline:
        return date(y, 3, 31)
    if rebalance_date >= annual_deadline:
        return date(y - 1, 12, 31)

    # 3월 31일 이전: 가장 최근 데이터는 전년도 Q3
    return date(y - 1, 9, 30)
