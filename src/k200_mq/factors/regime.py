"""리짓 필터 팩터 계산.

Provides
--------
- :class:`RegimeFactor` — KOSPI 200 MA200 + 20일 수익률 기반 시장 리짓 신호
"""

from __future__ import annotations

import pandas as pd

from k200_mq.core.factors.base import Factor


REGIME_FORMULA_VERSION = "k200mq-regime-continuous-vol-target-v6"
REGIME_FORMULA = "close > rolling_ma(ma_period) and cum_return(min_return_days) > min_return"


class RegimeFactor(Factor):
    """KOSPI 200 시장 리짓 팩터.

    시장 추세를 판단하여 포트폴리오의 노출을 조절합니다.

    * **이진 모드** — close > MA200 AND 20d return > min_return
        → bullish: 100%, bearish: reduction (기본 50%)
    * **연속 모드 (v6)** — min(1.0, target_vol / realized_vol) × trend_strength
        → 포지션이0~1 사이에서 연속적으로 조절됨
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

    def compute_continuous(
        self,
        data: pd.DataFrame,
        ma_period: int = 200,
        target_vol: float = 0.15,
        vol_lookback: int = 20,
    ) -> pd.DataFrame:
        """연속 변동성 타겟팅 리짓 신호를 계산합니다 (v6).

        Parameters
        ----------
        data : pd.DataFrame
            ``date`` 컬럼과 ``close`` 컬럼을 포함해야 합니다.
        ma_period : int
            이동 평균 기간 (기본 200).
        target_vol : float
            연간 목표 변동성 (기본 0.15 = 15%).
        vol_lookback : int
            실현 변동성 계산 룩백 일수 (기본 20).

        Returns
        -------
        pd.DataFrame
            컬럼: ``date``, ``close``, ``ma``, ``daily_return``,
            ``realized_vol``, ``trend_strength``, ``vol_scale``,
            ``position_scale`` (0.0~1.0 연속).
        """
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

        # MA200
        df["ma"] = df["close"].rolling(ma_period, min_periods=ma_period).mean()

        # 일별 수익률
        df["daily_return"] = df["close"].pct_change()

        # 실현 변동성 (연간화)
        df["realized_vol"] = (
            df["daily_return"]
            .rolling(vol_lookback, min_periods=vol_lookback)
            .std() * (252 ** 0.5)
        )

        # 추세 강도: (close - MA) / MA, clamped to [-0.5, 0.5]
        df["trend_strength"] = ((df["close"] - df["ma"]) / df["ma"]).clip(-0.5, 0.5)

        # 변동성 스케일: min(1.0, target_vol / realized_vol)
        df["vol_scale"] = (target_vol / df["realized_vol"]).clip(upper=1.0)

        # 최종 position_scale: vol_scale × (0.5 + trend_strength)
        # trend_strength ∈ [-0.5, 0.5] → 0.5 + trend_strength ∈ [0.0, 1.0]
        # vol_scale ∈ [0, 1] → position_scale ∈ [0, 1]
        raw_scale = df["vol_scale"] * (0.5 + df["trend_strength"])
        df["position_scale"] = raw_scale.clip(0.0, 1.0)

        return df[["date", "close", "ma", "daily_return", "realized_vol", "trend_strength", "vol_scale", "position_scale"]]
