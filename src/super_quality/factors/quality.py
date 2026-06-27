"""품질 팩터 계산.

기업의 수익성(GP/A - 총이익/총자산) 백분위를 계산합니다.
"""

import pandas as pd

from super_quality.factors.base import Factor


class GPAFactor(Factor):
    """GP/A (총이익 / 총자산) 백분위.

    `GP/A = (매출 - 매출원가) / 총자산`을 계산하고 각 ticker를
    오름차순(높은 GP/A → 높은 백분위)으로 순위 매깁니다.
    총자산이 0인 경우 GP/A를 0으로 설정합니다.
    """

    @property
    def name(self) -> str:
        return "GPA"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """GP/A 및 오름차순 백분위 계산.

        Parameters
        ----------
        data : pd.DataFrame
            `ticker`, `revenue`, `cogs`, `total_assets` 컬럼을 포함해야 합니다.

        Returns
        -------
        pd.DataFrame
            컬럼: `ticker`, `gpa`, `gpa_percentile`.
        """
        df = data[["ticker", "revenue", "cogs", "total_assets"]].copy()
        # GP/A = (매출 - 매출원가) / 총자산; 총자산이 0이면 0으로 설정
        df["gpa"] = (df["revenue"] - df["cogs"]) / df["total_assets"]
        df.loc[df["total_assets"] == 0, "gpa"] = 0.0
        # 오름차순 백분위 (높은 GP/A → 높은 백분위)
        df["gpa_percentile"] = (
            df["gpa"].rank(pct=True, ascending=True) * 100.0
        )
        return df[["ticker", "gpa", "gpa_percentile"]]

