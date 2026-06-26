"""공급 팩터 계산.

Provides
--------
- :class:`RetailSupplyFactor` — 개인 투자자 순매수 집계
"""

import pandas as pd

from super_quality.factors.base import Factor


class RetailSupplyFactor(Factor):
    """개인 투자자 순매수 공급 점수.

    각 ticker에 대해 ``supply_days`` (기본 5일)의 후행 기간 동안
    ``retail_net_buy``를 집계한 후, ticker를 오름차순으로 순위 매깁니다
    (높은 순매수 → 높은 공급 점수 → 높은 백분위).
    """

    def __init__(self, supply_days: int = 5) -> None:
        self.supply_days = supply_days

    @property
    def name(self) -> str:
        return "RetailSupply"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """롤링 공급 점수 및 백분위를 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``date``, ``retail_net_buy`` 컬럼을 포함해야 함.

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``supply_score``, ``supply_percentile``.
            최소 ``supply_days`` 이상의 데이터가 있는 ticker만 반환됨.
        """
        df = data[["ticker", "date", "retail_net_buy"]].copy()
        df = df.sort_values(["ticker", "date"])

        # 후행 윈도우 기준 ticker별 롤링 합계
        df["supply_score"] = (
            df.groupby("ticker")["retail_net_buy"]
            .transform(lambda x: x.rolling(self.supply_days, min_periods=self.supply_days).sum())
        )

        # 점수가 전체 윈도우 데이터를 가진 ticker별 가장 최근 날짜만 유지
        latest = (
            df.loc[df["supply_score"].notna()]
            .groupby("ticker")
            .last()
            .reset_index()
        )

        # 오름차순 백분위 (높은 순매수 → 높은 백분위)
        latest["supply_percentile"] = (
            latest["supply_score"].rank(pct=True, ascending=True) * 100.0
        )

        return latest[["ticker", "supply_score", "supply_percentile"]]
