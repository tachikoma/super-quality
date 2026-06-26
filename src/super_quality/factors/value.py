"""가치 팩터 계산.

Provides
--------
- :class:`PBRFactor` — trailing Price-to-Book ratio percentile
- :class:`MarketCapFactor` — 시가총액 백분위 (소형주 편향)
"""

import numpy as np
import pandas as pd

from super_quality.factors.base import Factor


class PBRFactor(Factor):
    """Trailing PBR (Price-to-Book Ratio, 주가순자산비율).

    ``PBR = market_cap / total_equity``를 계산하고 각 ticker를
    오름차순으로 순위를 매깁니다 (낮은 PBR = 저렴함 = 더 좋음). 자기자본이
    0 이하인 종목은 순위에서 제외됩니다 (PBR을 NaN으로 설정).
    """

    @property
    def name(self) -> str:
        return "PBR"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """Trailing PBR 및 오름차순 백분위 계산.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``mcap``, ``total_equity`` 컬럼을 포함해야 합니다.

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``pbr``, ``pbr_percentile``.
        """
        df = data[["ticker", "mcap", "total_equity"]].copy()

        # PBR = mcap / total_equity; 자기자본이 0 이하면 NaN
        df["pbr"] = df["mcap"] / df["total_equity"]
        df.loc[df["total_equity"] <= 0, "pbr"] = np.nan

        # 오름차순 백분위 (낮은 PBR → 낮은 백분위)
        valid = df["pbr"].notna()
        df["pbr_percentile"] = np.nan
        df.loc[valid, "pbr_percentile"] = (
            df.loc[valid, "pbr"].rank(pct=True, ascending=True) * 100.0
        )

        return df[["ticker", "pbr", "pbr_percentile"]]


class MarketCapFactor(Factor):
    """시가총액 백분위 (소형주 편향).

    시가총액이 작은 순서대로 ticker에 순위를 매겨,
    가장 작은 시가총액이 가장 낮은 백분위(0에 가까움)를 받고
    가장 큰 시가총액이 가장 높은 백분위(100에 가까움)를 받습니다.
    """

    @property
    def name(self) -> str:
        return "MarketCap"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """시가총액 오름차순 백분위를 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``mcap`` 컬럼을 포함해야 함.

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``mcap_percentile``.
        """
        df = data[["ticker", "mcap"]].copy()

        # 오름차순 백분위 (작은 mcap → 낮은 백분위)
        df["mcap_percentile"] = (
            df["mcap"].rank(pct=True, ascending=True) * 100.0
        )

        return df[["ticker", "mcap_percentile"]]
