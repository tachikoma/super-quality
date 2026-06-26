"""품질 팩터 계산.

이 팩터들은 기업의 수익성(GP/A)과 건전성(신F‑SCORE)을 평가합니다.
함수들은 `ticker`와 관련 재무 데이터(매출, 매출원가, 총자산 또는 주식수 변화 및 이익)를
포함하는 DataFrame을 입력받아 백분위 순위 또는 통과 여부를 반환합니다.
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


class NewFScoreFactor(Factor):
    """신F‑SCORE: 네 가지 불리언 조건 (C, D, E, F).

    ticker가 신F‑SCORE를 **통과**하려면 **모든** 아래 조건이 충족되어야 합니다:

    * C — 5개월 전 주식 발행 없음 (`share_change_5mo_ago == 0`)
    * D — 현재 기간 주식 발행 없음 (`share_change_now == 0`)
    * E — 최근 당기순이익 양수 (`trailing_ni > 0`)
    * F — 최근 영업현금흐름 양수 (`trailing_ocf > 0`)
    """

    @property
    def name(self) -> str:
        return "NewFScore"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """신F‑SCORE 통과 여부 계산.

        Parameters
        ----------
        data : pd.DataFrame
            `ticker`, `share_change_5mo_ago`, `share_change_now`,
            `trailing_ni`, `trailing_ocf` 컬럼을 포함해야 합니다.

        Returns
        -------
        pd.DataFrame
            컬럼: `ticker`, `c_pass`, `d_pass`, `e_pass`, `f_pass`,
            `new_fscore_pass`.
        """
        cols = [
            "ticker",
            "share_change_5mo_ago",
            "share_change_now",
            "trailing_ni",
            "trailing_ocf",
        ]
        df = data[cols].copy()
        df["c_pass"] = df["share_change_5mo_ago"] == 0
        df["d_pass"] = df["share_change_now"] == 0
        df["e_pass"] = df["trailing_ni"] > 0
        df["f_pass"] = df["trailing_ocf"] > 0
        df["new_fscore_pass"] = (
            df["c_pass"] & df["d_pass"] & df["e_pass"] & df["f_pass"]
        )
        return df[["ticker", "c_pass", "d_pass", "e_pass", "f_pass", "new_fscore_pass"]]
