"""KOSPI 200 유니버스 관리.

리밸런싱 일자별 KOSPI 200 종속 목록을 제공합니다.

현재 데이터 소스는 point-in-time 유니버스를 보장하지 않습니다. 캐시는
요청일을 키로 삼는 as-of-keyed proxy cache이며, 키의 날짜만으로 과거 구성을
복원하지 않습니다. 과거 리밸런싱 일자의 provenance는 `proxy_current`,
`mcap_proxy`, 또는 신뢰할 수 없는 레거시 캐시의 `legacy_proxy_unknown`으로
기록하며, 실제 PIT 유효성은 source/effective-date contract/fingerprint가
있는 역사 파일이 연결된 경우에만 인정합니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, cast


import pandas as pd

from k200_mq.core.cache import DataCache
from k200_mq.core.data.loader import get_krx_listings
from k200_mq.data.provenance import (
    LEGACY_PROXY_UNKNOWN as LEGACY_PROXY_UNKNOWN,
    PIT_EFFECTIVE_DATE_CONTRACT as PIT_EFFECTIVE_DATE_CONTRACT,
    PIT_SCHEMA_CONTRACT as PIT_SCHEMA_CONTRACT,
    PROXY_CONTRACTS as PROXY_CONTRACTS,
    _constituent_fingerprint as _constituent_fingerprint,
    _metadata_label as _metadata_label,
    _metadata_matches as _metadata_matches,
    validate_universe_provenance as _validate_universe_provenance,
)

logger = logging.getLogger(__name__)

_CACHE = DataCache(cache_dir="data/universe")

KOSPI200_CACHE_DIR = Path("data/universe/kospi200")

UniverseSource = Literal["pit", "proxy_current", "mcap_proxy", "legacy_proxy_unknown"]


class ConstituentList(list[str]):
    """Ticker list carrying the provenance of the lookup that produced it.

    The historical public API returns a list, so this is intentionally a list
    subclass rather than a new return type.  ``provenance`` is not persisted in
    the ticker values and is only used while assembling a history.
    """

    def __init__(
        self,
        tickers: list[str],
        provenance: str,
        provenance_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(tickers)
        self.provenance = provenance
        self.provenance_metadata = provenance_metadata


@dataclass(frozen=True)
class UniverseHistoryResult:
    """A universe history and its source metadata.

    ``data`` deliberately contains only the legacy ``as_of`` and ``ticker``
    columns.  Source metadata is kept out of the strategy input and is exposed
    through this result and ``DataFrame.attrs`` on the compatibility wrapper.
    """

    data: pd.DataFrame
    provenance: str
    provenance_by_as_of: dict[str, str]
    provenance_metadata_by_as_of: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def pit_valid(self) -> bool:
        """Whether every generated constituent set is point-in-time valid."""
        return bool(validate_universe_provenance(self)["pit_valid"])


def _ensure_cache_dir() -> None:
    KOSPI200_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _provenance_metadata(
    label: str,
    as_of: date,
    tickers: list[str],
    source: str,
) -> dict[str, Any]:
    """Build the complete metadata record required for a cache hit."""
    return {
        "label": label,
        "source": source,
        "schema": dict(PIT_SCHEMA_CONTRACT),
        "effective_date": as_of.isoformat(),
        "contract": (
            PIT_EFFECTIVE_DATE_CONTRACT
            if label == "pit"
            else PROXY_CONTRACTS.get(label, "")
        ),
        "fingerprint": _constituent_fingerprint(tickers),
    }


def _classify_cached_provenance(
    metadata: Any,
    as_of: date,
    tickers: list[str],
) -> tuple[UniverseSource, dict[str, Any] | None]:
    """Classify a cache entry conservatively; old sidecars become unknown."""
    label = _metadata_label(metadata)
    if label not in {"pit", "proxy_current", "mcap_proxy"}:
        return LEGACY_PROXY_UNKNOWN, None
    if not _metadata_matches(metadata, label, as_of, tickers):
        return LEGACY_PROXY_UNKNOWN, None
    return cast(UniverseSource, label), dict(metadata)


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
    The returned list carries a ``provenance`` attribute with one of
    ``pit``, ``proxy_current``, ``mcap_proxy``, or ``legacy_proxy_unknown``.
    The current FinanceDataReader KOSPI200 listing is ``proxy_current`` because
    it ignores ``as_of``; the current-listing market-cap fallback is
    ``mcap_proxy``.  Cache entries without a complete provenance record are
    ``legacy_proxy_unknown``.  Neither proxy is PIT-valid.
    """
    _ensure_cache_dir()
    cache_key = f"kospi200_{as_of.isoformat()}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        metadata = _CACHE.get_json("kospi200_provenance")
        tickers = cached["ticker"].astype(str).tolist()
        entry = metadata.get(cache_key) if isinstance(metadata, dict) else None
        provenance, trusted_metadata = _classify_cached_provenance(
            entry, as_of, tickers,
        )
        return ConstituentList(tickers, provenance, trusted_metadata)

    tickers, provenance = _fetch_kospi200_with_provenance(as_of)
    ticker_values = [str(ticker) for ticker in tickers]
    supplied_metadata = getattr(tickers, "provenance_metadata", None)
    metadata_entry = supplied_metadata
    if not _metadata_matches(metadata_entry, provenance, as_of, ticker_values):
        if provenance in PROXY_CONTRACTS:
            source = (
                "FinanceDataReader.StockListing(KOSPI200)"
                if provenance == "proxy_current"
                else "current KRX listings market-cap snapshot"
            )
            metadata_entry = _provenance_metadata(
                provenance, as_of, ticker_values, source,
            )
        else:
            # A bare ``pit`` label from a provider is not a PIT contract.
            provenance = LEGACY_PROXY_UNKNOWN
            metadata_entry = None

    result = pd.DataFrame({"ticker": ticker_values, "as_of": as_of})
    _CACHE.put(cache_key, result)
    metadata = _CACHE.get_json("kospi200_provenance")
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata_entry is not None:
        metadata[cache_key] = metadata_entry
    else:
        metadata.pop(cache_key, None)
    _CACHE.put_json("kospi200_provenance", metadata)
    return ConstituentList(ticker_values, provenance, metadata_entry)


