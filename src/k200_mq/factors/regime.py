"""리짓 필터 팩터 계산.

Provides
--------
- :class:`RegimeFactor` — KOSPI 200 MA200 + 20일 수익률 기반 시장 리짓 신호
"""

from __future__ import annotations

import pandas as pd

from k200_mq.core.factors.base import Factor


class RegimeFactor(Factor):
    """KOSPI 200 시장 리짓 팩터.

    시장 추세를 판단하여 포트폴리오의 노출을 조절합니다.

    * **Bullish** — KOSPI 200 > MA200 AND 20일 수익률 > 0
        → 포지션 100% 유지
    * **Bearish** — 위 조건 미충족
        → 포지션 축소 (config.REDUCTION 비율)
    """

    @property
    def name(self) -> str:
        return "Regime"

    def compute(
        self,
        data: pd.DataFrame,
        ma_period: int = 200,
        min_return_days: int = 20,
        reduction: float = 0.50,
    ) -> pd.DataFrame:
        """리짓 신호를 계산합니다.

        Parameters
        ----------
        data : pd.DataFrame
            ``date`` 컬럼과 ``close`` 컬럼을 포함해야 합니다.
        ma_period : int
            이동 평균 기간 (기본 200).
        min_return_days : int
            수익률 계산 기간 (기본 20일).
        reduction : float
            Bearish 시 포지션 축소 비율 (기본 0.5 = 50%).

        Returns
        -------
        pd.DataFrame
            컬럼: ``date``, ``close``, ``ma`` (MA), ``daily_return``,
            ``cum_return_20d``, ``regime`` (True = Bullish, False = Bearish),
            ``position_scale`` (1.0 or reduction).
        """
        df = data[["close"]].copy()
        if "date" not in df.columns:
            if isinstance(data.index, pd.DatetimeIndex):
                df["date"] = df.index
            else:
                df["date"] = pd.date_range(
                    start=df.index[0], periods=len(df), freq="B"
                )

        df = df.reset_index(drop=True)

        # MA200 계산
        df["ma"] = df["close"].rolling(ma_period, min_periods=ma_period).mean()

        # 일별 수익률
        df["daily_return"] = df["close"].pct_change()

        # 20일 누적 수익률
        df["cum_return_20d"] = (
            df["daily_return"]
            .rolling(min_return_days, min_periods=min_return_days)
            .apply(lambda x: (1 + x).prod() - 1, raw=True)
        )

        # 리짓 판단
        df["regime"] = (df["close"] > df["ma"]) & (df["cum_return_20d"] > 0)
        df["regime"] = df["regime"].fillna(False)

        # 포지션 스케일
        df["position_scale"] = df["regime"].map({True: 1.0, False: reduction})

        return df[["date", "close", "ma", "daily_return", "cum_return_20d", "regime", "position_scale"]]