"""모멘텀 팩터 계산.

Provides
--------
- :class:`MomentumFactor` — 12-7개월 수익률 (마지막 2개월 skip)
- :class:`YearHighFactor` — 52주 고점 비율
"""

from __future__ import annotations

import pandas as pd

from k200_mq.core.factors.base import Factor


class MomentumFactor(Factor):
    """12-7개월 수익률 모멘텀 팩터.

    과거 12개월 수익률 중 마지막 2개월을 제외한
    10개월 수익률을 계산합니다. 이는 한국 시장의
    2개월 반전 현상을 회피하기 위한 설계입니다
    (Sim & Kim 2021).

    교차섹셔널 z-score로 정규화하여 반환합니다.
    """

    @property
    def name(self) -> str:
        return "Momentum"

    def compute(
        self,
        data: pd.DataFrame,
        long_window: int = 252,
        short_window: int = 126,
        skip_days: int = 42,
    ) -> pd.DataFrame:
        """모멘텀 점수를 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``date``, ``close`` 컬럼을 포함해야 합니다.
        long_window : int
            장기 룩백 일수 (기본 252, 약 12개월).
        short_window : int
            단기 룩백 일수, long_window에서 차감 (기본 126, 약 6개월).
        skip_days : int
            long_window 끝에서 skip할 일수 (기본 42, 약 2개월).

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``date``, ``momentum``.
        """
        df = data[["ticker", "date", "close"]].copy()
        df = df.sort_values(["ticker", "date"])

        # 12개월 전 종가 (skip_days 포함, long_window에서 차감)
        lag_long = long_window - skip_days
        df["price_long_ago"] = (
            df.groupby("ticker")["close"]
            .shift(lag_long)
        )

        # 6개월 전 종가
        df["price_short_ago"] = (
            df.groupby("ticker")["close"]
            .shift(short_window)
        )

        # 수익률 계산 (12-7개월 = 10개월)
        df["momentum"] = (df["close"] / df["price_long_ago"]) - 1.0
        df["momentum_6m"] = (df["close"] / df["price_short_ago"]) - 1.0

        # NaN 行 제거
        df = df.dropna(subset=["momentum", "momentum_6m"])

        # 교차섹셔널 z-score 정규화
        df["momentum_z"] = (
            df.groupby("date")["momentum"]
            .transform(lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0.0)
        )

        return df[["ticker", "date", "momentum", "momentum_6m", "momentum_z"]]


class YearHighFactor(Factor):
    """52주 고점 비율 팩터.

    현재 가격이 해당 종목의 52주 고점-저점 범위에서
    어느 위치에 있는지를 반환합니다.
    """

    @property
    def name(self) -> str:
        return "YearHigh"

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """52주 고점 비율을 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``date``, ``close``, ``high``, ``low`` 컬럼 포함.

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``date``, ``year_high_pct``.
        """
        df = data[["ticker", "date", "close", "high", "low"]].copy()
        df = df.sort_values(["ticker", "date"])

        # 252거래일 고점/저점 윈도우 (거래일 기준)
        df["high_52w"] = (
            df.groupby("ticker")["high"]
            .transform(lambda x: x.rolling(252, min_periods=60).max())
        )
        df["low_52w"] = (
            df.groupby("ticker")["low"]
            .transform(lambda x: x.rolling(252, min_periods=60).min())
        )

        df["year_high_pct"] = (df["close"] - df["low_52w"]) / (df["high_52w"] - df["low_52w"])
        df["year_high_pct"] = df["year_high_pct"].fillna(0.5)

        return df[["ticker", "date", "year_high_pct"]]