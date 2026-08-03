"""리짓 필터 팩터 계산.

Provides
--------
- :class:`RegimeFactor` — KOSPI 200 MA200 + 20일 수익률 기반 시장 리짓 신호
"""

from __future__ import annotations

import pandas as pd

from k200_mq.core.factors.base import Factor


REGIME_FORMULA_VERSION = "k200mq-regime-ma-return-threshold-v2"
REGIME_FORMULA = "close > rolling_ma(ma_period) and cum_return(min_return_days) > min_return"


class RegimeFactor(Factor):
    """KOSPI 200 시장 리짓 팩터.

    시장 추세를 판단하여 포트폴리오의 노출을 조절합니다.

    * **Bullish** — KOSPI 200 > MA200 AND 20거래일 누적 수익률 >
      ``REGIME_MIN_RETURN`` (기본값 0.0)
        → 포지션 100% 유지
    * **Bearish** — 위 조건 미충족
        → 포지션 축소 (config.REDUCTION 비율)
    """

    formula_version = REGIME_FORMULA_VERSION
    formula = REGIME_FORMULA

    @property
    def name(self) -> str:
        return "Regime"

    def compute(
        self,
        data: pd.DataFrame,
        ma_period: int = 200,
        min_return_days: int = 20,
        min_return: float = 0.0,
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
        min_return : float
            Bullish 판정에 필요한 최소 누적 수익률 (기본 0.0).
        reduction : float
            Bearish 시 포지션 축소 비율 (기본 0.5 = 50%).

        Returns
        -------
        pd.DataFrame
            컬럼: ``date``, ``close``, ``ma`` (MA), ``daily_return``,
            ``cum_return_20d``, ``regime`` (True = Bullish, False = Bearish),
            ``position_scale`` (1.0 or reduction).
        """
        # Keep the source dates before reducing the frame to the columns used
        # by the calculation.  In particular, ``main`` passes
        # ``index_raw.reset_index()`` here; dropping ``date`` and then falling
        # back to a RangeIndex silently manufactured 1970 dates and left the
        # measured regime map empty.
        if "date" in data.columns:
            df = data[["date", "close"]].copy()
        elif isinstance(data.index, pd.RangeIndex):
            if len(data):
                raise ValueError("data must contain a 'date' column or a date-like index")
            df = data[["close"]].copy()
            df["date"] = pd.DatetimeIndex([])
        else:
            df = data[["close"]].copy()
            df["date"] = data.index

        df["date"] = pd.to_datetime(df["date"])
        df = df.reset_index(drop=True)

        # MA200 계산
        df["ma"] = df["close"].rolling(ma_period, min_periods=ma_period).mean()

        # 일별 수익률
        df["daily_return"] = df["close"].pct_change()

        # 20거래일 누적 수익률; the return window remains fixed at 20 days
        df["cum_return_20d"] = (
            df["daily_return"]
            .rolling(min_return_days, min_periods=min_return_days)
            .apply(lambda x: (1 + x).prod() - 1, raw=True)
        )

        # 리짓 판단.  Rows without a complete MA/return warmup are unknown,
        # not bearish.  Keeping them as NA prevents a short index download
        # from silently applying the bearish reduction to the start of a
        # measured backtest.
        valid = df["ma"].notna() & df["cum_return_20d"].notna()
        df["regime"] = pd.Series(pd.NA, index=df.index, dtype="boolean")
        df.loc[valid, "regime"] = (
            (df.loc[valid, "close"] > df.loc[valid, "ma"])
            & (df.loc[valid, "cum_return_20d"] > min_return)
        )

        # Missing regime values are intentionally left without a scale.  The
        # caller treats a missing map entry as neutral (1.0), rather than as a
        # bearish reduction.
        df["position_scale"] = df["regime"].map({True: 1.0, False: reduction})

        return df[["date", "close", "ma", "daily_return", "cum_return_20d", "regime", "position_scale"]]