def _fetch_kospi200(as_of: date) -> list[str]:
    """KOSPI 200 종목을 가져옵니다 (legacy list-only helper)."""
    tickers, _ = _fetch_kospi200_with_provenance(as_of)
    return tickers


def _fetch_kospi200_with_provenance(as_of: date) -> tuple[list[str], UniverseSource]:
    """Fetch constituents and classify the source without claiming PIT."""
    try:
        try:
            import FinanceDataReader as fdr  # type: ignore[import-untyped]
        except ImportError:
            from financedatareader import fdr  # type: ignore[import-untyped]

        current_constituents = fdr.StockListing("KOSPI200")
        if not current_constituents.empty:
            code_column = " 종목코드" if " 종목코드" in current_constituents else "Symbol"
            tickers = current_constituents[code_column].astype(str).tolist()
            logger.info("FinanceDataReader에서 KOSPI 200 종속 %d개 확보", len(tickers))
            return tickers, "proxy_current"
    except Exception as exc:
        logger.warning("FinanceDataReader KOSPI 200 조회 실패: %s", exc)

    return _get_kospi200_by_mcap(as_of), "mcap_proxy"


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
    ts_start = pd.Timestamp(start)
    ts_end = pd.Timestamp(end)

    # Generate calendar period ends once, then roll each one back to the
    # latest weekday.  The old ``every day + MonthEnd`` construction produced
    # duplicate candidates and left weekend month-ends unusable as trading
    # signals.  The engine performs a final roll against the actual price
    # calendar, which also handles exchange holidays absent from this simple
    # weekday calendar.
    frequency = "ME" if rebalance_freq == "M" else "QE"
    calendar_ends = pd.date_range(ts_start, ts_end, freq=frequency)
    rebalance_dates: list[date] = []
    for calendar_end in calendar_ends:
        rebalance_date = _last_weekday(calendar_end.date())
        if ts_start.date() <= rebalance_date <= ts_end.date():
            rebalance_dates.append(rebalance_date)
    rebalance_dates = sorted(set(rebalance_dates))

    records = []
    sources_by_date: dict[str, str] = {}
    metadata_by_date: dict[str, dict[str, Any]] = {}
    for rd in rebalance_dates:
        tickers = get_kospi200_constituents(rd)
        sources_by_date[rd.isoformat()] = getattr(tickers, "provenance", "unknown")
        metadata = getattr(tickers, "provenance_metadata", None)
        if isinstance(metadata, dict):
            metadata_by_date[rd.isoformat()] = dict(metadata)
        for t in tickers:
            records.append({"as_of": rd, "ticker": t})

    if not records:
        history = pd.DataFrame(columns=["as_of", "ticker"])
    else:
        history = pd.DataFrame(records)

    # Keep source metadata on the frame so callers that rely on the legacy
    # DataFrame API still receive the validity contract.
    history.attrs["provenance_by_as_of"] = dict(sources_by_date)
    unique_sources = set(sources_by_date.values())
    history.attrs["provenance"] = (
        next(iter(unique_sources)) if len(unique_sources) == 1 else "mixed"
    ) if unique_sources else "unknown"
    history.attrs["source"] = history.attrs["provenance"]
    history.attrs["source_by_as_of"] = dict(sources_by_date)
    history.attrs["provenance_metadata_by_as_of"] = dict(metadata_by_date)
    history.attrs["pit_valid"] = validate_universe_provenance(history)["pit_valid"]
    return history


