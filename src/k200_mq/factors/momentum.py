"""모멘텀 팩터 계산.

Provides
--------
- :class:`MomentumFactor` — skipped-return 12-2개월 수익률
- :class:`YearHighFactor` — 52주 고점 비율
"""

from __future__ import annotations

import pandas as pd

from k200_mq.core.factors.base import Factor


MOMENTUM_FORMULA_VERSION = "k200mq-momentum-skipped-return-v4"
MOMENTUM_FORMULA = "close[t-skip_days] / close[t-long_window] - 1"
MOMENTUM_FORMULA_DEFAULT = "close[t-42] / close[t-252] - 1"


class MomentumFactor(Factor):
    """Skipped-return momentum factor.

    The long feature is the current skipped-return formula
    ``close[t-skip_days] / close[t-long_window] - 1``.  With the default
    values this is ``close[t-42] / close[t-252] - 1``.  The separately exposed
    ``momentum_6m`` feature remains the current-price-to-short-lookback return;
    it is not used for the ranking score.

    The long feature is normalized to a cross-sectional z-score for ranking.
    """

    formula_version = MOMENTUM_FORMULA_VERSION
    formula = MOMENTUM_FORMULA

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
        """Calculate the long and exposed short momentum features.

        Parameters
        ----------
        data : pd.DataFrame
            ``ticker``, ``date``, ``close`` 컬럼을 포함해야 합니다.
        long_window : int
            장기 룩백 일수 (기본 252, 약 12개월).
        short_window : int
            Exposed short-feature lookback 일수 (기본 126, 약 6개월).
        skip_days : int
            현재 시점에서 제외할 최근 일수 (기본 42, 약 2개월).

        Returns
        -------
        pd.DataFrame
            컬럼: ``ticker``, ``date``, ``momentum``.
        """
        df = data[["ticker", "date", "close"]].copy()
        df = df.sort_values(["ticker", "date"])

        if long_window <= 0 or short_window <= 0:
            raise ValueError("long_window and short_window must be positive")
        if skip_days < 0 or skip_days >= long_window:
            raise ValueError("skip_days must satisfy 0 <= skip_days < long_window")

        # Skipped return: close[t-skip_days] / close[t-long_window] - 1.
        # Keep the endpoint and origin shifts separate: the skipped endpoint
        # is not the same as shortening the lookback window.
        df["price_skip_ago"] = (
            df.groupby("ticker")["close"]
            .shift(skip_days)
        )
        df["price_long_ago"] = (
            df.groupby("ticker")["close"]
            .shift(long_window)
        )

        # 6개월 전 종가
        df["price_short_ago"] = (
            df.groupby("ticker")["close"]
            .shift(short_window)
        )

        df["momentum"] = (df["price_skip_ago"] / df["price_long_ago"]) - 1.0
        # Preserve the existing short feature as a diagnostic/exposed column.
        df["momentum_6m"] = (df["close"] / df["price_short_ago"]) - 1.0

        # The skipped-return feature drives ranking.  Keep rows when only the
        # exposed short return is unavailable so the diagnostic window cannot
        # alter factor readiness or the measured trading universe.
        df = df.dropna(subset=["momentum"])

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
