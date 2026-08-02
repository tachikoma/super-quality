"""KOSPI 200 Momentum + Quality 전략.

크로스섹셔널 모멘텀+품질 스코어링으로 리밸런싱 일자에
최적 포트폴리오를 구성합니다.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from k200_mq.config import K200MQConfig

logger = logging.getLogger(__name__)


class MomentumQualityStrategy:
    """KOSPI 200 Momentum + Quality 전략.

    교차섹셔널 모멘텀+품질 스코어링으로 종목을 선택하고
    리밸런싱 일자에 포트폴리오를 재구성합니다.
    """

    def __init__(self, config: K200MQConfig) -> None:
        self.config = config

    def select_portfolio(
        self,
        factor_data: pd.DataFrame,
        universe: list[str],
        as_of: Any,
    ) -> list[dict[str, Any]]:
        """리밸런싱 일자에 포트폴리오를 선택합니다.

        Parameters
        ----------
        factor_data : pd.DataFrame
            팩터 스코어가 포함된 DataFrame.
            컬럼: ticker, momentum_z, quality_z, composite_z.
        universe : list[str]
            리서치 대상 티커 리스트.
        as_of : any
            리밸런싱 일자 (로그용).

        Returns
        -------
        list[dict]
            [{"ticker": str, "weight": float}, ...]
        """
        # 유니버스 필터링
        eligible = factor_data[
            factor_data["ticker"].isin(universe)
        ].copy()

        if eligible.empty:
            logger.warning("리밸런싱 %s: eligible 종목 없음", as_of)
            return []

        # 모멘텀 가중치 + 품질 가중치 = 복합 스코어
        w_mom = self.config.WEIGHT_MOMENTUM
        w_qual = self.config.WEIGHT_QUALITY
        eligible["composite_z"] = (
            w_mom * eligible["momentum_z"].fillna(0)
            + w_qual * eligible["quality_z"].fillna(0)
        )

        # 종목 선택 (top N)
        top_n = self.config.TOP_N
        selected = eligible.nlargest(top_n, "composite_z")

        # KOSPI 상위 50개 제외 (메가캡 모멘텀 희석 방지)
        if self.config.EXCLUDE_KOSPI_TOP_N > 0:
            selected = self._exclude_kospi_top(selected)

        # 섹션별 노출 캡 적용 (간단 구현)
        # TODO: GICS 코드 매핑 후 정확한 섹션별 캡

        if selected.empty:
            logger.warning("리밸런싱 %s: 선택된 종목 없음 (캡 적용 후)", as_of)
            return []

        # 포지션 배분
        weight_method = self.config.WEIGHT_METHOD
        if weight_method == "equal":
            n = len(selected)
            weight = 1.0 / n if n > 0 else 0.0
            selected["weight"] = weight
        elif weight_method == "rank_weighted":
            selected["rank"] = range(len(selected), 0, -1)
            total_rank = selected["rank"].sum()
            selected["weight"] = selected["rank"] / total_rank
        else:
            selected["weight"] = 1.0 / len(selected)

        # 포지션당 최대 비중 제한
        max_w = self.config.MAX_POSITION_WEIGHT
        selected["weight"] = selected["weight"].clip(upper=max_w)
        weights_norm = selected["weight"].sum()
        if weights_norm > 0:
            selected["weight"] = selected["weight"] / weights_norm

        logger.info(
            "리밸런싱 %s: %d개 선정, 평균 weight=%.4f",
            as_of,
            len(selected),
            selected["weight"].mean(),
        )

        return selected.to_dict(orient="records")

    def get_signal(
        self,
        factor_data: pd.DataFrame,
        as_of: Any,
    ) -> pd.DataFrame:
        """일일 시그널을 생성합니다 (stop-loss 모니터링용).

        Parameters
        ----------
        factor_data : pd.DataFrame
            일별 팩터 데이터 (ticker, date, close, momentum_z, quality_z).
        as_of : any
            기준 일자.

        Returns
        -------
        pd.DataFrame
            일일 시그널 DataFrame.
        """
        eligible = factor_data[
            factor_data["date"] == as_of
        ].copy() if "date" in factor_data.columns else factor_data.copy()

        w_mom = self.config.WEIGHT_MOMENTUM
        w_qual = self.config.WEIGHT_QUALITY
        eligible["composite_z"] = (
            w_mom * eligible.get("momentum_z", pd.Series(0, index=eligible.index))
            + w_qual * eligible.get("quality_z", pd.Series(0, index=eligible.index))
        )

        return eligible[["ticker", "composite_z"]]

    def _exclude_kospi_top(
        self, selected: pd.DataFrame
    ) -> pd.DataFrame:
        """KOSPI 상위 N개 시가총액 종목을 제외합니다."""
        from k200_mq.data.universe import exclude_kospi_top_n

        tickers = selected["ticker"].tolist()
        filtered = exclude_kospi_top_n(
            tickers,
            n=self.config.EXCLUDE_KOSPI_TOP_N,
            strict_pit=bool(getattr(self.config, "STRICT_PIT_VALIDATION", False)),
        )
        return selected[selected["ticker"].isin(filtered)]
