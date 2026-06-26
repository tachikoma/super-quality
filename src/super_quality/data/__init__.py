"""데이터 로딩 및 캐싱 서브패키지 for Super Quality 2.0.

FinanceDataReader, pykrx, OpenDartReader에서 한국 금융 시장 데이터를
가져오기 위한 깔끔한 공개 API를 제공하며, Parquet 기반의 적극적인
캐싱 레이어를 포함합니다.
"""

from super_quality.data.cache import DataCache
from super_quality.data.loader import (
    calculate_ttm,
    get_available_lag,
    get_financial_data,
    get_kosdaq_index,
    get_krx_listings,
    get_price_data,
    get_retail_net_buy,
    get_shares_outstanding,
    get_paid_in_capital_increases,
)

__all__ = [
    "DataCache",
    "calculate_ttm",
    "get_available_lag",
    "get_financial_data",
    "get_kosdaq_index",
    "get_krx_listings",
    "get_price_data",
    "get_retail_net_buy",
    "get_shares_outstanding",
    "get_paid_in_capital_increases",
]