def get_kospi200_history_with_provenance(
    start: date, end: date, rebalance_freq: str = "M"
) -> UniverseHistoryResult:
    """Return the compatible history together with explicit source metadata."""
    history = get_kospi200_history(start, end, rebalance_freq)
    validation = validate_universe_provenance(history)
    return UniverseHistoryResult(
        data=history,
        provenance=validation["provenance"],
        provenance_by_as_of=validation["provenance_by_as_of"],
        provenance_metadata_by_as_of=validation.get(
            "provenance_metadata_by_as_of", {},
        ),
    )


def validate_universe_provenance(
    universe_history: pd.DataFrame | UniverseHistoryResult,
) -> dict[str, Any]:
    """Delegate to the side-effect-free prepared-data validator."""
    return _validate_universe_provenance(universe_history)


def is_pit_valid_universe(
    universe_history: pd.DataFrame | UniverseHistoryResult,
) -> bool:
    """Return ``True`` only for histories explicitly sourced as PIT."""
    return bool(validate_universe_provenance(universe_history)["pit_valid"])


# Descriptive aliases for callers that prefer validator-style naming.
get_universe_provenance = validate_universe_provenance
validate_universe_pit = validate_universe_provenance
is_universe_pit_valid = is_pit_valid_universe


def _last_weekday(value: date) -> date:
    """Return the last weekday on or before *value*.

    This is the calendar-level fallback used before the price loader is
    available.  ``PortfolioRebalanceEngine`` additionally maps the result to
    the last actual price bar, covering exchange holidays as well.
    """
    timestamp = pd.Timestamp(value)
    while timestamp.weekday() >= 5:
        timestamp -= timedelta(days=1)
    return timestamp.date()


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


def exclude_kospi_top_n(
    tickers: list[str],
    n: int = 50,
    *,
    strict_pit: bool = False,
) -> list[str]:
    """KOSPI 상위 N개 시가총액 종목을 제외합니다.

    모멘텀 팩터 성능을 위해 상위 대형주를 제외합니다
    (Choi, Choi & Kang 2013 참조).

    Parameters
    ----------
    tickers : list[str]
        원본 티커 리스트.
    n : int
        제외할 상위 종목 수 (기본 50).
    strict_pit : bool
        Strict PIT mode.  The current implementation ranks from a current
        market-cap snapshot, so any positive ``n`` is rejected.

    Returns
    -------
    list[str]
        상위 N개가 제외된 티커 리스트.
    """
    if strict_pit and n > 0:
        raise RuntimeError(
            "STRICT_PIT_VALIDATION rejects current-market-cap top-N exclusion. "
            "Set EXCLUDE_KOSPI_TOP_N=0 until an effective-date PIT rank source "
            "is available."
        )

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
